#!/usr/bin/env python3
"""Manage the project-private namespace broker; never adopt an unrelated PID."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.runtime/evolution-transport'
PID=FOLDER/'worker.pid'
SCRIPT=ROOT/'scripts/evolution_worker.py'
UNIT='astrbot-evolution-publisher.service'
UNIT_SOURCE=ROOT/'deploy/systemd'/UNIT

def managed():
    if not Path('/usr/bin/systemctl').is_file():return False
    result=subprocess.run(['/usr/bin/systemctl','show',UNIT,'--property=FragmentPath','--value'],capture_output=True,text=True)
    return result.returncode==0 and Path(result.stdout.strip()).resolve()==UNIT_SOURCE.resolve()

def running():
    try:
        pid=int(PID.read_text());cmd=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return pid if str(SCRIPT).encode() in cmd else None
    except (OSError,ValueError):return None

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop','status']);args=parser.parse_args()
    pid=running()
    if args.action=='status':
        print(json.dumps({'running':bool(pid),'socket':(FOLDER/'worker.sock').is_socket(),'systemd_managed':managed()}));return
    if args.action=='stop':
        if managed():
            subprocess.run(['/usr/bin/systemctl','stop',UNIT],check=True,timeout=345)
        elif pid:
            os.kill(pid,signal.SIGTERM)
            for _ in range(50):
                if not running():break
                time.sleep(.1)
            if running():raise SystemExit('工程执行器正在结束在途操作，尚未确认退出；请稍后查看状态。')
        print('工程执行器已停止；任务与数据保留。');return
    if pid:return
    if managed():
        subprocess.run(['/usr/bin/systemctl','start',UNIT],check=True,timeout=30)
        for _ in range(50):
            if running() and (FOLDER/'worker.sock').is_socket():
                print('受限发布器已由系统服务启动。');return
            time.sleep(.1)
        raise SystemExit('受限发布器系统服务尚未就绪，请检查该服务状态。')
    if os.geteuid()!=0:raise SystemExit('工程执行器需要主机管理员创建隔离环境；不会降级为直接执行。')
    spec=json.loads((ROOT/'.runtime/evolution-control/manifest.json').read_text())
    test=subprocess.run(['/usr/bin/docker','image','inspect',spec['runner_image']],capture_output=True)
    if test.returncode:raise SystemExit('固定工程镜像预检失败，拒绝启动工程执行器。')
    FOLDER.mkdir(parents=True,exist_ok=True,mode=0o750)
    log=FOLDER/'worker.log'
    if log.exists() and log.stat().st_size>2_000_000:log.replace(log.with_suffix('.previous'))
    with log.open('ab') as stream:
        log.chmod(0o600)
        environment={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C.UTF-8'}
        for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','NO_PROXY','no_proxy'):
            if key in os.environ: environment[key]=os.environ[key]
        subprocess.Popen([sys.executable,str(SCRIPT)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,start_new_session=True,env=environment)
    for _ in range(50):
        if running() and (FOLDER/'worker.sock').is_socket():print('工程执行器已启动（仅本机私有连接）。');return
        time.sleep(.1)
    raise SystemExit('工程执行器未就绪，请检查私有运行日志。')
if __name__=='__main__':main()
