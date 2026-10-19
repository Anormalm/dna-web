"""Resumable OpenRouter native-chat enrollment, separate from raw checkpoints.

Provider routes and request controls are frozen before sampling. No provider/model
fallback, no empty-output resampling. Credentials never enter manifests or logs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from common import read, write, sha, expected_rows, row_key, load_records
from provider_config import openrouter_key
from full_catalog import connection, update
from admin_runtime import init_queue, reconcile, stopped, budget

API = 'https://openrouter.ai/api/v1'


class ApiFailure(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'OpenRouter HTTP {status}: {message}')


def public_json(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def choose_endpoint(model, endpoints):
    compatible = [e for e in endpoints if e.get('status') == 0 and e.get('tag') and
                  {'temperature','top_p'}.issubset(e.get('supported_parameters',[])) and
                  {'max_tokens','max_completion_tokens'} & set(e.get('supported_parameters',[])) and
                  e.get('pricing',{}).get('prompt') is not None and
                  e.get('pricing',{}).get('completion') is not None]
    if not compatible:
        raise ValueError('No available endpoint supports temperature, top_p and output-token limits')
    creator = model.split('/')[0]
    def priority(e):
        price = Decimal(e['pricing']['prompt'])+Decimal(e['pricing']['completion'])
        native = e['tag'].split('/')[0] == creator
        return (not native, -(e.get('uptime_last_1d') or 0), price, e['tag'])
    return sorted(compatible,key=priority)[0]


def build_payload(spec, row, prompt):
    endpoint = spec['endpoint']
    supported = endpoint['supported_parameters']
    payload = dict(model=spec['modelId'], messages=[dict(role='user',content=prompt)],
        temperature=row['temperature'], top_p=row['top_p'], stream=False,
        provider=dict(only=[endpoint['tag']], order=[endpoint['tag']], allow_fallbacks=False,
                      require_parameters=True))
    length_field = 'max_tokens' if 'max_tokens' in supported else 'max_completion_tokens'
    payload[length_field] = 128
    if 'seed' in supported:
        payload['seed'] = row['generation_seed'] % (2**31)
    if 'reasoning' in supported:
        payload['reasoning'] = dict(enabled=False)
    return payload


def reserve_cost(run, request_id, amount, cap):
    # Failed/time-out calls retain their reservation: a server might still bill them.
    with sqlite3.connect(run/'chat-spend.sqlite',timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS charges (id TEXT PRIMARY KEY, reserved REAL, actual REAL, created REAL)')
        db.execute('BEGIN IMMEDIATE')
        total = db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges').fetchone()[0]
        if total+amount > cap:
            raise ApiFailure('budget', f'Configured ${cap:.2f} collection ceiling reached; completed records retained')
        db.execute('INSERT INTO charges VALUES(?,?,NULL,?)',(request_id,amount,time.time()))
        db.commit()


def record_cost(run, request_id, usage):
    actual = usage.get('cost')
    if isinstance(actual,(int,float)) and actual >= 0:
        with sqlite3.connect(run/'chat-spend.sqlite',timeout=30) as db:
            db.execute('UPDATE charges SET actual=? WHERE id=?',(actual,request_id))


def estimated_request_cost(endpoint, prompt):
    rates = endpoint['pricing']
    # UTF-8 byte count plus a conservative chat-overhead allowance, not a token estimate.
    return float(Decimal(len(prompt.encode())+1024)*Decimal(rates['prompt']) +
                 Decimal(128)*Decimal(rates['completion']) + Decimal(rates.get('request','0')))


def completion(run, spec, row, prompt, key, cap):
    payload = build_payload(spec,row,prompt)
    for attempt in range(2):
        request_id = hashlib.sha256((spec['modelId']+json.dumps(row,sort_keys=True)+str(time.time_ns())).encode()).hexdigest()
        reserve_cost(run,request_id,estimated_request_cost(spec['endpoint'],prompt),cap)
        request = urllib.request.Request(API+'/chat/completions',data=json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json',
                     'X-OpenRouter-Title':'DistDNA local enrollment'},method='POST')
        try:
            with urllib.request.urlopen(request,timeout=120) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            # Never print HTTP headers, credential values, or arbitrary response bodies.
            if exc.code in (429,500,502,503,504) and attempt == 0:
                time.sleep(5)
                continue
            raise ApiFailure(exc.code,'request failed; no model/provider substitution') from None
        except (TimeoutError,urllib.error.URLError):
            if attempt == 0:
                time.sleep(5)
                continue
            raise ApiFailure('transport','request timed out or connection failed') from None
        if data.get('error'):
            raise ApiFailure(data['error'].get('code','response'),'provider returned an error')
        usage = data.get('usage') or {}
        record_cost(run,request_id,usage)
        provider = data.get('provider')
        if provider != spec['endpoint']['provider_name']:
            raise ApiFailure('provenance','served provider differs from frozen endpoint')
        choices = data.get('choices') or []
        if len(choices) != 1:
            raise ApiFailure('response','expected one completion')
        message = choices[0].get('message') or {}
        text = message.get('content')
        refusal = message.get('refusal')
        if text is None and isinstance(refusal,str):
            text = refusal
        if text is None:
            text = ''
        if not isinstance(text,str):
            raise ApiFailure('response','expected textual content')
        text = text.strip()
        return dict(row,response=text,status='ok' if text else 'empty',metadata=dict(
            model_revision=spec['revision'],requested_model=spec['modelId'],served_model=data.get('model'),
            provider=provider,endpoint_tag=spec['endpoint']['tag'],response_id=data.get('id'),
            created=data.get('created'),system_fingerprint=data.get('system_fingerprint'),
            usage=usage,request_seed=payload.get('seed'),request_id=request_id,
            finish_reason=choices[0].get('finish_reason'),refusal=bool(refusal),
            collection_mode='native_chat',max_output_tokens=128))


def prepare_job(run, row, endpoint):
    queue_spec = read(run/'queue.json')
    folder = run/'chat-jobs'/row['key']
    if (folder/'chat-run.json').exists():
        return folder
    folder.mkdir(parents=True,exist_ok=True)
    original = read(run/'collection-template.json')
    settings = [s for s in original['settings'] if s['setting_id'].startswith('train_')]
    settings += [s for s in original['settings'] if s['setting_id'].startswith('test_')][:2]
    protocol = dict(queue_spec['protocol'])
    protocol['preprocessing'] = 'native-chat; one user message; fixed provider route; at most 128 output tokens; sentence-transformers; no instruction prefix; L2 per response; no mean renormalization'
    protocol['calibration'] = 'Transferred frozen raw-response bandwidth: '+protocol['calibration']
    spec = dict(schema='distdna.chat-run.v1',modelId=row['model'],endpoint=endpoint,protocol=protocol,
        research=queue_spec['research'],promptIds=protocol['promptIds'],enrollmentSettings=[s['setting_id'] for s in settings[:8]],
        validationSettings=[s['setting_id'] for s in settings[8:]],created=time.time(),
        identity='Provider-served catalog entry observed during collection; not an independently pinned weight checkpoint',
        expectedPerModel=800,preflightPerModel=4,mode='chat',encoderDevice='cpu',
        sourceSha256={str(Path(__file__).resolve()):sha(Path(__file__)),
                      str(Path(__file__).with_name('provider_config.py').resolve()):sha(Path(__file__).with_name('provider_config.py'))})
    spec['revision'] = 'service-observation-'+hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()[:24]
    manifest = dict(format_version=1,dataset_id='native-chat-'+row['key'],model_ids=[row['model']],
        settings=settings,prompts=original['prompts'],generations=4,random_seed=2026101819,
        metadata=dict(model_revisions={row['model']:spec['revision']},prompt_format='native_chat'))
    write(folder/'collection.json',manifest)
    spec['collectionSha256'] = sha(folder/'collection.json')
    write(folder/'chat-run.json',spec)
    return folder


def collect(run, folder, cap):
    spec = read(folder/'chat-run.json')
    if sha(folder/'collection.json') != spec['collectionSha256']:
        raise ValueError('Frozen chat collection changed')
    for filename,digest in spec['sourceSha256'].items():
        if sha(filename) != digest:
            raise ValueError('Frozen chat worker changed; resume with its archived code')
    lockfile = folder/'worker.lock'
    lockfile.touch(exist_ok=True)
    lock = lockfile.open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    key = openrouter_key(run)
    if not key: raise ApiFailure('auth','OpenRouter key is absent')
    out = folder/'models/000'
    out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(Path(spec['research'])/'src'))
    from distdna.data import CollectionManifest, SentenceTransformerResponseEncoder
    import numpy as np
    manifest = CollectionManifest.load(folder/'collection.json')
    prompt_map = {p.prompt_id:p.text for p in manifest.prompts}
    completed = 0
    start = time.time()
    def state(status,**kwargs):
        write(out/'status.json',dict(status=status,mode='chat',modelId=spec['modelId'],revision=spec['revision'],
            pid=os.getpid(),gpu=None,records=completed,expected=800,updated=time.time(),elapsedSeconds=time.time()-start,**kwargs))
    try:
        served_model = None
        for preflight in (True,False):
            expected = list(expected_rows(manifest,spec['modelId'],spec['promptIds'],preflight))
            path = out/('preflight.jsonl' if preflight else 'responses.jsonl')
            rows = load_records(path,expected,spec['revision'])
            # Detect provider alias changes within a collected model, including resumes.
            for saved in rows.values():
                observed = saved['metadata']['served_model']
                if served_model is not None and observed != served_model:
                    raise ApiFailure('provenance','served model changed within enrollment')
                served_model = observed
            if not preflight: completed=len(rows)
            state('preflight' if preflight else 'collecting')
            missing = [r for r in expected if row_key(r) not in rows]
            # Submit bounded windows so a failed first request does not dispatch 800 calls.
            with path.open('a') as stream, ThreadPoolExecutor(max_workers=4) as pool:
                for offset in range(0,len(missing),4):
                    futures=[pool.submit(completion,run,spec,r,prompt_map[r['prompt_id']],key,cap) for r in missing[offset:offset+4]]
                    errors=[]
                    for future in as_completed(futures):
                        try:
                            value=future.result()
                            observed=value['metadata']['served_model']
                            if not observed or (served_model is not None and observed != served_model):
                                raise ApiFailure('provenance','served model changed within enrollment')
                            served_model=observed
                            stream.write(json.dumps(value,ensure_ascii=False)+'\n');stream.flush()
                            rows[row_key(value)]=value
                            if not preflight: completed=len(rows)
                        except Exception as exc: errors.append(exc)
                    os.fsync(stream.fileno())
                    state('preflight' if preflight else 'collecting')
                    if errors: raise errors[0]
            if len(rows)!=len(expected): raise ValueError('Incomplete response bank')
        state('encoding')
        encoder=SentenceTransformerResponseEncoder(model_name=spec['protocol']['encoder'],
            revision=spec['protocol']['encoderRevision'],device='cpu',batch_size=32)
        expected=list(expected_rows(manifest,spec['modelId'],spec['promptIds']))
        rows=load_records(out/'responses.jsonl',expected,spec['revision'])
        z=encoder.encode([rows[row_key(r)]['response'] for r in expected])
        norms=np.linalg.norm(z,axis=1,keepdims=True)
        if z.shape!=(800,768) or not np.isfinite(z).all() or (norms<=0).any(): raise ValueError('Invalid response embeddings')
        z=(z/norms).reshape(10,20,4,768)
        for filename,tensor in [('enrollment.npy',z[:8]),('queries.npy',z[8:])]:
            with (out/(filename+'.tmp')).open('wb') as stream: np.save(stream,tensor,allow_pickle=False)
            (out/(filename+'.tmp')).replace(out/filename)
        write(out/'generation-config.json',dict(endpoint=spec['endpoint'],servedModel=served_model,
            requestExample={k:v for k,v in build_payload(spec,expected[0],'<prompt>').items() if k!='messages'},
            settings=[s.to_dict() if hasattr(s,'to_dict') else vars(s) for s in manifest.settings],
            seedPolicy='Per-response seed where endpoint supports it; fresh provider RNG otherwise',
            minOutputTokens=None,responseBoundary='Provider EOS or 128 output tokens; whitespace stripped'))
        artifacts=['enrollment.npy','queries.npy','preflight.jsonl','responses.jsonl','generation-config.json']
        write(out/'complete.json',dict(schema='distdna.chat-shard.v1',modelId=spec['modelId'],revision=spec['revision'],
            protocol=spec['protocol'],runSha256=sha(folder/'chat-run.json'),responseCount=completed,
            emptyResponses=sum(not r['response'] for r in rows.values()),
            enrollmentSettings=spec['enrollmentSettings'],validationSettings=spec['validationSettings'],
            sha256={f:sha(out/f) for f in artifacts}))
        state('complete')
    except BaseException as exc:
        # All request-layer errors are sanitized and contain no key/response headers.
        state('failed',error=str(exc),errorCode=getattr(exc,'status',None))
        raise


def prepare_all(run):
    db=connection(run)
    targets=db.execute("SELECT * FROM jobs WHERE mode='chat' AND state IN ('awaiting_credentials','awaiting_chat_collector','awaiting_provider_check') ORDER BY model").fetchall()
    def prepare_one(row):
        data=public_json(API+'/models/'+row['model']+'/endpoints')['data']
        endpoint=choose_endpoint(row['model'],data['endpoints'])
        folder=prepare_job(run,row,endpoint)
        return str(folder),endpoint
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(prepare_one,r):r for r in targets}
        for future in as_completed(futures):
            row=futures[future]
            try:
                folder,endpoint=future.result()
                update(db,row['key'],state='chat_prepared',run=folder,item=json.dumps(endpoint),detail=None)
            except Exception as exc:
                update(db,row['key'],state='blocked_protocol',detail=str(exc)[:600])
    db.close()


def frozen_worker(folder):
    """Resume with the exact collection code recorded in this job's manifest."""
    sources = read(folder/'chat-run.json')['sourceSha256']
    candidates = [Path(p) for p in sources if Path(p).name == 'chat_queue.py']
    if len(candidates) != 1 or sha(candidates[0]) != sources[str(candidates[0])]:
        raise ValueError('Frozen chat worker is missing or has changed')
    return candidates[0]


