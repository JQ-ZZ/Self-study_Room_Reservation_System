from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy import or_

from .db import utcnow
from .m1_client import M1Error
from .models import (
    PENDING_STATUSES,
    Application,
    ApplicationStatus,
    ApprovalAction,
    ApprovalRecord,
    SyncAction,
    SyncRecord,
    SyncStatus,
)

logger = logging.getLogger("m2.worker")


def _expire_pending(session_factory, now) -> int:
    with session_factory() as db:
        apps = (
            db.query(Application)
            .filter(Application.status.in_(PENDING_STATUSES), Application.expires_at <= now)
            .all()
        )
        for app in apps:
            app.status = ApplicationStatus.EXPIRED.value
            app.decided_at = now
            app.reject_reason = "审批超时，系统自动撤销"
            db.add(
                ApprovalRecord(
                    application_id=app.id,
                    level=0,
                    approver_id="system",
                    approver_name="系统定时任务",
                    action=ApprovalAction.EXPIRE.value,
                    comment="待审批超过时限，自动撤销",
                )
            )
            logger.info("application %s expired", app.id)
        db.commit()
        return len(apps)


def _due_sync_records(session_factory, now) -> list[int]:
    with session_factory() as db:
        recs = (
            db.query(SyncRecord.id)
            .filter(
                SyncRecord.status.in_([SyncStatus.PENDING.value, SyncStatus.FAILED.value]),
                or_(SyncRecord.next_retry_at.is_(None), SyncRecord.next_retry_at <= now),
            )
            .order_by(SyncRecord.id.asc())
            .limit(50)
            .all()
        )
        return [r[0] for r in recs]


def _process_one(rec_id: int, session_factory, settings, m1) -> str | None:
    """返回 SUCCEEDED / FAILED / None(跳过)。"""
    with session_factory() as db:
        rec = db.get(SyncRecord, rec_id)
        if rec is None or rec.status not in (SyncStatus.PENDING.value, SyncStatus.FAILED.value):
            return None
        app = db.get(Application, rec.application_id)
        if app is None:
            rec.status = SyncStatus.SKIPPED.value
            rec.last_error = "关联申请不存在"
            db.commit()
            return None
        try:
            if rec.action == SyncAction.OCCUPY.value:
                m1.occupy(
                    application_id=app.id,
                    room_id=app.room_id,
                    start=app.start_time,
                    end=app.end_time,
                    reason=app.purpose,
                )
            else:
                m1.release(app.id)
        except M1Error as exc:
            rec.attempts += 1
            rec.last_error = str(exc)[:500]
            if not exc.retryable:
                # 4xx、释放后复用编号、占用未生效等：重试不会改变结果，直接暴露给运维。
                rec.status = SyncStatus.DEAD.value
                rec.next_retry_at = None
                db.commit()
                logger.error("sync %s permanent failure: %s", rec_id, exc)
                return "FAILED"
            if rec.attempts >= settings.sync_max_attempts:
                rec.status = SyncStatus.DEAD.value
                rec.next_retry_at = None
            else:
                rec.status = SyncStatus.FAILED.value
                rec.next_retry_at = utcnow() + timedelta(seconds=min(2 ** rec.attempts, 60))
            db.commit()
            logger.warning("sync %s failed (attempt %s): %s", rec_id, rec.attempts, exc)
            return "FAILED"
        rec.status = SyncStatus.SUCCEEDED.value
        rec.last_error = None
        rec.next_retry_at = None
        db.commit()
        logger.info("sync %s succeeded (%s %s)", rec_id, rec.action, rec.application_id)
        return "SUCCEEDED"


def run_sweep(session_factory, settings, m1, lock: threading.Lock | None = None) -> dict:
    """执行一轮：超时撤销 + outbox 重试。返回统计，供演示页手动触发。"""
    if lock is not None and not lock.acquire(blocking=False):
        return {"expired": 0, "sync_attempted": 0, "sync_succeeded": 0, "sync_failed": 0, "skipped": True}
    try:
        now = utcnow()
        expired = _expire_pending(session_factory, now)
        attempted = succeeded = failed = 0
        for rec_id in _due_sync_records(session_factory, now):
            result = _process_one(rec_id, session_factory, settings, m1)
            if result is None:
                continue
            attempted += 1
            if result == "SUCCEEDED":
                succeeded += 1
            else:
                failed += 1
        return {
            "expired": expired,
            "sync_attempted": attempted,
            "sync_succeeded": succeeded,
            "sync_failed": failed,
        }
    finally:
        if lock is not None:
            lock.release()


class Sweeper:
    def __init__(self, session_factory, settings, m1, lock):
        self.session_factory = session_factory
        self.settings = settings
        self.m1 = m1
        self.lock = lock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="m2-sweeper", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _loop(self) -> None:
        # 启动后先跑一轮，再按间隔执行
        while not self._stop.is_set():
            try:
                result = run_sweep(self.session_factory, self.settings, self.m1, self.lock)
                if any(result.get(k) for k in ("expired", "sync_attempted")):
                    logger.info("sweep result: %s", result)
            except Exception:  # 定时任务不能因单次异常退出
                logger.exception("sweeper iteration failed")
            self._stop.wait(self.settings.sync_interval_seconds)
