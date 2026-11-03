#!/usr/bin/env python3
"""Reproducible checks using the actual AstrBot image, with no user data/network mounts."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'.runtime/evolution-check'
sys.path.insert(0,str(ROOT/'plugins'))
from astrbot_plugin_autonomous_development.runner import Workspace

def copy_sources(source,target):
    for path in source.rglob('*'):
        rel=path.relative_to(source)
        if any(p in ('.git','__pycache__','node_modules','.pytest_cache','data','logs','dist','build') for p in rel.parts):continue
        if path.is_file() and not path.is_symlink() and (path.suffix in ('.py','.json','.js','.ts','.html','.css','.yaml','.yml','.md','.toml','.ini') or path.name=='requirements.txt'):
            dest=target/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)

def prepare():
    STAGE.mkdir(parents=True,exist_ok=True)
    source=STAGE/'source'
    if source.exists():shutil.rmtree(source)
    Workspace(ROOT,source).prepare()
    for folder in ('plugins','tests','scripts','overrides'):
        copy_sources(ROOT/folder,source/folder)
    for name in ('astrbot_plugin_private_companion','astrbot_plugin_memory_companion','meme_manager'):
        copy_sources(ROOT/'data/astrbot/plugins'/name,source/'plugins'/name)
        alias = source/'data/astrbot/plugins'/name
        alias.parent.mkdir(parents=True, exist_ok=True)
        alias.symlink_to(Path('/work/plugins')/name)
    copy_sources(ROOT/'Coopanion',source/'Coopanion')
    print('Prepared source-only framework checks.',flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    prepare()
    if args.prepare_only:return
    image=subprocess.check_output(['docker','inspect','--format','{{.Image}}','xiaoxiaoqing-astrbot'],text=True).strip()
    result = 0
    previous=STAGE/'framework.log'
    if previous.exists():previous.rename(STAGE/('framework-'+str(time.time_ns())+'.log'))
    with previous.open('w') as log:
        for group in ('tests/autonomous_evolution','tests/desktop_pet','tests/dialogue_cognition',
                      'plugins/astrbot_plugin_private_companion/tests/test_send_tracking_hot_reload.py'):
            # Legacy companion tests intentionally reset sys.modules. Keep that fixture
            # out of other suites so extension type identities are not unloaded mid-test.
            cmd=['docker','run','--rm','--network','none','--entrypoint','python','-e','PYTHONPATH=/work:/work/plugins:/AstrBot',
                '-v',str(STAGE/'source')+':/work','-w','/work',image,'-m','pytest','-q','--rootdir=/work',
                '-c','tests/autonomous_evolution/pytest.ini',group,'-m','not host_isolation']
            log.write('\n=== ' + group + ' ===\n'); log.flush()
            result = max(result, subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT).returncode)
    print((STAGE/'framework.log').read_text()[-18000:])
    raise SystemExit(result)
if __name__=='__main__':main()
