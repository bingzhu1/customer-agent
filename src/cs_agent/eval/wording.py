"""向后兼容的转发：词表本体已移到 `cs_agent.domain.wording`。"""

from cs_agent.domain.wording import CERTAINTY_WORDS, find_certainty_words

__all__ = ["CERTAINTY_WORDS", "find_certainty_words"]
