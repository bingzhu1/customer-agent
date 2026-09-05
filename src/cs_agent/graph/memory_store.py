"""CaseFacts 的存放位置（PRD §10 第 ② 层，记忆四条不变式）。

两种实现，接口一样：

- `DbCaseFactsStore`：写 `agent.case_state`，供 API 会话跨轮、跨进程恢复；
- `InMemoryCaseFactsStore`：只活在本次会话对象里，供 eval 与单测使用
  （eval 的会话没有 `agent.threads` 行，写库会撞外键）。

两条约束刻在接口上：

1. `CaseFacts` **只能被确定性代码写入**（不变式 2）：本模块只提供整体读写，
   合并逻辑在 `memory.case_facts` 的纯函数里，LLM 碰不到；
2. 压缩只作用于叙述部分（不变式 3）：这里根本没有 narrative 的写接口。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from cs_agent.memory.case_facts import CaseFacts
from cs_agent.memory.case_state import CaseStateRepo


class CaseFactsStore(Protocol):
    def load(self) -> CaseFacts: ...

    def save(self, facts: CaseFacts) -> None: ...


class InMemoryCaseFactsStore:
    """进程内保存。会话对象销毁即消失——eval 每个用例开新会话，正合适。"""

    def __init__(self, facts: CaseFacts | None = None) -> None:
        self._facts = facts or CaseFacts()

    def load(self) -> CaseFacts:
        return self._facts

    def save(self, facts: CaseFacts) -> None:
        self._facts = facts


class DbCaseFactsStore:
    """写 `agent.case_state`。`thread_id` 必须是已存在的会话，否则外键会拦下。"""

    def __init__(self, thread_id: UUID, repo: CaseStateRepo | None = None) -> None:
        self._thread_id = thread_id
        self._repo = repo or CaseStateRepo()

    def load(self) -> CaseFacts:
        return self._repo.load_facts(self._thread_id)

    def save(self, facts: CaseFacts) -> None:
        self._repo.save_facts(self._thread_id, facts)
