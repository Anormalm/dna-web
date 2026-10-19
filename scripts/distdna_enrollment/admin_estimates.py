"""Metadata-only enrollment quotes. No response generation or weight downloads."""
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import time
import uuid
from common import read
from full_catalog import inspect_model, connection
from hf_access import hf_token
from provider_config import openrouter_key
from chat_queue import public_json, choose_endpoint, estimated_request_cost, API

GIB = 2**30

def valid_model(value):
    if not isinstance(value,str) or len(value)>200 or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:+-]+',value) or '..' in value:
        raise ValueError('Enter an exact organization/model ID, without a URL.')
    return value

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS admin_quotes (id TEXT PRIMARY KEY, model TEXT, mode TEXT, billing TEXT, item TEXT, public TEXT, expires REAL)')
    db.commit()

def summarize_samples(values):
    values=sorted(v for v in values if isinstance(v,(int,float)) and math.isfinite(v) and v>0)
    if not values:return None
    n=len(values)
    return dict(low=values[max(0,int((n-1)*.2))]*.75,high=values[min(n-1,math.ceil((n-1)*.8))]*1.5,samples=n)

def raw_time(run,parameters,gpus):
    values=[]
    # Completed runs include loading, generation and encoding; queue/download time is separate.
    with connection(run) as db:
        rows=db.execute("SELECT item,run,idx FROM jobs WHERE mode='raw' AND state='complete' AND run IS NOT NULL").fetchall()
    for row in rows:
        try:
            item=json.loads(row['item']); p=item.get('parameters')
            if parameters and p and not .5<=p/parameters<=2:continue
            if item.get('gpuCount')!=gpus:continue
            status=read(Path(row['run'])/'models'/f"{row['idx']:03d}"/'status.json')
            values.append(status['elapsedSeconds']/3600)
        except (OSError,KeyError,TypeError,ValueError):continue
    return summarize_samples(values)

def api_observations(run):
    data_path=max(run.glob('api-cost-time-estimate-*.json'), key=lambda p:p.stat().st_mtime, default=run/'api-observations.json')
    try:
        d=read(data_path)
        return dict(prompt=d['observedMeanPromptTokens'],completion=d['observedMeanCompletionTokens'],seconds=d['observedMedianSecondsPerResponse'],at=d['created'])
    except (OSError,KeyError,ValueError):
        return dict(prompt=256,completion=128,seconds=None,at=None)

def estimate(run,request):
    import os
    model=valid_model(request.get('model'))
    mode=request.get('mode'); billing=request.get('billing','openrouter')
    if mode not in ('raw','chat') or billing not in ('openrouter','byok'):raise ValueError('Choose local weights or OpenRouter API.')
    spec=read(run/'queue.json');warnings=[]
    with connection(run) as db:
        schema(db)
        duplicate=db.execute('SELECT key,state FROM jobs WHERE mode=? AND model=?',(mode,model)).fetchone()
        existing=db.execute('SELECT item FROM jobs WHERE mode=? AND model=?',(mode,model)).fetchone()
    public=dict(model=model,mode=mode,billing=billing,responseCount=800,preflightCount=4,existing=dict(duplicate) if duplicate else None,warnings=warnings,estimatedAt=time.time())
    if mode=='raw':
        token=hf_token(run)
        if token:os.environ['HF_TOKEN']=token
        item=json.loads(existing['item']) if existing and existing['item'] else inspect_model(model,spec['caches'],run/'metadata',spec.get('nativeRuntimes'))
        size=item['weightBytes'];gpus=item['gpuCount'];duration=raw_time(run,item.get('parameters'),gpus)
        staged=item.get('snapshot') and Path(item['snapshot']).exists()
        free=shutil.disk_usage(spec['staging']).free
        public.update(revision=item['revision'],parameters=item.get('parameters'),weightBytes=size,gpuCount=gpus,
            downloadBytes=0 if staged else size,temporaryDiskBytes=0 if staged else math.ceil(size*1.1),
            retainedBytesEstimate=12*1024**2,freeDiskBytes=free,diskReserveBytes=spec['diskReserveGiB']*GIB,
            runtimeHours=duration,cleanup='Only owned staged weights are removed after verified completion; fingerprints and responses remain.',
            protocol='Native checkpoint; existing fixed enrollment protocol; no automatic quantization.')
        if not duration:warnings.append('No comparable completed run: runtime estimate unavailable.')
        if free-spec['diskReserveGiB']*GIB<public['temporaryDiskBytes']:warnings.append('Insufficient current staging headroom; job will wait for disk space.')
        warnings.append('Runtime excludes download and queue wait; retained storage is a planning allowance, not a hard bound.')
    else:
        data=public_json(API+'/models/'+model+'/endpoints')['data']
        tag=request.get('provider')
        endpoints=data['endpoints']
        if tag:endpoints=[e for e in endpoints if e.get('tag')==tag]
        item=choose_endpoint(model,endpoints)
        rates=item['pricing'];obs=api_observations(run)
        template=read(run/'collection-template.json');prompt_ids=set(spec['protocol']['promptIds'])
        prompts=[p['text'] for p in template['prompts'] if p['prompt_id'] in prompt_ids]
        expected=804*(obs['prompt']*float(rates['prompt'])+min(128,obs['completion'])*float(rates['completion'])+float(rates.get('request') or 0))
        reserve=804*max(estimated_request_cost(item,p) for p in prompts)
        public.update(provider=item['tag'],providerName=item['provider_name'],inputUsdPerMillion=float(rates['prompt'])*1e6,
            outputUsdPerMillion=float(rates['completion'])*1e6,expectedInferenceUsd=expected,onePassReservationUsd=reserve,
            byokFeeAllowanceUsd=reserve*.05 if billing=='byok' else 0,observationAt=obs['at'],
            runtimeHours=None if obs['seconds'] is None else dict(low=804*obs['seconds']/3600*.5,high=804*obs['seconds']/3600*2,samples=None),
            retainedBytesEstimate=12*1024**2,credentialConfigured=bool(openrouter_key(run)),
            providers=[dict(value=e['tag'],label=e.get('provider_name',e['tag'])) for e in data['endpoints'] if e.get('tag') and e.get('status')==0])
        warnings.append('Estimate uses observed token counts and current endpoint list prices; retries, queue wait and private discounts can change cost/time.')
        if billing=='byok':warnings.append('BYOK bills your provider separately. Reserve includes list-price inference plus up to 5% routing fee. Configure a prioritized BYOK key with shared-capacity fallback disabled in OpenRouter.')
    quote_id=uuid.uuid4().hex;expires=time.time()+900
    public.update(quoteId=quote_id,expiresAt=expires)
    with connection(run) as db:
        schema(db);db.execute('DELETE FROM admin_quotes WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO admin_quotes VALUES(?,?,?,?,?,?,?)',(quote_id,model,mode,billing,json.dumps(item),json.dumps(public),expires));db.commit()
    return public
