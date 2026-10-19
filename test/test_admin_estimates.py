import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/distdna_enrollment'))
import admin_estimates as a
from full_catalog import connection
class Estimates(unittest.TestCase):
 def test_ids_and_ranges(self):
  for name in ['https://evil/x','org/../secret','/tmp/x','x; echo hi','org/a?token=x']:
   with self.assertRaises(ValueError):a.valid_model(name)
  self.assertEqual(a.valid_model('openai/gpt-4o-mini'),'openai/gpt-4o-mini')
  self.assertIsNone(a.summarize_samples([]))
  self.assertEqual(a.summarize_samples([2]),{'low':1.5,'high':3.,'samples':1})
 def test_api_quote_is_read_only_and_pins_endpoint(self):
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp)
   (run/'queue.json').write_text(json.dumps({'protocol':{'promptIds':['p']}}))
   (run/'collection-template.json').write_text(json.dumps({'prompts':[{'prompt_id':'p','text':'Hello'}]}))
   endpoint={'status':0,'tag':'openai','provider_name':'OpenAI','supported_parameters':['temperature','top_p','max_tokens'],'pricing':{'prompt':'.000001','completion':'.000002'}}
   with patch.object(a,'public_json',return_value={'data':{'endpoints':[endpoint]}}),patch.object(a,'openrouter_key',return_value='not-returned'):
    q=a.estimate(run,{'model':'openai/test','mode':'chat','billing':'byok'})
   self.assertGreater(q['onePassReservationUsd'],q['expectedInferenceUsd'])
   self.assertAlmostEqual(q['byokFeeAllowanceUsd'],q['onePassReservationUsd']*.05)
   self.assertNotIn('not-returned',json.dumps(q))
   with connection(run) as db:
    self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    saved=db.execute('SELECT item,expires FROM admin_quotes WHERE id=?',(q['quoteId'],)).fetchone()
    self.assertEqual(json.loads(saved['item'])['tag'],'openai')
    self.assertEqual(saved['expires'],q['expiresAt'])
if __name__=='__main__':unittest.main()
