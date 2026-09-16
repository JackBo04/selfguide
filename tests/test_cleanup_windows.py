import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/selfguide-local/scripts'))
spec = importlib.util.spec_from_file_location('cleanup_windows', ROOT/'skills/selfguide-local/scripts/cleanup_windows.py')
cleanup = importlib.util.module_from_spec(spec);spec.loader.exec_module(cleanup)


class CleanupTest(unittest.TestCase):
    def test_completed_only_and_uncertain_submission_reuses_id(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d);prompt = 'The exact final handoff'
            (run/'out.txt').write_text(prompt)
            state = {'id':'task-a','phase':'waiting_reply','conversation_url':'https://chatgpt.com/g/g-p-test/c/one',
                     'rounds':[{'outgoing':'out.txt','outgoing_sha256':hashlib.sha256(prompt.encode()).hexdigest()}]}
            def save(): (run/'state.json').write_text(json.dumps(state))
            save()
            with patch.object(cleanup.bridge,'request') as request:
                self.assertEqual(cleanup.enqueue(run)['reason'],'task_not_complete');request.assert_not_called()
            state['phase']='complete';save();ids=[]
            def rpc(route,body):
                if route=='/command':
                    ids.append(body['id'])
                    if len(ids)==1: raise OSError('acknowledgement lost')
                    return {'id':body['id']}
                return {'state':'done','result':{'closed':True}}
            with patch.object(cleanup.bridge,'config',return_value={'project_url':'https://chatgpt.com/g/g-p-test/project','port':1,'agent_token':'fixture'}),patch.object(cleanup.bridge,'request',side_effect=rpc):
                with self.assertRaises(OSError):cleanup.enqueue(run)
                self.assertEqual(cleanup.enqueue(run)['status'],'queued')
                self.assertEqual(ids[0],ids[1])
                self.assertEqual(cleanup.enqueue(run)['status'],'closed')
                self.assertEqual(len(ids),2)
                cleanup.require_settled(run)
                # A later completed round gets its own cleanup job.
                prompt='The next completed handoff';(run/'out.txt').write_text(prompt)
                state['rounds'][-1]['outgoing_sha256']=hashlib.sha256(prompt.encode()).hexdigest();save()
                self.assertEqual(cleanup.enqueue(run)['status'],'queued')
                self.assertNotEqual(ids[-1],ids[0])
            self.assertEqual(json.loads((run/'state.json').read_text()),state)

    def test_resume_waits_for_pending_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);(run/'checks').mkdir()
            (run/'checks/window-cleanup.json').write_text(json.dumps({'id':'pending'}))
            with patch.object(cleanup.bridge,'request',return_value={'state':'claimed'}):
                with self.assertRaises(ValueError):cleanup.require_settled(run)

    def test_close_requires_completed_task_and_current_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);state={'id':'task-a','phase':'executing','rounds':[]}
            (run/'state.json').write_text(json.dumps(state))
            command={'action':'close','session':'task-a','text':'wrong'}
            with self.assertRaises(ValueError):cleanup.bridge.validate_run(command,run)
            state['phase']='complete';(run/'state.json').write_text(json.dumps(state))
            with self.assertRaises(ValueError):cleanup.bridge.validate_run(command,run)


if __name__=='__main__':unittest.main()
