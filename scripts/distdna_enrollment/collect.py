"""Collect and encode one checkpoint using the archived DistDNA provider."""
import argparse
import fcntl
import os
import sys
import time
from pathlib import Path
from common import expected_rows, load_records, read, row_key, sha, verify_run, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--index', type=int, required=True)
    args = p.parse_args()
    run = args.run.resolve()
    spec = verify_run(run)
    item = spec['models'][args.index]
    model, revision = item['modelId'], item['revision']
    out = run / 'models' / f'{args.index:03d}'
    out.mkdir(parents=True, exist_ok=True)
    lock = (out / 'worker.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    sys.path.insert(0, str(Path(spec['research']) / 'src'))
    from distdna.data import CollectionManifest, SentenceTransformerResponseEncoder
    from distdna.providers.transformers_local import LocalTransformersGenerator
    import numpy as np
    manifest = CollectionManifest.load(run / 'collection.json')
    previous = read(out / 'status.json') if (out / 'status.json').exists() else {}
    elapsed = previous.get('elapsedSeconds', 0)
    start = time.time()
    completed = 0
    def state(status, **kw):
        write(out / 'status.json', dict(status=status, modelId=model, revision=revision,
              pid=os.getpid(), gpu=os.environ.get('CUDA_VISIBLE_DEVICES'), records=completed,
              expected=spec['expectedPerModel'], elapsedSeconds=elapsed + time.time()-start,
              updated=time.time(), **kw))
    g = None
    try:
        state('loading')
        generation_dtype = spec.get('generationDtype', 'float32')
        g = LocalTransformersGenerator(manifest, device='cuda', dtype=generation_dtype,
                                       local_files_only=True, status=lambda x: print(x, flush=True))
        g._ensure_model(model)
        resolved = g._model.generation_config.to_dict()
        settings = []
        for s in manifest.settings:
            effective = dict(resolved, max_new_tokens=s.max_new_tokens, min_new_tokens=g.min_new_tokens,
                 do_sample=s.temperature > 0, temperature=s.temperature, top_p=s.top_p,
                 pad_token_id=g._tokenizer.pad_token_id, eos_token_id=g._tokenizer.eos_token_id, use_cache=True)
            settings.append(dict(settingId=s.setting_id, controls=effective))
        write(out / 'generation-config.json', dict(modelId=model, revision=revision, dtype=g.dtype_name,
              quantizationConfig=g._model.config.to_dict().get('quantization_config'),
              runtime=dict(torch=g._torch.__version__, transformers=g._transformers.__version__),
              settings=settings, responseBoundary=g.response_boundary, promptFormat='plain_text'))
        prompt_map = {x.prompt_id: x for x in manifest.prompts}
        for preflight in (True, False):
            expected = list(expected_rows(manifest, model, spec['promptIds'], preflight))
            path = out / ('preflight.jsonl' if preflight else 'responses.jsonl')
            rows = load_records(path, expected, revision)
            if not preflight:
                completed = len(rows)
            with path.open('a') as stream:
                for template in expected:
                    key = row_key(template)
                    if key in rows:
                        continue
                    if elapsed + time.time()-start > spec['perModelHours'] * 3600:
                        raise TimeoutError('Per-model compute limit reached; records retained for explicit resume')
                    row = dict(template)
                    try:
                        result = g.generate(model, prompt_map[row['prompt_id']],
                                            manifest.setting(row['setting_id']), row['generation_seed'])
                        row.update(response=result.text, metadata=result.metadata, status='ok')
                    except ValueError as e:
                        if 'model produced an empty response after' not in str(e):
                            raise
                        row.update(response='', metadata=dict(model_revision=revision, empty_decode_attempts=1),
                                   status='empty', diagnostic=str(e))
                    import json
                    stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                    stream.flush()
                    rows[key] = row
                    if not preflight:
                        completed = len(rows)
                    if completed % 20 == 0:
                        os.fsync(stream.fileno())
                        state('preflight' if preflight else 'collecting')
                        print(model, completed, '/', spec['expectedPerModel'], flush=True)
            if len(rows) != len(expected):
                raise ValueError('Incomplete response collection')
        g.close()
        g = None
        state('encoding')
        encoder = SentenceTransformerResponseEncoder(model_name=spec['protocol']['encoder'],
                    revision=spec['protocol']['encoderRevision'], device='cuda', batch_size=64)
        expected = list(expected_rows(manifest, model, spec['promptIds']))
        rows = load_records(out / 'responses.jsonl', expected, revision)
        z = encoder.encode([rows[row_key(x)]['response'] for x in expected])
        norms = np.linalg.norm(z, axis=1, keepdims=True)
        if z.shape != (800, spec['protocol']['dimension']) or not np.isfinite(z).all() or (norms <= 0).any():
            raise ValueError('Invalid encoded response tensor')
        z = (z / norms).reshape(10, 20, 4, spec['protocol']['dimension'])
        for name, tensor in [('enrollment.npy', z[:8]), ('queries.npy', z[8:])]:
            with (out / (name + '.tmp')).open('wb') as stream:
                np.save(stream, tensor, allow_pickle=False)
            (out / (name + '.tmp')).replace(out / name)
        artifacts = ['responses.jsonl', 'preflight.jsonl', 'enrollment.npy', 'queries.npy', 'generation-config.json']
        write(out / 'complete.json', dict(modelId=model, revision=revision, protocol=spec['protocol'],
              runSha256=sha(run / 'run.json'), enrollmentSettings=spec['enrollmentSettings'],
              validationSettings=spec['validationSettings'], responseCount=completed,
              emptyResponses=sum(x['status'] == 'empty' for x in rows.values()),
              sha256={f: sha(out / f) for f in artifacts}))
        state('complete')
    except BaseException as e:
        state('failed', error=repr(e))
        raise
    finally:
        if g:
            g.close()


if __name__ == '__main__':
    main()
