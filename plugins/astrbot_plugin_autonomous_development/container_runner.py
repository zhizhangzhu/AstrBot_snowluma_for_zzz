"""Fixed-profile disposable containers. No host-writable mounts or Docker socket in jobs."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import uuid

from .policy import relative_path, validate_files

# Runs as the unprivileged container user, using only the public read-only source tree.
BOOTSTRAP = '''import os,pathlib,shutil,time
source=pathlib.Path('/source');work=pathlib.Path('/workspace')
shutil.copytree(source,work,dirs_exist_ok=True,symlinks=True)
(work/'data/astrbot/plugins').mkdir(parents=True,exist_ok=True)
for name in ('astrbot_plugin_private_companion','astrbot_plugin_memory_companion','meme_manager'):
 p=work/'vendor/installed-plugins'/name
 if p.exists():
  target=work/'data/astrbot/plugins'/name
  if not target.exists():target.symlink_to(p)
print('READY',flush=True)
time.sleep(600)
'''
CHECK = '''import json,os,pathlib,sys
root=pathlib.Path('/workspace').resolve()
for name in json.loads(sys.argv[1]):
 p=root/name
 if p.is_symlink() or not p.resolve().is_relative_to(root) or any(a.is_symlink() for a in p.parents if a.is_relative_to(root)):
  raise SystemExit(4)
 if p.exists() and (not p.is_file() or p.stat().st_size>2000000):raise SystemExit(5)
'''
EXPORT = '''import base64,hashlib,json,pathlib,sys
root=pathlib.Path('/workspace').resolve();result={};total=0
for name in json.loads(sys.argv[1]):
 p=root/name
 if p.is_symlink() or not p.resolve().is_relative_to(root) or any(a.is_symlink() for a in p.parents if a.is_relative_to(root)):raise SystemExit(4)
 if not p.exists():result[name]=None;continue
 if not p.is_file() or p.stat().st_size>2000000:raise SystemExit(5)
 raw=p.read_bytes();total+=len(raw)
 if total>5000000:raise SystemExit(6)
 result[name]={'sha256':hashlib.sha256(raw).hexdigest()}
 if sys.argv[2]=='export':result[name]['base64']=base64.b64encode(raw).decode('ascii')
print(json.dumps(result))
'''


class ContainerRunner:
    def __init__(self, root, image, *, environment_keys=(), bank=None):
        import re
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', str(image)):
            raise ValueError('工程镜像必须是预检固定的本地镜像摘要')
        self.root=Path(root).resolve();self.image=image
        self.environment_keys=[k for k in environment_keys if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',k)]
        self.bank=Path(bank) if bank else None

    async def cli(self,*args,timeout=60,max_output=100_000):
        process=await asyncio.create_subprocess_exec('/usr/bin/docker',*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
        async def capture(stream):
            output=bytearray()
            while chunk:=await stream.read(8192):
                if len(output)<max_output:output.extend(chunk[:max_output-len(output)])
            return bytes(output)
        readers=[asyncio.create_task(capture(process.stdout)),asyncio.create_task(capture(process.stderr))]
        try:
            await asyncio.wait_for(process.wait(),timeout)
        except BaseException:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait();raise
        finally:
            output=await asyncio.gather(*readers)
        return process.returncode,output[0],output[1]

    async def cleanup_stale(self):
        # Called only after the launcher's exclusive project lock; other containers are never selected.
        code, raw, _ = await self.cli('ps', '--all', '--filter', 'label=io.astrbot.evolution=true',
            '--format', '{{.Names}}', timeout=10)
        if code: raise RuntimeError('无法核验遗留工程容器，暂不接受开发命令')
        import re
        for name in raw.decode().splitlines():
            if re.fullmatch(r'astrbot-evolution-[a-f0-9]{32}', name):
                removed, _, _ = await self.cli('rm', '--force', '--', name, timeout=20)
                if removed: raise RuntimeError('遗留工程容器尚未清理，暂不接受开发命令')

    async def run(self, directory, argv, timeout=60, *, allowed=(), export=True):
        directory=Path(directory).resolve()
        if not directory.is_relative_to(self.root/'.runtime/evolution-worker'):
            raise PermissionError('工程容器仅接受本项目专用任务目录')
        allowed=[relative_path(p) for p in allowed]
        name='astrbot-evolution-'+uuid.uuid4().hex
        uid,gid=self.root.stat().st_uid,self.root.stat().st_gid
        command=['run','--detach','--name',name,'--label','io.astrbot.evolution=true','--init','--network','none',
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{uid}:{gid}',
            '--pids-limit','64','--memory','2g','--memory-swap','2g','--cpus','2','--log-driver','local',
            '--log-opt','max-size=1m','--log-opt','max-file=2','--log-opt','compress=false','--ulimit','nofile=1024:1024','--ulimit','fsize=52428800:52428800',
            '--tmpfs',f'/workspace:rw,nosuid,nodev,size=512m,uid={uid},gid={gid}',
            '--tmpfs',f'/tmp:rw,nosuid,nodev,size=128m,uid={uid},gid={gid}',
            '--tmpfs',f'/AstrBot/data:rw,nosuid,nodev,size=128m,uid={uid},gid={gid}',
            '--tmpfs',f'/root:rw,nosuid,nodev,size=1m,uid={uid},gid={gid}',
            '--mount',f'type=bind,source={directory},target=/source,readonly', '--workdir','/workspace',
            '--entrypoint','/usr/local/bin/python']
        if self.bank and self.bank.is_dir():command+=['--mount',f'type=bind,source={self.bank},target=/opt/regression,readonly']
        node=self.root/'runtime/desktop-pet-development/toolchain/node-v24.11.0-linux-x64/bin/node'
        if node.is_file():command+=['--mount',f'type=bind,source={node},target=/usr/local/bin/node,readonly']
        for key in self.environment_keys:command+=['--env',key+'=']
        for key,value in {'HOME':'/tmp','PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','TZ':'Asia/Shanghai',
                          'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':'/workspace:/workspace/plugins:/workspace/vendor/installed-plugins:/AstrBot',
                          'EVOLUTION_SOURCE_ROOT':'/workspace'}.items():command+=['--env',key+'='+value]
        command += [self.image,'-I','-S','-u','-c',BOOTSTRAP]
        try:
            code,out,error=await self.cli(*command,timeout=20)
            if code:raise RuntimeError('受控工程容器未能启动；不会降级为主机执行：'+error.decode('utf8',errors='replace')[:500])
            for _ in range(100):
                _,logs,_=await self.cli('logs',name,timeout=3)
                if b'READY\n' in logs:break
                await asyncio.sleep(.1)
            else:raise RuntimeError('工程容器的源码副本尚未就绪')
            # Interpreter paths are fixed to the Python in the verified image.
            if argv[0] in ('python','python3','/usr/bin/python3','/usr/bin/python3.12'):
                argv=['/usr/local/bin/python',*argv[1:]]
            elif argv[0] in ('node','/usr/bin/node'):
                argv=['/usr/local/bin/node',*argv[1:]]
            code,out,error=await self.cli('exec','--workdir','/workspace','--',name,*argv,timeout=timeout)
            if allowed:
                checked,serialized,_=await self.cli('exec','--',name,'/usr/local/bin/python','-I','-S','-c',EXPORT,json.dumps(allowed),'export' if export else 'verify',timeout=15,max_output=8_000_000)
                if checked:raise PermissionError('工程产物存在链接逃逸或超限文件')
                captured=json.loads(serialized)
                values={}
                for path in allowed:
                    target=directory/path;item=captured[path]
                    if not item:
                        if target.exists():raise PermissionError('工程命令删除了已批准文件，删除未获许可')
                        continue
                    if not export:
                        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:
                            raise PermissionError('检查过程中修改了受检源码，结果不能用于提交')
                    else:
                        raw=base64.b64decode(item['base64'],validate=True)
                        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise PermissionError('工程产物校验失败')
                        values[path]=raw.decode('utf8')
                if values:
                    validate_files(values)
                    for path in values:
                        target=directory/path
                        if not target.resolve().is_relative_to(directory) or any(p.is_symlink() for p in [target,*target.parents] if p.is_relative_to(directory)):
                            raise PermissionError('宿主工作副本存在路径或链接逃逸')
                    for path,value in values.items():
                        target=directory/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(value)
                        if os.getuid()==0:os.chown(target,uid,gid)
            return {'argv':argv,'exit_code':code,'output':(out+error).decode('utf8',errors='replace'),
                'isolated':True,'engine':'docker','limits':{'memory_bytes':2*1024**3,'pids':64,'cpus':2,'scratch_bytes':512*1024**2}}
        finally:
            await self.cli('rm','--force','--',name,timeout=20)
