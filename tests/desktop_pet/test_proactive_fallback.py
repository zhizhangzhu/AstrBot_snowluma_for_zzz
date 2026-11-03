"""A new proactive route may use QQ only when no desktop submission occurred."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
pytest.importorskip('astrbot.api')
from astrbot.api.message_components import Plain
from astrbot_plugin_desktop_pet.hub import DeliveryUnknown, DeliveryNotSubmitted

@pytest.mark.asyncio
@pytest.mark.parametrize('error,expected',[(DeliveryNotSubmitted('offline'),None),(DeliveryUnknown('receipt_lost'),False),(TimeoutError(),False)])
async def test_only_definitely_unsubmitted_proactive_output_can_choose_qq(error,expected):
    package=Path(importlib.util.find_spec('astrbot_plugin_desktop_pet').submodule_search_locations[0])
    cls=next(n for n in ast.parse((package/'main.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='DesktopPetPlugin')
    method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='deliver_proactive')
    namespace={'DeliveryUnknown':DeliveryUnknown,'DeliveryNotSubmitted':DeliveryNotSubmitted}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(package/'main.py'),'exec'),namespace)
    async def unavailable(*args,**kwargs):raise error
    host=SimpleNamespace(proactive_preference=lambda *args:'desktop',
        hub=SimpleNamespace(for_origin=lambda origin:SimpleNamespace(binding='verified')),
        platform=SimpleNamespace(deliver=unavailable))
    assert await namespace['deliver_proactive'](host,object(),'same-session',[Plain('A new proactive message')]) is expected

@pytest.mark.asyncio
async def test_tool_rejected_before_submission_reports_unavailable_instead_of_unknown():
    import json
    import uuid
    from astrbot.api.event import AstrMessageEvent
    package=Path(importlib.util.find_spec('astrbot_plugin_desktop_pet').submodule_search_locations[0])
    cls=next(n for n in ast.parse((package/'main.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='DesktopPetPlugin')
    method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='call')
    method.decorator_list=[]
    namespace={'DeliveryUnknown':DeliveryUnknown,'DeliveryNotSubmitted':DeliveryNotSubmitted,
               'AstrMessageEvent':AstrMessageEvent,'json':json,'uuid':uuid}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(package/'main.py'),'exec'),namespace)
    async def authorized(event):return SimpleNamespace(binding='verified')
    async def unavailable(*args,**kwargs):raise DeliveryNotSubmitted('terminal_not_interactive')
    host=SimpleNamespace(authorized_peer=authorized,hub=SimpleNamespace(call=unavailable))
    result=json.loads(await namespace['call'](host,object(),'desktop-pet','pet_cua_key','{"keys":"shift"}'))
    assert result['status']=='unavailable' and result['submitted'] is False
    assert result['reason']=='terminal_not_interactive' and result['request_id']

@pytest.mark.asyncio
async def test_capability_pages_expose_live_lock_state_and_the_remaining_input_tools():
    import json
    from astrbot.api.event import AstrMessageEvent
    package=Path(importlib.util.find_spec('astrbot_plugin_desktop_pet').submodule_search_locations[0])
    cls=next(n for n in ast.parse((package/'main.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='DesktopPetPlugin')
    method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='capabilities')
    method.decorator_list=[]
    namespace={'AstrMessageEvent':AstrMessageEvent,'json':json}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(package/'main.py'),'exec'),namespace)
    tools=[{'name':f'tool_{i}'} for i in range(20)]+[{'name':'pet_cua_key'}]
    async def authorized(event):return SimpleNamespace(state={'locked':True},catalog={'worlds':[{'id':'desktop-pet','tools':tools}]})
    host=SimpleNamespace(authorized_peer=authorized)
    first=json.loads(await namespace['capabilities'](host,object(),'desktop-pet',0))
    assert first['state']['locked'] is True and first['total_tools']==21
    assert first['more'] is True and first['next_offset']==12
    last=json.loads(await namespace['capabilities'](host,object(),'desktop-pet',first['next_offset']))
    assert last['tools'][-1]['name']=='pet_cua_key' and last['more'] is False
    assert last['next_offset'] is None
