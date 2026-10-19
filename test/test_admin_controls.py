import json,sqlite3,sys,tempfile,unittest,time,subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/distdna_enrollment'))
from admin_control import dispatch
from admin_runtime import init_queue,init_money,budget,reserve,reconcile_cost,ControlError,apply_state,stopped,worker_identity,signal_worker
from admin_estimates import schema
from full_catalog import connection
class Controls(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.run=Path(self.tmp.name)
  (self.run/'chat-collection-policy.json').write_text('{"maximumCostUsd":75}')
  with connection(self.run) as db:
   init_queue(db);schema(db)
   item={'revision':'a'*40,'files':['weights'],'weightBytes':123}
   db.execute('INSERT INTO admin_quotes VALUES(?,?,?,?,?,?,?)',('quote','org/model','raw','openrouter',json.dumps(item),'{}',time.time()+100));db.commit()
  init_money(self.run).close()
 def tearDown(self):self.tmp.cleanup()
 def test_add_is_held_idempotent_and_start_stop_preserves_data(self):
  added=dispatch(self.run,'add',{'quoteId':'quote'},7);key=added['key']
  self.assertEqual(added['state'],'paused')
  self.assertTrue(dispatch(self.run,'add',{'quoteId':'quote'},7)['alreadyExists'])
  self.assertEqual(dispatch(self.run,'job',{'key':key,'operation':'start'},7)['state'],'discovered')
  (self.run/'saved-responses.jsonl').write_text('saved\n')
  dispatch(self.run,'job',{'key':key,'operation':'stop'},7)
  with connection(self.run) as db:
   self.assertTrue(stopped(db,key,'raw'))
   self.assertEqual(apply_state(db,key,{'state':'ready','item':'new'})['state'],'paused')
   self.assertEqual(apply_state(db,key,{'state':'complete'})['state'],'complete')
  self.assertEqual((self.run/'saved-responses.jsonl').read_text(),'saved\n')
 def test_budget_concurrency_and_stale_edits(self):
  dispatch(self.run,'budget',{'capUsd':1,'version':1},7)
  with self.assertRaises(ControlError):dispatch(self.run,'budget',{'capUsd':10,'version':1},7)
  def attempt(i):
   try:reserve(self.run,'job','openrouter',str(i),.75);return True
   except ControlError:return False
  with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(attempt,[1,2])),1)
  self.assertEqual(budget(self.run)['committedUsd'],.75)
  dispatch(self.run,'budget',{'capUsd':0,'version':2},7)
  with self.assertRaises(ControlError):reserve(self.run,'job','openrouter','3',.01)
 def test_byok_cost_not_treated_as_free_and_unknown_charges_retained(self):
  reserve(self.run,'job','byok','1',3)
  reconcile_cost(self.run,'1',{'cost':.1,'is_byok':True,'prompt_tokens':100,'completion_tokens':50},{'prompt':'.01','completion':'.02'})
  self.assertAlmostEqual(budget(self.run)['committedUsd'],2.1)
  self.assertAlmostEqual(budget(self.run)['byokInferenceEstimateUsd'],2)
  reserve(self.run,'job','openrouter','2',.2)
  reconcile_cost(self.run,'2',{}, {})
  self.assertAlmostEqual(budget(self.run)['committedUsd'],2.3)
 def test_bulk_stop_blocks_new_work_and_unknown_pid_is_never_signaled(self):
  key=dispatch(self.run,'add',{'quoteId':'quote'},7)['key']
  dispatch(self.run,'collection',{'mode':'raw','operation':'stop'},7)
  with self.assertRaises(ControlError):dispatch(self.run,'job',{'key':key,'operation':'start'},7)
  self.assertIsNone(worker_identity(1,self.run))
  with self.assertRaises(ControlError):dispatch(self.run,'job',{'key':"x';DROP TABLE jobs;--",'operation':'start'},7)
 def test_stop_signals_only_the_matched_test_worker(self):
  script=self.run/'full_collect.py';script.write_text('import time; time.sleep(60)')
  child=subprocess.Popen([sys.executable,str(script),'--run',str(self.run)])
  try:
   self.assertFalse(signal_worker(child.pid,self.run/'unrelated'))
   self.assertIsNone(child.poll())
   self.assertTrue(signal_worker(child.pid,self.run))
   self.assertLess(child.wait(timeout=5),0)
  finally:
   if child.poll() is None:child.terminate();child.wait()
 def test_byok_and_expired_quotes_require_explicit_setup(self):
  with connection(self.run) as db:
   db.execute("UPDATE admin_quotes SET billing='byok',mode='chat'");db.commit()
  with self.assertRaises(ControlError):dispatch(self.run,'add',{'quoteId':'quote'},7)
  with connection(self.run) as db:db.execute('UPDATE admin_quotes SET expires=0');db.commit()
  with self.assertRaises(ControlError):dispatch(self.run,'add',{'quoteId':'quote','byokConfirmed':True},7)
if __name__=='__main__':unittest.main()
