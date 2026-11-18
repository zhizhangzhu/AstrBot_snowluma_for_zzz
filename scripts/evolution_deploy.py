#!/usr/bin/env python3
"""Checked opt-in deployment overlay. Rollback removes code hooks, never user records."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.runtime/evolution-deployment'
MARKER=ROOT/'.runtime/evolution.enabled'
CONTAINER='xiaoxiaoqing-astrbot'

def run(*argv):return subprocess.check_output(argv,text=True).strip()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def prepare():
    run('python3',str(ROOT/'scripts/evolution_sources.py'),'verify')
    FOLDER.mkdir(parents=True,exist_ok=True,mode=0o700)
    current=FOLDER/'index.live.html'
    subprocess.run(['docker','cp',CONTAINER+':/AstrBot/astrbot/dashboard/dist/index.html',str(current)],check=True,capture_output=True)
    html=current.read_text()
    # Idempotent updates preserve the original native app and all existing overlays.
    tag='<script type="module" src="/evolution-dashboard.js" data-astrbot-evolution></script>'
    import re
    html=re.sub(r'<script\b[^>]*data-astrbot-evolution[^>]*>\s*</script>','',html)
    (FOLDER/'index.html').write_text(html.replace('</body>',tag+'\n</body>'))
    (FOLDER/'baseline.json').write_text(json.dumps({'image':run('docker','inspect','--format','{{.Image}}',CONTAINER),'prepared_at':time.time(),
        'index_sha256':digest(FOLDER/'index.html')}))
    # Host authority is pinned outside the model-visible source workspaces.
    control=ROOT/'.runtime/evolution-control'
    modules=control/'plugins/astrbot_plugin_autonomous_development'
    modules.mkdir(parents=True,exist_ok=True,mode=0o700)
    hashes={}
    for name in ('__init__.py','policy.py','runner.py','release.py','worker.py','updates.py','container_runner.py'):
        source=ROOT/'plugins/astrbot_plugin_autonomous_development'/name
        target=modules/name
        target.write_bytes(source.read_bytes());target.chmod(0o400)
        hashes[name]=digest(target)
    image=run('docker','inspect','--format','{{.Image}}',CONTAINER)
    image_spec=json.loads(run('docker','image','inspect',image))[0]
    keys=[entry.split('=',1)[0] for entry in image_spec['Config'].get('Env',[])]
    (control/'manifest.json').write_text(json.dumps({'format':1,'files':hashes,'prepared_at':time.time(),
        'runner_image':image,'image_environment_keys':keys}))
    (control/'manifest.json').chmod(0o400)
    import shutil
    bank=ROOT/'.runtime/evolution-regression-bank'
    if bank.exists():shutil.rmtree(bank)
    (bank/'companion').mkdir(parents=True,mode=0o700)
    shutil.copytree(ROOT/'tests/autonomous_evolution',bank/'autonomous',ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','*.pyc'))
    shutil.copyfile(ROOT/'tests/autonomous_evolution/pytest.ini',bank/'pytest.ini')
    shutil.copyfile(ROOT/'data/astrbot/plugins/astrbot_plugin_private_companion/tests/test_send_tracking_hot_reload.py',bank/'companion/test_send_tracking_hot_reload.py')
    for path in bank.rglob('*'):
        path.chmod(0o555 if path.is_dir() else 0o444)
    print('已准备自主成长插件和费用页面；尚未切换服务。')

def apply():
    spec=json.loads((FOLDER/'baseline.json').read_text())
    if run('docker','inspect','--format','{{.Image}}',CONTAINER)!=spec['image'] or digest(FOLDER/'index.html')!=spec['index_sha256']:
        raise SystemExit('镜像或预备页面已变化，请重新准备。')
    subprocess.run(['python3',str(ROOT/'scripts/evolution_service.py'),'start'],check=True)
    MARKER.write_text('version=1\n')
    print('已启用启动覆盖层。通过 start.sh 或 scripts/common.sh 重建 AstrBot 服务后生效；QQ 实例无须重启。')

def disable():
    MARKER.unlink(missing_ok=True)
    subprocess.run(['python3',str(ROOT/'scripts/evolution_service.py'),'stop'],check=True)
    print('已停用自主成长覆盖层。重建 AstrBot 即恢复原入口；任务、账本、聊天及配置均保留。')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','apply','disable']);args=parser.parse_args()
    {'prepare':prepare,'apply':apply,'disable':disable}[args.action]()
