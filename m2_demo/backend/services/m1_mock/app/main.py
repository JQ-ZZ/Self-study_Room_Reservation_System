"""M1 座位/空间服务模拟实现，接口镜像成员 A 的 M1 demo（PR #1）。

这不是 M1 的正式实现，只用于 M2 的本地演示与 CI：
- `GET /api/meta` 教室主数据
- `GET /api/seats` 座位级可用性（M2 用于冲突预检）
- `POST /api/integrations/restrictions` M2/M3 整教室限制
- `DELETE /api/integrations/restrictions/{source}/{external_id}` 幂等解除
- `POST /api/reservations` 个人座位预约（用于演示 M1 侧冲突）
- `/admin/*` 故障注入与状态查看（M2 演示专用，不属于契约）

契约见 docs/api/m1-m2-contract.md v0.2。真实 M1 由成员 A 提供。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

APP_VERSION = os.getenv("GIT_SHA", "dev")
TZ = ZoneInfo("Asia/Shanghai")

ROOMS = [
    {"id": 1, "name": "明德楼 201", "area": "安静区", "opens": 8, "closes": 23, "active": 1},
    {"id": 2, "name": "明德楼 202", "area": "靠窗区", "opens": 8, "closes": 23, "active": 1},
    {"id": 3, "name": "图书馆 301", "area": "研读区", "opens": 8, "closes": 23, "active": 1},
]
ROOM_BY_ID = {room["id"]: room for room in ROOMS}

# 与 A 的初始化顺序一致：每间教室 24 个座位，座位 ID 全局递增。
SEATS: list[dict] = []
for _room in ROOMS:
    for _i in range(1, 25):
        SEATS.append(
            {
                "id": (_room["id"] - 1) * 24 + _i,
                "room_id": _room["id"],
                "label": f"{_i:02}",
                "outlet": 1 if _i % 3 != 0 else 0,
                "active": 1,
            }
        )
SEAT_BY_ID = {seat["id"]: seat for seat in SEATS}


def now() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="时间格式错误") from exc
    if parsed.tzinfo is not None:
        raise HTTPException(status_code=422, detail="请使用北京时间，不携带时区后缀")
    return parsed.replace(microsecond=0)


def parse_interval(start: str, end: str) -> tuple[datetime, datetime]:
    start_dt, end_dt = parse_time(start), parse_time(end)
    if start_dt >= end_dt:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    return start_dt, end_dt


def overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and end > other_start


def identity(user: str) -> str:
    if user not in ("student-a", "student-b", "admin"):
        raise HTTPException(status_code=401, detail="未知演示身份")
    return user


def require_admin(user: str) -> None:
    identity(user)
    if user != "admin":
        raise HTTPException(status_code=403, detail="需要管理员身份")


class RestrictionIn(BaseModel):
    source: Literal["M2", "M3"]
    external_id: str = Field(min_length=1, max_length=80)
    room_id: int
    start: str
    end: str


class BookingIn(BaseModel):
    seat_id: int
    start: str
    end: str


class FailureModeIn(BaseModel):
    enabled: bool


app = FastAPI(title="M1 Mock Service (A's API)", version=APP_VERSION)
STATE: dict = {
    "restrictions": {},  # (source, external_id) -> dict
    "reservations": {},  # id -> dict
    "failure_mode": False,
    "call_log": [],
    "next_restriction_id": 1,
    "next_reservation_id": 1,
}


def record(event: str, payload: dict) -> None:
    STATE["call_log"].append({"at": stamp(now()), "event": event, "payload": payload})
    del STATE["call_log"][:-50]


def ensure_available() -> None:
    if STATE["failure_mode"]:
        raise HTTPException(status_code=503, detail="M1 mock 故障注入中：座位状态写入暂时不可用")


def active_restrictions(room_id: int, start: datetime, end: datetime) -> list[dict]:
    return [
        item
        for item in STATE["restrictions"].values()
        if item["room_id"] == room_id
        and item["active"]
        and overlaps(start, end, datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
    ]


def personal_conflicts(room_id: int, start: datetime, end: datetime) -> list[dict]:
    return [
        item
        for item in STATE["reservations"].values()
        if item["room_id"] == room_id
        and item["status"] in ("reserved", "checked_in")
        and overlaps(start, end, datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
    ]


def seat_sources(seat: dict, start: datetime, end: datetime) -> list[str]:
    room = ROOM_BY_ID[seat["room_id"]]
    sources: list[str] = []
    if not seat["active"] or not room["active"]:
        sources.append("管理员停用")
    sources.extend(f"{item['source']}:{item['external_id']}" for item in active_restrictions(seat["room_id"], start, end))
    if any(
        item["seat_id"] == seat["id"]
        and item["status"] in ("reserved", "checked_in")
        and overlaps(start, end, datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
        for item in STATE["reservations"].values()
    ):
        sources.append("M1:个人预约")
    if (
        start.date() != end.date()
        or start.hour < room["opens"]
        or end.hour > room["closes"]
        or (end.hour == room["closes"] and (end.minute or end.second))
    ):
        sources.append("非开放时间")
    return sources


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "m1-mock", "failure_mode": STATE["failure_mode"]}


@app.get("/version")
def version() -> dict:
    return {"service": "m1-mock", "git_sha": APP_VERSION}


@app.get("/api/meta")
def meta() -> dict:
    return {"now": stamp(now()), "rooms": ROOMS, "demo": True}


@app.get("/api/seats")
def seats(start: str, end: str, room_id: int = 0, outlet: bool = False) -> list[dict]:
    start_dt, end_dt = parse_interval(start, end)
    result = []
    for seat in SEATS:
        if room_id and seat["room_id"] != room_id:
            continue
        if outlet and not seat["outlet"]:
            continue
        room = ROOM_BY_ID[seat["room_id"]]
        sources = seat_sources(seat, start_dt, end_dt)
        result.append(
            {
                **seat,
                "room": room["name"],
                "area": room["area"],
                "opens": room["opens"],
                "closes": room["closes"],
                "room_active": room["active"],
                "sources": sources,
                "available": not sources,
            }
        )
    return result


@app.post("/api/reservations", status_code=201)
def book_seat(payload: BookingIn, x_demo_user: str = Header(default="student-a")) -> dict:
    user = identity(x_demo_user)
    start, end = parse_interval(payload.start, payload.end)
    seat = SEAT_BY_ID.get(payload.seat_id)
    if seat is None:
        raise HTTPException(status_code=404, detail="座位不存在")
    if not seat["active"] or not ROOM_BY_ID[seat["room_id"]]["active"]:
        raise HTTPException(status_code=409, detail="座位不可预约：管理员停用")
    reasons = seat_sources(seat, start, end)
    if reasons:
        raise HTTPException(status_code=409, detail="座位不可预约：" + "、".join(reasons))
    if any(
        item["user"] == user
        and item["status"] in ("reserved", "checked_in")
        and overlaps(start, end, datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
        for item in STATE["reservations"].values()
    ):
        raise HTTPException(status_code=409, detail="你在同一时段已有预约")

    rid = STATE["next_reservation_id"]
    STATE["next_reservation_id"] += 1
    item = {
        "id": rid,
        "user": user,
        "seat_id": seat["id"],
        "room_id": seat["room_id"],
        "start": stamp(start),
        "end": stamp(end),
        "status": "reserved",
        "created": stamp(now()),
    }
    STATE["reservations"][rid] = item
    record("M1_BOOK", item)
    return {"id": rid, "message": "预约成功"}


@app.get("/api/reservations")
def list_reservations(x_demo_user: str = Header(default="student-a")) -> list[dict]:
    user = identity(x_demo_user)
    return [item for item in STATE["reservations"].values() if item["user"] == user]


@app.post("/api/integrations/restrictions")
def restrict(payload: RestrictionIn, x_demo_user: str = Header(default="")) -> dict:
    require_admin(x_demo_user)
    ensure_available()
    start, end = parse_interval(payload.start, payload.end)
    if payload.room_id not in ROOM_BY_ID:
        raise HTTPException(status_code=404, detail="教室不存在")

    key = (payload.source, payload.external_id)
    old = STATE["restrictions"].get(key)
    if old is not None:
        if (old["room_id"], old["start"], old["end"]) != (payload.room_id, stamp(start), stamp(end)):
            raise HTTPException(status_code=409, detail="同一来源业务编号的参数不可更改")
        # 与 A 当前实现保持一致：即使 active=0 也返回 200，M2 客户端必须识别。
        return dict(old)

    if personal_conflicts(payload.room_id, start, end):
        raise HTTPException(status_code=409, detail="该时段已有个人预约，需先协调取消")

    item = {
        "id": STATE["next_restriction_id"],
        "source": payload.source,
        "external_id": payload.external_id,
        "room_id": payload.room_id,
        "start": stamp(start),
        "end": stamp(end),
        "active": 1,
        "created": stamp(now()),
    }
    STATE["next_restriction_id"] += 1
    STATE["restrictions"][key] = item
    record("M2_OCCUPY" if payload.source == "M2" else "M3_RESTRICT", item)
    return {"id": item["id"], "message": "限制已生效"}


@app.delete("/api/integrations/restrictions/{source}/{external_id}")
def release(source: str, external_id: str, x_demo_user: str = Header(default="")) -> dict:
    require_admin(x_demo_user)
    ensure_available()
    item = STATE["restrictions"].get((source, external_id))
    if item is not None:
        item["active"] = 0
    record("M2_RELEASE" if source == "M2" else "M3_RELEASE", {"source": source, "external_id": external_id})
    return {"message": "已解除指定来源限制，其他限制继续有效"}


# ---- 演示运维接口（不属于 M1/M2 契约） ----
@app.post("/admin/failure-mode")
def set_failure_mode(payload: FailureModeIn) -> dict:
    STATE["failure_mode"] = payload.enabled
    return {"failure_mode": STATE["failure_mode"]}


@app.post("/admin/reset")
def reset() -> dict:
    STATE["restrictions"].clear()
    STATE["reservations"].clear()
    STATE["call_log"].clear()
    STATE["failure_mode"] = False
    STATE["next_restriction_id"] = 1
    STATE["next_reservation_id"] = 1
    return {"ok": True}


@app.get("/admin/state")
def state() -> dict:
    return {
        "failure_mode": STATE["failure_mode"],
        "demo_controls": True,
        "restrictions": list(STATE["restrictions"].values()),
        "reservations": list(STATE["reservations"].values()),
        "call_log": list(reversed(STATE["call_log"])),
    }
