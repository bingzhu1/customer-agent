"""Service 层：业务逻辑与事务边界。不关心 HTTP，也不关心 LLM 概念。

**这里不做 re-export**：`chat` 依赖 `actions`，而 `actions.service` 依赖
`services.refund`，包级 `from .chat import ChatService` 会让这两条边形成循环导入。
各处按模块路径直接导入（`from cs_agent.services.chat import ChatService`）。
"""
