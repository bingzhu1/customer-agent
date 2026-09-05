"""embedding provider：FakeEmbeddings 的确定性与词面相关性、OpenAIEmbeddings 的批量与参数。

全部不触网：OpenAI 侧用注入的 mock client。
"""

from typing import Any

import pytest

from cs_agent.rag.embeddings import (
    EmbeddingProvider,
    FakeEmbeddings,
    OpenAIEmbeddings,
    format_vector,
)
from cs_agent.settings import Settings

DIM = 32


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class _FakeOpenAIClient:
    """最小 mock：记录每次调用的参数，按 input 顺序返回等长向量。"""

    def __init__(self, dim: int = DIM, *, shuffle: bool = False) -> None:
        self.dim = dim
        self.shuffle = shuffle
        self.calls: list[dict[str, Any]] = []
        self.embeddings = self

    def create(self, *, model: str, input: list[str], dimensions: int) -> Any:  # noqa: A002
        self.calls.append({"model": model, "input": list(input), "dimensions": dimensions})
        items = [
            type("Item", (), {"index": i, "embedding": [float(i)] * dimensions})()
            for i in range(len(input))
        ]
        if self.shuffle:  # 服务端不保证顺序，provider 必须自己按 index 排
            items.reverse()
        return type("Response", (), {"data": items})()


def _settings(dim: int = DIM) -> Settings:
    return Settings(embedding_model="text-embedding-3-small", embedding_dimensions=dim)


def test_both_implementations_satisfy_provider_protocol() -> None:
    for provider in (FakeEmbeddings(dimensions=DIM), OpenAIEmbeddings(settings=_settings())):
        assert isinstance(provider, EmbeddingProvider)


def test_fake_is_deterministic_and_normalized() -> None:
    a, b = FakeEmbeddings(dimensions=DIM), FakeEmbeddings(dimensions=DIM)
    text = "退款政策是多少天"
    assert a.embed_query(text) == b.embed_query(text)
    assert a.embed([text, text]) == [a.embed_query(text), a.embed_query(text)]
    assert len(a.embed_query(text)) == DIM
    assert _cos(a.embed_query(text), a.embed_query(text)) == pytest.approx(1.0)


def test_fake_handles_empty_text() -> None:
    v = FakeEmbeddings(dimensions=DIM).embed_query("   ")
    assert len(v) == DIM
    assert _cos(v, v) == pytest.approx(1.0)
    assert FakeEmbeddings(dimensions=DIM).embed([]) == []


def test_fake_lexical_overlap_raises_similarity() -> None:
    """检索测试要能断言"退款问题命中退款政策"，Fake 就必须带词面信号。"""
    p = FakeEmbeddings(dimensions=1536)
    query = p.embed_query("退款政策多少天")
    refund = p.embed_query("标准商品退款规则：签收后 30 天内可以退款")
    shipping = p.embed_query("包裹丢失的赔付说明与承运商查询流程")
    assert _cos(query, refund) > _cos(query, shipping)


def test_fake_dimensions_default_from_settings() -> None:
    assert FakeEmbeddings().dimensions == Settings().embedding_dimensions


def test_openai_passes_model_and_dimensions_from_settings() -> None:
    client = _FakeOpenAIClient()
    provider = OpenAIEmbeddings(client=client, settings=_settings())
    out = provider.embed(["a", "b"])
    assert len(out) == 2 and len(out[0]) == DIM
    assert client.calls == [
        {"model": "text-embedding-3-small", "input": ["a", "b"], "dimensions": DIM}
    ]


def test_openai_batches_by_batch_size() -> None:
    client = _FakeOpenAIClient()
    provider = OpenAIEmbeddings(client=client, settings=_settings(), batch_size=2)
    out = provider.embed([f"t{i}" for i in range(5)])
    assert len(out) == 5
    assert [c["input"] for c in client.calls] == [["t0", "t1"], ["t2", "t3"], ["t4"]]


def test_openai_restores_order_by_index() -> None:
    """服务端乱序返回时不能错位——错位等于把 A 的向量写到 B 的 chunk 上。"""
    provider = OpenAIEmbeddings(
        client=_FakeOpenAIClient(shuffle=True), settings=_settings(), batch_size=3
    )
    out = provider.embed(["x", "y", "z"])
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]


def test_openai_skips_request_on_empty_input() -> None:
    client = _FakeOpenAIClient()
    assert OpenAIEmbeddings(client=client, settings=_settings()).embed([]) == []
    assert client.calls == []


def test_format_vector_is_pgvector_literal() -> None:
    assert format_vector([1.0, -0.5]) == "[1.0,-0.5]"
