"""Continuous full Galaxy enrollment: all catalog rows stay visible, including blockers.

The controller streams metadata discovery, bounded downloads, and GPU jobs. It
has no model-count or total-compute cutoff. Only its own staging files are eligible
for cleanup. Existing caches, completed observations, and source banks are kept.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from common import cached_snapshot, read, sha, verify_run, write
from launch import availability
from provider_config import openrouter_key
from hf_access import refresh as refresh_hf_access

GIB = 2**30


def model_id(row):
    return row.get('name') if '/' in row.get('name', '') else row['id'].replace('_', '/', 1)


def job_key(mode, name):
    return hashlib.sha256(f'{mode}:{name}'.encode()).hexdigest()[:24]


def gpu_count(parameters, weight_bytes, dtype):
    # Preserve float32 generation; allow 12 GiB for activations/cache per 80 GiB GPU.
    source_width = 4 if str(dtype) in ('float32', 'torch.float32') else 2
    weights = parameters * 4 if parameters else weight_bytes * 4 / source_width
    return max(1, math.ceil((weights + 4*GIB) / (64*GIB)))


def select_files(siblings):
    names = {x.rfilename for x in siblings}
    for single, index, safe in [('model.safetensors', 'model.safetensors.index.json', True),
                               ('pytorch_model.bin', 'pytorch_model.bin.index.json', False)]:
        if single in names:
            weights = [single]
            break
        if index in names:
            # Exact index membership is checked before downloading any weights.
            return index, safe
    else:
        raise ValueError('No native Transformers root weight file or shard index')
    return weights, safe


def json_metadata(value):
    """Represent non-finite config metadata without changing the source config."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: json_metadata(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_metadata(v) for v in value]
    return value


def cached_native_snapshot(folder, native_runtimes):
    revision = (folder/'refs/main').read_text().strip()
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Cache revision must be immutable')
    snapshot = folder/'snapshots'/revision
    config = read(snapshot/'config.json')
    method = config.get('quantization_config', {}).get('quant_method')
    if method not in (native_runtimes or {}):
        raise ValueError('No verified native runtime for this cache')
    from transformers.models.auto.configuration_auto import CONFIG_MAPPING_NAMES
    if config.get('model_type') not in CONFIG_MAPPING_NAMES:
        raise ValueError('Cached architecture requires separate review')
    from types import SimpleNamespace
    weights, safe = select_files([SimpleNamespace(rfilename=p.name) for p in snapshot.iterdir() if p.is_file()])
    if isinstance(weights, str):
        weights = sorted(set(read(snapshot/weights)['weight_map'].values()))
    if any('/' in name or '..' in name or not (snapshot/name).is_file() for name in weights):
        raise ValueError('Incomplete cached native weights')
    if not any((snapshot/p).is_file() for p in ['tokenizer.json', 'tokenizer.model', 'vocab.json']):
        raise ValueError('Cached tokenizer is missing')
    size = sum((snapshot/name).stat().st_size for name in weights)
    dtype = config['quantization_config'].get('bnb_4bit_compute_dtype', config.get('dtype', config.get('torch_dtype', 'float16')))
    if dtype not in ('float16', 'bfloat16', 'float32'):
        raise ValueError('Unsupported native compute dtype')
    return dict(revision=revision, snapshot=str(snapshot.resolve()), weightBytes=size,
        useSafetensors=safe, modelType=config.get('model_type'), parameters=None,
        gpuCount=max(1, math.ceil((1.5*size+4*GIB)/(64*GIB))), cached=True, owned=False,
        config=json_metadata(config), files=[], nativeRuntime=method, generationDtype=dtype)


