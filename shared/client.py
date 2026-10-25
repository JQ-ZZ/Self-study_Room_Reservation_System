"""供 A/B 在自己的模块中使用；不将 M1 演示身份冒充统一登录身份。"""
import httpx
from fastapi import HTTPException


class CommonClient:
    def __init__(self, base_url, service_token, client=None):
        self.client = client or httpx.Client(base_url=base_url, timeout=5)
        self.service_token = service_token
        self.owned = client is None

    def identity(self, bearer_token, permission=None):
        """必须从当前请求取得真实 Bearer token，不能信任前端传来的角色。"""
        try:
            response = self.client.get('/api/auth/me', headers={'Authorization': f'Bearer {bearer_token}'})
        except httpx.TransportError as exc:
            raise HTTPException(503, '公共认证服务暂不可用') from exc
        if response.status_code == 401:
            raise HTTPException(401, '会话无效')
        if response.status_code != 200:
            raise HTTPException(503, '公共认证服务暂不可用')
        user = response.json()
        if permission and permission not in user['permissions']:
            raise HTTPException(403, '权限不足')
        return user

    def notify(self, event_id, user_id, text):
        response = self.client.post('/api/integrations/notifications', headers={'X-Service-Token': self.service_token},
                                    json={'event_id': event_id, 'user_id': user_id, 'text': text})
        response.raise_for_status()
        return response.json()

    def audit(self, event_id, actor, action, target, detail=''):
        response = self.client.post('/api/integrations/audit', headers={'X-Service-Token': self.service_token},
                                    json={'event_id': event_id, 'actor': actor, 'action': action, 'target': target, 'detail': detail})
        response.raise_for_status()
        return response.json()

    def close(self):
        if self.owned:
            self.client.close()
