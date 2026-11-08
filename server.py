"""StudyMate: offline-capable Python standard-library HTTP + SQLite application.

Run `python init_db.py` once, then `python server.py`.
For a real deployment, set APP_ENV=production, PUBLIC_BASE_URL=https://..., SMTP_*,
and serve only through a TLS reverse proxy. See SECURITY.md.
"""
from __future__ import annotations
import base64
from collections import defaultdict, deque
import hashlib
import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import smtplib
import sqlite3
import sys
import time
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('STUDYMATE_DB', ROOT / 'studymate.sqlite'))
ENV = os.environ.get('APP_ENV', 'development')
HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', 8000))
DOMAIN = 'astanait.edu.kz'
EMAIL_RX = re.compile(r'^[a-z0-9][a-z0-9._+\-]{0,63}@astanait\.edu\.kz$')
NAME_RX = re.compile(r"^[\w \-’'\u0400-\u04ff]{2,50}$", re.UNICODE)
CATEGORIES = {'project', 'group', 'midterm', 'endterm', 'homework', 'study'}
FORMATS = {'online', 'offline', 'either'}
INTENTS = {'seek', 'offer'}
RATE_LOCK = __import__('threading').Lock()
RATE = defaultdict(deque)

if ENV == 'production':
    SECRET = os.environ.get('SESSION_SECRET', '').encode()
    if len(SECRET) < 32:
        raise RuntimeError('Set SESSION_SECRET (>=32 random characters) in production')
    if not os.environ.get('PUBLIC_BASE_URL', '').startswith('https://'):
        raise RuntimeError('Production requires HTTPS PUBLIC_BASE_URL')
    if not os.environ.get('MODERATOR_EMAILS'):
        raise RuntimeError('Production requires MODERATOR_EMAILS server-side admin allowlist')
    if not os.environ.get('SMTP_HOST') or not os.environ.get('SMTP_FROM'):
        raise RuntimeError('Production requires SMTP_HOST and SMTP_FROM for email verification')
else:
    # Persistent across local restarts; do not commit or share this file.
    local_key = ROOT / '.local_session_secret'
    if not local_key.exists():
        local_key.write_text(secrets.token_hex(32), encoding='utf-8')
        try: local_key.chmod(0o600)
        except OSError: pass
    SECRET = local_key.read_text(encoding='utf-8').strip().encode()


# Spread synthetic seed deadlines over six weeks, making the Due by filter
# testable with the demo data. These are illustrative dates, not user deadlines.
DEMO_DEADLINE_OFFSETS = (1, 3, 5, 7, 10, 14, 21, 28, 35, 42)


def migrate_database():
    """Upgrade DB without resetting posts or modifying non-demo user records."""
    with sqlite3.connect(DB_PATH) as conn:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(posts)')}
        if 'deadline' not in columns:
            conn.execute('ALTER TABLE posts ADD COLUMN deadline TEXT')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_posts_deadline ON posts(status, deadline)')
        usercols = {r[1] for r in conn.execute('PRAGMA table_info(users)')}
        if 'avatar_data' not in usercols:
            conn.execute('ALTER TABLE users ADD COLUMN avatar_data TEXT')
        conn.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 1000),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_post_id ON chat_messages(post_id,id)')
        conn.execute('''CREATE TABLE IF NOT EXISTS chat_reads (
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            last_read_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(post_id,user_id))''')
        for field in ('course','subjects_text','preferred_time','telegram'):
            if field not in usercols: conn.execute(f"ALTER TABLE users ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        if 'is_blocked' not in usercols:
            conn.execute('ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0')
        conn.execute('''CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 10 AND 500),
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(reporter_id,post_id))''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status,created_at)')
        audit_schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='audit_logs' AND type='table'").fetchone()
        if audit_schema and "CHECK (event IN" in audit_schema[0] and "'report_post'" not in audit_schema[0]:
            conn.execute('ALTER TABLE audit_logs RENAME TO audit_logs_legacy')
            conn.execute('''CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                event TEXT NOT NULL,
                target_type TEXT, target_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            conn.execute('''INSERT INTO audit_logs(id,actor_id,event,target_type,target_id,created_at)
                SELECT id,actor_id,event,target_type,target_id,created_at FROM audit_logs_legacy''')
            conn.execute('DROP TABLE audit_logs_legacy')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id,created_at)')
        # Admin rights assigned from trusted server configuration, never the browser.
        admins = {email.strip().lower() for email in os.environ.get('MODERATOR_EMAILS','').split(',') if email.strip()}
        if admins:
            conn.executemany("UPDATE users SET role='moderator' WHERE email=? AND verified_at IS NOT NULL",[(x,) for x in admins])
        # Existing installations have 120 synthetic posts created by init_db.py.
        # Only those exact seed records are eligible for a demonstration deadline.
        # User-created posts, including legacy posts with NULL deadlines, are untouched.
        rows = conn.execute("""SELECT p.id, p.description, u.email
                  FROM posts p JOIN users u ON u.id=p.author_id
                  WHERE p.deadline IS NULL AND p.id BETWEEN 1 AND 120""").fetchall()
        today = datetime.now(timezone.utc).date()
        filled = 0
        for post_id, description, author_email in rows:
            if not re.fullmatch(r'studymate-seed-\d{3}@astanait\.edu\.kz', author_email):
                continue
            if not description.endswith(f'Request #{post_id}.'):
                continue
            offset = DEMO_DEADLINE_OFFSETS[(post_id - 1) % len(DEMO_DEADLINE_OFFSETS)]
            demo_deadline = (today + timedelta(days=offset)).isoformat()
            conn.execute('UPDATE posts SET deadline=? WHERE id=? AND deadline IS NULL',
                         (demo_deadline, post_id))
            filled += 1
        if filled:
            print(f'[StudyMate demo] Assigned illustrative deadlines to {filled} synthetic posts.', flush=True)


def valid_date(value):
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def db():
    conn = sqlite3.connect(DB_PATH, timeout=12)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=12000')
    return conn


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def hash_password(password):
    salt = secrets.token_bytes(16)
    raw = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1)
    return 'scrypt$16384$' + salt.hex() + '$' + raw.hex()


