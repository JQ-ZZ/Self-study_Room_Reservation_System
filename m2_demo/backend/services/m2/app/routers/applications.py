from __future__ import annotations

import logging
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import APPLICANT_ROLES, APPROVER_ROLES, CurrentUser, get_current_user
from ..db import utcnow
from ..deps import application_payload, get_db, get_m1, latest_sync_map
from ..m1_client import M1Client, M1Error
from ..models import (
    PENDING_STATUSES,
    Application,
    ApplicationStatus,
    ApprovalAction,
    ApprovalRecord,
    RoomLock,
    SyncAction,
    SyncRecord,
    SyncStatus,
)
from ..schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApprovalRecordOut,
    ApprovalRequest,
    CancelRequest,
)
from ..state_machine import TransitionError, ensure_transition

router = APIRouter(prefix="/applications", tags=["applications"])
logger = logging.getLogger("m2.applications")


def _get_or_404(db: Session, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    return app


def _ensure_can_view(app: Application, user: CurrentUser) -> None:
    if user.id == app.applicant_id or user.has_any(*APPROVER_ROLES):
        return
    raise HTTPException(status_code=403, detail="无权查看该申请")


def _ensure_room_lock(db: Session, room_id: str) -> None:
    """Postgres 下用行锁串行化同一教室的审批；SQLite 演示时降级为无锁。"""
    try:
        with db.begin_nested():
            db.add(RoomLock(room_id=room_id))
    except IntegrityError:
        pass  # 已存在


def _lock_room(db: Session, room_id: str) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(select(RoomLock).where(RoomLock.room_id == room_id).with_for_update())


def _find_conflict(db: Session, app: Application) -> Application | None:
    return (
        db.query(Application)
        .filter(
            Application.id != app.id,
            Application.room_id == app.room_id,
            Application.status == ApplicationStatus.APPROVED.value,
            Application.start_time < app.end_time,
            Application.end_time > app.start_time,
        )
        .first()
    )


def _add_record(db: Session, app: Application, level: int, user: CurrentUser, action: str, comment: str | None) -> None:
    db.add(
        ApprovalRecord(
            application_id=app.id,
            level=level,
            approver_id=user.id,
            approver_name=user.name,
            action=action,
            comment=comment,
        )
    )


def _sync_record(db: Session, app: Application, action: str) -> SyncRecord:
    existing = (
        db.query(SyncRecord)
        .filter(SyncRecord.application_id == app.id, SyncRecord.action == action)
        .first()
    )
    if existing is not None:
        return existing
    rec = SyncRecord(
        application_id=app.id,
        action=action,
        status=SyncStatus.PENDING.value,
        attempts=0,
        next_retry_at=utcnow(),
    )
    db.add(rec)
    db.flush()
    return rec


@router.post("", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    m1: M1Client = Depends(get_m1),
) -> ApplicationOut:
    if not user.has_any(*APPLICANT_ROLES):
        raise HTTPException(status_code=403, detail="当前角色不能提交教室申请")

    settings = request.app.state.settings
    now = utcnow()
    if payload.start_time <= now - timedelta(seconds=60):
        raise HTTPException(status_code=422, detail="开始时间必须晚于当前时间")
    if payload.end_time - payload.start_time > timedelta(hours=settings.max_application_hours):
        raise HTTPException(status_code=422, detail=f"单次申请不能超过 {settings.max_application_hours} 小时")

    try:
        room = m1.get_room(payload.room_id)
    except M1Error as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="教室不存在") from exc
        raise HTTPException(status_code=503, detail=f"无法校验教室：{exc}") from exc

    _ensure_room_lock(db, payload.room_id)

    conflict = (
        db.query(Application)
        .filter(
            Application.room_id == payload.room_id,
            Application.status == ApplicationStatus.APPROVED.value,
            Application.start_time < payload.end_time,
            Application.end_time > payload.start_time,
        )
        .first()
    )
    if conflict is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "该教室在所选时段已被批准占用", "conflicting_application_id": conflict.id},
        )

    app = Application(
        id=str(uuid4()),
        room_id=payload.room_id,
        room_name=room["name"],
        applicant_id=user.id,
        applicant_name=user.name,
        purpose=payload.purpose,
        start_time=payload.start_time,
        end_time=payload.end_time,
        status=ApplicationStatus.PENDING_L1.value,
        expires_at=now + timedelta(seconds=settings.approval_timeout_seconds),
    )
    db.add(app)
    db.flush()
    _add_record(db, app, 0, user, ApprovalAction.SUBMIT.value, "提交申请")
    db.flush()
    return ApplicationOut(**application_payload(app, None))