def inspect_model(name, caches, metadata_dir, native_runtimes=None):
    from huggingface_hub import HfApi, hf_hub_download
    for root in caches:
        folder = Path(root) / ('models--' + name.replace('/', '--'))
        try:
            item = cached_snapshot(folder, 2048*GIB)
        except (ValueError, OSError, KeyError):
            try:
                return dict(cached_native_snapshot(folder, native_runtimes), modelId=name)
            except (ValueError, OSError, KeyError):
                continue
        config = read(Path(item['snapshot']) / 'config.json')
        # Downloaded metadata below can refine this conservative native-cache estimate.
        item.update(parameters=None, gpuCount=gpu_count(None, item['weightBytes'],
                    config.get('dtype', config.get('torch_dtype'))), cached=True,
                    owned=False, config=json_metadata(config), files=[])
        return item
    api = HfApi()
    info = api.model_info(name, files_metadata=True, timeout=30)
    if not re.fullmatch('[0-9a-f]{40}', info.sha or ''):
        raise ValueError('Hub returned no immutable revision')
    root = Path(metadata_dir) / job_key('raw', name)
    root.mkdir(parents=True, exist_ok=True)
    def fetch(filename):
        return hf_hub_download(name, filename, revision=info.sha, local_dir=root)
    config = read(fetch('config.json'))
    quantization = config.get('quantization_config') or {}
    quant_method = quantization.get('quant_method')
    if quantization and quant_method not in (native_runtimes or {}):
        raise ValueError('Native quantized checkpoint needs a separate precision/runtime protocol')
    from transformers.models.auto.configuration_auto import CONFIG_MAPPING_NAMES
    if config.get('model_type') not in CONFIG_MAPPING_NAMES:
        raise ValueError(f"Custom or unsupported architecture requires runtime review: {config.get('model_type')}")
    if info.pipeline_tag not in (None, 'text-generation', 'text2text-generation'):
        raise ValueError(f'Non-causal-text pipeline requires a separate collector: {info.pipeline_tag}')
    weights, safe = select_files(info.siblings)
    index = weights if isinstance(weights, str) else None
    if index:
        weights = sorted(set(read(fetch(index))['weight_map'].values()))
    siblings = {x.rfilename: x for x in info.siblings}
    if not weights or any(w not in siblings or '/' in w or '..' in w for w in weights):
        raise ValueError('Invalid or incomplete root weight shard index')
    weight_bytes = sum(siblings[w].size or 0 for w in weights)
    if not weight_bytes:
        raise ValueError('Weight sizes unavailable; resource estimate required')
    # Only tokenizer/config assets and selected root weights, never all repo checkpoints.
    extras = [n for n in siblings if '/' not in n and (
        n.endswith(('.json', '.txt', '.model', '.tiktoken')) and
        not n.startswith(('optimizer', 'trainer', 'training', 'adapter')))]
    files = sorted(set(weights + extras + ([index] if index else [])))
    parameters = info.safetensors.total if info.safetensors else None
    count = gpu_count(parameters, weight_bytes, config.get('dtype', config.get('torch_dtype')))
    if quantization:
        # Stored quantized weights plus room for unquantized modules and buffers.
        count = max(1, math.ceil((1.5*weight_bytes + 4*GIB)/(64*GIB)))
    if count > 8:
        raise ValueError(f'Float32 estimate needs {count} GPUs; available server has 8')
    item = dict(modelId=name, revision=info.sha, snapshot=None, weightBytes=weight_bytes,
                useSafetensors=safe, modelType=config.get('model_type'), parameters=parameters,
                gpuCount=count, cached=False, owned=True, config=json_metadata(config), files=files,
                weightProvenance={w: dict(size=siblings[w].size,
                    sha256=getattr(siblings[w].lfs, 'sha256', None)) for w in weights})
    if quantization:
        dtype = quantization.get('bnb_4bit_compute_dtype', config.get('dtype', config.get('torch_dtype', 'float16')))
        if dtype not in ('float16', 'bfloat16', 'float32'):
            raise ValueError(f'Native quantization has unsupported compute dtype: {dtype}')
        item.update(nativeRuntime=quant_method, generationDtype=dtype)
    write(root / 'discovery.json', item)
    return item


