"""Opt-in checks against the actual pinned Docker image; no production services or data."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import uuid
import pytest
from astrbot_plugin_autonomous_development.container_runner import ContainerRunner

pytestmark=pytest.mark.host_isolation

@pytest.fixture
def runner():
    spec_path=os.environ.get('EVOLUTION_RUNNER_TEST_SPEC')
    if not spec_path:pytest.skip('Explicit source-only Docker runner spec required')
    root=Path(__file__).resolve().parents[2];spec=json.loads(Path(spec_path).read_text())
    class Observed(ContainerRunner):
        name=None;configuration=None
        async def cli(self,*args,**kwargs):
            result=await super().cli(*args,**kwargs)
            if args[0]=='run' and result[0]==0:
                self.name=args[args.index('--name')+1]
                _,raw,_=await super().cli('inspect',self.name)
                self.configuration=json.loads(raw)[0]
            return result
    yield Observed(root,spec['image'],environment_keys=spec['keys'])

@pytest.fixture
def source(runner):
    path=runner.root/'.runtime/evolution-worker'/('test_'+uuid.uuid4().hex)
    path.mkdir(parents=True);(path/'code.py').write_text('before=1\n')
    yield path
    shutil.rmtree(path)

@pytest.mark.asyncio
async def test_actual_container_has_enforced_limits_no_private_mounts_and_scoped_export(runner,source):
    result=await runner.run(source,['/usr/bin/python3','-I','-c',
        "import pathlib,os;assert not pathlib.Path('/var/run/docker.sock').exists();assert not pathlib.Path('/home/yingjxu/workspace').exists();assert not os.environ.get('OPENAI_API_KEY');assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip()=='64';pathlib.Path('code.py').write_text('after=2\\n')"],30,allowed=['code.py'])
    assert result['exit_code']==0 and result['engine']=='docker'
    assert (source/'code.py').read_text()=='after=2\n'
    host=runner.configuration['HostConfig']
    assert host['ReadonlyRootfs'] and not host['Privileged'] and host['NetworkMode']=='none'
    assert host['Memory']==2*1024**3 and host['PidsLimit']==64 and host['NanoCpus']==2_000_000_000
    assert all(not m.get('RW') for m in runner.configuration['Mounts'] if m['Type']=='bind')
    assert 'docker.sock' not in json.dumps(runner.configuration['Mounts'])

@pytest.mark.asyncio
async def test_verification_cannot_silently_change_the_tested_artifact(runner,source):
    with pytest.raises(PermissionError,match='受检源码'):
        await runner.run(source,['/usr/bin/python3','-c',"open('code.py','w').write('fake=3')"],30,allowed=['code.py'],export=False)
    assert (source/'code.py').read_text()=='before=1\n'

@pytest.mark.asyncio
async def test_cancelled_command_removes_the_disposable_container(runner,source):
    task=asyncio.create_task(runner.run(source,['/usr/bin/python3','-c','import time;time.sleep(20)'],30,allowed=['code.py']))
    for _ in range(100):
        if runner.name:break
        await asyncio.sleep(.05)
    assert runner.name
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    code,_,_=await runner.cli('inspect',runner.name)
    assert code!=0

@pytest.mark.asyncio
async def test_real_docker_worker_checks_then_commits_without_touching_human_tree(runner):
    from dataclasses import asdict
    import subprocess
    from astrbot_plugin_autonomous_development.worker import Worker
    from astrbot_plugin_autonomous_development.integrations import owner_identity
    root=runner.root/'.runtime'/('evolution-repo-test-'+uuid.uuid4().hex)
    root.mkdir();(root/'docs').mkdir();(root/'docs/example.md').write_text('before\n')
    git=lambda *args:subprocess.check_output(['git','-C',str(root),*args])
    git('init','-q');git('add','--','docs/example.md');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','base')
    worker=Worker(root,runner_image=runner.image,image_environment_keys=runner.environment_keys)
    task='dev_'+uuid.uuid4().hex
    async def call(operation,**args):
        return await worker.execute({'identity':asdict(owner_identity()),'task_id':task,'operation':operation,**args})
    try:
        await call('prepare',paths=['docs/example.md'],checks=[['python3','-c',"from pathlib import Path;assert Path('docs/example.md').read_text()=='after\\n'"]])
        await call('write',files={'docs/example.md':'after\n'})
        verified=await call('verify')
        assert verified['clean_verification'] and all(c['exit_code']==0 and c['isolated'] for c in verified['checks'])
        committed=await call('commit',diff_sha256=verified['diff_sha256'])
        assert git('show',committed['commit']+':docs/example.md')==b'after\n'
        assert (root/'docs/example.md').read_text()=='before\n'
        with pytest.raises(PermissionError):await call('run',argv=['python3','-c','pass'])
    finally:shutil.rmtree(root)
