#!/usr/bin/env python3
"""Apply a hash-checked, reversible Qzone source update with services quiescent."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
NAME='astrbot_plugin_private_companion'
FILES=['qzone_comments.py','qzone_native_actions.py','qzone_interactions.py','qzone_runtime.py']
STATE=ROOT/'.runtime/qzone-interactions-20261111/deployment'
RUNTIME=ROOT/'runtime/snowluma-native/index.mjs'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def stopped():
    assert subprocess.check_output(['docker','inspect','-f','{{.State.Running}}','xiaoxiaoqing-astrbot'],text=True).strip()=='false','Stop AstrBot first'
    for bot in ('xiaoxiaoqing','chuxiaoqing'):
        result=subprocess.run(['docker','exec',bot+'-snowluma','supervisorctl','status','snowluma'],text=True,capture_output=True)
        assert 'STOPPED' in result.stdout,'Stop only the SnowLuma protocol process first: '+bot


def atomic(path, data):
    info=path.stat() if path.exists() else path.parent.stat()
    fd,name=tempfile.mkstemp(prefix='.qzone-update-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        os.chmod(name,(info.st_mode&0o777) if path.exists() else 0o644)
        if os.geteuid()==0:os.chown(name,info.st_uid,info.st_gid)
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def prepare():
    if (STATE/'state.json').exists():raise RuntimeError('Prepared state already exists; inspect it before replacing')
    STATE.mkdir(parents=True,exist_ok=True)
    subprocess.run(['python3',str(ROOT/'scripts/prepare_qzone_interactions.py'),str(RUNTIME),str(STATE/'index.prepared.mjs')],check=True)
    manifest=json.loads((ROOT/'overrides/evolution-source-baseline.json').read_text())
    entries={}
    for name in FILES:
        target=ROOT/'data/astrbot/plugins'/NAME/name
        expected=manifest['plugins'][NAME]['files'].get(name)
        assert sha(target)==expected,'Live source changed: '+name
        entries[str(target.relative_to(ROOT))]={'before':expected,'after':sha(ROOT/'vendor/installed-plugins'/NAME/name)}
    entries[str(RUNTIME.relative_to(ROOT))]={'before':sha(RUNTIME),'after':sha(STATE/'index.prepared.mjs')}
    for relative,spec in entries.items():
        p=ROOT/relative
        if p.exists():
            backup=STATE/'before'/relative;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,backup)
    for relative in ['overrides/evolution-source-baseline.json','runtime/snowluma-native/patch-manifest.json']:
        backup=STATE/'before'/relative;backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,backup)
    (STATE/'state.json').write_text(json.dumps({'files':entries,'prepared_at':time.time()},indent=2)+'\n')
    print('Prepared source backup and candidate bundle:',STATE)


def apply():
    stopped();state=json.loads((STATE/'state.json').read_text())
    for relative,spec in state['files'].items():
        assert sha(ROOT/relative)==spec['before'],'Source changed: '+relative
        source=STATE/'index.prepared.mjs' if ROOT/relative==RUNTIME else ROOT/'vendor/installed-plugins'/NAME/Path(relative).name
        assert sha(source)==spec['after'],'Candidate changed: '+relative
    for relative,spec in state['files'].items():
        target=ROOT/relative
        source=STATE/'index.prepared.mjs' if target==RUNTIME else ROOT/'vendor/installed-plugins'/NAME/target.name
        if target==RUNTIME:
            # Existing containers bind-mount this inode. Do not replace it.
            with target.open('wb') as out:out.write(source.read_bytes());out.flush();os.fsync(out.fileno())
        else:atomic(target,source.read_bytes())
        assert sha(target)==spec['after']
    p=ROOT/'overrides/evolution-source-baseline.json';manifest=json.loads(p.read_text())
    for name in FILES:manifest['plugins'][NAME]['files'][name]=sha(ROOT/'vendor/installed-plugins'/NAME/name)
    atomic(p,(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode())
    p=ROOT/'runtime/snowluma-native/patch-manifest.json';manifest=json.loads(p.read_text())
    manifest['patched_sha256']=sha(RUNTIME);manifest['changes'].append('2026-11-11: read Qzone likers and reply to explicit comment parents')
    atomic(p,(json.dumps(manifest,indent=2)+'\n').encode())
    sync_containers(state, 'after', STATE/'index.prepared.mjs')
    print('Source update applied; start SnowLuma protocol processes, then AstrBot')


def rollback():
    stopped();state=json.loads((STATE/'state.json').read_text())
    for relative,spec in state['files'].items():assert sha(ROOT/relative)==spec['after'],'Source changed since application'
    for relative,spec in state['files'].items():
        target=ROOT/relative
        if spec['before'] is None:target.unlink()
        elif target==RUNTIME:
            with target.open('wb') as out:out.write((STATE/'before'/relative).read_bytes());out.flush();os.fsync(out.fileno())
        else:atomic(target,(STATE/'before'/relative).read_bytes())
    for relative in ['overrides/evolution-source-baseline.json','runtime/snowluma-native/patch-manifest.json']:
        atomic(ROOT/relative,(STATE/'before'/relative).read_bytes())
    sync_containers(state, 'before', STATE/'before/runtime/snowluma-native/index.mjs')
    print('Source rollback complete; chat, memory and login data preserved')


def sync_containers(state, version, source):
    expected=state['files']['runtime/snowluma-native/index.mjs'][version]
    for bot in ('xiaoxiaoqing','chuxiaoqing'):
        name=bot+'-snowluma'
        current=subprocess.check_output(['docker','exec',name,'sha256sum','/app/runtime/index.mjs'],text=True).split()[0]
        allowed=set(state['files']['runtime/snowluma-native/index.mjs'].values())
        assert current in allowed,'Container bundle changed: '+name
        if current!=expected:
            subprocess.run(['docker','cp',str(source),name+':/app/runtime/index.mjs'],check=True)
        actual=subprocess.check_output(['docker','exec',name,'sha256sum','/app/runtime/index.mjs'],text=True).split()[0]
        assert actual==expected,'Container bundle differs: '+name


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','apply','rollback','check']);args=parser.parse_args()
    if args.action=='check':
        state=json.loads((STATE/'state.json').read_text())
        for relative,spec in state['files'].items():assert sha(ROOT/relative)==spec['after'],relative
        for bot in ('xiaoxiaoqing','chuxiaoqing'):
            actual=subprocess.check_output(['docker','exec',bot+'-snowluma','sha256sum','/app/runtime/index.mjs'],text=True).split()[0]
            assert actual==state['files']['runtime/snowluma-native/index.mjs']['after'],'Container runtime mismatch: '+bot
        print('Deployed source hashes and both container bundles match candidate')
    else:globals()[args.action]()
