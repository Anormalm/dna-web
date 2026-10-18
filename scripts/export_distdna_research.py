#!/usr/bin/env python3
"""Export a small, reproducible web demo and aggregate grid from a DistDNA run.

Requires numpy and the archived wide-decoding-115-20260924 run, its sibling
continuous-expanded-20260917 and wide-grid-main-analysis-20260925 directories.
No response text, model weights, local paths or fitted classifiers are exported.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n')


def vectors(x):
    # Explicit serialization precision; distances checked against the unrounded bank.
    return np.round(np.asarray(x, dtype=np.float64), 7).tolist()


def distance(q, r, sigma_squared, method):
    if method == 'linear':
        return float(np.mean(np.sum((q.mean(1) - r.mean(1)) ** 2, axis=1)))
    def kernel(a, b):
        return np.exp(-np.sum((a[:, None, :] - b[None, :, :]) ** 2, axis=-1) / (2 * sigma_squared)).mean()
    return max(0., float(np.mean([kernel(a, a) + kernel(b, b) - 2 * kernel(a, b) for a, b in zip(q, r)])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('server/distdna/data'))
    args = parser.parse_args()
    w, out = args.run, args.output
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads((w / 'protocol.json').read_text())
    collection = json.loads((w / 'enrollment_collection.json').read_text())
    analysis = w.parent / 'wide-grid-main-analysis-20260925'
    calibration = w.parent / 'continuous-expanded-20260917/analysis/bge-calibration.json'
    sigma = json.loads(calibration.read_text())['sigma']
    indices = [0, 1, 6, 7]  # Two base/instruction pairs; fixed before examining results.
    selected = [spec['model_ids'][i] for i in indices]
    prompt_ids = [p['prompt_id'] for p in spec['evaluation_prompts']]
    prompt_sha = hashlib.sha256(json.dumps(spec['evaluation_prompts'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    protocol = dict(encoder='BAAI/bge-base-en-v1.5', encoderRevision='a5beb1e3e68b9ab74eb54cfd186867f64f240e1a', dimension=768,
        normalized=True, preprocessing='plain-text; sentence-transformers; no instruction prefix; L2 per response; no mean renormalization',
        promptIds=prompt_ids, promptSha256=prompt_sha, sigmaSquared=sigma ** 2,
        calibration=f'disjoint calibration prompts; 9200 responses; frozen median-positive squared distance; sha256:{digest(calibration)}')
    train = np.load(w / 'fits/enrollment-bge.npy', mmap_mode='r')
    bank = np.load(w / 'bge.npy', mmap_mode='r')
    references, reference_arrays = [], []
    for s in range(2):
        for i, model in zip(indices, selected):
            x = np.asarray(train[s * len(spec['model_ids']) + i], dtype=np.float64)
            references.append(dict(id=f'{model}:train_{s:02d}', modelId=model,
                revision=collection['metadata']['model_revisions'][model], samples=vectors(x)))
            reference_arrays.append(x)
    queries, query_arrays, truth = [], [], []
    setting_id = 'wide_T090_P070'
    records_per_model = len(spec['settings']) * len(prompt_ids) * 4
    for k, (i, model) in enumerate(zip(indices, selected)):
        records = [json.loads(line) for line in (w / 'models' / f'{i:03d}' / 'responses.jsonl').open()]
        assert len(records) == records_per_model and all(r['model_id'] == model for r in records)
        lookup = {(r['setting_id'], r['prompt_id'], r['generation_index']): i * records_per_model + j for j, r in enumerate(records)}
        qi = [[lookup[setting_id, p, d] for d in range(4)] for p in prompt_ids]
        x = np.asarray(bank[qi], dtype=np.float64)
        query_id = f'heldout-{k+1:02d}'
        queries.append(dict(id=query_id, samples=vectors(x)))
        query_arrays.append(x)
        truth.append(dict(queryId=query_id, modelId=model))
    write(out / 'example-gallery.json', dict(schema='distdna.gallery.v1', title='Four-checkpoint research example',
        description='Two SmolLM2 and two Qwen2.5 entries from the archived study. Two independently collected enrollment settings per entry; 20 prompts and 4 responses per prompt. This smaller gallery demonstrates scoring; it is not the 115-entry benchmark.',
        protocol=protocol, references=references))
    write(out / 'example-queries.json', dict(schema='distdna.queries.v1', title='Four held-out grid queries', protocol=protocol, queries=queries))
    golden = []
    max_error = 0.
    for method in ['linear', 'rbf']:
        for q, raw in zip(queries, query_arrays):
            rows = []
            for ref, original in zip(references, reference_arrays):
                d = distance(np.array(q['samples']), np.array(ref['samples']), sigma**2, method)
                max_error = max(max_error, abs(d - distance(raw, original, sigma**2, method)))
                rows.append(dict(referenceId=ref['id'], modelId=ref['modelId'], distance=d))
            golden.append(dict(method=method, queryId=q['id'], distances=rows))
    assert max_error < 2e-6
    write(out / 'example-provenance.json', dict(source='DistDNA archived wide-decoding-115-20260924 collection',
        selection='Fixed roster indices 0, 1, 6, 7; first two enrollment settings; query cell T=0.9, p=0.7. No outcome-based selection.',
        querySetting=dict(temperature=.9, top_p=.7), enrollmentSettings=collection['settings'][:2], groundTruth=truth,
        embeddingDecimalPlaces=7, maxDistanceSerializationError=max_error,
        inputSha256={str(p.relative_to(w.parent)): digest(p) for p in [w/'protocol.json', w/'fits/enrollment-bge.npy', w/'bge.npy', calibration]},
        outputSha256={name: digest(out/name) for name in ['example-gallery.json', 'example-queries.json']}))
    # Independent NumPy oracle for the TypeScript engine.
    write(out / 'example-distances.json', golden)
    labels = {'exact_mmd': 'DistDNA-RBF', 'mean_l2': 'DistDNA-Linear', 'llmdna_d512_s42': 'LLM-DNA (paper adaptation)', 'phylolm_prefix4': 'PhyloLM-4char'}
    settings = {s['setting_id']: s for s in spec['settings']}
    cells = []
    for r in csv.DictReader((analysis/'cell_metrics.csv').open()):
        if r['method'] in labels:
            s = settings[r['setting']]
            cells.append(dict(method=r['method'], readout=r['readout'], temperature=s['temperature'], topP=s['top_p'],
                top1=float(r['top1']), top3=float(r['top3']), macroF1=float(r['macro_f1'])))
    assert len(cells) == 4*2*36
    write(out/'benchmark.json', dict(schema='distdna.benchmark.v1', models=115, settings=36, prompts=20, responsesPerPrompt=4,
        enrollmentReferencesPerModel=8, enrollment='T uniform on (0.7, 1.0]; top-p uniform on [0.8, 1.0]; independent draws',
        aggregation='Equal checkpoint-entry weighting; BGE and MPNet scores averaged for semantic methods; PhyloLM uses one text construction.',
        baseline='LLM-DNA uses the paper shared-bank, 512-coordinate projected-mean adaptation. These are not the subsequent native v1.0.1 reruns.',
        sourceSha256=digest(analysis/'cell_metrics.csv'), methods=labels, cells=cells))
    print(f'Exported {len(references)} references, {len(queries)} queries and {len(cells)} metric cells. Maximum distance serialization error {max_error:.3g}.')


if __name__ == '__main__':
    main()
