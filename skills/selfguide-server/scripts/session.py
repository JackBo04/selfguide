#!/usr/bin/env python3
"""Record handoffs before browser actions so uncertain sends are never retried blindly."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
import uuid

from config_paths import browser_home, project_file

BASE = browser_home()

def now():
    return datetime.now(timezone.utc).isoformat()

def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)

def text_file(path):
    text = path.read_text()
    if not text.strip():
        raise ValueError('The message or note cannot be empty.')
    return text


def reply_markers(state, number):
    suffix = f"task={state['id']} round={number}"
    return 'SELFGUIDE_REPLY_BEGIN ' + suffix, 'SELFGUIDE_REPLY_END ' + suffix

def reply_instruction(state, number):
    begin, end = reply_markers(state, number)
    return ("\n\n[SelfGuide] 本轮完整反馈放入一个 text 代码块，包含下列首尾标记；"
            "块外不放执行所需内容，内部不嵌套三反引号。\n"
            + begin + "\n<完整反馈正文>\n" + end + "\n")


def validate_reply(text, state):
    lines = text.strip().splitlines()
    if lines and lines[0].strip() in ['```', '```text', '```plaintext']:
        if len(lines) < 2 or lines[-1].strip() != '```':
            raise ValueError('Reply has an unfinished code fence; copy the complete reply again.')
        lines = lines[1:-1]
    begin, end = reply_markers(state, state['round'])
    if len(lines) < 3 or lines[0].strip() != begin or lines[-1].strip() != end:
        raise ValueError('Reply task/round markers are missing or mismatched; check the current reply and copy again.')
    body = '\n'.join(lines[1:-1]).strip()
    if not body or body == '<完整反馈正文>' or any(
            line.startswith(('SELFGUIDE_REPLY_BEGIN ', 'SELFGUIDE_REPLY_END ')) for line in lines[1:-1]):
        raise ValueError('Reply body is empty, a placeholder, or contains mixed handoffs.')

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    new = sub.add_parser('new'); new.add_argument('--task-file', type=Path, required=True)
    new.add_argument('--workspace', type=Path, default=Path.cwd() / 'selfguide')
    for action in ['stage', 'prepare', 'submitting', 'sent', 'reply', 'checkpoint', 'resume', 'status']:
        item = sub.add_parser(action); item.add_argument('--run', type=Path, required=True)
        if action in ['stage', 'prepare', 'reply']:
            item.add_argument('--file', type=Path, required=True)
        if action == 'status':
            item.add_argument('--brief', action='store_true', help='Return current handoff paths without full history.')
        if action == 'reply':
            item.add_argument('--source', choices=['clipboard', 'dom', 'download'])
            item.add_argument('--format-note-file', type=Path)
        if action == 'sent':
            item.add_argument('--url', required=True)
        if action == 'checkpoint':
            item.add_argument('--phase', choices=['executing', 'waiting_user', 'paused', 'complete'], required=True)
            item.add_argument('--note-file', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'new':
        dom_config = Path(os.environ.get('SELFGUIDE_BRIDGE_HOME', BASE / 'dom-bridge')).expanduser() / 'config.json'
        project = ({'name':'selfguide','url':json.loads(dom_config.read_text())['project_url']}
                   if dom_config.exists() else json.loads(project_file(BASE).read_text()))
        if project['name'] not in ['selfguide', 'astra']:
            raise ValueError('All new conversations must belong to selfguide.')
        task = text_file(args.task_file)
        run = args.workspace.resolve() / 'tasks' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        run.mkdir(parents=True, mode=0o700)
        for directory in ['uploads', 'messages', 'feedback', 'outputs', 'checks']:
            (run / directory).mkdir(mode=0o700)
        (run / 'task.txt').write_text(task)
        state = {'id': run.name, 'project_name': 'selfguide', 'project_url': project['url'],
                 'conversation_url': None, 'phase': 'ready', 'round': 0, 'rounds': [],
                 'created_at': now(), 'events': [], 'layout_version': 2, 'reply_format': 'selfguide-text-v1'}
        save(run / 'state.json', state)
        print(json.dumps({'run': str(run), 'project_url': project['url']}))
        return
    run = args.run.resolve()
    with (run / '.state.lock').open('w') as lock, ExitStack() as held:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = run / 'state.json'
        state = json.loads(path.read_text())
        result = {'run': str(run)}
        if args.action == 'status':
            if args.brief:
                # Keep recovery output bounded as the on-disk audit history grows.
                result = {key: state[key] for key in (
                    'id', 'project_url', 'conversation_url', 'phase', 'round',
                    'resume_phase', 'latest_note') if key in state}
                current = state.get('rounds', [])[-1:]
                result['current'] = {key: current[0][key] for key in (
                    'number', 'outgoing', 'incoming', 'incoming_source',
                    'reply_format_valid') if key in current[0]} if current else None
                result['run'] = str(run)
                print(json.dumps(result, ensure_ascii=False)); return
            print(json.dumps(state, ensure_ascii=False, indent=2)); return
        if state['phase'] == 'complete' and args.action != 'resume':
            raise ValueError('This task is complete; resume it to continue the same conversation, or create a new task.')
        if args.action == 'stage':
            if state['phase'] not in ['ready', 'executing']:
                raise ValueError('Stage attachments before preparing the next message.')
            source = args.file.resolve(strict=True)
            if not source.is_file() or source.name == 'manifest.json':
                raise ValueError('Select one regular file with a nonreserved filename.')
            uploads = run / 'uploads'
            uploads.mkdir(exist_ok=True, mode=0o700)
            target = uploads / source.name
            if target.exists():
                raise ValueError('An attachment with this name already exists; use a distinct filename.')
            with source.open('rb') as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            digest = hashlib.sha256()
            with target.open('rb') as staged:
                for chunk in iter(lambda: staged.read(1024 * 1024), b''):
                    digest.update(chunk)
            manifest_path = uploads / 'manifest.json'
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files': []}
            manifest['files'].append({'name': target.name, 'source': str(source),
                                      'bytes': target.stat().st_size, 'sha256': digest.hexdigest(),
                                      'status': 'staged', 'staged_at': now()})
            save(manifest_path, manifest)
            result['file'] = str(target)
        elif args.action == 'prepare':
            if state['phase'] not in ['ready', 'executing']:
                raise ValueError('Resolve the previous handoff first; do not duplicate an uncertain send.')
            text = text_file(args.file)
            if state.get('reply_format') == 'selfguide-text-v1':
                text += reply_instruction(state, state['round'] + 1)
            if len(text.encode()) > 65536:
                raise ValueError('Split this message into task-relevant portions of at most 64 KB.')
            state['round'] += 1
            name = ('messages/' if state.get('layout_version', 1) >= 2 else '') + f"out-{state['round']:03}.txt"
            (run / name).write_text(text)
            state['rounds'].append({'number': state['round'], 'outgoing': name,
                                    'outgoing_sha256': hashlib.sha256(text.encode()).hexdigest()})
            state['phase'] = 'prepared'
            result['file'] = str(run / name)
        elif args.action == 'submitting':
            if state['phase'] != 'prepared':
                raise ValueError('Only a prepared message can be marked for submission.')
            state['phase'] = 'send_pending'
        elif args.action == 'sent':
            if state['phase'] != 'send_pending':
                raise ValueError('Confirm the pending browser submission first.')
            parsed = urlsplit(args.url)
            project_match = re.fullmatch(r'/g/(g-p-[A-Za-z0-9]+)(?:-[^/]+)?/project', urlsplit(state['project_url']).path)
            if not project_match:
                raise ValueError('The configured project URL is invalid.')
            project_key = project_match.group(1)
            if (parsed.scheme != 'https' or parsed.netloc != 'chatgpt.com' or parsed.query or parsed.fragment
                    or not re.fullmatch('/g/' + re.escape(project_key) + r'(?:-[^/]+)?/c/[A-Za-z0-9-]+', parsed.path)):
                raise ValueError('The conversation URL must be inside the configured selfguide project.')
            if state['conversation_url'] and urlsplit(state['conversation_url']).path.rsplit('/', 1)[-1] != parsed.path.rsplit('/', 1)[-1]:
                raise ValueError('Continue in the original task conversation.')
            state['conversation_url'] = args.url
            state['rounds'][-1]['sent_at'] = now()
            state['phase'] = 'waiting_reply'
        elif args.action == 'reply':
            if state['phase'] != 'waiting_reply':
                raise ValueError('Record a confirmed send before accepting its reply.')
            text = text_file(args.file)
            metadata = {'incoming_source': args.source or 'legacy-unspecified'}
            if state.get('reply_format') == 'selfguide-text-v1':
                if not args.source:
                    raise ValueError('Declare the actual text source: --source clipboard, dom, or download. OCR is not a reply source.')
                try:
                    validate_reply(text, state)
                    metadata['reply_format_valid'] = True
                except ValueError as error:
                    if not args.format_note_file:
                        raise
                    metadata.update({'reply_format_valid': False, 'format_error': str(error),
                                     'format_review_note': text_file(args.format_note_file)})
            name = ('feedback/' if state.get('layout_version', 1) >= 2 else '') + f"in-{state['round']:03}.txt"
            (run / name).write_text(text)
            state['rounds'][-1].update({'incoming': name, 'incoming_sha256': hashlib.sha256(text.encode()).hexdigest(),
                                        'received_at': now()})
            state['rounds'][-1].update(metadata)
            state['phase'] = 'executing'
            result['file'] = str(run / name)
        elif args.action == 'checkpoint':
            if args.phase in ['executing', 'complete'] and state['phase'] != 'executing':
                raise ValueError('Do not bypass an unresolved handoff; confirm and read the reply first.')
            note = text_file(args.note_file)
            name = ('checks/' if state.get('layout_version', 1) >= 2 else '') + f"note-{len(state['events']) + 1:03}.txt"
            (run / name).write_text(note)
            if args.phase in ['paused', 'waiting_user'] and state['phase'] not in ['paused', 'waiting_user']:
                state['resume_phase'] = state['phase']
            state['phase'] = args.phase
            state['latest_note'] = name
        elif args.action == 'resume':
            if state['phase'] == 'complete':
                checks = run / 'checks'; checks.mkdir(exist_ok=True)
                cleanup_lock = held.enter_context((checks / 'window-cleanup.lock').open('a'))
                fcntl.flock(cleanup_lock, fcntl.LOCK_EX)
                from cleanup_windows import require_settled
                require_settled(run)
                state['phase'] = 'executing'
                result['restore_url'] = state['conversation_url']
            else:
                if state['phase'] not in ['paused', 'waiting_user'] or 'resume_phase' not in state:
                    raise ValueError('Only a paused or completed task can be resumed.')
                state['phase'] = state.pop('resume_phase')
        state['updated_at'] = now()
        state['events'].append({'at': state['updated_at'], 'action': args.action, 'phase': state['phase'], 'round': state['round']})
        save(path, state)
        result['phase'] = state['phase']
        if args.action == 'checkpoint' and state['phase'] == 'complete':
            try:
                from cleanup_windows import enqueue
                result['window_cleanup'] = enqueue(run, recheck=True)
            except Exception as error:
                # Completion remains recorded even when the browser is offline.
                result['window_cleanup'] = {'status': 'deferred', 'reason': type(error).__name__}
        print(json.dumps(result))

if __name__ == '__main__':
    main()
