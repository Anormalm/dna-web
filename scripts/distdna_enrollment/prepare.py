"""Select cached legacy Galaxy models before observing any new results."""
import argparse
import sys
from pathlib import Path
from common import cached_snapshot, read, sha, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--research', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--collection', type=Path, required=True)
    p.add_argument('--legacy', type=Path, required=True)
    p.add_argument('--cache', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=12)
    p.add_argument('--max-weight-gib', type=float, default=8)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('Use a new run directory; preparation never overwrites a run')
    sys.path.insert(0, str(args.research / 'src'))
    from distdna.data import CollectionManifest
    bank = read(args.base / 'bank.json')
    original = read(args.collection)
    legacy = read(args.legacy)
    legacy_rows = legacy['models'] if isinstance(legacy, dict) else legacy
    # Legacy identifiers use one underscore between repository owner and name.
    ids = {row['id'] if '/' in row['id'] else row['id'].replace('_', '/', 1) for row in legacy_rows}
    enrolled = {r['modelId'] for r in bank['references']}
    audit, available = [], {}
    for root in args.cache:
        for folder in sorted(root.glob('models--*')):
            model = folder.name[len('models--'):].replace('--', '/')
            if model not in ids or model in enrolled:
                continue
            record = dict(modelId=model, cache=str(folder))
            try:
                if 'tiny-random' in model.lower() or 'test-fixture' in model.lower():
                    raise ValueError('Synthetic test fixture excluded from deployed-model batch')
                item = cached_snapshot(folder, args.max_weight_gib * 2**30)
                record.update(status='ready', **item)
                available.setdefault(model, dict(modelId=model, **item))
            except (OSError, ValueError, KeyError) as e:
                record.update(status='deferred', reason=str(e))
            audit.append(record)
    priority = ['EleutherAI/pythia-14m-seed3', 'EleutherAI/gpt-neo-125m',
                'openai-community/gpt2', 'MBZUAI/LaMini-GPT-124M']
    ordered = sorted(available.values(), key=lambda x: (
        priority.index(x['modelId']) if x['modelId'] in priority else len(priority),
        x['weightBytes'], x['modelId']))
    chosen = ordered[:args.limit]
    if not chosen:
        raise ValueError('No complete cached legacy checkpoints found')
    prompt_ids = bank['protocol']['promptIds']
    prompt_map = {x['prompt_id']: x for x in original['prompts']}
    prompts = [prompt_map[i] for i in prompt_ids]
    # Compare the canonical original evaluation records, including split metadata.
    import hashlib, json
    digest = hashlib.sha256(json.dumps(prompts, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != bank['protocol']['promptSha256']:
        raise ValueError('Prompt text/order does not match existing bank')
    names = [x['modelId'] for x in chosen]
    keep = ['prompt_format', 'plain_text_prefix', 'plain_text_suffix', 'response_boundary',
            'min_new_tokens', 'stop_strings', 'allow_empty_observation']
    meta = {k: original['metadata'][k] for k in keep}
    meta.update(empty_decode_max_attempts=1,
                model_revisions={x['modelId']: x['revision'] for x in chosen},
                model_sources={x['modelId']: dict(repo_id=x['snapshot'], requested_revision=x['revision']) for x in chosen},
                use_safetensors={x['modelId']: x['useSafetensors'] for x in chosen})
    settings = [s for s in original['settings'] if s['setting_id'].startswith('train_')]
    tests = [s for s in original['settings'] if s['setting_id'].startswith('test_')][:2]
    if len(settings) != 8 or len(tests) != 2 or len(prompts) != 20:
        raise ValueError('Expected eight enrollment, two validation settings and 20 prompts')
    manifest = dict(format_version=1, dataset_id=args.output.name, model_ids=names,
                    settings=settings + tests, prompts=original['prompts'], generations=4,
                    random_seed=2026101819, metadata=meta)
    args.output.mkdir(parents=True)
    write(args.output / 'collection.json', manifest)
    CollectionManifest.load(args.output / 'collection.json')
    write(args.output / 'cache-audit.json', dict(legacyRaw=len(ids), alreadyEnrolled=len(ids & enrolled),
          missingCache=sorted(ids - enrolled - set(available)), cachedCandidates=audit,
          eligibleCount=len(available), selected=names,
          selection='Cached main revisions; four fixed pilot models then ascending stored weight size. No outcome selection.'))
    code = Path(__file__).resolve().parent
    frozen = [args.output / 'collection.json', args.base / 'bank.json', args.base / 'enrollment.f32',
              args.base / 'example-queries.json', args.collection, code / 'common.py', code / 'collect.py']
    frozen.extend((args.research / 'src/distdna').rglob('*.py'))
    for item in chosen:
        snap = Path(item['snapshot'])
        frozen.extend(p for p in snap.iterdir() if p.is_file() and p.suffix in ['.json', '.txt', '.model'])
    write(args.output / 'run.json', dict(schema='distdna.enrollment-run.v1', research=str(args.research.resolve()),
          base=str(args.base.resolve()), protocol=bank['protocol'], models=chosen, promptIds=prompt_ids,
          enrollmentSettings=[s['setting_id'] for s in settings], validationSettings=[s['setting_id'] for s in tests],
          expectedPerModel=800, enrollmentResponsesPerModel=640, validationResponsesPerModel=160,
          preflightPerModel=4, maxGpuHours=24, perModelHours=2, maxParallelGpus=4, mode='raw',
          frozenFiles={str(p.resolve()): sha(p) for p in frozen},
          generationControls='Native generation defaults retained as in archived provider; resolved controls saved per checkpoint.',
          validationScope='Two fresh within-prior query fingerprints per added entry; smoke validation, not the 36-cell paper benchmark.'))
    print(f'Prepared {len(names)} models; {len(available)} eligible cached models; {800*len(names)} response records plus {4*len(names)} preflight records', flush=True)
    for x in chosen:
        print(x['modelId'], x['revision'], round(x['weightBytes']/2**30, 2), 'GiB')


if __name__ == '__main__':
    main()
