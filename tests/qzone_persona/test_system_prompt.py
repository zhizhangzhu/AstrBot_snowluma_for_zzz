import asyncio
from contextvars import ContextVar
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'vendor/installed-plugins/astrbot_plugin_private_companion/qzone_persona.py'
if not SOURCE.exists():SOURCE=ROOT/'plugins/astrbot_plugin_private_companion/qzone_persona.py'
spec=importlib.util.spec_from_file_location('qzone_persona_contract',SOURCE)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def host_fixture():
    active=ContextVar('test_qzone_persona');requests=[]
    class Manager:
        async def get_persona(self, name):
            await asyncio.sleep(0)
            return {'prompt': {'First':'First character voice','Second':'Second character voice'}.get(name,'')}
    async def query(key, factory, timeout):return await factory()
    async def llm(prompt,**kwargs):
        requests.append((active.get(),prompt,kwargs));return 'draft'
    host=SimpleNamespace(_effective_plugin_persona_id=lambda:active.get(),
      context=SimpleNamespace(persona_manager=Manager()),_await_framework_db_query=query,
      _extract_default_persona_prompt=lambda raw:raw['prompt'],_llm_call=llm)
    return host,active,requests


def test_concurrent_personas_keep_their_own_full_prompt_and_public_boundary():
    host,active,requests=host_fixture()
    async def run(name):
        token=active.set(name)
        try:return await module.call(host,'Public writing task',task='qzone_publish',max_tokens=180)
        finally:active.reset(token)
    async def main():return await asyncio.gather(run('First'),run('Second'))
    assert asyncio.run(main())==['draft','draft']
    for name,prompt,kwargs in requests:
        other='Second' if name=='First' else 'First'
        assert name+' character voice' in kwargs['system_prompt']
        assert other+' character voice' not in kwargs['system_prompt']
        assert module.PUBLIC_BOUNDARY in kwargs['system_prompt']
        assert kwargs['task']=='qzone_publish' and kwargs['max_tokens']==180


@pytest.mark.parametrize('name',['','Missing'])
def test_missing_selected_persona_does_not_generate_with_another_default(name):
    host,active,requests=host_fixture();active.set(name)
    with pytest.raises(RuntimeError):asyncio.run(module.call(host,'draft'))
    assert not requests


@pytest.mark.parametrize('task',['qzone_publish_length','qzone_publish_deduplicate','qzone_publish_sanitize','qzone_emotional_vent'])
def test_rewrites_retain_persona_and_any_extra_task_system_instruction(task):
    host,active,requests=host_fixture();active.set('Second')
    asyncio.run(module.call(host,'Rewrite draft',task=task,system_prompt='Keep the required output contract'))
    system=requests[0][2]['system_prompt']
    assert 'Second character voice' in system and 'Keep the required output contract' in system
