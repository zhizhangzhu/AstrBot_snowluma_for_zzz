"""Pinned Windows CI artifacts and a user-started, one-version installation channel."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import time
import zipfile

REPOSITORY = 'zhizhangzhu/Coopanion'
WORKFLOW = 'astrbot-terminal-windows.yml'
ARTIFACT = 'AstrBot-Desktop-Pet-Windows-x64'


class WindowsUpdates:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, task_id):
        if not re.fullmatch(r'dev_[a-f0-9]{32}', task_id): raise ValueError('无效更新任务')
        path = self.root / '.runtime/evolution-artifacts' / task_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def gh(self, *args):
        result = subprocess.run(['/usr/bin/gh', *args], capture_output=True, timeout=30)
        if result.returncode: raise RuntimeError('GitHub构建接口暂不可用，请检查发布身份与仓库权限')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def start(self, task_id, meta):
        if Path(meta['repository']).resolve() != (self.root/'Coopanion').resolve() or not meta.get('uploaded'):
            raise PermissionError('只有已上传的客户端任务提交可以申请Windows构建')
        path=self.folder(task_id)/'build.json'
        if path.exists():return json.loads(path.read_text())  # Never double-submit an uncertain workflow.
        record={'commit':meta['commit'],'branch':meta['branch'],'requested_at':time.time(),'state':'requesting'}
        path.write_text(json.dumps(record));path.chmod(0o600)
        try:
            self.gh('workflow','run',WORKFLOW,'--repo',REPOSITORY,'--ref',meta['branch'])
        except Exception:
            record['state']='submission_unknown';path.write_text(json.dumps(record));raise
        record['state']='queued';path.write_text(json.dumps(record));return record

    def renew_channel(self, task_id):
        """Only the authenticated management action can renew this exact verified artifact."""
        folder=self.folder(task_id);path=folder/'manifest.json'
        manifest=json.loads(path.read_text())
        setup=folder/'setup.exe'
        with setup.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=manifest['sha256']:
                raise PermissionError('安装包与已验收版本不一致，不开放更新通道')
        record=json.loads((folder/'build.json').read_text())
        if record.get('installed'):raise PermissionError('此版本已安装，不重复部署')
        receipt=folder/'receipt.json'
        if receipt.exists():receipt.rename(folder/('receipt-previous-'+str(time.time_ns())+'.json'))
        manifest.update(nonce=secrets.token_hex(32),created_at=time.time(),expires_at=time.time()+86400)
        manifest.pop('consumed_at',None);path.write_text(json.dumps(manifest));path.chmod(0o600)
        record.update(state='awaiting_windows_channel',installed=False)
        (folder/'build.json').write_text(json.dumps(record))
        return self.status(task_id,{})

    def status(self, task_id, meta):
        folder=self.folder(task_id);path=folder/'build.json'
        if not path.exists():return {'state':'not_requested'}
        record=json.loads(path.read_text())
        receipt=folder/'receipt.json'
        manifest_file=folder/'manifest.json'
        if manifest_file.exists():
            manifest=json.loads(manifest_file.read_text())
            public={k:manifest[k] for k in ('version','sha256','commit','run_id','expires_at','task_id')}
            if receipt.exists():
                response=json.loads(receipt.read_text(encoding='utf-8-sig'))
                if not secrets.compare_digest(str(response.get('nonce','')),manifest['nonce']):
                    raise PermissionError('Windows更新回执不是本次用户通道')
                if response.get('sha256')!=manifest['sha256'] or response.get('version')!=manifest['version']:
                    raise PermissionError('Windows实际安装版本与批准产物不一致')
                if response.get('status')=='installed':
                    record.update(state='installed',installed=True,installed_version=manifest['version'],receipt_at=time.time())
                else:record.update(state='installation_failed',installed=False,rollback=response.get('rollback',False))
                path.write_text(json.dumps(record))
                manifest['consumed_at'] = record['receipt_at'] if record.get('installed') else time.time()
                manifest_file.write_text(json.dumps(manifest))
                # Preserve the consumed, exact-version receipt; it grants no new install.
                return {**record,'artifact':public}
            return {**record,'state':'awaiting_windows_channel','artifact':public,
                'instruction':'请用户为本次版本执行Start-AstrBotDesktopUpdate.ps1；无连接时不自动安装'}
        runs=self.gh('run','list','--repo',REPOSITORY,'--workflow',WORKFLOW,'--branch',record['branch'],
            '--json','databaseId,headSha,status,conclusion,createdAt','--limit','20')
        matching=[r for r in runs if r['headSha']==record['commit']]
        if not matching:return {**record,'state':'waiting_ci_registration'}
        run=matching[0];record['run_id']=run['databaseId']
        if run['status']!='completed':return {**record,'state':'building'}
        if run.get('conclusion')!='success':
            record['state']='build_failed';path.write_text(json.dumps(record));return record
        artifacts=self.gh('api',f'repos/{REPOSITORY}/actions/runs/{run["databaseId"]}/artifacts')
        selected=next((a for a in artifacts.get('artifacts',[]) if a['name']==ARTIFACT and not a.get('expired')),None)
        if not selected or selected['size_in_bytes']>500_000_000:raise ValueError('缺少有效Windows验收安装包，或包过大')
        archive=folder/'build.zip'
        with archive.open('wb') as output:
            def bound():
                import resource
                resource.setrlimit(resource.RLIMIT_FSIZE,(500_000_000,500_000_000))
            result=subprocess.run(['/usr/bin/gh','api',f'repos/{REPOSITORY}/actions/artifacts/{int(selected["id"])}/zip'],
                stdout=output,stderr=subprocess.PIPE,timeout=90,preexec_fn=bound)
        if result.returncode:raise RuntimeError('安装包下载失败，不进入Windows安装阶段')
        extracted=folder/'verified-build';extracted.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            entries=z.infolist()
            if len(entries)>200 or sum(i.file_size for i in entries)>1_000_000_000:raise ValueError('构建产物超过安全大小')
            for item in entries:
                p=PurePosixPath(item.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr>>16):
                    raise PermissionError('构建产物存在路径或链接逃逸')
            z.extractall(extracted)
        reports=list(extracted.rglob('acceptance.json'))
        required = {'installer', 'original_ui', 'wss_pairing', 'chat_and_receipt', 'body_png_gif_receipt',
            'body_image_only_receipt', 'qq_avatar', 'message_times', 'windows_power_observer', 'hide_restore', 'reinstall_preserves_pairing'}
        report = json.loads(reports[0].read_text()) if len(reports) == 1 else {}
        if not required.issubset(report) or any(report[k] != 'passed' for k in required):
            raise PermissionError('原生Windows验收未全部通过')
        from .runner import Workspace
        version=json.loads(Workspace(self.root/'Coopanion',folder/'unused').git('show',record['commit']+':package.json'))['version']
        if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):raise ValueError('安装版本格式无效')
        setups=list(extracted.rglob('AstrBot-Desktop-Pet-Setup-'+version+'.exe'))
        if len(setups)!=1:raise PermissionError('安装包版本不符合已检查的客户端提交')
        setup=folder/'setup.exe'
        import shutil
        shutil.copyfile(setups[0],setup)
        with setup.open('rb') as source:
            fingerprint=hashlib.file_digest(source,'sha256').hexdigest()
        manifest={'format':1,'task_id':task_id,'version':version,'sha256':fingerprint,'commit':record['commit'],
            'run_id':record['run_id'],'created_at':time.time(),'expires_at':time.time()+86400,'nonce':secrets.token_hex(32)}
        manifest_file.write_text(json.dumps(manifest));manifest_file.chmod(0o600)
        record['state']='awaiting_windows_channel';path.write_text(json.dumps(record))
        return self.status(task_id,meta)
