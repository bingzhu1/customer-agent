"""模拟退款服务（FR-506）。写 `biz.refunds`，`simulated = true`。

**本服务不做任何资格判断。** 能不能退是策略引擎的结论，金额上限是决策层的结论；
这里只负责把已经定好的一笔退款落库，并把判定依据（`policy_id` / `policy_version` /
`reason_code`）一并写进去——审计要能回答"依据哪条规则退的"（FR-507）。

把资格判断放进这里会重开一条绕过策略引擎的路径，属于红线 2 的违反。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from cs_agent.db.models.biz import Refund
from cs_agent.domain.enums import RefundStatus
from cs_agent.policy.engine import PolicyVerdict


class RefundService:
    """一次事务一个实例：`RefundService(session)`。不自行提交，事务边界由调用方掌握。"""

    #: 本阶段不接真实支付通道，写库时恒为 True。
    SIMULATED = True

    def __init__(self, session: Session) -> None:
        self._session = session

    def execute_refund(
        self,
        *,
        order_id: int,
        user_id: int,
        amount: Decimal,
        verdict: PolicyVerdict,
        now: datetime,
    ) -> Refund:
        """写入一条 `succeeded` 的模拟退款。

        `user_id` 由调用方从 `AuthContext` 传入，不从对话或 params 取（红线 1）；
        `policy_id` / `policy_version` / `reason_code` 一律取本轮判定，不另行检索（ADR-0006）。
        """
        refund = Refund(
            order_id=order_id,
            user_id=user_id,
            amount=amount,
            status=RefundStatus.SUCCEEDED.value,
            reason_code=verdict.reason_code.value,
            policy_id=verdict.policy_id,
            policy_version=verdict.policy_version,
            simulated=self.SIMULATED,
            created_at=now,
            executed_at=now,
        )
        self._session.add(refund)
        # flush 而不是 commit：拿到自增 id 写进 agent_actions.result，事务仍由调用方收口
        self._session.flush()
        return refund
