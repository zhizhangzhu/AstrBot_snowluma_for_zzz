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
