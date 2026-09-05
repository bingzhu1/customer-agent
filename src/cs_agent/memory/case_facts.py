"""会话内的强类型事实 `CaseFacts`（PRD §10.2、FR-701 / FR-702，不变式 2）。

**唯一写入路径是本模块的三个纯函数**，它们各自只接受一种确定性来源：

| 函数 | 来源 | 不接受 |
|---|---|---|
| `apply_tool_result` | 工具返回的结构化结果（业务库查出来的） | 用户原话、LLM 输出 |
| `apply_verdict` | 策略引擎的 `PolicyVerdict`（鸭子类型，避免反向依赖 policy 包） | 任何自由文本 |
| `apply_action` | 已登记的 `ActionRecord`（确定性代码构造） | LLM 提议的参数 |

刻意**没有** `apply_text` / `apply_llm_output` 这类入口：LLM 想往 CaseFacts 里写东西，
在类型层面就无路可走（与 ADR-0008 / ADR-0009 同一思路：让越权不可表达）。

`apply_tool_result` 只读工具结果里**白名单内的键**，其余一律丢弃——
这样即使某个工具将来多返回了一个自由文本字段，也进不到 CaseFacts 里。
特别地：`order.note` / `ticket.body` 是用户可写字段（FR-209 已包成不可信内容），
白名单里没有它们。

三个函数都是纯函数：不改入参，返回新的 `CaseFacts`，同样输入永远同样输出。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

#: 允许写入 CaseFacts 的工具名。不在表里的工具结果一律忽略，不是报错——
#: 新工具接入时必须显式想清楚"它的哪个字段该进 CaseFacts"，默认什么都不进。
KNOWN_TOOLS: frozenset[str] = frozenset(
    {"get_order", "get_shipping", "get_ticket", "get_refunds", "get_payments", "search_policy"}
)

ActionStatus = Literal["proposed", "confirmed", "executed", "failed", "rejected"]


class Money(BaseModel):
    """金额及其来源字段。来源必须写清楚，便于审计"这个数字是从哪来的"。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal
    currency: str = "CNY"
    #: 取值如 `order.82913.total_amount`，指向业务库字段，不是对话里提到的数字。
    source: str


class PolicyRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    policy_version: int


class ActionRef(BaseModel):
    """当前待确认 / 待审批的动作。不变式 3：永不参与压缩。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    action_type: str
    status: ActionStatus


class ActionRecord(BaseModel):
    """已登记动作的完整记录，由确定性代码在写路径上构造。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    action_type: str
    status: ActionStatus
    idempotency_key: str | None = None
    amount: Money | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    reason_code: str | None = None
    at: datetime | None = None


class Promise(BaseModel):
    """Agent 已作出的承诺。文案来自确定性话术模板，不是 LLM 自由发挥。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    at: datetime
    source: str


class CaseFacts(BaseModel):
    """PRD §10.2 的九个字段。冻结不可变——所有更新走本模块的纯函数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_ids: tuple[int, ...] = ()
    ticket_ids: tuple[int, ...] = ()
    amounts: tuple[Money, ...] = ()
    complaint_points: tuple[str, ...] = ()
    promises_made: tuple[Promise, ...] = ()
    actions_taken: tuple[ActionRecord, ...] = ()
    pending_action: ActionRef | None = None
    relevant_policy_ids: tuple[PolicyRef, ...] = ()
    #: 排障用：最后一次写入来自哪个确定性来源，如 `tool:get_order`。
    last_updated_by: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        """序列化到 `case_state.case_facts`（JSONB）。

        `mode="json"` 保证 Decimal 与 datetime 都落成 JSON 标量。
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_json_dict(cls, raw: dict[str, Any] | None) -> CaseFacts:
        return cls.model_validate(raw or {})


class SupportsVerdict(Protocol):
    """`policy.engine.PolicyVerdict` 的结构子集。

    用 Protocol 而不是直接 import：记忆层不依赖策略层，反向依赖也就无从谈起。
    """

    @property
    def policy_id(self) -> str | None: ...

    @property
    def policy_version(self) -> int | None: ...


def _append_unique(existing: tuple[Any, ...], item: Any) -> tuple[Any, ...]:
    """去重追加，保持首次出现顺序——CaseFacts 要稳定可比对，不能靠集合。"""
    return existing if item in existing else (*existing, item)


def _coerce_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _coerce_money(value: Any, currency: Any, source: str) -> Money | None:
    """金额只接受数字或数字字符串。任何解析不了的东西一律丢弃，不猜。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return Money(amount=amount, currency=str(currency or "CNY"), source=source)


