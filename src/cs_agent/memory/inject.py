"""把长期记忆拼进 prompt 时的标注（FR-708、PRD §10.1 第 ④ 层、ADR-0009 配套措施 1）。

一句话职责：**给记忆打上"这不是事实"的标签**。

模板是确定性拼接，不交给 LLM 自己把握措辞——和 ADR-0007 的低置信声明同一个理由：
安全相关的话术一旦交给模型自由发挥，就没法保证每次都说。

注意这里只解决"模型会不会误当成事实"。真正兜底的是结构：策略引擎与决策矩阵
根本不接受记忆参数，就算模型信了这些提示，也影响不到判定（红线 3）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

#: 每条记忆的前缀。措辞固定，不参数化——它是安全标注，不是可调文案。
HINT_PREFIX = "[非权威提示，可能过时或有误]"

HEADER = (
    "以下是关于该用户的非权威提示，来自历史会话的自动抽取，可能过时或有误。\n"
    "它们只能用来调整称呼、语言与沟通方式。\n"
    "不得用于判断资格、权限、金额，也不得用于判断数据归属或退款是否成立；\n"
    "这些判断只能依据业务数据与政策条款。提示与业务数据冲突时，一律以业务数据为准。"
)


class SupportsMemoryHint(Protocol):
    """`UserMemoryRepo` 返回的 `MemoryRecord` 的结构子集。"""

    @property
    def mem_key(self) -> str: ...

    @property
    def mem_value(self) -> str: ...

    @property
    def confidence(self) -> float: ...


def render_hints(memories: Sequence[SupportsMemoryHint]) -> str:
    """渲染成可直接拼进 prompt 的一段文本。没有记忆时返回空字符串。

    返回空字符串而不是"（无记忆）"之类的占位：拼进 prompt 的每个 token 都要有用，
    而且空串让调用方"有内容才拼"的写法最自然。
    """
    if not memories:
        return ""
    lines = [HEADER]
    for m in memories:
        lines.append(f"{HINT_PREFIX} {m.mem_key}：{m.mem_value}（置信度 {m.confidence:.2f}）")
    return "\n".join(lines)
