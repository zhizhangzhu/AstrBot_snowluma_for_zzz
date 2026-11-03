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
