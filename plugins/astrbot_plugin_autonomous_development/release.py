"""Trusted publication/activation profiles. Never execute a model-supplied host command."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

from .policy import relative_path, validate_files
from .runner import Workspace


def sha(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, content):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    original = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix='.evolution-', dir=path.parent)
    try:
        os.fchmod(fd, original.st_mode & 0o777 if original else 0o600)
        if os.getuid() == 0:
            owner = original or path.parent.stat()
            os.fchown(fd, owner.st_uid, owner.st_gid)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class Release:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / '.runtime/evolution-releases'
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)

    def source_base(self, repository):
        repository=Path(repository).resolve()
        if repository not in (self.root,self.root/'Coopanion'):
            raise PermissionError('源码基线不属于本项目登记仓库')
        ws=Workspace(repository, self.folder/'unused')
        human=ws.git('rev-parse','HEAD').decode().strip()
        path=self.folder/'source-baselines.json'
        records=json.loads(path.read_text()) if path.exists() else {}
        record=records.get(str(repository))
        if not record:return human
        tip=record['commit']
        ws.git('rev-parse','--verify',tip+'^{commit}')
        if human==record['human_anchor']:return tip
        try:ws.git('merge-base','--is-ancestor',tip,human)
        except subprocess.CalledProcessError as exc:
            raise PermissionError('人工源码基线已变化且未包含已发布改动；暂停自主修改，需人工核对') from exc
        return human

    def record_source(self, repository, commit, *, expected):
        repository=Path(repository).resolve()
        current=self.source_base(repository)
        if current==commit:return  # Durable completion recovered after a lost caller reply.
        if current!=expected:
            raise PermissionError('源码基线已推进，当前任务不能覆盖新版本')
        ws=Workspace(repository,self.folder/'unused')
        ws.git('merge-base','--is-ancestor',expected,commit)
        path=self.folder/'source-baselines.json'
        records=json.loads(path.read_text()) if path.exists() else {}
        records[str(repository)]={'commit':commit,'human_anchor':ws.git('rev-parse','HEAD').decode().strip(),'at':time.time()}
        atomic(path,json.dumps(records).encode());path.chmod(0o600)

    def restore_source(self, state):
        repository=state.get('source_repository')
        if not repository:return
        path=self.folder/'source-baselines.json'
        records=json.loads(path.read_text()) if path.exists() else {}
        record=records.get(repository)
        if record and record['commit']==state['commit']:
            records[repository]={**record,'commit':state['source_base_before'],'at':time.time()}
            atomic(path,json.dumps(records).encode());path.chmod(0o600)

    def state_path(self, task_id):
        import re
        if not re.fullmatch(r'dev_[a-f0-9]{32}', task_id): raise ValueError('无效任务编号')
        return self.folder / task_id / 'activation.json'

    def target(self, path):
        path = relative_path(path)
        parts = Path(path).parts
        if parts[:2] == ('vendor', 'installed-plugins') and len(parts) > 3:
            target = self.root / 'data/astrbot/plugins' / Path(*parts[2:])
        elif parts[0] == 'plugins' and len(parts) > 2:
            name = parts[1]
            # Existing bind-mounted plugins are activated through their source directory.
            target = self.root / path if name in ('astrbot_plugin_desktop_pet', 'astrbot_plugin_autonomous_development') else self.root / 'data/astrbot/plugins' / Path(*parts[1:])
        elif parts[0] in ('docs', 'tests'):
            target = self.root / path
        else:
            raise PermissionError('此路径需要新增受控发布配置；不会执行任意部署脚本')
        if target.is_symlink() or not target.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in target.parents if p.is_relative_to(self.root)):
            raise PermissionError('发布目标存在链接逃逸')
        return target

    def code_parent(self, target):
        """New approved code directories inherit the local maintainer, not root's umask."""
        missing=[];parent=target.parent
        while not parent.exists():
            if not parent.is_relative_to(self.root):raise PermissionError('发布目录超出项目')
            missing.append(parent);parent=parent.parent
        owner=parent.stat()
        for folder in reversed(missing):
            folder.mkdir(mode=0o700)
            if os.getuid()==0:os.chown(folder,owner.st_uid,owner.st_gid)

    def push(self, meta):
        repo = Path(meta['repository']).resolve()
        if repo not in (self.root, self.root/'Coopanion'):
            raise PermissionError('发布仓库不是本项目登记的父仓库或客户端')
        if meta.get('base_commit') and self.source_base(repo) not in (meta['base_commit'],meta['commit']):
            raise PermissionError('任务基线已过期，请核对新版本，不上传旧分支')
        expected = 'https://github.com/zhizhangzhu/' + ('Coopanion.git' if repo.name == 'Coopanion' else 'AstrBot_snowluma_for_zzz.git')
        ws = Workspace(repo, repo / '.runtime/no-credentials')
        actual = ws.git('remote', 'get-url', 'origin').decode().strip()
        if actual.removesuffix('.git') != expected.removesuffix('.git'):
            raise PermissionError('远程仓库与登记来源不匹配')
        branch, commit = meta['branch'], meta['commit']
        if not branch.startswith('codex/autonomous-'):
            raise PermissionError('仅能上传任务分支')
        # The trusted publisher may access its own gh credential helper; task code cannot.
        command = ['/usr/bin/git', '-c', 'safe.directory='+str(repo), '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.fsmonitor=false', '-c', 'credential.helper=',
            '-c', 'credential.helper=!/usr/bin/gh auth git-credential', '-C', str(repo), 'push', expected,
            commit + ':refs/heads/' + branch]
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
        result = subprocess.run(command, capture_output=True, env=env, timeout=90)
        # Do not retry an uncertain upload. Verify the remote reference first.
        verify = subprocess.run(command[:command.index('push')] + ['ls-remote', expected, 'refs/heads/' + branch], capture_output=True, env=env, timeout=30)
        confirmed = verify.returncode == 0 and verify.stdout.decode().startswith(commit + '\t')
        if not confirmed:
            raise RuntimeError('上传未核实；请检查发布凭据或远程分支，不盲目重试')
        if repo == self.root/'Coopanion':
            self.record_source(repo,commit,expected=meta['base_commit'])
        return {'uploaded': True, 'uploaded_commit': commit, 'remote_branch': branch, 'uploaded_at': time.time()}

    def activate(self):
        result = subprocess.run(['/usr/bin/docker', 'restart', '--time', '30', 'xiaoxiaoqing-astrbot'], capture_output=True, timeout=100)
        if result.returncode:
            raise RuntimeError('AstrBot重启失败')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:6185/', timeout=3) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        raise RuntimeError('管理服务未恢复健康')

    def deploy(self, task_id, meta, folder):
        state_file = self.state_path(task_id)
        if state_file.exists():
            prior = json.loads(state_file.read_text())
            if prior.get('state') == 'active':
                return {'deployed': True, 'deployed_commit': prior['commit'], 'deployed_at': prior['at']}
            raise RuntimeError('此版本已有未完成的发布回执，请先核实或恢复')
        if Path(meta['repository']).name == 'Coopanion':
            raise PermissionError('客户端需要Windows构建与安装回执；服务器不能代称客户端已更新')
        if self.source_base(meta['repository'])!=meta['base_commit']:
            raise PermissionError('发布源码基线已变化，旧任务必须重新核对')
        ws = Workspace(Path(meta['repository']), Path(folder) / 'workspace'); ws.base = meta['base_commit']
        diff = ws.diff(meta['paths'])
        if diff['diff_sha256'] != meta.get('verified_diff') or meta.get('diff_sha256') != meta.get('verified_diff'):
            raise PermissionError('发布差异未通过当前版本检查')
        backups = state_file.parent; backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        changes = []
        for item in diff['changes']:
            target = self.target(item['path'])
            try: expected = ws.git('show', meta['base_commit'] + ':' + item['path'])
            except subprocess.CalledProcessError: expected = b''
            current = target.read_bytes() if target.exists() else b''
            if current != expected:
                raise PermissionError('线上或人工代码已改变，旧版本不能覆盖：' + item['path'])
            content = ws.path(item['path']).read_bytes()
            validate_files({item['path']: content.decode()})
            backup = backups / (str(len(changes)) + '.before')
            if target.exists(): backup.write_bytes(current); backup.chmod(0o600)
            changes.append({'path': str(target.relative_to(self.root)), 'existed': target.exists(),
                'before': sha(current), 'after': sha(content), 'backup': backup.name, 'source': item['path']})
        state = {'commit': meta['commit'], 'at': time.time(), 'state': 'preparing', 'changes': changes,
            'source_repository':str(Path(meta['repository']).resolve()), 'source_base_before':meta['base_commit']}
        atomic(state_file, json.dumps(state).encode()); state_file.chmod(0o600)
        touched = []
        try:
            for change in changes:
                target = self.root / change['path']
                self.code_parent(target)
                atomic(target, ws.path(change['source']).read_bytes()); touched.append(change)
            state['state'] = 'activating'; atomic(state_file, json.dumps(state).encode())
            if any('/plugins/' in '/' + c['path'] for c in changes): self.activate()
            self.record_source(meta['repository'],meta['commit'],expected=meta['base_commit'])
        except BaseException:
            # Restore only the exact files written by this activation, never user data.
            state['changes'] = touched; state['state'] = 'failed'; atomic(state_file, json.dumps(state).encode())
            self.rollback(task_id, restart=False)
            if any('/plugins/' in '/' + c['path'] for c in touched):
                try: self.activate()
                except Exception: pass
            raise
        state['state'] = 'active'; atomic(state_file, json.dumps(state).encode())
        return {'deployed': True, 'deployed_commit': meta['commit'], 'deployed_at': state['at']}

    def rollback(self, task_id, restart=True):
        path = self.state_path(task_id)
        state = json.loads(path.read_text())
        if state['state'] == 'rolled_back': return {'deployed': False, 'restored': True}
        if state['state']=='active' and state.get('source_repository') and self.source_base(state['source_repository'])!=state['commit']:
            raise PermissionError('已有后续源码版本，不能单独回滚旧发布，请先核对依赖')
        for change in state['changes']:
            target = self.root / change['path']
            if target.is_symlink() or not target.resolve().is_relative_to(self.root): raise PermissionError('恢复路径异常')
            current = target.read_bytes() if target.exists() else b''
            if sha(current) != change['after']:
                raise PermissionError('发布后出现人工改动，停止恢复：' + change['path'])
        for change in reversed(state['changes']):
            target = self.root / change['path']
            if change['existed']:
                content = (path.parent / change['backup']).read_bytes()
                if sha(content) != change['before']: raise PermissionError('恢复副本校验失败')
                atomic(target, content)
            else: target.unlink(missing_ok=True)
        if restart and any('/plugins/' in '/' + c['path'] for c in state['changes']): self.activate()
        self.restore_source(state)
        state['state'] = 'rolled_back'; state['restored_at'] = time.time()
        atomic(path, json.dumps(state).encode())
        return {'deployed': False, 'restored': True, 'restored_at': state['restored_at']}
