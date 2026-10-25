"""M1 唯一接入边界。生产代码只走 HTTP，不导入 m1_demo 或访问其表。"""
import httpx


class M1Error(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


class M1Client:
    def __init__(self, settings, client=None):
        # 模块间直连不继承终端的 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY，
        # 防止本地或容器内服务请求误入代理后返回 502。
        self.client = client or httpx.Client(
            base_url=settings.m1_base_url, timeout=5, follow_redirects=False, trust_env=False)
        self.owned = client is None
        self.headers = ({'X-Demo-User': 'admin'} if settings.m1_mode == 'demo'
                        else {'Authorization': f'Bearer {settings.m1_service_token}'})

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, headers=self.headers, **kwargs)
        except httpx.TransportError as exc:
            raise M1Error('M1 网络连接失败或超时') from exc
        if not 200 <= response.status_code < 300:
            # 不持久化上游原始响应，避免记录令牌、堆栈等敏感信息。
            reasons = {401: '认证失败', 403: '无权限', 404: '教室或接口不存在',
                       409: '已有预约或幂等参数冲突，请协调处理', 422: '参数不符合 M1 契约'}
            raise M1Error(f'M1 HTTP {response.status_code}: {reasons.get(response.status_code, "上游请求失败")}',
                          retryable=response.status_code >= 500 or response.status_code in (408, 429))
        try:
            data = response.json()
        except ValueError as exc:
            raise M1Error('M1 返回非 JSON 响应') from exc
        if not isinstance(data, dict):
            raise M1Error('M1 响应结构不符合契约')
        return data

    def rooms(self):
        data = self.request('GET', '/api/meta')
        rooms = data.get('rooms')
        if not isinstance(rooms, list) or any(not isinstance(r, dict) or not {'id', 'name', 'active'} <= r.keys() for r in rooms):
            raise M1Error('M1 空间列表结构错误')
        return rooms

    def restrict(self, ticket):
        data = self.request('POST', '/api/integrations/restrictions', json={
            'source': 'M3', 'external_id': f'repair-{ticket.id}', 'room_id': ticket.room_id,
            'start': ticket.maintenance_start.isoformat(timespec='seconds'),
            # 不按预计完工时间自动放开，只有管理员验收后才解除限制。
            'end': '9999-12-31T23:59:59',
        })
        if not isinstance(data.get('id'), int) or data.get('active', 1) != 1:
            raise M1Error('M1 限制未确认生效；可能已被外部解除', retryable=False)

    def release(self, ticket):
        data = self.request('DELETE', f'/api/integrations/restrictions/M3/repair-{ticket.id}')
        if not isinstance(data.get('message'), str):
            raise M1Error('M1 未返回解除确认')

    def close(self):
        if self.owned:
            self.client.close()
