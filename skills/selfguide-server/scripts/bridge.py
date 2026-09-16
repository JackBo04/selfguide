#!/usr/bin/env python3
"""Loopback-only, durable command mailbox for a paired local browser extension."""
import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid

from config_paths import browser_home

BASE = Path(os.environ['SELFGUIDE_BRIDGE_HOME']).expanduser() if os.environ.get('SELFGUIDE_BRIDGE_HOME') else browser_home() / 'dom-bridge'
LIMIT = 12 * 1024 * 1024
ACTIONS = {'status', 'snapshot', 'project', 'compose', 'send', 'reply', 'reply-status', 'attach', 'open', 'focus'}
MUTATIONS = ACTIONS - {'status', 'snapshot', 'reply', 'reply-status'}

def session_key(run):
    state = json.loads((run.expanduser().resolve() / 'state.json').read_text())
    key = state['id']
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', key) or key == 'legacy':
        raise ValueError('Invalid task ID for an isolated browser window.')
    return key


def lane(command):
    return command.get('session', 'legacy')


def validate_run(command, run):
    """Check local handoff identity before a job or watcher is created."""
    state = json.loads((run.expanduser().resolve() / 'state.json').read_text())
    if command.get('session') != state['id']:
        raise ValueError('Task window does not match --run.')
    action = command['action']
    if action in {'compose', 'send', 'attach'}:
        allowed = {'compose': {'prepared'}, 'send': {'send_pending'},
                   'attach': {'ready', 'executing', 'prepared'}}[action]
        if state.get('phase') not in allowed:
            raise ValueError(f"Cannot {action} in task phase {state.get('phase')}; inspect the current handoff first.")
    if action in {'compose', 'send', 'reply', 'reply-status'}:
        current = state.get('rounds', [])[-1:]
        digest = hashlib.sha256(command['text'].encode()).hexdigest()
        if not current or current[0].get('outgoing_sha256') != digest:
            raise ValueError('Message does not match this task\'s current prepared handoff; use its outgoing file.')
    if action in {'compose', 'send', 'attach', 'reply', 'reply-status'} and state.get('conversation_url'):
        if command.get('expected_url') != state['conversation_url']:
            raise ValueError('Expected URL does not match this task\'s saved conversation.')