@router.get("/mine", response_model=list[ApplicationOut])
def my_applications(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApplicationOut]:
    apps = (
        db.query(Application)
        .filter(Application.applicant_id == user.id)
        .order_by(Application.created_at.desc())
        .all()
    )
    syncs = latest_sync_map(db, [a.id for a in apps])
    return [ApplicationOut(**application_payload(a, syncs.get(a.id))) for a in apps]


@router.get("/pending", response_model=list[ApplicationOut])
def pending_applications(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApplicationOut]:
    if user.has_any("admin"):
        statuses = list(PENDING_STATUSES)
    elif user.has_any("approver_l1"):
        statuses = [ApplicationStatus.PENDING_L1.value]
    elif user.has_any("approver_l2"):
        statuses = [ApplicationStatus.PENDING_L2.value]
    else:
        raise HTTPException(status_code=403, detail="当前角色没有审批权限")
    apps = (
        db.query(Application)
        .filter(Application.status.in_(statuses))
        .order_by(Application.created_at.asc())
        .all()
    )
    syncs = latest_sync_map(db, [a.id for a in apps])
    return [ApplicationOut(**application_payload(a, syncs.get(a.id))) for a in apps]


@router.get("/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    app = _get_or_404(db, application_id)
    _ensure_can_view(app, user)
    syncs = latest_sync_map(db, [app.id])
    return ApplicationOut(**application_payload(app, syncs.get(app.id)))


@router.get("/{application_id}/approvals", response_model=list[ApprovalRecordOut])
def approval_records(
    application_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApprovalRecordOut]:
    app = _get_or_404(db, application_id)
    _ensure_can_view(app, user)
    records = (
        db.query(ApprovalRecord)
        .filter(ApprovalRecord.application_id == application_id)
        .order_by(ApprovalRecord.id.asc())
        .all()
    )
    return [ApprovalRecordOut.model_validate(r, from_attributes=True) for r in records]


@router.post("/{application_id}/approve", response_model=ApplicationOut)
def approve(
    application_id: str,
    payload: ApprovalRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    m1: M1Client = Depends(get_m1),
) -> ApplicationOut:
    app = _get_or_404(db, application_id)
    if not user.has_any(*APPROVER_ROLES):
        raise HTTPException(status_code=403, detail="当前角色没有审批权限")

    if app.status == ApplicationStatus.PENDING_L1.value:
        if not user.has_any("approver_l1", "admin"):
            raise HTTPException(status_code=403, detail="需要一级审批权限")
        level = 1
        target = ApplicationStatus.PENDING_L2.value
    elif app.status == ApplicationStatus.PENDING_L2.value:
        if not user.has_any("approver_l2", "admin"):
            raise HTTPException(status_code=403, detail="需要二级审批权限")
        level = 2
        target = ApplicationStatus.APPROVED.value
        first = (
            db.query(ApprovalRecord)
            .filter(
                ApprovalRecord.application_id == app.id,
                ApprovalRecord.level == 1,
                ApprovalRecord.action == ApprovalAction.APPROVE.value,
            )
            .first()
        )
        if first is not None and first.approver_id == user.id:
            raise HTTPException(status_code=409, detail="同一审批人不能完成两级审批")
    else:
        raise HTTPException(status_code=409, detail=f"当前状态 {app.status} 不可审批")

    _lock_room(db, app.room_id)
    if target == ApplicationStatus.APPROVED.value:
        conflict = _find_conflict(db, app)
        if conflict is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "该教室时段已被另一份申请占用，不能审批通过",
                    "conflicting_application_id": conflict.id,
                },
            )
        # M1 侧冲突预检：A 的 M1 不允许整教室限制与个人预约重叠。
        # 只读检查，冲突立即 409；M1 临时不可用时不阻塞审批，交给 outbox 重试，
        # 避免因为一次网络抖动把审批人卡死。
        try:
            m1_conflict = m1.check_conflict(app.room_id, app.start_time, app.end_time)
        except M1Error as exc:
            if exc.retryable:
                logger.warning("M1 冲突预检不可用，继续审批并依赖 outbox 重试：%s", exc)
                m1_conflict = None
            else:
                raise HTTPException(
                    status_code=503 if exc.status_code in (401, 403) else 409,
                    detail=f"M1 教室状态校验失败：{exc}",
                ) from exc
        if m1_conflict:
            raise HTTPException(status_code=409, detail=f"M1 侧冲突：{m1_conflict}")

    try:
        ensure_transition(app.status, target)
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.status = target
    if target == ApplicationStatus.APPROVED.value:
        app.decided_at = utcnow()
        _sync_record(db, app, SyncAction.OCCUPY.value)
    _add_record(db, app, level, user, ApprovalAction.APPROVE.value, payload.comment)
    db.flush()

    sync = (
        db.query(SyncRecord)
        .filter(SyncRecord.application_id == app.id)
        .order_by(SyncRecord.id.desc())
        .first()
    )
    return ApplicationOut(**application_payload(app, sync))


