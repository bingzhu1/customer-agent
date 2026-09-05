"""引用后置校验纯函数（FR-304 / FR-305）。"""

import pytest

from cs_agent.rag.citations import citations_are_valid, validate_citations


@pytest.mark.parametrize(
    ("cited", "retrieved", "missing"),
    [
        ([], ["REFUND-STD-001"], []),
        (["REFUND-STD-001"], ["REFUND-STD-001", "MEMBER-GOLD-001"], []),
        (["REFUND-STD-001"], [], ["REFUND-STD-001"]),
        (["REFUND-STD-001", "FAKE-001"], ["REFUND-STD-001"], ["FAKE-001"]),
        ([" REFUND-STD-001 "], ["REFUND-STD-001"], []),
        (["REFUND-STD-001"], [" REFUND-STD-001 "], []),
        (["", "  "], ["REFUND-STD-001"], []),
    ],
)
def test_missing_citations(cited: list[str], retrieved: list[str], missing: list[str]) -> None:
    assert validate_citations(cited, retrieved) == missing


def test_missing_is_deduped_and_ordered() -> None:
    cited = ["B-001", "A-001", "B-001", "C-001"]
    assert validate_citations(cited, ["A-001"]) == ["B-001", "C-001"]


def test_case_is_not_normalized() -> None:
    """policy_id 在 schema 里恒为大写；小写形式说明是模型自己编的，必须判失败。"""
    assert validate_citations(["refund-std-001"], ["REFUND-STD-001"]) == ["refund-std-001"]


def test_boolean_helper_matches_list_version() -> None:
    assert citations_are_valid(["A-001"], ["A-001"])
    assert not citations_are_valid(["A-001"], ["B-001"])


def test_accepts_any_iterable() -> None:
    assert validate_citations(iter(["A-001"]), iter(["A-001"])) == []
