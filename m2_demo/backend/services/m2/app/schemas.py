from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


class LoginRequest(BaseModel):
    username: str


class UserOut(BaseModel):
    id: str
    name: str
    roles: list[str]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RoomOut(BaseModel):
    """M1 主数据视图。A 的 M1 用 area/opens/closes 描述教室，没有 building/capacity。"""

    id: str
    name: str
    area: str = ""
    opens: int | None = None
    closes: int | None = None
    active: bool = True


class ApplicationCreate(BaseModel):
    room_id: str = Field(min_length=1, max_length=32)
    purpose: str = Field(min_length=2, max_length=200)
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def _normalize_dt(cls, v: datetime) -> datetime:
        return _to_utc_naive(v)

    @model_validator(mode="after")
    def _check_range(self):
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class ApplicationOut(BaseModel):
    id: str
    room_id: str
    room_name: str
    applicant_id: str
    applicant_name: str
    purpose: str
    start_time: datetime
    end_time: datetime
    status: str
    reject_reason: str | None = None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    sync_status: str | None = None
    sync_last_error: str | None = None

    @field_serializer("start_time", "end_time", "created_at", "expires_at", "decided_at")
    def _ser_dt(self, value: datetime | None, _info):
        if value is None:
            return None
        return value.isoformat(timespec="seconds") + "Z"


class ApprovalRecordOut(BaseModel):
    id: int
    application_id: str
    level: int
    approver_id: str
    approver_name: str
    action: str
    comment: str | None = None
    created_at: datetime

    @field_serializer("created_at")
    def _ser_dt(self, value: datetime, _info):
        return value.isoformat(timespec="seconds") + "Z"


class ApprovalRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class SyncOut(BaseModel):
    id: int
    application_id: str
    action: str
    status: str
    attempts: int
    last_error: str | None = None
    next_retry_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("next_retry_at", "created_at", "updated_at")
    def _ser_dt(self, value: datetime | None, _info):
        if value is None:
            return None
        return value.isoformat(timespec="seconds") + "Z"


class FailureModeIn(BaseModel):
    enabled: bool


class M1PersonalBookingIn(BaseModel):
    """仅演示：在 M1 制造个人座位预约，用于验证二级审批前的冲突预检。"""

    room_id: str = Field(min_length=1, max_length=32)
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def _normalize_dt(cls, v: datetime) -> datetime:
        return _to_utc_naive(v)

    @model_validator(mode="after")
    def _check_range(self):
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class SweepResult(BaseModel):
    expired: int
    sync_attempted: int
    sync_succeeded: int
    sync_failed: int


StatusLiteral = Literal[
    "PENDING_L1", "PENDING_L2", "APPROVED", "REJECTED", "CANCELLED", "EXPIRED"
]
