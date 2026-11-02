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
