"""Run inside the production AstrBot image to cover its actual tool dispatcher."""
import ast
import importlib.util
import json
from pathlib import Path
from types import MethodType, SimpleNamespace
import uuid

import pytest
pytest.importorskip('astrbot.api')
from astrbot.api.event import AstrMessageEvent
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot_plugin_desktop_pet.integrations import record_outputs
from astrbot_plugin_desktop_pet.hub import DeliveryUnknown

@pytest.mark.asyncio
async def test_desktop_capability_arguments_pass_the_real_framework_dispatcher(monkeypatch):
    source=Path(importlib.util.find_spec('astrbot_plugin_desktop_pet').submodule_search_locations[0])/'main.py'
    cls=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='DesktopPetPlugin')
    method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='call');method.decorator_list=[]
    namespace={'AstrMessageEvent':AstrMessageEvent,'json':json,'uuid':uuid,'DeliveryUnknown':DeliveryUnknown,'record_outputs':record_outputs}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),namespace)
    calls=[]
    class Hub:
        async def call(self,binding,world,capability,args,**kwargs):calls.append((world,capability,args));return {'status':'completed','text':'displayed'}
    class Owner:
        context=SimpleNamespace(get_all_stars=lambda:[])
        hub=Hub()
        async def authorized_peer(self,event):return SimpleNamespace(binding='verified')
    owner=Owner();handler=MethodType(namespace['call'],owner)
    async def local(cls,tool,context,**kwargs):yield await handler(context.event,**kwargs)
    monkeypatch.setattr(FunctionToolExecutor,'_execute_local',classmethod(local))
    arguments={'world':'desktop-pet','capability':'pet_say','arguments':json.dumps({'script':'Hello'})}
    result=[r async for r in FunctionToolExecutor.execute(tool=SimpleNamespace(is_background_task=False),run_context=SimpleNamespace(event=object()),**arguments)]
    assert json.loads(result[0])['status']=='completed'
    assert calls==[('desktop-pet','pet_say',{'script':'Hello'})]
