#!/usr/bin/env python3
"""Wait for one webpage reply without screenshots or model-driven polling."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import URLError
import uuid

import bridge


def save(path, value):
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temp.replace(path)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def owned_write(path, text):
    try:
        with path.open('x', encoding='utf-8') as file:
            file.write(text)
    except FileExistsError:
        if path.read_text(encoding='utf-8') != text:
            raise ValueError('Reply output already contains different content; it was not overwritten.')


def run(args):
    prompt = args.file.read_text(encoding='utf-8')
    command = {'action':'reply-status', 'expected_url':args.expect_url, 'text':prompt}
    session = bridge.session_key(args.run) if getattr(args, 'run', None) else None
    if session:
        command['session'] = session
    bridge.validate(command, bridge.config())
    if session:
        bridge.validate_run(command, args.run)
    if '/c/' not in args.expect_url:
        raise ValueError('Wait only in the confirmed task conversation, not a project landing page.')
    reply = args.reply_out.resolve()
    full = reply.with_name(reply.name+'.full.txt')
    out = args.out.resolve()
    if len({out, reply, full, args.file.resolve()}) != 4:
        raise ValueError('Prompt, state and output files must have distinct paths.')
    out.parent.mkdir(parents=True, exist_ok=True)
    reply.parent.mkdir(parents=True, exist_ok=True)
    with out.with_suffix(out.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.resume:
            state = json.loads(out.read_text())
            if (state.get('session') != session or state['url'] != args.expect_url or state['prompt_sha256'] != digest(prompt) or
                    state['reply_file'] != str(reply) or state['full_reply_file'] != str(full)):
                raise ValueError('Resume arguments do not match the saved task.')
            if state['state'] == 'ready':
                if digest(reply.read_text()) != state['reply_sha256'] or digest(full.read_text()) != state['full_reply_sha256']:
                    raise ValueError('Saved reply files have changed.')
                print(json.dumps({'state':'ready','file':str(out),'reply_file':str(reply),'reused':True}));return 0
            if state['state'] == 'blocked':
                state.update(probe_id=None,probe_enqueued=False)  # Recheck after the user resolves a page error.
        else:
            if out.exists() or reply.exists() or full.exists():
                raise ValueError('Use new output paths, or --resume for the saved watcher.')
            state = {'state':'waiting','session':session,'url':args.expect_url,'prompt_sha256':digest(prompt),
                     'reply_file':str(reply),'full_reply_file':str(full),'probes':0,'polls':0,
                     'probe_id':None,'screenshot_requests':0,'started_at':time.time()}
            save(out,state)
        state.update(state='waiting',screenshot_recommended=False,error=None)
        save(out,state)
        print(json.dumps({'state':'waiting','file':str(out)}),flush=True)
        deadline = time.monotonic()+args.timeout
        while time.monotonic() < deadline:
            if not state.get('probe_id'):
                state.update(probe_id=uuid.uuid4().hex,probe_enqueued=False)
                state['probes'] += 1
                save(out,state)  # The read operation can be recovered after a network interruption.
            try:
                if not state.get('probe_enqueued'):
                    bridge.request('/command',{'id':state['probe_id'],'command':command})
                    state['probe_enqueued'] = True
                    save(out,state)
                job = bridge.request('/job',{'id':state['probe_id']})
            except (URLError,TimeoutError,OSError):
                state.update(state='waiting',reason='bridge_unavailable')
                save(out,state)
                time.sleep(min(args.interval,max(0,deadline-time.monotonic())))
                continue
            state['polls'] += 1
            if job['state'] == 'done':
                result = job.get('result') or {}
                if result.get('status') == 'ready' and result.get('complete') and result.get('url') == args.expect_url and result.get('text','').strip():
                    selected = result.get('handoff_text') or result['text']
                    state.update(state='saving',reason=None,source='dom',reply_sha256=digest(selected),
                                 full_reply_sha256=digest(result['text']))
                    save(out,state)
                    owned_write(full,result['text'])
                    owned_write(reply,selected)
                    state.update(state='ready',completed_at=time.time(),characters=len(selected))
                    save(out,state)
                    print(json.dumps({'state':'ready','file':str(out),'reply_file':str(reply),
                                      'characters':len(selected),'probes':state['probes'],'screenshot_requests':0}))
                    return 0
                if result.get('status') == 'waiting':
                    state.update(state='waiting',reason=result.get('reason'),probe_id=None,probe_enqueued=False)
                else:
                    state.update(state='blocked',reason=result.get('code','unexpected_result'),
                                 error=str(result.get('error','Unexpected reply state'))[:300],
                                 screenshot_recommended=bool(result.get('screenshot_recommended',True)))
                    save(out,state)
                    print(json.dumps({'state':'blocked','file':str(out),'reason':state['reason']}))
                    return 2
            elif job['state'] in ['expired','resolved']:
                # These are pure reads; never create, resend or regenerate a user message here.
                state.update(state='waiting',reason='read_expired',probe_id=None,probe_enqueued=False)
            else:
                state.update(state='waiting',reason='awaiting_browser')
            save(out,state)
            time.sleep(min(args.interval,max(0,deadline-time.monotonic())))
        state.update(state='timed_out',screenshot_recommended=False)
        save(out,state)
        print(json.dumps({'state':'timed_out','file':str(out),'next':'Resume the same watcher; do not resend the prompt.'}))
        return 3


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,help='Route to the same isolated task window as the send.')
    p.add_argument('--file',type=Path,required=True,help='The exact sent prompt file.')
    p.add_argument('--expect-url',required=True)
    p.add_argument('--out',type=Path,required=True,help='Compact progress and recovery state.')
    p.add_argument('--reply-out',type=Path,required=True)
    p.add_argument('--timeout',type=float,default=900)
    p.add_argument('--interval',type=float,default=3)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args()
    if not 0 < args.timeout <= 86400 or not 0.05 <= args.interval <= 60:
        p.error('Timeout must be 0..86400 seconds and interval 0.05..60 seconds.')
    raise SystemExit(run(args))


if __name__ == '__main__':
    main()
