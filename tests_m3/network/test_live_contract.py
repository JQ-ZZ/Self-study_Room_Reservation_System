"""显式 opt-in 的真实网络验收；仅对无预约的专用测试教室执行，会留下测试工单。"""
import os
import time
from datetime import datetime, timedelta, timezone
import httpx
import pytest

pytestmark = pytest.mark.skipif(os.getenv('M3_NETWORK_ALLOW_WRITE') != '1',
    reason='未授权对已运行的专用测试环境执行真实网络写入验收')


def test_live_room_repair_roundtrip():
    required = ['M3_NETWORK_URL', 'M1_NETWORK_URL', 'M3_TEST_REPORTER', 'M3_TEST_ADMIN',
                'M3_TEST_TECH', 'M3_TEST_PASSWORD', 'M3_TEST_ROOM_ID']
    assert all(os.getenv(k) for k in required), '请按验收说明配置全部环境变量'
    with httpx.Client(base_url=os.environ['M3_NETWORK_URL'], timeout=10) as client, \
         httpx.Client(base_url=os.environ['M1_NETWORK_URL'], timeout=10) as m1:
        headers = {}
        tech_id = None
        for role in ('REPORTER', 'ADMIN', 'TECH'):
            r = client.post('/api/auth/login', json={'username': os.environ['M3_TEST_' + role], 'password': os.environ['M3_TEST_PASSWORD']})
            assert r.status_code == 200
            data = r.json()
            headers[role] = {'Authorization': 'Bearer ' + data['access_token']}
            if role == 'TECH':
                tech_id = data['user']['id']
        room_id = int(os.environ['M3_TEST_ROOM_ID'])
        meta = m1.get('/api/meta').json()
        start = datetime.fromisoformat(meta['now']).replace(minute=0, second=0) + timedelta(hours=1)
        room = next(r for r in meta['rooms'] if r['id'] == room_id)
        if start.hour < room['opens'] or start.hour >= room['closes']:
            start = (start + timedelta(days=1)).replace(hour=room['opens'])
        params = {'room_id': room_id, 'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat()}
        baseline = m1.get('/api/seats', params=params).json()
        assert baseline and all(s['available'] for s in baseline), '必须使用无预约、无限制的专用测试教室'
        response = client.post('/api/m3/tickets', headers=headers['REPORTER'], json={'room_id': room_id, 'description': '真实网络验收：教室灯具检修'})
        assert response.status_code == 201
        tid = response.json()['id']
        assert client.post(f'/api/m3/tickets/{tid}/assign', headers=headers['ADMIN'], json={'assignee_id': tech_id}).status_code == 202
        def wait_state(expected):
            for _ in range(30):
                r = client.get(f'/api/m3/tickets/{tid}', headers=headers['ADMIN'])
                assert r.status_code == 200
                if r.json()['state'] == expected:
                    return
                time.sleep(.5)
            pytest.fail(f'工单 {tid} 未到达 {expected}，请检查同步记录')
        wait_state('repairing')
        rows = m1.get('/api/seats', params=params).json()
        assert all(f'M3:repair-{tid}' in s['sources'] and not s['available'] for s in rows)
        assert client.post(f'/api/m3/tickets/{tid}/complete', headers=headers['TECH'], json={'note': '网络验收维修完成'}).status_code == 200
        assert client.post(f'/api/m3/tickets/{tid}/accept', headers=headers['ADMIN'], json={'approved': True, 'note': '网络验收通过'}).status_code == 200
        wait_state('closed')
        assert all(s['available'] for s in m1.get('/api/seats', params=params).json())
