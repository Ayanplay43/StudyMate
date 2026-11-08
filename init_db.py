"""Initializes SQLite and 100+ clearly synthetic study requests for defense.
Use --reset to REPLACE an existing database (destroys existing data!).
"""
import argparse
import hashlib
import random
import secrets
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from server import ROOT, DB_PATH, hash_password

SUBJECTS = [
 ('ICT101','Information and Communication Technologies'),
 ('CS101','Programming Fundamentals'),
 ('MAT101','Calculus I'),
 ('MAT102','Calculus II'),
 ('PHY101','Physics'),
 ('ENG101','Academic English'),
 ('DS201','Data Structures and Algorithms'),
 ('DB202','Database Management Systems'),
 ('WEB203','Web Technologies'),
 ('STAT201','Probability and Statistics'),
 ('AI301','Artificial Intelligence'),
 ('ML302','Machine Learning'),
 ('NET201','Computer Networks'),
 ('SEC301','Cybersecurity'),
 ('OS202','Operating Systems'),
 ('SE301','Software Engineering'),
 ('MOB301','Mobile Development'),
 ('BUS102','Project Management'),
]
NAMES = [
 ('Aydana','Serikova'),('Alikhan','Nurlanov'),('Aruzhan','Omarova'),('Daniyar','Sadykov'),
 ('Madina','Ermekova'),('Amir','Abdrakhmanov'),('Aliya','Toktarova'),('Sanzhar','Akhmetov'),
 ('Kamila','Zhanabaeva'),('Arman','Kasymov'),('Zhanel','Bekova'),('Ersin','Tuleuov'),
 ('Diana','Abilova'),('Timur','Askarov'),('Ayaulym','Mukhtarova'),('Rasul','Imanov'),
 ('Amira','Ibraeva'),('Nurasyl','Esenov'),('Meruert','Dauletova'),('Adilet','Muratov'),
]
CATEGORIES = ['project','group','midterm','endterm','homework','study']
TITLES = {
 'project':['Building a team for the final project','Looking for a project partner','Seeking teammates for a development project'],
 'group':['Looking for a group-work team','Looking for a lab partner','Working together on a presentation'],
 'midterm':['Preparing for the midterm together','Reviewing topics before the midterm','Looking for a midterm study partner'],
 'endterm':['Preparing for the endterm together','Reviewing the full course for the endterm','Looking for an endterm study group'],
 'homework':['Help with homework','Working through difficult problems','Solving assignments together'],
 'study':['Looking for a study buddy this week','Practicing and revising together','Let’s hold a study session'],
}
DESCRIPTIONS = [
 'I want to connect with motivated students: split up tasks, agree on a schedule and support one another.',
 'Looking for a regular study partner. We can share notes and work through examples after classes.',
 'Planning short study sessions several times a week to cover the material and practice consistently.',
 'Let’s study together, make a plan and work through the hardest questions in practice.',
]


def init(reset=False):
    if DB_PATH.exists() and not reset:
        print(f'Database already exists: {DB_PATH}. To reset completely: python init_db.py --reset')
        return
    if DB_PATH.exists(): DB_PATH.unlink()
    rng = random.Random(4103)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((ROOT/'schema.sql').read_text(encoding='utf-8'))
        conn.executemany('INSERT INTO subjects(code,name) VALUES(?,?)', SUBJECTS)
        # Test accounts are synthetic placeholders with random, unpublished passwords.
        # They do NOT prove ownership of real university accounts.
        for i, (first,last) in enumerate(NAMES, 1):
            conn.execute('''INSERT INTO users(first_name,last_name,email,password_hash,verified_at)
                 VALUES(?,?,?,?,CURRENT_TIMESTAMP)''',
                 (first,last,f'studymate-seed-{i:03d}@astanait.edu.kz',hash_password(secrets.token_urlsafe(26))))
        base = datetime.now(timezone.utc)
        for i in range(120):
            author = 1 + ((i * 7 + 3) % len(NAMES))
            subject = 1 + ((i * 11 + 1) % len(SUBJECTS))
            cat = CATEGORIES[i % len(CATEGORIES)]
            title = TITLES[cat][i % 3]
            desc = DESCRIPTIONS[(i * 5) % len(DESCRIPTIONS)] + f' We can agree on a study plan and meeting format. Request #{i+1}.'
            date = (base - timedelta(hours=i * 3)).strftime('%Y-%m-%d %H:%M:%S')
            # Future demo deadlines are illustrative and can be used to test the filter.
            demo_deadline = (base.date() + timedelta(days=(1,3,5,7,10,14,21,28,35,42)[i % 10])).isoformat()
            conn.execute('''INSERT INTO posts(author_id,subject_id,category,intent,title,description,meeting_format,max_partners,status,created_at,deadline)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (author,subject,cat,'seek' if i % 4 else 'offer',title,desc,
                   ['online','offline','either'][i % 3],1+(i%5),'open' if i % 13 else 'closed',date,demo_deadline))
        accepted_counter = {}
        made = set()
        for i in range(55):
            post_id = 1 + ((i * 13 + 7) % 120)
            owner = 1 + (((post_id-1)*7 + 3) % len(NAMES))
            applicant = 1 + ((i * 9 + 2) % len(NAMES))
            if applicant == owner:
                applicant = (applicant % len(NAMES)) + 1
            while (post_id,applicant) in made or applicant == owner:
                applicant = applicant % len(NAMES) + 1
            made.add((post_id,applicant))
            status = ['pending','declined','accepted'][i % 3]
            if status == 'accepted':
                cap = 1 + ((post_id-1)%5)
                count = accepted_counter.get(post_id,0)
                if count >= cap: status = 'pending'
                else: accepted_counter[post_id] = count+1
            conn.execute('INSERT INTO applications(post_id,applicant_id,message,status) VALUES(?,?,?,?)',
                         (post_id,applicant,'Hi! I am studying this subject too. Let’s prepare together and share materials.',status))
        for i in range(35):
            conn.execute('INSERT INTO audit_logs(actor_id,event,target_type,target_id) VALUES(?,?,?,?)',
                         (1+(i%20), 'create_post' if i%2 else 'apply', 'posts',1+(i%120)))
        conn.commit()
        counts = {table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for table in ('users','subjects','posts','applications','audit_logs')}
    print(f'StudyMate database created: {DB_PATH}\nSample data: {counts}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true', help='Deletes existing database!')
    args = parser.parse_args()
    init(args.reset)
