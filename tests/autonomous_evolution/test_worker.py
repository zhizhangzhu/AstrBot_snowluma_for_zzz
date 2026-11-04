import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import uuid
import pytest
from astrbot_plugin_autonomous_development.worker import Worker
from astrbot_plugin_autonomous_development.integrations import owner_identity
from astrbot_plugin_autonomous_development.release import Release


def repo(tmp_path):
    root=tmp_path/'repo';root.mkdir();(root/'docs').mkdir();(root/'docs/sample.md').write_text('before\n')
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','--','docs/sample.md'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','base'],check=True)
    return root

@pytest.mark.asyncio
@pytest.mark.host_isolation
async def test_clean_checks_commit_separate_branch_and_rollback_keeps_new_data(tmp_path):
    root=repo(tmp_path);worker=Worker(root);tid='dev_'+uuid.uuid4().hex
    async def call(op,**kw):
        return await worker.execute({'identity':asdict(owner_identity()),'task_id':tid,'operation':op,**kw})
    prepared=await call('prepare',paths=['docs/sample.md'],checks=[['/usr/bin/python3','-c',"from pathlib import Path;assert Path('docs/sample.md').read_text()=='after\\n'"]])
    await call('write',files={'docs/sample.md':'after\n'})
    # A scratch file must not be included in the clean verification tree.
    scratch=worker.job(tid)/'workspace/sneaky.py';scratch.write_text('wrong')
    verified=await call('verify')
    assert verified['clean_verification'] and all(c['exit_code']==0 for c in verified['checks'])
    committed=await call('commit',title='isolated change',diff_sha256=verified['diff_sha256'])
    assert committed['branch'].startswith('codex/autonomous-')
    assert (root/'docs/sample.md').read_text()=='before\n'
    assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD']).decode().strip()==prepared['base_commit']
    deployed=await call('deploy',commit=committed['commit'])
    assert deployed['deployed'] and (root/'docs/sample.md').read_text()=='after\n'
    (root/'data').mkdir();(root/'data/new-chat.txt').write_text('new conversation')
    await call('rollback')
    assert (root/'docs/sample.md').read_text()=='before\n'
    assert (root/'data/new-chat.txt').read_text()=='new conversation'

@pytest.mark.asyncio
@pytest.mark.host_isolation
async def test_cannot_commit_after_failed_check_or_changed_artifact(tmp_path):
    root=repo(tmp_path);worker=Worker(root);tid='dev_'+uuid.uuid4().hex
    async def call(op,**kw):return await worker.execute({'identity':asdict(owner_identity()),'task_id':tid,'operation':op,**kw})
    await call('prepare',paths=['docs/sample.md'],checks=[['/usr/bin/python3','-c','raise SystemExit(6)']])
    await call('write',files={'docs/sample.md':'after\n'})
    result=await call('verify');assert result['checks'][0]['exit_code']==6
    with pytest.raises(PermissionError):await call('commit',title='bad',diff_sha256=result['diff_sha256'])

