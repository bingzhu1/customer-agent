"""golden dataset 结构与契约一致性检查（docs/phase0-fixtures.md §2 / §5 / §7）。"""

import re
from pathlib import Path

import pytest

from cs_agent.domain.enums import GoldenCategory
from cs_agent.eval.schema import GoldenCase, GoldenDataset, load_golden

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "data" / "golden"

# 契约 §7
EXPECTED_COUNTS: dict[GoldenCategory, int] = {
    GoldenCategory.POLICY: 10,
    GoldenCategory.ORDER: 8,
    GoldenCategory.SECURITY: 10,
    GoldenCategory.ESCALATION: 6,
    GoldenCategory.MEMORY: 8,
    GoldenCategory.RAG: 10,
    GoldenCategory.IDEMPOTENCY: 2,
}

# 契约 §2：golden 允许出现的 order_id
CONTRACT_ORDER_IDS = {*range(82913, 82924), 82930, 82931, 82932, 90210, 90211, 77777}

# 契约 §5：11 个 policy_id
CONTRACT_POLICY_IDS = {
    "REFUND-STD-001",
    "MEMBER-GOLD-001",
    "REFUND-FOOD-001",
    "REFUND-CUSTOM-001",
    "REFUND-UNDELIVERED-001",
    "SHIP-DELAY-001",
    "SHIP-LOST-001",
    "WARRANTY-STD-001",
    "WARRANTY-EXCL-001",
    "MEMBER-BENEFIT-001",
    "COMPLAINT-SLA-001",
}

# 契约 §6
CONTRACT_TOOLS = {
    "get_order",
    "get_shipping",
    "get_ticket",
    "search_policy",
    "request_refund",
    "escalate_to_human",
    "create_ticket",
}

# 契约 §1：auth 允许的 user_id
CONTRACT_USER_IDS = {101, 102, 201, 202}

ORDER_ID_RE = re.compile(r"(?<!\d)\d{5,6}(?!\d)")


@pytest.fixture(scope="module")
def dataset() -> GoldenDataset:
    return load_golden(GOLDEN_DIR)


def _by_cat(dataset: GoldenDataset, cat: GoldenCategory) -> list[GoldenCase]:
    return dataset.by_category()[cat]


def _all_expects(case: GoldenCase) -> list:
    return [case.expect, *[t.expect for t in case.turns if t.expect is not None]]


def test_total_count(dataset: GoldenDataset) -> None:
    assert len(dataset.cases) == 54


def test_per_category_counts(dataset: GoldenDataset) -> None:
    actual = {cat: len(cases) for cat, cases in dataset.by_category().items()}
    assert actual == EXPECTED_COUNTS


def test_one_file_per_category(dataset: GoldenDataset) -> None:
    files = {p.stem for p in GOLDEN_DIR.glob("*.yaml")}
    assert files == {cat.value for cat in GoldenCategory}


def test_ids_are_contiguous_per_category(dataset: GoldenDataset) -> None:
    for cat, cases in dataset.by_category().items():
        nums = sorted(int(c.id.split("-")[1]) for c in cases)
        assert nums == list(range(1, EXPECTED_COUNTS[cat] + 1)), cat


def test_security_and_memory_reviewed_each(dataset: GoldenDataset) -> None:
    for cat in (GoldenCategory.SECURITY, GoldenCategory.MEMORY):
        assert all(c.review == "each" for c in _by_cat(dataset, cat)), cat


def test_rag_has_exactly_four_reviewed_each(dataset: GoldenDataset) -> None:
    each = [c for c in _by_cat(dataset, GoldenCategory.RAG) if c.review == "each"]
    assert len(each) == 4
    # 其中 2 条低置信信息类、2 条资格判定陷阱
    low_conf = [c for c in each if c.expect.confidence == "low"]
    traps = [c for c in each if c.expect.confidence != "low"]
    assert len(low_conf) == 2 and len(traps) == 2
    for c in low_conf:
        assert c.expect.decision == "ANSWER"
        assert c.expect.reason_code == "RETRIEVAL_LOW_CONFIDENCE"
        assert c.expect.no_certainty_wording is True
        # 规则 14 第 3 条：必须有引用（具体 id 或"非空"二者之一）
        assert c.expect.citations_must_include or c.expect.citations_must_not_be_empty, c.id
    for c in traps:
        # 陷阱题绝不允许 ANSWER（无论单值还是 any_of 形式）
        assert c.expect.decision != "ANSWER", c.id
        assert "ANSWER" not in c.expect.decision_any_of, c.id
        assert "RETRIEVAL_LOW_CONFIDENCE" not in c.expect.reason_code_any_of, c.id


def test_order_ids_in_turns_belong_to_contract(dataset: GoldenDataset) -> None:
    for case in dataset.cases:
        for turn in case.turns:
            if turn.user is None:
                continue
            found = {int(m) for m in ORDER_ID_RE.findall(turn.user)}
            assert found <= CONTRACT_ORDER_IDS, f"{case.id}: {found - CONTRACT_ORDER_IDS}"


def test_citations_belong_to_contract(dataset: GoldenDataset) -> None:
    for case in dataset.cases:
        for exp in _all_expects(case):
            unknown = set(exp.citations_must_include) - CONTRACT_POLICY_IDS
            assert not unknown, f"{case.id}: {unknown}"
            assert not (exp.citations_must_include and exp.citations_must_be_empty), case.id


def test_tools_belong_to_contract(dataset: GoldenDataset) -> None:
    for case in dataset.cases:
        for exp in _all_expects(case):
            used = set(exp.tools_called_must_include) | set(exp.tools_called_must_not_include)
            assert used <= CONTRACT_TOOLS, f"{case.id}: {used - CONTRACT_TOOLS}"
            overlap = set(exp.tools_called_must_include) & set(exp.tools_called_must_not_include)
            assert not overlap, f"{case.id}: {overlap}"


def test_auth_users_belong_to_contract(dataset: GoldenDataset) -> None:
    for case in dataset.cases:
        assert case.auth.user_id in CONTRACT_USER_IDS, case.id


def test_security_cases_have_no_side_effects(dataset: GoldenDataset) -> None:
    for case in _by_cat(dataset, GoldenCategory.SECURITY):
        assert case.expect.db_side_effects == "none", case.id
        assert "request_refund" in case.expect.tools_called_must_not_include, case.id


def test_memory_cases_are_long_enough(dataset: GoldenDataset) -> None:
    for case in _by_cat(dataset, GoldenCategory.MEMORY):
        assert len(case.turns) >= 10, case.id
        # 订单号只能出现在前两轮，最后一轮用指代
        for idx, turn in enumerate(case.turns):
            if turn.user and ORDER_ID_RE.search(turn.user):
                assert idx < 2, f"{case.id} turn {idx}: 订单号只允许出现在前两轮"
        assert not ORDER_ID_RE.search(case.turns[-1].user or ""), case.id
        assert "get_order" in case.expect.tools_called_must_include, case.id


def test_idempotency_cases_replay(dataset: GoldenDataset) -> None:
    cases = _by_cat(dataset, GoldenCategory.IDEMPOTENCY)
    for case in cases:
        last = case.turns[-1]
        assert last.confirm is True and last.repeat == 2, case.id
        assert case.expect.reason_code == "IDEMPOTENT_REPLAY", case.id
        assert case.expect.db_side_effects == "refund_created", case.id
    assert sum(c.turns[-1].concurrent for c in cases) == 1


def test_every_case_has_description_and_notes(dataset: GoldenDataset) -> None:
    for case in dataset.cases:
        assert case.description.strip(), case.id
        assert case.notes and case.notes.strip(), case.id
