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


#: 英文模板的确定性措辞。只给 `tests/test_templates.py` 反向校验英文骨架用；
#: **不并入** `CERTAINTY_WORDS`——那份词表由 eval 拿去检查模型自由生成的回答，
#: 把 must / always 加进去会误伤正常英文（"you must reply 确认"）。
CERTAINTY_WORDS_EN: tuple[str, ...] = (
    "definitely",
    "guarantee",
    "guaranteed",
    "certainly",
    "absolutely",
    "100%",
    "without doubt",
    "no doubt",
    "always",
)


def find_certainty_words_en(text: str) -> list[str]:
    lowered = text.lower()
    return [w for w in CERTAINTY_WORDS_EN if w in lowered]
