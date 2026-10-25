from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, or_, select, update

from m3.m1_client import M1Error
from m3.models import SyncJob, Ticket
from m3.service import expect, queue, record, ticket_or_404, ticket_view, visible_ticket
from shared.models import User
from shared.routes import Input
from shared.security import current_user, require

router = APIRouter(prefix='/api/m3', tags=['物业报修'])


class CreateTicket(Input):
    room_id: int = Field(gt=0)
    description: str = Field(min_length=5, max_length=2000)


class Assign(Input):
    assignee_id: str = Field(min_length=1, max_length=36)


class Note(Input):
    note: str = Field(min_length=2, max_length=2000)


class Acceptance(Note):
    approved: bool


class Review(Input):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default='', max_length=1000)


@router.get('/rooms')
def rooms(request: Request, user=Depends(current_user)):
    try:
        return request.app.state.m1.rooms()
    except M1Error as exc:
        raise HTTPException(503, str(exc))


@router.post('/tickets', status_code=201)
def create(p: CreateTicket, request: Request, user=Depends(require('tickets:create'))):
    try:
        room = next((r for r in request.app.state.m1.rooms() if r['id'] == p.room_id), None)
    except M1Error as exc:
        raise HTTPException(503, str(exc))
    if not room:
        raise HTTPException(404, '教室不存在')
    if not room['active']:
        raise HTTPException(409, '教室已停用，请联系管理员')
    t = request.app.state.clock()
    with request.app.state.sessions.begin() as db:
        ticket = Ticket(id=str(uuid4()), reporter_id=user.id, room_id=p.room_id,
                        room_name=room['name'], description=p.description, state='pending',
                        created=t, updated=t, due=t + timedelta(hours=request.app.state.settings.sla_hours))
        db.add(ticket)
        db.flush()
        record(db, ticket, user.id, 'created', '报修已提交，等待派单', t)
        db.flush()
        return ticket_view(db, ticket)


@router.get('/tickets')
def listing(request: Request, user=Depends(current_user), state: str | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    query = select(Ticket)
    if user.role != 'admin':
        query = query.where(or_(Ticket.reporter_id == user.id, Ticket.assignee_id == user.id))
    if state:
        query = query.where(Ticket.state == state)
    with request.app.state.sessions() as db:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        items = [ticket_view(db, t) for t in db.scalars(query.order_by(Ticket.created.desc(), Ticket.id).offset(offset).limit(limit))]
        return {'items': items, 'total': total, 'offset': offset, 'limit': limit}


@router.get('/tickets/{tid}')
def detail(tid: str, request: Request, user=Depends(current_user)):
    with request.app.state.sessions() as db:
        return ticket_view(db, visible_ticket(db, tid, user), detail=True)


@router.post('/tickets/{tid}/assign', status_code=202)
def assign(tid: str, p: Assign, request: Request, user=Depends(require('tickets:assign'))):
    with request.app.state.sessions.begin() as db:
        ticket = ticket_or_404(db, tid)
        expect(ticket, 'pending', 'repairing')
        worker = db.get(User, p.assignee_id)
        if not worker or not worker.active or worker.role != 'technician':
            raise HTTPException(422, '只能指派启用的维修人员')
        ticket.assignee_id = worker.id
        if ticket.state == 'pending':
            ticket.state, ticket.maintenance_start = 'starting', request.app.state.clock()
            queue(db, ticket, 'restrict', request.app.state.clock())
        record(db, ticket, user.id, 'assigned', f'已指派给 {worker.display_name}', request.app.state.clock())
        db.flush()
        return ticket_view(db, ticket)


@router.post('/tickets/{tid}/complete')
def complete(tid: str, p: Note, request: Request, user=Depends(require('tickets:repair'))):
    with request.app.state.sessions.begin() as db:
        ticket = visible_ticket(db, tid, user)
        if ticket.assignee_id != user.id:
            raise HTTPException(403, '只能处理指派给自己的工单')
        expect(ticket, 'repairing')
        ticket.state, ticket.repair_note = 'awaiting_acceptance', p.note
        record(db, ticket, user.id, 'completed', '维修已完成，等待管理员验收：' + p.note, request.app.state.clock())
        db.flush()
        return ticket_view(db, ticket)


@router.post('/tickets/{tid}/accept')
def accept(tid: str, p: Acceptance, request: Request, user=Depends(require('tickets:accept'))):
    with request.app.state.sessions.begin() as db:
        ticket = ticket_or_404(db, tid)
        expect(ticket, 'awaiting_acceptance')
        ticket.acceptance_note = p.note
        ticket.state = 'closing' if p.approved else 'repairing'
        if p.approved:
            queue(db, ticket, 'release', request.app.state.clock())
        record(db, ticket, user.id, 'accepted' if p.approved else 'rejected',
               ('验收通过，等待解除维修限制：' if p.approved else '验收驳回，继续维修：') + p.note, request.app.state.clock())
        db.flush()
        return ticket_view(db, ticket)


@router.post('/tickets/{tid}/review')
def review(tid: str, p: Review, request: Request, user=Depends(current_user)):
    with request.app.state.sessions.begin() as db:
        ticket = visible_ticket(db, tid, user)
        if ticket.reporter_id != user.id:
            raise HTTPException(403, '只有报修人可以评价')
        expect(ticket, 'closed')
        if ticket.rating is not None:
            raise HTTPException(409, '该工单已评价')
        ticket.rating, ticket.review = p.rating, p.comment
        record(db, ticket, user.id, 'reviewed', f'报修人已评价：{p.rating} 星', request.app.state.clock())
        db.flush()
        return ticket_view(db, ticket)


@router.post('/tickets/{tid}/retry', status_code=202)
def retry(tid: str, request: Request, user=Depends(require('tickets:retry'))):
    with request.app.state.sessions.begin() as db:
        ticket = ticket_or_404(db, tid)
        expect(ticket, 'starting', 'closing')
        job = db.scalar(select(SyncJob).where(SyncJob.ticket_id == tid, SyncJob.status.in_(['retry', 'failed'])))
        if not job:
            raise HTTPException(409, '没有可重试的失败任务，或任务正在执行')
        changed = db.execute(update(SyncJob).where(SyncJob.id == job.id,
            SyncJob.status.in_(['retry', 'failed']), SyncJob.lease_token.is_(None)).values(
                status='pending', next_run=request.app.state.clock()))
        if changed.rowcount != 1:
            raise HTTPException(409, '任务正在执行，请稍后刷新')
        record(db, ticket, user.id, 'retry_requested', '管理员请求重试 M1 同步', request.app.state.clock())
        db.flush()
        return ticket_view(db, ticket)
