"""Keep authorized collectors available and apply stops; no shell or arbitrary commands."""
import argparse,fcntl,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
from common import read,write,sha
from full_catalog import connection,update
from admin_runtime import init_queue,init_money,reconcile,stopped,budget

def held(path):
    with path.open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
        except BlockingIOError:return True

def prepare_chat(run,row):
    import chat_queue
    root=run/'admin-chat-code'/sha(Path(chat_queue.__file__))[:16];root.mkdir(parents=True,exist_ok=True)
    for name in ('chat_queue.py','provider_config.py'):
        source=Path(__file__).with_name(name);target=root/name
        if not target.exists():target.write_bytes(source.read_bytes())
    source=root/'chat_queue.py';s=importlib.util.spec_from_file_location('frozen_prepare_'+root.name,source);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    return m.prepare_job(run,row,json.loads(row['item']))

def loop(run):
    lock=(run/'admin-supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    db=connection(run);init_queue(db);init_money(run).close();children={};last_start={}
    while True:
        reconcile(db)
        for row in db.execute("SELECT * FROM jobs WHERE state='admin_prepare'").fetchall():
            if stopped(db,row['key'],'chat'):continue
            try:update(db,row['key'],state='chat_prepared',run=str(prepare_chat(run,row)),detail=None)
            except Exception:update(db,row['key'],state='blocked_protocol',detail='Provider preparation failed. Inspect compatibility and retry.')
        # Reconcile bulk resumes after a worker has finished shutting down.
        for row in db.execute("SELECT j.* FROM jobs j JOIN admin_job_intents i ON j.key=i.key WHERE j.state='paused' AND i.desired='run'").fetchall():
            from admin_control import resume_state
            from admin_runtime import worker_identity
            if not stopped(db,row['key'],row['mode']) and not (row['pid'] and row['run'] and worker_identity(row['pid'],row['run'])):
                update(db,row['key'],state=resume_state(row))
        for mode,script,command,states in [('raw','full_catalog.py','resume',('pending','discovered','ready','running','inherited')),('chat','chat_queue.py','launch',('chat_prepared','chat_running'))]:
            if mode in children and children[mode].poll() is not None:del children[mode]
            count=db.execute('SELECT count(*) FROM jobs WHERE mode=? AND state IN ('+','.join('?' for _ in states)+')',(mode,*states)).fetchone()[0]
            flag=db.execute('SELECT paused FROM admin_modes WHERE mode=?',(mode,)).fetchone()[0]
            lockname='controller.lock' if mode=='raw' else 'chat-controller.lock'
            if count and not flag and not held(run/lockname) and mode not in children and time.time()-last_start.get(mode,0)>30:
                if mode=='chat' and budget(run)['remainingUsd']<=0:continue
                args=[sys.executable,str(Path(__file__).with_name(script)),command,'--run',str(run)]
                if mode=='chat':args+=['--max-cost-usd',str(budget(run)['capUsd'])]
                with (run/('admin-'+mode+'-controller.log')).open('a') as log:
                    children[mode]=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True,env=dict(os.environ,PYTHONNOUSERSITE='1'))
                last_start[mode]=time.time()
        write(run/'admin-supervisor.json',dict(pid=os.getpid(),updated=time.time(),protocol='Existing frozen workers and saved response records retained'))
        time.sleep(2)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();loop(a.run.resolve())
