from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from .models import Application, SyncRecord


def get_db(request: Request):
    session_factory = request.app.state.session_factory
    db: Session = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_m1(request: Request):
    return request.app.state.m1_client


def latest_sync_map(db: Session, application_ids: list[str]) -> dict[str, SyncRecord]:
    if not application_ids:
        return {}
    records = (
        db.query(SyncRecord)
        .filter(SyncRecord.application_id.in_(application_ids))
        .order_by(SyncRecord.id.asc())
        .all()
    )
    result: dict[str, SyncRecord] = {}
    for rec in records:
        result[rec.application_id] = rec
    return result


def application_payload(app: Application, sync: SyncRecord | None) -> dict:
    return {
        "id": app.id,
        "room_id": app.room_id,
        "room_name": app.room_name,
        "applicant_id": app.applicant_id,
        "applicant_name": app.applicant_name,
        "purpose": app.purpose,
        "start_time": app.start_time,
        "end_time": app.end_time,
        "status": app.status,
        "reject_reason": app.reject_reason,
        "created_at": app.created_at,
        "expires_at": app.expires_at,
        "decided_at": app.decided_at,
        "sync_status": sync.status if sync else None,
        "sync_last_error": sync.last_error if sync else None,
    }
