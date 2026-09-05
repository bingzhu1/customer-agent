"""三路夹具的交叉一致性：golden 引用的 id 必须真实存在于策略 YAML 与 seed 数据中。

单路测试各自对着契约文档写死期望；本文件对着**彼此的实际产物**核对，
防止契约改了一处而另一路没跟上。
"""

import re
from pathlib import Path

import pytest

from cs_agent.eval.schema import GoldenDataset, load_golden
from cs_agent.policy.schema import PolicySet, load_policies
from cs_agent.seed.biz_seed import build_seed

NONEXISTENT_ORDER_ID = 77777  # 契约 §2：故意不存在，用于存在性探测用例


@pytest.fixture(scope="module")
def golden() -> GoldenDataset:
    return load_golden(Path("data/golden"))


@pytest.fixture(scope="module")
def policies() -> PolicySet:
    return load_policies(Path("policies"))


@pytest.fixture(scope="module")
def seed_ids() -> tuple[set[int], set[int], set[int]]:
    seed = build_seed()
    order_ids = {o.id for o in seed.orders}
    ticket_ids = {t.id for t in seed.tickets}
    user_ids = {u.id for u in seed.users}
    return order_ids, ticket_ids, user_ids


def test_golden_citations_exist_in_policies(golden: GoldenDataset, policies: PolicySet) -> None:
    known = {r.id for r in policies.rules}
    for case in golden.cases:
        for pid in case.expect.citations_must_include:
            assert pid in known, f"{case.id} cites unknown policy {pid}"
        for turn in case.turns:
            if turn.expect:
                for pid in turn.expect.citations_must_include:
                    assert pid in known, f"{case.id} cites unknown policy {pid}"


def test_golden_auth_users_exist_in_seed(
    golden: GoldenDataset, seed_ids: tuple[set[int], set[int], set[int]]
) -> None:
    _, _, user_ids = seed_ids
    for case in golden.cases:
        assert case.auth.user_id in user_ids, f"{case.id} auth user {case.auth.user_id} not seeded"


def test_golden_order_ids_exist_in_seed(
    golden: GoldenDataset, seed_ids: tuple[set[int], set[int], set[int]]
) -> None:
    order_ids, ticket_ids, _ = seed_ids
    for case in golden.cases:
        text = " ".join(t.user or "" for t in case.turns)
        for token in re.findall(r"\b(\d{4,6})\b", text):
            n = int(token)
            if n == NONEXISTENT_ORDER_ID or n in ticket_ids:
                continue
            if 1000 <= n <= 9999 and n not in ticket_ids and n not in order_ids:
                # 4 位数可能是金额或年份，只在明显像工单号时才要求存在
                continue
            assert n in order_ids, f"{case.id} mentions order {n} that is not seeded"


def test_policy_rules_cover_every_reason_code_used_by_golden(
    golden: GoldenDataset, policies: PolicySet
) -> None:
    """golden 期望的 POLICY_VIOLATION_* / POLICY_SATISFIED 必须有某条规则能产出它。"""
    producible = set()
    for r in policies.rules:
        producible.update(
            c for c in (r.reason_code_on_pass, r.reason_code_on_fail) if c is not None
        )
        producible.update(r.fail_reason_codes.values())
    wanted = set()
    for case in golden.cases:
        codes = [case.expect.reason_code, *case.expect.reason_code_any_of]
        wanted.update(c for c in codes if c is not None and c.value.startswith("POLICY_"))
    missing = wanted - producible
    assert not missing, f"golden expects reason codes no policy can produce: {sorted(missing)}"
