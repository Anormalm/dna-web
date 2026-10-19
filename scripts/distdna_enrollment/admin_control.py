"""Authenticated server adapter. All input is validated JSON, never shell code."""
import argparse,fcntl,json,math,os,sqlite3,sys,time
from pathlib import Path
from admin_estimates import estimate,schema
from admin_runtime import ControlError,init_queue,init_money,budget,audit,signal_worker,worker_identity
from full_catalog import connection,job_key
from common import read
from provider_config import openrouter_key
from hf_access import hf_token

TERMINAL=('complete','chat_complete','enrolled')
def resume_state(row):
    item=json.loads(row['item'] or '{}')
    if row['mode']=='chat':return 'chat_prepared' if row['run'] and (Path(row['run'])/'chat-run.json').exists() else 'admin_prepare'
    if item.get('snapshot') and Path(item['snapshot']).is_dir():return 'ready'
    if item.get('revision') and item.get('files'):return 'discovered'
    return 'pending'

def checked_key(db,key):
    if not isinstance(key,str) or len(key)>100:raise ControlError('Invalid job ID.')
    row=db.execute('SELECT * FROM jobs WHERE key=?',(key,)).fetchone()
    if row is None:raise ControlError('Job not found.')
    return row

def status(run):
    with connection(run) as db:
        init_queue(db)
        modes=dict(db.execute('SELECT mode,paused FROM admin_modes').fetchall())
        intents={r['key']:dict(r) for r in db.execute('SELECT * FROM admin_job_intents')}
        actions=[dict(r) for r in db.execute('SELECT at,actor,action,target FROM admin_actions ORDER BY id DESC LIMIT 15')]
    try:
        heartbeat=read(run/'admin-supervisor.json');online=time.time()-heartbeat['updated']<20
    except (OSError,ValueError,KeyError):online=False
    return dict(budget=budget(run),modes=modes,intents=intents,actions=actions,supervisorOnline=online,
        openrouterConfigured=bool(openrouter_key(run)),huggingFaceConfigured=bool(hf_token(run)),
        byokSetupUrl='https://openrouter.ai/settings/integrations',
        protocol=dict(responses=800,preflight=4,enrollmentResponses=640,queryResponses=160,maxOutputTokens=128),
        stopPolicy='Completed responses are retained. API calls already in flight may finish and be billed. Local GPU jobs release memory after shutdown.')

