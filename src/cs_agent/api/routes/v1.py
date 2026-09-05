"""`/v1` 业务路由（FR-108）。

本 milestone 只有认证自检 `GET /v1/whoami`——它回显**服务端认定的**身份，
用来手工确认 JWT 链路通了。真正的业务接口（threads / messages / actions …）
按 PRD §8.1 在后续 milestone 加入。
"""

from fastapi import APIRouter

from cs_agent.api.deps import AuthDep

router = APIRouter(prefix="/v1")


@router.get("/whoami", tags=["auth"])
def whoami(auth: AuthDep) -> dict[str, object]:
    """回显当前身份。注意：值来自 token 校验结果，与请求体无关。"""
    return {"user_id": auth.user_id, "roles": sorted(r.value for r in auth.roles)}
