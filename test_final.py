"""Integration tests using an isolated copy of the demo DB. No real users modified."""
from tempfile import TemporaryDirectory
from pathlib import Path
from http.server import ThreadingHTTPServer
from http.cookiejar import CookieJar
from urllib import request,error
import json,sqlite3,shutil,threading
import server

with TemporaryDirectory() as tmp:
    dbpath=Path(tmp)/'test.sqlite';shutil.copy2(server.DB_PATH,dbpath)
    server.DB_PATH=dbpath
    server.migrate_database();server.migrate_database()
    with sqlite3.connect(dbpath) as conn:
        for i,pwd in [(1,'OwnerPassword908'),(2,'StudentPassword908'),(3,'AdminPassword908')]:
            conn.execute('UPDATE users SET password_hash=? WHERE id=?',(server.hash_password(pwd),i))
        conn.execute("UPDATE users SET role='moderator' WHERE id=3")
        conn.execute("UPDATE users SET telegram='@studymateowner' WHERE id=1")
        conn.execute("UPDATE users SET telegram='@studymatestudent' WHERE id=2")
        # isolated post with author 1
        conn.execute("INSERT INTO posts(author_id,subject_id,category,intent,title,description,meeting_format,max_partners,deadline) VALUES(1,1,'project','seek','Contact privacy test','Need a partner for a real course project','online',3,'2099-01-01')")
        pid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    srv=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
    base=f'http://127.0.0.1:{srv.server_port}'
    def mkuser():
        opener=request.build_opener(request.HTTPCookieProcessor(CookieJar()))
        def req(path,method='GET',payload=None,csrf=None):
            body=json.dumps(payload).encode() if payload is not None else None
            h={'Content-Type':'application/json'} if payload is not None else {}
            if csrf:h['X-CSRF-Token']=csrf
            r=request.Request(base+path,data=body,headers=h,method=method)
            try:
                with opener.open(r,timeout=8) as resp: return resp.status,json.loads(resp.read())
            except error.HTTPError as exc:return exc.code,json.loads(exc.read())
        return req
    try:
        owner,student,mod=mkuser(),mkuser(),mkuser()
        def login(req,id,pwd):
            status,d=req('/api/login','POST',{'email':f'studymate-seed-{id:03d}@astanait.edu.kz','password':pwd})
            assert status==200,(status,d)
            return d['csrf']
        o=login(owner,1,'OwnerPassword908');b=login(student,2,'StudentPassword908');m=login(mod,3,'AdminPassword908')
        # R2: profile all fields persist through reload
        status,d=owner('/api/profile','POST',{'first_name':'Aydana','last_name':'Serikova','course':'SE 1','subjects_text':'Calculus, ICT','preferred_time':'After 17:00','telegram':'@studymateowner'},o)
        assert status==200,(status,d)
        status,d=owner('/api/me')
        assert d['user']['course']=='SE 1' and d['user']['preferred_time']=='After 17:00'
        assert d['user']['telegram']=='@studymateowner'
        # R5 R6: send, verify no contact before acceptance, allow after
        status,d=student(f'/api/posts/{pid}/apply','POST',{'message':'I would like to study with your group.'},b)
        assert status==201,(status,d)
        received=owner('/api/applications?scope=received')[1]['applications']
        app=next(x for x in received if x['post_id']==pid)
        assert app['status']=='pending' and app['contact_telegram'] is None and app['contact_email'] is None
        aid=app['id']
        assert student(f'/api/applications/{aid}/decision','POST',{'status':'accepted'},b)[0]==404
        assert owner(f'/api/applications/{aid}/decision','POST',{'status':'accepted'},o)[0]==200
        received=owner('/api/applications?scope=received')[1]['applications']
        app=next(x for x in received if x['id']==aid)
        assert app['contact_telegram']=='@studymatestudent'
        sent=student('/api/applications?scope=sent')[1]['applications']
        app=next(x for x in sent if x['id']==aid)
        assert app['contact_telegram']=='@studymateowner'
        # R9: only mod can moderate; report, block, remove
        assert student('/api/mod/reports')[0]==403
        assert owner('/api/mod/reports')[0]==403
        assert owner(f'/api/posts/{pid}/report','POST',{'reason':'This is spam content'},o)[0]==400
        assert student(f'/api/posts/{pid}/report','POST',{'reason':'Suspicious or inappropriate study request'},b)[0]==201
        reports=mod('/api/mod/reports')[1]['reports']
        report=next(x for x in reports if x['post_id']==pid)
        assert student(f'/api/mod/reports/{report["id"]}/remove','POST',{},b)[0]==403
        assert mod(f'/api/mod/reports/{report["id"]}/remove','POST',{},m)[0]==200
        assert not any(x['id']==pid for x in owner('/api/posts')[1]['posts'])
        assert mod('/api/mod/users/2/block','POST',{'blocked':True},m)[0]==200
        assert student('/api/me')[1]['user'] is None
        assert student('/api/login','POST',{'email':'studymate-seed-002@astanait.edu.kz','password':'StudentPassword908'})[0]==403
        # health is available, source+static only; database cannot be downloaded
        assert owner('/api/health')[0]==200
        try:
            request.urlopen(base+'/studymate.sqlite',timeout=5)
            raise AssertionError('Database exposed')
        except error.HTTPError as e:
            assert e.code==404
        print('PASS: final R2 R5 R6 R9, access-control, blocked sessions and private DB routes')
    finally:
        srv.shutdown();srv.server_close();t.join(timeout=2)
