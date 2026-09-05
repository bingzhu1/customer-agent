"""决策层（PRD §9.4 升级矩阵）。对外只暴露 `DecisionInput` / `Decision` / `decide`。"""

from cs_agent.decision.matrix import Decision, DecisionInput, decide

__all__ = ["Decision", "DecisionInput", "decide"]