def dispatch(run,action,request,actor=0):
    if action=='estimate':return estimate(run,request)
    if action=='controls':return status(run)
    if not isinstance(request,dict):raise ControlError('Invalid request.')
    with (run/'admin-control.lock').open('a') as lock, connection(run) as db:
        fcntl.flock(lock,fcntl.LOCK_EX)
        init_queue(db);schema(db)
        if action=='add':
            quote=db.execute('SELECT * FROM admin_quotes WHERE id=?',(request.get('quoteId'),)).fetchone()
            if quote is None or quote['expires']<time.time():raise ControlError('Estimate expired. Inspect the model again.')
            if quote['billing']=='byok' and request.get('byokConfirmed') is not True:raise ControlError('Confirm the prioritized BYOK key and disabled shared-capacity fallback first.')
            exists=db.execute('SELECT key FROM jobs WHERE mode=? AND model=?',(quote['mode'],quote['model'])).fetchone()
            if exists:
                old=db.execute('SELECT * FROM jobs WHERE key=?',(exists['key'],)).fetchone()
                if not old['run'] and old['state'] in ('blocked_protocol','unavailable','blocked','awaiting_provider_check'):
                    db.execute("UPDATE jobs SET item=?,state='paused',updated=? WHERE key=?",(quote['item'],time.time(),old['key']))
                    db.execute("INSERT OR REPLACE INTO admin_job_intents VALUES(?,'paused',NULL,?,?)",(old['key'],quote['billing'],int(bool(request.get('byokConfirmed')))))
                    audit(db,actor,'refresh-metadata',old['key']);db.commit()
                return dict(key=exists['key'],alreadyExists=True)
            if quote['billing']=='byok' and request.get('byokConfirmed') is not True:raise ControlError('Confirm the prioritized BYOK key and disabled shared-capacity fallback first.')
            key=job_key(quote['mode'],quote['model'])
            db.execute("INSERT INTO jobs(key,mode,model,state,item,updated) VALUES(?,?,?,'paused',?,?)",(key,quote['mode'],quote['model'],quote['item'],time.time()))
            db.execute('INSERT INTO admin_job_intents VALUES(?,?,?,?,?)',(key,'paused',None,quote['billing'],int(bool(request.get('byokConfirmed')))))
            audit(db,actor,'add',key,dict(model=quote['model'],billing=quote['billing'],quoteId=quote['id']));db.commit()
            return dict(key=key,state='paused',message='Added and stopped. Use Start when ready.')
        if action=='budget':
            cap=request.get('capUsd');version=request.get('version')
            if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or not 0<=cap<=100000:raise ControlError('Enter a budget between $0 and $100,000.')
            with init_money(run) as money:
                money.execute('BEGIN IMMEDIATE');old=money.execute('SELECT * FROM admin_budget WHERE id=1').fetchone()
                if version!=old['version']:raise ControlError('Budget changed in another session. Refresh before saving.')
                money.execute('UPDATE admin_budget SET cap=?,version=version+1 WHERE id=1',(cap,))
                money.execute('INSERT INTO admin_budget_audit(at,actor,previous,value) VALUES(?,?,?,?)',(time.time(),actor,old['cap'],cap));money.commit()
            audit(db,actor,'budget','chat',dict(capUsd=cap));db.commit();return budget(run)
        if action=='job':
            row=checked_key(db,request.get('key'));op=request.get('operation')
            if op not in ('start','stop'):raise ControlError('Choose Start or Stop.')
            if row['state'] in TERMINAL:return dict(key=row['key'],state=row['state'],message='This entry is already complete.')
            intent=db.execute('SELECT * FROM admin_job_intents WHERE key=?',(row['key'],)).fetchone()
            if op=='start':
                if row['mode']=='chat':
                    if not openrouter_key(run):raise ControlError('Configure the server OpenRouter key first.')
                    if budget(run)['remainingUsd']<=0:raise ControlError('Budget exhausted. Increase the cap before starting.')
                mode=db.execute('SELECT paused FROM admin_modes WHERE mode=?',(row['mode'],)).fetchone()
                if mode[0]:raise ControlError('This collection is stopped. Resume the collection first.')
                if row['state'] in ('running','chat_running','downloading','inspecting'):return dict(key=row['key'],state=row['state'])
                if row['pid'] and row['run'] and worker_identity(row['pid'],row['run']):raise ControlError('The worker is still stopping. Retry after it exits.')
                state=resume_state(row)
            else:state='paused'
            db.execute("INSERT INTO admin_job_intents(key,desired,resume_state) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET desired=excluded.desired,resume_state=excluded.resume_state",(row['key'],'run' if op=='start' else 'paused',row['state']))
            db.execute('UPDATE jobs SET state=?,detail=?,retry_after=0,updated=? WHERE key=?',(state,'Administrator '+op+' requested; completed records retained',time.time(),row['key']))
            audit(db,actor,op,row['key']);db.commit()
            signaled=op=='stop' and row['pid'] and row['run'] and signal_worker(row['pid'],row['run'])
            return dict(key=row['key'],state=state,stopping=bool(signaled),message='Queued for collection.' if op=='start' else 'Stop requested; completed records are retained.')
        if action=='collection':
            mode=request.get('mode');op=request.get('operation')
            if mode not in ('raw','chat') or op not in ('start','stop'):raise ControlError('Choose a collection and Start or Stop.')
            db.execute('UPDATE admin_modes SET paused=? WHERE mode=?',(int(op=='stop'),mode))
            if op=='start':
                for row in db.execute("SELECT j.* FROM jobs j LEFT JOIN admin_job_intents i ON j.key=i.key WHERE j.mode=? AND j.state='paused' AND COALESCE(i.desired,'run')='run'",(mode,)).fetchall():
                    if row['pid'] and row['run'] and worker_identity(row['pid'],row['run']):continue
                    db.execute('UPDATE jobs SET state=?,updated=? WHERE key=?',(resume_state(row),time.time(),row['key']))
            audit(db,actor,op+'-collection',mode);db.commit()
            return dict(mode=mode,paused=op=='stop',message='Collection '+('resumed.' if op=='start' else 'stop requested; active API requests may finish.'))
    raise ControlError('Unknown administrator action.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    try:
        payload=json.load(sys.stdin)
        print(json.dumps(dispatch(a.run.resolve(),payload['action'],payload.get('input',{}),payload.get('actor',0)),allow_nan=False))
    except ControlError as exc:print(json.dumps({'error':str(exc),'status':409}));sys.exit(1)
    except Exception:print(json.dumps({'error':'Collector operation could not complete. Check the exact model ID, credentials, access and current status.','status':503}));sys.exit(1)
