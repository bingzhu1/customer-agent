"""V5 +Memory：在 V3 基础上打开长期记忆（PRD §12.6 V5 行）。

相对 V3 的增量只有记忆那一层：

- `ingest` 检索 `user_memory` 并把命中经 `render_hints` 注入 respond 的 prompt
  （带"非权威提示"声明，只影响称呼与语气）；
- `persist` 抽取本轮的偏好类信息并 `upsert`（带置信度、来源 thread、TTL）。

CaseFacts 那一层 V1 / V3 就有（否则多轮里"那个订单"根本接不上），
所以 V3→V5 的差异应当**只能**归因于跨会话的长期记忆，不含会话内事实。

**记忆改变不了判定**：`policy_gate` 与 `decide` 拿不到 `memory_hints`，
投毒专项在 `tests/test_memory_wiring.py` 里反向校验（红线 3、ADR-0009）。

代价要说清楚：抽取是同步的，每轮多一次 Haiku 调用，报表上的
LLM calls / session 与 cost 会比 V3 高——这是记忆的真实成本，不该藏起来。
"""

from __future__ import annotations

from cs_agent.agents.v1_tools import GraphAgent


class V5MemoryAgent(GraphAgent):
    """V5：完整图 + 长期记忆读写。"""

    name = "v5-memory"
    enable_policy_gate = True
    enable_memory = True


AGENT = V5MemoryAgent