def check_password(password, encoded):
    try:
        scheme, n, salt, digest = encoded.split('$')
        if scheme != 'scrypt' or n != '16384': return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def seal(data):
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).rstrip(b'=')
    sig = hmac.new(SECRET, payload, hashlib.sha256).hexdigest().encode()
    return payload.decode() + '.' + sig.decode()


def unseal(token):
    try:
        p, signature = token.split('.', 1)
        expected = hmac.new(SECRET, p.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected): return None
        return json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def rate_ok(address, action, max_attempts=9, window=900):
    key = (address, action)
    with RATE_LOCK:
        q = RATE[key]
        current = time.monotonic()
        while q and current - q[0] > window:
            q.popleft()
        if len(q) >= max_attempts:
            return False
        q.append(current)
        return True


def send_code(email, code):
    if ENV != 'production' and not os.environ.get('SMTP_HOST'):
        print(f'\n[StudyMate DEV OTP] {email} => {code} (valid 10 min)\n', flush=True)
        return
    msg = EmailMessage()
    msg['Subject'] = 'StudyMate — AITU email verification'
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = email
    msg.set_content(f'Your StudyMate verification code: {code}\nValid for 10 minutes.\nIf you did not register, ignore this email.')
    port = int(os.environ.get('SMTP_PORT', '587'))
    with smtplib.SMTP(os.environ['SMTP_HOST'], port, timeout=12) as smtp:
        smtp.starttls()
        if os.environ.get('SMTP_USER'):
            smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
        smtp.send_message(msg)


def log(conn, actor_id, event, target_type=None, target_id=None):
    conn.execute('INSERT INTO audit_logs(actor_id,event,target_type,target_id) VALUES(?,?,?,?)',
                 (actor_id, event, target_type, target_id))


