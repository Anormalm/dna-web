"""Frozen, resumable raw-protocol enrollment utilities. No network downloads."""
import hashlib
import json
import os
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with tmp.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_run(run):
    run = Path(run)
    spec = read(run / 'run.json')
    for filename, expected in spec['frozenFiles'].items():
        if sha(filename) != expected:
            raise ValueError(f'Frozen input changed: {filename}; use a new run directory')
    return spec


def cached_snapshot(folder, max_bytes):
    """Accept complete main snapshots only; never substitute an old training step."""
    folder = Path(folder)
    revision = (folder / 'refs/main').read_text().strip()
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('main is not an immutable commit SHA')
    snapshot = folder / 'snapshots' / revision
    config = read(snapshot / 'config.json')
    if config.get('quantization_config') or config.get('auto_map'):
        raise ValueError('quantized or custom-code checkpoint deferred')
    if not any((snapshot / p).is_file() for p in ['tokenizer.json', 'tokenizer.model', 'vocab.json']):
        raise ValueError('tokenizer assets missing')
    weights = None
    for single, index, safe in [('model.safetensors', 'model.safetensors.index.json', True),
                                ('pytorch_model.bin', 'pytorch_model.bin.index.json', False)]:
        if (snapshot / single).is_file():
            weights = [snapshot / single]
        elif (snapshot / index).is_file():
            weights = [snapshot / p for p in set(read(snapshot / index)['weight_map'].values())]
        if weights:
            if not all(p.is_file() for p in weights):
                raise ValueError('incomplete weight shards')
            break
    if not weights:
        raise ValueError('weights missing')
    size = sum(p.stat().st_size for p in weights)
    if size > max_bytes:
        raise ValueError('checkpoint exceeds initial cached-batch size limit')
    return dict(revision=revision, snapshot=str(snapshot.resolve()), weightBytes=size,
                useSafetensors=safe, modelType=config.get('model_type'))


def expected_rows(manifest, model, prompt_ids, preflight=False):
    from distdna.data import generation_seed
    settings = manifest.settings[:2] if preflight else manifest.settings
    prompts = prompt_ids[:2] if preflight else prompt_ids
    for setting in settings:
        for prompt in prompts:
            for draw in range(1 if preflight else manifest.generations):
                yield dict(model_id=model, setting_id=setting.setting_id, prompt_id=prompt,
                           generation_index=draw, generation_seed=generation_seed(
                               manifest.random_seed + (100000 if preflight else 0),
                               model, setting.setting_id, prompt, draw),
                           temperature=setting.temperature, top_p=setting.top_p,
                           stage='preflight' if preflight else 'responses')


def row_key(row):
    return (row['setting_id'], row['prompt_id'], row['generation_index'])


def load_records(path, expected, revision):
    """Reject corrupt tails, duplicate keys and incompatible resumes explicitly."""
    expected = {row_key(row): row for row in expected}
    rows = {}
    if not Path(path).exists():
        return rows
    with Path(path).open() as stream:
        for line in stream:
            row = json.loads(line)
            key = row_key(row)
            if key in rows or key not in expected:
                raise ValueError('Duplicate or unexpected response record')
            if any(row.get(k) != v for k, v in expected[key].items()):
                raise ValueError('Response does not match frozen collection protocol')
            if row['metadata']['model_revision'] != revision:
                raise ValueError('Response checkpoint revision mismatch')
            if not isinstance(row['response'], str) or row['status'] not in ['ok', 'empty']:
                raise ValueError('Invalid response')
            if (row['response'] == '') != (row['status'] == 'empty'):
                raise ValueError('Empty response status mismatch')
            rows[key] = row
    return rows
