#!/usr/bin/env python3
"""Install or update SelfGuide on the Codex execution server, with local backups."""
import argparse
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import shutil
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', 'node_modules')


def build(destination, variant, name):
    shutil.copytree(ROOT/'skills'/('selfguide-'+variant), destination, dirs_exist_ok=True, ignore=IGNORE)
    if variant == 'server':
        shutil.copytree(ROOT/'runtime/server-browser', destination/'runtime/server-browser',
                        dirs_exist_ok=True, ignore=IGNORE)
    shutil.copytree(ROOT/'extension', destination/'extension', dirs_exist_ok=True, ignore=IGNORE)
    if name == 'selfguide':
        for file in [destination/'SKILL.md', destination/'agents/openai.yaml']:
            if file.exists():
                file.write_text(file.read_text().replace('selfguide-'+variant, 'selfguide'))


def matches(source, target):
    return all((target/file.relative_to(source)).is_file() and
               file.read_bytes() == (target/file.relative_to(source)).read_bytes()
               for file in source.rglob('*') if file.is_file())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('variant', choices=['server', 'local'])
    p.add_argument('--skills-dir', type=Path, default=Path.home()/'.agents/skills')
    p.add_argument('--update', action='store_true', help='Back up and update an existing installation; retain extra local files.')
    p.add_argument('--name', choices=['selfguide'], help='Keep the original single-skill invocation name.')
    p.add_argument('--backup-dir', type=Path, default=Path.home()/'.local/share/selfguide/skill-backups')
    a = p.parse_args()
    name = a.name or 'selfguide-'+a.variant
    if not (ROOT/'skills'/('selfguide-'+a.variant)/'SKILL.md').is_file():
        p.error('This bundle does not contain the selected variant.')
    a.skills_dir = a.skills_dir.expanduser().resolve()
    a.skills_dir.mkdir(parents=True, exist_ok=True)
    link = a.skills_dir/name
    if link.is_symlink() and not link.exists():
        p.error('The existing installation link is broken; restore its target first: '+str(link))
    target = link.resolve()
    if target.exists() and not target.is_dir():
        p.error('Installation target is not a directory: '+str(target))
    with (a.skills_dir/'.selfguide-install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='.selfguide-install-', dir=target.parent) as temp:
            staging = Path(temp)
            desired = staging/'desired'
            build(desired, a.variant, name)
            if target.exists() and matches(desired, target):
                print('Already up to date: '+str(link))
                return
            if target.exists() and not a.update:
                p.error('An existing installation differs. Run again with --update to back it up and update it.')
            if target.exists():
                backup_root = a.backup_dir.expanduser().resolve()
                if backup_root == target or target in backup_root.parents:
                    p.error('The backup directory must be outside the installation target.')
                backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                backup = backup_root/(name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
                shutil.copytree(target, backup, symlinks=True)
                # Build the complete replacement before swapping it into place.
                replacement = staging/'replacement'
                shutil.copytree(target, replacement, symlinks=True)
                # Replace packaged files without following customized symlinks.
                for source in desired.rglob('*'):
                    relative = source.relative_to(desired)
                    destination = replacement/relative
                    for parent in list(relative.parents)[:-1]:
                        if (replacement/parent).is_symlink():
                            raise ValueError('A packaged directory is a local symlink; review it before updating: '+str(relative))
                    if source.is_dir():
                        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
                            raise ValueError('A packaged directory conflicts with local content: '+str(relative))
                        destination.mkdir(exist_ok=True)
                    else:
                        if destination.is_symlink():
                            destination.unlink()
                        if destination.exists() and not destination.is_file():
                            raise ValueError('A packaged file conflicts with a local directory: '+str(relative))
                        shutil.copy2(source, destination)
                previous = staging/'previous'
                target.rename(previous)
                try:
                    replacement.rename(target)
                except BaseException:
                    previous.rename(target)
                    raise
                print('Updated '+str(link))
                print('Backup: '+str(backup))
                print('Packaged files replaced; extra local files retained. Custom edits to packaged files are in the backup.')
            else:
                desired.rename(target)
                print('Installed '+str(link))
    print('Start a new Codex conversation and invoke $'+name+'.')
    if a.variant == 'server':
        print('If runtime/server-browser/package-lock.json changed, run npm ci in runtime/server-browser. Keep the logged-in browser running.')
    else:
        print('After pending operations finish, replace the desktop extension files and reload the extension. Its next command reconnects existing tabs without refreshing them.')


if __name__ == '__main__':
    main()
