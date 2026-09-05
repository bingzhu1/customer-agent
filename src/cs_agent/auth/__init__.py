"""身份与授权。身份一律由服务端构造，永不经过 LLM（ADR-0008）。"""

from cs_agent.auth.context import AuthContext, Role

__all__ = ["AuthContext", "Role"]
