from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, Request

DEMO_USERS: dict[str, dict] = {
    "student1": {"name": "李同学", "roles": ["student"]},
    "student2": {"name": "王同学", "roles": ["student"]},
    "teacher1": {"name": "陈老师", "roles": ["teacher"]},
    "approver1": {"name": "赵主任（一级审批）", "roles": ["approver_l1"]},
    "approver2": {"name": "钱处长（二级审批）", "roles": ["approver_l2"]},
    "admin": {"name": "系统管理员", "roles": ["admin"]},
}

APPLICANT_ROLES = {"student", "teacher", "admin"}
APPROVER_ROLES = {"approver_l1", "approver_l2", "admin"}


@dataclass(frozen=True)
class CurrentUser:
    id: str
    name: str
    roles: list[str]

    def has_any(self, *roles: str) -> bool:
        return bool(set(self.roles) & set(roles))


def create_token(settings, user_id: str) -> str:
    user = DEMO_USERS[user_id]
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "name": user["name"],
        "roles": user["roles"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.token_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_user(request: Request) -> CurrentUser:
    settings = request.app.state.settings
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = header.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="登录已失效") from exc
    return CurrentUser(id=payload["sub"], name=payload.get("name", payload["sub"]), roles=payload.get("roles", []))


def require_roles(*roles: str):
    def dependency(request: Request) -> CurrentUser:
        user = get_current_user(request)
        if not user.has_any(*roles):
            raise HTTPException(status_code=403, detail=f"无权限，需要角色之一：{', '.join(roles)}")
        return user

    return dependency
