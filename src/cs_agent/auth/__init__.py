"""身份与授权。身份一律由服务端构造，永不经过 LLM（ADR-0008）。"""

from cs_agent.auth.context import AuthContext, Role
from cs_agent.auth.jwt import AuthError, decode_token, issue_token, parse_bearer

__all__ = ["AuthContext", "AuthError", "Role", "decode_token", "issue_token", "parse_bearer"]
