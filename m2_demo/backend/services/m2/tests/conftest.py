from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.m1_client import M1Error
from app.main import create_app


class FakeM1:
    """内存版 M1，验证 M2 的调用参数、幂等与失败路径。"""

    def __init__(self) -> None:
        self.rooms = [
            {"id": "1", "name": "明德楼 201", "area": "安静区", "opens": 8, "closes": 23, "active": True},
            {"id": "2", "name": "明德楼 202", "area": "靠窗区", "opens": 8, "closes": 23, "active": True},
        ]
        self.occupancies: dict[str, dict] = {}
        self.failure_mode = False
        self.conflict_reason: str | None = None
        self.occupy_error: M1Error | None = None
        self.calls: list[tuple[str, str]] = []

    def list_rooms(self) -> list[dict]:
        return self.rooms

    def get_room(self, room_id: str) -> dict:
        for room in self.rooms:
            if room["id"] == room_id:
                return room
        raise M1Error("M1 返回 404：教室不存在", 404)

    def check_conflict(self, room_id: str, start, end) -> str | None:
        self.calls.append(("CHECK", str(room_id)))
        if self.failure_mode:
            raise M1Error("M1 返回 503：故障注入", 503)
        return self.conflict_reason

    def occupy(self, *, application_id: str, room_id: str, start, end, reason: str) -> dict:
        self.calls.append(("OCCUPY", application_id))
        if self.failure_mode:
            raise M1Error("M1 返回 503：故障注入", 503)
        if self.occupy_error is not None:
            raise self.occupy_error
        if application_id not in self.occupancies:
            self.occupancies[application_id] = {
                "application_id": application_id,
                "room_id": room_id,
                "start_time": start.isoformat() + "Z",
                "end_time": end.isoformat() + "Z",
                "reason": reason,
            }
        return self.occupancies[application_id]

    def release(self, application_id: str) -> None:
        self.calls.append(("RELEASE", application_id))
        if self.failure_mode:
            raise M1Error("M1 返回 503：故障注入", 503)
        self.occupancies.pop(application_id, None)

    def state(self) -> dict:
        return {
            "failure_mode": self.failure_mode,
            "occupancies": list(self.occupancies.values()),
            "call_log": [],
        }

    def set_failure_mode(self, enabled: bool) -> dict:
        self.failure_mode = enabled
        return {"failure_mode": enabled}

    def reset(self) -> dict:
        self.occupancies.clear()
        self.failure_mode = False
        return {"ok": True}

    def close(self) -> None:
        pass


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'm2_test.db'}",
        worker_enabled=False,
        auto_create_tables=True,
        approval_timeout_seconds=120,
        sync_max_attempts=3,
        sync_interval_seconds=0.1,
        m1_base_url="http://fake-m1",
    )


@pytest.fixture
def fake_m1() -> FakeM1:
    return FakeM1()


@pytest.fixture
def app(settings, fake_m1):
    return create_app(settings, fake_m1)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth(client):
    cache: dict[str, dict] = {}

    def _auth(username: str) -> dict:
        if username not in cache:
            resp = client.post("/api/v1/auth/login", json={"username": username})
            assert resp.status_code == 200, resp.text
            cache[username] = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        return cache[username]

    return _auth
