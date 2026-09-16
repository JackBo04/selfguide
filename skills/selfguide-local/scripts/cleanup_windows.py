#!/usr/bin/env python3
"""Close completed task tabs; preserve chats, login, drafts and active work."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import bridge


def require_settled(run):
    """Called with the cleanup lock held, before resuming a completed task."""
    record = run / 'checks/window-cleanup.json'
    if record.exists():
        saved = json.loads(record.read_text())
        job = bridge.request('/job', {'id': saved['id']})
        if job['state'] in {'queued', 'claimed'}:
            raise ValueError('Window cleanup is still pending; inspect job '+saved['id']+' before resuming.')


def enqueue(run, recheck=False):
    run = run.expanduser().resolve()
    state = json.loads((run / 'state.json').read_text())
    if state.get('phase') != 'complete':
        return {'status': 'skipped', 'reason': 'task_not_complete'}
    if not state.get('conversation_url') or not state.get('rounds'):
        return {'status': 'skipped', 'reason': 'no_saved_conversation'}
    command = {'action': 'close', 'session': bridge.session_key(run),
               'expected_url': state['conversation_url'],
               'text': (run / state['rounds'][-1]['outgoing']).read_text()}
    cfg = bridge.config()
    if not isinstance(cfg.get('port'), int) or not cfg.get('agent_token'):
        raise ValueError('Browser bridge is not configured for window cleanup.')
    bridge.validate(command, cfg)
    bridge.validate_run(command, run)
    checks = run / 'checks'
    checks.mkdir(exist_ok=True)
    record = checks / 'window-cleanup.json'
    digest = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()
    with (checks / 'window-cleanup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        bridge.validate_run(command, run)  # A concurrent resume may have changed the phase.
        saved = json.loads(record.read_text()) if record.exists() else None
        if saved:
            changed = saved['command_sha256'] != digest
            job = bridge.request('/job', {'id': saved['id']}) if saved.get('submitted') or changed else None
            if job and job['state'] in {'queued', 'claimed'}:
                if changed:
                    raise ValueError('Earlier cleanup is unresolved; inspect its job before closing a newer round.')
                return {'status': job['state'], 'id': saved['id']}
            if job and job['state'] == 'done' and job.get('result', {}).get('closed') and not recheck and not changed:
                return {'status': 'closed', 'id': saved['id']}
            # A confirmed failed/expired job may be checked again later. An
            # interrupted submission reuses its persisted ID, never a new close.
            if job:
                saved = None
        if not saved:
            saved = {'id': uuid.uuid4().hex, 'command_sha256': digest, 'submitted': False}
        def save():
            temp = record.with_suffix('.tmp')
            temp.write_text(json.dumps(saved, indent=2) + '\n')
            temp.replace(record)
        save()
        bridge.request('/command', {'id': saved['id'], 'command': command})
        saved['submitted'] = True
        save()
        return {'status': 'queued', 'id': saved['id']}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', default=[])
    parser.add_argument('--workspace', type=Path, action='append', default=[],
                        help='A task workspace or its tasks directory; may be repeated.')
    parser.add_argument('--apply', action='store_true', help='Enqueue closes; default is a local preview.')
    parser.add_argument('--wait', type=float, default=0, help='Wait up to 55 seconds for queued cleanup results.')
    args = parser.parse_args()
    if not args.run and not args.workspace:
        parser.error('Provide --run or --workspace.')
    if not 0 <= args.wait <= 55:
        parser.error('--wait must be between 0 and 55 seconds.')
    runs = set(path.expanduser().resolve() for path in args.run)
    for workspace in args.workspace:
        root = workspace.expanduser().resolve()
        tasks = root / 'tasks' if (root / 'tasks').is_dir() else root
        runs.update(p.parent for p in tasks.glob('*/state.json'))
    results = []
    for run in sorted(runs):
        try:
            state = json.loads((run / 'state.json').read_text())
            if state.get('phase') != 'complete':
                result = {'status': 'skipped', 'reason': 'task_not_complete'}
            else:
                result = enqueue(run, recheck=True) if args.apply else {'status': 'eligible'}
        except Exception as error:
            result = {'status': 'deferred', 'reason': type(error).__name__}
        results.append({'run': str(run), **result})
    deadline = time.monotonic() + args.wait
    while args.apply and time.monotonic() < deadline:
        pending = [r for r in results if r['status'] in {'queued', 'claimed'}]
        if not pending:
            break
        for result in pending:
            job = bridge.request('/job', {'id': result['id']})
            if job['state'] == 'done':
                outcome = job.get('result') or {}
                result.update(status='closed' if outcome.get('closed') else 'kept',
                              reason=outcome.get('code', outcome.get('reason')),
                              already_closed=bool(outcome.get('already_closed')))
            else:
                result['status'] = job['state']
        if any(r['status'] in {'queued', 'claimed'} for r in results):
            time.sleep(min(1, max(0, deadline-time.monotonic())))
    print(json.dumps({'applied': args.apply, 'tasks': results}, ensure_ascii=False))


if __name__ == '__main__':
    main()
