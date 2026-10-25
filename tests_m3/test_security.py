from datetime import timedelta
import pytest

from conftest import PASSWORD, assign_ticket, create_ticket
from m3.worker import tick


def test_requires_real_session_not_demo_header(env):
    assert env.client.get('/api/m3/tickets').status_code == 401
    assert env.client.get('/api/m3/tickets', headers={'X-Demo-User': 'admin'}).status_code == 401
    assert env.client.get('/api/users', headers=env.headers['alice']).status_code == 403
    assert env.client.get('/api/audit', headers=env.headers['tech']).status_code == 403


def test_ticket_visibility_and_assignee_ownership(env):
    tid = create_ticket(env)
    assert env.client.get(f'/api/m3/tickets/{tid}', headers=env.headers['bob']).status_code == 404
    assert env.client.get('/api/m3/tickets', headers=env.headers['bob']).json()['total'] == 0
    assign_ticket(env, tid)
    tick(env.app)
    assert env.client.post(f'/api/m3/tickets/{tid}/complete', headers=env.headers['othertech'], json={'note': '假冒完工'}).status_code == 404
    assert env.client.post(f'/api/m3/tickets/{tid}/assign', headers=env.headers['alice'], json={'assignee_id': env.users['tech']}).status_code == 403


def test_state_machine_rejects_skips_and_duplicate_assign(env):
    tid = create_ticket(env)
    assert env.client.post(f'/api/m3/tickets/{tid}/accept', headers=env.headers['admin'], json={'approved': True, 'note': '跳过维修'}).status_code == 409
    assert env.client.post(f'/api/m3/tickets/{tid}/review', headers=env.headers['alice'], json={'rating': 5}).status_code == 409
    assert assign_ticket(env, tid).status_code == 202
    assert assign_ticket(env, tid).status_code == 409


def test_logout_expiry_and_password_revocation(env):
    assert env.client.post('/api/auth/logout', headers=env.headers['bob']).status_code == 200
    assert env.client.get('/api/auth/me', headers=env.headers['bob']).status_code == 401
    r = env.client.post('/api/auth/password', headers=env.headers['alice'], json={'old_password': PASSWORD, 'new_password': 'Changed-password-456!'})
    assert r.status_code == 200
    assert env.client.get('/api/auth/me', headers=env.headers['alice']).status_code == 401
    env.clock[0] += timedelta(hours=8)
    assert env.client.get('/api/auth/me', headers=env.headers['admin']).status_code == 401


def test_user_management_revokes_sessions(env):
    r = env.client.put(f'/api/users/{env.users["tech"]}/access', headers=env.headers['admin'], json={'active': False, 'role': 'technician'})
    assert r.status_code == 200
    assert env.client.get('/api/auth/me', headers=env.headers['tech']).status_code == 401
    tid = create_ticket(env)
    assert assign_ticket(env, tid).status_code == 422
    assert env.client.put(f'/api/users/{env.users["admin"]}/access', headers=env.headers['admin'], json={'active': False, 'role': 'student'}).status_code == 409


def test_password_whitespace_is_preserved(env):
    password = ' Leading-password-123 '
    response = env.client.post('/api/users', headers=env.headers['admin'], json={
        'username': 'space-password', 'display_name': '测试账号', 'role': 'student', 'password': password})
    assert response.status_code == 201
    assert env.client.post('/api/auth/login', json={'username': 'space-password', 'password': password.strip()}).status_code == 401
    assert env.client.post('/api/auth/login', json={'username': 'space-password', 'password': password}).status_code == 200


def test_login_rate_limit_and_no_secret_in_audit(env):
    for _ in range(10):
        assert env.client.post('/api/auth/login', json={'username': 'alice', 'password': 'wrong'}).status_code == 401
    assert env.client.post('/api/auth/login', json={'username': 'alice', 'password': PASSWORD}).status_code == 429
    rows = env.client.get('/api/audit', headers=env.headers['admin']).text
    assert PASSWORD not in rows and 'Bearer' not in rows and 'wrong' not in rows


def test_notification_ownership_and_service_isolation(env):
    payload = {'event_id': 'booking-1-reminder', 'user_id': env.users['alice'], 'text': '预约即将开始'}
    assert env.client.post('/api/integrations/notifications', headers=env.headers['admin'], json=payload).status_code == 401
    headers = {'X-Service-Token': '1' * 32}
    first = env.client.post('/api/integrations/notifications', headers=headers, json=payload)
    assert first.status_code == 200
    assert env.client.post('/api/integrations/notifications', headers=headers, json=payload).json() == first.json()
    assert env.client.post('/api/integrations/notifications', headers=headers, json={**payload, 'text': '被修改'}).status_code == 409
    nid = first.json()['id']
    assert env.client.post(f'/api/notifications/{nid}/read', headers=env.headers['bob']).status_code == 404
    assert env.client.post(f'/api/notifications/{nid}/read', headers=env.headers['alice']).status_code == 200
    row = env.client.get('/api/notifications', headers=env.headers['alice']).json()[0]
    assert row['read_at'] and row['source'] == 'M1'


@pytest.mark.parametrize('payload', [
    {'room_id': 0, 'description': '需要维修设备'}, {'room_id': 1, 'description': '    '},
    {'room_id': 1, 'description': '需要维修设备', 'state': 'closed'},
])
def test_input_validation(env, payload):
    assert env.client.post('/api/m3/tickets', headers=env.headers['alice'], json=payload).status_code == 422