def project_key(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'chatgpt.com' or p.query or p.fragment:
        raise ValueError('Use the normal HTTPS ChatGPT project URL, without query parameters.')
    m = re.fullmatch(r'/g/(g-p-[a-zA-Z0-9]+)(?:-[^/]+)?/project', p.path)
    if not m:
        raise ValueError('Expected a ChatGPT project URL ending in /project.')
    return m.group(1)

def config():
    return json.loads((BASE / 'config.json').read_text())

def database():
    db = sqlite3.connect(BASE / 'jobs.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT, state TEXT, created REAL, claimed REAL, result TEXT)')
    return db

def validate(command, cfg):
    if command.get('action') not in ACTIONS:
        raise ValueError('Unknown action.')
    action = command['action']
    if 'session' in command and (not isinstance(command['session'], str) or
            not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', command['session']) or command['session'] == 'legacy'):
        raise ValueError('Invalid browser session.')
    if 'restore' in command and (action != 'open' or not isinstance(command['restore'], bool)):
        raise ValueError('Restore is only available for opening a task window.')
    if action in {'open', 'focus'} and 'session' not in command:
        raise ValueError('Use --run to select an isolated task window.')
    if action == 'project' and 'session' in command:
        raise ValueError('Use open for an isolated task; never reset its conversation.')
    if action not in {'project', 'status'} or command.get('expected_url'):
        p = urlsplit(command.get('expected_url', ''))
        key = project_key(cfg['project_url'])
        if (p.scheme != 'https' or p.netloc != 'chatgpt.com' or p.query or p.fragment or
                not re.fullmatch('/g/' + re.escape(key) + r'(?:-[^/]+)?/(?:project|c/[A-Za-z0-9-]+)', p.path)):
            raise ValueError('Expected URL must belong to the configured project.')
    if action in {'compose', 'send', 'reply', 'reply-status'}:
        if not isinstance(command.get('text'), str) or not command['text'].strip() or len(command['text'].encode()) > 65536:
            raise ValueError('Provide nonempty text up to 64 KB.')
        # The final pair is the current handoff; quoted older rounds may precede it.
        markers = re.findall(r'^SELFGUIDE_REPLY_(BEGIN|END) task=([A-Za-z0-9_-]+) round=(\d+)\s*$', command['text'], re.M)
        if command.get('session') and markers:
            if (len(markers) < 2 or markers[-2][0] != 'BEGIN' or markers[-1][0] != 'END' or
                    markers[-2][1:] != markers[-1][1:] or markers[-1][1] != command['session']):
                raise ValueError('Message task/round markers do not match the selected task window.')
    if action == 'attach':
        f = command.get('file', {})
        data = base64.b64decode(f.get('base64', ''), validate=True)
        if not data or len(data) > 8 * 1024 * 1024 or Path(f.get('name', '')).name != f.get('name'):
            raise ValueError('Attachment must have a basename and contain 1 byte to 8 MiB.')
        if hashlib.sha256(data).hexdigest() != f.get('sha256'):
            raise ValueError('Attachment hash mismatch.')
    return command

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Payloads and pairing credentials must not enter access logs.

    def respond(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            cfg = self.server.cfg
            host = self.headers.get('Host', '').split(':')[0]
            if host not in {'127.0.0.1', 'localhost'}:
                return self.respond(403, {'error': 'Loopback Host required.'})
            origin = self.headers.get('Origin')
            if origin and not re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
                return self.respond(403, {'error': 'Web page origins are not allowed.'})
            role = 'browser' if self.path in {'/info', '/poll', '/result'} else 'agent'
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied, 'Bearer ' + cfg[role + '_token']):
                return self.respond(401, {'error': 'Pairing token required.'})
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= LIMIT:
                return self.respond(413, {'error': 'Request too large or empty.'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('JSON object required.')
            with database() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE jobs SET state='expired' WHERE state='queued' AND created<?", (time.time() - 60,))
                if self.path == '/info':
                    result = {'project_url': cfg['project_url'], 'project_key': project_key(cfg['project_url'])}
                elif self.path == '/command':
                    command = validate(body['command'], cfg)
                    ident = body['id']
                    if not re.fullmatch(r'[a-f0-9]{32}', ident):
                        raise ValueError('Invalid command ID.')
                    raw = json.dumps(command, sort_keys=True)
                    old = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
                    if old:
                        if old['payload'] != raw:
                            raise ValueError('Command ID reused with a different payload.')
                    else:
                        unresolved = db.execute("SELECT payload FROM jobs WHERE state IN ('queued','claimed')").fetchall()
                        if command['action'] in MUTATIONS and any(json.loads(row['payload'])['action'] in MUTATIONS and lane(json.loads(row['payload'])) == lane(command) for row in unresolved):
                            raise ValueError('An earlier mutation in this task is unresolved. Inspect its job; do not resend.')
                        db.execute('INSERT INTO jobs VALUES (?,?,?, ?,NULL,NULL)', (ident, raw, 'queued', time.time()))
                    result = {'id': ident}
                elif self.path == '/poll':
                    excluded = body.get('exclude_sessions', [])
                    if not isinstance(excluded, list) or len(excluded) > 1024 or any(
                            not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', key) for key in excluded):
                        raise ValueError('Invalid session exclusions.')
                    blocked = set(excluded)
                    blocked.update(lane(json.loads(row['payload'])) for row in
                                   db.execute("SELECT payload FROM jobs WHERE state='claimed'"))
                    row = next((row for row in db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created")
                                if lane(json.loads(row['payload'])) not in blocked), None)
                    if row:
                        db.execute("UPDATE jobs SET state='claimed',claimed=? WHERE id=?", (time.time(), row['id']))
                        result = {'id': row['id'], 'command': json.loads(row['payload']), 'project_url': cfg['project_url']}
                    else:
                        result = {'idle': True}
                elif self.path == '/result':
                    row = db.execute('SELECT * FROM jobs WHERE id=?', (body['id'],)).fetchone()
                    raw = json.dumps(body['result'], sort_keys=True)
                    if not row or row['state'] not in {'claimed', 'done'}:
                        raise ValueError('No claimed command with this ID.')
                    if row['state'] == 'done' and row['result'] != raw:
                        raise ValueError('A different result was already recorded.')
                    db.execute("UPDATE jobs SET state='done',result=? WHERE id=?", (raw, body['id']))
                    result = {'ok': True}
                elif self.path == '/job':
                    row = db.execute('SELECT id,state,result FROM jobs WHERE id=?', (body['id'],)).fetchone()
                    if not row:
                        return self.respond(404, {'error': 'Unknown job.'})
                    result = dict(row)
                    result['result'] = json.loads(row['result']) if row['result'] else None
                elif self.path == '/resolve':
                    if not body.get('note', '').strip():
                        raise ValueError('Record what the visible page showed before resolving uncertainty.')
                    count = db.execute("UPDATE jobs SET state='resolved',result=? WHERE id=? AND state IN ('claimed','queued')",
                                       (json.dumps({'recovery_note': body['note']}), body['id'])).rowcount
                    if not count:
                        raise ValueError('No unresolved job with that ID.')
                    result = {'resolved': True}
                else:
                    return self.respond(404, {'error': 'Unknown route.'})
            self.respond(200, result)
        except (ValueError, KeyError, TypeError) as exc:
            self.respond(400, {'error': str(exc)})
        except Exception:
            self.respond(500, {'error': 'Bridge failure; inspect local state without resubmitting.'})

def request(route, body):
    cfg = config()
    req = Request(f"http://127.0.0.1:{cfg['port']}" + route, data=json.dumps(body).encode(),
                  headers={'Authorization': 'Bearer ' + cfg['agent_token'], 'Content-Type': 'application/json'})
    with urlopen(req, timeout=10) as response:
        return json.load(response)

def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init'); init.add_argument('--project-url', required=True); init.add_argument('--port', type=int, default=8766)
    sub.add_parser('serve'); sub.add_parser('pairing')
    job = sub.add_parser('job'); job.add_argument('id')
    resolve = sub.add_parser('resolve'); resolve.add_argument('id'); resolve.add_argument('--note-file', type=Path, required=True)
    for name in ACTIONS:
        command = sub.add_parser(name)
        if name == 'open': command.add_argument('--restore', action='store_true', help='Explicitly restore a closed/unverified task window from its saved conversation URL.')
        command.add_argument('--run', type=Path, help='Route exclusively to this task window.')
        if name != 'project': command.add_argument('--expect-url', required=name not in {'status', 'open'})
        if name in {'compose', 'send', 'reply', 'reply-status', 'attach'}: command.add_argument('--file', type=Path, required=True)
        command.add_argument('--out', type=Path, required=True)
        command.add_argument('--timeout', type=int, default=45)
    args = p.parse_args()
    if args.action == 'init':
        project_key(args.project_url)
        if not 1024 <= args.port <= 65535: raise ValueError('Choose a port from 1024 to 65535.')
        BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = BASE / 'config.json'
        if path.exists(): raise ValueError('Already configured; keep existing pairing or edit config intentionally.')
        cfg = {'project_url': args.project_url, 'port': args.port, 'agent_token': secrets.token_hex(32), 'browser_token': secrets.token_hex(32)}
        with path.open('x') as f: json.dump(cfg, f, indent=2)
        print(json.dumps({'configured': str(path), 'next': 'Run serve on the server, then pairing to configure your local extension.'}))
    elif args.action == 'pairing':
        cfg = config()
        print(json.dumps({'endpoint': f"http://127.0.0.1:{cfg['port']}", 'token': cfg['browser_token'], 'project_url': cfg['project_url']}, indent=2))
    elif args.action == 'serve':
        cfg = config(); server = ThreadingHTTPServer(('127.0.0.1', cfg['port']), Handler); server.cfg = cfg
        print('Local browser mailbox listening on loopback.', flush=True)
        server.serve_forever()
    elif args.action == 'job':
        print(json.dumps(request('/job', {'id': args.id}), ensure_ascii=False, indent=2))
    elif args.action == 'resolve':
        print(json.dumps(request('/resolve', {'id': args.id, 'note': args.note_file.read_text()})))
    else:
        cmd = {'action': args.action}
        if args.run:
            args.run = args.run.expanduser().resolve()
            cmd['session'] = session_key(args.run)
        if args.action == 'open' and args.restore:
            cmd['restore'] = True
        if args.action == 'open' and not args.expect_url and args.run:
            state = json.loads((args.run / 'state.json').read_text())
            args.expect_url = state.get('conversation_url') or state['project_url']
        if args.action != 'project' and args.expect_url: cmd['expected_url'] = args.expect_url
        if args.action in {'compose', 'send', 'reply', 'reply-status'}: cmd['text'] = args.file.read_text()
        if args.action == 'attach':
            f = args.file.resolve(strict=True)
            if not f.is_file() or not 0 < f.stat().st_size <= 8 * 1024 * 1024: raise ValueError('Select a file up to 8 MiB; summarize larger inputs.')
            data = f.read_bytes()
            cmd['file'] = {'name': f.name, 'mime': mimetypes.guess_type(f.name)[0] or 'application/octet-stream',
                           'base64': base64.b64encode(data).decode(), 'sha256': hashlib.sha256(data).hexdigest()}
        validate(cmd, config())
        if args.run:
            validate_run(cmd, args.run)
        ident = uuid.uuid4().hex
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Persist the ID before networking. A timeout is never a license to create another send.
        with args.out.open('x') as f: json.dump({'id': ident, 'state': 'submitting'}, f)
        request('/command', {'id': ident, 'command': cmd})
        deadline = time.monotonic() + min(max(args.timeout, 1), 55)
        result = {'id': ident, 'state': 'queued'}
        while time.monotonic() < deadline:
            result = request('/job', {'id': ident})
            if result['state'] not in {'queued', 'claimed'}: break
            time.sleep(1)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'id': ident, 'state': result['state'], 'file': str(args.out.resolve())}))
        if result['state'] != 'done' or result.get('result', {}).get('error'): raise SystemExit(2)

if __name__ == '__main__':
    main()
