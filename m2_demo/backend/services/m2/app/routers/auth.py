from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import DEMO_USERS, CurrentUser, create_token, get_current_user
from ..schemas import LoginRequest, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, request: Request) -> TokenOut:
    """演示用登录。正式环境由成员 C 的认证服务签发 JWT，M2 只负责验签。"""
    settings = request.app.state.settings
    user = DEMO_USERS.get(payload.username)
    if user is None:
        raise HTTPException(status_code=404, detail="演示账号不存在")
    return TokenOut(
        access_token=create_token(settings, payload.username),
        user=UserOut(id=payload.username, name=user["name"], roles=user["roles"]),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, name=user.name, roles=user.roles)


@router.get("/demo-users")
def demo_users() -> dict:
    return {
        "items": [
            {"id": uid, "name": data["name"], "roles": data["roles"]}
            for uid, data in DEMO_USERS.items()
        ]
    }