@router.post("/{application_id}/reject", response_model=ApplicationOut)
def reject(
    application_id: str,
    payload: ApprovalRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    app = _get_or_404(db, application_id)
    if not user.has_any(*APPROVER_ROLES):
        raise HTTPException(status_code=403, detail="当前角色没有审批权限")

    if app.status == ApplicationStatus.PENDING_L1.value:
        if not user.has_any("approver_l1", "admin"):
            raise HTTPException(status_code=403, detail="需要一级审批权限")
        level = 1
    elif app.status == ApplicationStatus.PENDING_L2.value:
        if not user.has_any("approver_l2", "admin"):
            raise HTTPException(status_code=403, detail="需要二级审批权限")
        level = 2
    else:
        raise HTTPException(status_code=409, detail=f"当前状态 {app.status} 不可驳回")

    app.status = ApplicationStatus.REJECTED.value
    app.decided_at = utcnow()
    app.reject_reason = payload.comment or "审批驳回"
    _add_record(db, app, level, user, ApprovalAction.REJECT.value, payload.comment)
    db.flush()
    sync = (
        db.query(SyncRecord)
        .filter(SyncRecord.application_id == app.id)
        .order_by(SyncRecord.id.desc())
        .first()
    )
    return ApplicationOut(**application_payload(app, sync))


@router.post("/{application_id}/cancel", response_model=ApplicationOut)
def cancel(
    application_id: str,
    payload: CancelRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationOut:
    app = _get_or_404(db, application_id)
    if user.id != app.applicant_id and not user.has_any("admin"):
        raise HTTPException(status_code=403, detail="只能撤销自己的申请")

    now = utcnow()
    if app.status in PENDING_STATUSES:
        app.status = ApplicationStatus.CANCELLED.value
        app.decided_at = now
        app.reject_reason = payload.reason or "申请人撤销"
        _add_record(db, app, 0, user, ApprovalAction.CANCEL.value, payload.reason)
    elif app.status == ApplicationStatus.APPROVED.value:
        if now >= app.start_time:
            raise HTTPException(status_code=409, detail="申请已开始，不能撤销")
        app.status = ApplicationStatus.CANCELLED.value
        app.decided_at = now
        app.reject_reason = payload.reason or "申请人撤销（已批准的申请）"
        # 尚未执行的占用任务作废；释放接口幂等，失败可重试
        pending_occupy = (
            db.query(SyncRecord)
            .filter(
                SyncRecord.application_id == app.id,
                SyncRecord.action == SyncAction.OCCUPY.value,
                SyncRecord.status.in_([SyncStatus.PENDING.value, SyncStatus.FAILED.value]),
            )
            .all()
        )
        for rec in pending_occupy:
            rec.status = SyncStatus.SKIPPED.value
            rec.last_error = "申请撤销，占用任务作废"
        _sync_record(db, app, SyncAction.RELEASE.value)
        _add_record(db, app, 0, user, ApprovalAction.CANCEL.value, payload.reason)
    else:
        raise HTTPException(status_code=409, detail=f"当前状态 {app.status} 不可撤销")

    db.flush()
    sync = (
        db.query(SyncRecord)
        .filter(SyncRecord.application_id == app.id)
        .order_by(SyncRecord.id.desc())
        .first()
    )
    return ApplicationOut(**application_payload(app, sync))
