"""向量化 provider（PRD §11 ②）。

Anthropic 不提供 embedding 接口，向量化走独立 provider：默认 OpenAI
`text-embedding-3-small`（1536 维），模型名与维度都从 `Settings` 读，不硬编码。

两个实现：
- `OpenAIEmbeddings`：真调用，client 依赖注入（测试传 mock，生产传真 client），批量发送；
- `FakeEmbeddings`：确定性哈希向量，同维度，**不触网**，所有单测与 CI 用它。

`FakeEmbeddings` 用 hashing trick 而不是"整段文本一个哈希"：这样词面重叠会体现为
余弦相似度，检索测试才能断言"退款问题命中退款政策"，而不是只能断言"没报错"。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from cs_agent.settings import Settings, get_settings

#: 英文按词、中文按单字切分。朴素但确定——`FakeEmbeddings` 只需要稳定的词面信号。
_CJK = r"一-鿿"
_TOKEN_RE = re.compile(rf"[a-zA-Z0-9]+|[{_CJK}]")

#: OpenAI embeddings 单次请求的输入条数上限留有余量，避免超长 body。
_BATCH_SIZE = 128


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化接口。实现必须是确定性的：同一文本多次调用返回同一向量。"""

    @property
    def dimensions(self) -> int:
        """向量维度，必须与 `policy_chunks.embedding` 的 `vector(N)` 一致。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量向量化。返回顺序与入参一一对应。"""

    def embed_query(self, text: str) -> list[float]:
        """单条查询向量化。语义上与 `embed([text])[0]` 等价。"""


class OpenAIEmbeddings:
    """OpenAI 向量化。`client` 注入以便测试替换；不传则惰性构造真 client。

    惰性构造的意义：导入本模块不应要求配好 `OPENAI_API_KEY`，
    否则跑纯单测的 CI 也会被逼着配密钥。
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        settings: Settings | None = None,
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._batch_size = batch_size

    @property
    def model(self) -> str:
        return self._settings.embedding_model

    @property
    def dimensions(self) -> int:
        return self._settings.embedding_dimensions

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI  # 惰性导入：未装 / 未配密钥时不影响其余模块

            self._client = OpenAI(api_key=self._settings.openai_api_key)
        return self._client

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = client.embeddings.create(
                model=self.model, input=batch, dimensions=self.dimensions
            )
            # 不假设服务端按序返回：显式按 index 排序后再取 embedding
            items = sorted(response.data, key=lambda d: d.index)
            if len(items) != len(batch):
                raise RuntimeError(f"embedding 返回条数 {len(items)} 与请求 {len(batch)} 不一致")
            out.extend([float(x) for x in item.embedding] for item in items)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class FakeEmbeddings:
    """确定性哈希向量，仅供测试与本地标定预演。**不要用于生产检索**。

    做法：把文本切成 token，每个 token 用 sha256 定位到一个维度并按符号累加
    （`hash()` 带进程随机盐，不能用），最后 L2 归一化。于是：
    - 同一文本任何进程、任何次数都得到同一向量；
    - 词面重叠越多，余弦相似度越高；
    - 全部为空的文本落到一个固定的零向量替代值，避免除零。
    """

    def __init__(self, *, dimensions: int | None = None) -> None:
        self._dimensions = dimensions or get_settings().embedding_dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self._dimensions
        tokens = _TOKEN_RE.findall(text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            # 空文本：给一个固定的单位向量，保证"确定 + 可归一化"，相似度接近 0
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def format_vector(vector: Sequence[float]) -> str:
    """pgvector 的文本字面量。入库/查询都走 `:param::vector`，不引入 pgvector 的 Python 包。"""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"
