import argparse
import contextlib
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/selfguide-local/scripts'))
spec=importlib.util.spec_from_file_location('wait_reply',ROOT/'skills/selfguide-local/scripts/wait_reply.py')
waiter=importlib.util.module_from_spec(spec);spec.loader.exec_module(waiter)

class WaitReplyTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.prompt=self.root/'prompt.txt';self.prompt.write_text('exact prompt')
        self.args=argparse.Namespace(file=self.prompt,expect_url='https://chatgpt.com/g/g-p-test-selfguide/c/current',
            out=self.root/'state.json',reply_out=self.root/'reply.txt',timeout=.12,interval=.05,resume=False)
        self.calls=[]
    def tearDown(self):self.temp.cleanup()
    def call(self,result):
        def request(route,body):
            self.calls.append((route,body))
            return {'state':'done','result':result} if route=='/job' else {'id':body['id']}
        with patch.object(waiter.bridge,'config',return_value={'project_url':'https://chatgpt.com/g/g-p-test/project'}), patch.object(waiter.bridge,'request',side_effect=request), contextlib.redirect_stdout(io.StringIO()) as stdout:
            code=waiter.run(self.args)
        return code,stdout.getvalue()
    def test_original_text_saved_once_and_resume_detects_edits(self):
        result={'status':'ready','complete':True,'url':self.args.expect_url,'text':'outside\nfull reply','handoff_text':'中文 /tmp/a_b \\ exact'}
        code,output=self.call(result);self.assertEqual(code,0)
        self.assertEqual(self.args.reply_out.read_text(),result['handoff_text'])
        self.assertEqual(Path(str(self.args.reply_out)+'.full.txt').read_text(),result['text'])
        self.assertNotIn(result['handoff_text'],output)
        self.args.resume=True;self.calls=[]
        self.assertEqual(self.call(result)[0],0);self.assertFalse(self.calls)
        self.args.reply_out.write_text('modified by someone')
        with self.assertRaises(ValueError):self.call(result)
    def test_partial_text_is_not_returned_and_timeout_is_not_a_screenshot(self):
        code,output=self.call({'status':'waiting','reason':'generating','text':'PRIVATE PARTIAL '*1000})
        self.assertEqual(code,3);self.assertFalse(self.args.reply_out.exists())
        state=json.loads(self.args.out.read_text());self.assertFalse(state['screenshot_recommended'])
        self.assertNotIn('PRIVATE PARTIAL',self.args.out.read_text()+output)
        self.assertTrue(all(body['command']['action']=='reply-status' for route,body in self.calls if route=='/command'))
    def test_resume_cannot_switch_to_another_window(self):
        task=self.root/'task';task.mkdir()
        state={'id':'task-a','rounds':[{'outgoing_sha256':hashlib.sha256(self.prompt.read_bytes()).hexdigest()}]}
        (task/'state.json').write_text(json.dumps(state))
        self.args.run=task
        self.assertEqual(self.call({'status':'waiting','reason':'generating'})[0],3)
        commands=[body['command'] for route,body in self.calls if route=='/command']
        self.assertTrue(commands);self.assertTrue(all(c['session']=='task-a' for c in commands))
        self.args.resume=True
        state['id']='task-b';(task/'state.json').write_text(json.dumps(state))
        with self.assertRaises(ValueError):self.call({})
    def test_blocked_page_has_diagnostic_but_no_reply(self):
        self.assertEqual(self.call({'status':'blocked','code':'page_unavailable','error':'login','screenshot_recommended':True})[0],2)
        self.assertFalse(self.args.reply_out.exists())
        self.assertTrue(json.loads(self.args.out.read_text())['screenshot_recommended'])
    def test_wrong_url_and_resume_task_are_rejected(self):
        self.assertEqual(self.call({'status':'ready','complete':True,'url':'https://chatgpt.com/g/g-p-test/c/other','text':'wrong reply'})[0],2)
        self.assertFalse(self.args.reply_out.exists())
        self.args.resume=True;self.prompt.write_text('different prompt')
        with self.assertRaises(ValueError):self.call({})
    def test_resume_rechecks_after_page_problem_is_resolved(self):
        self.assertEqual(self.call({'status':'blocked','code':'page_unavailable','error':'login'})[0],2)
        old_id=json.loads(self.args.out.read_text())['probe_id']
        self.args.resume=True
        self.assertEqual(self.call({'status':'ready','complete':True,'url':self.args.expect_url,'text':'recovered reply'})[0],0)
        state=json.loads(self.args.out.read_text())
        self.assertNotEqual(state['probe_id'],old_id)
        self.assertFalse(state['screenshot_recommended'])

if __name__=='__main__':unittest.main()
