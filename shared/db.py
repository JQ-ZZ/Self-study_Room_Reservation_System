from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def now():
    """与 M1 契约一致：北京时间、不携带时区后缀，精确到秒。"""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None, microsecond=0)


def database(url):
    parsed = make_url(url)
    if parsed.drivername.startswith('sqlite') and parsed.database not in (None, ':memory:'):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 15}
                           if parsed.drivername.startswith('sqlite') else {}, pool_pre_ping=True)
    if parsed.drivername.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def sqlite_setup(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=15000')
    return engine, sessionmaker(engine, expire_on_commit=False)
