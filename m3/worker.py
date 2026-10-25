"""可恢复的数据库任务队列：带租约认领、幂等 HTTP、指数退避。"""
from datetime import timedelta
import logging
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm.exc import StaleDataError

from m3.m1_client import M1Error
from m3.models import SyncJob, Ticket
from m3.service import record

logger = logging.getLogger(__name__)


def sync_once(app):
    factory, clock = app.state.sessions, app.state.clock
    t = clock()
    eligible = (SyncJob.status.in_(['pending', 'retry', 'running']), SyncJob.next_run <= t,
                or_(SyncJob.lease_until.is_(None), SyncJob.lease_until <= t))
    with factory() as db:
        ids = list(db.scalars(select(SyncJob.id).where(*eligible).order_by(SyncJob.next_run).limit(20)))
    for jid in ids:
        token = str(uuid4())
        with factory.begin() as db:
            claim = db.execute(update(SyncJob).where(SyncJob.id == jid, *eligible).values(
                status='running', lease_until=clock() + timedelta(seconds=60), lease_token=token,
                attempts=SyncJob.attempts + 1))
            if claim.rowcount != 1:
                continue
        with factory() as db:
            job = db.get(SyncJob, jid)
            ticket = db.get(Ticket, job.ticket_id)
        error = None
        try:
            if job.operation == 'restrict':
                app.state.m1.restrict(ticket)
            else:
                app.state.m1.release(ticket)
        except M1Error as exc:
            error = exc
        with factory.begin() as db:
            # 把最终写入也置于条件 UPDATE 之后，避免过期 worker 覆盖新认领者。
            owned = db.execute(update(SyncJob).where(SyncJob.id == jid, SyncJob.lease_token == token).values(lease_token=None, lease_until=None))
            if owned.rowcount != 1:
                continue
            current = db.get(SyncJob, jid)
            ticket = db.get(Ticket, current.ticket_id)
            if error:
                current.status = 'retry' if error.retryable else 'failed'
                current.last_error = str(error)[:500]
                current.next_run = clock() + timedelta(seconds=min(300, 2 ** min(current.attempts, 8)))
                if current.attempts == 1 or current.status == 'failed':
                    record(db, ticket, 'system', 'sync_failed', current.last_error, clock())
            else:
                current.status, current.last_error = 'done', ''
                ticket.state = 'repairing' if current.operation == 'restrict' else 'closed'
                record(db, ticket, 'system', 'sync_succeeded',
                       '维修限制已生效，开始维修' if current.operation == 'restrict' else '验收通过，M3 限制已解除', clock())


def escalate_once(app):
    t = app.state.clock()
    with app.state.sessions() as db:
        ids = list(db.scalars(select(Ticket.id).where(Ticket.state != 'closed', Ticket.escalated.is_(False), Ticket.due <= t)))
    for tid in ids:
        try:
            with app.state.sessions.begin() as db:
                ticket = db.get(Ticket, tid)
                if ticket.escalated or ticket.state == 'closed' or ticket.due > t:
                    continue
                ticket.escalated = True
                # 乐观锁先于事件写入，保证多个 worker 只升级一次。
                db.flush()
                record(db, ticket, 'system', 'escalated', '处理超时，已升级通知管理员', t)
        except StaleDataError:
            continue


def tick(app):
    sync_once(app)
    escalate_once(app)
