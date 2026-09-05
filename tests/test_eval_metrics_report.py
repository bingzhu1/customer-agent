"""指标推导与报表渲染。用哑 agent 跑真实 golden（无数据库、无 LLM）作为数据源。"""

from __future__ import annotations

from pathlib import Path

from cs_agent.domain.enums import DecisionOutcome as D
from cs_agent.domain.enums import ReasonCode as R
from cs_agent.eval.dummy import AlwaysHumanAgent
from cs_agent.eval.metrics import compute_metrics
from cs_agent.eval.pricing import estimate_cost_usd
from cs_agent.eval.protocol import Usage
from cs_agent.eval.report import render_markdown, write_report
from cs_agent.eval.runner import RunResult, run_dataset
from cs_agent.eval.schema import load_golden
from cs_agent.eval.side_effects import NullSideEffectProbe

GOLDEN = Path("data/golden")


def _dummy_run() -> RunResult:
    return run_dataset(AlwaysHumanAgent(), load_golden(GOLDEN), NullSideEffectProbe())


def test_dummy_agent_runs_all_54_cases_and_only_passes_explicit_escalations() -> None:
    run = _dummy_run()
    assert len(run.cases) == 54
    passed = {c.case.id for c in run.cases if c.passed}
    # 哑 agent 每轮都 REQUIRE_HUMAN / CUSTOMER_ESCALATION_REQUEST：只该通过"明确要人工"类用例
    for cr in run.cases:
        e = cr.case.expect
        wants_human = (
            e.decision is D.REQUIRE_HUMAN and e.reason_code is R.CUSTOMER_ESCALATION_REQUEST
        )
        tools_ok = set(e.tools_called_must_include) <= {"escalate_to_human"}
        assert (cr.case.id in passed) == (
            wants_human and tools_ok and not e.response_must_contain
        ), cr.case.id
    assert 1 <= len(passed) <= 3, passed
    assert all(c.error is None for c in run.cases)


def test_metrics_from_dummy_run() -> None:
    run = _dummy_run()
    m = compute_metrics(run.cases, "claude-sonnet-5")
    assert m.task_success.total == 54
    assert m.task_success.hits == run.passed_count
    # 期望 DENY 的越权用例它答 REQUIRE_HUMAN → 决策错 → 计为越权；
    # 注入用例期望值里没有 REQUIRE_HUMAN，故 resistance 为 0
    assert m.authorization_violations > 0
    assert m.over_refunds == 0
    assert m.injection_resistance.total == 4 and m.injection_resistance.hits == 0
    assert m.escalation_recall.value == 1.0  # 全都转人工，召回必然 100%
    assert 0 < (m.escalation_precision.value or 0) < 1
    assert m.entity_retention.total == 8 and m.entity_retention.hits == 0
    assert m.tokens_per_session == 0 and m.cost_per_session_usd == 0
    assert set(m.by_category) == {
        "policy",
        "order",
        "security",
        "escalation",
        "memory",
        "rag",
        "idempotency",
    }
    assert not m.hard_gates_passed
    d = m.to_dict()
    assert d["hard_gates_passed"] is False and d["task_success"]["total"] == 54


def test_report_renders_and_writes(tmp_path: Path) -> None:
    run = _dummy_run()
    m = compute_metrics(run.cases, "claude-sonnet-5")
    md = render_markdown(run, m, "abc1234")
    assert "# Eval 报表" in md and "abc1234" in md
    assert "authorization violation" in md and "❌" in md
    assert "| SEC-001 |" in md  # 失败用例列出
    md_path, json_path = write_report(run, m, "abc1234", tmp_path)
    assert md_path.exists() and json_path.exists()
    assert (tmp_path / f"latest_{run.agent_name}.md").read_text() == md_path.read_text()
    assert json_path.stat().st_size > 1000


def test_cost_estimate_uses_most_expensive_model_and_cache_factors() -> None:
    u = Usage(
        input_tokens=1_000_000, output_tokens=0, models=["claude-haiku-4-5", "claude-sonnet-5"]
    )
    assert estimate_cost_usd(u, "claude-haiku-4-5") == 2.0
    cached = Usage(cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000)
    assert abs(estimate_cost_usd(cached, "claude-sonnet-5") - (0.2 + 2.5)) < 1e-9
    assert estimate_cost_usd(Usage(), "unknown-model") == 0.0
