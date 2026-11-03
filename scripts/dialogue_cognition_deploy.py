#!/usr/bin/env python3
"""Apply/verify the reviewed source-only overlay; never migrate user data."""
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

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'overrides/dialogue-cognition'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def stopped():
    state=subprocess.check_output(['docker','inspect','--format','{{.State.Running}}','xiaoxiaoqing-astrbot'],text=True).strip()
    if state!='false':raise RuntimeError('Stop only AstrBot before apply/rollback; preserve both QQ instances')

def atomic(path,data,reference=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    info=reference.stat() if reference and reference.exists() else None
    fd,name=tempfile.mkstemp(prefix='.cognition-',dir=path.parent)
    try:
        os.fchmod(fd,(info.st_mode&0o777) if info else 0o644)
        if info and os.geteuid()==0:os.fchown(fd,info.st_uid,info.st_gid)
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def entries():
    manifest=json.loads((SOURCE/'manifest.json').read_text())
    return manifest['groups']

def apply():
    stopped();groups=entries()
    if all(sha(ROOT/g['root']/p)==spec['after'] for g in groups.values() for p,spec in g['files'].items()):
        print('Already applied');return
    with tempfile.TemporaryDirectory(prefix='cognition-stage-') as temporary:
        stage=Path(temporary);writes={}
        for name,g in groups.items():
            directory=stage/name;directory.mkdir()
            for relative,spec in g['files'].items():
                target=ROOT/g['root']/relative
                if sha(target)!=spec['before']:raise RuntimeError('Source baseline changed: '+str(target.relative_to(ROOT)))
                local=directory/relative;local.parent.mkdir(parents=True,exist_ok=True)
                if spec['before'] is not None:shutil.copy2(target,local)
            subprocess.run(['patch','--batch','--forward','--fuzz=0','-p1','-i',str(SOURCE/g['patch'])],cwd=directory,check=True,capture_output=True)
            for relative,spec in g['files'].items():
                local=directory/relative
                if 'source' in spec:shutil.copyfile(SOURCE/spec['source'],local)
                if sha(local)!=spec['after']:raise RuntimeError('Prepared source differs: '+relative)
                writes[str(Path(g['root'])/relative)]=local.read_bytes()
        backup=ROOT/'backups'/(SOURCE.name+'-apply-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True)
        state={}
        for relative,data in writes.items():
            target=ROOT/relative;state[relative]={'before':sha(target),'after':hashlib.sha256(data).hexdigest()}
            if target.exists():
                dest=backup/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,dest)
        (backup/'source-state.local.json').write_text(json.dumps(state,indent=2))
        for relative,data in writes.items():atomic(ROOT/relative,data,ROOT/relative)
        print('Applied source overlay; rollback backup: '+str(backup))

def rollback(backup):
    stopped();state=json.loads((backup/'source-state.local.json').read_text())
    for relative,spec in state.items():
        if sha(ROOT/relative)!=spec['after']:raise RuntimeError('Source changed since deployment: '+relative)
    for relative,spec in state.items():
        target=ROOT/relative
        if spec['before'] is None:target.unlink()
        else:
            original=backup/relative
            if sha(original)!=spec['before']:raise RuntimeError('Backup hash differs: '+relative)
            atomic(target,original.read_bytes(),target)
    print('Source rollback complete; user state untouched')

def main():
    global SOURCE
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['check','apply','rollback']);parser.add_argument('backup',nargs='?',type=Path)
    parser.add_argument('--overlay',choices=['dialogue-cognition','desktop-availability'],default='dialogue-cognition')
    args=parser.parse_args()
    SOURCE=ROOT/'overrides'/args.overlay
    if args.action=='apply':apply()
    elif args.action=='rollback':
        if args.backup is None:parser.error('rollback requires the generated backup directory')
        rollback(args.backup)
    else:
        failures=[]
        for group in entries().values():
            for relative,spec in group['files'].items():
                if sha(ROOT/group['root']/relative)!=spec['after']:failures.append(str(Path(group['root'])/relative))
        if failures:raise SystemExit('Source drift: '+', '.join(failures))
        print('All deployed source hashes match the reviewed cognition overlay')

if __name__=='__main__':main()
