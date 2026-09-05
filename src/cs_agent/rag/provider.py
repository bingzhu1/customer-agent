"""按配置挑 embedding provider。

灌库（`python -m cs_agent.rag.ingest`）与检索必须用**同一个** provider，
否则向量空间对不上、分数没有意义。把选择逻辑收在这一处，就不会两边挑得不一样。

没配 `OPENAI_API_KEY` 时退到 `FakeEmbeddings`（确定性哈希向量，不触网）：
本机与 CI 照样能跑通完整检索路径。它的分数分布与真 provider 不同，
**τ 阈值因此是 provider 相关的**——换成 OpenAI 必须按 ADR-0007 重新标定。
"""

from __future__ import annotations

from cs_agent.rag.embeddings import EmbeddingProvider, FakeEmbeddings, OpenAIEmbeddings
from cs_agent.rag.retriever import PolicyRetriever
from cs_agent.settings import get_settings

#: `FakeEmbeddings` 的 τ，用 policies/ 全量语料 + golden 里的问句实测标定（2026-09-05）：
#:
#: | 类别 | 分数区间 |
#: |---|---|
#: | 政策覆盖的主题（退款窗口、保修、会员、投诉） | 0.35 – 0.69 |
#: | 政策未覆盖的主题（价格保护、发票、注销、关税） | 0.10 – 0.27 |
#:
#: 两类之间没有干净的间隔（最低覆盖 0.214 vs 最高未覆盖 0.268），取 0.28 / 0.40
#: 是在"未覆盖不得被当成有据可依"与"覆盖主题不要被误判成低置信"之间取的折中。
#: 换成 OpenAI provider 后**必须重新标定**——余弦分布完全不同（ADR-0007）。
FAKE_TAU_LOW = 0.28
FAKE_TAU_HIGH = 0.40


def default_provider() -> EmbeddingProvider:
    """按 `EMBEDDING_PROVIDER` 显式选择。默认 `fake`：本机与 CI 不触网也能跑通全链路。"""
    if get_settings().embedding_provider == "openai":
        return OpenAIEmbeddings()
    return FakeEmbeddings()


def default_retriever() -> PolicyRetriever:
    """按 provider 配 τ。阈值是 provider 相关的，不能跨 provider 复用。"""
    provider = default_provider()
    if isinstance(provider, FakeEmbeddings):
        return PolicyRetriever(provider, tau_low=FAKE_TAU_LOW, tau_high=FAKE_TAU_HIGH)
    # 真 provider 用 Settings 里的值；换 provider 后**必须重新灌库并重新标定**（ADR-0007）
    return PolicyRetriever(provider)
