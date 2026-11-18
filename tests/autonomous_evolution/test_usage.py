import json
from types import SimpleNamespace
import httpx
import pytest
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.usage import UsageLedger, BudgetExceeded
from astrbot_plugin_autonomous_development.instrumentation import ANCHOR, install, instrument_provider, usage_scope

@pytest.fixture
def ledger(tmp_path):
    store=Store(tmp_path/'costs.sqlite3'); ledger=UsageLedger(store)
    install(ledger)
    yield ledger
    ANCHOR.ledger=None;ANCHOR.resolve_scope=None;store.close()

class Provider:
    provider_config={'id':'oauth','model':'gpt-6-luna'}
    def __init__(self,transport):self.transport=transport;self.entered=0
    def get_model(self):return 'gpt-6-luna'
    async def text_chat(self,prompt='',**kwargs):
        self.entered+=1
        async with httpx.AsyncClient(transport=self.transport) as c:
            r=await c.post('https://provider.invalid/responses',json={'model':'gpt-6-luna','input':prompt,'max_output_tokens':30})
            if r.status_code==401:
                r=await c.post('https://provider.invalid/responses',json={'model':'gpt-6-luna','input':prompt,'max_output_tokens':30})
        return SimpleNamespace(completion_text=r.text,usage=None)

@pytest.mark.asyncio
async def test_retry_and_reload_are_physical_once_and_private_content_not_stored(ledger):
    calls=[]
    def transport(req):
        calls.append(1)
        return httpx.Response(401,json={'error':'expired'}) if len(calls)==1 else httpx.Response(200,json={'usage':{'input_tokens':100,'output_tokens':5}})
    p=Provider(httpx.MockTransport(transport));instrument_provider(p)
    fn=p.text_chat;instrument_provider(p);install(ledger)
    assert p.text_chat is fn
    with usage_scope(plugin='meme_manager',feature='character',bot='2510093206',persona='初小晴'):
        await p.text_chat(prompt='PRIVATE_SENSITIVE_CONTENT')
    rows=ledger.store.rows('SELECT * FROM requests')
    assert len(rows)==2 and len(calls)==2
    assert [r['state'] for r in rows]==['failed','completed']
    assert rows[0]['quality']=='unknown' and rows[1]['input']==100
    assert all(r['bot']=='2510093206' and r['plugin']=='meme_manager' for r in rows)
    assert 'PRIVATE_SENSITIVE_CONTENT' not in json.dumps(rows)
    assert ANCHOR.scope.get() is None

@pytest.mark.asyncio
async def test_consumed_sse_and_unknown_usage_settle_without_duplicate(ledger):
    sse='data: '+json.dumps({'type':'response.completed','response':{'usage':{'input_tokens':10,'output_tokens':3}}})+'\n\n'
    p=Provider(httpx.MockTransport(lambda req:httpx.Response(200,text=sse,headers={'content-type':'text/event-stream'})))
    instrument_provider(p)
    with usage_scope(plugin='test'):
        await p.text_chat(prompt='sse')
    rows=ledger.store.rows('SELECT * FROM requests')
    assert len(rows)==1 and rows[0]['input']==10 and rows[0]['state']=='completed'

@pytest.mark.asyncio
async def test_budget_is_checked_before_non_http_provider_starts(ledger):
    ledger.store.set_setting('budget',{'monthly_usd':'0','enforce':True,'billing_day':1,'chat_reserve_usd':'0'})
    p=Provider(httpx.MockTransport(lambda req:httpx.Response(200)))
    instrument_provider(p)
    with pytest.raises(BudgetExceeded):
        await p.text_chat(prompt='no charge')
    assert p.entered==0 and ledger.store.rows('SELECT * FROM requests')==[]

@pytest.mark.asyncio
async def test_stream_failure_is_not_known_zero(ledger):
    class StreamProvider:
        provider_config={'id':'other','model':'gpt-6-luna'}
        def get_model(self):return 'gpt-6-luna'
        async def text_chat_stream(self,**kwargs):
            yield SimpleNamespace(usage=None)
            raise RuntimeError('disconnected')
    p=StreamProvider();instrument_provider(p)
    with pytest.raises(RuntimeError):
        async for _ in p.text_chat_stream():pass
    rows=ledger.store.rows('SELECT * FROM requests')
    assert len(rows)==1 and rows[0]['state']=='failed' and rows[0]['cost'] is None

@pytest.mark.asyncio
async def test_price_change_during_request_does_not_reprice_in_flight_usage(ledger):
    rid=ledger.reserve({'plugin':'test'},'gpt-6.1-sol',100,20)
    ledger.store.set_setting('prices',{'gpt-6.1-sol':{'input':'999','cached':'999','output':'999'}})
    ledger.finish(rid,{'input':100,'output':20})
    assert ledger.store.rows('SELECT cost FROM requests')[0]['cost']==400

@pytest.mark.asyncio
async def test_actual_module_reload_updates_dispatch_without_double_wrapping(ledger):
    import importlib
    import astrbot_plugin_autonomous_development.instrumentation as module
    p=Provider(httpx.MockTransport(lambda req:httpx.Response(200,json={'usage':{'input_tokens':12,'output_tokens':2}})))
    instrument_provider(p);stable=httpx.AsyncClient.send
    module=importlib.reload(module);module.install(ledger);module.instrument_provider(p)
    await p.text_chat(prompt='after reload')
    assert httpx.AsyncClient.send is stable
    assert len(ledger.store.rows('SELECT * FROM requests'))==1

@pytest.mark.asyncio
async def test_codex_sse_without_event_stream_header_is_accounted(ledger):
    sse='data: '+json.dumps({'type':'response.completed','response':{'usage':{'input_tokens':41,'output_tokens':5}}})+'\n\n'
    class Codex(Provider):
        async def text_chat(self,**kw):
            async with httpx.AsyncClient(transport=self.transport) as client:
                await client.post('https://provider.invalid/responses',json={'model':'gpt-6-luna','stream':True})
            return SimpleNamespace(usage=None)
    provider=Codex(httpx.MockTransport(lambda req:httpx.Response(200,text=sse,headers={'content-type':'text/plain'})))
    instrument_provider(provider);await provider.text_chat()
    rows=ledger.store.rows('SELECT * FROM requests')
    assert len(rows)==1 and rows[0]['input']==41 and rows[0]['output']==5 and rows[0]['cost'] is not None

@pytest.mark.asyncio
async def test_compressed_stream_preserves_provider_bytes_and_recovers_usage(ledger):
    import gzip
    plain=b'data: '+json.dumps({'type':'response.completed','response':{'usage':{'input_tokens':12,'output_tokens':3}}}).encode()+b'\n\n'
    encoded=gzip.compress(plain)
    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            for start in range(0,len(encoded),7):yield encoded[start:start+7]
    class Stream(Provider):
        async def text_chat(self,**kw):
            async with httpx.AsyncClient(transport=self.transport) as client:
                async with client.stream('POST','https://provider.invalid/responses',json={'model':'gpt-6-luna','stream':True}) as response:
                    assert await response.aread()==plain
            return SimpleNamespace(usage=None)
    provider=Stream(httpx.MockTransport(lambda req:httpx.Response(200,stream=Bytes(),headers={'content-type':'text/event-stream','content-encoding':'gzip'})))
    instrument_provider(provider);await provider.text_chat()
    rows=ledger.store.rows('SELECT * FROM requests')
    assert len(rows)==1 and rows[0]['input']==12 and rows[0]['output']==3
