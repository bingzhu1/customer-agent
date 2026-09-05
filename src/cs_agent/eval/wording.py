"""低置信措辞检查（PRD §12.4）：低置信回答不得出现确定性措辞。关键词级，确定性检查。"""

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
    """返回回答中出现的确定性措辞（按词表顺序，去重）。空列表表示通过。"""
    return [w for w in CERTAINTY_WORDS if w in text]
