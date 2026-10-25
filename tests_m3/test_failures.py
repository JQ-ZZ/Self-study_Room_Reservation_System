import httpx
import pytest
from sqlalchemy import select

from conftest import assign_ticket, create_ticket, details
from m3.m1_client import M1Error
from m3.models import SyncJob
from m3.worker import tick
from shared.config import Settings
from shared.models import Session, User


@pytest.mark.parametrize('response,retryable', [
    (httpx.Response(401, json={'detail': 'secret'}), False),
    (httpx.Response(503, json={'detail': 'secret'}), True),
    (httpx.Response(200, text='<html>not JSON</html>'), True),
    (httpx.Response(200, json=[]), True),
    (httpx.Response(200, json={'id': 5, 'active': 0}), False),
    (httpx.Response(200, json={'message': 'missing id'}), False),
])
def test_bad_upstream_response_never_marks_success(env, monkeypatch, response, retryable):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    monkeypatch.setattr(env.upstream, 'request', lambda *a, **kw: response)
    tick(env.app)
    detail = details(env, tid)
    assert detail['state'] == 'starting'
    assert detail['sync_jobs'][0]['status'] == ('retry' if retryable else 'failed')
    assert 'secret' not in detail['sync_jobs'][0]['last_error']


def test_missing_room_and_metadata_network_failure(env, monkeypatch):
    assert env.client.post('/api/m3/tickets', headers=env.headers['alice'],
                           json={'room_id': 9999, 'description': '不存在教室维修'}).status_code == 404
    def timeout(*args, **kwargs):
        raise httpx.ConnectTimeout('offline')
    monkeypatch.setattr(env.upstream, 'request', timeout)
    assert env.client.get('/api/m3/rooms', headers=env.headers['alice']).status_code == 503
    r = env.client.post('/api/m3/tickets', headers=env.headers['alice'], json={'room_id': 1, 'description': '无法查询教室信息'})
    assert r.status_code == 503
    assert env.client.get('/api/m3/tickets', headers=env.headers['admin']).json()['total'] == 0


def test_passwords_and_tokens_not_stored_in_plaintext(env):
    from conftest import PASSWORD
    with env.app.state.sessions() as db:
        user = db.get(User, env.users['alice'])
        assert user.password_hash.startswith('scrypt$') and PASSWORD not in user.password_hash
        token = env.headers['alice']['Authorization'].split()[1]
        assert token not in list(db.scalars(select(Session.token_hash)))


@pytest.mark.parametrize('settings', [
    Settings(sla_hours=0), Settings(sla_hours=float('nan')), Settings(poll_seconds=-1),
    Settings(m1_mode='service'), Settings(service_token_m1='short'),
    Settings(service_token_m1='x'*32, service_token_m2='x'*32), Settings(m1_base_url='javascript:alert(1)'),
    Settings(m1_base_url='http://user:secret@localhost'),
])
def test_bad_configuration_rejected(settings):
    with pytest.raises(ValueError):
        settings.validate()


def test_non_ascii_service_token_is_rejected_not_crashed(env):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from shared.security import service_source
    with pytest.raises(HTTPException) as exc:
        service_source(SimpleNamespace(app=env.app), x_service_token='无效服务凭据')
    assert exc.value.status_code == 401
