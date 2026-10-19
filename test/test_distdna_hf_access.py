import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/distdna_enrollment'))
from hf_access import hf_token,authenticate,refresh
from full_catalog import connection


class HubAccessTests(unittest.TestCase):
    def test_private_file_empty_rotation_and_permissions(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True):
            path=Path(d)/'huggingface.env'
            path.write_text('HF_TOKEN=\n');path.chmod(0o600)
            self.assertIsNone(hf_token(d))
            path.write_text('HF_TOKEN=hf_example_one\n')
            self.assertEqual(hf_token(d),'hf_example_one')
            os.environ['HF_TOKEN']='hf_example_old'
            path.write_text('HF_TOKEN="hf_example_two"\n')
            self.assertEqual(hf_token(d),'hf_example_two')
            path.chmod(0o644)
            with self.assertRaises(ValueError):hf_token(d)

    def test_authentication_errors_never_expose_body(self):
        error=urllib.error.HTTPError('https://huggingface.co',401,'private-secret',{},None)
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(ValueError,'HTTP 401') as caught:authenticate('hf_test')
            self.assertNotIn('secret',str(caught.exception))

    def test_access_refresh_only_retries_access_blockers(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True),patch('hf_access.authenticate') as auth:
            run=Path(d);path=run/'huggingface.env';path.write_text('HF_TOKEN=hf_example\n');path.chmod(0o600)
            db=connection(run)
            for key,mode,detail in [('gated','raw','Cannot access gated repo'),('runtime','raw','Unsupported runtime'),('chat','chat','403 Client Error')]:
                db.execute('INSERT INTO jobs(key,mode,state,detail) VALUES(?,?,?,?)',(key,mode,'blocked',detail))
            db.commit();state={};refresh(run,db,state)
            self.assertEqual(dict(db.execute('SELECT key,state FROM jobs')),dict(gated='pending',runtime='blocked',chat='blocked'))
            self.assertNotIn('hf_example',(run/'hf-access-status.json').read_text())
            refresh(run,db,state);auth.assert_called_once()
            db.close()


if __name__=='__main__':unittest.main()
