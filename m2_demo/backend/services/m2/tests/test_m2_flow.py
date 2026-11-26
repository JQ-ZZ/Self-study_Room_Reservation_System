from __future__ import annotations

from datetime import timedelta

from app.db import utcnow
from app.m1_client import M1Error
from app.models import Application


def slot(hours_ahead: int = 24, duration_hours: int = 2):
    start = utcnow() + timedelta(hours=hours_ahead)
    return start.isoformat() + "Z", (start + timedelta(hours=duration_hours)).isoformat() + "Z"


def submit(client, auth, username="student1", room_id="1", purpose="班会", hours_ahead=24, duration=2):
    start, end = slot(hours_ahead, duration)
    resp = client.post(
        "/api/v1/applications",
        json={"room_id": room_id, "purpose": purpose, "start_time": start, "end_time": end},
        headers=auth(username),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def approve(client, auth, app_id, username):
    resp = client.post(f"/api/v1/applications/{app_id}/approve", json={"comment": "同意"}, headers=auth(username))
    return resp


def run_sweep(client, auth):
    resp = client.post("/api/v1/ops/run-sweep", json={}, headers=auth("admin"))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_full_two_level_approval_occupies_room(client, auth, fake_m1):
    app = submit(client, auth)
    assert app["status"] == "PENDING_L1"
    assert app["sync_status"] is None

    r1 = approve(client, auth, app["id"], "approver1")
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "PENDING_L2"
    assert fake_m1.occupancies == {}

    r2 = approve(client, auth, app["id"], "approver2")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "APPROVED"
    # 审批通过 ≠ 座位已经写回成功，必须显式报告同步中
    assert body["sync_status"] == "PENDING"

    result = run_sweep(client, auth)
    assert result["sync_succeeded"] == 1
    assert app["id"] in fake_m1.occupancies
    assert fake_m1.occupancies[app["id"]]["room_id"] == "1"

    detail = client.get(f"/api/v1/applications/{app['id']}", headers=auth("student1")).json()
    assert detail["sync_status"] == "SUCCEEDED"


def test_permission_gates(client, auth):
    app = submit(client, auth)

    # 学生不能审批
    assert approve(client, auth, app["id"], "student1").status_code == 403
    # 学生不能看待审批列表
    assert client.get("/api/v1/applications/pending", headers=auth("student1")).status_code == 403
    # 一级审批人不能处理二级
    assert approve(client, auth, app["id"], "approver1").status_code == 200
    assert approve(client, auth, app["id"], "approver1").status_code == 403
    # 没有申请权限的角色不能提交
    resp = client.post(
        "/api/v1/applications",
        json={"room_id": "1", "purpose": "越权", "start_time": slot()[0], "end_time": slot()[1]},
        headers=auth("approver2"),
    )
    assert resp.status_code == 403


def test_same_approver_cannot_do_both_levels(client, auth):
    app = submit(client, auth)
    assert approve(client, auth, app["id"], "admin").status_code == 200
    resp = approve(client, auth, app["id"], "admin")
    assert resp.status_code == 409
    assert "两级" in resp.text


def test_conflict_rejected_at_second_approval(client, auth):
    first = submit(client, auth, username="student1", purpose="第一份")
    second = submit(client, auth, username="student2", purpose="第二份")

    for app_id in (first["id"],):
        assert approve(client, auth, app_id, "approver1").status_code == 200
        assert approve(client, auth, app_id, "approver2").status_code == 200
    run_sweep(client, auth)

    assert approve(client, auth, second["id"], "approver1").status_code == 200
    resp = approve(client, auth, second["id"], "approver2")
    assert resp.status_code == 409, resp.text

    detail = client.get(f"/api/v1/applications/{second['id']}", headers=auth("student2")).json()
    assert detail["status"] == "PENDING_L2", "冲突时不得改变申请状态"


def test_timeout_expires_pending_application(client, auth):
    app = submit(client, auth)
    Session = client.app.state.session_factory
    with Session() as db:
        row = db.get(Application, app["id"])
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    result = run_sweep(client, auth)
    assert result["expired"] == 1
    detail = client.get(f"/api/v1/applications/{app['id']}", headers=auth("student1")).json()
    assert detail["status"] == "EXPIRED"
    records = client.get(f"/api/v1/applications/{app['id']}/approvals", headers=auth("student1")).json()
    assert any(r["action"] == "EXPIRE" for r in records)


def test_sync_failure_is_visible_and_retryable(client, auth, fake_m1):
    fake_m1.failure_mode = True
    app = submit(client, auth)
    approve(client, auth, app["id"], "approver1")
    resp = approve(client, auth, app["id"], "approver2")
    assert resp.status_code == 200

    result = run_sweep(client, auth)
    assert result["sync_failed"] == 1

    detail = client.get(f"/api/v1/applications/{app['id']}", headers=auth("student1")).json()
    assert detail["status"] == "APPROVED"
    assert detail["sync_status"] == "FAILED"
    assert "503" in (detail["sync_last_error"] or "")

    syncs = client.get("/api/v1/sync", headers=auth("admin")).json()
    record = next(s for s in syncs if s["application_id"] == app["id"])
    assert record["status"] == "FAILED"

    fake_m1.failure_mode = False
    retried = client.post(f"/api/v1/sync/{record['id']}/retry", json={}, headers=auth("admin"))
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "SUCCEEDED"
    assert app["id"] in fake_m1.occupancies


def test_cancel_approved_application_releases_occupancy(client, auth, fake_m1):
    app = submit(client, auth)
    approve(client, auth, app["id"], "approver1")
    approve(client, auth, app["id"], "approver2")
    run_sweep(client, auth)
    assert app["id"] in fake_m1.occupancies

    resp = client.post(
        f"/api/v1/applications/{app['id']}/cancel",
        json={"reason": "行程变更"},
        headers=auth("student1"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CANCELLED"

    run_sweep(client, auth)
    assert app["id"] not in fake_m1.occupancies
    records = client.get(f"/api/v1/applications/{app['id']}/approvals", headers=auth("student1")).json()
    assert any(r["action"] == "CANCEL" for r in records)


def test_cancel_pending_application_has_no_sync_side_effect(client, auth):
    app = submit(client, auth)
    resp = client.post(
        f"/api/v1/applications/{app['id']}/cancel", json={"reason": "不需要了"}, headers=auth("student1")
    )
    assert resp.status_code == 200
    syncs = client.get(f"/api/v1/sync?application_id={app['id']}", headers=auth("admin")).json()
    assert syncs == []


def test_invalid_time_range_and_duration(client, auth):
    start, end = slot(duration_hours=2)
    resp = client.post(
        "/api/v1/applications",
        json={"room_id": "1", "purpose": "错误时段", "start_time": end, "end_time": start},
        headers=auth("student1"),
    )
    assert resp.status_code == 422

    start, end = slot(duration_hours=13)
    resp = client.post(
        "/api/v1/applications",
        json={"room_id": "1", "purpose": "超长", "start_time": start, "end_time": end},
        headers=auth("student1"),
    )
    assert resp.status_code == 422


def test_m1_personal_booking_blocks_final_approval(client, auth, fake_m1):
    """A 的 M1 侧已有个人预约时，二级审批必须 409，且申请状态不变。"""
    app = submit(client, auth)
    assert approve(client, auth, app["id"], "approver1").status_code == 200

    fake_m1.conflict_reason = "明德楼 201 的座位 01 已被 M1:个人预约 占用"
    resp = approve(client, auth, app["id"], "approver2")
    assert resp.status_code == 409, resp.text
    assert "M1" in resp.text

    detail = client.get(f"/api/v1/applications/{app['id']}", headers=auth("student1")).json()
    assert detail["status"] == "PENDING_L2", "M1 冲突时不得改变申请状态"
    assert app["id"] not in fake_m1.occupancies


def test_permanent_m1_error_marks_sync_dead_without_retry(client, auth, fake_m1):
    app = submit(client, auth)
    approve(client, auth, app["id"], "approver1")
    approve(client, auth, app["id"], "approver2")

    fake_m1.occupy_error = M1Error("M1 返回 409：该时段已有个人预约", 409)
    result = run_sweep(client, auth)
    assert result["sync_failed"] == 1

    syncs = client.get("/api/v1/sync", headers=auth("admin")).json()
    record = next(s for s in syncs if s["application_id"] == app["id"])
    assert record["status"] == "DEAD", "4xx 不应继续重试"
    assert record["attempts"] == 1
    assert "409" in (record["last_error"] or "")


def test_m1_unavailable_at_preflight_does_not_block_approval(client, auth, fake_m1):
    """预检遇到 5xx 时审批仍可继续，由 outbox 负责重试，避免网络抖动卡死审批。"""
    fake_m1.failure_mode = True
    app = submit(client, auth)
    approve(client, auth, app["id"], "approver1")
    resp = approve(client, auth, app["id"], "approver2")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"
    assert resp.json()["sync_status"] == "PENDING"
