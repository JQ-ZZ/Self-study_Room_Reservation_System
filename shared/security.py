"""密码使用 scrypt + 随机盐；数据库只保存会话令牌的 SHA-256 摘要。"""
import hashlib
import secrets
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from shared.models import Session, User

PERMISSIONS = {
    'student': ['tickets:create', 'tickets:own', 'notifications:own', 'm1:book'],
    'approver': ['tickets:create', 'tickets:own', 'notifications:own', 'm1:book', 'm2:apply', 'm2:approve'],
    'technician': ['tickets:assigned', 'tickets:repair', 'notifications:own'],
    'admin': ['tickets:create', 'tickets:all', 'tickets:assign', 'tickets:accept',
              'tickets:retry', 'users:manage', 'audit:read', 'notifications:own',
              'm1:book', 'm1:manage', 'm2:apply', 'm2:approve'],
}


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f'scrypt${salt.hex()}${digest.hex()}'


def verify_password(password, encoded):
    try:
        _, salt, expected = encoded.split('$')
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return secrets.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def digest_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_user(username, password, display_name, role):
    return User(id=str(uuid4()), username=username, password_hash=hash_password(password),
                display_name=display_name, role=role, active=True)


def user_view(user):
    return {k: getattr(user, k) for k in ('id', 'username', 'display_name', 'role', 'active')} | {
        'permissions': PERMISSIONS[user.role]}


def current_user(request: Request, authorization: str = Header(default='')):
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 256:
        raise HTTPException(401, '请先登录', headers={'WWW-Authenticate': 'Bearer'})
    with request.app.state.sessions() as db:
        session = db.get(Session, digest_token(token))
        user = db.get(User, session.user_id) if session and session.expires > request.app.state.clock() else None
        if not user or not user.active:
            raise HTTPException(401, '会话已过期或账号已停用')
        return user


def require(permission):
    def dependency(user=Depends(current_user)):
        if permission not in PERMISSIONS[user.role]:
            raise HTTPException(403, '当前角色无权执行此操作')
        return user
    return dependency


def service_source(request: Request, x_service_token: str = Header(default='')):
    settings = request.app.state.settings
    for source, expected in [('M1', settings.service_token_m1), ('M2', settings.service_token_m2)]:
        if expected and secrets.compare_digest(x_service_token.encode(), expected.encode()):
            return source
    raise HTTPException(401, '无效服务凭据')
