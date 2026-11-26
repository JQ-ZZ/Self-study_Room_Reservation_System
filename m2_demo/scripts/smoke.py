#!/usr/bin/env python3
"""端到端冒烟脚本：只依赖标准库，适合本地演示和 CI。

用法：
    python3 scripts/smoke.py [base_url]
默认 base_url = http://localhost:8080
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict | list | None]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"detail": raw.decode(errors="replace")}
        return exc.code, payload


def must(status: int, expected: int, payload, step: str) -> dict | list | None:
    if status != expected:
        print(f"  ✗ {step}: HTTP {status} {payload}")
        sys.exit(1)
    print(f"  ✓ {step}")
    return payload


def wait_health(path: str, seconds: int = 60) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            status, _ = call("GET", path)
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    print(f"服务未就绪：{BASE}{path}")
    sys.exit(1)


def login(username: str) -> str:
    status, payload = call("POST", "/api/v1/auth/login", body={"username": username})
    must(status, 200, payload, f"登录 {username}")
    return payload["access_token"]


def main() -> None:
    print(f"目标：{BASE}")
    wait_health("/health")

    student = login("student1")
    approver1 = login("approver1")
    approver2 = login("approver2")
    admin = login("admin")

    status, rooms = call("GET", "/api/v1/rooms", token=student)
    must(status, 200, rooms, f"通过 HTTP 从 M1 读取教室主数据（{len(rooms)} 间）")

    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    end = start + timedelta(hours=2)
    status, app = call(
        "POST",
        "/api/v1/applications",
        token=student,
        body={
            "room_id": rooms[0]["id"],
            "purpose": "冒烟测试：班级班会",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        },
    )
    must(status, 201, app, f"提交申请（{app['status']}）")

    status, app = call("POST", f"/api/v1/applications/{app['id']}/approve", token=approver1, body={"comment": "同意"})
    must(status, 200, app, f"一级审批通过（{app['status']}）")
    assert app["status"] == "PENDING_L2"

    status, app = call("POST", f"/api/v1/applications/{app['id']}/approve", token=approver2, body={"comment": "同意"})
    must(status, 200, app, f"二级审批通过（{app['status']}，同步 {app['sync_status']}）")

    status, result = call("POST", "/api/v1/ops/run-sweep", token=admin, body={})
    must(status, 200, result, f"执行 outbox 同步：{result}")

    status, detail = call("GET", f"/api/v1/applications/{app['id']}", token=student)
    must(status, 200, detail, f"查询申请（同步状态 {detail['sync_status']}）")

    status, m1_state = call("GET", "/api/v1/ops/m1-state", token=admin)
    must(status, 200, m1_state, "读取 M1 限制状态")
    occupied = [
        o for o in m1_state["restrictions"]
        if o["source"] == "M2" and o["external_id"] == app["id"] and o["active"]
    ]
    if not occupied:
        print("  ✗ M1 中没有找到该申请的整教室限制")
        sys.exit(1)
    print(f"  ✓ M1 已占用教室 {occupied[0]['room_id']}（M2 → M1 写回成功）")

    status, app = call(
        "POST",
        f"/api/v1/applications/{app['id']}/cancel",
        token=student,
        body={"reason": "冒烟测试结束，释放教室"},
    )
    must(status, 200, app, f"撤销已批准申请（{app['status']}）")

    status, result = call("POST", "/api/v1/ops/run-sweep", token=admin, body={})
    must(status, 200, result, f"执行释放同步：{result}")

    status, m1_state = call("GET", "/api/v1/ops/m1-state", token=admin)
    must(status, 200, m1_state, "复查 M1 限制状态")
    released = [
        o for o in m1_state["restrictions"]
        if o["source"] == "M2" and o["external_id"] == app["id"] and not o["active"]
    ]
    if not released:
        print("  ✗ M1 中该申请的整教室限制未被解除")
        sys.exit(1)
    print("  ✓ M1 已释放整教室限制（幂等释放成功）")
    print("\n冒烟测试通过。打开 http://localhost:8080/demo/ 查看演示页面。")


if __name__ == "__main__":
    main()
