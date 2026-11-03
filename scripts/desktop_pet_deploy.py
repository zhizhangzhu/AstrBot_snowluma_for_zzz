#!/usr/bin/env python3
"""Prepare, apply or roll back version-checked desktop terminal integration patches."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT/'overrides/desktop-pet'
RUNTIME = ROOT/'.runtime/desktop-pet'
MANIFEST = json.loads((PATCHES/'manifest.json').read_text())
CONTAINER = 'xiaoxiaoqing-astrbot'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def atomic(path, content, *, reference=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    info = reference.stat() if reference and reference.exists() else None
    fd, name = tempfile.mkstemp(prefix='.desktop-', dir=path.parent)
    try:
        os.fchmod(fd, info.st_mode & 0o777 if info else 0o600)
        if info and os.geteuid() == 0:
            os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def patch(directory, name):
    run('patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(PATCHES/name), cwd=directory)


def prepare():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stage-', dir=RUNTIME) as temporary:
        stage = Path(temporary)
        originals = {}
        for group, spec in MANIFEST['groups'].items():
            folder = stage/group
            for relative, expected in spec['files'].items():
                source = ROOT/spec['root']/relative
                if digest(source) != expected:
                    raise ValueError(f'Baseline changed: {spec["root"]}/{relative}; review and refresh the patch first')
                target = folder/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source.read_text(), encoding='utf-8')
                originals[str(source.relative_to(ROOT))] = expected
            patch(folder, spec['patch'])
        shutil.copyfile(PATCHES/'terminal_integration.py', stage/'companion/terminal_integration.py')
        # The temporary container is never started and has no account or user-data mounts.
        image = run('docker', 'inspect', '--format', '{{.Image}}', CONTAINER)
        created = run('docker', 'create', '--entrypoint', '/bin/true', image)
        try:
            target = stage/'astrbot'
            target.mkdir()
            run('docker', 'cp', f'{created}:/AstrBot/{MANIFEST["astrbot"]["path"]}', str(target/'follow_up.py'))
        finally:
            run('docker', 'rm', created)
        if digest(target/'follow_up.py') != MANIFEST['astrbot']['sha256']:
            raise ValueError('AstrBot image baseline changed; review the transport guard before deployment')
        (target/'follow_up.py').write_text((target/'follow_up.py').read_text())
        patch(target, MANIFEST['astrbot']['patch'])
        files = {}
        for group, spec in MANIFEST['groups'].items():
            for source in (stage/group).rglob('*.py'):
                files[str(Path(spec['root'])/source.relative_to(stage/group))] = str(source.relative_to(stage))
        state = {'format':1, 'prepared_at':time.time(), 'image':image, 'originals':originals,
                 'files':files, 'patched':{name:digest(stage/value) for name,value in files.items()},
                 'framework_sha256':digest(target/'follow_up.py')}
        atomic(stage/'state.local.json', json.dumps(state, indent=2).encode())
        destination = RUNTIME/'prepared'
        if destination.exists(): shutil.rmtree(destination)
        shutil.copytree(stage, destination)
    print('Prepared checked patches. Live sources, configuration and services are unchanged.')


def require_stopped():
    if run('docker','inspect','--format','{{.State.Running}}',CONTAINER) != 'false':
        raise ValueError('Stop only the AstrBot service before applying or rolling back; keep the QQ services running')


def apply(enable_schedule):
    require_stopped()
    stage = RUNTIME/'prepared'
    state = json.loads((stage/'state.local.json').read_text())
    for path, expected in state['originals'].items():
        if digest(ROOT/path) != expected:
            raise ValueError(f'Live source changed since preparation: {path}')
    writes = {}
    for target, source in state['files'].items():
        if digest(stage/source) != state['patched'][target]: raise ValueError('Prepared source changed')
        writes[target] = (stage/source).read_bytes()
    framework = stage/'astrbot/follow_up.py'
    if digest(framework) != state['framework_sha256']: raise ValueError('Prepared framework changed')
    writes['.runtime/desktop-pet/follow_up.py'] = framework.read_bytes()
    cfg = 'data/astrbot/config/astrbot_plugin_desktop_pet_config.json'
    writes[cfg] = (json.dumps({'enabled':True,'host':'0.0.0.0','port':6190},indent=2)+'\n').encode()
    if enable_schedule:
        cfg = 'data/astrbot/config/astrbot_plugin_private_companion_config.json'
        raw = (ROOT/cfg).read_bytes()
        value = json.loads(raw.decode('utf-8-sig'))
        if value.get('plugin_specific_persona_id') != '小小情': raise ValueError('Primary Companion persona changed')
        activity = 'extension:astrbot_plugin_desktop_pet:activity'
        for node in (value, value.get('real_schedule_config', {})):
            items = node.setdefault('real_schedule_allowed_extensions', [])
            if not isinstance(items,list): raise ValueError('Unexpected schedule allowlist type')
            if activity not in items: items.append(activity)
        encoded = (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
        writes[cfg] = (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + encoded
    tls = ROOT/'data/astrbot/plugin_data/astrbot_plugin_desktop_pet'
    if not all((tls/n).exists() for n in ('ca.pem','server.pem','server.key')):
        raise ValueError('Generate the dedicated IP certificate before applying')
    writes['.runtime/desktop-pet.enabled'] = b'protocol=1\n'
    backup = ROOT/'backups'/('desktop-pet-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,mode=0o700)
    restore = {}
    for relative in writes:
        file=ROOT/relative
        restore[relative]={'exists':file.exists()}
        if file.exists():
            target=backup/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(file,target)
            restore[relative].update({'mode':file.stat().st_mode & 0o777,'uid':file.stat().st_uid,'gid':file.stat().st_gid})
    atomic(backup/'restore.local.json',json.dumps(restore,indent=2).encode())
    try:
        for relative, content in writes.items(): atomic(ROOT/relative,content,reference=ROOT/relative)
    except BaseException:
        rollback(backup,check=False)
        raise
    print('Applied. Backup:',backup)
    print('Start AstrBot through start.sh or scripts/common.sh so the WSS overlay and framework guard load together.')


def rollback(backup, *, check=True):
    if check: require_stopped()
    backup=backup.resolve()
    if backup.parent != (ROOT/'backups').resolve(): raise ValueError('Expected a desktop backup in this project')
    state=json.loads((backup/'restore.local.json').read_text())
    for relative, info in state.items():
        target=(ROOT/relative).resolve()
        if not target.is_relative_to(ROOT.resolve()): raise ValueError('Invalid backup path')
        if not info['exists']:
            target.unlink(missing_ok=True)
        else:
            atomic(target,(backup/relative).read_bytes())
            os.chmod(target,info['mode'])
            if os.geteuid()==0: os.chown(target,info['uid'],info['gid'])
    print('Restored code and configuration. User databases, memories and device pairing were not replaced.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('prepare')
    install=commands.add_parser('apply');install.add_argument('--enable-schedule',action='store_true')
    restore=commands.add_parser('rollback');restore.add_argument('backup',type=Path)
    args=parser.parse_args()
    if args.command=='prepare':prepare()
    elif args.command=='apply':apply(args.enable_schedule)
    else:rollback(args.backup)

if __name__=='__main__':main()
