"""写路径内核：提议 → 确认 / 审批 → 幂等执行 → 审计（PRD §5.3–5.4、FR-501~509）。

分层：本包是 Service 层，只依赖 `db` / `domain` / `policy`，不认识 HTTP 与 LLM。
"""

from cs_agent.actions.errors import (
    ActionError,
    ActionExpiredError,
    ActionNotFoundError,
    ActionStateError,
)
from cs_agent.actions.proposal import (
    ActionProposal,
    ActionType,
    InvalidProposalError,
    canonical_params,
    idempotency_key,
)
from cs_agent.actions.service import ActionRecord, ActionService, ExecutionOutcome
from cs_agent.actions.state import (
    ActionEvent,
    ActionStatus,
    InvalidTransitionError,
    can_transition,
    transition,
)

__all__ = [
    "ActionError",
    "ActionEvent",
    "ActionExpiredError",
    "ActionNotFoundError",
    "ActionProposal",
    "ActionRecord",
    "ActionService",
    "ActionStateError",
    "ActionStatus",
    "ActionType",
    "ExecutionOutcome",
    "InvalidProposalError",
    "InvalidTransitionError",
    "canonical_params",
    "idempotency_key",
    "transition",
    "can_transition",
]
