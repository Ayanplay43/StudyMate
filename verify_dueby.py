"""Read-only deadline statistics to verify StudyMate Due by filtering."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
path = Path(__file__).resolve().parent/'studymate.sqlite'
with sqlite3.connect(path) as c:
    columns = [r[1] for r in c.execute('PRAGMA table_info(posts)')]
    if 'deadline' not in columns:
        print('Not migrated: restart StudyMate with the patched server.py first.')
        raise SystemExit(1)
    total, dated = c.execute('SELECT COUNT(*), COUNT(deadline) FROM posts').fetchone()
    cutoff=(datetime.now(timezone.utc).date()+timedelta(days=7)).isoformat()
    active=c.execute("SELECT COUNT(*) FROM posts WHERE status='open' AND (deadline IS NULL OR deadline >= date('now'))").fetchone()[0]
    due=c.execute("SELECT COUNT(*) FROM posts WHERE status='open' AND deadline >= date('now') AND deadline <= ?",(cutoff,)).fetchone()[0]
    print(f'All posts: {total}; with deadline: {dated}; visible active: {active}; due by {cutoff}: {due}')
    print('New posts retain real user-selected deadlines; seeded demo dates are illustrative.')
