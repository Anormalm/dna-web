"""Full-catalog selection, resource sizing, and immutable job preparation checks."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/distdna_enrollment'))
from full_catalog import gpu_count, job_key, model_id, select_files, make_job,json_metadata,cached_native_snapshot,connection,release_owned_staging,download_model,safe_download_error
from common import read, write, sha, verify_run
from provider_config import openrouter_key


class FullCatalogTests(unittest.TestCase):
    def test_bundle_import_requires_byte_identical_source_archive(self):
        from full_bundle import verify_collection_source
        with tempfile.TemporaryDirectory() as tmp:
            run=Path(tmp);original=run/'collect.py';saved=run/'archive.py'
            original.write_text('original source\n');saved.write_bytes(original.read_bytes())
            spec={'frozenFiles':{str(original):sha(original)}};write(run/'run.json',spec)
            original.write_text('changed source\n')
            with self.assertRaises(ValueError):verify_collection_source(run)
            write(run/'archived-inputs.json',{'files':{str(original):str(saved)}})
            self.assertEqual(verify_collection_source(run),spec)
            with self.assertRaises(ValueError):verify_run(run)
            saved.write_text('wrong archive\n')
            with self.assertRaises(ValueError):verify_collection_source(run)

    def test_native_gpu_placement_without_device_map(self):
        from native_quantized_collect import verify_gpu_placement
        gpu = SimpleNamespace(type='cuda',index=0)
        model = SimpleNamespace(parameters=lambda:[SimpleNamespace(device=gpu)],
            get_input_embeddings=lambda:SimpleNamespace(weight=SimpleNamespace(device=gpu)))
        verify_gpu_placement(model)
        model.hf_device_map={'layer':'cpu'}
        with self.assertRaises(RuntimeError):verify_gpu_placement(model)
        model.hf_device_map={}
        model.parameters=lambda:[SimpleNamespace(device=SimpleNamespace(type='meta',index=None))]
        with self.assertRaises(RuntimeError):verify_gpu_placement(model)

    def test_downloads_use_isolated_http_workers_and_recover_after_failure(self):
        import subprocess
        results = [subprocess.CompletedProcess([],1,'','Network failure'),
                   subprocess.CompletedProcess([],0,json.dumps({'snapshot':'/staging/b'}),'')]
        with patch('full_catalog.subprocess.run', side_effect=results) as run:
            with self.assertRaisesRegex(RuntimeError,'Network failure'):
                download_model('a/failed',{},'/staging')
            self.assertEqual(download_model('b/ok',{},'/staging')['snapshot'],'/staging/b')
        self.assertEqual(run.call_count,2)
        self.assertEqual(run.call_args.args[0][0],sys.executable)
        self.assertEqual(run.call_args.kwargs['env']['HF_HUB_DISABLE_XET'],'1')
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['name'],'b/ok')

    def test_download_errors_hide_tokens_and_signed_url_queries(self):
        with patch.dict('os.environ',{'HF_TOKEN':'test-private-value'}):
            text = safe_download_error('test-private-value https://example.org/file?signature=private')
        self.assertNotIn('test-private-value',text)
        self.assertNotIn('signature=private',text)

    def test_native_cache_requires_complete_weights_and_registered_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);revision='a'*40;snapshot=folder/'snapshots'/revision
            snapshot.mkdir(parents=True);(folder/'refs').mkdir();(folder/'refs/main').write_text(revision)
            write(snapshot/'config.json',{'model_type':'llama','torch_dtype':'bfloat16',
                  'quantization_config':{'quant_method':'bitsandbytes','bnb_4bit_compute_dtype':'bfloat16'}})
            write(snapshot/'tokenizer.json',{})
            write(snapshot/'model.safetensors.index.json',{'weight_map':{'w':'part.safetensors'}})
            with self.assertRaises(ValueError):cached_native_snapshot(folder,{})
            with self.assertRaises(ValueError):cached_native_snapshot(folder,{'bitsandbytes':{}})
            (snapshot/'part.safetensors').write_bytes(b'test')
            item=cached_native_snapshot(folder,{'bitsandbytes':{}})
            self.assertEqual(item['revision'],revision)
            self.assertEqual(item['nativeRuntime'],'bitsandbytes')
            self.assertEqual(item['generationDtype'],'bfloat16')
            self.assertFalse(item['owned'])

    def test_nonfinite_metadata_is_serializable_and_source_unchanged(self):
        source={'rope':{'limit':float('inf')},'values':[1,float('-inf')]}
        cleaned=json_metadata(source)
        self.assertEqual(cleaned['rope']['limit'],'inf')
        self.assertEqual(source['rope']['limit'],float('inf'))
        json.dumps(cleaned,allow_nan=False)

    def test_quantized_job_records_actual_runtime_and_frozen_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);snapshot=root/'snapshot';snapshot.mkdir()
            write(snapshot/'config.json',{'model_type':'llama','quantization_config':{'quant_method':'bitsandbytes'}})
            adapter=root/'native.py';adapter.write_text('# frozen native loader\n')
            runtime=dict(version='0.50.2',frozenFiles={str(adapter):sha(adapter)})
            settings=[dict(setting_id=f'train_{i:02d}') for i in range(8)]+[dict(setting_id=f'test_{i:02d}') for i in range(2)]
            metadata={k:None for k in ['prompt_format','plain_text_prefix','plain_text_suffix','response_boundary','min_new_tokens','stop_strings','allow_empty_observation']}
            write(root/'collection-template.json',dict(settings=settings,prompts=[],metadata=metadata))
            spec=dict(research='/source',base='/base',protocol={'promptIds':[]},frozenFiles={},perModelHours=72,nativeRuntimes={'bitsandbytes':runtime})
            item=dict(revision='a'*40,snapshot=str(snapshot),useSafetensors=True,nativeRuntime='bitsandbytes',generationDtype='bfloat16')
            job=make_job(root,spec,{'key':'native','model':'a/b'},item)
            manifest=verify_run(job)
            self.assertEqual(manifest['generationDtype'],'bfloat16')
            self.assertEqual(manifest['nativeRuntime']['version'],'0.50.2')
            self.assertIn(str(adapter),manifest['frozenFiles'])
            self.assertEqual(manifest['expectedPerModel'],800)
            retry = make_job(root,spec,{'key':'native','model':'a/b'},dict(item,jobRevision='loader-v2'))
            self.assertNotEqual(retry,job)
            self.assertEqual(verify_run(job),manifest)
            with self.assertRaises(ValueError):
                make_job(root,spec,{'key':'native','model':'a/b'},dict(item,jobRevision='../bad'))

    def test_private_provider_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {}, clear=True):
            path = Path(tmp) / 'openrouter.env'
            self.assertIsNone(openrouter_key(tmp))
            path.write_text('OPENROUTER_API_KEY="example-test-key"\n')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                openrouter_key(tmp)
            path.chmod(0o600)
            self.assertEqual(openrouter_key(tmp), 'example-test-key')
            path.write_text('OPENAI_API_KEY=unrelated\n')
            with self.assertRaises(ValueError):
                openrouter_key(tmp)

    def test_mode_and_model_identity(self):
        self.assertEqual(model_id({'id':'a_b_c', 'name':'a/b_c'}), 'a/b_c')
        self.assertNotEqual(job_key('raw', 'a/b'), job_key('chat', 'a/b'))

    def test_float32_memory_sizing(self):
        self.assertEqual(gpu_count(7_000_000_000, 14_000_000_000, 'float16'), 1)
        self.assertEqual(gpu_count(34_000_000_000, 68_000_000_000, 'bfloat16'), 3)
        self.assertEqual(gpu_count(None, 40*2**30, 'float16'), 2)

    def test_weight_selection_never_collects_old_steps(self):
        with self.assertRaises(ValueError):
            select_files([SimpleNamespace(rfilename='checkpoint-100/model.safetensors')])
        self.assertEqual(select_files([SimpleNamespace(rfilename='model.safetensors.index.json')]),
                         ('model.safetensors.index.json', True))

    def test_job_preserves_protocol_and_owns_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / 'snapshot'
            snapshot.mkdir()
            write(snapshot / 'config.json', {'model_type':'gpt2'})
            settings = [dict(setting_id=f'train_{i:02d}') for i in range(8)] + [dict(setting_id=f'test_{i:02d}') for i in range(16)]
            metadata = {k:None for k in ['prompt_format','plain_text_prefix','plain_text_suffix','response_boundary','min_new_tokens','stop_strings','allow_empty_observation']}
            write(root / 'collection-template.json', dict(settings=settings, prompts=[], metadata=metadata))
            spec = dict(research='/source', base='/base', protocol={'promptIds':[]}, frozenFiles={}, perModelHours=72)
            item = dict(revision='a'*40, snapshot=str(snapshot), useSafetensors=True)
            job = make_job(root, spec, {'key':'test', 'model':'a/b'}, item)
            manifest = read(job / 'collection.json')
            self.assertEqual(len(manifest['settings']), 10)
            self.assertEqual(manifest['metadata']['model_sources']['a/b']['repo_id'], str(snapshot))
            (snapshot / 'config.json').unlink()
            self.assertEqual(verify_run(job)['expectedPerModel'], 800)
            write(job / 'checkpoint-metadata/config.json', {'changed':True})
            with self.assertRaises(ValueError):
                verify_run(job)


class StagingCleanupTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = connection(self.root)
        self.addCleanup(self.db.close)
        self.key = job_key('raw', 'example/model')
        self.stage = self.root / 'staging' / self.key
        self.stage.mkdir(parents=True)
        (self.stage / 'model.safetensors').write_bytes(b'owned test weights')
        self.job = self.root / 'jobs' / self.key
        output = self.job / 'models/000'
        output.mkdir(parents=True)
        item = dict(modelId='example/model', revision='a'*40, snapshot=str(self.stage),
                    owned=True, weightBytes=18)
        write(self.stage / '.distdna-owned.json', {k:item[k] for k in ['modelId','revision']})
        provenance = self.job / 'checkpoint-provenance.json'
        write(provenance, item)
        protocol = dict(dimension=2)
        self.run_spec = dict(models=[item], protocol=protocol, promptIds=['p'],
            expectedPerModel=800, enrollmentSettings=list(range(8)), validationSettings=list(range(2)),
            frozenFiles={str(provenance):sha(provenance)})
        write(self.job / 'run.json', self.run_spec)
        for name, count in [('enrollment.npy',8), ('queries.npy',2)]:
            data = np.zeros((count,1,4,2), dtype=np.float32)
            data[...,0] = 1
            np.save(output / name, data)
        (output / 'responses.jsonl').write_text('{}\n'*800)
        (output / 'preflight.jsonl').write_text('{}\n'*4)
        write(output / 'generation-config.json', {})
        names = ['responses.jsonl','preflight.jsonl','enrollment.npy','queries.npy','generation-config.json']
        write(output / 'complete.json', dict(modelId=item['modelId'],revision=item['revision'],
            protocol=protocol,runSha256=sha(self.job/'run.json'),responseCount=800,
            enrollmentSettings=list(range(8)),validationSettings=list(range(2)),
            sha256={n:sha(output/n) for n in names}))
        write(output / 'status.json', dict(status='complete'))
        self.db.execute('INSERT INTO jobs(key,mode,model,state,item,run,idx,updated) VALUES(?,?,?,?,?,?,?,?)',
            (self.key,'raw',item['modelId'],'complete',json.dumps(item),str(self.job),0,1))
        self.db.commit()
        self.queue_spec = dict(staging=str(self.stage.parent), diskReserveGiB=100)

    def test_removes_verified_weights_immediately_and_preserves_replay(self):
        from merge import verified_shards
        before = {str(p.relative_to(self.job)):sha(p) for p in self.job.rglob('*') if p.is_file()}
        released = release_owned_staging(self.queue_spec, self.db, {})
        self.assertEqual(len(released), 1)
        self.assertFalse(self.stage.exists())
        for name, digest in before.items():
            self.assertEqual(sha(self.job/name), digest)
        self.assertEqual(len(verified_shards(self.job, verify_run(self.job))), 1)
        self.assertEqual(read(self.job/'weights-cleanup.json')['status'], 'deleted')
        self.assertEqual(release_owned_staging(self.queue_spec,self.db,{}), [])

    def test_retains_failed_or_still_running_workers(self):
        for state, active, alive in [('failed',{},False),('complete',{self.key:object()},False),('complete',{},True)]:
            with self.subTest(state=state, active=bool(active), alive=alive):
                self.db.execute('UPDATE jobs SET state=?',(state,));self.db.commit()
                with patch('full_catalog.pid_running',return_value=alive):
                    self.assertEqual(release_owned_staging(self.queue_spec,self.db,active), [])
                self.assertTrue(self.stage.exists())

    def test_retains_weights_when_output_verification_fails(self):
        output = self.job/'models/000'
        for filename in ['responses.jsonl','enrollment.npy','complete.json']:
            with self.subTest(filename=filename):
                path = output/filename; original = path.read_bytes(); path.unlink()
                self.assertEqual(release_owned_staging(self.queue_spec,self.db,{}), [])
                self.assertTrue(self.stage.exists())
                path.write_bytes(original)
        (output/'responses.jsonl').write_text('corrupted\n')
        self.assertEqual(release_owned_staging(self.queue_spec,self.db,{}), [])
        self.assertTrue(self.stage.exists())
        self.assertEqual(read(self.job/'weights-cleanup.json')['status'], 'retained')

    def test_retains_unowned_mismatched_and_symlinked_paths(self):
        item = read(self.job/'checkpoint-provenance.json')
        self.db.execute('UPDATE jobs SET item=?',(json.dumps(dict(item,owned=False)),));self.db.commit()
        self.assertEqual(release_owned_staging(self.queue_spec,self.db,{}), [])
        self.assertTrue(self.stage.exists())
        self.db.execute('UPDATE jobs SET item=?',(json.dumps(item),));self.db.commit()
        marker = self.stage/'.distdna-owned.json';original = marker.read_bytes()
        write(marker,dict(modelId='someone/else',revision='a'*40))
        self.assertEqual(release_owned_staging(self.queue_spec,self.db,{}), [])
        self.assertTrue(self.stage.exists());marker.write_bytes(original)
        other = self.root/'shared-cache';self.stage.rename(other);self.stage.symlink_to(other,target_is_directory=True)
        self.assertEqual(release_owned_staging(self.queue_spec,self.db,{}), [])
        self.assertTrue((other/'model.safetensors').exists())


if __name__ == '__main__':
    unittest.main()
