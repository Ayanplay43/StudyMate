"""Safe local integration test: uses a temporary copy of studymate.sqlite."""
import contextlib
from http.server import ThreadingHTTPServer
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import shutil
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
import server

with TemporaryDirectory() as d:
    testdb=Path(d)/'studymate.sqlite'
    shutil.copy2(server.DB_PATH,testdb)
    server.DB_PATH=testdb
    server.migrate_database()
    with sqlite3.connect(testdb) as c:
        assert c.execute('SELECT COUNT(*) FROM posts WHERE deadline IS NOT NULL').fetchone()[0] >= 120, 'Demo posts need deadlines'
        # Re-running migration must not change already populated demo deadlines.
        before = c.execute('SELECT deadline FROM posts WHERE id=1').fetchone()[0]
        server.migrate_database()
        assert c.execute('SELECT deadline FROM posts WHERE id=1').fetchone()[0] == before
    with sqlite3.connect(testdb) as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=1",(server.hash_password('TestPassword909'),))
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    t=threading.Thread(target=httpd.serve_forever,daemon=True);t.start()
    base=f'http://127.0.0.1:{httpd.server_port}'
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    def request(path,method='GET',payload=None,csrf=None):
        body=json.dumps(payload).encode() if payload is not None else None
        headers={'Content-Type':'application/json'} if payload is not None else {}
        if csrf is not None:headers['X-CSRF-Token']=csrf
        r=urllib.request.Request(base+path,data=body,method=method,headers=headers)
        try:
            with opener.open(r,timeout=5) as resp:return resp.status,json.loads(resp.read())
        except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
    try:
        assert request('/api/posts')[0]==401
        status,d=request('/api/login','POST',{'email':'studymate-seed-001@astanait.edu.kz','password':'TestPassword909'})
        assert status==200,(status,d)
        csrf=d['csrf']
        status,d=request('/api/analytics')
        assert status==200 and len(d['subjects'])==8 and sum(x['count'] for x in d['categories'])==120,(status,d)
        status,all_posts=request('/api/posts')
        assert status==200 and len(all_posts['posts'])>0
        close_date=(datetime.now(timezone.utc).date()+timedelta(days=7)).isoformat()
        status,soon_posts=request('/api/posts?deadline='+close_date)
        assert status==200 and 0<len(soon_posts['posts'])<len(all_posts['posts']), (len(soon_posts['posts']),len(all_posts['posts']))
        assert all(p['deadline'] and p['deadline']<=close_date for p in soon_posts['posts'])
        deadline=(datetime.now(timezone.utc).date()+timedelta(days=7)).isoformat()
        post={'title':'Endterm system test','description':'Test deadline and database persistence for StudyMate.','subject_id':1,'category':'project','intent':'seek','meeting_format':'online','max_partners':2,'deadline':deadline}
        assert request('/api/posts','POST',post)[0]==403,'Missing CSRF must fail'
        status,d=request('/api/posts','POST',post,csrf)
        assert status==201,(status,d)
        post_id=d['post_id']
        with sqlite3.connect(testdb) as c:
            assert c.execute('select deadline from posts where id=?',(post_id,)).fetchone()[0]==deadline
        status,d=request('/api/posts?deadline='+deadline)
        assert status==200 and any(x['id']==post_id for x in d['posts']),(status,d)
        status,d=request('/api/posts?deadline=2000-01-01')
        assert status==200 and not d['posts']
        assert request('/api/posts','POST',{**post,'deadline':'2020-01-01'},csrf)[0]==400
        # The author can change an existing deadline, other users cannot.
        new_deadline=(datetime.now(timezone.utc).date()+timedelta(days=15)).isoformat()
        endpoint='/api/posts/'+str(post_id)+'/deadline'
        assert request(endpoint,'POST',{'deadline':new_deadline})[0]==403,'CSRF required for edits'
        assert request(endpoint,'POST',{'deadline':'2020-01-01'},csrf)[0]==400,'Past due dates rejected'
        status,d=request(endpoint,'POST',{'deadline':new_deadline},csrf)
        assert status==200 and d['deadline']==new_deadline,(status,d)
        with sqlite3.connect(testdb) as c:
            assert c.execute('SELECT deadline FROM posts WHERE id=?',(post_id,)).fetchone()[0]==new_deadline
        status,d=request('/api/posts?deadline='+deadline)
        assert status==200 and not any(x['id']==post_id for x in d['posts']), 'Changed post must leave earlier Due by filter'
        status,d=request('/api/posts?deadline='+new_deadline)
        assert status==200 and any(x['id']==post_id for x in d['posts']), 'Changed post must appear with new Due by filter'
        # Sign in as a different user and confirm author checks.
        other=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        other_body=json.dumps({'email':'studymate-seed-002@astanait.edu.kz','password':'TestPassword910'}).encode()
        with sqlite3.connect(testdb) as c:
            c.execute('UPDATE users SET password_hash=? WHERE id=2',(server.hash_password('TestPassword910'),))
        with other.open(urllib.request.Request(base+'/api/login',data=other_body,method='POST',headers={'Content-Type':'application/json'}),timeout=5) as resp:
            other_csrf=json.loads(resp.read())['csrf']
        wrong_owner_req=urllib.request.Request(base+endpoint,data=json.dumps({'deadline':deadline}).encode(),method='POST',headers={'Content-Type':'application/json','X-CSRF-Token':other_csrf})
        try:
            other.open(wrong_owner_req,timeout=5)
            raise AssertionError('Different user must not edit the post')
        except urllib.error.HTTPError as exc:
            assert exc.code==404,exc.code
        status,d=request('/api/posts/'+str(post_id)+'/close','POST',{},csrf)
        assert status==200
        assert request(endpoint,'POST',{'deadline':deadline},csrf)[0]==404,'Closed posts cannot be edited'
        print('PASS: demo deadlines, Due by filters, login, CSRF, analytics, create, owner-only deadline edits, persistence, rejection of past/closed dates, close')
    finally:
        httpd.shutdown();httpd.server_close();t.join(timeout=2)
