"""Fixed-operation Unix broker. The model's engineering code only runs in Workspace.run."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import shutil
import time

from .policy import Identity, ROOT, validate_files
from .runner import Workspace
from .release import Release
from .updates import WindowsUpdates
from .container_runner import ContainerRunner

MAX = 6_000_000
# External publication cannot be unsubmitted by cancelling an awaiting Python thread.
ATOMIC_OPERATIONS = {'push', 'deploy', 'rollback', 'restart', 'client_build', 'pin_parent', 'push_parent', 'client_renew', 'commit'}


class Worker:
    def __init__(self, root=ROOT, *, runner_image=None, image_environment_keys=()):
        self.root = Path(root).resolve()
        self.state = self.root / '.runtime/evolution-worker'
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.getuid() == 0:
            os.chown(self.state, self.root.stat().st_uid, self.root.stat().st_gid)
        self.active = {}
        self.active_operations = {}
        self.mutation_lock = asyncio.Lock()
        self.releases = Release(self.root)
        self.windows = WindowsUpdates(self.root)
        self.container_runner = ContainerRunner(self.root,runner_image,environment_keys=image_environment_keys,
            bank=self.root/'.runtime/evolution-regression-bank') if runner_image else None

    def workspace(self, repository, directory):
        return Workspace(repository, directory,container_runner=self.container_runner,
            libraries=self.root / 'runtime/desktop-pet-development/venv/lib/python3.12/site-packages',
            framework=self.root / '.runtime/evolution-test-framework')

    def job(self, job_id):
        if not re.fullmatch(r'dev_[a-f0-9]{32}', job_id):
            raise ValueError('无效任务编号')
        return self.state / job_id

    def load(self, job_id):
        folder = self.job(job_id)
        meta = json.loads((folder / 'metadata.json').read_text())
        ws = self.workspace(Path(meta['repository']), folder / 'workspace')
        ws.base = meta['base_commit']
        ws.allowed = meta['paths']
        return folder, meta, ws

    @staticmethod
    def save(folder, meta):
        tmp = folder / 'metadata.tmp'
        tmp.write_text(json.dumps(meta, ensure_ascii=False)); tmp.chmod(0o600)
        tmp.replace(folder / 'metadata.json')

    def pin_parent(self, job_id, folder, meta):
        if Path(meta['repository']).resolve() != (self.root/'Coopanion').resolve() or not meta.get('committed'):
            raise PermissionError('只能固定本项目实际客户端提交')
        if meta.get('integration_commit'):
            return {k:meta[k] for k in ('integration_commit','integration_branch','integration_base')}
        parent = Workspace(self.root, folder/'unused')
        base = meta.get('parent_base') or parent.git('rev-parse','HEAD').decode().strip()
        if not re.fullmatch(r'[a-f0-9]{40,64}',base): raise ValueError('父仓库基础版本无效')
        entry = parent.git('ls-tree',base,'--','Coopanion').decode().strip()
        if not entry.startswith('160000 commit '): raise PermissionError('父仓库的Coopanion不是已登记子模块')
        branch = 'codex/autonomous-'+job_id[4:]+'-integration'
        export = folder/'parent-integration'
        if not export.exists(): parent.git('worktree','add','-b',branch,str(export),base)
        git = Workspace(export,folder/'unused')
        current = git.git('rev-parse','HEAD').decode().strip()
        if current != base:
            paths = git.git('diff','--name-only',base,current).decode().splitlines()
            target = git.git('ls-tree',current,'--','Coopanion').decode()
            if paths != ['Coopanion'] or meta['commit'] not in target:
                raise PermissionError('父仓库集成分支有其他变更，拒绝覆盖')
        else:
            git.git('update-index','--cacheinfo','160000,'+meta['commit']+',Coopanion')
            git.git('-c','user.name=小小情','-c','user.email=bot@localhost.invalid','commit','-m','chore: pin desktop client '+meta['commit'][:12])
            current = git.git('rev-parse','HEAD').decode().strip()
        return {'integration_commit':current,'integration_branch':branch,'integration_base':base}

    async def execute(self, payload):
        Identity(**payload['identity']).assert_owner()
        op, job_id = payload['operation'], payload['task_id']
        self.job(job_id)
        if op in ('cancel', 'status', 'read', 'inventory'):
            return await self.execute_once(payload)
        async with self.mutation_lock:
            task = asyncio.create_task(self.execute_once(payload))
            self.active[job_id] = task
            self.active_operations[job_id] = op
            try:
                return await asyncio.wait_for(task, 300)
            finally:
                self.active.pop(job_id, None)
                self.active_operations.pop(job_id, None)

    async def execute_once(self, payload):
        Identity(**payload['identity']).assert_owner()
        op, job_id = payload['operation'], payload['task_id']
        folder = self.job(job_id)
        if op == 'cancel':
            operation = self.active_operations.get(job_id)
            if job_id in self.active and operation in ATOMIC_OPERATIONS:
                return {'cancelled':False, 'completion_pending':True, 'operation':operation}
            if job_id in self.active:self.active[job_id].cancel()
            return {'cancelled':True}
        if op == 'inventory':
            from .policy import relative_path
            repository = self.root if payload.get('repository', 'parent') == 'parent' else self.root / 'Coopanion'
            if payload.get('repository', 'parent') not in ('parent', 'client'):
                raise ValueError('仓库未登记')
            ws = Workspace(repository, folder / 'unused')
            names = ws.git('ls-tree', '-r', '--name-only', 'HEAD').decode().splitlines()
            allowed = []
            for name in names:
                try: allowed.append(relative_path(name))
                except (PermissionError, ValueError): pass
            return {'base_commit': ws.git('rev-parse', 'HEAD').decode().strip(), 'files': allowed[:5000]}
        if op == 'prepare':
            if folder.exists():
                if not (folder / 'metadata.json').exists():
                    # Only an incomplete initial source copy can have no metadata.
                    # No engineering command is admitted until metadata is durable.
                    shutil.rmtree(folder)
                    return await self.execute_once(payload)
                existing = self.load(job_id)[1]
                if sorted(payload['paths']) != sorted(existing['paths']) or payload.get('checks') != existing['approved_checks']:
                    raise PermissionError('此任务的工程范围已经固定，扩大范围须建立新任务')
                return existing
            repository = self.root if payload.get('repository', 'parent') == 'parent' else self.root / 'Coopanion'
            if payload.get('repository', 'parent') not in ('parent', 'client'):
                raise ValueError('仓库未登记')
            folder.mkdir(mode=0o700)
            if os.getuid() == 0:
                os.chown(folder, repository.stat().st_uid, repository.stat().st_gid)
            paths = list(validate_files({p: '' for p in payload['paths']}))
            checks = payload.get('checks')
            if not isinstance(checks, list) or not checks or len(checks) > 12:
                raise ValueError('批准方案必须含1至12条实际检查命令')
            ws = self.workspace(repository, folder / 'workspace')
            base = payload.get('base_commit') or 'HEAD'
            if base != 'HEAD' and not re.fullmatch(r'[a-f0-9]{40,64}', base):
                raise ValueError('基础版本须为完整提交号')
            result = ws.prepare(base)
            meta = {**result, 'repository': str(repository), 'paths': paths, 'approved_checks': checks, 'checks': [], 'committed': False,
                'parent_base':payload.get('parent_base')}
            self.save(folder, meta)
            return meta
        if op == 'restart':
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            meta = json.loads((folder/'metadata.json').read_text()) if (folder/'metadata.json').exists() else {}
            if meta and meta.get('kind') != 'service_restart': raise PermissionError('任务不是服务重启')
            if meta.get('restarted'): return meta
            meta.update(kind='service_restart',state='restarting',target='xiaoxiaoqing-astrbot',started_at=time.time())
            self.save(folder, meta)
            await asyncio.to_thread(self.releases.activate)
            meta.update(state='completed',restarted=True,completed_at=time.time())
            self.save(folder, meta)
            return meta
        if op == 'status' and (folder/'metadata.json').exists():
            meta = json.loads((folder/'metadata.json').read_text())
            if meta.get('kind') == 'service_restart': return meta
        folder, meta, ws = self.load(job_id)
        if meta.get('committed') and op in ('write','run','verify'):
            raise PermissionError('已提交的工程产物不可再次执行修改命令')
        if op == 'read':
            start, limit = int(payload.get('start_line',1)), int(payload.get('limit_lines',200))
            return {'text': ws.read(payload['path'],start,limit),'start_line':start,'limit_lines':limit}
        if op == 'write':
            if meta['committed']:
                raise ValueError('已提交产物不可直接覆盖，须建立新任务')
            result = ws.write(payload['files'], meta['paths'])
            meta['checks'] = []
            meta.pop('verified_diff', None)
            self.save(folder, meta)
            return {'written': result}
        if op == 'run':
            result = await ws.run(payload['argv'], int(payload.get('timeout', 60)))
            meta['checks'].append({k: result[k] for k in ('argv', 'exit_code', 'isolated')})
            self.save(folder, meta)
            return result
        if op == 'verify':
            # Build from the clean approved base, never from scratch/test artifacts.
            diff = ws.diff(meta['paths'])
            if not diff['changes']:
                raise ValueError('没有实际变更')
            clean = folder / ('verification-' + str(time.time_ns()))
            verifier = self.workspace(Path(meta['repository']), clean)
            verifier.allowed = meta['paths']; verifier.export = False
            await asyncio.to_thread(verifier.prepare, meta['base_commit'])
            verifier.write({c['path']: ws.path(c['path']).read_text() for c in diff['changes']}, meta['paths'])
            python_files = [c['path'] for c in diff['changes'] if c['path'].endswith('.py')]
            checks = []
            if python_files:
                checks.append(['/usr/bin/python3', '-c', 'import ast,pathlib,sys;[ast.parse(pathlib.Path(p).read_text(),filename=p) for p in sys.argv[1:]]', *python_files])
            checks.extend(meta['approved_checks'])
            paths = meta['paths']
            if any('astrbot_plugin_autonomous_development/' in p for p in paths):
                checks.append(['/usr/bin/python3', '-m', 'pytest', '-q', 'tests/autonomous_evolution', '-m', 'not host_isolation'])
            if any('astrbot_plugin_desktop_pet/' in p for p in paths):
                checks.append(['/usr/bin/python3', '-m', 'pytest', '-q', 'tests/desktop_pet'])
            if self.container_runner is not None:
                # The fixed regression bank cannot be edited by an engineering task.
                if any('astrbot_plugin_autonomous_development/' in p for p in paths):
                    checks.append(['/usr/bin/python3','-m','pytest','-q','/opt/regression/autonomous',
                        '-c','/opt/regression/pytest.ini','-m','not host_isolation'])
                if any('private_companion/' in p or 'memory_companion/' in p or 'pipeline/' in p for p in paths):
                    checks.append(['/usr/bin/python3','-m','pytest','-q','/opt/regression/companion/test_send_tracking_hot_reload.py'])
            receipts = []
            for argv in checks:
                result = await verifier.run(argv, 180)
                receipts.append({k: result[k] for k in ('argv', 'exit_code', 'output', 'isolated')})
                if result['exit_code'] != 0:
                    break
            if ws.diff(meta['paths'])['diff_sha256'] != diff['diff_sha256']:
                raise PermissionError('检查期间工作区已改变')
            meta['checks'] = receipts
            meta['verified_diff'] = diff['diff_sha256'] if all(r['exit_code'] == 0 for r in receipts) else None
            self.save(folder, meta)
            return {**diff, 'checks': receipts, 'clean_verification': True}
        if op == 'cancel':
            if job_id in self.active:
                self.active[job_id].cancel()
            return {'cancelled': True}
        if op in ('diff', 'commit'):
            diff = ws.diff(meta['paths'])
            diff['checks'] = meta['checks']
            if op == 'diff':
                return diff
            if not diff['changes'] or meta.get('verified_diff') != diff['diff_sha256'] or not meta['checks'] or any(x['exit_code'] != 0 for x in meta['checks']):
                raise PermissionError('没有通过实际检查的变更')
            if payload.get('diff_sha256') != diff['diff_sha256']:
                raise PermissionError('产物版本已变化')
            if meta['committed']:
                return {'commit': meta['commit'], 'branch': meta['branch']}
            # Separate worktree preserves the human's current branch and uncommitted files.
            branch = 'codex/autonomous-' + job_id[4:]
            exported = folder / 'export'
            ws.git('worktree', 'add', '-b', branch, str(exported), meta['base_commit'])
            for change in diff['changes']:
                path = change['path']; target = exported / path
                if target.is_symlink() or not target.resolve().is_relative_to(exported.resolve()):
                    raise PermissionError('提交目标存在链接逃逸')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(ws.path(path).read_bytes())
            git = Workspace(exported, folder / 'unused')
            paths = [x['path'] for x in diff['changes']]
            git.git('add', '--', *paths)
            git.git('diff', '--cached', '--check')
            git.git('-c', 'user.name=小小情', '-c', 'user.email=bot@localhost.invalid', 'commit', '-m', 'feat: autonomous task ' + job_id)
            commit = git.git('rev-parse', 'HEAD').decode().strip()
            meta.update(committed=True, commit=commit, branch=branch, diff_sha256=diff['diff_sha256'])
            self.save(folder, meta)
            return {'commit': commit, 'branch': branch, 'uploaded': False, 'deployed': False}
        if op in ('push', 'deploy', 'rollback'):
            if not meta['committed']:
                raise PermissionError('产物尚未提交')
            if op != 'rollback' and payload.get('commit') != meta['commit']:
                raise PermissionError('提交版本不匹配')
            if op == 'push':
                result = await asyncio.to_thread(self.releases.push, meta)
            elif op == 'deploy':
                result = await asyncio.to_thread(self.releases.deploy, job_id, meta, folder)
            else:
                result = await asyncio.to_thread(self.releases.rollback, job_id)
            meta.update(result)
            self.save(folder, meta)
            return result
        if op == 'client_renew':
            if not meta.get('committed'): raise PermissionError('客户端产物尚未提交')
            result = await asyncio.to_thread(self.windows.renew_channel, job_id)
            meta['windows'] = result; self.save(folder, meta)
            return result
        if op in ('client_build', 'client_status'):
            if not meta.get('committed'): raise PermissionError('客户端产物尚未提交')
            result = await asyncio.to_thread(self.windows.start if op == 'client_build' else self.windows.status, job_id, meta)
            meta['windows'] = result
            self.save(folder, meta)
            return result
        if op == 'pin_parent':
            result = await asyncio.to_thread(self.pin_parent, job_id, folder, meta)
            meta.update(result);self.save(folder,meta)
            return result
        if op == 'push_parent':
            if not meta.get('integration_commit'): raise PermissionError('父仓库集成提交尚未准备')
            target={'repository':str(self.root),'branch':meta['integration_branch'],'commit':meta['integration_commit']}
            result=await asyncio.to_thread(self.releases.push,target)
            meta.update(integration_uploaded=result['uploaded']);self.save(folder,meta)
            return {'integration_uploaded':result['uploaded'],'integration_commit':meta['integration_commit']}
        if op == 'status':
            return {**meta, 'running': job_id in self.active}
        raise ValueError('未登记的工程操作')

    async def handle(self, reader, writer):
        try:
            credentials = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            uid = struct.unpack('3i', credentials)[1]
            if uid not in (0, os.getuid(), self.root.stat().st_uid):
                raise PermissionError('维护身份不匹配')
            raw = await asyncio.wait_for(reader.readline(), 15)
            if len(raw) > MAX:
                raise ValueError('请求过大')
            result = await self.execute(json.loads(raw))
            response = {'ok': True, 'result': result}
        except BaseException as exc:
            response = {'ok': False, 'error': type(exc).__name__, 'message': str(exc)[:500]}
        try:
            writer.write(json.dumps(response, ensure_ascii=True).encode() + b'\n')
            await writer.drain()
        finally:
            writer.close(); await writer.wait_closed()


class WorkerClient:
    def __init__(self, path):
        self.path = str(path)

    async def call(self, identity, task_id, operation, **values):
        from dataclasses import asdict
        identity.assert_owner()
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.path, limit=MAX), 5)
        try:
            writer.write(json.dumps({'identity': asdict(identity), 'task_id': task_id, 'operation': operation, **values}, ensure_ascii=True).encode() + b'\n')
            await writer.drain()
            body = json.loads(await asyncio.wait_for(reader.readline(), 330))
            if not body.get('ok'):
                raise RuntimeError(body.get('message', '工程执行失败'))
            return body['result']
        finally:
            writer.close(); await writer.wait_closed()
