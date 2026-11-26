from __future__ import annotations

from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .db import utcnow


class Base(DeclarativeBase):
    pass


class ApplicationStatus(str, Enum):
    PENDING_L1 = "PENDING_L1"
    PENDING_L2 = "PENDING_L2"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


PENDING_STATUSES = (ApplicationStatus.PENDING_L1.value, ApplicationStatus.PENDING_L2.value)
TERMINAL_STATUSES = (
    ApplicationStatus.REJECTED.value,
    ApplicationStatus.CANCELLED.value,
    ApplicationStatus.EXPIRED.value,
)


class ApprovalAction(str, Enum):
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    EXPIRE = "EXPIRE"


class SyncAction(str, Enum):
    OCCUPY = "OCCUPY"
    RELEASE = "RELEASE"


class SyncStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD = "DEAD"
    SKIPPED = "SKIPPED"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(String(32), nullable=False)
    room_name: Mapped[str] = mapped_column(String(128), nullable=False)
    applicant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    applicant_name: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    start_time: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    expires_at: Mapped[DateTime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_applications_room_time", "room_id", "start_time", "end_time"),
        Index("ix_applications_status_expires", "status", "expires_at"),
    )


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=一级, 2=二级, 0=系统/申请人
    approver_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approver_name: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow, nullable=False)


class SyncRecord(Base):
    """Outbox：审批状态变化的跨模块副作用，与业务状态同事务写入。"""

    __tablename__ = "sync_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("application_id", "action", name="uq_sync_application_action"),
    )


class RoomLock(Base):
    """按教室串行化审批，避免两个审批同时通过造成重复占用（Postgres 下行锁）。"""

    __tablename__ = "room_locks"

    room_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
