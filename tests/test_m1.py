import concurrent.futures
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from m1_demo import app as m

ADMIN={'X-Demo-User':'admin'}
B={'X-Demo-User':'student-b'}

class M1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        m.DB=str(Path(self.tmp.name)/'test.sqlite3')
        self.oldnow=m.now
        self.time=datetime(2026,10,21,8,40)
        m.now=lambda:self.time
        self.client=TestClient(m.app)
        self.client.__enter__()
        self.p={'seat_id':1,'start':'2026-10-21T09:00:00','end':'2026-10-21T11:00:00'}
    def tearDown(self):
        self.client.__exit__(None,None,None)
        m.now=self.oldnow
        self.tmp.cleanup()
    def test_concurrent_seat_conflict(self):
        def reserve(user):
            return self.client.post('/api/reservations',json=self.p,headers={'X-Demo-User':user}).status_code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            codes=list(pool.map(reserve,['student-a','student-b']))
        self.assertEqual(sorted(codes),[201,409])
        with m.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],1)
    def test_time_rules_and_filter(self):
        for start,end in [('09:30:00','10:00:00'),('09:00:00','14:00:00'),('07:00:00','08:00:00')]:
            r=self.client.post('/api/reservations',json={**self.p,'start':'2026-10-21T'+start,'end':'2026-10-21T'+end})
            self.assertEqual(r.status_code,422,r.text)
        seats=self.client.get('/api/seats',params={**{k:v for k,v in self.p.items() if k!='seat_id'},'room_id':2,'outlet':True}).json()
        self.assertEqual(len(seats),16)
        self.assertTrue(all(s['outlet'] and s['room_id']==2 for s in seats))
    def test_checkin_code_ownership_and_window(self):
        rid=self.client.post('/api/reservations',json=self.p).json()['id']
        code=self.client.get('/api/admin/rooms/1/code',headers=ADMIN).json()['code']
        self.assertEqual(self.client.get('/api/admin/rooms/1/code',headers={'X-Demo-User':'student-a'}).status_code,403)
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/checkin',json={'code':code}).status_code,409)
        self.time=datetime(2026,10,21,8,45)
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/checkin',json={'code':code},headers=B).status_code,404)
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/checkin',json={'code':'WRONG'}).status_code,422)
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/checkin',json={'code':code}).status_code,200)
        self.time=datetime(2026,10,21,9,16);m.tick()
        self.assertEqual(self.client.get('/api/reservations').json()[0]['status'],'checked_in')
        self.time=datetime(2026,10,21,11);m.tick()
        self.assertEqual(self.client.get('/api/reservations').json()[0]['status'],'completed')
    def test_notifications_violation_release(self):
        rid=self.client.post('/api/reservations',json=self.p).json()['id']
        self.time=datetime(2026,10,21,8,45);m.tick();m.tick()
        self.time=datetime(2026,10,21,9,10);m.tick();m.tick()
        self.time=datetime(2026,10,21,9,15)
        code=self.client.get('/api/admin/rooms/1/code',headers=ADMIN).json()['code']
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/checkin',json={'code':code}).status_code,409)
        m.tick();m.tick()
        self.assertEqual([x['kind'] for x in self.client.get('/api/messages').json()],['violation','late','before'])
        self.assertEqual(self.client.get('/api/reservations').json()[0]['status'],'violated')
        with m.db() as c:self.assertEqual(m.sources(c,m.seatrow(c,1),self.p['start'],self.p['end']),[])
    def test_background_scheduler_without_refresh(self):
        self.client.post('/api/reservations',json=self.p)
        self.time=datetime(2026,10,21,9,16)
        time.sleep(2.5)
        with m.db() as c:
            self.assertEqual(c.execute('SELECT status FROM reservations').fetchone()[0],'violated')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE kind='violation'").fetchone()[0],1)
    def test_external_sources_idempotency_release(self):
        p={'room_id':1,'start':self.p['start'],'end':self.p['end'],'external_id':'001'}
        for source in ['M2','M3']:
            self.assertEqual(self.client.post('/api/integrations/restrictions',headers=ADMIN,json={**p,'source':source}).status_code,200)
        self.client.post('/api/integrations/restrictions',headers=ADMIN,json={**p,'source':'M2'})
        with m.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM restrictions').fetchone()[0],2)
        self.assertEqual(self.client.post('/api/reservations',json=self.p).status_code,409)
        self.client.delete('/api/integrations/restrictions/M3/001',headers=ADMIN)
        with m.db() as c:self.assertEqual(m.sources(c,m.seatrow(c,1),self.p['start'],self.p['end']),['M2:001'])
        self.client.delete('/api/integrations/restrictions/M2/001',headers=ADMIN)
        self.assertEqual(self.client.post('/api/reservations',json=self.p).status_code,201)
        self.assertEqual(self.client.post('/api/integrations/restrictions',headers=ADMIN,json={**p,'source':'M3','external_id':'002'}).status_code,409)
    def test_adjacent_and_user_overlap(self):
        self.assertEqual(self.client.post('/api/reservations',json=self.p).status_code,201)
        self.assertEqual(self.client.post('/api/reservations',json={**self.p,'seat_id':2}).status_code,409)
        self.assertEqual(self.client.post('/api/reservations',json={**self.p,'start':'2026-10-21T11:00:00','end':'2026-10-21T12:00:00'}).status_code,201)
    def test_management_permissions_and_disabled(self):
        p={'room_id':1,'label':'01','outlet':True,'active':False}
        self.assertEqual(self.client.put('/api/admin/seats/1',json=p,headers={'X-Demo-User':'student-a'}).status_code,403)
        self.assertEqual(self.client.put('/api/admin/seats/1',json=p,headers=ADMIN).status_code,200)
        self.assertEqual(self.client.post('/api/reservations',json=self.p).status_code,409)
        r=self.client.post('/api/admin/rooms',headers=ADMIN,json={'name':'测试教室','area':'安静区','opens':8,'closes':22}).json()
        self.assertIn('id',r)
    def test_cancel_ownership(self):
        rid=self.client.post('/api/reservations',json=self.p).json()['id']
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/cancel',headers=B).status_code,404)
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/cancel').status_code,200)
        self.assertEqual(self.client.post('/api/reservations',headers=B,json=self.p).status_code,201)

if __name__=='__main__':unittest.main()