def download_model(name, item, staging):
    from huggingface_hub import snapshot_download
    target = Path(staging) / job_key('raw', name)
    target.mkdir(parents=True, exist_ok=True)
    marker = target / '.distdna-owned.json'
    if not marker.exists():
        write(marker, dict(modelId=name, revision=item['revision']))
    if read(marker) != dict(modelId=name, revision=item['revision']):
        raise ValueError('Staging owner/revision mismatch')
    snapshot_download(name, revision=item['revision'], local_dir=target,
                      allow_patterns=item['files'], max_workers=4)
    # Verify all requested assets have actually materialized before scheduling.
    if any(not (target / f).is_file() for f in item['files']):
        raise ValueError('Download incomplete')
    for filename, meta in item.get('weightProvenance', {}).items():
        path = target / filename
        if path.stat().st_size != meta['size']:
            raise ValueError('Downloaded weight size mismatch')
        if meta.get('sha256') and sha(path) != meta['sha256']:
            raise ValueError('Downloaded weight checksum mismatch')
    return dict(item, snapshot=str(target.resolve()))


def release_owned_staging(spec, db, active):
    """Delete owned weights after verified success; keep caches and retry inputs."""
    root = Path(spec['staging']).resolve()
    released = []
    if not root.exists():
        return released
    rows = db.execute("SELECT * FROM jobs WHERE mode='raw' AND state='complete' ORDER BY updated").fetchall()
    for row in rows:
        if row['key'] in active or not row['item'] or not row['run']:
            continue
        item = json.loads(row['item'])
        if not item.get('owned') or not item.get('snapshot'):
            continue
        target = Path(item['snapshot'])
        if (target.is_symlink() or not target.is_dir() or
                target.parent.resolve() != root or target.name != row['key']):
            continue
        job = Path(row['run'])
        if job.resolve().is_relative_to(target.resolve()) or pid_running(row['pid'], job):
            continue
        marker = target / '.distdna-owned.json'
        if not marker.is_file():
            continue
        try:
            if read(marker) != dict(modelId=row['model'], revision=item['revision']):
                continue
            source = read(job / 'checkpoint-provenance.json')
            if (source['modelId'], source['revision'], source['snapshot']) != (
                    row['model'], item['revision'], str(target)):
                raise ValueError('Checkpoint provenance differs from staged weights')
            output = job / 'models' / f"{row['idx']:03d}"
            if read(output / 'status.json').get('status') != 'complete':
                continue
            from merge import verified_shards
            shards = verified_shards(job, verify_run(job))
            if not any(i['modelId'] == row['model'] and i['revision'] == item['revision']
                       and folder == output for i, folder, _, _ in shards):
                raise ValueError('No verified response/embedding shard for this checkpoint')
            receipt = dict(modelId=row['model'], revision=item['revision'],
                snapshot=str(target), weightBytes=item.get('weightBytes'),
                completionSha256=sha(output / 'complete.json'),
                preserved=['responses', 'embeddings', 'collection manifests', 'checkpoint provenance'])
            shutil.rmtree(target)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            write(job / 'weights-cleanup.json', dict(status='retained', updated=time.time(), error=str(exc)))
            update(db, row['key'], detail='Staged weights retained: cleanup verification or removal failed')
            continue
        write(job / 'weights-cleanup.json', dict(receipt, status='deleted', updated=time.time()))
        update(db, row['key'], detail='Own staged weights deleted after verified completion; responses, embeddings and provenance retained')
        released.append(receipt)
        print(f"Released completed checkpoint weights: {row['model']}", flush=True)
    return released


def connection(run):
    db = sqlite3.connect(run / 'queue.sqlite')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, mode TEXT, model TEXT, '
               'state TEXT, detail TEXT, item TEXT, run TEXT, idx INTEGER DEFAULT 0, '
               'pid INTEGER, attempts INTEGER DEFAULT 0, updated REAL)')
    db.commit()
    return db


def update(db, key, **fields):
    fields['updated'] = time.time()
    db.execute('UPDATE jobs SET ' + ','.join(k+'=?' for k in fields) + ' WHERE key=?',
               [*fields.values(), key])
    db.commit()


