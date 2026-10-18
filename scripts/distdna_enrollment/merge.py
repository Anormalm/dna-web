"""Build an immutable gallery from complete shards, then validate fresh queries.

No outcome-based exclusions: every verified complete shard is merged. The original
bank is never overwritten. Gallery geometry uses enrollment only. No SVM is fitted.
"""
import argparse
import hashlib
import os
import re
import shutil
from pathlib import Path
import numpy as np
from scipy.spatial.distance import cdist
from common import read, sha, verify_run, write


def squared_distances(query, reference, sigma2, method):
    """Exact prompt-averaged biased MMD squared, or mean-vector L2 squared."""
    q, r = np.asarray(query, dtype=np.float64), np.asarray(reference, dtype=np.float64)
    nq, t, rq, d = q.shape
    nr, tr, rr, dr = r.shape
    if (t, d) != (tr, dr) or not np.isfinite(q).all() or not np.isfinite(r).all():
        raise ValueError('Incompatible or nonfinite fingerprint arrays')
    if method == 'linear':
        return cdist(q.mean(2).reshape(nq, -1), r.mean(2).reshape(nr, -1), 'sqeuclidean') / t
    if method != 'rbf' or sigma2 <= 0:
        raise ValueError('Invalid distance construction')
    result = np.zeros((nq, nr))
    def self_kernel(x):
        distances = np.sum((x[:, :, None, :] - x[:, None, :, :])**2, axis=-1)
        return np.exp(-distances / (2*sigma2)).mean((1, 2))
    for prompt in range(t):
        a, b = q[:, prompt], r[:, prompt]
        cross = np.exp(-cdist(a.reshape(nq*rq, d), b.reshape(nr*rr, d), 'sqeuclidean') / (2*sigma2))
        cross = cross.reshape(nq, rq, nr, rr).mean((1, 3))
        result += (self_kernel(a)[:, None] + self_kernel(b)[None, :] - 2*cross) / t
    return np.maximum(result, 0)


def verified_shards(run, spec):
    found = []
    for i, item in enumerate(spec['models']):
        folder = run / 'models' / f'{i:03d}'
        path = folder / 'complete.json'
        if not path.exists():
            continue
        info = read(path)
        if (info['modelId'], info['revision'], info['protocol'], info['runSha256']) != (
                item['modelId'], item['revision'], spec['protocol'], sha(run / 'run.json')):
            raise ValueError(f'Completed shard provenance mismatch: {folder}')
        required = {'responses.jsonl', 'preflight.jsonl', 'enrollment.npy', 'queries.npy', 'generation-config.json'}
        if set(info['sha256']) != required or info['responseCount'] != spec['expectedPerModel']:
            raise ValueError(f'Incomplete shard manifest: {folder}')
        if info['enrollmentSettings'] != spec['enrollmentSettings'] or info['validationSettings'] != spec['validationSettings']:
            raise ValueError('Shard setting schedule mismatch')
        for name, digest in info['sha256'].items():
            if sha(folder / name) != digest:
                raise ValueError(f'Shard checksum mismatch: {folder / name}')
        train = np.load(folder / 'enrollment.npy', allow_pickle=False)
        query = np.load(folder / 'queries.npy', allow_pickle=False)
        dims = (len(spec['promptIds']), 4, spec['protocol']['dimension'])
        for array, count in [(train, 8), (query, 2)]:
            if array.shape != (count, *dims) or not np.isfinite(array).all():
                raise ValueError('Shard tensor shape or finiteness failure')
            if not np.allclose(np.linalg.norm(array, axis=-1), 1, atol=2e-5, rtol=0):
                raise ValueError('Shard vectors must be normalized per response')
        found.append((item, folder, train, query))
    return found


