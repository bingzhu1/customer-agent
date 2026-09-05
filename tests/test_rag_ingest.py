"""ingest 集成测试：连本 worktree 的开发库 `cs_agent_p2`，用 FakeEmbeddings（不触网）。

覆盖 FR-301 的验收标准："修改 YAML 后重跑 ingestion，chunk 内容与版本号同步变化"。

注意：本文件会真的重写 `agent.policy_chunks`（该表完全由 ingest 拥有，是 YAML 的投影，
不含任何人工数据），跑完后表里是 Fake 向量；要真向量请重跑
`uv run python -m cs_agent.rag.ingest`。
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from cs_agent.db.base import get_engine
from cs_agent.policy.schema import load_policies
from cs_agent.rag.chunker import chunk_policies
from cs_agent.rag.embeddings import FakeEmbeddings
from cs_agent.rag.ingest import ingest_policies

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"
PROVIDER = FakeEmbeddings()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """连不上库或没跑过 0003 迁移就 skip，不让本机环境把整个测试套拖挂。"""
    eng = get_engine()
    try:
        with eng.connect() as conn:
            dtype = conn.execute(
                text(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_schema='agent' AND table_name='policy_chunks' "
                    "AND column_name='embedding'"
                )
            ).scalar()
    except OperationalError as exc:  # pragma: no cover - 取决于本机环境
        pytest.skip(f"数据库不可达，跳过 RAG 集成测试：{exc.__class__.__name__}")
    if dtype != "vector":
        pytest.skip("policy_chunks.embedding 还不是 vector，请先跑 make migrate")
    yield eng
    # 收尾：把库恢复成当前 YAML 的完整投影，别给别的测试 / 手工验证留半套语料
    ingest_policies(POLICY_DIR, provider=PROVIDER, engine=eng)


def _rows(engine: Engine, policy_id: str) -> list[tuple[int, int, str]]:
    with engine.connect() as conn:
        return [
            (r[0], r[1], r[2])
            for r in conn.execute(
                text(
                    "SELECT policy_version, chunk_index, content FROM agent.policy_chunks "
                    "WHERE policy_id = :pid ORDER BY policy_version, chunk_index"
                ),
                {"pid": policy_id},
            )
        ]


def test_ingest_row_count_equals_chunk_count(engine: Engine) -> None:
    report = ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    expected = len(chunk_policies(load_policies(POLICY_DIR)))
    assert report.chunks == expected
    assert report.total_rows == expected
    assert report.policies == len(load_policies(POLICY_DIR).rules)


def test_reingest_is_idempotent(engine: Engine) -> None:
    first = ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    second = ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    assert first.total_rows == second.total_rows
    assert second.pruned == 0
    with engine.connect() as conn:
        dupes = conn.execute(
            text(
                "SELECT count(*) FROM (SELECT policy_id, policy_version, chunk_index "
                "FROM agent.policy_chunks GROUP BY 1,2,3 HAVING count(*) > 1) d"
            )
        ).scalar_one()
    assert dupes == 0


def test_every_row_has_an_embedding(engine: Engine) -> None:
    ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    with engine.connect() as conn:
        missing = conn.execute(
            text("SELECT count(*) FROM agent.policy_chunks WHERE embedding IS NULL")
        ).scalar_one()
        dims = (
            conn.execute(text("SELECT DISTINCT vector_dims(embedding) FROM agent.policy_chunks"))
            .scalars()
            .all()
        )
    assert missing == 0
    assert dims == [PROVIDER.dimensions]


def test_editing_yaml_updates_content_and_version(engine: Engine, tmp_path: Path) -> None:
    """FR-301 验收标准。改文案 + 升版本后重跑，库里必须是新内容新版本，旧版本被清掉。"""
    ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    before = _rows(engine, "REFUND-STD-001")
    assert {v for v, _, _ in before} == {3}

    edited = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, edited)
    target = edited / "refund.yaml"
    raw = target.read_text(encoding="utf-8")
    raw = raw.replace("- id: REFUND-STD-001\n  version: 3", "- id: REFUND-STD-001\n  version: 4", 1)
    raw = raw.replace("自签收之日起 30 天内（含第 30 天）", "自签收之日起 45 天内（含第 45 天）", 1)
    target.write_text(raw, encoding="utf-8")

    ingest_policies(edited, provider=PROVIDER, engine=engine)
    after = _rows(engine, "REFUND-STD-001")
    assert {v for v, _, _ in after} == {4}, "旧版本 chunk 必须被清掉，否则检索会召回过期条款"
    assert any("45 天内" in content for _, _, content in after)
    assert not any("30 天内（含第 30 天）" in content for _, _, content in after)


def test_no_prune_keeps_old_versions(engine: Engine, tmp_path: Path) -> None:
    ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    edited = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, edited)
    target = edited / "refund.yaml"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "- id: REFUND-STD-001\n  version: 3", "- id: REFUND-STD-001\n  version: 4", 1
        ),
        encoding="utf-8",
    )
    ingest_policies(edited, provider=PROVIDER, engine=engine, prune=False)
    assert {v for v, _, _ in _rows(engine, "REFUND-STD-001")} == {3, 4}


def test_removing_a_policy_prunes_its_chunks(engine: Engine, tmp_path: Path) -> None:
    ingest_policies(POLICY_DIR, provider=PROVIDER, engine=engine)
    edited = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, edited)
    (edited / "complaint.yaml").unlink()
    report = ingest_policies(edited, provider=PROVIDER, engine=engine)
    assert report.pruned == 4  # rule card + 3 条 FAQ
    assert _rows(engine, "COMPLAINT-SLA-001") == []
