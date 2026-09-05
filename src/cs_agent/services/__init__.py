"""Service 层：业务逻辑与事务边界。不关心 HTTP，也不关心 LLM 概念。"""

from cs_agent.services.chat import ChatService

__all__ = ["ChatService"]
