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
from full_catalog import gpu_count, job_key, model_id, select_files, make_job,json_metadata,cached_native_snapshot
from common import read, write, sha, verify_run
from provider_config import openrouter_key


class FullCatalogTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
