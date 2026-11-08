PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 first_name TEXT NOT NULL CHECK (length(trim(first_name)) BETWEEN 2 AND 50),
 last_name TEXT NOT NULL CHECK (length(trim(last_name)) BETWEEN 2 AND 50),
 email TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (email GLOB '*@astanait.edu.kz'),
 password_hash TEXT NOT NULL CHECK (length(password_hash) > 40),
 role TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student','moderator')),
 verified_at TEXT,
 verify_hash TEXT,
 verify_expires_at TEXT,
 verify_attempts INTEGER NOT NULL DEFAULT 0 CHECK (verify_attempts BETWEEN 0 AND 5),
 session_version INTEGER NOT NULL DEFAULT 0,
 course TEXT NOT NULL DEFAULT '',
 subjects_text TEXT NOT NULL DEFAULT '',
 preferred_time TEXT NOT NULL DEFAULT '',
 telegram TEXT NOT NULL DEFAULT '',
 avatar_data TEXT,
 is_blocked INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subjects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 code TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
 category TEXT NOT NULL CHECK (category IN ('project','group','midterm','endterm','homework','study')),
 intent TEXT NOT NULL CHECK (intent IN ('seek','offer')),
 title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 5 AND 100),
 description TEXT NOT NULL CHECK (length(trim(description)) BETWEEN 10 AND 1000),
 meeting_format TEXT NOT NULL CHECK (meeting_format IN ('online','offline','either')),
 max_partners INTEGER NOT NULL CHECK (max_partners BETWEEN 1 AND 12),
 deadline TEXT,
 status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 applicant_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 message TEXT NOT NULL CHECK (length(trim(message)) BETWEEN 5 AND 500),
 status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(post_id, applicant_id)
);

CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 10 AND 500),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(reporter_id,post_id)
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status,created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 event TEXT NOT NULL CHECK (event IN ('register','verify','login','logout','create_post','close_post','apply','accept','decline','update_profile','report_post','remove_post','block_user','unblock_user')),
 target_type TEXT,
 target_id INTEGER,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_posts_filters ON posts(status, subject_id, category, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_applications_post_status ON applications(post_id, status);
CREATE INDEX IF NOT EXISTS idx_applications_applicant ON applications(applicant_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id, created_at);

CREATE INDEX IF NOT EXISTS idx_posts_deadline ON posts(status, deadline);

CREATE TABLE IF NOT EXISTS chat_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 1000),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_post_id ON chat_messages(post_id,id);

CREATE TABLE IF NOT EXISTS chat_reads (
 post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 last_read_id INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(post_id, user_id)
);