def verify_encoder_runtime():
    # Check before spending on generation. Keep the venv executable's original
    # path: resolving its symlink silently switches workers to system Python.
    subprocess.run([sys.executable, '-c', 'import numpy, sentence_transformers'],
                   check=True, env=dict(os.environ, PYTHONNOUSERSITE='1'))


def recover_encoding_failures(db):
    for row in db.execute("SELECT * FROM jobs WHERE mode='chat' AND state='chat_failed'").fetchall():
        status_file = Path(row['run'])/'models/000/status.json'
        status = read(status_file) if status_file.exists() else {}
        missing_encoder = any(message in status.get('error', '') for message in (
            "No module named 'sentence_transformers'",
            'sentence-transformers is required; install distdna[embedding]'))
        if row['attempts'] < 3 and status.get('records') == 800 and missing_encoder:
            update(db, row['key'], state='chat_prepared',
                   detail='Encoding saved responses in the verified experiment environment')


def recover_transient_failures(db):
    """Bounded, delayed retries keep the frozen route, samples and spend ledger."""
    for row in db.execute("SELECT * FROM jobs WHERE mode='chat' AND state='chat_failed'").fetchall():
        if row['attempts'] >= 4 or not row['run']:
            continue
        path = Path(row['run'])/'models/000/status.json'
        status = read(path) if path.exists() else {}
        if status.get('errorCode') not in (429,500,502,503,504,'transport'):
            continue
        delay = min(3600,300*2**max(0,row['attempts']-1))
        retry_at = max(row['retry_after'], status.get('updated',time.time())+delay)
        update(db,row['key'],state='chat_prepared',retry_after=retry_at,
               detail='Retrying transient provider failure after cooldown; frozen route and saved records retained')


