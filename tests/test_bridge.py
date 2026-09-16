import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/selfguide-local/scripts'))
spec=importlib.util.spec_from_file_location('bridge',ROOT/'skills/selfguide-local/scripts/bridge.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class MailboxTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();bridge.BASE=Path(self.tmp.name)
  self.cfg={'project_url':'https://chatgpt.com/g/g-p-test/project','agent_token':'agent-secret','browser_token':'browser-secret'}
  self.server=bridge.ThreadingHTTPServer(('127.0.0.1',0),bridge.Handler);self.server.cfg=self.cfg
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
 def rpc(self,route,body=None,role='agent',origin=None):
  h={'Authorization':'Bearer '+self.cfg.get(role+'_token','wrong'),'Content-Type':'application/json'}
  if origin:h['Origin']=origin
  req=Request('http://127.0.0.1:'+str(self.server.server_port)+route,data=json.dumps(body or {}).encode(),headers=h)
  try:
   with urlopen(req) as r:return r.status,json.load(r)
  except HTTPError as e:return e.code,json.load(e)
 def queue(self,action='compose',ident=None):
  ident=ident or uuid.uuid4().hex
  c={'action':action,'expected_url':self.cfg['project_url'],'text':'task evidence'}
  return ident,self.rpc('/command',{'id':ident,'command':c})
 def test_tokens_roles_and_origin(self):
  self.assertEqual(self.rpc('/poll',role='wrong')[0],401)
  self.assertEqual(self.rpc('/poll',role='agent')[0],401)
  self.assertEqual(self.rpc('/info',role='browser',origin='https://evil.example')[0],403)
  self.assertEqual(self.rpc('/info',role='browser',origin='chrome-extension://'+'a'*32)[0],200)
 def test_claim_once_and_idempotent_result(self):
  ident,result=self.queue();self.assertEqual(result[0],200)
  self.assertEqual(self.queue(ident=ident)[1][0],200)
  self.assertEqual(self.rpc('/poll',role='browser')[1]['id'],ident)
  self.assertTrue(self.rpc('/poll',role='browser')[1]['idle'])
  self.assertEqual(self.queue()[1][0],400)
  body={'id':ident,'result':{'sent':True}}
  self.assertEqual(self.rpc('/result',body,role='browser')[0],200)
  self.assertEqual(self.rpc('/result',body,role='browser')[0],200)
  self.assertEqual(self.rpc('/result',{'id':ident,'result':{'sent':False}},role='browser')[0],400)
 def test_uncertain_recovery_and_expiry(self):
  ident,_=self.queue();self.rpc('/poll',role='browser')
  self.assertEqual(self.queue('snapshot')[1][0],200)
  self.assertEqual(self.rpc('/resolve',{'id':ident,'note':''})[0],400)
  self.assertEqual(self.rpc('/resolve',{'id':ident,'note':'Visible page inspected, draft not sent.'})[0],200)
  later,_=self.queue()
  with bridge.database() as db:db.execute('UPDATE jobs SET created=0 WHERE id=?',(later,))
  self.assertEqual(self.rpc('/job',{'id':later})[1]['state'],'expired')
 def test_task_lanes_are_independent_and_claimed_in_order(self):
  def enqueue(session,action='compose'):
   ident=uuid.uuid4().hex
   command={'session':session,'action':action,'expected_url':self.cfg['project_url'],'text':'unique '+session}
   return ident,self.rpc('/command',{'id':ident,'command':command})
  a,result=enqueue('task-a');self.assertEqual(result[0],200)
  b,result=enqueue('task-b');self.assertEqual(result[0],200)
  self.assertEqual(enqueue('task-a')[1][0],400)
  self.assertEqual(self.rpc('/poll',{'exclude_sessions':['task-a']},role='browser')[1]['id'],b)
  self.assertEqual(self.rpc('/poll',role='browser')[1]['id'],a)
  read,_=enqueue('task-a','status')
  self.assertTrue(self.rpc('/poll',role='browser')[1]['idle'])
  self.rpc('/result',{'id':a,'result':{'draft_verified':True}},role='browser')
  self.assertEqual(self.rpc('/poll',role='browser')[1]['id'],read)
  # An unacknowledged operation in B cannot stop a new task C.
  c,result=enqueue('task-c');self.assertEqual(result[0],200)
  self.assertEqual(self.rpc('/poll',role='browser')[1]['id'],c)
 def test_window_routes_require_valid_ids_and_never_reset_a_task(self):
  for command in [
   {'action':'open','expected_url':self.cfg['project_url']},
   {'action':'status','session':'../other'},
   {'action':'status','session':'legacy'},
   {'action':'project','session':'task-a'},
  ]:
   with self.assertRaises(ValueError):bridge.validate(command,self.cfg)
  self.assertEqual(self.rpc('/poll',{'exclude_sessions':'task-a'},role='browser')[0],400)
 def test_project_and_attachment_validation(self):
  with self.assertRaises(ValueError):bridge.validate({'action':'snapshot','expected_url':'https://chatgpt.com/c/other'},self.cfg)
  data=b'attachment';c={'action':'attach','expected_url':self.cfg['project_url'],'file':{'name':'input.txt','base64':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()}}
  bridge.validate(c,self.cfg)
  c['file']['sha256']='incorrect'
  with self.assertRaises(ValueError):bridge.validate(c,self.cfg)

 def test_cross_task_marker_rejected_before_queue(self):
  def text(task,round=1):
   return f'SELFGUIDE_REPLY_BEGIN task={task} round={round}\nbody\nSELFGUIDE_REPLY_END task={task} round={round}'
  command={'action':'send','session':'task-a','expected_url':self.cfg['project_url'],'text':text('task-b')}
  for action in ['compose','send','reply','reply-status']:
   command['action']=action
   status,result=self.rpc('/command',{'id':uuid.uuid4().hex,'command':command})
   self.assertEqual(status,400);self.assertIn('markers',result['error'])
  with bridge.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
  command['text']=text('task-b')+'\nQuoted previous round above.\n'+text('task-a',2)
  bridge.validate(command,self.cfg)
  command['text']=text('task-a').replace('END task=task-a round=1','END task=task-a round=2')
  with self.assertRaises(ValueError):bridge.validate(command,self.cfg)
  del command['session']
  bridge.validate(command,self.cfg)  # Legacy window recovery remains available.

 def test_current_handoff_hash_phase_and_conversation(self):
  run=Path(self.tmp.name)/'task';run.mkdir()
  text='the current exact message'
  state={'id':'task-a','phase':'prepared','conversation_url':None,
         'rounds':[{'outgoing_sha256':hashlib.sha256(text.encode()).hexdigest()}]}
  def save(): (run/'state.json').write_text(json.dumps(state))
  save()
  command={'action':'compose','session':'task-a','expected_url':self.cfg['project_url'],'text':text}
  bridge.validate_run(command,run)
  with self.assertRaises(ValueError):bridge.validate_run({**command,'text':'same task, stale round'},run)
  with self.assertRaises(ValueError):bridge.validate_run({**command,'session':'task-b'},run)
  with self.assertRaises(ValueError):bridge.validate_run({**command,'action':'send'},run)
  state['phase']='send_pending';save();bridge.validate_run({**command,'action':'send'},run)
  state['phase']='complete';save()
  for action in ['compose','send','attach']:
   with self.assertRaises(ValueError):bridge.validate_run({**command,'action':action},run)
  bridge.validate_run({**command,'action':'reply-status'},run)
  state['conversation_url']=self.cfg['project_url'].replace('/project','/c/original');save()
  with self.assertRaises(ValueError):bridge.validate_run({**command,'action':'reply-status'},run)
  bridge.validate_run({**command,'action':'reply-status','expected_url':state['conversation_url']},run)

if __name__=='__main__':unittest.main()
