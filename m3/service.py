"""工单变更、历史、通知及审计在同一个事务中提交。"""
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select

from m3.models import History, SyncJob, Ticket
from shared.events import admins, audit, notify


def ticket_or_404(db, tid):
    ticket = db.get(Ticket, tid)
    if not ticket:
        raise HTTPException(404, '工单不存在')
    return ticket


def can_view(ticket, user):
    return user.role == 'admin' or ticket.reporter_id == user.id or ticket.assignee_id == user.id


def visible_ticket(db, tid, user):
    ticket = ticket_or_404(db, tid)
    if not can_view(ticket, user):
        raise HTTPException(404, '工单不存在')
    return ticket


def expect(ticket, *states):
    if ticket.state not in states:
        raise HTTPException(409, '当前工单状态不允许该操作')


def record(db, ticket, actor, action, note, t):
    ticket.updated = t
    db.add(History(ticket_id=ticket.id, actor=actor, action=action, note=note, created=t))
    audit(db, actor, action, ticket.id, t)
    notify(db, [ticket.reporter_id, ticket.assignee_id, *admins(db)],
           f'工单 {ticket.id[:8]} · {ticket.room_name}：{note}', t)


def queue(db, ticket, operation, t):
    db.add(SyncJob(id=str(uuid4()), ticket_id=ticket.id, operation=operation,
                   status='pending', attempts=0, next_run=t, created=t, last_error=''))


def ticket_view(db, ticket, detail=False):
    result = {k: getattr(ticket, k) for k in (
        'id', 'reporter_id', 'room_id', 'room_name', 'description', 'state', 'assignee_id',
        'created', 'updated', 'due', 'escalated', 'maintenance_start', 'repair_note',
        'acceptance_note', 'rating', 'review', 'version')}
    jobs = db.scalars(select(SyncJob).where(SyncJob.ticket_id == ticket.id).order_by(SyncJob.created, SyncJob.id))
    result['sync_jobs'] = [{k: getattr(j, k) for k in ('id', 'operation', 'status', 'attempts', 'next_run', 'last_error')} for j in jobs]
    if detail:
        result['history'] = [{k: getattr(h, k) for k in ('id', 'actor', 'action', 'note', 'created')}
                             for h in db.scalars(select(History).where(History.ticket_id == ticket.id).order_by(History.id))]
    return result