@pytest.mark.asyncio
@pytest.mark.host_isolation
async def test_failed_activation_restores_only_written_code(tmp_path):
    root=repo(tmp_path);(root/'plugins/test').mkdir(parents=True);(root/'plugins/test/helper.py').write_text('before=1\n')
    subprocess.run(['git','-C',str(root),'add','--','plugins/test/helper.py'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','plugin'],check=True)
    live=root/'data/astrbot/plugins/test';live.mkdir(parents=True);(live/'helper.py').write_text('before=1\n');(live/'chat.sqlite').write_text('must survive')
    worker=Worker(root);tid='dev_'+uuid.uuid4().hex
    async def call(op,**kw):return await worker.execute({'identity':asdict(owner_identity()),'task_id':tid,'operation':op,**kw})
    await call('prepare',paths=['plugins/test/helper.py'],checks=[['/usr/bin/python3','-c','import ast;ast.parse(open("plugins/test/helper.py").read())']])
    await call('write',files={'plugins/test/helper.py':'after=2\n'});v=await call('verify');c=await call('commit',diff_sha256=v['diff_sha256'])
    def fail():raise RuntimeError('injected activation failure')
    worker.releases.activate=fail
    with pytest.raises(RuntimeError):await call('deploy',commit=c['commit'])
    assert (live/'helper.py').read_text()=='before=1\n' and (live/'chat.sqlite').read_text()=='must survive'

@pytest.mark.asyncio
@pytest.mark.host_isolation
async def test_cancel_kills_engineering_command_and_cannot_produce_a_late_file(tmp_path):
    root=repo(tmp_path);worker=Worker(root);tid='dev_'+uuid.uuid4().hex
    async def call(op,**kw):return await worker.execute({'identity':asdict(owner_identity()),'task_id':tid,'operation':op,**kw})
    await call('prepare',paths=['docs/sample.md'],checks=[['/usr/bin/true']])
    run=asyncio.create_task(call('run',argv=['/usr/bin/python3','-c',"import time;time.sleep(20);open('late.txt','w').write('unexpected')"]))
    await asyncio.sleep(.1);await call('cancel')
    with pytest.raises(asyncio.CancelledError):await run
    assert not (worker.job(tid)/'workspace/late.txt').exists()

@pytest.mark.asyncio
async def test_restart_profile_is_fixed_and_idempotent_without_docker_access_from_task(tmp_path):
    root=repo(tmp_path);worker=Worker(root);tid='dev_'+uuid.uuid4().hex;calls=[]
    worker.releases.activate=lambda:calls.append('only-astrbot-profile')
    payload={'identity':asdict(owner_identity()),'task_id':tid,'operation':'restart','target':'arbitrary-container'}
    first=await worker.execute(payload);second=await worker.execute(payload)
    assert first['target']=='xiaoxiaoqing-astrbot' and second['restarted'] and len(calls)==1

@pytest.mark.asyncio
async def test_client_commit_pins_only_submodule_on_separate_parent_branch(tmp_path):
    root=repo(tmp_path);client=root/'Coopanion';client.mkdir()
    subprocess.run(['git','init','-q',str(client)],check=True)
    (client/'package.json').write_text('{"version":"0.0.1"}')
    def git(directory,*args):return subprocess.check_output(['git','-C',str(directory),*args]).decode().strip()
    git(client,'add','--','package.json');git(client,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','client')
    original=git(client,'rev-parse','HEAD');git(root,'update-index','--add','--cacheinfo','160000,'+original+',Coopanion')
    git(root,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','pin base')
    base=git(root,'rev-parse','HEAD')
    (client/'package.json').write_text('{"version":"0.0.2"}');git(client,'add','--','package.json');git(client,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','client next')
    target=git(client,'rev-parse','HEAD')
    worker=Worker(root);tid='dev_'+uuid.uuid4().hex;folder=worker.job(tid);folder.mkdir()
    result=worker.pin_parent(tid,folder,{'repository':str(client),'committed':True,'commit':target,'parent_base':base})
    assert git(root,'diff','--name-only',base,result['integration_commit'])=='Coopanion'
    assert target in git(root,'ls-tree',result['integration_commit'],'--','Coopanion')
    assert git(root,'rev-parse','HEAD')==base and original in git(root,'ls-tree','HEAD','--','Coopanion')

@pytest.mark.asyncio
async def test_cancel_during_submitted_restart_preserves_real_receipt(tmp_path):
    import threading
    root=repo(tmp_path);worker=Worker(root);tid='dev_'+uuid.uuid4().hex
    entered=threading.Event();release=threading.Event()
    def activate():entered.set();release.wait(5)
    worker.releases.activate=activate
    async def call(operation):return await worker.execute({'identity':asdict(owner_identity()),'task_id':tid,'operation':operation})
    pending=asyncio.create_task(call('restart'))
    await asyncio.to_thread(entered.wait,5)
    cancellation=await call('cancel')
    assert cancellation['completion_pending'] and not pending.done()
    release.set();result=await pending
    assert result['restarted'] and (await call('status'))['restarted']

@pytest.mark.asyncio
@pytest.mark.host_isolation
async def test_following_task_uses_published_source_while_human_branch_stays_untouched(tmp_path):
    root=repo(tmp_path);worker=Worker(root)
    human=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD']).decode().strip()
    def task():
        tid='dev_'+uuid.uuid4().hex
        async def call(operation,**args):return await worker.execute({'identity':asdict(owner_identity()),'task_id':tid,'operation':operation,**args})
        return call
    first=task();await first('prepare',paths=['docs/sample.md'],checks=[['/usr/bin/true']])
    await first('write',files={'docs/sample.md':'after\n'});verified=await first('verify');one=await first('commit',diff_sha256=verified['diff_sha256'])
    await first('deploy',commit=one['commit'])
    second=task();inventory=await second('inventory')
    assert inventory['base_commit']==one['commit']
    await second('prepare',paths=['docs/sample.md'],checks=[['/usr/bin/true']])
    assert (await second('read',path='docs/sample.md'))['text']=='after\n'
    await second('write',files={'docs/sample.md':'next\n'});verified=await second('verify');two=await second('commit',diff_sha256=verified['diff_sha256'])
    await second('deploy',commit=two['commit'])
    assert worker.releases.source_base(root)==two['commit'] and (root/'docs/sample.md').read_text()=='next\n'
    assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD']).decode().strip()==human
    stale=task()
    with pytest.raises(PermissionError):await stale('prepare',base_commit=one['commit'],paths=['docs/sample.md'],checks=[['/usr/bin/true']])
    with pytest.raises(PermissionError):await first('rollback')
    await second('rollback')
    assert worker.releases.source_base(root)==one['commit'] and (root/'docs/sample.md').read_text()=='after\n'
    # An independent human commit must not silently discard deployed source history.
    (root/'docs/human.md').write_text('manual')
    subprocess.run(['git','-C',str(root),'add','--','docs/human.md'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','human change'],check=True)
    with pytest.raises(PermissionError):worker.releases.source_base(root)


def test_source_copy_and_new_files_remain_engine_readable_under_private_umask(tmp_path):
    import os,stat
    from astrbot_plugin_autonomous_development.runner import Workspace
    root=repo(tmp_path);workspace=Workspace(root,tmp_path/'source')
    previous=os.umask(0o077)
    try:
        workspace.prepare()
        workspace.write({'docs/new/nested.md':'public source'},['docs/new/nested.md'])
    finally:os.umask(previous)
    for path in (workspace.directory/'.base-commit',workspace.directory/'docs/sample.md',workspace.directory/'docs/new/nested.md'):
        assert path.stat().st_uid==root.stat().st_uid and stat.S_IMODE(path.stat().st_mode)==0o600
    for path in (workspace.directory,workspace.directory/'docs',workspace.directory/'docs/new'):
        assert path.stat().st_uid==root.stat().st_uid and stat.S_IMODE(path.stat().st_mode)==0o700