def as_int(value, default=None):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class Handler(BaseHTTPRequestHandler):
    server_version = 'StudyMate/1.0'

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header('Cache-Control', 'no-store')
        if ENV == 'production': self.send_header('Strict-Transport-Security','max-age=31536000; includeSubDomains')
        super().end_headers()

    def send_json(self, data, status=200, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def failure(self, status, message):
        self.send_json({'error': message}, status)

    def cookie_value(self):
        try:
            jar = SimpleCookie()
            jar.load(self.headers.get('Cookie', ''))
            return jar['sm_session'].value if 'sm_session' in jar else None
        except Exception:
            return None

    def user(self, conn):
        data = unseal(self.cookie_value() or '')
        if not isinstance(data, dict) or data.get('exp', 0) < int(time.time()): return None, None
        user_id, ver = as_int(data.get('uid')), as_int(data.get('version'))
        if user_id is None or ver is None: return None, None
        user = conn.execute('SELECT id,first_name,last_name,email,role,verified_at,session_version,course,subjects_text,preferred_time,telegram,is_blocked,(avatar_data IS NOT NULL) AS has_avatar FROM users WHERE id=?',
                            (user_id,)).fetchone()
        if not user or user['is_blocked'] or not user['verified_at'] or user['session_version'] != ver: return None, None
        return user, data

    def cookie_header(self, token=None):
        secure = '; Secure' if ENV == 'production' else ''
        if token:
            return f'sm_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800{secure}'
        return f'sm_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure}'

    def new_session(self, user):
        csrf = secrets.token_urlsafe(32)
        return self.cookie_header(seal({'uid': user['id'], 'version': user['session_version'],
                                        'exp': int(time.time()) + 7 * 86400, 'csrf': csrf})), csrf

    def authorize(self, conn, writing=False):
        user, session = self.user(conn)
        if not user:
            self.failure(401, 'Sign in with your university email.'); return None
        if writing and not hmac.compare_digest(str(self.headers.get('X-CSRF-Token', '')), str(session['csrf'])):
            self.failure(403, 'Invalid CSRF token. Refresh the page.'); return None
        return user

    def body_json(self):
        length = as_int(self.headers.get('Content-Length', 0), 0)
        if length < 0 or length > (1100000 if urlparse(self.path).path == '/api/avatar' else 12000): raise ValueError('Request is too large.')
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('Invalid JSON format.')
        if not isinstance(data, dict): raise ValueError('Expected a JSON object.')
        return data

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            return self.static(path)
        with db() as conn:
            user, session = self.user(conn)
            avatar_match = re.fullmatch(r'/api/avatar/(\d+)', path)
            if avatar_match:
                if not user: return self.failure(401, 'Sign in to view avatars.')
                row = conn.execute('SELECT avatar_data FROM users WHERE id=? AND is_blocked=0',(int(avatar_match[1]),)).fetchone()
                if not row or not row['avatar_data']: return self.failure(404,'Avatar not found.')
                data = row['avatar_data']
                try:
                    header, content = data.split(',',1)
                    raw = base64.b64decode(content, validate=True)
                    mime = header[5:].split(';')[0]
                except (ValueError,base64.binascii.Error): return self.failure(404,'Avatar unavailable.')
                self.send_response(200)
                self.send_header('Content-Type',mime)
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                return self.wfile.write(raw)
            if path == '/api/me':
                return self.send_json({'user': dict(user) if user else None, 'csrf': session['csrf'] if session and user else None})
            if path == '/api/health':
                return self.send_json({'ok': True})
            if not user:
                return self.failure(401, 'Authentication required.')
            if path == '/api/mod/reports':
                if user['role'] != 'moderator': return self.failure(403,'Moderator only.')
                records=conn.execute('''SELECT r.id,r.reason,r.status,r.created_at,
                  p.title AS post_title,p.id AS post_id,p.author_id,u.first_name,u.last_name
                  FROM reports r JOIN posts p ON p.id=r.post_id JOIN users u ON u.id=p.author_id
                  ORDER BY CASE r.status WHEN 'open' THEN 0 ELSE 1 END,r.created_at DESC LIMIT 100''').fetchall()
                return self.send_json({'reports':[dict(x) for x in records]})
            if path == '/api/chat/notifications':
                return self.chat_notifications(conn,user)
            if path == '/api/subjects':
                return self.send_json({'subjects': [dict(x) for x in conn.execute('SELECT * FROM subjects ORDER BY name')]})
            if path == '/api/posts':
                return self.posts(conn, user, parse_qs(parsed.query))
            if path == '/api/applications':
                return self.applications(conn, user, parse_qs(parsed.query))
            chat_match = re.fullmatch(r'/api/posts/(\d+)/messages',path)
            if chat_match:
                return self.chat_messages(conn,user,int(chat_match[1]))
            if path == '/api/analytics':
                # Aggregated synthetic-data statistics; no personal identifiers are returned.
                by_subject = conn.execute('''SELECT s.name AS label, COUNT(*) AS count
                      FROM posts p JOIN subjects s ON s.id=p.subject_id
                      GROUP BY p.subject_id ORDER BY count DESC, s.name LIMIT 8''').fetchall()
                by_category = conn.execute('''SELECT category AS label, COUNT(*) AS count
                      FROM posts GROUP BY category ORDER BY count DESC, category''').fetchall()
                by_app_status = conn.execute('''SELECT status AS label, COUNT(*) AS count
                      FROM applications GROUP BY status ORDER BY status''').fetchall()
                return self.send_json({'subjects':[dict(r) for r in by_subject],
                  'categories':[dict(r) for r in by_category],
                  'applications':[dict(r) for r in by_app_status],
                  'sample': True})
            if path == '/api/stats':
                return self.send_json({'posts': conn.execute("SELECT COUNT(*) FROM posts WHERE status='open'").fetchone()[0],
                  'students': conn.execute('SELECT COUNT(*) FROM users WHERE verified_at IS NOT NULL').fetchone()[0],
                  'connections': conn.execute("SELECT COUNT(*) FROM applications WHERE status='accepted'").fetchone()[0]})
            return self.failure(404, 'Not found.')

    def static(self, path):
        files = {'/': ROOT/'static'/'index.html', '/index.html': ROOT/'static'/'index.html',
                 '/style.css': ROOT/'static'/'style.css', '/app.js': ROOT/'static'/'app.js',
                 '/favicon.svg': ROOT/'static'/'favicon.svg', '/assets/create-preview.webp': ROOT/'static'/'assets'/'create-preview.webp', '/assets/dashboard-preview.webp': ROOT/'static'/'assets'/'dashboard-preview.webp', '/assets/icon-exam.svg': ROOT/'static'/'assets'/'icon-exam.svg', '/assets/icon-help.svg': ROOT/'static'/'assets'/'icon-help.svg', '/assets/icon-team.svg': ROOT/'static'/'assets'/'icon-team.svg', '/assets/landing-preview.webp': ROOT/'static'/'assets'/'landing-preview.webp'}
        target = files.get(path)
        if not target or not target.is_file():
            self.send_error(404); return
        data = target.read_bytes()
        kind = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self.send_response(200)
        self.send_header('Content-Type', kind + ('; charset=utf-8' if kind.startswith('text/') or kind == 'application/javascript' else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def posts(self, conn, user, qs):
        where = ["p.status='open'", "(SELECT is_blocked FROM users WHERE id=p.author_id)=0", "(p.deadline IS NULL OR p.deadline >= date('now'))"]
        args = []
        if qs.get('mine') == ['1']:
            where = ['p.author_id=?']; args.append(user['id'])
        subject = as_int(qs.get('subject', [''])[0])
        if subject is not None and subject > 0:
            where.append('p.subject_id=?'); args.append(subject)
        category = qs.get('category', [''])[0]
        if category in CATEGORIES:
            where.append('p.category=?'); args.append(category)
        deadline_filter = qs.get('deadline', [''])[0]
        if deadline_filter:
            if not valid_date(deadline_filter):
                return self.failure(400, 'Invalid deadline filter.')
            where.append('p.deadline IS NOT NULL AND p.deadline <= ?')
            args.append(deadline_filter)
        search = qs.get('q', [''])[0].strip()[:100]
        if search:
            safe = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where.append("(p.title LIKE ? ESCAPE '\\' OR s.name LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\')")
            args.extend(['%' + safe + '%'] * 3)
        result = conn.execute('''SELECT p.*, s.name AS subject, s.code AS subject_code,
          u.first_name, u.last_name,(u.avatar_data IS NOT NULL) AS has_avatar,
          (SELECT COUNT(*) FROM applications a WHERE a.post_id=p.id AND a.status='accepted') AS joined,
          (SELECT status FROM applications a WHERE a.post_id=p.id AND a.applicant_id=?) AS my_status
          FROM posts p JOIN subjects s ON s.id=p.subject_id JOIN users u ON u.id=p.author_id
          WHERE ''' + ' AND '.join(where) + ' ORDER BY p.created_at DESC,p.id DESC LIMIT 200', [user['id']] + args).fetchall()
        self.send_json({'posts': [dict(x) for x in result]})

    def applications(self, conn, user, qs):
        scope = qs.get('scope', ['received'])[0]
        if scope not in ('received','sent'):
            return self.failure(400, 'Unknown section.')
        where = 'p.author_id=?' if scope == 'received' else 'a.applicant_id=?'
        rows = conn.execute('''SELECT a.id,a.post_id,a.applicant_id,a.message,a.status,a.created_at,
           p.title AS post_title,p.status AS post_status,p.max_partners,
           s.name AS subject,u.first_name,u.last_name,(u.avatar_data IS NOT NULL) AS applicant_has_avatar,
           CASE WHEN a.status='accepted' THEN
             CASE WHEN ?='received' THEN u.telegram ELSE owner.telegram END
             ELSE NULL END AS contact_telegram,
           CASE WHEN a.status='accepted' THEN
             CASE WHEN ?='received' THEN u.email ELSE owner.email END
             ELSE NULL END AS contact_email,
           (SELECT COUNT(*) FROM applications a2 WHERE a2.post_id=p.id AND a2.status='accepted') AS joined
           FROM applications a JOIN posts p ON p.id=a.post_id JOIN subjects s ON s.id=p.subject_id
           JOIN users u ON u.id=a.applicant_id JOIN users owner ON owner.id=p.author_id WHERE ''' + where + ' ORDER BY a.created_at DESC,a.id DESC LIMIT 200', (scope,scope,user['id'])).fetchall()
        self.send_json({'applications': [dict(x) for x in rows]})

    def do_POST(self):
        path = urlparse(self.path).path
        origin = self.headers.get('Origin')
        if origin:
            expected_origin = (os.environ.get('PUBLIC_BASE_URL', '').rstrip('/') if ENV == 'production' else
                               ('https://' if isinstance(self.connection, __import__('ssl').SSLSocket) else 'http://') + self.headers.get('Host', ''))
            if not hmac.compare_digest(origin, expected_origin):
                return self.failure(403, 'Cross-origin request blocked.')
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip() != 'application/json':
            return self.failure(415, 'Use application/json.')
        try: data = self.body_json()
        except ValueError as exc: return self.failure(400, str(exc))
        try:
            with db() as conn:
                if path == '/api/register': return self.register(conn, data)
                if path == '/api/verify': return self.verify(conn, data)
                if path == '/api/resend-code': return self.resend(conn, data)
                if path == '/api/login': return self.login(conn, data)
                user = self.authorize(conn, writing=True)
                if not user: return
                if path == '/api/logout':
                    conn.execute('UPDATE users SET session_version=session_version+1 WHERE id=?', (user['id'],))
                    log(conn, user['id'], 'logout')
                    conn.commit()
                    return self.send_json({'ok': True}, cookie=self.cookie_header())
                if path == '/api/posts': return self.create_post(conn, user, data)
                if path == '/api/profile': return self.profile(conn, user, data)
                if path == '/api/avatar': return self.save_avatar(conn, user, data)
                read_match = re.fullmatch(r'/api/posts/(\d+)/messages/read',path)
                if read_match: return self.mark_chat_read(conn,user,int(read_match[1]))
                chat_match = re.fullmatch(r'/api/posts/(\d+)/messages',path)
                if chat_match: return self.send_chat(conn,user,int(chat_match[1]),data)
                match = re.fullmatch(r'/api/posts/(\d+)/report',path)
                if match: return self.report_post(conn,user,int(match[1]),data)
                match = re.fullmatch(r'/api/mod/reports/(\d+)/remove',path)
                if match: return self.mod_remove(conn,user,int(match[1]))
                match = re.fullmatch(r'/api/mod/users/(\d+)/block',path)
                if match: return self.mod_block(conn,user,int(match[1]),data)
                match = re.fullmatch(r'/api/posts/(\d+)/deadline', path)
                if match: return self.update_post_deadline(conn, user, int(match[1]), data)
                match = re.fullmatch(r'/api/posts/(\d+)/close', path)
                if match: return self.close_post(conn, user, int(match[1]))
                match = re.fullmatch(r'/api/posts/(\d+)/apply', path)
                if match: return self.apply(conn, user, int(match[1]), data)
                match = re.fullmatch(r'/api/applications/(\d+)/decision', path)
                if match: return self.decision(conn, user, int(match[1]), data)
                return self.failure(404, 'Not found.')
        except sqlite3.IntegrityError:
            return self.failure(409, 'This record already exists or violates a database constraint.')
        except sqlite3.OperationalError:
            return self.failure(503, 'Database temporarily unavailable.')

    def register(self, conn, data):
        if not rate_ok(self.client_address[0], 'register', 6): return self.failure(429, 'Too many attempts. Try again later.')
        name = str(data.get('first_name', '')).strip()
        surname = str(data.get('last_name', '')).strip()
        email = str(data.get('email', '')).strip().lower()
        password = str(data.get('password', ''))
        if not NAME_RX.fullmatch(name) or not NAME_RX.fullmatch(surname):
            return self.failure(400, 'Enter valid first and last names (2–50 characters).')
        if not EMAIL_RX.fullmatch(email):
            return self.failure(400, 'Use an @astanait.edu.kz email address.')
        if len(password) < 10 or len(password) > 128 or not any(x.isdigit() for x in password) or not any(x.isalpha() for x in password):
            return self.failure(400, 'Password must be 10–128 characters and include letters and digits.')
        if data.get('consent') is not True:
            return self.failure(400, 'Consent to data processing is required.')
        if conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone():
            return self.failure(409, 'An account with this email already exists.')
        code = f'{secrets.randbelow(1000000):06}'
        code_hash = hmac.new(SECRET, (email + ':' + code).encode(), hashlib.sha256).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            # Send before writing unverified user: no half-created account if SMTP fails.
            send_code(email, code)
        except (OSError, smtplib.SMTPException, KeyError) as exc:
            print('SMTP error:', repr(exc), file=sys.stderr)
            return self.failure(503, 'Unable to send email. Check SMTP settings.')
        cur = conn.execute('''INSERT INTO users(first_name,last_name,email,password_hash,verify_hash,verify_expires_at)
                       VALUES(?,?,?,?,?,?)''', (name, surname, email, hash_password(password), code_hash, expires))
        log(conn, cur.lastrowid, 'register', 'users', cur.lastrowid)
        conn.commit()
        return self.send_json({'ok': True, 'message': 'Verification code sent.'}, 201)

    def resend(self, conn, data):
        if not rate_ok(self.client_address[0], 'resend', 4): return self.failure(429, 'Please try again later.')
        email = str(data.get('email', '')).strip().lower()
        row = conn.execute('SELECT id FROM users WHERE email=? AND verified_at IS NULL', (email,)).fetchone()
        if row:
            code = f'{secrets.randbelow(1000000):06}'
            hv = hmac.new(SECRET, (email + ':' + code).encode(), hashlib.sha256).hexdigest()
            ex = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
            try: send_code(email, code)
            except (OSError, smtplib.SMTPException, KeyError): return self.failure(503, 'Unable to send email.')
            conn.execute('UPDATE users SET verify_hash=?,verify_expires_at=?,verify_attempts=0 WHERE id=?', (hv, ex, row['id']))
            conn.commit()
        return self.send_json({'ok': True, 'message': 'If the account is awaiting verification, a code has been sent.'})

    def verify(self, conn, data):
        if not rate_ok(self.client_address[0], 'verify', 16): return self.failure(429, 'Too many attempts.')
        email = str(data.get('email', '')).strip().lower()
        code = str(data.get('code', '')).strip()
        if not EMAIL_RX.fullmatch(email) or not re.fullmatch(r'\d{6}', code):
            return self.failure(400, 'Enter your AITU email and six-digit code.')
        row = conn.execute('SELECT * FROM users WHERE email=? AND verified_at IS NULL', (email,)).fetchone()
        if not row or row['verify_attempts'] >= 5 or not row['verify_hash'] or (row['verify_expires_at'] or '') < now():
            return self.failure(400, 'Code expired or invalid. Request a new one.')
        provided = hmac.new(SECRET, (email + ':' + code).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(provided, row['verify_hash']):
            conn.execute('UPDATE users SET verify_attempts=verify_attempts+1 WHERE id=?', (row['id'],))
            conn.commit()
            return self.failure(400, 'Incorrect verification code.')
        conn.execute('UPDATE users SET verified_at=?,verify_hash=NULL,verify_expires_at=NULL,verify_attempts=0 WHERE id=?',
                     (now(), row['id']))
        log(conn, row['id'], 'verify', 'users', row['id'])
        conn.commit()
        cookie, csrf = self.new_session(row)
        return self.send_json({'ok': True, 'csrf': csrf}, cookie=cookie)

    def login(self, conn, data):
        if not rate_ok(self.client_address[0], 'login', 12): return self.failure(429, 'Too many sign-in attempts.')
        email = str(data.get('email', '')).strip().lower()
        password = str(data.get('password', ''))
        if not EMAIL_RX.fullmatch(email) or len(password) > 128:
            return self.failure(401, 'Incorrect email or password.')
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if not user or not check_password(password, user['password_hash']):
            return self.failure(401, 'Incorrect email or password.')
        if not user['verified_at']:
            return self.failure(403, 'Verify your university email first.')
        if user['is_blocked']:
            return self.failure(403, 'This account is blocked.')
        log(conn, user['id'], 'login')
        conn.commit()
        cookie, csrf = self.new_session(user)
        return self.send_json({'ok': True, 'csrf': csrf}, cookie=cookie)

    def create_post(self, conn, user, data):
        title = str(data.get('title', '')).strip()
        description = str(data.get('description', '')).strip()
        category, intent, fmt = data.get('category'), data.get('intent'), data.get('meeting_format')
        subject = as_int(data.get('subject_id'))
        spots = as_int(data.get('max_partners'))
        deadline = str(data.get('deadline', '')).strip()
        due_date = valid_date(deadline)
        if not due_date or due_date < datetime.now(timezone.utc).date():
            return self.failure(400, 'Choose a valid deadline (today or later).')
        if not 5 <= len(title) <= 100 or not 10 <= len(description) <= 1000:
            return self.failure(400, 'Title: 5–100 characters; description: 10–1000.')
        if category not in CATEGORIES or intent not in INTENTS or fmt not in FORMATS or not spots or not 1 <= spots <= 12:
            return self.failure(400, 'Check the type, format and number of partners.')
        if not conn.execute('SELECT id FROM subjects WHERE id=?', (subject,)).fetchone():
            return self.failure(400, 'Choose a subject from the list.')
        cur = conn.execute('''INSERT INTO posts(author_id,subject_id,category,intent,title,description,meeting_format,max_partners,deadline)
                              VALUES(?,?,?,?,?,?,?,?,?)''', (user['id'], subject, category, intent, title, description, fmt, spots, deadline))
        log(conn, user['id'], 'create_post', 'posts', cur.lastrowid)
        conn.commit()
        return self.send_json({'ok': True, 'post_id': cur.lastrowid}, 201)

    def update_post_deadline(self, conn, user, post_id, data):
        """Only the author can reschedule their own open post."""
        deadline = str(data.get('deadline', '')).strip()
        due_date = valid_date(deadline)
        if not due_date or due_date < datetime.now(timezone.utc).date():
            return self.failure(400, 'Choose a valid deadline (today or later).')
        changed = conn.execute("UPDATE posts SET deadline=? WHERE id=? AND author_id=? AND status='open'",
                               (deadline, post_id, user['id']))
        if not changed.rowcount:
            return self.failure(404, 'Open post not found or not owned by you.')
        conn.commit()
        return self.send_json({'ok': True, 'post_id': post_id, 'deadline': deadline})

    def close_post(self, conn, user, post_id):
        changed = conn.execute("UPDATE posts SET status='closed' WHERE id=? AND author_id=? AND status='open'", (post_id, user['id']))
        if not changed.rowcount: return self.failure(404, 'Active post not found or not owned by you.')
        log(conn, user['id'], 'close_post', 'posts', post_id)
        conn.commit()
        return self.send_json({'ok': True})

    def apply(self, conn, user, post_id, data):
        msg = str(data.get('message', '')).strip()
        if not 5 <= len(msg) <= 500: return self.failure(400, 'Write a message between 5 and 500 characters.')
        post = conn.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
        if not post or post['status'] != 'open' or (post['deadline'] and post['deadline'] < datetime.now(timezone.utc).date().isoformat()): return self.failure(404, 'Post unavailable or expired.')
        if post['author_id'] == user['id']: return self.failure(400, 'You cannot apply to your own post.')
        accepted = conn.execute("SELECT COUNT(*) FROM applications WHERE post_id=? AND status='accepted'", (post_id,)).fetchone()[0]
        if accepted >= post['max_partners']: return self.failure(409, 'This team is already full.')
        cur = conn.execute('INSERT INTO applications(post_id,applicant_id,message) VALUES(?,?,?)',
                           (post_id, user['id'], msg))
        log(conn, user['id'], 'apply', 'applications', cur.lastrowid)
        conn.commit()
        return self.send_json({'ok': True}, 201)

    def decision(self, conn, user, app_id, data):
        choice = data.get('status')
        if choice not in ('accepted','declined'): return self.failure(400, 'Choose accept or decline.')
        # IMMEDIATE transaction ensures the team limit stays correct for concurrent acceptances.
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('''SELECT a.*,p.author_id,p.max_partners,p.status AS post_status
            FROM applications a JOIN posts p ON p.id=a.post_id WHERE a.id=?''', (app_id,)).fetchone()
        if not row or row['author_id'] != user['id'] or row['status'] != 'pending' or row['post_status'] != 'open':
            return self.failure(404, 'This application cannot be changed.')
        if choice == 'accepted':
            accepted = conn.execute("SELECT COUNT(*) FROM applications WHERE post_id=? AND status='accepted'", (row['post_id'],)).fetchone()[0]
            if accepted >= row['max_partners']: return self.failure(409, 'No spots are available.')
        conn.execute('UPDATE applications SET status=? WHERE id=?', (choice, app_id))
        log(conn, user['id'], 'accept' if choice == 'accepted' else 'decline', 'applications', app_id)
        conn.commit()
        return self.send_json({'ok': True})

    def chat_allowed(self, conn, user, post_id):
        post=conn.execute('SELECT author_id FROM posts WHERE id=?',(post_id,)).fetchone()
        if not post: return False
        if post['author_id']==user['id']: return True
        return bool(conn.execute("SELECT 1 FROM applications WHERE post_id=? AND applicant_id=? AND status='accepted'",(post_id,user['id'])).fetchone())

    def chat_notifications(self, conn, user):
        # Both organizers and accepted participants, no data from unrelated chats.
        rows=conn.execute("""SELECT p.id AS post_id,p.title AS post_title,
          COALESCE((SELECT MAX(m.id) FROM chat_messages m WHERE m.post_id=p.id),0) AS latest_id,
          COALESCE((SELECT MAX(m.id) FROM chat_messages m WHERE m.post_id=p.id AND m.sender_id!=?),0) AS latest_other_id,
          COALESCE((SELECT COUNT(*) FROM chat_messages m WHERE m.post_id=p.id AND m.sender_id!=? AND
             m.id>COALESCE((SELECT r.last_read_id FROM chat_reads r WHERE r.post_id=p.id AND r.user_id=?),0)),0) AS unread
          FROM posts p WHERE p.author_id=? OR EXISTS
           (SELECT 1 FROM applications a WHERE a.post_id=p.id AND a.applicant_id=? AND a.status='accepted')
          ORDER BY p.id DESC LIMIT 150""",(user['id'],user['id'],user['id'],user['id'],user['id'])).fetchall()
        return self.send_json({'chats':[dict(r) for r in rows if r['latest_id'] or conn.execute(
            "SELECT 1 FROM applications a WHERE a.post_id=? AND a.status='accepted' LIMIT 1",(r['post_id'],)).fetchone()]})

    def mark_chat_read(self, conn, user, post_id):
        if not self.chat_allowed(conn,user,post_id): return self.failure(403,'No access to this chat.')
        latest=conn.execute('SELECT COALESCE(MAX(id),0) FROM chat_messages WHERE post_id=?',(post_id,)).fetchone()[0]
        conn.execute("""INSERT INTO chat_reads(post_id,user_id,last_read_id) VALUES(?,?,?)
           ON CONFLICT(post_id,user_id) DO UPDATE SET last_read_id=MAX(chat_reads.last_read_id,excluded.last_read_id)""",
           (post_id,user['id'],latest))
        conn.commit()
        return self.send_json({'ok':True,'last_read_id':latest})

    def chat_messages(self, conn, user, post_id):
        if not self.chat_allowed(conn,user,post_id): return self.failure(403,'Chat opens only after acceptance.')
        rows=conn.execute('''SELECT m.id,m.sender_id,m.body,m.created_at,u.first_name,u.last_name,
                       (u.avatar_data IS NOT NULL) AS has_avatar
                       FROM chat_messages m JOIN users u ON u.id=m.sender_id
                       WHERE m.post_id=? ORDER BY m.id DESC LIMIT 150''',(post_id,)).fetchall()
        post=conn.execute('SELECT title FROM posts WHERE id=?',(post_id,)).fetchone()
        return self.send_json({'post_title':post['title'],'messages':[dict(r) for r in reversed(rows)]})

    def send_chat(self, conn, user, post_id, data):
        if not self.chat_allowed(conn,user,post_id): return self.failure(403,'Chat opens only after acceptance.')
        body=str(data.get('body','')).strip()
        if not 1 <= len(body) <= 1000 or any((ord(c)<32 and c not in '\n\t') for c in body):
            return self.failure(400,'Message must contain 1–1000 characters.')
        cur=conn.execute('INSERT INTO chat_messages(post_id,sender_id,body) VALUES(?,?,?)',(post_id,user['id'],body))
        conn.commit()
        return self.send_json({'ok':True,'id':cur.lastrowid},201)

    def save_avatar(self, conn, user, data):
        photo=data.get('image')
        if photo is None:
            conn.execute('UPDATE users SET avatar_data=NULL WHERE id=?',(user['id'],))
            conn.commit()
            return self.send_json({'ok':True})
        if not isinstance(photo,str) or len(photo)>900000:
            return self.failure(400,'Avatar is too large (max 600 KB).' )
        m=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})',photo)
        if not m: return self.failure(400,'Use a JPEG, PNG or WebP image.')
        try: raw=base64.b64decode(m[2],validate=True)
        except (ValueError,base64.binascii.Error): return self.failure(400,'Invalid image.')
        if len(raw)>600000 or len(raw)<40: return self.failure(400,'Avatar must be smaller than 600 KB.')
        if m[1]=='png' and not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            return self.failure(400,'Invalid PNG image.')
        if m[1]=='jpeg' and not raw.startswith(b'\xff\xd8\xff'):
            return self.failure(400,'Invalid JPEG image.')
        if m[1]=='webp' and not (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'):
            return self.failure(400,'Invalid WebP image.')
        conn.execute('UPDATE users SET avatar_data=? WHERE id=?',(photo,user['id']))
        conn.commit()
        return self.send_json({'ok':True})

    def profile(self, conn, user, data):
        first, last = str(data.get('first_name', '')).strip(), str(data.get('last_name', '')).strip()
        if not NAME_RX.fullmatch(first) or not NAME_RX.fullmatch(last):
            return self.failure(400, 'Invalid first or last name.')
        info = {}
        for field,limit in (('course',60),('subjects_text',120),('preferred_time',80),('telegram',64)):
            value = str(data.get(field,'')).strip()
            if len(value)>limit or any(ord(c)<32 for c in value):
                return self.failure(400,'Invalid profile field: '+field)
            info[field]=value
        if info['telegram'] and not re.fullmatch(r'@?[A-Za-z0-9_]{5,32}',info['telegram']):
            return self.failure(400,'Telegram must be a username (5-32 characters), or empty.')
        conn.execute('''UPDATE users SET first_name=?,last_name=?,course=?,subjects_text=?,preferred_time=?,telegram=? WHERE id=?''',
            (first,last,info['course'],info['subjects_text'],info['preferred_time'],info['telegram'],user['id']))
        log(conn,user['id'],'update_profile','users',user['id'])
        conn.commit()
        return self.send_json({'ok': True})

    def report_post(self, conn, user, post_id, data):
        reason=str(data.get('reason','')).strip()
        if not 10<=len(reason)<=500: return self.failure(400,'Reason must contain 10-500 characters.')
        post=conn.execute('SELECT author_id FROM posts WHERE id=?',(post_id,)).fetchone()
        if not post or post['author_id']==user['id']: return self.failure(400,'Cannot report this post.')
        cur=conn.execute('INSERT INTO reports(reporter_id,post_id,reason) VALUES(?,?,?)',
                         (user['id'],post_id,reason))
        log(conn,user['id'],'report_post','reports',cur.lastrowid)
        conn.commit()
        return self.send_json({'ok':True},201)

    def mod_remove(self, conn, user, report_id):
        if user['role']!='moderator': return self.failure(403,'Moderator only.')
        report=conn.execute('SELECT post_id FROM reports WHERE id=?',(report_id,)).fetchone()
        if not report: return self.failure(404,'Report not found.')
        conn.execute("UPDATE posts SET status='closed' WHERE id=?",(report['post_id'],))
        conn.execute("UPDATE reports SET status='resolved' WHERE post_id=?",(report['post_id'],))
        log(conn,user['id'],'remove_post','posts',report['post_id'])
        conn.commit()
        return self.send_json({'ok':True})

    def mod_block(self,conn,user,target_id,data):
        if user['role']!='moderator': return self.failure(403,'Moderator only.')
        if user['id']==target_id: return self.failure(400,'Cannot block yourself.')
        target=conn.execute('SELECT id,role FROM users WHERE id=?',(target_id,)).fetchone()
        if not target or target['role']=='moderator': return self.failure(403,'This account cannot be blocked.')
        blocked=bool(data.get('blocked',True))
        conn.execute('UPDATE users SET is_blocked=?,session_version=session_version+1 WHERE id=?',(int(blocked),target_id))
        log(conn,user['id'],'block_user' if blocked else 'unblock_user','users',target_id)
        conn.commit()
        return self.send_json({'ok':True})


def main():
    if not DB_PATH.is_file():
        sys.exit('Database missing. Run python init_db.py first.')
    migrate_database()
    with db() as conn:
        conn.execute('SELECT id FROM subjects LIMIT 1').fetchone()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'StudyMate running: http://{HOST}:{PORT} (mode={ENV}, db={DB_PATH})', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopped.')
    finally: server.server_close()


if __name__ == '__main__':
    main()
