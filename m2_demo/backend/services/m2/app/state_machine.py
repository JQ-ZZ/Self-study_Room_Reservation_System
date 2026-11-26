from __future__ import annotations

from enum import Enum

from .models import ApplicationStatus


class TransitionError(Exception):
    pass


ALLOWED: dict[str, set[str]] = {
    ApplicationStatus.PENDING_L1.value: {
        ApplicationStatus.PENDING_L2.value,   # 一级通过
        ApplicationStatus.REJECTED.value,     # 一级驳回
        ApplicationStatus.CANCELLED.value,    # 申请人撤销
        ApplicationStatus.EXPIRED.value,      # 超时撤销
    },
    ApplicationStatus.PENDING_L2.value: {
        ApplicationStatus.APPROVED.value,     # 二级通过
        ApplicationStatus.REJECTED.value,     # 二级驳回
        ApplicationStatus.CANCELLED.value,    # 申请人撤销
        ApplicationStatus.EXPIRED.value,      # 超时撤销
    },
    ApplicationStatus.APPROVED.value: {
        ApplicationStatus.CANCELLED.value,    # 开始前撤销，需要释放座位
    },
    ApplicationStatus.REJECTED.value: set(),
    ApplicationStatus.CANCELLED.value: set(),
    ApplicationStatus.EXPIRED.value: set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, set())


def ensure_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise TransitionError(f"非法状态流转：{current} -> {target}")


class Level(str, Enum):
    FIRST = 1
    SECOND = 2