def prepare(args):
    run = args.output.resolve()
    if (run / 'queue.json').exists():
        return run
    run.mkdir(parents=True, exist_ok=True)
    base = args.base.resolve()
    bank = read(base / 'bank.json')
    if sha(base / 'enrollment.f32') != bank['sha256']:
        raise ValueError('Base bank checksum mismatch')
    original = read(args.collection)
    prompt_map = {p['prompt_id']: p for p in original['prompts']}
    prompts = [prompt_map[p] for p in bank['protocol']['promptIds']]
    if hashlib.sha256(json.dumps(prompts, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != bank['protocol']['promptSha256']:
        raise ValueError('Original prompt/protocol mismatch')
    code = run / 'code'
    code.mkdir(exist_ok=True)
    for name in ['common.py', 'collect.py', 'full_collect.py']:
        shutil.copyfile(Path(__file__).with_name(name), code / name)
    write(run / 'collection-template.json', original)
    frozen = {str(p.resolve()): sha(p) for p in (args.research / 'src/distdna').rglob('*.py')}
    frozen.update({str(p.resolve()): sha(p) for p in code.glob('*.py')})
    frozen.update({str((base / 'bank.json').resolve()): sha(base / 'bank.json'),
                   str((run / 'collection-template.json').resolve()): sha(run / 'collection-template.json')})
    # The large base bank checksum is verified above; do not hash it per worker.
    spec = dict(schema='distdna.full-catalog.v1', research=str(args.research.resolve()),
                base=str(base), protocol=bank['protocol'], caches=[str(p.resolve()) for p in args.cache],
                staging=str(args.staging.resolve()), python=sys.executable,
                frozenFiles=frozen, chatProvider='openrouter', created=time.time(),
                inheritedRuns=[str(p.resolve()) for p in args.inherit],
                schedule='All catalog entries; cached first, then ascending weight size; no outcome selection',
                expectedPerNewRawModel=800, preflightPerNewRawModel=4,
                maxParallelDownloads=2, maxPreparedJobs=8, perModelHours=72,
                diskReserveGiB=100, modeSeparation='Raw and native-chat enrollment remain separate')
    enrolled = {r['modelId'] for r in bank['references']}
    inherited = {}
    for path in args.inherit:
        for i, item in enumerate(read(path / 'run.json')['models']):
            inherited[item['modelId']] = (str(path.resolve()), i)
    db = connection(run)
    for mode, path in [('raw', args.raw), ('chat', args.chat)]:
        rows = read(path)['models']
        ids = [model_id(row) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Duplicate {mode} catalog identities')
        write(run / f'{mode}-catalog.json', read(path))
        for name in ids:
            state, reason, source, idx = 'pending', None, None, 0
            if mode == 'raw' and name in enrolled:
                state, source = 'enrolled', str(base)
            elif mode == 'raw' and name in inherited:
                state, (source, idx) = 'inherited', inherited[name]
            elif mode == 'chat':
                state, reason = 'awaiting_provider_check', 'OpenRouter catalog and credentials'
            db.execute('INSERT OR IGNORE INTO jobs(key,mode,model,state,detail,run,idx,updated) VALUES(?,?,?,?,?,?,?,?)',
                       (job_key(mode, name), mode, name, state, reason, source, idx, time.time()))
    db.commit()
    db.close()
    write(run / 'queue.json', spec)
    return run


def make_job(run, spec, row, item):
    folder = run / 'jobs' / row['key']
    if (folder / 'run.json').exists():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    original = read(run / 'collection-template.json')
    meta = {k: original['metadata'][k] for k in ['prompt_format', 'plain_text_prefix',
        'plain_text_suffix', 'response_boundary', 'min_new_tokens', 'stop_strings', 'allow_empty_observation']}
    name = row['model']
    meta.update(empty_decode_max_attempts=1, model_revisions={name: item['revision']},
                model_sources={name: dict(repo_id=item['snapshot'], requested_revision=item['revision'])},
                use_safetensors={name: item['useSafetensors']})
    train = [s for s in original['settings'] if s['setting_id'].startswith('train_')]
    tests = [s for s in original['settings'] if s['setting_id'].startswith('test_')][:2]
    if len(train) != 8 or len(tests) != 2:
        raise ValueError('Collection schedule must contain 8 enrollment and 2 query settings')
    write(folder / 'collection.json', dict(format_version=1, dataset_id=row['key'], model_ids=[name],
          settings=train+tests, prompts=original['prompts'], generations=4, random_seed=2026101819, metadata=meta))
    # Keep provenance even if this controller later releases its own downloaded weights.
    snapshot = Path(item['snapshot'])
    for source in snapshot.iterdir():
        if source.is_file() and source.suffix in ('.json', '.txt', '.model', '.tiktoken'):
            target = folder / 'checkpoint-metadata' / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
    write(folder / 'checkpoint-provenance.json', item)
    frozen = dict(spec['frozenFiles'])
    native = spec.get('nativeRuntimes', {}).get(item.get('nativeRuntime'))
    if native:
        frozen.update(native['frozenFiles'])
    for source in [folder / 'collection.json', folder / 'checkpoint-provenance.json',
                   *(folder / 'checkpoint-metadata').iterdir()]:
        frozen[str(source.resolve())] = sha(source)
    write(folder / 'run.json', dict(schema='distdna.enrollment-run.v1', research=spec['research'],
        base=spec['base'], protocol=spec['protocol'], models=[dict(item, modelId=name)],
        promptIds=spec['protocol']['promptIds'], enrollmentSettings=[s['setting_id'] for s in train],
        validationSettings=[s['setting_id'] for s in tests], expectedPerModel=800,
        enrollmentResponsesPerModel=640, validationResponsesPerModel=160, preflightPerModel=4,
        perModelHours=spec['perModelHours'], mode='raw', frozenFiles=frozen,
        generationDtype=item.get('generationDtype', 'float32'),
        nativeRuntime=native,
        generationControls=('Native saved quantization and compute dtype retained; resolved configuration saved'
            if native else 'Native defaults retained; resolved configuration saved; float32, no quantization'),
        validationScope='Two independent within-prior query banks; not the 36-cell paper benchmark'))
    return folder


def check_chat(run, db):
    try:
        with urllib.request.urlopen('https://openrouter.ai/api/v1/models', timeout=30) as response:
            catalog = json.load(response)
        write(run / 'openrouter-models.json', catalog)
        available = {x['id']: x for x in catalog['data']}
        for row in db.execute("SELECT * FROM jobs WHERE mode='chat' AND state IN ('awaiting_provider_check','awaiting_credentials','awaiting_chat_collector','unavailable')").fetchall():
            if row['model'] not in available:
                state, detail = 'unavailable', 'Exact model ID is absent from the current OpenRouter catalog; no substitution'
            elif not {'temperature', 'top_p'}.issubset(available[row['model']].get('supported_parameters', [])):
                state, detail = 'blocked_protocol', 'Provider catalog does not advertise both temperature and top_p'
            else:
                state, detail = 'awaiting_credentials', 'Set OPENROUTER_API_KEY or provide its configuration-file path; separate native-chat collection required'
                if openrouter_key(run):
                    state, detail = 'awaiting_chat_collector', 'Credential present; native-chat collector/provenance must be finalized before collection'
            update(db, row['key'], state=state, detail=detail)
    except Exception as exc:
        write(run / 'chat-check-error.json', dict(error=repr(exc), updated=time.time()))


def pid_running(pid, folder):
    try:
        cmd = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode()
        return str(folder) in cmd and 'full_collect.py' in cmd
    except (OSError, TypeError):
        return False


def summarize(run, db, active):
    try:
        credential_present, credential_error = bool(openrouter_key(run)), None
    except ValueError as exc:
        credential_present, credential_error = False, str(exc)
    rows = db.execute('SELECT * FROM jobs').fetchall()
    counts, records, running = {}, 0, []
    for row in rows:
        counts.setdefault(row['mode'], {})[row['state']] = counts.setdefault(row['mode'], {}).get(row['state'], 0)+1
        if row['run'] and row['state'] not in ('enrolled',):
            path = Path(row['run']) / 'models' / f"{row['idx']:03d}" / 'status.json'
            if path.exists():
                state = read(path)
                records += state.get('records', 0)
                if state['status'] in ('loading', 'preflight', 'collecting', 'encoding'):
                    running.append({k: state.get(k) for k in ['modelId', 'status', 'records', 'expected', 'gpu', 'pid']})
    write(run / 'status.json', dict(pid=os.getpid(), updated=time.time(), catalogEntries=len(rows),
        counts=counts, collectedNewResponses=records, workers=running,
        managedWorkers=len(active), provider='OpenRouter', providerCredentialPresent=credential_present, providerConfigurationError=credential_error))
    # Full inventory makes every unavailable/unsupported row inspectable.
    write(run / 'inventory.json', [dict(mode=r['mode'], modelId=r['model'], state=r['state'],
          detail=r['detail'], run=r['run'], index=r['idx']) for r in rows])


def run_queue(run):
    spec = read(run / 'queue.json')
    lock = (run / 'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    db = connection(run)
    for filename, digest in spec['frozenFiles'].items():
        if sha(filename) != digest:
            raise ValueError(f'Frozen worker input changed: {filename}')
    db.execute("UPDATE jobs SET state='pending' WHERE state='inspecting'")
    db.execute("UPDATE jobs SET state='discovered' WHERE state='downloading'")
    db.commit()
    check_chat(run, db)
    active, inspection, downloads, idle = {}, {}, {}, {}
    executor = ThreadPoolExecutor(max_workers=6)
    def interrupted(signum, frame):
        raise InterruptedError(f'Controller signal {signum}; children retain cooperative GPU locks')
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    last_summary = 0
    hf_access_state = {}
    try:
        while True:
            refresh_hf_access(run, db, hf_access_state)
            # Observe externally running phase-2 work, without collecting duplicates.
            for row in db.execute("SELECT * FROM jobs WHERE state IN ('inherited','running')").fetchall():
                folder = Path(row['run'])
                output = folder / 'models' / f"{row['idx']:03d}"
                status = read(output / 'status.json') if (output / 'status.json').exists() else {}
                if (output / 'complete.json').exists() and status.get('status') == 'complete':
                    update(db, row['key'], state='complete', detail='Collected; pending bank integration')
                elif status.get('status') == 'failed':
                    update(db, row['key'], state='failed', detail=status.get('error'))
                elif row['state'] == 'running' and row['key'] not in active and not pid_running(row['pid'], folder):
                    update(db, row['key'], state='ready', detail='Controller resumed after worker exit; records retained')
            for key, (child, handles, folder, started) in list(active.items()):
                if child.poll() is not None:
                    state = read(folder / 'models/000/status.json') if (folder / 'models/000/status.json').exists() else {}
                    if state.get('status') != 'complete':
                        update(db, key, state='failed', detail=state.get('error', f'Worker exit {child.returncode}'))
                    for handle in handles:
                        handle.close()
                    del active[key]
                elif time.time()-started > spec['perModelHours']*3600:
                    os.killpg(child.pid, signal.SIGTERM)
            for pool, kind in [(inspection, 'inspect'), (downloads, 'download')]:
                for future, key in list(pool.items()):
                    if not future.done():
                        continue
                    del pool[future]
                    try:
                        item = future.result()
                        update(db, key, state='ready' if item.get('snapshot') else 'discovered',
                               item=json.dumps(item), detail=None)
                    except Exception as exc:
                        # Network failures are distinguishable and explicitly retryable.
                        label = 'download_failed' if kind == 'download' else 'blocked'
                        update(db, key, state=label, detail=str(exc)[:1600])
            pending = db.execute("SELECT * FROM jobs WHERE state='pending' AND mode='raw' ORDER BY model").fetchall()
            # Prioritize complete caches, then small catalog sizes, without dropping any entry.
            def priority(row):
                cached = any((Path(c) / ('models--'+row['model'].replace('/', '--')) / 'refs/main').exists() for c in spec['caches'])
                size = re.search(r'(\d+(?:\.\d+)?)([bBmM])(?:\b|[-_])', row['model'])
                estimate = float(size[1])*(1000 if size[2].lower() == 'b' else 1) if size else 10000
                return (not cached, estimate, row['model'])
            for row in sorted(pending, key=priority)[:max(0, 4-len(inspection))]:
                update(db, row['key'], state='inspecting')
                future = executor.submit(inspect_model, row['model'], spec['caches'], run / 'metadata', spec.get('nativeRuntimes'))
                inspection[future] = row['key']
            ready = db.execute("SELECT * FROM jobs WHERE state='ready' AND mode='raw'").fetchall()
            discovered = db.execute("SELECT * FROM jobs WHERE state='discovered' ORDER BY updated").fetchall()
            discovered.sort(key=lambda r: json.loads(r['item'])['weightBytes'])
            Path(spec['staging']).mkdir(parents=True, exist_ok=True)
            release_owned_staging(spec, db, active)
            reserve = spec['diskReserveGiB']*GIB + sum(json.loads(db.execute('SELECT item FROM jobs WHERE key=?',(k,)).fetchone()[0])['weightBytes'] for k in downloads.values())
            for row in discovered:
                if len(downloads) >= 2 or len(downloads)+len(ready) >= spec['maxPreparedJobs']:
                    break
                item = json.loads(row['item'])
                if shutil.disk_usage(spec['staging']).free < reserve + item['weightBytes']:
                    break
                reserve += item['weightBytes']
                update(db, row['key'], state='downloading')
                downloads[executor.submit(download_model, row['model'], item, spec['staging'])] = row['key']
            av = availability()
            for gpu, available in av.items():
                idle[gpu] = idle.get(gpu, 0)+1 if available else 0
            ready.sort(key=lambda r: (r['attempts'], r['updated']))
            for row in ready:
                item = json.loads(row['item'])
                candidates = [g for g in sorted(av) if av[g] and idle.get(g, 0) >= 2]
                if len(candidates) < item['gpuCount']:
                    # Reserve free cards for the oldest large model instead of starving it.
                    break
                handles, selected = [], []
                for gpu in candidates:
                    handle = (Path(spec['research']) / f'.local/experiments/.gpu-{gpu}.lock').open('a')
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        handle.close()
                        continue
                    handles.append(handle)
                    selected.append(gpu)
                    if len(selected) == item['gpuCount']:
                        break
                if len(selected) != item['gpuCount'] or not all(availability().get(g, False) for g in selected):
                    for handle in handles:
                        handle.close()
                    continue
                try:
                    folder = make_job(run, spec, row, item)
                    env = dict(os.environ, CUDA_VISIBLE_DEVICES=','.join(map(str, selected)), PYTHONNOUSERSITE='1',
                          OPENBLAS_NUM_THREADS='4', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4',
                          HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
                    native = spec.get('nativeRuntimes', {}).get(item.get('nativeRuntime'))
                    worker = run / 'code/full_collect.py'
                    if native:
                        worker = Path(native['workerDirectory']) / 'full_collect.py'
                        env['PYTHONPATH'] = native['pythonPath']
                    with (folder / 'worker.log').open('a') as stream:
                        child = subprocess.Popen([spec['python'], str(worker),
                                '--run', str(folder), '--index', '0'], env=env, stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                pass_fds=tuple(h.fileno() for h in handles))
                    active[row['key']] = (child, handles, folder, time.time())
                    update(db, row['key'], state='running', run=str(folder), pid=child.pid,
                           attempts=row['attempts']+1, detail=None)
                    for gpu in selected:
                        av[gpu] = False
                    print(f"Started {row['model']} on GPUs {selected}", flush=True)
                except BaseException:
                    for handle in handles:
                        handle.close()
                    raise
            if time.time()-last_summary > 15:
                summarize(run, db, active)
                last_summary = time.time()
            waiting = db.execute("SELECT COUNT(*) FROM jobs WHERE mode='raw' AND state IN ('pending','inspecting','discovered','downloading','ready','running','inherited')").fetchone()[0]
            if not waiting and not active:
                summarize(run, db, active)
                print('All raw entries have a terminal recorded status; inspect blockers before declaring full coverage', flush=True)
                break
            time.sleep(3)
    finally:
        summarize(run, db, active)
        executor.shutdown(wait=False, cancel_futures=True)
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('start')
    for name in ['research', 'base', 'collection', 'raw', 'chat', 'output', 'staging']:
        init.add_argument('--'+name, type=Path, required=True)
    init.add_argument('--cache', type=Path, action='append', required=True)
    init.add_argument('--inherit', type=Path, action='append', default=[])
    resume = sub.add_parser('resume')
    resume.add_argument('--run', type=Path, required=True)
    status = sub.add_parser('status')
    status.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(read(args.run / 'status.json'), indent=2))
    else:
        run_queue(prepare(args) if args.command == 'start' else args.run.resolve())


if __name__ == '__main__':
    main()
