from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import io
import time

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from conftest import assign_ticket, create_ticket, details
from m3.app import create_app
from m3.manage import migrate, migration_config
from m3.models import Ticket, SyncJob
from m3.worker import tick
from shared.client import CommonClient
from shared.db import database


def test_migration_roundtrip_and_no_m1_table_changes(tmp_path):
    url = f'sqlite:///{tmp_path / "migration.sqlite3"}'
    engine, _ = database(url)
    with engine.begin() as c:
        c.execute(text('CREATE TABLE rooms(id INTEGER PRIMARY KEY, name TEXT)'))
        c.execute(text("INSERT INTO rooms VALUES(1, 'M1 sentinel')"))
    migrate(url)
    command.check(migration_config(url))
    assert 'm3_tickets' in inspect(engine).get_table_names()
    command.downgrade(migration_config(url), 'base')
    assert 'm3_tickets' not in inspect(engine).get_table_names()
    with engine.connect() as c:
        assert c.execute(text('SELECT name FROM rooms')).scalar() == 'M1 sentinel'
    migrate(url)
    assert 'c_users' in inspect(engine).get_table_names()
    engine.dispose()


def test_postgresql_migration_compiles_offline():
    config = migration_config('postgresql+psycopg://unused:unused@localhost/unused')
    output = io.StringIO()
    config.output_buffer = output
    command.upgrade(config, 'head', sql=True)
    sql = output.getvalue()
    assert 'CREATE TABLE m3_tickets' in sql and 'CREATE TABLE c_users' in sql
    assert 'SERIAL' in sql and 'DROP TABLE rooms' not in sql


def test_concurrent_assignment_creates_one_job(env):
    tid = create_ticket(env)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: assign_ticket(env, tid), range(2)))
    assert sorted(r.status_code for r in responses) == [202, 409]
    assert len(details(env, tid)['sync_jobs']) == 1


def test_concurrent_workers_claim_once(env):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: tick(env.app), range(2)))
    detail = details(env, tid)
    assert detail['state'] == 'repairing'
    assert detail['sync_jobs'][0]['attempts'] == 1
    assert len([h for h in detail['history'] if h['action'] == 'sync_succeeded']) == 1


def test_background_runs_without_page_refresh(env):
    tid = create_ticket(env)
    with env.app.state.sessions.begin() as db:
        db.get(Ticket, tid).due = env.clock[0] - timedelta(seconds=1)
    background_app = create_app(env.settings, m1=env.app.state.m1, clock=lambda: env.clock[0], background=True)
    with TestClient(background_app):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with env.app.state.sessions() as db:
                if db.get(Ticket, tid).escalated:
                    break
            time.sleep(.05)
        else:
            raise AssertionError('未刷新网页时，后台未自动升级')
    assert details(env, tid)['escalated']


def test_common_client_contract(env):
    client = CommonClient('http://testserver', '2' * 32, client=env.client)
    identity = client.identity(env.headers['approver']['Authorization'].split()[1], 'm2:approve')
    assert identity['id'] == env.users['approver']
    first = client.audit('approval-42', identity['id'], 'approved', 'application-42')
    assert client.audit('approval-42', identity['id'], 'approved', 'application-42') == first
    client.notify('approval-42', env.users['alice'], '审批已通过')
    rows = env.client.get('/api/audit', headers=env.headers['admin']).json()
    assert any(a['source'] == 'M2' and a['action'] == 'approved' for a in rows)


def test_job_survives_app_recreation(env):
    tid = create_ticket(env)
    assign_ticket(env, tid)
    restarted = create_app(env.settings, m1=env.app.state.m1, clock=lambda: env.clock[0], background=False)
    try:
        tick(restarted)
        assert details(env, tid)['state'] == 'repairing'
    finally:
        restarted.state.engine.dispose()
