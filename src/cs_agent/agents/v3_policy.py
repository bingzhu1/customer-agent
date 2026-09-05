"""V3 +Policy：在 V1 基础上打开 `policy_gate`（PRD §12.6 V3 行）。

相对 V1 的增量只有一条边：`policy_gate` 用**实时查库**的 `PolicyFacts` 调
`policy.engine.evaluate()`，把 `PolicyVerdict` 交给决策矩阵。因此报表上
V1→V3 的差异应当**只能**归因于"能不能确定性地判定退款资格"：

- 超期 / 食品 / 定制 / 已使用 → 由引擎判定后 DENY，并引用做出判定的那条策略；
- 通过且 ≤ max_auto_amount → REQUIRE_CONFIRMATION（停在提议，不执行）；
- 通过但超额 → REQUIRE_HUMAN / AMOUNT_ABOVE_AUTO_LIMIT。

V1 里这些全部落到矩阵规则 9（转人工）——安全，但把本可自动处理的请求也推给了人工。

写路径仍然没有（Phase 4）：`confirm()` 不执行任何写操作，幂等类用例照旧失败。
"""

from __future__ import annotations

from cs_agent.agents.v1_tools import GraphAgent


class V3PolicyAgent(GraphAgent):
    """V3：走完整图 ingest→understand→act→policy_gate→decide→respond。"""

    name = "v3-policy"
    enable_policy_gate = True


AGENT = V3PolicyAgent