def build(run, output):
    spec = verify_run(run)
    base = Path(spec['base'])
    bank = read(base / 'bank.json')
    if bank['protocol'] != spec['protocol'] or sha(base / 'enrollment.f32') != bank['sha256']:
        raise ValueError('Base bank protocol/checksum mismatch')
    refs = [dict(x) for x in bank['references']]
    old_ids = {r['modelId'] for r in refs}
    if len(refs) != bank['shape'][0] or sorted(r['row'] for r in refs) != list(range(len(refs))):
        raise ValueError('Base bank row mapping is not a permutation')
    shards = verified_shards(run, spec)
    if not shards:
        raise ValueError('No completed shards to merge')
    if old_ids & {x[0]['modelId'] for x in shards}:
        raise ValueError('Refusing to replace an existing enrolled model')
    if output.exists():
        raise ValueError('Output must be a new versioned directory')
    staging = output.with_name(output.name + f'.building-{os.getpid()}')
    staging.mkdir(parents=True)
    try:
        shutil.copyfile(base / 'enrollment.f32', staging / 'enrollment.f32')
        with (staging / 'enrollment.f32').open('ab') as stream:
            for item, _, train, _ in shards:
                np.asarray(train, dtype='<f4').tofile(stream)
                for setting in spec['enrollmentSettings']:
                    refs.append(dict(id=f"{item['modelId']}:{setting}", modelId=item['modelId'],
                                     revision=item['revision'], row=len(refs)))
        shape = [len(refs), *bank['shape'][1:]]
        write(staging / 'bank.json', dict(schema=bank['schema'], protocol=bank['protocol'], shape=shape,
                                        references=refs, sha256=sha(staging / 'enrollment.f32')))
        examples = read(base / 'example-queries.json')
        added_examples = []
        if examples:  # Synthetic merge tests can omit a web query bank.
            existing_query_ids = {q['id'] for q in examples['queries']}
            for item, _, _, query_array in shards[:4]:
                query_id = 'extension-' + hashlib.sha256((item['modelId'] + ':test_00').encode()).hexdigest()[:16]
                if query_id in existing_query_ids:
                    raise ValueError('Duplicate saved query ID')
                examples['queries'].append(dict(id=query_id, samples=query_array[0].tolist()))
                added_examples.append(dict(queryId=query_id, modelId=item['modelId'], settingId='test_00'))
            examples['title'] = 'Independent archived and newly collected query examples'
        write(staging / 'example-queries.json', examples)
        enrollment = np.memmap(staging / 'enrollment.f32', dtype='<f4', mode='r', shape=tuple(shape))
        ids = list(dict.fromkeys(r['modelId'] for r in refs))
        first_rows = [next(r['row'] for r in refs if r['modelId'] == m) for m in ids]
        matrix = squared_distances(enrollment[first_rows], enrollment[first_rows],
                                   bank['protocol']['sigmaSquared'], 'rbf')
        np.fill_diagonal(matrix, 0)
        h = np.eye(len(ids)) - np.ones((len(ids), len(ids))) / len(ids)
        values, vectors = np.linalg.eigh(-.5*h@matrix@h)
        ix = np.argsort(values)[-2:][::-1]
        xy = vectors[:, ix]*np.sqrt(np.maximum(values[ix], 0))
        for col in range(2):
            if xy[np.argmax(abs(xy[:, col])), col] < 0:
                xy[:, col] *= -1
        old_catalog = read(base / 'catalog.json')
        old_models = {x['id']: x for x in old_catalog['models']}
        models = []
        for i, model in enumerate(ids):
            org, name = model.split('/', 1)
            family = re.match('[A-Za-z]+', name)
            param = re.search(r'(\d+(?:\.\d+)?[BbMm])', name)
            row = dict(old_models[model]) if model in old_models else dict(id=model, name=name,
                organization=org, family=family.group() if family else org,
                parameters=param.group() if param else None, isInstruct=bool(re.search(r'instruct|chat|it$', name, re.I)),
                signature=None, revision=next(r['revision'] for r in refs if r['modelId'] == model))
            row.update(x=float(xy[i, 0]), y=float(xy[i, 1]))
            models.append(row)
        counts = [sum(r['modelId'] == m for r in refs) for m in ids]
        if len(set(counts)) != 1:
            raise ValueError('This catalog expects an equal number of references per entry')
        meta = dict(old_catalog['metadata'], title=f'{len(ids)}-checkpoint enrolled gallery',
                    referenceCount=len(refs), referencesPerModel=counts[0],
                    dataRevision=hashlib.sha256(((staging / 'bank.json').read_text() +
                                                (staging / 'example-queries.json').read_text()).encode()).hexdigest())
        meta['queries'] = list(old_catalog['metadata'].get('queries', []))
        used_labels = {q['label'] for q in meta['queries']}
        for q in added_examples:
            number = 1
            while f'New held-out query {number}' in used_labels:
                number += 1
            label = f'New held-out query {number}'
            used_labels.add(label)
            meta['queries'].append(dict(id=q['queryId'], label=label))
        write(staging / 'catalog.json', dict(models=models, metadata=meta))
        write(staging / 'geometry-distances.json', dict(ids=ids, squaredRbf=matrix.tolist()))
        # Query outcomes never enter enrollment or projection. Report all added entries.
        query = np.concatenate([q for _, _, _, q in shards])
        truth = [item['modelId'] for item, _, _, _ in shards for _ in range(2)]
        query_settings = spec['validationSettings']*len(shards)
        scores = []
        for method in ['linear', 'rbf']:
            distances = squared_distances(query, enrollment, bank['protocol']['sigmaSquared'], method)
            for j, true_id in enumerate(truth):
                candidates = []
                for model in ids:
                    selected = sorted((r for r in refs if r['modelId'] == model), key=lambda r: (distances[j, r['row']], r['id']))
                    best = selected[0]
                    candidates.append(dict(modelId=model, distance=float(distances[j, best['row']]), referenceId=best['id']))
                candidates.sort(key=lambda r: (r['distance'], r['modelId']))
                rank = next(i+1 for i, r in enumerate(candidates) if r['modelId'] == true_id)
                scores.append(dict(method=method, modelId=true_id, settingId=query_settings[j], rank=rank,
                                   predictions=candidates[:3]))
        write(staging / 'validation.json', dict(scope=spec['validationScope'], galleryEntries=len(ids),
               addedEntries=len(shards), queryCount=len(truth), rows=scores,
               metrics={m: dict(top1=100*np.mean([x['rank'] == 1 for x in scores if x['method'] == m]),
                               top3=100*np.mean([x['rank'] <= 3 for x in scores if x['method'] == m]))
                        for m in ['linear', 'rbf']},
               outcomePolicy='All complete shards included regardless of accuracy; no new 115-entry benchmark claim.'))
        write(staging / 'enrollment-provenance.json', dict(runSha256=sha(run / 'run.json'),
               baseBankSha256=sha(base / 'bank.json'), mode=spec['mode'],
               added=[dict(modelId=item['modelId'], revision=item['revision'],
                           completeSha256=sha(folder / 'complete.json')) for item, folder, _, _ in shards],
               collectionSettings=read(run / 'collection.json')['settings'],
               newSavedQueryTruth=added_examples,
               exampleSelection='First held-out query for up to four complete entries in frozen roster order; no outcome selection.',
               controls=spec['generationControls'], pipelineSha256=sha(Path(__file__))))
        # Closed file handles before atomic rename on POSIX; keep source artifacts unchanged.
        del enrollment
        staging.rename(output)
    except BaseException:
        # Retain the incomplete build for diagnosis; it is never served automatically.
        raise
    print(f'Built {len(ids)} entries / {len(refs)} references in {output}', flush=True)
    print(read(output / 'validation.json')['metrics'], flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    build(args.run.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
