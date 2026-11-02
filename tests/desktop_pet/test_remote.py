"""Shared history is bounded by the authenticated device, never client-supplied IDs."""
import json
from types import SimpleNamespace
import pytest
from plugins.astrbot_plugin_desktop_pet.remote import RemoteMixin
from plugins.astrbot_plugin_desktop_pet.protocol import Binding

@pytest.mark.asyncio
async def test_history_uses_only_bound_origin_and_filters_tool_internals(monkeypatch):
    binding=Binding('device','persona','bot','user','adapter');seen=[]
    history=[{'role':'user','content':'hello'}, {'role':'tool','content':'private implementation'},
             {'role':'assistant','content':[{'type':'text','text':'reply'},{'type':'reasoning','text':'not public'}]},
             {'role':'user','content':'synthetic proactive prompt'}, {'role':'user','content':'latest'}]
    class Conversations:
        async def get_curr_conversation_id(self,origin):seen.append(origin);return 'conversation'
        async def get_conversation(self,origin,cid):seen.append(origin);return SimpleNamespace(history=json.dumps(history))
    class Remote(RemoteMixin):
        context=SimpleNamespace(conversation_manager=Conversations())
        async def authorized_binding(self,event):return binding
    import plugins.astrbot_plugin_desktop_pet.remote as module
    monkeypatch.setattr(module,'installed',lambda *args:SimpleNamespace(_proactive_archive_context_text=lambda text:text.startswith('synthetic')))
    remote=Remote()
    result=await remote.remote_request(binding,'query',{'kind':'history','limit':2,'user_id':'other','persona_id':'other'})
    assert seen==[binding.conversation_origin]*2
    assert [row['text'] for row in result['items']]==['reply','latest']
    older=await remote.read_history(binding,{'kind':'history','before':result['before']})
    assert [row['text'] for row in older['items']]==['hello']
    other=Binding('device','other','bot','user','adapter')
    assert (await remote.remote_request(other,'query',{'kind':'history'}))['error']=='paired_identity_unavailable'

def test_usage_and_recoverable_inputs_remain_in_the_bound_persona(tmp_path):
    from plugins.astrbot_plugin_desktop_pet.store import Store
    store=Store(tmp_path/'state.sqlite3')
    a=Binding('a','persona-a','bot-a','same-user','adapter-a')
    b=Binding('b','persona-b','bot-b','same-user','adapter-b')
    store.pair(a);store.pair(b)
    store.claim(a.device_id,'usage-a','usage',{'id':'usage-a','promptTokens':12})
    assert store.usages(a)==[{'id':'usage-a','promptTokens':12}]
    assert store.usages(b)==[]
    for mid in ('queued','read','observed'):
        store.claim(a.device_id,mid,'event',{'world':'desktop-pet','text':mid})
        store.update(a.device_id,mid,{'queued':'queued','read':'delivered','observed':'observed'}[mid])
    assert [row['id'] for row in store.queued_events(a)]==['queued']
    assert store.queued_events(b)==[]
    store.close()

@pytest.mark.asyncio
async def test_identity_uses_live_bound_profile_and_rejects_another_persona(monkeypatch):
    binding=Binding('device','persona-a','bot-a','user-a','adapter-a')
    profiles={'user-a':{'nickname':'Preferred address'},'user-b':{'nickname':'Other address'}}
    active=[]
    owner=SimpleNamespace(data={'users':profiles},_activate_persona_id=lambda *a,**k:active.append(a[0]) or 'scope',_deactivate_persona_for_event=lambda token:active.append(token))
    import plugins.astrbot_plugin_desktop_pet.remote as module
    monkeypatch.setattr(module,'installed',lambda *args:owner)
    class Remote(RemoteMixin):
        context=SimpleNamespace(get_config=lambda **k:{'timezone':'Asia/Shanghai'})
        async def authorized_binding(self,event):return binding
    remote=Remote()
    first=await remote.remote_request(binding,'query',{'kind':'identity','user_id':'user-b'})
    assert first['bot_name']=='persona-a' and first['user_name']=='Preferred address' and first['user_id']=='user-a'
    profiles['user-a']['nickname']='Updated address'
    assert (await remote.remote_request(binding,'query',{'kind':'identity'}))['user_name']=='Updated address'
    other=Binding('device','persona-b','bot-a','user-a','adapter-a')
    assert (await remote.remote_request(other,'query',{'kind':'identity'}))['error']=='paired_identity_unavailable'
    assert active==['persona-a','scope','persona-a','scope']

@pytest.mark.asyncio
@pytest.mark.parametrize('fields',[3,4])
@pytest.mark.parametrize('persona',['persona-a','persona-b'])
async def test_authorized_binding_accepts_framework_persona_results_without_crossing_scopes(fields,persona):
    import ast
    from pathlib import Path
    source=ast.parse((Path(__file__).parents[2]/'plugins/astrbot_plugin_desktop_pet/main.py').read_text())
    cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='DesktopPetPlugin')
    method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='authorized_binding')
    namespace={};exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual desktop authorization>','exec'),namespace)
    bindings=[Binding('a','persona-a','bot-a','same-user','adapter-a'),Binding('b','persona-b','bot-b','same-user','adapter-b')]
    binding=next(b for b in bindings if b.persona_id==persona)
    class Manager:
        async def get_curr_conversation_id(self,origin):return 'conversation'
        async def get_conversation(self,origin,cid):return SimpleNamespace(persona_id=persona)
    class Personas:
        selected=persona
        async def resolve_selected_persona(self,**kwargs):
            assert kwargs['umo']==binding.conversation_origin
            return (self.selected,object(),*([None]*(fields-2)))
    personas=Personas()
    owner=SimpleNamespace(hub=SimpleNamespace(stopping=False),store=SimpleNamespace(bindings=lambda:bindings),context=SimpleNamespace(conversation_manager=Manager(),persona_manager=personas,get_config=lambda **k:{}))
    assert await namespace['authorized_binding'](owner,SimpleNamespace(unified_msg_origin=binding.conversation_origin))==binding
    personas.selected='another-persona'
    assert await namespace['authorized_binding'](owner,SimpleNamespace(unified_msg_origin=binding.conversation_origin)) is None
