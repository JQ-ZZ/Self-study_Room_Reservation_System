from datetime import timedelta
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from shared.events import audit
from shared.models import Audit, LoginAttempt, Notification, Session, User
from shared.security import (current_user, digest_token, hash_password, new_user,
                             require, service_source, user_view, verify_password)

router = APIRouter(prefix='/api', tags=['公共认证与协作'])
DUMMY_PASSWORD = hash_password('constant-time-dummy-not-an-account')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Login(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    username: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=1, max_length=128)


class UserCreate(Login):
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal['student', 'approver', 'technician', 'admin']

    @field_validator('display_name')
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError('姓名不能为空')
        return value.strip()


class UserAccess(Input):
    active: bool
    role: Literal['student', 'approver', 'technician', 'admin']


class PasswordChange(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@router.post('/auth/login')
def login(p: Login, request: Request):
    t = request.app.state.clock()
    with request.app.state.sessions.begin() as db:
        db.execute(delete(LoginAttempt).where(LoginAttempt.created < t - timedelta(minutes=5)))
        attempts = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.username == p.username))
        if attempts >= 10:
            raise HTTPException(429, '登录尝试过多，请 5 分钟后重试')
        user = db.scalar(select(User).where(User.username == p.username))
        valid = verify_password(p.password, user.password_hash if user else DUMMY_PASSWORD)
        if not user or not valid or not user.active:
            db.add(LoginAttempt(username=p.username, created=t))
            audit(db, p.username, 'login_failed', 'auth', t, source='AUTH')
            failed = True
        else:
            failed = False
            token = secrets.token_urlsafe(32)
            expires = t + timedelta(hours=request.app.state.settings.session_hours)
            db.add(Session(token_hash=digest_token(token), user_id=user.id, expires=expires))
            db.execute(delete(Session).where(Session.expires <= t))
            db.execute(delete(LoginAttempt).where(LoginAttempt.username == p.username))
            audit(db, user.id, 'login', 'auth', t, source='AUTH')
            result = {'access_token': token, 'token_type': 'bearer', 'expires_at': expires, 'user': user_view(user)}
    if failed:
        raise HTTPException(401, '账号或密码错误')
    return result


@router.get('/auth/me')
def me(user=Depends(current_user)):
    return user_view(user)


@router.post('/auth/logout')
def logout(request: Request, authorization: str = Header(), user=Depends(current_user)):
    with request.app.state.sessions.begin() as db:
        db.execute(delete(Session).where(Session.token_hash == digest_token(authorization.partition(' ')[2])))
        audit(db, user.id, 'logout', 'auth', request.app.state.clock(), source='AUTH')
    return {'message': '已退出登录'}


@router.post('/auth/password')
def change_password(p: PasswordChange, request: Request, user=Depends(current_user)):
    with request.app.state.sessions.begin() as db:
        current = db.get(User, user.id)
        if not verify_password(p.old_password, current.password_hash):
            raise HTTPException(403, '原密码不正确')
        current.password_hash = hash_password(p.new_password)
        db.execute(delete(Session).where(Session.user_id == user.id))
        audit(db, user.id, 'password_changed', user.id, request.app.state.clock(), source='AUTH')
    return {'message': '密码已更新，请重新登录'}


@router.get('/users')
def users(request: Request, user=Depends(require('users:manage'))):
    with request.app.state.sessions() as db:
        return [user_view(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post('/users', status_code=201)
def create_user(p: UserCreate, request: Request, actor=Depends(require('users:manage'))):
    try:
        with request.app.state.sessions.begin() as db:
            user = new_user(p.username, p.password, p.display_name, p.role)
            db.add(user)
            db.flush()
            audit(db, actor.id, 'user_created', user.id, request.app.state.clock(), source='AUTH')
            return user_view(user)
    except IntegrityError:
        raise HTTPException(409, '账号已存在')


@router.put('/users/{uid}/access')
def update_access(uid: str, p: UserAccess, request: Request, actor=Depends(require('users:manage'))):
    if uid == actor.id:
        raise HTTPException(409, '不能修改自己的角色或停用自己')
    with request.app.state.sessions.begin() as db:
        user = db.get(User, uid)
        if not user:
            raise HTTPException(404, '用户不存在')
        user.active, user.role = p.active, p.role
        db.execute(delete(Session).where(Session.user_id == uid))
        audit(db, actor.id, 'user_access_changed', uid, request.app.state.clock(),
              detail=f'role={p.role},active={p.active}', source='AUTH')
        return user_view(user)


@router.get('/notifications')
def notifications(request: Request, user=Depends(current_user), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    with request.app.state.sessions() as db:
        rows = db.scalars(select(Notification).where(Notification.user_id == user.id)
                          .order_by(Notification.id.desc()).offset(offset).limit(limit))
        return [{'id': n.id, 'text': n.text, 'source': n.source, 'created': n.created, 'read_at': n.read_at} for n in rows]


@router.post('/notifications/{nid}/read')
def read_notification(nid: int, request: Request, user=Depends(current_user)):
    with request.app.state.sessions.begin() as db:
        n = db.get(Notification, nid)
        if not n or n.user_id != user.id:
            raise HTTPException(404, '消息不存在')
        n.read_at = n.read_at or request.app.state.clock()
    return {'message': '已读'}


@router.get('/audit')
def audits(request: Request, user=Depends(require('audit:read')), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    with request.app.state.sessions() as db:
        return [{k: getattr(a, k) for k in ('id', 'source', 'actor', 'action', 'target', 'detail', 'created')}
                for a in db.scalars(select(Audit).order_by(Audit.id.desc()).offset(offset).limit(limit))]


class NotificationInput(Input):
    event_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=2000)


class AuditInput(Input):
    event_id: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=100)
    detail: str = Field(max_length=2000, default='')


def ingest(request, source, p, model):
    """同一幂等键只接受同一内容；唯一约束处理并发提交。"""
    values = p.model_dump()
    def existing(db):
        query = select(model).where(model.source == source, model.event_id == p.event_id)
        if model is Notification:
            query = query.where(model.user_id == p.user_id)
        return db.scalar(query)
    def result(row):
        if any(getattr(row, k) != v for k, v in values.items()):
            raise HTTPException(409, '同一事件编号的内容不可更改')
        return {'id': row.id}
    try:
        with request.app.state.sessions.begin() as db:
            if model is Notification and not db.get(User, p.user_id):
                raise HTTPException(404, '收件人不存在')
            old = existing(db)
            if old:
                return result(old)
            row = model(**values, source=source, created=request.app.state.clock())
            db.add(row)
            db.flush()
            return {'id': row.id}
    except IntegrityError:
        with request.app.state.sessions() as db:
            old = existing(db)
            if old:
                return result(old)
        raise HTTPException(409, '事件写入冲突，请重试')


@router.post('/integrations/notifications')
def ingest_notification(p: NotificationInput, request: Request, source=Depends(service_source)):
    return ingest(request, source, p, Notification)


@router.post('/integrations/audit')
def ingest_audit(p: AuditInput, request: Request, source=Depends(service_source)):
    return ingest(request, source, p, Audit)
