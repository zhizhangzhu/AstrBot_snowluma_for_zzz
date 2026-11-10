"""The real chat planner is used by direct sends, independent of source/transport."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace, MethodType
from unittest.mock import AsyncMock
import pytest
from astrbot.api.message_components import Plain, Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion import outbound_rules as rules
from astrbot_plugin_private_companion.final_response_persistence import FinalResponsePersistenceCoordinator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'data/astrbot/plugins/astrbot_plugin_private_companion/tests'))
from test_segmented_chat_profiles import _harness


def host():
    owner = object.__new__(PrivateCompanionPlugin)
    owner.__dict__.update(_harness().__dict__)
    owner.persona_setting = MethodType(lambda self,k,d=None:self.persona_values.get(k,getattr(self,k,d)), owner)
    owner.persona_values.update({'enable_framework_error_leak_guard':False,
        'enable_proactive_quote_trigger_message':False, 'enable_llm_controlled_segmenting':False})
    owner.segmented_proactive_split_mode='regex'
    owner.segmented_proactive_regex=r'.*?[。；;]+|.+$'
    owner.enable_segmented_proactive_content_cleanup=True
    owner.segmented_proactive_content_cleanup_rule=r'[。；;，,]+'
    owner.segmented_proactive_content_cleanup_scope='trailing'
    owner._calc_segmented_proactive_interval=AsyncMock(return_value=0)
    return owner


def event(origin):
    return SimpleNamespace(unified_msg_origin=origin,message_str='当前消息',
        get_platform_name=lambda:'aiocqhttp',is_private_chat=lambda:True,
        get_group_id=lambda:'',get_result=lambda:None,
        get_self_id=lambda:origin.split(':')[0],get_sender_id=lambda:origin.rsplit(':',1)[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize('origin',['xiaoxiaoqing:FriendMessage:1005145948','chuxiaoqing:FriendMessage:1207862759'])
@pytest.mark.parametrize('source',['ordinary','poke','tool','desktop'])
@pytest.mark.parametrize('controlled',[False,True])
async def test_direct_event_plans_once_and_keeps_transport(origin,source,controlled):
    owner=host();incoming=event(origin);incoming.source=source
    from astrbot_plugin_private_companion.segmented_message import LLM_SEGMENT_MARKER
    owner.persona_values['enable_llm_controlled_segmenting']=controlled
    owner.persona_values['enable_segmented_plugin_rules']=True
    text=f'第一句。\n{LLM_SEGMENT_MARKER}\n第二句。' if controlled else '第一句。第二句。'
    outgoing=MessageChain([Plain(text),Image.fromURL('https://example.test/image.gif')])
    delivered=[]
    async def dispatch(message):delivered.append(message);return None
    result=await rules.send_event(owner,incoming,outgoing,dispatch)
    assert result['error'] is None and result['chains']
    assert [c.text for m in delivered for c in m.chain if isinstance(c,Plain)]==['第一句','第二句']
    assert sum(isinstance(c,Image) for m in delivered for c in m.chain)==1
    assert incoming.unified_msg_origin==origin
    for message in delivered:
        assert rules.plan(owner,incoming,message.chain)==[message.chain]


@pytest.mark.asyncio
async def test_false_or_exception_stops_without_replaying_confirmed_chunk():
    for failure in [False,RuntimeError('transport unknown')]:
        owner=host();incoming=event('xiaoxiaoqing:FriendMessage:1005145948');sent=[]
        async def dispatch(message):
            sent.append(message)
            if len(sent)==2:
                if isinstance(failure,Exception):raise failure
                return False
        result=await rules.send_event(owner,incoming,MessageChain([Plain('第一句话。第二句话。第三句话。')]),dispatch)
        assert len(sent)==2 and len(result['chains'])==1 and result['result'] is False
        assert isinstance(result['error'],RuntimeError) if isinstance(failure,Exception) else result['error'] is None


def test_background_uses_same_rules_and_filters_before_dispatch():
    owner=host();origin='xiaoxiaoqing:FriendMessage:1005145948'
    chunks=rules.plan(owner,None,[Plain('第一句话。第二句话。')],umo=origin)
    assert [c.text for chunk in chunks for c in chunk if isinstance(c,Plain)]==['第一句话','第二句话']
    owner._chain_text_for_forbidden_recall=lambda chain:''.join(c.text for c in chain if isinstance(c,Plain))
    owner._forbidden_recall_hit=lambda text:'blocked' if '禁止内容' in text else ''
    assert rules.plan(owner,None,[Plain('禁止内容。正常。')],umo=origin)==[]


@pytest.mark.asyncio
async def test_tracking_records_each_confirmed_filtered_chunk():
    owner=host();incoming=event('xiaoxiaoqing:FriendMessage:1005145948');sent=[]
    async def dispatch(message):sent.append(message);return True
    incoming.send=dispatch
    coordinator=FinalResponsePersistenceCoordinator(owner)
    ledger=coordinator.begin_passive(incoming);coordinator.install_send_tracking(incoming)
    await incoming.send(MessageChain([Plain('第一句。第二句。')]))
    assert [c.text for chain in ledger.confirmed_chains for c in chain if isinstance(c,Plain)]==['第一句','第二句']
    assert len(sent)==2
    coordinator._reset_context(ledger)


@pytest.mark.asyncio
async def test_desktop_speech_with_companion_passes_real_tool_executor(monkeypatch):
    import ast, importlib.util, importlib, json, uuid
    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
    from astrbot_plugin_desktop_pet.integrations import installed, record_outputs
    from astrbot_plugin_desktop_pet.hub import DeliveryUnknown, DeliveryNotSubmitted
    path=Path(importlib.util.find_spec('astrbot_plugin_desktop_pet').submodule_search_locations[0])/'main.py'
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='DesktopPetPlugin')
    method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='call');method.decorator_list=[]
    namespace=dict(AstrMessageEvent=AstrMessageEvent,json=json,uuid=uuid,installed=installed,
        record_outputs=record_outputs,DeliveryUnknown=DeliveryUnknown,DeliveryNotSubmitted=DeliveryNotSubmitted,
        sys=sys,importlib=importlib)
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
    companion=host();companion._activate_persona_id=lambda *a,**k:'token'
    companion._deactivate_persona_for_event=lambda token:None
    context=SimpleNamespace(get_all_stars=lambda:[SimpleNamespace(activated=True,
        root_dir_name='astrbot_plugin_private_companion',star_cls=companion)])
    calls=[]
    async def hub_call(binding,world,capability,args,**kwargs):
        calls.append(args['script']);return {'status':'submitted'}
    binding=SimpleNamespace(persona_id='小小情',conversation_origin='xiaoxiaoqing:FriendMessage:1005145948')
    desktop=SimpleNamespace(context=context,authorized_peer=AsyncMock(return_value=SimpleNamespace(binding=binding)),
        hub=SimpleNamespace(call=hub_call))
    handler=MethodType(namespace['call'],desktop)
    async def local(cls,tool,context,**kwargs):yield await handler(context.event,**kwargs)
    monkeypatch.setattr(FunctionToolExecutor,'_execute_local',classmethod(local))
    results=[r async for r in FunctionToolExecutor.execute(tool=SimpleNamespace(is_background_task=False),
        run_context=SimpleNamespace(event=object()),world='desktop-pet',capability='pet_say',
        arguments=json.dumps({'script':'第一句话。第二句话。'}))]
    assert calls==['第一句话','第二句话']
    receipt=json.loads(results[0]);assert receipt['status']=='submitted' and len(receipt['deliveries'])==2


@pytest.mark.asyncio
@pytest.mark.parametrize('platform',['aiocqhttp','desktop_pet'])
async def test_streaming_deltas_use_common_rules_instead_of_adapter_fallback(platform):
    owner=host();incoming=event('xiaoxiaoqing:FriendMessage:1005145948');sent=[]
    incoming.get_platform_name=lambda:platform
    async def dispatch(message):sent.append(message);return True
    incoming.send=dispatch;incoming.send_streaming=AsyncMock(side_effect=AssertionError('adapter fallback bypass'))
    coordinator=FinalResponsePersistenceCoordinator(owner)
    ledger=coordinator.begin_passive(incoming);coordinator.install_send_tracking(incoming)
    async def deltas():
        yield MessageChain([Plain('第一')]);yield MessageChain([Plain('句话。第二句话。')])
    await incoming.send_streaming(deltas(),use_fallback=True)
    assert [c.text for m in sent for c in m.chain if isinstance(c,Plain)]==['第一句话','第二句话']
    assert len(ledger.confirmed_chains)==2
    coordinator._reset_context(ledger)
