from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from m1_demo import app as m1
from m3.app import create_app
from m3.m1_client import M1Client
from m3.manage import migrate
from shared.config import Settings
from shared.security import new_user

PASSWORD = 'Test-Only-Password-123!'


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = [datetime(2030, 1, 2, 8)]
    monkeypatch.setattr(m1, 'DB', str(tmp_path / 'm1.sqlite3'))
    monkeypatch.setattr(m1, 'now', lambda: clock[0])
    m1.init()
    upstream = TestClient(m1.app)
    settings = Settings(database_url=f'sqlite:///{tmp_path / "m3.sqlite3"}',
                        service_token_m1='1' * 32, service_token_m2='2' * 32)
    migrate(settings.database_url)
    adapter = M1Client(settings, client=upstream)
    app = create_app(settings, m1=adapter, clock=lambda: clock[0], background=False)
    users = {}
    with app.state.sessions.begin() as db:
        for name, role in [('alice', 'student'), ('bob', 'student'), ('admin', 'admin'),
                           ('tech', 'technician'), ('othertech', 'technician'), ('approver', 'approver')]:
            user = new_user(name, PASSWORD, name, role)
            db.add(user)
            users[name] = user.id
    with TestClient(app) as client:
        headers = {}
        for name in users:
            response = client.post('/api/auth/login', json={'username': name, 'password': PASSWORD})
            assert response.status_code == 200, response.text
            headers[name] = {'Authorization': 'Bearer ' + response.json()['access_token']}
        yield SimpleNamespace(app=app, client=client, upstream=upstream, clock=clock,
                              users=users, headers=headers, settings=settings)
    upstream.close()


def create_ticket(env, actor='alice', room_id=1):
    response = env.client.post('/api/m3/tickets', headers=env.headers[actor],
                               json={'room_id': room_id, 'description': '教室灯具损坏，需要检查维修'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def assign_ticket(env, tid):
    return env.client.post(f'/api/m3/tickets/{tid}/assign', headers=env.headers['admin'],
                           json={'assignee_id': env.users['tech']})


def details(env, tid):
    response = env.client.get(f'/api/m3/tickets/{tid}', headers=env.headers['admin'])
    assert response.status_code == 200, response.text
    return response.json()


def seats(env):
    return env.upstream.get('/api/seats', params={'room_id': 1, 'start': '2030-01-02T09:00:00',
                                                 'end': '2030-01-02T10:00:00'}).json()
