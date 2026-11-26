from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

# M1（成员 A 的 demo）只接受北京时间、不带时区后缀的时间字符串。
# M2 内部统一存 UTC-naive，所有转换都收敛在这个模块里，避免把 demo 限制扩散到业务代码。
M1_TIMEZONE = ZoneInfo("Asia/Shanghai")


def to_m1_time(value: datetime) -> str:
    """UTC-naive（M2 内部）→ 北京时间 naive（M1 接口）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(M1_TIMEZONE).replace(tzinfo=None).isoformat(timespec="seconds")


def from_m1_time(value: str) -> datetime:
    """北京时间 naive（M1 接口）→ UTC-naive（M2 内部）。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=M1_TIMEZONE)
    return parsed.astimezone(UTC).replace(tzinfo=None)


class M1Error(Exception):
    """调用 M1 失败。message 会记录到 outbox，供页面展示和排查。

    retryable=False 表示重试没有意义（4xx、占用未生效等），worker 应直接置 DEAD；
    网络错误和 5xx 默认可重试。
    """

    def __init__(self, message: str, status_code: int | None = None, *, retryable: bool | None = None):
        super().__init__(message)
        self.status_code = status_code
        if retryable is None:
            retryable = status_code is None or status_code >= 500
        self.retryable = retryable


class M1Client:
    """M1 适配层：把 M2 的 UTC/字符串教室 ID 翻译成 A 的 demo 接口。

    契约见 docs/api/m1-m2-contract.md v0.2。
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 3.0,
        demo_user: str = "admin",
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.demo_user = demo_user
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={"X-Demo-User": demo_user},
            # 服务间调用不应走 shell 里的 HTTP(S)_PROXY；本机 socks 代理会让 httpx 直接报错。
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs):
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise M1Error(f"M1 不可达：{exc}") from exc
        if resp.status_code >= 400:
            detail = ""
            try:
                payload = resp.json()
                detail = payload.get("detail", payload)
            except Exception:
                detail = resp.text[:200]
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False)
            raise M1Error(f"M1 返回 {resp.status_code}：{detail}", resp.status_code)
        if resp.status_code == 204:
            return None
        return resp.json()

    def _room_id(self, room_id: str) -> int:
        try:
            return int(room_id)
        except (TypeError, ValueError) as exc:
            raise M1Error(f"教室 ID 非法：{room_id!r}（A 的 M1 使用整数 ID）", 422, retryable=False) from exc

    def list_rooms(self) -> list[dict]:
        payload = self._request("GET", "/api/meta")
        rooms = payload.get("rooms", []) if isinstance(payload, dict) else []
        return [
            {
                "id": str(room["id"]),
                "name": room["name"],
                "area": room.get("area", ""),
                "opens": room.get("opens"),
                "closes": room.get("closes"),
                "active": bool(room.get("active", True)),
            }
            for room in rooms
        ]

    def get_room(self, room_id: str) -> dict:
        for room in self.list_rooms():
            if room["id"] == str(room_id):
                return room
        raise M1Error("M1 返回 404：教室不存在", 404, retryable=False)

    def check_conflict(self, room_id: str, start: datetime, end: datetime) -> str | None:
        """只读预检：M1 侧是否已有个人预约或 M2/M3 限制。

        返回 None 表示可占用；返回字符串表示冲突原因。
        M1 不可用时抛 M1Error，由调用方决定是否继续走 outbox。
        """
        seats = self._request(
            "GET",
            "/api/seats",
            params={
                "start": to_m1_time(start),
                "end": to_m1_time(end),
                "room_id": self._room_id(room_id),
            },
        )
        if not isinstance(seats, list) or not seats:
            return "M1 未返回该教室的座位状态，无法确认是否可以占用"
        for seat in seats:
            for source in seat.get("sources") or []:
                if source == "M1:个人预约" or source.startswith(("M2:", "M3:")):
                    room_name = seat.get("room") or f"教室 {room_id}"
                    label = seat.get("label", seat.get("id", "?"))
                    return f"{room_name} 的座位 {label} 已被 {source} 占用"
        if all(not seat.get("available", False) for seat in seats):
            reasons = sorted({reason for seat in seats for reason in (seat.get("sources") or [])})
            suffix = f"：{'、'.join(reasons)}" if reasons else ""
            return f"教室在所选时段不可用{suffix}"
        return None

    def occupy(self, *, application_id: str, room_id: str, start: datetime, end: datetime, reason: str) -> dict:
        """写 M2 整教室限制。幂等键是 application_id（M1 的 external_id + source=M2）。"""
        payload = self._request(
            "POST",
            "/api/integrations/restrictions",
            json={
                "source": "M2",
                "external_id": application_id,
                "room_id": self._room_id(room_id),
                "start": to_m1_time(start),
                "end": to_m1_time(end),
            },
        )
        # A 的当前实现：业务编号释放后再次提交会返回 200 + active=0，但不会重新生效。
        # 这是“2xx 掩盖失败”，M2 必须自己识别，否则 outbox 会谎报成功。
        if isinstance(payload, dict) and "active" in payload and not payload["active"]:
            raise M1Error(
                "M1 返回成功但占用未生效：该业务编号已解除，不能复用，需人工确认",
                409,
                retryable=False,
            )
        return payload

    def release(self, application_id: str) -> None:
        """幂等释放 M2 自己的限制；A 的接口对不存在的编号也返回 200。"""
        self._request(
            "DELETE",
            f"/api/integrations/restrictions/M2/{quote(application_id, safe='')}",
        )

    def create_personal_booking(self, *, room_id: str, start: datetime, end: datetime) -> dict:
        """仅演示/联调：在 M1 制造一条个人座位预约，用于验证 M2 冲突预检。"""
        room = self._room_id(room_id)
        seat_id = (room - 1) * 24 + 1  # 与 A 的 demo 初始化顺序一致：每间教室 24 个座位
        return self._request(
            "POST",
            "/api/reservations",
            json={"seat_id": seat_id, "start": to_m1_time(start), "end": to_m1_time(end)},
            headers={"X-Demo-User": "student-a"},
        )

    # ---- 仅演示环境使用的运维接口 ----
    def set_failure_mode(self, enabled: bool) -> dict:
        return self._request("POST", "/admin/failure-mode", json={"enabled": enabled})

    def reset(self) -> dict:
        return self._request("POST", "/admin/reset")

    def state(self) -> dict:
        try:
            return self._request("GET", "/admin/state")
        except M1Error as exc:
            if exc.status_code != 404:
                raise
        # 真实 M1（A 的实现）没有 /admin/state；用管理员总览接口提供只读视图。
        overview = self._request("GET", "/api/admin/overview")
        return {
            "failure_mode": False,
            "demo_controls": False,
            "restrictions": overview.get("restrictions", []),
            "reservations": [],
            "call_log": [],
        }
