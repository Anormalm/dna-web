"""Data-integrity checks for cached collection and resumability; no GPU required."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/distdna_enrollment'))
from common import cached_snapshot, load_records, sha, write
import numpy as np
from merge import build, squared_distances, verified_shards


class EnrollmentTests(unittest.TestCase):
    def test_missing_main_is_not_replaced_by_old_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'snapshots' / ('a'*40)).mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                cached_snapshot(folder, 1000)

    def test_missing_shard_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            snap = folder / 'snapshots' / ('a'*40)
            snap.mkdir(parents=True)
            (folder / 'refs').mkdir()
            (folder / 'refs/main').write_text('a'*40)
            write(snap / 'config.json', {'model_type': 'gpt2'})
            write(snap / 'tokenizer.json', {})
            write(snap / 'model.safetensors.index.json', {'weight_map': {'a': 'missing.safetensors'}})
            with self.assertRaisesRegex(ValueError, 'incomplete weight'):
                cached_snapshot(folder, 1000)

    def test_resume_accepts_only_matching_unique_records(self):
        expected = dict(model_id='org/m', setting_id='train_00', prompt_id='p',
                        generation_index=0, generation_seed=42, temperature=.8, top_p=.9, stage='responses')
        row = dict(expected, response='ok', status='ok', metadata={'model_revision': 'a'*40})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'responses.jsonl'
            path.write_text(json.dumps(row)+'\n')
            self.assertEqual(len(load_records(path, [expected], 'a'*40)), 1)
            with self.assertRaisesRegex(ValueError, 'revision'):
                load_records(path, [expected], 'b'*40)
            path.write_text((json.dumps(row)+'\n')*2)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                load_records(path, [expected], 'a'*40)
            row['generation_seed'] = 43
            path.write_text(json.dumps(row)+'\n')
            with self.assertRaisesRegex(ValueError, 'frozen'):
                load_records(path, [expected], 'a'*40)

    def test_incomplete_json_tail_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'responses.jsonl'
            path.write_text('{"response":')
            with self.assertRaises(json.JSONDecodeError):
                load_records(path, [], 'a'*40)

    def test_vectorized_mmd_matches_independent_kernel_sum(self):
        rng = np.random.default_rng(18)
        q, r = rng.normal(size=(2, 3, 4, 5)), rng.normal(size=(6, 3, 2, 5))
        result = squared_distances(q, r, 1.7, 'rbf')
        def k(a, b):
            return sum(np.exp(-np.dot(x-y, x-y)/3.4) for x in a for y in b)/(len(a)*len(b))
        oracle = np.array([[np.mean([k(a, a)+k(b, b)-2*k(a, b) for a, b in zip(x, y)])
                            for y in r] for x in q])
        np.testing.assert_allclose(result, oracle, rtol=0, atol=1e-14)
        linear = np.array([[np.mean([np.dot(a.mean(0)-b.mean(0), a.mean(0)-b.mean(0))
                                    for a, b in zip(x, y)]) for y in r] for x in q])
        np.testing.assert_allclose(squared_distances(q, r, 1.7, 'linear'), linear, atol=1e-14)

    def test_merge_preserves_base_and_checks_completed_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, run, folder = root/'base', root/'run', root/'run/models/000'
            base.mkdir(); folder.mkdir(parents=True)
            protocol = dict(promptIds=[f'p{i}' for i in range(20)], dimension=3, sigmaSquared=1.1)
            old = np.zeros((16, 20, 4, 3), dtype='<f4')
            old[:8, :, :, 0] = 1; old[8:, :, :, 1] = 1
            old.tofile(base/'enrollment.f32')
            refs = [dict(id=f'org/{i//8}:{i%8}', modelId=f'org/{i//8}', revision='a'*40, row=i) for i in range(16)]
            write(base/'bank.json', dict(schema='distdna.binary.v1', protocol=protocol, shape=list(old.shape),
                                         references=refs, sha256=sha(base/'enrollment.f32')))
            write(base/'catalog.json', dict(models=[dict(id='org/0'), dict(id='org/1')], metadata={}))
            write(base/'example-queries.json', {})
            train = np.zeros((8, 20, 4, 3), dtype='<f4'); train[:, :, :, 2] = 1
            np.save(folder/'enrollment.npy', train); np.save(folder/'queries.npy', train[:2])
            for name in ['responses.jsonl', 'preflight.jsonl', 'generation-config.json']:
                (folder/name).write_text('{}\n')
            item = dict(modelId='org/new', revision='b'*40)
            spec = dict(base=str(base), frozenFiles={}, models=[item], protocol=protocol, expectedPerModel=800,
                        promptIds=protocol['promptIds'], enrollmentSettings=[f'train_{i:02d}' for i in range(8)],
                        validationSettings=['test_00','test_01'], mode='raw', generationControls='test', validationScope='test')
            write(run/'run.json', spec); write(run/'collection.json', dict(settings=[]))
            complete = dict(**item, protocol=protocol, runSha256=sha(run/'run.json'), responseCount=800,
                            enrollmentSettings=spec['enrollmentSettings'], validationSettings=spec['validationSettings'],
                            sha256={p.name: sha(p) for p in folder.iterdir()})
            write(folder/'complete.json', complete)
            before = sha(base/'enrollment.f32')
            build(run, root/'combined')
            self.assertEqual(before, sha(base/'enrollment.f32'))
            bank = json.loads((root/'combined/bank.json').read_text())
            self.assertEqual(bank['shape'], [24, 20, 4, 3])
            self.assertEqual([r['row'] for r in bank['references']], list(range(24)))
            self.assertEqual(bank['references'][:16], refs)
            self.assertEqual(json.loads((root/'combined/validation.json').read_text())['metrics']['rbf']['top1'], 100)
            (folder/'queries.npy').write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                verified_shards(run, spec)


if __name__ == '__main__':
    unittest.main()
