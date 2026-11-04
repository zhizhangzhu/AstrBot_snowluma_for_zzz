"""Host-side isolated engineering. No shell, host credentials or writable system mounts."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import pwd

from .policy import relative_path, validate_files


class Workspace:
    def __init__(self, repository, directory, *, bwrap='/usr/bin/bwrap', libraries=None, framework=None, container_runner=None, allowed=(), export=True):
        self.repository = Path(repository).resolve()
        self.directory = Path(directory).resolve()
        self.bwrap = bwrap
        self.base = None
        self.libraries, self.framework = libraries, framework
        self.container_runner, self.allowed, self.export = container_runner, allowed, export

    def git(self, *args, timeout=60):
        # Do not execute project hooks, credential helpers, external diff or user-defined Git aliases.
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.directory), 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0', 'LANG': 'C.UTF-8'}
        command = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'diff.external=', '-C', str(self.repository), *args]
        uid = self.repository.stat().st_uid
        if os.getuid() == 0 and uid != 0:
            command = ['/usr/sbin/runuser', '-u', pwd.getpwuid(uid).pw_name, '--', *command]
        return subprocess.run(command,
            env=env, capture_output=True, timeout=timeout, check=True).stdout

    def source_folder(self, folder):
        """Source copies belong to the engine UID even under a private service umask."""
        folder = Path(folder)
        if not folder.resolve().is_relative_to(self.directory):
            raise PermissionError('源码目录超出任务工作区')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        owner = self.repository.stat()
        for item in (folder, *folder.parents):
            if not item.is_relative_to(self.directory):break
            if item.is_symlink():raise PermissionError('源码目录不允许链接')
            item.chmod(0o700)
            if os.getuid()==0:os.chown(item,owner.st_uid,owner.st_gid)

    def source_file(self, path, mode=0o600):
        path.chmod(mode)
        if os.getuid()==0:
            owner=self.repository.stat();os.chown(path,owner.st_uid,owner.st_gid)

    def write_base_marker(self):
        marker=self.directory/'.base-commit'
        marker.write_text(self.base)
        self.source_file(marker)

    def prepare(self, commit='HEAD'):
        if self.directory.exists():
            raise ValueError('工作目录已存在，不覆盖旧任务')
        self.base = self.git('rev-parse', '--verify', commit + '^{commit}').decode().strip()
        self.source_folder(self.directory)
        if not self.git('ls-tree', '--name-only', self.base):
            self.write_base_marker()
            return {'base_commit': self.base}
        raw = self.git('archive', '--format=tar', self.base)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for member in archive:
                try:
                    relative_path(member.name)
                except (PermissionError, ValueError):
                    continue
                if not member.isfile():
                    continue
                target = self.directory / member.name
                self.source_folder(target.parent)
                with archive.extractfile(member) as source:
                    target.write_bytes(source.read())
                self.source_file(target,0o700 if member.mode & 0o111 else 0o600)
        self.write_base_marker()
        return {'base_commit': self.base}

    def path(self, name):
        name = relative_path(name)
        p = self.directory / name
        if not p.resolve().is_relative_to(self.directory) or any(parent.is_symlink() for parent in [p, *p.parents] if parent.is_relative_to(self.directory)):
            raise PermissionError('不允许工作区链接逃逸')
        return p

    def read(self, name, start_line=1, limit_lines=200):
        p = self.path(name)
        if not 1 <= int(start_line) <= 1_000_000 or not 1 <= int(limit_lines) <= 1000:
            raise ValueError('读取范围无效；每次最多1000行')
        import itertools
        with p.open() as source:
            text = ''.join(itertools.islice(source, int(start_line)-1, int(start_line)-1+int(limit_lines)))
        if len(text.encode()) > 200_000:
            raise ValueError('所选片段过大，请缩小读取行数')
        return text

    def write(self, files, allowed):
        files = validate_files(files)
        if not set(files) <= set(allowed):
            raise PermissionError('超出已批准的文件范围')
        for name, body in files.items():
            p = self.path(name)
            self.source_folder(p.parent)
            p.write_text(body)
            self.source_file(p,0o700 if p.stat().st_mode & 0o111 else 0o600)
        return list(files)

    async def run(self, argv, timeout=60):
        if not isinstance(argv, list) or not argv or len(argv) > 100 or not all(isinstance(a, str) and len(a) < 5000 and '\x00' not in a for a in argv):
            raise ValueError('无效命令参数')
        if not 1 <= timeout <= 300:
            raise ValueError('命令超时须为1至300秒')
        if self.container_runner is not None:
            return await self.container_runner.run(self.directory,argv,timeout,allowed=self.allowed,export=self.export)
        if not Path(self.bwrap).is_file():
            raise RuntimeError('隔离执行器不可用，拒绝在主机直接运行')
        cmd = [self.bwrap, '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
               '--ro-bind', '/usr', '/usr', '--ro-bind', '/bin', '/bin', '--ro-bind', '/lib', '/lib',
               '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home',
               '--bind', str(self.directory), '/workspace', '--chdir', '/workspace', '--clearenv',
               '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp', '--setenv', 'LANG', 'C.UTF-8']
        if Path('/lib64').exists():
            cmd += ['--ro-bind', '/lib64', '/lib64']
        python_paths = []
        for source, target in ((self.libraries, '/opt/test-libs'), (self.framework, '/opt/framework')):
            if source and Path(source).is_dir():
                cmd += ['--ro-bind', str(source), target]
                python_paths.append(target)
        if python_paths:
            cmd += ['--setenv', 'PYTHONPATH', ':'.join(python_paths)]
        cmd += ['--', *argv]
        def limits():
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
            resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024**2, 50 * 1024**2))
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 2))
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, preexec_fn=limits)
        output = bytearray()
        async def capture(stream):
            while chunk := await stream.read(8192):
                if len(output) < 100_000:
                    output.extend(chunk[:100_000-len(output)])
        readers = [asyncio.create_task(capture(process.stdout)), asyncio.create_task(capture(process.stderr))]
        try:
            await asyncio.wait_for(process.wait(), timeout)
        except BaseException:
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            raise
        finally:
            await asyncio.gather(*readers)
        return {'argv': argv, 'exit_code': process.returncode,
                'output': bytes(output).decode('utf8', errors='replace'), 'isolated': True}

    def diff(self, allowed):
        changes = []
        for name in allowed:
            name = relative_path(name)
            current = self.path(name)
            try:
                before = self.git('show', self.base + ':' + name)
            except subprocess.CalledProcessError:
                before = b''
            after = current.read_bytes() if current.exists() else b''
            if before != after:
                validate_files({name: after.decode('utf8')})
                import difflib
                patch = ''.join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
                    fromfile='a/' + name, tofile='b/' + name))
                changes.append({'path': name, 'patch': patch, 'sha256': hashlib.sha256(after).hexdigest()})
        # Only explicitly approved paths are exported; engineering commands can edit their private scratch freely.
        digest = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
        return {'base_commit': self.base, 'diff_sha256': digest, 'changes': changes}
