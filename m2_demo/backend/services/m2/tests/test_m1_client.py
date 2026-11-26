from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from app.m1_client import M1Client, M1Error, from_m1_time, to_m1_time


def make_client(handler) -> M1Client:
    return M1Client(
        "http://m1.test",
        demo_user="admin",
        transport=httpx.MockTransport(handler),
    )


def test_time_conversion_crosses_midnight():
    # M2 内部 UTC 16:00 == 北京时间次日 00:00
    assert to_m1_time(datetime(2026, 10, 21, 16, 0)) == "2026-10-22T00:00:00"
    assert from_m1_time("2026-10-22T00:00:00") == datetime(2026, 10, 21, 16, 0)


def test_time_conversion_accepts_aware_input():
    aware = datetime.fromisoformat("2026-10-21T16:00:00+00:00")
    assert to_m1_time(aware) == "2026-10-22T00:00:00"


def test_list_rooms_maps_a_meta_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/meta"
        return httpx.Response(
            200,
            json={
                "rooms": [
                    {"id": 1, "name": "明德楼 201", "area": "安静区", "opens": 8, "closes": 23, "active": 1}
                ]
            },
        )

    client = make_client(handler)
    try:
        assert client.list_rooms() == [
            {
                "id": "1",
                "name": "明德楼 201",
                "area": "安静区",
                "opens": 8,
                "closes": 23,
                "active": True,
            }
        ]
    finally:
        client.close()


def test_occupy_uses_restriction_endpoint_with_beijing_time():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        captured["demo_user"] = request.headers.get("X-Demo-User")
        return httpx.Response(200, json={"id": 1, "message": "限制已生效"})

    client = make_client(handler)
    try:
        client.occupy(
            application_id="app-1",
            room_id="1",
            start=datetime(2026, 10, 21, 16, 0),
            end=datetime(2026, 10, 21, 18, 0),
            reason="班会",
        )
    finally:
        client.close()

    assert captured["path"] == "/api/integrations/restrictions"
    assert captured["demo_user"] == "admin"
    assert captured["body"] == {
        "source": "M2",
        "external_id": "app-1",
        "room_id": 1,
        "start": "2026-10-22T00:00:00",
        "end": "2026-10-22T02:00:00",
    }


def test_occupy_inactive_response_is_permanent_failure():
    """A 当前对已释放编号返回 200 + active=0；M2 必须识别为失败。"""

    client = make_client(
        lambda request: httpx.Response(
            200, json={"id": 1, "external_id": "app-1", "active": 0}
        )
    )
    try:
        with pytest.raises(M1Error) as exc_info:
            client.occupy(
                application_id="app-1",
                room_id="1",
                start=datetime(2026, 10, 21, 16, 0),
                end=datetime(2026, 10, 21, 18, 0),
                reason="班会",
            )
    finally:
        client.close()

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.retryable is False
    assert "未生效" in str(exc)


@pytest.mark.parametrize("status,retryable", [(409, False), (422, False), (503, True), (500, True)])
def test_error_retry_classification(status: int, retryable: bool):
    client = make_client(
        lambda request: httpx.Response(status, json={"detail": "nope"})
    )
    try:
        with pytest.raises(M1Error) as exc_info:
            client.list_rooms()
    finally:
        client.close()
    assert exc_info.value.retryable is retryable


def test_network_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = make_client(handler)
    try:
        with pytest.raises(M1Error) as exc_info:
            client.list_rooms()
    finally:
        client.close()
    assert exc_info.value.status_code is None
    assert exc_info.value.retryable is True


def test_check_conflict_detects_personal_booking():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "room_id": 1,
                    "label": "01",
                    "room": "明德楼 201",
                    "sources": ["M1:个人预约"],
                    "available": False,
                }
            ],
        )

    client = make_client(handler)
    try:
        reason = client.check_conflict("1", datetime(2026, 10, 21, 16, 0), datetime(2026, 10, 21, 18, 0))
    finally:
        client.close()

    assert captured["path"] == "/api/seats"
    assert captured["params"] == {
        "start": "2026-10-22T00:00:00",
        "end": "2026-10-22T02:00:00",
        "room_id": "1",
    }
    assert reason is not None
    assert "M1:个人预约" in reason


def test_check_conflict_returns_none_when_available():
    client = make_client(
        lambda request: httpx.Response(
            200,
            json=[{"id": 1, "label": "01", "room": "明德楼 201", "sources": [], "available": True}],
        )
    )
    try:
        assert client.check_conflict("1", datetime(2026, 10, 21, 16, 0), datetime(2026, 10, 21, 18, 0)) is None
    finally:
        client.close()


def test_release_uses_source_and_external_id():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["demo_user"] = request.headers.get("X-Demo-User")
        return httpx.Response(200, json={"message": "已解除"})

    client = make_client(handler)
    try:
        client.release("app-1")
    finally:
        client.close()

    assert captured == {
        "method": "DELETE",
        "path": "/api/integrations/restrictions/M2/app-1",
        "demo_user": "admin",
    }