def launch(run,cap):
    lock=(run/'chat-controller.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if not openrouter_key(run): raise ValueError('Configure the private OpenRouter key first')
    verify_encoder_runtime()
    prepare_all(run)
    db=connection(run)
    init_queue(db)
    active={}
    try:
        while True:
            reconcile(db)
            cap=budget(run)['capUsd']
            for k,(child,folder) in list(active.items()):
                if child.poll() is None: continue
                status=read(folder/'models/000/status.json') if (folder/'models/000/status.json').exists() else {}
                update(db,k,state='chat_complete' if status.get('status')=='complete' else 'chat_failed',detail=status.get('error',None if child.returncode==0 else f'Worker exited {child.returncode}'))
                del active[k]
                if status.get('errorCode') in (401,402,'auth','budget'):
                    raise RuntimeError('Chat queue stopped for authentication/credit/budget issue; observations retained')
            # Recover surviving children after a controller restart without duplicate calls.
            for row in db.execute("SELECT * FROM jobs WHERE mode='chat' AND state='chat_running'").fetchall():
                if row['key'] in active: continue
                status_path=Path(row['run'])/'models/000/status.json'
                status=read(status_path) if status_path.exists() else {}
                if status.get('status') in ('complete','failed'):
                    update(db,row['key'],state='chat_complete' if status['status']=='complete' else 'chat_failed',detail=status.get('error'))
                elif not Path(f"/proc/{row['pid']}").exists():
                    update(db,row['key'],state='chat_prepared',detail='Resuming saved response records')
            recover_encoding_failures(db)
            recover_transient_failures(db)
            running=db.execute("SELECT COUNT(*) FROM jobs WHERE mode='chat' AND state='chat_running'").fetchone()[0]
            pending=db.execute("SELECT * FROM jobs WHERE mode='chat' AND state='chat_prepared' AND retry_after<=? ORDER BY CASE WHEN model='openai/gpt-4o-mini' THEN 0 ELSE 1 END,model",(time.time(),)).fetchall()
            # Two models, four bounded in-flight calls per model.
            for row in pending[:max(0,2-running)]:
                if stopped(db,row['key'],'chat') or budget(run)['remainingUsd']<=0: continue
                folder=Path(row['run'])
                with (folder/'worker.log').open('a') as stream:
                    child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('admin_chat_worker.py')),'--run',str(run),
                        '--job',str(folder),'--key',row['key'],'--billing', (db.execute('SELECT billing FROM admin_job_intents WHERE key=?',(row['key'],)).fetchone() or ['openrouter'])[0]],stdin=subprocess.DEVNULL,stdout=stream,
                        stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,PYTHONNOUSERSITE='1',
                        OPENBLAS_NUM_THREADS='4',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',TOKENIZERS_PARALLELISM='false'))
                active[row['key']]=(child,folder)
                update(db,row['key'],state='chat_running',pid=child.pid,attempts=row['attempts']+1)
                print('Started chat model',row['model'],'provider',json.loads(row['item'])['tag'],flush=True)
            counts=dict(db.execute("SELECT state,COUNT(*) FROM jobs WHERE mode='chat' GROUP BY state").fetchall())
            spend=0
            if (run/'chat-spend.sqlite').exists():
                with sqlite3.connect(run/'chat-spend.sqlite') as money:
                    spend=money.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM charges').fetchone()[0]
            write(run/'chat-status.json',dict(pid=os.getpid(),updated=time.time(),counts=counts,
                spendingCeilingUsd=cap,chargedOrReservedUsd=spend,credential='loaded privately',
                collection='Native chat; fixed route; 640 enrollment + 160 query responses per entry'))
            waiting=db.execute("SELECT COUNT(*) FROM jobs WHERE mode='chat' AND state='chat_prepared'").fetchone()[0]
            if not waiting and not running and not active: break
            time.sleep(5)
    finally:
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','launch','collect'])
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--job',type=Path)
    parser.add_argument('--max-cost-usd',type=float,default=100)
    args=parser.parse_args()
    if args.command=='prepare': prepare_all(args.run.resolve())
    elif args.command=='launch': launch(args.run.resolve(),args.max_cost_usd)
    else: collect(args.run.resolve(),args.job.resolve(),args.max_cost_usd)


if __name__=='__main__':
    main()
