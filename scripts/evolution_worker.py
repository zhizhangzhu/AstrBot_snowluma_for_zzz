#!/usr/bin/env python3
"""Private engineering broker; never exposes a TCP management endpoint."""
import argparse
import asyncio
import fcntl
import os
from pathlib import Path
import signal
import sys
import json
import hashlib

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / '.runtime/evolution-control'
manifest = json.loads((CONTROL / 'manifest.json').read_text())
for name, expected in manifest['files'].items():
    file = CONTROL / 'plugins/astrbot_plugin_autonomous_development' / name
    if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
        raise SystemExit('固定工程控制器校验失败，拒绝启动')
sys.path.insert(0, str(CONTROL / 'plugins'))
from astrbot_plugin_autonomous_development.worker import Worker, ATOMIC_OPERATIONS


async def serve():
    folder = ROOT / '.runtime/evolution-transport'
    folder.mkdir(parents=True, exist_ok=True, mode=0o750)
    lock = (folder / 'worker.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = folder / 'worker.sock'
    path.unlink(missing_ok=True)
    (folder / 'worker.pid').write_text(str(os.getpid()))
    if not manifest.get('runner_image'):
        raise SystemExit('没有预检固定的工程镜像，拒绝以其他方式执行工程代码')
    worker = Worker(ROOT,runner_image=manifest['runner_image'],image_environment_keys=manifest.get('image_environment_keys',[]))
    await worker.container_runner.cleanup_stale()
    server = await asyncio.start_unix_server(worker.handle, str(path), limit=6_000_000)
    path.chmod(0o660)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        server.close()
        for job_id, task in list(worker.active.items()):
            if worker.active_operations.get(job_id) not in ATOMIC_OPERATIONS:task.cancel()
        await asyncio.gather(*worker.active.values(), return_exceptions=True)
        await server.wait_closed()
        path.unlink(missing_ok=True)
        (folder / 'worker.pid').unlink(missing_ok=True)
        lock.close()


if __name__ == '__main__':
    asyncio.run(serve())
