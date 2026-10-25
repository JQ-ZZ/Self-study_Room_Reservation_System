from datetime import timedelta
import httpx
from sqlalchemy import select

from conftest import PASSWORD, assign_ticket, create_ticket, details, seats
from m3.models import SyncJob
from m3.worker import tick


def finish(env, tid, approved=True):
    r = env.client.post(f'/api/m3/tickets/{tid}/complete', headers=env.headers['tech'], json={'note': '更换灯管，通电测试正常'})
    assert r.status_code == 200, r.text
    r = env.client.post(f'/api/m3/tickets/{tid}/accept', headers=env.headers['admin'], json={'approved': approved, 'note': '现场检查完毕'})
    assert r.status_code == 200, r.text


def test_full_flow_restores_room_and_reviews_once(env):
    tid = create_ticket(env)
    assert assign_ticket(env, tid).json()['state'] == 'starting'
    tick(env.app)
    assert details(env, tid)['state'] == 'repairing'
    rows = seats(env)
    assert len(rows) == 24 and all(not s['available'] for s in rows)
    assert all(f'M3:repair-{tid}' in s['sources'] for s in rows)
    finish(env, tid)
    assert details(env, tid)['state'] == 'closing'
    assert all(not s['available'] for s in seats(env))
    tick(env.app)
    assert details(env, tid)['state'] == 'closed'
    assert all(s['available'] for s in seats(env))
    review = {'rating': 5, 'comment': '维修及时'}
    assert env.client.post(f'/api/m3/tickets/{tid}/review', headers=env.headers['alice'], json=review).status_code == 200
    assert env.client.post(f'/api/m3/tickets/{tid}/review', headers=env.headers['alice'], json=review).status_code == 409
    detail = details(env, tid)
    assert detail['rating'] == 5
    assert [h['action'] for h in detail['history']] == ['created', 'assigned', 'sync_succeeded', 'completed', 'accepted', 'sync_succeeded', 'reviewed']
    assert len(env.client.get('/api/notifications', headers=env.headers['alice']).json()) >= 6


def test_m2_overlap_survives_m3_release(env):
    r = env.upstream.post('/api/integrations/restrictions', headers={'X-Demo-User': 'admin'}, json={
        'source': 'M2', 'external_id': 'approval-1', 'room_id': 1, 'start': '2030-01-02T09:00:00', 'end': '2030-01-02T10:00:00'})
    assert r.status_code == 200
    tid = create_ticket(env)
    assign_ticket(env, tid)
    tick(env.app)
    assert all(len(s['sources']) == 2 for s in seats(env))
    finish(env, tid)
    tick(env.app)
    assert all(s['sources'] == ['M2:approval-1'] and not s['available'] for s in seats(env))


def test_multiple_repairs_release_only_their_own_source(env):
    first, second = create_ticket(env), create_ticket(env)
    assign_ticket(env, first)
    assign_ticket(env, second)
    tick(env.app)
    finish(env, first)
    tick(env.app)
    assert all(s['sources'] == [f'M3:repair-{second}'] for s in seats(env))


def test_reservation_conflict_is_visible_and_can_retry(env):
    reservation = env.upstream.post('/api/reservations', json={'seat_id': 1, 'start': '2030-01-02T09:00:00', 'end': '2030-01-02T10:00:00'}).json()['id']
    tid = create_ticket(env)
    assign_ticket(env, tid)
    tick(env.app)
    detail = details(env, tid)
    assert detail['state'] == 'starting'
    assert detail['sync_jobs'][0]['status'] == 'failed'
    assert '409' in detail['sync_jobs'][0]['last_error']
    assert seats(env)[0]['sources'] == ['M1:个人预约']
    env.upstream.post(f'/api/reservations/{reservation}/cancel')
    assert env.client.post(f'/api/m3/tickets/{tid}/retry', headers=env.headers['admin']).status_code == 202
    tick(env.app)
    assert details(env, tid)['state'] == 'repairing'
    assert all(not s['available'] for s in seats(env))


def test_lost_response_retries_idempotently(env, monkeypatch):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    original = env.upstream.request
    def lost(method, path, **kwargs):
        response = original(method, path, **kwargs)
        if method == 'POST':
            raise httpx.ReadTimeout('simulated lost response')
        return response
    monkeypatch.setattr(env.upstream, 'request', lost)
    tick(env.app)
    assert details(env, tid)['state'] == 'starting'
    assert details(env, tid)['sync_jobs'][0]['status'] == 'retry'
    assert all(not s['available'] for s in seats(env))
    monkeypatch.setattr(env.upstream, 'request', original)
    env.clock[0] += timedelta(seconds=3)
    tick(env.app)
    assert details(env, tid)['state'] == 'repairing'
    overview = env.upstream.get('/api/admin/overview', headers={'X-Demo-User': 'admin'}).json()
    assert len(overview['restrictions']) == 1


def test_release_failure_does_not_close_ticket(env, monkeypatch):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    tick(env.app)
    finish(env, tid)
    original = env.upstream.request
    def unavailable(method, path, **kwargs):
        if method == 'DELETE':
            return httpx.Response(503, json={'detail': 'unavailable'})
        return original(method, path, **kwargs)
    monkeypatch.setattr(env.upstream, 'request', unavailable)
    tick(env.app)
    assert details(env, tid)['state'] == 'closing'
    assert all(not s['available'] for s in seats(env))
    monkeypatch.setattr(env.upstream, 'request', original)
    env.clock[0] += timedelta(seconds=3)
    tick(env.app)
    assert details(env, tid)['state'] == 'closed'
    assert all(s['available'] for s in seats(env))


def test_rejected_acceptance_keeps_restriction(env):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    tick(env.app)
    finish(env, tid, approved=False)
    assert details(env, tid)['state'] == 'repairing'
    assert all(not s['available'] for s in seats(env))
    finish(env, tid)
    tick(env.app)
    assert all(s['available'] for s in seats(env))


def test_sla_escalates_once(env):
    tid = create_ticket(env)
    env.clock[0] += timedelta(hours=24)
    tick(env.app)
    tick(env.app)
    token = env.client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD}).json()['access_token']
    env.headers['admin'] = {'Authorization': 'Bearer ' + token}
    detail = details(env, tid)
    assert detail['escalated'] is True
    assert len([h for h in detail['history'] if h['action'] == 'escalated']) == 1


def test_expired_worker_lease_recovers(env):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    with env.app.state.sessions.begin() as db:
        job = db.scalar(select(SyncJob).where(SyncJob.ticket_id == tid))
        job.status, job.lease_token, job.lease_until = 'running', 'old-worker', env.clock[0] + timedelta(seconds=60)
    tick(env.app)
    assert details(env, tid)['state'] == 'starting'
    env.clock[0] += timedelta(seconds=61)
    tick(env.app)
    assert details(env, tid)['state'] == 'repairing'
