import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
class SessionTest(unittest.TestCase):
 def test_both_variants_keep_evidence_and_send_guards(self):
  for variant in ['server','local']:
   with self.subTest(variant=variant), tempfile.TemporaryDirectory() as d:
    root=Path(d);(root/'browser/run').mkdir(parents=True);(root/'bridge').mkdir()
    project='https://chatgpt.com/g/g-p-test-selfguide/project'
    (root/'browser/run/selfguide-project.json').write_text(json.dumps({'name':'selfguide','url':project}))
    (root/'bridge/config.json').write_text(json.dumps({'project_url':project}))
    env={**os.environ,'SELFGUIDE_BROWSER_HOME':str(root/'browser'),'SELFGUIDE_LOCAL_HOME':str(root/'bridge')}
    script=ROOT/f'skills/selfguide-{variant}/scripts/session.py'
    task=root/'task.txt';task.write_text('Synthetic task')
    source=root/'data.txt';source.write_bytes(b'original evidence')
    def call(*args,ok=True):
     result=subprocess.run([sys.executable,str(script),*map(str,args)],capture_output=True,text=True,env=env)
     self.assertEqual(result.returncode==0,ok,result.stderr)
     return json.loads(result.stdout) if ok else None
    run=Path(call('new','--task-file',task,'--workspace',root/'selfguide')['run'])
    for name in ['uploads','messages','feedback','outputs','checks']:self.assertTrue((run/name).is_dir())
    call('stage','--run',run,'--file',source)
    item=json.loads((run/'uploads/manifest.json').read_text())['files'][0]
    self.assertEqual(item['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
    call('stage','--run',run,'--file',source,ok=False)
    call('prepare','--run',run,'--file',task);call('submitting','--run',run)
    call('prepare','--run',run,'--file',task,ok=False)

    call('sent','--run',run,'--url','https://chatgpt.com/c/wrong',ok=False)
    call('checkpoint','--run',run,'--phase','paused','--note-file',task)
    call('resume','--run',run);self.assertEqual(call('status','--run',run)['phase'],'send_pending')
    call('sent','--run',run,'--url','https://chatgpt.com/g/g-p-test-selfguide/c/123')
    self.assertIn(f'SELFGUIDE_REPLY_END task={run.name} round=1', (run/'messages/out-001.txt').read_text())
    reply=root/'reply.txt'
    body='信息需求：读取 config.json\n下一步：检查 /tmp/a_b；保留反斜杠 \\ 和中文。\n验证：输出应等于 31。'
    def envelope(task_id=run.name, number=1):
     return f'SELFGUIDE_REPLY_BEGIN task={task_id} round={number}\n{body}\nSELFGUIDE_REPLY_END task={task_id} round={number}'
    for bad in [envelope('wrong'),envelope(number=2),envelope().rsplit('\n',1)[0], '识图摘要']:
     reply.write_text(bad)
     call('reply','--run',run,'--file',reply,'--source','clipboard',ok=False)
     self.assertEqual(call('status','--run',run)['phase'],'waiting_reply')
     self.assertFalse((run/'feedback/in-001.txt').exists())
    reply.write_text('```text\n'+envelope()+'\n```\n')
    call('reply','--run',run,'--file',reply,ok=False)
    call('reply','--run',run,'--file',reply,'--source','ocr',ok=False)
    call('reply','--run',run,'--file',reply,'--source','clipboard')
    self.assertTrue((run/'feedback/in-001.txt').exists())
    self.assertEqual((run/'feedback/in-001.txt').read_bytes(),reply.read_bytes())
    incoming=call('status','--run',run)['rounds'][-1]
    self.assertTrue(incoming['reply_format_valid'])
    self.assertEqual(incoming['incoming_sha256'],hashlib.sha256(reply.read_bytes()).hexdigest())
    self.assertEqual(incoming['incoming_source'],'clipboard')
    call('prepare','--run',run,'--file',task);call('submitting','--run',run)
    call('sent','--run',run,'--url','https://chatgpt.com/g/g-p-test-renamed/c/different',ok=False)
    call('sent','--run',run,'--url','https://chatgpt.com/g/g-p-test-renamed/c/123')
    reply.write_text('完整 DOM 原文，但网页未遵守格式。')
    note=root/'review.txt';note.write_text('核对本轮用户消息和 DOM 生成结束状态，完整正文已取得。')
    call('reply','--run',run,'--file',reply,'--source','dom','--format-note-file',note)
    incoming=call('status','--run',run)['rounds'][-1]
    self.assertFalse(incoming['reply_format_valid'])
    self.assertEqual(incoming['format_review_note'],note.read_text())
    # Existing flat-layout tasks remain resumable without the new reply contract.
    legacy=Path(call('new','--task-file',task,'--workspace',root/'legacy')['run'])
    state=json.loads((legacy/'state.json').read_text());state.pop('reply_format');state.pop('layout_version')
    (legacy/'state.json').write_text(json.dumps(state))
    call('prepare','--run',legacy,'--file',task);call('submitting','--run',legacy)
    call('sent','--run',legacy,'--url','https://chatgpt.com/g/g-p-test-selfguide/c/456')
    call('reply','--run',legacy,'--file',task)
    self.assertEqual((legacy/'in-001.txt').read_text(),task.read_text())
    call('checkpoint','--run',run,'--phase','complete','--note-file',task)
    call('prepare','--run',run,'--file',task,ok=False)

    # Continuing a completed task keeps its conversation and round history.
    before=call('status','--run',run)
    resumed=call('resume','--run',run)
    self.assertEqual(resumed['phase'],'executing')
    self.assertEqual(resumed['restore_url'],before['conversation_url'])
    call('prepare','--run',run,'--file',task)
    after=call('status','--run',run)
    self.assertEqual(after['id'],before['id'])
    self.assertEqual(after['conversation_url'],before['conversation_url'])
    self.assertEqual(after['round'],before['round']+1)

if __name__=='__main__':unittest.main()
