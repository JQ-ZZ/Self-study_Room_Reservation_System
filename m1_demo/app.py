"""M1 teaching demo. Local-only demo identities; replace at the shared auth boundary."""
import asyncio
import hashlib
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DB = os.getenv('M1_DB', str(ROOT / 'demo.sqlite3'))
TZ = timezone(timedelta(hours=8))
OFFSET = timedelta()
CODE_KEY = secrets.token_bytes(32)

def now():
    return (datetime.now(TZ) + OFFSET).replace(tzinfo=None, microsecond=0)

def stamp(t):
    return t.isoformat(timespec='seconds')

@contextmanager
def db(write=False):
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        if write:
            c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init():
    with db(True) as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY, name TEXT NOT NULL, area TEXT NOT NULL, opens INTEGER NOT NULL, closes INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS seats(id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES rooms(id), label TEXT NOT NULL, outlet INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(room_id,label));
        CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY, user TEXT NOT NULL, seat_id INTEGER NOT NULL REFERENCES seats(id), start TEXT NOT NULL, end TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'reserved', created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS restrictions(id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL, room_id INTEGER NOT NULL REFERENCES rooms(id), start TEXT NOT NULL, end TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(source,external_id));
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, user TEXT NOT NULL, reservation_id INTEGER NOT NULL REFERENCES reservations(id), kind TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(reservation_id,kind));
        CREATE INDEX IF NOT EXISTS reservation_overlap ON reservations(seat_id,start,end,status);
        PRAGMA user_version=1;
        ''')
        if not c.execute('SELECT 1 FROM rooms').fetchone():
            for rid, name, area in [(1,'明德楼 201','安静区'),(2,'明德楼 202','靠窗区'),(3,'图书馆 301','研读区')]:
                c.execute('INSERT INTO rooms VALUES(?,?,?,?,?,1)',(rid,name,area,8,23))
                for i in range(1,25):
                    c.execute('INSERT INTO seats(room_id,label,outlet) VALUES(?,?,?)',(rid,f'{i:02}',int(i%3!=0)))

def fail(code, text):
    raise HTTPException(code,text)

def identity(x_demo_user: str = Header(default='student-a')):
    if x_demo_user not in ('student-a','student-b','admin'):
        fail(401,'未知演示身份')
    return x_demo_user

def admin(user):
    if identity(user) != 'admin':
        fail(403,'需要管理员身份')

def interval(start, end):
    try:
        a,b = datetime.fromisoformat(start),datetime.fromisoformat(end)
        if a.tzinfo or b.tzinfo:
            fail(422,'请使用北京时间，不携带时区后缀')
        if a >= b:
            fail(422,'结束时间必须晚于开始时间')
        return stamp(a),stamp(b),a,b
    except ValueError:
        fail(422,'时间格式错误')

def sources(c, seat, start, end):
    result=[]
    if not seat['active'] or not seat['room_active']:
        result.append('管理员停用')
    result += [r['source']+':'+r['external_id'] for r in c.execute('SELECT * FROM restrictions WHERE room_id=? AND active=1 AND start<? AND end>?',(seat['room_id'],end,start))]
    if c.execute("SELECT 1 FROM reservations WHERE seat_id=? AND status IN ('reserved','checked_in') AND start<? AND end>?",(seat['id'],end,start)).fetchone():
        result.append('M1:个人预约')
    return result

def seatrow(c,sid):
    s=c.execute('SELECT s.*,r.active room_active,r.name room,r.area,r.opens,r.closes FROM seats s JOIN rooms r ON s.room_id=r.id WHERE s.id=?',(sid,)).fetchone()
    if s is None: fail(404,'座位不存在')
    return s

def message(c,r,kind,text):
    c.execute('INSERT OR IGNORE INTO messages(user,reservation_id,kind,text,created) VALUES(?,?,?,?,?)',(r['user'],r['id'],kind,text,stamp(now())))

def tick():
    t=now()
    with db(True) as c:
        for r in c.execute("SELECT * FROM reservations WHERE status IN ('reserved','checked_in')").fetchall():
            a,b=datetime.fromisoformat(r['start']),datetime.fromisoformat(r['end'])
            if r['status']=='reserved' and t>=a+timedelta(minutes=15):
                c.execute("UPDATE reservations SET status='violated' WHERE id=?",(r['id'],))
                message(c,r,'violation','未在开始后15分钟内签到，预约已取消并记违约，座位已释放。')
            elif t>=b:
                c.execute("UPDATE reservations SET status='completed' WHERE id=?",(r['id'],))
            elif r['status']=='reserved':
                if a-timedelta(minutes=15)<=t<a:
                    message(c,r,'before','距离预约开始不足15分钟，请按时到场。')
                if a+timedelta(minutes=10)<=t<a+timedelta(minutes=15):
                    message(c,r,'late','预约已开始10分钟，请尽快签到。15分钟未签到将记违约。')

async def scheduler():
    while True:
        await asyncio.to_thread(tick)
        await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app):
    init()
    task=asyncio.create_task(scheduler())
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass

app=FastAPI(title='M1 自习座位预约 Demo',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

@app.get('/')
def page(): return FileResponse(ROOT/'static/index.html')

@app.get('/api/meta')
def meta():
    with db() as c:
        return {'now':stamp(now()),'rooms':[dict(r) for r in c.execute('SELECT * FROM rooms')], 'demo':True}

@app.get('/api/seats')
def seats(start:str,end:str,room_id:int=0,outlet:bool=False):
    start,end,a,b=interval(start,end)
    with db() as c:
        rows=c.execute('SELECT s.*,r.active room_active,r.name room,r.area,r.opens,r.closes FROM seats s JOIN rooms r ON s.room_id=r.id WHERE (?=0 OR r.id=?) AND (?=0 OR s.outlet=1)',(room_id,room_id,int(outlet))).fetchall()
        result=[]
        for s in rows:
            reasons=sources(c,s,start,end)
            if a.date()!=b.date() or a.hour<s['opens'] or b.hour>s['closes'] or (b.hour==s['closes'] and (b.minute or b.second)):
                reasons.append('非开放时间')
            result.append({**dict(s),'sources':reasons,'available':not reasons})
        return result

class Booking(BaseModel):
    seat_id:int
    start:str
    end:str

@app.post('/api/reservations',status_code=201)
def book(p:Booking,x_demo_user:str=Header(default='student-a')):
    user=identity(x_demo_user)
    start,end,a,b=interval(p.start,p.end)
    if any((a.minute,a.second,a.microsecond,b.minute,b.second,b.microsecond)) or b-a>timedelta(hours=4):
        fail(422,'仅支持整点时段，最多连续4小时')
    if a<now(): fail(422,'不能预约过去的时间')
    with db(True) as c:
        s=seatrow(c,p.seat_id)
        if a.date()!=b.date() or a.hour<s['opens'] or b.hour>s['closes']:
            fail(422,'预约超出教室开放时间')
        reasons=sources(c,s,start,end)
        if reasons: fail(409,'座位不可预约：'+'、'.join(reasons))
        if c.execute("SELECT 1 FROM reservations WHERE user=? AND status IN ('reserved','checked_in') AND start<? AND end>?",(user,end,start)).fetchone():
            fail(409,'你在同一时段已有预约')
        cur=c.execute('INSERT INTO reservations(user,seat_id,start,end,created) VALUES(?,?,?,?,?)',(user,p.seat_id,start,end,stamp(now())))
        return {'id':cur.lastrowid,'message':'预约成功'}

@app.get('/api/reservations')
def bookings(x_demo_user:str=Header(default='student-a')):
    user=identity(x_demo_user)
    with db() as c:
        return [dict(r) for r in c.execute('SELECT b.*,s.label,r.name room,r.id room_id FROM reservations b JOIN seats s ON b.seat_id=s.id JOIN rooms r ON s.room_id=r.id WHERE user=? ORDER BY b.id DESC',(user,))]

@app.post('/api/reservations/{rid}/cancel')
def cancel(rid:int,x_demo_user:str=Header(default='student-a')):
    user=identity(x_demo_user)
    with db(True) as c:
        r=c.execute('SELECT * FROM reservations WHERE id=? AND user=?',(rid,user)).fetchone()
        if not r: fail(404,'预约不存在')
        if r['status']!='reserved': fail(409,'当前状态不能取消')
        c.execute("UPDATE reservations SET status='cancelled' WHERE id=?",(rid,))
    return {'message':'已取消预约'}

def daily_code(room_id):
    return hashlib.sha256(CODE_KEY+f'{room_id}:{now().date()}'.encode()).hexdigest()[:6].upper()

@app.get('/api/admin/rooms/{room_id}/code')
def code(room_id:int,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    with db() as c:
        if not c.execute('SELECT 1 FROM rooms WHERE id=?',(room_id,)).fetchone(): fail(404,'教室不存在')
    return {'code':daily_code(room_id),'date':str(now().date())}

class Checkin(BaseModel): code:str

@app.post('/api/reservations/{rid}/checkin')
def checkin(rid:int,p:Checkin,x_demo_user:str=Header(default='student-a')):
    user=identity(x_demo_user)
    with db(True) as c:
        r=c.execute('SELECT b.*,s.room_id FROM reservations b JOIN seats s ON b.seat_id=s.id WHERE b.id=? AND b.user=?',(rid,user)).fetchone()
        if not r: fail(404,'预约不存在')
        if r['status']!='reserved': fail(409,'当前状态不能签到')
        a=datetime.fromisoformat(r['start'])
        if not a-timedelta(minutes=15)<=now()<a+timedelta(minutes=15): fail(409,'签到窗口为开始前15分钟至开始后15分钟（不含）')
        if not secrets.compare_digest(p.code.upper(),daily_code(r['room_id'])): fail(422,'签到码不正确')
        c.execute("UPDATE reservations SET status='checked_in' WHERE id=?",(rid,))
    return {'message':'签到成功，祝学习顺利'}

@app.get('/api/messages')
def messages(x_demo_user:str=Header(default='student-a')):
    user=identity(x_demo_user)
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM messages WHERE user=? ORDER BY id DESC',(user,))]

class Restriction(BaseModel):
    source:str=Field(pattern='^(M2|M3)$')
    external_id:str=Field(min_length=1,max_length=80)
    room_id:int
    start:str
    end:str

@app.post('/api/integrations/restrictions')
def restrict(p:Restriction,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    start,end,_,_=interval(p.start,p.end)
    with db(True) as c:
        if not c.execute('SELECT 1 FROM rooms WHERE id=?',(p.room_id,)).fetchone(): fail(404,'教室不存在')
        old=c.execute('SELECT * FROM restrictions WHERE source=? AND external_id=?',(p.source,p.external_id)).fetchone()
        if old:
            if (old['room_id'],old['start'],old['end'])!=(p.room_id,start,end): fail(409,'同一来源业务编号的参数不可更改')
            return dict(old)
        if c.execute("SELECT 1 FROM reservations b JOIN seats s ON b.seat_id=s.id WHERE s.room_id=? AND b.status IN ('reserved','checked_in') AND b.start<? AND b.end>?",(p.room_id,end,start)).fetchone():
            fail(409,'该时段已有个人预约，需先协调取消')
        cur=c.execute('INSERT INTO restrictions(source,external_id,room_id,start,end) VALUES(?,?,?,?,?)',(p.source,p.external_id,p.room_id,start,end))
        return {'id':cur.lastrowid,'message':'限制已生效'}

@app.delete('/api/integrations/restrictions/{source}/{external_id}')
def release(source:str,external_id:str,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    with db(True) as c:
        c.execute('UPDATE restrictions SET active=0 WHERE source=? AND external_id=?',(source,external_id))
    return {'message':'已解除指定来源限制，其他限制继续有效'}

@app.get('/api/admin/overview')
def overview(x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    with db() as c:
        return {'total':c.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],
                'violations':c.execute("SELECT COUNT(*) FROM reservations WHERE status='violated'").fetchone()[0],
                'checked_in':c.execute("SELECT COUNT(*) FROM reservations WHERE status='checked_in'").fetchone()[0],
                'restrictions':[dict(r) for r in c.execute('SELECT q.*,r.name room FROM restrictions q JOIN rooms r ON q.room_id=r.id ORDER BY q.id DESC')],
                'usage':[dict(r) for r in c.execute("SELECT r.name,COUNT(b.id) bookings,SUM(CASE WHEN b.status='checked_in' THEN 1 ELSE 0 END) checked_in FROM rooms r LEFT JOIN seats s ON s.room_id=r.id LEFT JOIN reservations b ON b.seat_id=s.id GROUP BY r.id")],
                'seats':[dict(r) for r in c.execute('SELECT * FROM seats')]}

class Room(BaseModel):
    name:str=Field(min_length=1,max_length=60)
    area:str=Field(min_length=1,max_length=30)
    opens:int=Field(ge=0,le=23)
    closes:int=Field(ge=1,le=24)
    active:bool=True

@app.post('/api/admin/rooms')
def add_room(p:Room,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    if p.opens>=p.closes: fail(422,'开放时间范围错误')
    with db(True) as c:
        cur=c.execute('INSERT INTO rooms(name,area,opens,closes,active) VALUES(?,?,?,?,?)',(p.name,p.area,p.opens,p.closes,int(p.active)))
        return {'id':cur.lastrowid}

@app.put('/api/admin/rooms/{rid}')
def edit_room(rid:int,p:Room,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    if p.opens>=p.closes: fail(422,'开放时间范围错误')
    with db(True) as c:
        if not c.execute('SELECT 1 FROM rooms WHERE id=?',(rid,)).fetchone(): fail(404,'教室不存在')
        c.execute('UPDATE rooms SET name=?,area=?,opens=?,closes=?,active=? WHERE id=?',(p.name,p.area,p.opens,p.closes,int(p.active),rid))
    return {'message':'教室已更新'}

class Seat(BaseModel):
    room_id:int
    label:str=Field(min_length=1,max_length=12)
    outlet:bool=True
    active:bool=True

@app.post('/api/admin/seats')
def add_seat(p:Seat,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    with db(True) as c:
        if not c.execute('SELECT 1 FROM rooms WHERE id=?',(p.room_id,)).fetchone(): fail(404,'教室不存在')
        try:
            cur=c.execute('INSERT INTO seats(room_id,label,outlet,active) VALUES(?,?,?,?)',(p.room_id,p.label,int(p.outlet),int(p.active)))
        except sqlite3.IntegrityError: fail(409,'座位编号已存在')
        return {'id':cur.lastrowid}

@app.put('/api/admin/seats/{sid}')
def edit_seat(sid:int,p:Seat,x_demo_user:str=Header(default='')):
    admin(x_demo_user)
    with db(True) as c:
        seatrow(c,sid)
        if not c.execute('SELECT 1 FROM rooms WHERE id=?',(p.room_id,)).fetchone(): fail(404,'教室不存在')
        try: c.execute('UPDATE seats SET room_id=?,label=?,outlet=?,active=? WHERE id=?',(p.room_id,p.label,int(p.outlet),int(p.active),sid))
        except sqlite3.IntegrityError: fail(409,'座位编号已存在')
    return {'message':'座位已更新'}

class Advance(BaseModel): minutes:int=Field(ge=0,le=10080)

@app.post('/api/demo/advance')
def advance(p:Advance,x_demo_user:str=Header(default='')):
    global OFFSET
    admin(x_demo_user)
    OFFSET+=timedelta(minutes=p.minutes)
    tick()
    return {'now':stamp(now())}
