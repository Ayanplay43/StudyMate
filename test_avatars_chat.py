"""Run from project directory. Tests only a disposable copy of the user's DB."""
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import ThreadingHTTPServer
from urllib import request,error
from http.cookiejar import CookieJar
import base64,json,shutil,sqlite3,threading
import server

with TemporaryDirectory() as tmp:
    path=Path(tmp)/'test.sqlite'
    shutil.copy2(server.DB_PATH,path)
    server.DB_PATH=path
    server.migrate_database()
    with sqlite3.connect(path) as conn:
        for i in (1,2,3):
            conn.execute('UPDATE users SET password_hash=?,is_blocked=0,verified_at=COALESCE(verified_at,CURRENT_TIMESTAMP) WHERE id=?',(server.hash_password('TestingOnly4321'),i))
        conn.execute("INSERT INTO posts(author_id,subject_id,category,intent,title,description,meeting_format,max_partners,deadline) VALUES (1,1,'project','seek','Group chat test','A disposable test group for messages','online',3,'2099-01-01')")
        post=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    srv=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{srv.server_port}'
    def client():
        op=request.build_opener(request.HTTPCookieProcessor(CookieJar()))
        def send(path,method='GET',data=None,csrf=None):
            body=json.dumps(data).encode() if data is not None else None
            headers={'Content-Type':'application/json'} if data is not None else {}
            if csrf:headers['X-CSRF-Token']=csrf
            req=request.Request(base+path,method=method,data=body,headers=headers)
            try:
                with op.open(req,timeout=7) as resp:
                    content=resp.read();return resp.status,json.loads(content) if resp.headers.get_content_type()=='application/json' else content
            except error.HTTPError as exc:
                return exc.code,json.loads(exc.read())
        return send
    try:
        a,b,c=client(),client(),client()
        def login(client,uid):
            status,d=client('/api/login','POST',{'email':f'studymate-seed-{uid:03d}@astanait.edu.kz','password':'TestingOnly4321'})
            assert status==200,(status,d)
            return d['csrf']
        at,bt,ct=login(a,1),login(b,2),login(c,3)
        # Never accept a message from an unapproved applicant or stranger.
        assert b(f'/api/posts/{post}/messages')[0]==403
        assert b(f'/api/posts/{post}/messages','POST',{'body':'not allowed'},bt)[0]==403
        assert c(f'/api/posts/{post}/messages')[0]==403
        assert b(f'/api/posts/{post}/apply','POST',{'message':'I would like to join'},bt)[0]==201
        assert b(f'/api/posts/{post}/messages')[0]==403
        aid=next(x['id'] for x in a('/api/applications?scope=received')[1]['applications'] if x['post_id']==post)
        assert a(f'/api/applications/{aid}/decision','POST',{'status':'accepted'},at)[0]==200
        assert b(f'/api/posts/{post}/messages','POST',{'body':'Hello team!'},bt)[0]==201
        assert a(f'/api/posts/{post}/messages')[1]['messages'][-1]['body']=='Hello team!'
        assert c(f'/api/posts/{post}/messages')[0]==403
        assert c(f'/api/posts/{post}/messages','POST',{'body':'spy'},ct)[0]==403
        assert a(f'/api/posts/{post}/messages','POST',{'body':'Nice to meet you!'},at)[0]==201
        assert len(b(f'/api/posts/{post}/messages')[1]['messages'])==2
        # A transparent, valid 1px PNG.
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==')
        photo='data:image/png;base64,'+base64.b64encode(png).decode()
        assert a('/api/avatar','POST',{'image':photo},at)[0]==200
        assert a('/api/me')[1]['user']['has_avatar']==1
        status,received=b('/api/avatar/1')
        assert status==200 and received==png
        assert a('/api/avatar','POST',{'image':'data:image/svg+xml;base64,AAAA'},at)[0]==400
        assert a('/api/avatar','POST',{'image':None},at)[0]==200
        assert a('/api/me')[1]['user']['has_avatar']==0
        assert b('/api/avatar/1')[0]==404
        print('PASS: avatar add/remove; group chat after acceptance; outsiders blocked; no data deleted')
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=2)
