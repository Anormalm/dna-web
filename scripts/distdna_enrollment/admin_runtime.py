"""Shared operational state. Sampling inputs and frozen response archives stay unchanged."""
import json,os,signal,sqlite3,time
from pathlib import Path
from common import read

class ControlError(ValueError):pass

def init_queue(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS admin_job_intents (key TEXT PRIMARY KEY, desired TEXT NOT NULL, resume_state TEXT, billing TEXT NOT NULL DEFAULT 'openrouter', byok_confirmed INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS admin_modes (mode TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0);
    INSERT OR IGNORE INTO admin_modes VALUES('raw',0),('chat',0);
    CREATE TABLE IF NOT EXISTS admin_actions (id INTEGER PRIMARY KEY, at REAL, actor INTEGER, action TEXT, target TEXT, details TEXT);
    ''');db.commit()

def init_money(run):
    db=sqlite3.connect(Path(run)/'chat-spend.sqlite',timeout=30);db.row_factory=sqlite3.Row
    db.executescript('''
    CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY,reserved REAL,actual REAL,created REAL);
    CREATE TABLE IF NOT EXISTS admin_budget (id INTEGER PRIMARY KEY CHECK(id=1),cap REAL NOT NULL,version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS admin_cost_details (id TEXT PRIMARY KEY, job TEXT, billing TEXT, openrouter REAL, upstream_estimate REAL);
    CREATE TABLE IF NOT EXISTS admin_budget_audit (id INTEGER PRIMARY KEY,at REAL,actor INTEGER,previous REAL,value REAL);
    ''')
    policy=Path(run)/'chat-collection-policy.json'
    initial=float(read(policy).get('maximumCostUsd',75)) if policy.exists() else 75.
    db.execute('INSERT OR IGNORE INTO admin_budget(id,cap) VALUES(1,?)',(initial,));db.commit();return db

def budget(run):
    with init_money(run) as db:
        setting=dict(db.execute('SELECT cap,version FROM admin_budget WHERE id=1').fetchone())
        used=db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges').fetchone()[0]
        upstream=db.execute('SELECT COALESCE(SUM(upstream_estimate),0) FROM admin_cost_details').fetchone()[0]
    return dict(capUsd=setting['cap'],version=setting['version'],committedUsd=used,remainingUsd=max(0,setting['cap']-used),byokInferenceEstimateUsd=upstream)

def stopped(db,key,mode):
    try:
        intent=db.execute('SELECT desired FROM admin_job_intents WHERE key=?',(key,)).fetchone()
        flag=db.execute('SELECT paused FROM admin_modes WHERE mode=?',(mode,)).fetchone()
        return bool((intent and intent[0]=='paused') or (flag and flag[0]))
    except sqlite3.OperationalError:return False

def worker_identity(pid,folder):
    try:
        if not isinstance(pid,int) or pid<=1:return None
        parts=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        argv=[p.decode() for p in parts if p]
        if str(Path(folder).resolve()) not in argv:return None
        if not any(Path(v).name in ('full_collect.py','chat_queue.py','admin_chat_worker.py') for v in argv):return None
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,UnicodeError):return None

def signal_worker(pid,folder,sig=signal.SIGTERM):
    identity=worker_identity(pid,folder)
    if identity is None:return False
    try:
        # pidfd prevents signaling a different process if this PID exits and is reused.
        fd=os.pidfd_open(pid)
        try:
            if worker_identity(pid,folder)!=identity:return False
            signal.pidfd_send_signal(fd,sig)
        finally:os.close(fd)
        return True
    except ProcessLookupError:return False

def reconcile(db):
    init_queue(db)
    for row in db.execute("SELECT * FROM jobs WHERE state NOT IN ('complete','chat_complete','enrolled')").fetchall():
        if not stopped(db,row['key'],row['mode']):continue
        if row['pid'] and row['run']:signal_worker(row['pid'],row['run'])
        if row['state']!='paused':
            db.execute("INSERT INTO admin_job_intents(key,desired,resume_state) VALUES(?,'run',?) ON CONFLICT(key) DO UPDATE SET resume_state=excluded.resume_state",(row['key'],row['state']))
            db.execute("UPDATE jobs SET state='paused',detail='Stopped by administrator; completed records retained',updated=? WHERE key=?",(time.time(),row['key']))
    db.commit()

def apply_state(db,key,fields):
    if 'state' not in fields:return fields
    row=db.execute('SELECT mode,state FROM jobs WHERE key=?',(key,)).fetchone()
    if row and stopped(db,key,row['mode']) and fields['state'] not in ('complete','chat_complete','enrolled'):
        if fields['state']!='paused':
            db.execute('UPDATE admin_job_intents SET resume_state=? WHERE key=?',(fields['state'],key))
        fields=dict(fields,state='paused',detail='Stopped by administrator; completed records retained')
    return fields

def audit(db,actor,action,target,details=None):
    db.execute('INSERT INTO admin_actions(at,actor,action,target,details) VALUES(?,?,?,?,?)',(time.time(),actor,action,target,json.dumps(details or {})))


def reserve(run,key,billing,request_id,amount):
    with sqlite3.connect(Path(run)/'queue.sqlite',timeout=30) as queue:
        if stopped(queue,key,'chat'):raise ControlError('Job paused by administrator.')
    with init_money(run) as db:
        db.execute('BEGIN IMMEDIATE')
        cap=db.execute('SELECT cap FROM admin_budget WHERE id=1').fetchone()[0]
        total=db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges').fetchone()[0]
        if total+amount>cap:raise ControlError('API budget exhausted; increase the cap or stop jobs.')
        db.execute('INSERT INTO charges VALUES(?,?,NULL,?)',(request_id,amount,time.time()))
        db.execute('INSERT INTO admin_cost_details(id,job,billing) VALUES(?,?,?)',(request_id,key,billing));db.commit()

def reconcile_cost(run,request_id,usage,rates):
    actual=usage.get('cost');is_byok=usage.get('is_byok') is True
    if not isinstance(actual,(float,int)) or actual<0:return
    upstream=0.
    if is_byok:
        detail=usage.get('cost_details') or {};upstream=detail.get('upstream_inference_cost')
        if not isinstance(upstream,(int,float)) or upstream<0:
            if not all(isinstance(usage.get(k),(int,float)) for k in ('prompt_tokens','completion_tokens')):return
            upstream=usage['prompt_tokens']*float(rates['prompt'])+usage['completion_tokens']*float(rates['completion'])+float(rates.get('request') or 0)
    with init_money(run) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE charges SET actual=? WHERE id=?',(actual+upstream,request_id))
        db.execute('UPDATE admin_cost_details SET openrouter=?,upstream_estimate=? WHERE id=?',(actual,upstream,request_id));db.commit()
