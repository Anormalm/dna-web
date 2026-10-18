"""Provider pinning, credential isolation, budget accounting and error handling."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import io
import json
import sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/distdna_enrollment'))
from chat_queue import choose_endpoint,build_payload,reserve_cost,record_cost,completion,ApiFailure,frozen_worker,recover_encoding_failures,verify_encoder_runtime
from common import write,sha
from full_catalog import connection


def endpoint(tag='openai',provider='OpenAI'):
    return dict(tag=tag,provider_name=provider,status=0,supported_parameters=['temperature','top_p','max_tokens','seed'],pricing=dict(prompt='0.00000015',completion='0.0000006'))


class ChatTests(unittest.TestCase):
    def test_resume_uses_frozen_worker_and_rejects_changed_code(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d); worker=folder/'chat_queue.py'
            worker.write_text('# archived collector\n')
            write(folder/'chat-run.json',{'sourceSha256':{str(worker):sha(worker)}})
            self.assertEqual(frozen_worker(folder),worker)
            worker.write_text('# changed\n')
            with self.assertRaises(ValueError): frozen_worker(folder)

    def test_runtime_probe_preserves_venv_executable(self):
        with patch('chat_queue.sys.executable','/venv/bin/python'),patch('chat_queue.subprocess.run') as run:
            verify_encoder_runtime()
            self.assertEqual(run.call_args.args[0][0],'/venv/bin/python')

    def test_encoding_retry_keeps_collection_failures_and_retry_limit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); db=connection(root)
            for name,count,attempts in [('recover',800,1),('partial',799,1),('exhausted',800,3)]:
                folder=root/name
                write(folder/'models/000/status.json',dict(records=count,error='sentence-transformers is required; install distdna[embedding]'))
                db.execute('INSERT INTO jobs(key,mode,model,state,run,attempts) VALUES(?,?,?,?,?,?)',
                           (name,'chat',name,'chat_failed',str(folder),attempts))
            db.commit(); recover_encoding_failures(db)
            states=dict(db.execute('SELECT key,state FROM jobs').fetchall())
            self.assertEqual(states,dict(recover='chat_prepared',partial='chat_failed',exhausted='chat_failed'))
            db.close()

    def test_endpoint_prefers_native_and_rejects_missing_control(self):
        a,b=endpoint('azure','Azure'),endpoint()
        self.assertEqual(choose_endpoint('openai/gpt-4o-mini',[a,b])['tag'],'openai')
        a['supported_parameters'].remove('top_p')
        with self.assertRaises(ValueError): choose_endpoint('openai/model',[a])

    def test_payload_pins_provider_and_never_contains_key(self):
        p=build_payload(dict(endpoint=endpoint(),modelId='openai/model'),dict(temperature=.8,top_p=.9,generation_seed=2**34+5),'hello')
        self.assertEqual(p['seed'],5)
        self.assertFalse(p['provider']['allow_fallbacks'])
        self.assertTrue(p['provider']['require_parameters'])
        self.assertEqual(p['provider']['only'],['openai'])
        self.assertNotIn('Authorization',json.dumps(p))

    def test_budget_persists_and_settles_without_losing_unknown_calls(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)
            reserve_cost(run,'a',.6,1)
            with self.assertRaises(ApiFailure): reserve_cost(run,'b',.5,1)
            record_cost(run,'a',{'cost':.1})
            reserve_cost(run,'c',.6,1)
            with self.assertRaises(ApiFailure): reserve_cost(run,'d',.4,1)

    def test_response_provenance_and_usage(self):
        data=dict(provider='OpenAI',model='openai/model-2024',id='id',usage={'cost':.0001},choices=[dict(message={'content':' hello '},finish_reason='stop')])
        with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
            row=dict(temperature=.8,top_p=.9,generation_seed=5)
            result=completion(Path(d),dict(endpoint=endpoint(),modelId='openai/model',revision='observed'),row,'prompt','not-a-real-key',1)
            self.assertEqual(result['response'],'hello')
            self.assertEqual(result['metadata']['served_model'],'openai/model-2024')
            self.assertNotIn('not-a-real-key',json.dumps(result))

    def test_provider_mismatch_is_rejected(self):
        data=dict(provider='Other',usage={},choices=[dict(message={'content':'hi'})])
        with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaisesRegex(ApiFailure,'provider differs'):
                completion(Path(d),dict(endpoint=endpoint(),modelId='openai/model'),dict(temperature=.8,top_p=.9,generation_seed=5),'prompt','not-a-real-key',1)

    def test_http_errors_never_include_response_body_or_key(self):
        e=urllib.error.HTTPError('https://openrouter.ai',401,'fake-secret-response',{},None)
        with tempfile.TemporaryDirectory() as d,patch('urllib.request.urlopen',side_effect=e):
            with self.assertRaises(ApiFailure) as caught:
                completion(Path(d),dict(endpoint=endpoint(),modelId='openai/model'),dict(temperature=.8,top_p=.9,generation_seed=5),'prompt','not-a-real-key',1)
            self.assertNotIn('secret',str(caught.exception))


if __name__=='__main__': unittest.main()
