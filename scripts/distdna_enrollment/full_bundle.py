"""Merge every verified completed raw job, retaining a full coverage manifest.

Wait mode builds one final bundle after the continuous raw queue reaches terminal
states. Blocked rows remain in coverage.json, never synthetic enrollment points.
Exact identification uses all 8 references. Landmark MDS is display-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import numpy as np
from common import read, sha, verify_run, write
from merge import squared_distances, verified_shards


def queue_rows(run):
    with sqlite3.connect(run / 'queue.sqlite') as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY mode,model')]


def landmarks(first, sigma, count=32):
    # Fixed enrollment-only selection; frozen original gallery defines the axes.
    indices = np.sort(np.random.default_rng(20261018).choice(len(first), min(count, len(first)), replace=False))
    selected = first[indices]
    distances = squared_distances(selected, selected, sigma, 'rbf')
    np.fill_diagonal(distances, 0)
    h = np.eye(len(selected))-1/len(selected)
    values, vectors = np.linalg.eigh(-.5*h@distances@h)
    order = np.argsort(values)[-2:][::-1]
    eigenvalues = values[order]
    if np.any(eigenvalues <= 1e-12):
        raise ValueError('Enrollment landmarks lack two positive MDS dimensions')
    eigenvectors = vectors[:, order]
    for axis in range(2):
        if eigenvectors[np.argmax(abs(eigenvectors[:, axis])), axis] < 0:
            eigenvectors[:, axis] *= -1
    return selected, indices, distances.mean(0), distances.mean(), eigenvectors/np.sqrt(eigenvalues)


def build(run, output):
    spec = read(run / 'queue.json')
    base = Path(spec['base'])
    bank = read(base / 'bank.json')
    if sha(base / 'enrollment.f32') != bank['sha256']:
        raise ValueError('Base bank checksum mismatch')
    rows = queue_rows(run)
    selected = [r for r in rows if r['mode'] == 'raw' and r['state'] == 'complete']
    grouped = {}
    for row in selected:
        grouped.setdefault(row['run'], {})[row['idx']] = row['model']
    # Verify once per source run, then per-shard checksums before accepting it.
    shards = []
    for source, indices in grouped.items():
        source = Path(source)
        source_spec = verify_run(source)
        if source_spec['protocol'] != bank['protocol']:
            raise ValueError('Completed run protocol differs from base gallery')
        for item, folder, train, query in verified_shards(source, source_spec):
            if item['modelId'] in indices.values():
                shards.append((item, folder, train, query))
    if len(shards) != len(selected):
        raise ValueError('Queue completion records disagree with verified completed shards')
    shards.sort(key=lambda x: x[0]['modelId'])
    refs = [dict(r) for r in bank['references']]
    base_ids = list(dict.fromkeys(r['modelId'] for r in refs))
    if set(base_ids) & {x[0]['modelId'] for x in shards}:
        raise ValueError('Refusing to replace existing enrolled identity')
    if not shards:
        raise ValueError('No additional completed checkpoints to integrate')
    if output.exists():
        raise ValueError('Use a new versioned output directory')
    stage = output.with_name(output.name + '.building-' + str(os.getpid()))
    stage.mkdir(parents=True)
    try:
        shutil.copyfile(base / 'enrollment.f32', stage / 'enrollment.f32')
        with (stage / 'enrollment.f32').open('ab') as stream:
            for item, folder, train, _ in shards:
                np.asarray(train, dtype='<f4').tofile(stream)
                for setting in read(folder / 'complete.json')['enrollmentSettings']:
                    refs.append(dict(id=item['modelId']+':'+setting, modelId=item['modelId'],
                                     revision=item['revision'], row=len(refs)))
        shape = (len(refs), *bank['shape'][1:])
        write(stage / 'bank.json', dict(schema=bank['schema'], protocol=bank['protocol'],
            shape=shape, references=refs, sha256=sha(stage / 'enrollment.f32')))
        arrays = np.memmap(stage / 'enrollment.f32', dtype='<f4', mode='r', shape=shape)
        ids = list(dict.fromkeys(r['modelId'] for r in refs))
        by_model = {m:[r for r in refs if r['modelId'] == m] for m in ids}
        first_rows = [by_model[m][0]['row'] for m in ids]
        sigma = bank['protocol']['sigmaSquared']
        fixed = arrays[first_rows[:len(base_ids)]]
        anchor, anchor_indices, column_mean, grand_mean, transform = landmarks(fixed, sigma)
        coordinates = []
        for start in range(0, len(ids), 32):
            distance = squared_distances(arrays[first_rows[start:start+32]], anchor, sigma, 'rbf')
            centered = -.5*(distance - distance.mean(1)[:,None] - column_mean[None,:] + grand_mean)
            coordinates.extend((centered@transform).tolist())
        old_catalog = read(base / 'catalog.json')
        old_models = {m['id']:m for m in old_catalog['models']}
        source_models = {r['name']:r for r in read(run / 'raw-catalog.json')['models']}
        models = []
        for i, name in enumerate(ids):
            row = dict(old_models.get(name, source_models.get(name, {})))
            row.update(id=name, name=name.split('/',1)[-1], signature=None,
                       revision=by_model[name][0]['revision'], x=coordinates[i][0], y=coordinates[i][1])
            models.append(row)
        examples = read(base / 'example-queries.json')
        saved, truths = [], []
        # Fixed lexicographic selection, never based on classification outcomes.
        for item, _, _, query in shards[:4]:
            name = item['modelId']
            query_id = 'full-' + hashlib.sha256((name+':test_00').encode()).hexdigest()[:16]
            examples['queries'].append(dict(id=query_id, samples=query[0].tolist()))
            saved.append(query[0])
            truths.append(dict(queryId=query_id, modelId=name, settingId='test_00'))
        write(stage / 'example-queries.json', examples)
        metadata = dict(old_catalog['metadata'], title=f'{len(ids)}-checkpoint enrolled gallery',
            referenceCount=len(refs), referencesPerModel=8,
            projection='landmark-mds-rbf',
            dataRevision=hashlib.sha256(((stage/'bank.json').read_text()+(stage/'example-queries.json').read_text()).encode()).hexdigest())
        metadata['queries'] = list(metadata['queries']) + [dict(id=t['queryId'], label=f'Full-queue held-out query {i+1}') for i,t in enumerate(truths)]
        write(stage / 'catalog.json', dict(models=models, metadata=metadata))
        write(stage / 'projection.json', dict(method='Landmark classical MDS with out-of-sample extension',
            distance='Exact promptwise biased RBF MMD squared, first enrollment setting',
            landmarkModelIds=[base_ids[int(i)] for i in anchor_indices], seed=20261018,
            coordinatePurpose='Display only; identification retains every enrolled reference'))
        # Independent NumPy oracle for the newly saved queries; bounded reference blocks.
        validation = []
        queries = np.asarray(saved)
        for method in ['linear','rbf']:
            distances = np.empty((len(saved), len(refs)))
            for start in range(0, len(refs), 64):
                distances[:,start:start+64] = squared_distances(queries, arrays[start:start+64], sigma, method)
            for qi, truth in enumerate(truths):
                candidates = []
                for name in ids:
                    best = min(by_model[name], key=lambda r:(distances[qi,r['row']], r['id']))
                    candidates.append(dict(modelId=name, referenceId=best['id'], distance=float(distances[qi,best['row']])))
                candidates.sort(key=lambda r:(r['distance'],r['modelId']))
                validation.append(dict(method=method, modelId=truth['modelId'], settingId='test_00',
                    rank=next(i+1 for i,r in enumerate(candidates) if r['modelId'] == truth['modelId']), predictions=candidates[:3]))
        write(stage / 'validation.json', dict(scope='Saved-query implementation replay; not a full-gallery accuracy benchmark',rows=validation))
        write(stage / 'enrollment-provenance.json', dict(fullQueue=str(run), base=str(base),
            newSavedQueryTruth=truths, sourceShards=[dict(modelId=i['modelId'],folder=str(f),
            completionSha256=sha(f/'complete.json')) for i,f,_,_ in shards],
            originalRowsPreserved=bank['shape'][0]))
        write(stage / 'coverage.json', [dict(mode=r['mode'],modelId=r['model'],state=r['state'],detail=r['detail']) for r in rows])
        del arrays
        stage.rename(output)
        return dict(galleryEntries=len(ids),newEntries=len(shards),output=str(output))
    except BaseException:
        # Only this process's new unpublished staging directory is removed.
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--wait',action='store_true')
    args = parser.parse_args()
    if args.wait:
        pending = {'pending','inspecting','discovered','downloading','ready','running','inherited'}
        while any(r['mode'] == 'raw' and r['state'] in pending for r in queue_rows(args.run)):
            time.sleep(30)
    report = build(args.run.resolve(),args.output.resolve())
    web = Path(__file__).resolve().parents[2]
    result = subprocess.run([str(web/'node_modules/.bin/tsx'),str(web/'scripts/validate_distdna_bundle.ts'),str(args.output.resolve())],
        cwd=web,check=True,capture_output=True,text=True)
    report['typescriptValidation'] = json.loads(result.stdout.strip().splitlines()[-1])
    write(args.run / 'bundle-ready.json',report)
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
