"""chunker：确定性、切分规则、metadata 完整性（FR-301、PRD §11 ①）。"""

from datetime import date
from pathlib import Path

import pytest

from cs_agent.policy.schema import Condition, PolicySet, load_policies
from cs_agent.rag.chunker import chunk_policies, chunk_rule, render_condition, render_rule_card

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"
REQUIRED_METADATA = {
    "policy_id",
    "policy_version",
    "domain",
    "anchor",
    "effective_date",
    "kind",
    "title",
}


@pytest.fixture(scope="module")
def policies() -> PolicySet:
    return load_policies(POLICY_DIR)


def test_chunking_is_byte_identical_across_runs() -> None:
    """同一份 YAML 生成两次必须逐字节相同——ingest 的幂等判断依赖这一点。"""
    first = [c.to_json() for c in chunk_policies(load_policies(POLICY_DIR))]
    second = [c.to_json() for c in chunk_policies(load_policies(POLICY_DIR))]
    assert first == second
    assert "\n".join(first).encode("utf-8") == "\n".join(second).encode("utf-8")


def test_one_rule_card_plus_one_chunk_per_faq(policies: PolicySet) -> None:
    for rule in policies.rules:
        chunks = chunk_rule(rule)
        assert len(chunks) == 1 + len(rule.faq)
        assert chunks[0].kind == "rule_card"
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert {c.kind for c in chunks[1:]} <= {"faq"}
        for i, entry in enumerate(rule.faq):
            assert entry.q in chunks[i + 1].content
            assert entry.a in chunks[i + 1].content


def test_chunk_primary_key_is_unique(policies: PolicySet) -> None:
    keys = [(c.policy_id, c.policy_version, c.chunk_index) for c in chunk_policies(policies)]
    assert len(keys) == len(set(keys))


def test_metadata_fields_complete_and_serializable(policies: PolicySet) -> None:
    for chunk in chunk_policies(policies):
        assert set(chunk.metadata) == REQUIRED_METADATA
        assert chunk.metadata["policy_id"] == chunk.policy_id
        assert chunk.metadata["policy_version"] == chunk.policy_version
        assert chunk.metadata["anchor"] == chunk.anchor
        assert chunk.metadata["domain"] == chunk.anchor.split("#", 1)[0]
        date.fromisoformat(chunk.metadata["effective_date"])
        assert chunk.metadata_json().startswith("{")


def test_rule_card_keeps_human_text_and_renders_conditions(policies: PolicySet) -> None:
    """rule card = 条件的人话渲染 + human_text 原文，原文不得被摘要掉。"""
    rule = policies.by_id("REFUND-STD-001")
    card = render_rule_card(rule)
    assert rule.human_text.strip() in card
    assert "REFUND-STD-001 v3" in card
    assert "签收后天数不超过 30" in card
    assert "商品状态属于未使用、未拆封" in card
    assert "标准商品" in card and "普通会员" in card
    assert "200" in card  # 自动办理上限
    assert "2026-05-15" in card


def test_informational_rule_renders_no_conditions(policies: PolicySet) -> None:
    card = render_rule_card(policies.by_id("COMPLAINT-SLA-001"))
    assert "【生效条件】" not in card
    assert "全部用户与全部商品类别" in card
    assert "不参与退款资格判定" in card


@pytest.mark.parametrize(
    ("field", "condition", "expected"),
    [
        ("days_since_delivery", Condition(lte=30), "签收后天数不超过 30"),
        ("order_delivered", Condition(eq=False), "订单是否已签收等于否"),
        ("item_condition", Condition(**{"in": ["unused"]}), "商品状态属于未使用"),
        ("unknown_field", Condition(gte=1), "unknown_field不少于 1"),
    ],
)
def test_render_condition(field: str, condition: Condition, expected: str) -> None:
    assert render_condition(field, condition) == expected
