from uuid import uuid4
from sqlalchemy import select
from shared.models import Audit, Notification, User


def audit(db, actor, action, target, created, detail='', source='M3', event_id=None):
    db.add(Audit(actor=actor, action=action, target=target, detail=detail,
                 source=source, event_id=event_id or str(uuid4()), created=created))


def notify(db, users, text, created, event_id=None):
    key = event_id or str(uuid4())
    for uid in set(users) - {None}:
        db.add(Notification(user_id=uid, source='M3', event_id=key, text=text, created=created))


def admins(db):
    return list(db.scalars(select(User.id).where(User.role == 'admin', User.active.is_(True))))
