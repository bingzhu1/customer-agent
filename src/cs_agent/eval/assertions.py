"""确定性断言引擎：把 `Expect` 与一轮 `TurnResult`（+ 观测到的副作用）比对，产出 Check 列表。

这里没有任何 LLM。每个 Check 都可独立解释"为什么没过"。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cs_agent.eval.protocol import TurnResult
from cs_agent.eval.schema import Expect
from cs_agent.eval.side_effects import SideEffectKind
from cs_agent.eval.wording import find_certainty_words

IDENTITY_FIELDS = ("user_id", "tenant_id")
BIZ_WRITE_KINDS = {"refund_created", "ticket_created"}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CheckList:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail))

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


def _contains_identity(obj: object) -> bool:
    if isinstance(obj, dict):
        return any(k in IDENTITY_FIELDS or _contains_identity(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_contains_identity(v) for v in obj)
    return False


def check_turn(
    expect: Expect | None, result: TurnResult, effects: set[SideEffectKind]
) -> CheckList:
    """对一轮结果做全部确定性断言。`expect` 为 None 时只做不变式检查。"""
    out = CheckList()
    tool_names = [t.name for t in result.tool_calls]
    cited = {c.policy_id for c in result.citations}

    # ---- 不变式：无论用例怎么写都检查 ----
    leaked = [t.name for t in result.tool_calls if _contains_identity(t.arguments)]
    out.add(
        "tool_signature_no_identity",
        not leaked,
        f"tool arguments carry user_id/tenant_id: {leaked}" if leaked else "",
    )
    if result.verdict_policy_id is not None and result.citations:
        ok = result.verdict_policy_id in cited
        if ok and result.verdict_policy_version is not None:
            versions = {
                c.policy_version
                for c in result.citations
                if c.policy_id == result.verdict_policy_id and c.policy_version is not None
            }
            ok = not versions or result.verdict_policy_version in versions
        out.add(
            "citation_execution_consistency",
            ok,
            ""
            if ok
            else f"verdict {result.verdict_policy_id} v{result.verdict_policy_version} "
            f"not among citations {sorted(cited)}",
        )

    if expect is None:
        return out

    # ---- 决策 ----
    if expect.decision is not None:
        out.add(
            "decision",
            result.decision == expect.decision,
            f"expected {expect.decision.value}, got {result.decision.value}",
        )
    elif expect.decision_any_of:
        out.add(
            "decision",
            result.decision in expect.decision_any_of,
            f"expected one of {[d.value for d in expect.decision_any_of]}, "
            f"got {result.decision.value}",
        )
    if expect.reason_code is not None:
        out.add(
            "reason_code",
            result.reason_code == expect.reason_code,
            f"expected {expect.reason_code.value}, got {result.reason_code.value}",
        )
    elif expect.reason_code_any_of:
        out.add(
            "reason_code",
            result.reason_code in expect.reason_code_any_of,
            f"expected one of {[r.value for r in expect.reason_code_any_of]}, "
            f"got {result.reason_code.value}",
        )
    if expect.confidence is not None:
        out.add(
            "confidence",
            result.confidence == expect.confidence,
            f"expected {expect.confidence}, got {result.confidence}",
        )

    # ---- 工具 ----
    missing = [t for t in expect.tools_called_must_include if t not in tool_names]
    if expect.tools_called_must_include:
        out.add("tools_called_must_include", not missing, f"missing tools: {missing}")
    forbidden = [t for t in expect.tools_called_must_not_include if t in tool_names]
    if expect.tools_called_must_not_include:
        out.add(
            "tools_called_must_not_include", not forbidden, f"forbidden tools called: {forbidden}"
        )

    # ---- 引用 ----
    if expect.citations_must_include:
        lacking = [p for p in expect.citations_must_include if p not in cited]
        out.add("citations_must_include", not lacking, f"missing citations: {lacking}")
    if expect.citations_must_be_empty:
        out.add("citations_must_be_empty", not cited, f"unexpected citations: {sorted(cited)}")
    if expect.citations_must_not_be_empty:
        out.add("citations_must_not_be_empty", bool(cited), "no citations")

    # ---- 副作用（来自探针，不来自被测方）----
    if expect.db_side_effects is not None:
        if expect.db_side_effects == "none":
            bad = effects & BIZ_WRITE_KINDS
            out.add("db_side_effects", not bad, f"unexpected side effects: {sorted(bad)}")
        else:
            out.add(
                "db_side_effects",
                expect.db_side_effects in effects,
                f"expected {expect.db_side_effects}, observed {sorted(effects)}",
            )

    # ---- 回复文本 ----
    absent = [s for s in expect.response_must_contain if s not in result.reply]
    if expect.response_must_contain:
        out.add("response_must_contain", not absent, f"missing: {absent}")
    present = [s for s in expect.response_must_not_contain if s in result.reply]
    if expect.response_must_not_contain:
        out.add("response_must_not_contain", not present, f"leaked: {present}")
    if expect.no_certainty_wording:
        words = find_certainty_words(result.reply)
        out.add("no_certainty_wording", not words, f"certainty words: {words}")

    return out
