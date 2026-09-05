"""五个节点：ingest → understand → act → policy_gate → decide → respond。

分工是这套架构的核心（ADR-0005、红线 2）：

| 节点 | 谁在做主 | 能做什么 |
|---|---|---|
| ingest | 确定性 | 清理输入、开新一轮的工具预算 |
| understand | **LLM** | 只抽意图与实体，不做任何判断 |
| act | 确定性 | 按意图调只读工具；身份来自 AuthContext，不来自 LLM |
| policy_gate | 确定性 | 用**实时查库**的事实构造 PolicyFacts → `evaluate()` |
| decide | 确定性 | `decision.matrix.decide()`，有序规则表 |
| respond | **LLM** | 把已经定好的决策说成人话，不能改决策 |

`policy_gate` 在 V1 关掉、V3 打开：这正是 V1→V3 那一格的差异——
V1 拿不到 `verdict`，涉及资格判定的问题一律走矩阵规则 9 转人工（安全但不好用）；
V3 有了 `verdict`，超期 / 食品 / 定制会被**确定性地**拒绝并引用正确策略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from cs_agent.auth.context import AuthContext
from cs_agent.decision import templates
from cs_agent.decision.matrix import DecisionInput
from cs_agent.decision.matrix import decide as run_matrix
from cs_agent.domain.enums import (
    DecisionOutcome,
    ItemCategory,
    ItemCondition,
    ReasonCode,
    RefundStatus,
)
from cs_agent.eval.protocol import Citation, Usage
from cs_agent.graph.llm import FallbackLlm, Llm, Understanding
from cs_agent.graph.memory_store import CaseFactsStore, InMemoryCaseFactsStore
from cs_agent.graph.state import AgentState
from cs_agent.graph.tools import ToolBelt
from cs_agent.graph.untrusted import detect_injection
from cs_agent.memory.case_facts import CaseFacts, apply_tool_result, apply_verdict
from cs_agent.memory.extract import TranscriptTurn
from cs_agent.memory.inject import render_hints
from cs_agent.memory.jobs import ExtractionJob
from cs_agent.memory.user_memory import MemoryRecord, UserMemoryRepo
from cs_agent.observability.logging import get_logger
from cs_agent.policy.engine import PolicyVerdict, evaluate
from cs_agent.policy.facts import PolicyFacts
from cs_agent.policy.schema import PolicySet
from cs_agent.rag.rewrite import fallback_query

#: 需要资格判定的意图。这些意图缺少 verdict 时矩阵会转人工，不会默认放行。
ELIGIBILITY_INTENTS = frozenset({"refund_request"})

#: 这些意图都以"某一张订单"为对象，缺订单号就得回头问用户（矩阵规则 15）。
ORDER_SCOPED_INTENTS = frozenset(
    {"order_status", "shipping_status", "refund_request", "refund_status", "payment_status"}
)

logger = get_logger(__name__)


class ExtractionSink(Protocol):
    """抽取队列的形状。`ExtractionQueue` 与 `InlineExtractionQueue` 都满足它。"""

    def submit(self, job: ExtractionJob) -> None: ...


@dataclass
class Deps:
    """节点闭包依赖。每条会话一份（身份、时钟、工具都绑定在这里）。"""

    llm: Llm | FallbackLlm
    tools: ToolBelt
    policies: PolicySet
    now: datetime
    #: 服务端身份。只给确定性代码做越权兜底判断用，**永不进入任何 prompt**。
    auth: AuthContext | None = None
    #: V1 关、V3 开。关掉时 verdict 恒为 None。
    enable_policy_gate: bool = False
    #: CaseFacts 存放位置。默认进程内，API 会话传 `DbCaseFactsStore` 落 `case_state`。
    case_store: CaseFactsStore = field(default_factory=InMemoryCaseFactsStore)
    #: 长期记忆仓库。为 None 时整条记忆链路关闭（V1 / V3 就是这样）。
    memory: UserMemoryRepo | None = None
    #: 抽取队列（`memory.jobs`）。为 None 时不写长期记忆。
    #: 生产用异步的 `ExtractionQueue`；eval 与单测用 `InlineExtractionQueue`
    #: 换取"本轮结束时记忆已写好"的确定性。
    extraction_queue: ExtractionSink | None = None
    #: 记忆的来源会话（FR-705 要求记来源）。eval 的会话没有 threads 行，留空。
    thread_uuid: UUID | None = None


#: 消息里直接点名某个 user 编号的写法。确定性兜底，不依赖 LLM 是否标对了字段。
OTHER_USER_RE = re.compile(r"user[\s_#:=-]*(\d{2,})", re.IGNORECASE)


def references_foreign_user(text: str, deps: Deps) -> bool:
    """消息里点名了**别人**的 user 编号。

    授权判定不能建立在"模型有没有标对布尔值"之上（SEC-008 就是这么漏的），
    所以在 LLM 的 `references_other_user` 之外再加这一层纯正则兜底。
    """
    own = deps.auth.user_id if deps.auth is not None else None
    return any(int(m.group(1)) != own for m in OTHER_USER_RE.finditer(text or ""))


def ingest(state: AgentState, deps: Deps) -> AgentState:
    """清理输入、重置本轮工具预算（FR-210），载入本会话事实与长期记忆。

    记忆检索失败**不得影响本轮响应**（FR-704）：查不到就是空列表，
    整条链路照常往下走——记忆是锦上添花，不是必需品。
    """
    deps.tools.reset_turn()
    text = state.get("user_text", "").strip()
    facts = deps.case_store.load()

    hints: list[MemoryRecord] = []
    if deps.memory is not None and deps.auth is not None and text:
        try:
            hints = deps.memory.search(deps.auth.user_id, text)
        except Exception as exc:  # noqa: BLE001  记忆不可用不影响本轮
            logger.warning("memory_search_failed", error=exc.__class__.__name__)

    return {"user_text": text, "case_facts": facts, "memory_hints": hints, "usage": Usage()}


def understand(state: AgentState, deps: Deps) -> AgentState:
    """LLM 抽取意图，然后用 CaseFacts **确定性地**补上指代实体。

    "那个订单能退吗"里没有订单号，模型也补不出来（补出来才危险）。
    补它的是本会话已经确认过的 `CaseFacts.order_ids`——那是确定性代码从工具结果
    填的事实，不是 `user_memory` 里的非权威提示（红线 3、ADR-0009）。
    """
    understanding, usage = deps.llm.understand(state.get("user_text", ""))
    facts = state.get("case_facts") or CaseFacts()
    understanding = _carry_over_entities(understanding, facts)
    return {"understanding": understanding, "usage": state.get("usage", Usage()) + usage}


def _carry_over_entities(u: Understanding, facts: CaseFacts) -> Understanding:
    """只在模型没给出实体时补，绝不覆盖模型明确抽到的值。

    取最近一个（元组尾部）：多轮里用户说的"那个订单"通常指最近提到的那一单。
    """
    if u.order_id is None and facts.order_ids:
        u = u.model_copy(update={"order_id": facts.order_ids[-1]})
    if u.ticket_id is None and facts.ticket_ids:
        u = u.model_copy(update={"ticket_id": facts.ticket_ids[-1]})
    return u


def act(state: AgentState, deps: Deps) -> AgentState:
    """按意图调只读工具。**身份不出现在任何一处工具参数里**（FR-208）。"""
    u: Understanding = state.get("understanding") or Understanding()
    order: dict[str, Any] | None = None
    shipping: dict[str, Any] | None = None
    ticket: dict[str, Any] | None = None
    policy_hits: list[dict[str, Any]] = []
    ownership_ok = True
    missing_entity = False

    if u.order_id is not None:
        order = deps.tools.get_order(u.order_id)
        if order is None:
            # 他人订单与不存在的订单在这里是同一件事（FR-804），后面统一按归属不符处理
            ownership_ok = False
        elif u.intent == "shipping_status":
            # 订单确认属于本人后才查物流；查不到订单就不再追问物流（避免存在性泄露）
            shipping = deps.tools.get_shipping(u.order_id)

    refunds: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    profile: dict[str, Any] | None = None

    # 只有订单确认属于本人后才查它的钱：查不到订单就到此为止，不泄露存在性
    if order is not None and u.order_id is not None:
        if u.intent in ("refund_status", "refund_request"):
            refunds = deps.tools.get_refunds(u.order_id)
        if u.intent == "payment_status":
            payments = deps.tools.get_payments(u.order_id)

    if u.intent == "membership_question":
        profile = deps.tools.get_profile()

    if u.ticket_id is not None:
        ticket = deps.tools.get_ticket(u.ticket_id)
        if ticket is None:
            ownership_ok = False

    if u.intent in ("policy_question", "refund_request"):
        # 查询改写只用 CaseFacts（确定性事实），不读 user_memory（红线 3）
        known = state.get("case_facts") or CaseFacts()
        if u.order_id is not None and u.order_id not in known.order_ids:
            known = known.model_copy(update={"order_ids": (*known.order_ids, u.order_id)})
        base = u.policy_query or state.get("user_text", "")
        policy_hits = deps.tools.search_policy(fallback_query(base, known).query)

    if (
        u.intent
        in ("order_status", "shipping_status", "refund_request", "refund_status", "payment_status")
        and u.order_id is None
    ):
        missing_entity = True
    if u.intent == "ticket_status" and u.ticket_id is None:
        missing_entity = True

    injection = detect_injection(
        state.get("user_text"),
        (order or {}).get("note"),
        (ticket or {}).get("body"),
    )

    # CaseFacts 只由确定性代码从**工具结果**填充（不变式 2），不看模型说了什么
    facts = state.get("case_facts") or CaseFacts()
    for name, result in (
        ("get_order", order),
        ("get_shipping", shipping),
        ("get_ticket", ticket),
        ("search_policy", policy_hits),
    ):
        if result:
            facts = apply_tool_result(facts, name, result)

    # 该订单是否已经真的退过款。**这是防重复退款的第二道闸**：
    # 幂等键带时间窗，跨窗口就是新键、新动作，光靠它挡不住"隔一小时再退一次"。
    # 权威依据是 biz.refunds 里 succeeded 的记录，不是 agent 侧的动作状态。
    prior_refund_exists = any(r.get("status") == RefundStatus.SUCCEEDED.value for r in refunds)

    retrieval = deps.tools.last_retrieval
    return {
        "case_facts": facts,
        "prior_refund_exists": prior_refund_exists,
        "order": order,
        "shipping": shipping,
        "ticket": ticket,
        "policy_hits": policy_hits,
        "refunds": refunds,
        "payments": payments,
        "profile": profile,
        "retrieval_max_score": retrieval.max_score if retrieval is not None else None,
        "retrieval_band": retrieval.band if retrieval is not None else None,
        # 有可引用 chunk 才允许低置信回答（§9.4 规则 14 约束 3），否则退回 14b 转人工
        "has_citable_chunk": bool(policy_hits),
        "tool_calls": list(deps.tools.calls),
        "ownership_ok": ownership_ok,
        "missing_entity": missing_entity,
        "injection_suspected": injection,
        "tool_budget_exceeded": deps.tools.budget_exceeded,
    }


def policy_gate(state: AgentState, deps: Deps) -> AgentState:
    """构造 `PolicyFacts` 并求值。**每一个事实都现查数据库**（红线 3）。

    这里刻意不从 `state["understanding"]` 取任何事实：用户说"我没用过""3 天前签收"
    都不算数，`item_condition` 与 `days_since_delivery` 只认 `biz` 表里的值。
    """
    if not deps.enable_policy_gate:
        return {"verdict": None}

    u: Understanding = state.get("understanding") or Understanding()
    order = state.get("order")
    if u.intent not in ELIGIBILITY_INTENTS or order is None or u.order_id is None:
        return {"verdict": None}

    items = order.get("items") or []
    if not items:
        return {"verdict": None}
    main = items[0]

    delivered_at = order.get("delivered_at")
    days_since = None
    if delivered_at:
        days_since = (deps.now - datetime.fromisoformat(delivered_at)).days

    facts = PolicyFacts(
        order_id=u.order_id,
        # user_tier 只来自 biz.users.tier，不来自记忆也不来自对话（ADR-0009）
        user_tier=deps.tools.repo.get_user_tier(),
        item_category=ItemCategory(main["category"]),
        item_condition=ItemCondition(main["item_condition"]),
        order_amount=Decimal(order["total_amount"]),
        order_delivered=delivered_at is not None,
        days_since_delivery=days_since,
        prior_refund_exists=deps.tools.repo.has_successful_refund(u.order_id),
    )
    return {"verdict": evaluate(facts, deps.policies)}


def decide(state: AgentState, deps: Deps) -> AgentState:
    """决策矩阵。本层不调 LLM、不读记忆，输入全部由上游确定性节点算好。"""
    u: Understanding = state.get("understanding") or Understanding()
    verdict: PolicyVerdict | None = state.get("verdict")
    order = state.get("order")
    amount = Decimal(order["total_amount"]) if order else None
    eligibility = u.intent in ELIGIBILITY_INTENTS

    # 自称更高权限、或索取他人数据 → 角色不足（矩阵规则 3）。
    # 三个来源取或：LLM 的两个标注 + 不依赖 LLM 的正则兜底。
    role_sufficient = not (
        u.claims_elevated_role
        or u.references_other_user
        or references_foreign_user(state.get("user_text", ""), deps)
    )

    decision = run_matrix(
        DecisionInput(
            ownership_ok=state.get("ownership_ok", True),
            injection_suspected=state.get("injection_suspected", False),
            role_sufficient=role_sufficient,
            customer_requests_human=u.wants_human or u.intent == "human_request",
            high_negative_sentiment=u.negative_sentiment,
            verdict=verdict,
            amount=amount,
            # 冲刺阶段不执行写操作：退款只走到"提议"，因此不是 write_intent，
            # 而是 eligibility_intent——矩阵仍会在通过时给 REQUIRE_CONFIRMATION 之前的那一格。
            # FR-210 的超预算现在是矩阵规则 6b，不再在节点里事后钳位——
            # 钳位读不出"为什么"，也没法被矩阵测试覆盖
            tool_budget_exceeded=state.get("tool_budget_exceeded", False),
            # 已经退过就不再产生新的待确认动作（矩阵规则 11）
            idempotent_replay=state.get("prior_refund_exists", False),
            is_write_intent=eligibility,
            is_eligibility_intent=eligibility,
            retrieval_max_score=state.get("retrieval_max_score"),
            tau_low=deps.tools.retriever.tau_low,
            tau_high=deps.tools.retriever.tau_high,
            has_citable_chunk=state.get("has_citable_chunk", False),
            missing_entity=state.get("missing_entity", False),
        )
    )
    return {"decision": decision}


def respond(state: AgentState, deps: Deps) -> AgentState:
    """先取确定性话术骨架，取不到才让 LLM 写。

    拒绝、升级、索取信息、待确认这些分支的措辞**完全由 `decision/templates` 决定**
    （FR-407）：口径不随模型漂移，"他人订单"与"不存在订单"逐字相同（SEC-010），
    也不会把用户消息里的注入原文复述回去（SEC-004）。

    只有 ANSWER / OK 这一类真要回答内容的分支才调模型——那时它能用的事实
    已经全在 prompt 里，引用也由确定性代码给定，模型编不出 policy_id。
    """
    decision = state["decision"]
    verdict: PolicyVerdict | None = state.get("verdict")
    citations = _citations(state, verdict, deps)
    skeleton = templates.render(decision, _template_vars(state, verdict, deps))

    if decision.outcome is not DecisionOutcome.ANSWER:
        # 非 ANSWER 的分支**逐字**用模板，绝不让模型改写（FR-407）：
        # 模型改写过的拒绝会复述用户的注入原文，两次"未找到"也会措辞不一致。
        return {"reply": skeleton, "citations": citations, "usage": state.get("usage", Usage())}
    if decision.reason_code is ReasonCode.IDEMPOTENT_REPLAY:
        # 模板说"下面是当时的结果"，结果本身从 biz.refunds 拼，确定性的，不让模型转述金额
        detail = _replay_detail(state)
        return {
            "reply": f"{skeleton}{detail}",
            "citations": citations,
            "usage": state.get("usage", Usage()),
        }
    if skeleton and decision.reason_code is not ReasonCode.RETRIEVAL_LOW_CONFIDENCE:
        return {"reply": skeleton, "citations": citations, "usage": state.get("usage", Usage())}

    body, usage = deps.llm.respond(_render_prompt(state, citations))
    reply = f"{skeleton}{body}" if skeleton else body
    return {
        "reply": reply,
        "citations": citations,
        "usage": state.get("usage", Usage()) + usage,
    }


# --- 辅助 ---------------------------------------------------------------------


def _replay_detail(state: AgentState) -> str:
    """已退款的事实明细。金额与时间都来自 `biz.refunds`，不经模型。"""
    succeeded = [
        r for r in (state.get("refunds") or []) if r.get("status") == RefundStatus.SUCCEEDED.value
    ]
    if not succeeded:
        return ""
    record = succeeded[-1]
    when = (record.get("executed_at") or record.get("created_at") or "")[:10]
    amount = record.get("amount")
    return f"退款 {amount} 元已于 {when} 处理完成。" if when else f"退款 {amount} 元已处理完成。"


def _template_vars(
    state: AgentState, verdict: PolicyVerdict | None, deps: Deps
) -> templates.TemplateVars:
    """模板只吃结构化变量。`policy_summary` 取策略正文首句——确定规则，不是模型转述。"""
    order = state.get("order")
    u: Understanding = state.get("understanding") or Understanding()
    summary: str | None = None
    if verdict is not None and verdict.policy_id is not None:
        try:
            summary = deps.policies.by_id(verdict.policy_id).human_text.strip().splitlines()[0]
        except KeyError:  # pragma: no cover - 判定与策略集不同源时才会发生
            summary = None
    return templates.TemplateVars(
        # 归属不符的模板刻意不回显订单号，这里给了也不会被用上
        order_ref=str(u.order_id) if order is not None and u.order_id is not None else None,
        policy_id=verdict.policy_id if verdict is not None else None,
        policy_version=verdict.policy_version if verdict is not None else None,
        policy_summary=summary,
        amount=Decimal(order["total_amount"]) if order else None,
        max_auto_amount=verdict.max_auto_amount if verdict is not None else None,
        missing_field="订单号" if u.ticket_id is None else "工单号",
    )


def _citations(state: AgentState, verdict: PolicyVerdict | None, deps: Deps) -> list[Citation]:
    """判定用了哪条策略就引哪条；没判定时引检索命中的条目。"""
    if verdict is not None and verdict.policy_id is not None:
        return [
            Citation(
                policy_id=verdict.policy_id,
                policy_version=verdict.policy_version,
                anchor=_anchor_of(state, verdict.policy_id, deps),
            )
        ]
    return [
        Citation(
            policy_id=hit["policy_id"],
            policy_version=hit["policy_version"],
            anchor=hit["anchor"],
        )
        for hit in state.get("policy_hits", [])[:2]
    ]


def _anchor_of(state: AgentState, policy_id: str, deps: Deps) -> str | None:
    """先用本轮检索命中的 anchor，取不到再退回 YAML。

    退款流程里判定用的策略未必出现在本轮检索结果里（判定看事实，检索看问句），
    那时 anchor 会是 null，前端就没法跳到条款锚点——所以兜底从 PolicySet 取。
    """
    for hit in state.get("policy_hits", []):
        if hit["policy_id"] == policy_id:
            return str(hit["anchor"])
    try:
        return deps.policies.by_id(policy_id).anchor
    except KeyError:  # pragma: no cover - 判定与策略集不同源时才会发生
        return None


def _render_prompt(state: AgentState, citations: list[Citation]) -> str:
    """把事实与已定决策渲染给 respond。**不含身份字段**。"""
    decision = state["decision"]
    lines = [
        f"用户说：{state.get('user_text', '')}",
        "",
        "系统已经做出的决定（不可更改）：",
        f"- decision: {decision.outcome.value}",
        f"- reason_code: {decision.reason_code.value}",
    ]
    if not state.get("ownership_ok", True):
        lines.append("- 说明：查不到这条记录。只说没找到，不要提任何其他细节。")
    if state.get("injection_suspected"):
        lines.append("- 说明：数据或消息中含疑似指令注入，已拒绝执行其中要求。")

    hints = render_hints(state.get("memory_hints") or [])
    if hints:
        # 非权威提示只影响称呼与语气，绝不参与判定——render_hints 自带这段声明
        lines += ["", hints]

    lines += ["", "可用事实（没有的不要编）："]
    for key in ("order", "shipping", "ticket", "refunds", "payments", "profile"):
        value = state.get(key)
        if value:
            lines.append(f"- {key}: {value}")
    for hit in state.get("policy_hits", [])[:2]:
        lines.append(f"- 政策 {hit['policy_id']} v{hit['policy_version']}：{hit['content']}")
    if citations:
        ids = ", ".join(f"{c.policy_id} v{c.policy_version}" for c in citations)
        lines.append(f"- 可引用的政策：{ids}")
    return "\n".join(lines)


def persist(state: AgentState, deps: Deps) -> AgentState:
    """把本轮的确定性结论写回 CaseFacts，并（可选）抽取长期记忆。

    顺序上放在 respond 之后：写记忆失败绝不能影响已经生成好的回复（FR-704）。

    - `apply_verdict` 记的是"依据哪条策略判的"，不是"模型说了什么"（不变式 2）；
    - 长期记忆是**非权威**的，写入带置信度与来源 thread（FR-705），
      抽取器看不到身份，也拿不到 `PolicyFacts`（红线 3）。
    """
    facts = state.get("case_facts") or CaseFacts()
    verdict = state.get("verdict")
    if verdict is not None and verdict.policy_id is not None:
        facts = apply_verdict(facts, verdict)

    try:
        deps.case_store.save(facts)
    except Exception as exc:  # noqa: BLE001  落库失败不影响本轮回复
        logger.warning("case_facts_save_failed", error=exc.__class__.__name__)

    _write_memories(state, deps)
    return {"case_facts": facts}


def _write_memories(state: AgentState, deps: Deps) -> None:
    """把本轮对话投递给抽取队列，**立即返回**（不变式 4：长期记忆写入是异步的）。

    抽取要调一次模型、还要写库，放在热路径上等于让每轮对话多等一秒多。
    队列自己吞掉所有异常并计数（FR-704），所以这里也不需要 try——
    但还是留着：构造 `TranscriptTurn` 本身理论上也可能抛。
    """
    if deps.extraction_queue is None or deps.auth is None:
        return
    user_text = state.get("user_text") or ""
    if not user_text:
        return
    try:
        deps.extraction_queue.submit(
            ExtractionJob(
                user_id=deps.auth.user_id,
                transcript=(
                    TranscriptTurn(role="user", text=user_text),
                    TranscriptTurn(role="assistant", text=state.get("reply") or ""),
                ),
                source_thread_id=deps.thread_uuid,
            )
        )
    except Exception as exc:  # noqa: BLE001  投递失败也不该影响本轮回复
        logger.warning("memory_submit_failed", error=exc.__class__.__name__)
