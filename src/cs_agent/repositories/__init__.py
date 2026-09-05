"""数据访问层。只做取数与 scope 强制，不含任何业务规则。"""

from cs_agent.repositories.biz import BizRepository

__all__ = ["BizRepository"]
