from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..auth import CurrentUser, get_current_user
from ..db import utcnow
from ..deps import get_db, get_m1
from ..m1_client import M1Client, M1Error
from ..models import Application, SyncRecord, SyncStatus
from ..schemas import FailureModeIn, M1PersonalBookingIn, SweepResult, SyncOut
from ..worker import run_sweep

router = APIRouter(tags=["operations"])


def _require_admin(user: CurrentUser) -> None:
    if not user.has_any("admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@router.get("/sync", response_model=list[SyncOut])
def list_sync(
    application_id: str | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncOut]:
    query = db.query(SyncRecord)
    if application_id:
        app = db.get(Application, application_id)
        if app is None:
            raise HTTPException(status_code=404, detail="申请不存在")
        if user.id != app.applicant_id and not user.has_any("admin"):
            raise HTTPException(status_code=403, detail="无权查看该申请的同步记录")
        query = query.filter(SyncRecord.application_id == application_id)
    elif not user.has_any("admin"):
        app_ids = [row[0] for row in db.query(Application.id).filter(Application.applicant_id == user.id)]
        query = query.filter(SyncRecord.application_id.in_(app_ids))
    records = query.order_by(SyncRecord.id.desc()).limit(100).all()
    return [SyncOut.model_validate(r, from_attributes=True) for r in records]


@router.post("/sync/{record_id}/retry", response_model=SyncOut)
def retry_sync(
    record_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    m1: M1Client = Depends(get_m1),
) -> SyncOut:
    _require_admin(user)
    rec = db.get(SyncRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="同步记录不存在")
    if rec.status == SyncStatus.SUCCEEDED.value:
        return SyncOut.model_validate(rec, from_attributes=True)
    rec.status = SyncStatus.PENDING.value
    rec.attempts = 0
    rec.last_error = None
    rec.next_retry_at = utcnow()
    db.commit()

    run_sweep(
        request.app.state.session_factory,
        request.app.state.settings,
        m1,
        request.app.state.sweep_lock,
    )
    db.expire_all()
    rec = db.get(SyncRecord, record_id)
    return SyncOut.model_validate(rec, from_attributes=True)


@router.post("/ops/run-sweep", response_model=SweepResult)
def run_sweep_now(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    m1: M1Client = Depends(get_m1),
) -> SweepResult:
    """手动执行一轮超时扫描与重试，用于演示和故障恢复。"""
    _require_admin(user)
    result = run_sweep(
        request.app.state.session_factory,
        request.app.state.settings,
        m1,
        request.app.state.sweep_lock,
    )
    return SweepResult(
        expired=result.get("expired", 0),
        sync_attempted=result.get("sync_attempted", 0),
        sync_succeeded=result.get("sync_succeeded", 0),
        sync_failed=result.get("sync_failed", 0),
    )


@router.post("/ops/m1-failure")
def toggle_m1_failure(
    payload: FailureModeIn,
    user: CurrentUser = Depends(get_current_user),
    m1: M1Client = Depends(get_m1),
) -> dict:
    _require_admin(user)
    try:
        return m1.set_failure_mode(payload.enabled)
    except M1Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/ops/m1-reset")
def reset_m1(
    user: CurrentUser = Depends(get_current_user),
    m1: M1Client = Depends(get_m1),
) -> dict:
    _require_admin(user)
    try:
        return m1.reset()
    except M1Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/ops/m1-state")
def m1_state(
    user: CurrentUser = Depends(get_current_user),
    m1: M1Client = Depends(get_m1),
) -> dict:
    _require_admin(user)
    try:
        return m1.state()
    except M1Error as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/ops/m1-personal-booking")
def m1_personal_booking(
    payload: M1PersonalBookingIn,
    user: CurrentUser = Depends(get_current_user),
    m1: M1Client = Depends(get_m1),
) -> dict:
    """演示专用：在 M1 写一条个人预约，验证 M2 二级审批的冲突预检。"""
    _require_admin(user)
    try:
        return m1.create_personal_booking(
            room_id=payload.room_id,
            start=payload.start_time,
            end=payload.end_time,
        )
    except M1Error as exc:
        raise HTTPException(status_code=exc.status_code or 503, detail=str(exc)) from exc
