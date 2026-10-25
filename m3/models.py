from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from shared.db import Base


class Ticket(Base):
    __tablename__ = 'm3_tickets'
    __table_args__ = (CheckConstraint('rating IS NULL OR (rating >= 1 AND rating <= 5)'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reporter_id: Mapped[str] = mapped_column(ForeignKey('c_users.id'), index=True)
    room_id: Mapped[int] = mapped_column(Integer)
    room_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), index=True, default='pending')
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey('c_users.id'), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime)
    updated: Mapped[datetime] = mapped_column(DateTime)
    due: Mapped[datetime] = mapped_column(DateTime, index=True)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    maintenance_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repair_note: Mapped[str] = mapped_column(Text, default='')
    acceptance_note: Mapped[str] = mapped_column(Text, default='')
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {'version_id_col': version}


class History(Base):
    __tablename__ = 'm3_history'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('m3_tickets.id'), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    note: Mapped[str] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(DateTime)


class SyncJob(Base):
    __tablename__ = 'm3_sync_jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey('m3_tickets.id'), index=True)
    operation: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run: Mapped[datetime] = mapped_column(DateTime)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), default='')
    created: Mapped[datetime] = mapped_column(DateTime)
