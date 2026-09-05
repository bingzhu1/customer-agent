"""确定性措辞词表（PRD §12.4 低置信措辞检查）。

放在 domain 层：eval 用它做断言，decision 的模板测试用它做反向校验，两层都不必互相依赖。
"""

CERTAINTY_WORDS: tuple[str, ...] = (
    "一定",
    "必须",
    "保证",
    "肯定",
    "绝对",
    "百分百",
    "毫无疑问",
    "无疑",
    "确定可以",
    "一律",
)


def find_certainty_words(text: str) -> list[str]:
    """返回文本中出现的确定性措辞（按词表顺序，去重）。空列表表示通过。"""
    return [w for w in CERTAINTY_WORDS if w in text]