def _from_get_order(facts: CaseFacts, result: dict[str, Any]) -> CaseFacts:
    order_id = _coerce_int(result.get("order_id"))
    if order_id is None:
        return facts
    money = _coerce_money(
        result.get("total_amount"), result.get("currency"), f"order.{order_id}.total_amount"
    )
    return facts.model_copy(
        update={
            "order_ids": _append_unique(facts.order_ids, order_id),
            "amounts": facts.amounts if money is None else _append_unique(facts.amounts, money),
        }
    )


def _from_get_shipping(facts: CaseFacts, result: dict[str, Any]) -> CaseFacts:
    order_id = _coerce_int(result.get("order_id"))
    if order_id is None:
        return facts
    return facts.model_copy(update={"order_ids": _append_unique(facts.order_ids, order_id)})


def _from_get_ticket(facts: CaseFacts, result: dict[str, Any]) -> CaseFacts:
    ticket_id = _coerce_int(result.get("ticket_id"))
    if ticket_id is None:
        return facts
    update: dict[str, Any] = {"ticket_ids": _append_unique(facts.ticket_ids, ticket_id)}
    # 投诉点取工单的 type + subject：这两个字段来自业务库的结构化列，
    # 不是用户在本轮对话里说的话，也不是 LLM 概括的。`body` 是用户原文，不取。
    ticket_type, subject = result.get("type"), result.get("subject")
    if isinstance(ticket_type, str) and isinstance(subject, str) and subject.strip():
        point = f"{ticket_type}:{subject.strip()}"
        update["complaint_points"] = _append_unique(facts.complaint_points, point)
    return facts.model_copy(update=update)


def _from_search_policy(facts: CaseFacts, result: list[Any]) -> CaseFacts:
    refs = facts.relevant_policy_ids
    for item in result:
        if not isinstance(item, dict):
            continue
        policy_id, version = item.get("policy_id"), _coerce_int(item.get("policy_version"))
        if isinstance(policy_id, str) and version is not None:
            refs = _append_unique(refs, PolicyRef(policy_id=policy_id, policy_version=version))
    return facts.model_copy(update={"relevant_policy_ids": refs})


def apply_tool_result(facts: CaseFacts, tool_name: str, result: Any) -> CaseFacts:
    """把一次工具调用的结构化结果并入 CaseFacts（FR-702 允许的两个来源之一）。

    未知工具、`None`（未命中或越权）、形状不对的结果一律原样返回 `facts`——
    宁可少记，也不把不确定的东西写进事实。
    """
    if tool_name not in KNOWN_TOOLS or result is None:
        return facts
    updated = facts
    if tool_name == "get_order" and isinstance(result, dict):
        updated = _from_get_order(facts, result)
    elif tool_name == "get_shipping" and isinstance(result, dict):
        updated = _from_get_shipping(facts, result)
    elif tool_name == "get_ticket" and isinstance(result, dict):
        updated = _from_get_ticket(facts, result)
    elif tool_name == "search_policy" and isinstance(result, list):
        updated = _from_search_policy(facts, result)
    elif tool_name in ("get_refunds", "get_payments") and isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                updated = _from_get_order(updated, {"order_id": item.get("order_id")})
    if updated == facts:
        return facts
    return updated.model_copy(update={"last_updated_by": f"tool:{tool_name}"})


def apply_verdict(facts: CaseFacts, verdict: SupportsVerdict) -> CaseFacts:
    """把策略判定引用到的策略记进 CaseFacts（FR-702 的另一个来源）。

    只取 `policy_id` + `policy_version`——引用—执行一致性校验（FR-306）需要的就是这两个。
    判定结论本身不进 CaseFacts：结论每轮都要用当时的业务事实重新算，不能"回忆"。
    """
    policy_id, version = verdict.policy_id, verdict.policy_version
    if not isinstance(policy_id, str) or not isinstance(version, int):
        return facts
    refs = _append_unique(
        facts.relevant_policy_ids, PolicyRef(policy_id=policy_id, policy_version=version)
    )
    if refs == facts.relevant_policy_ids:
        return facts
    return facts.model_copy(
        update={"relevant_policy_ids": refs, "last_updated_by": "policy_engine"}
    )


def apply_action(facts: CaseFacts, action_record: ActionRecord) -> CaseFacts:
    """登记一次动作。`proposed` / `confirmed` 时同时更新 `pending_action`，终态时清空。

    同一 `action_id` 再次到达视为状态推进：替换旧记录而不是追加，
    否则 `actions_taken` 会被幂等重放撑成一串重复行。
    """
    others = tuple(a for a in facts.actions_taken if a.action_id != action_record.action_id)
    pending = (
        ActionRef(
            action_id=action_record.action_id,
            action_type=action_record.action_type,
            status=action_record.status,
        )
        if action_record.status in ("proposed", "confirmed")
        else None
    )
    return facts.model_copy(
        update={
            "actions_taken": (*others, action_record),
            "pending_action": pending,
            "last_updated_by": f"action:{action_record.action_type}",
        }
    )
