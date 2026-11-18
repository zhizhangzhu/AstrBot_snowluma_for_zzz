"""Load the entire entry module and use the actual dispatcher, not an extracted AST method."""
import json
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
pytest.importorskip('astrbot.api')
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor, FunctionTool
from astrbot_plugin_autonomous_development.main import AutonomousDevelopment
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.tasks import Tasks
from astrbot_plugin_autonomous_development.integrations import event_identity

class Event:
    def __init__(self,platform='xiaoxiaoqing',bot='2893214983',user='1005145948'):
        self.unified_msg_origin=platform+':FriendMessage:'+user;self.bot=bot;self.user=user
    def get_self_id(self):return self.bot
    def get_sender_id(self):return self.user
    def get_group_id(self):return ''
    def get_result(self):return None

@pytest.mark.asyncio
@pytest.mark.parametrize('arity',[3,4])
async def test_real_function_executor_proposes_goal_and_uses_real_identity_validation(tmp_path,arity):
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'state.sqlite3');owner.tasks=Tasks(owner.store)
    companion=S(_primary_persona_id=lambda:'小小情');memory=S(service=object())
    owner.context=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=companion),S(name='astrbot_plugin_memory_companion',activated=True,star_cls=memory)],
        get_config=lambda **kw:{'provider_settings':{}},
        conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value=None)),
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=tuple(['小小情',object(),None,None][:arity]))))
    tool=FunctionTool(name='autonomous_growth',description='test',parameters={},handler=owner.growth_tool)
    event=Event();ctx=S(context=S(event=event),tool_call_timeout=30)
    results=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation='propose',arguments={'goal':'改进公开阅读功能'})]
    payload=json.loads(results[0].content[0].text)
    assert payload['ok'] and payload['data']['state']=='proposed'
    # The same real tool path must reject both the secondary identity and approval attempts.
    ctx.context.event=Event('chuxiaoqing','2510093206','1207862759')
    denied=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation='propose',arguments={'goal':'不应创建的任务'})]
    assert not json.loads(denied[0].content[0].text)['ok']
    ctx.context.event=event
    denied=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation='approval',arguments={'decision':'approved'})]
    assert not json.loads(denied[0].content[0].text)['ok']
    assert len(owner.store.rows('SELECT * FROM tasks'))==1
    owner.store.close()

@pytest.mark.asyncio
async def test_real_executor_omitted_arguments_and_invalid_mutation_still_authenticate(tmp_path):
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'state.sqlite3');owner.tasks=Tasks(owner.store)
    companion=S(_primary_persona_id=lambda:'小小情');memory=S(service=object())
    owner.context=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=companion),S(name='astrbot_plugin_memory_companion',activated=True,star_cls=memory)],
        get_config=lambda **kw:{'provider_settings':{}},
        conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value=None)),
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情',object(),None))))
    owner.configuration=S(enabled=lambda:{'enabled':True})
    owner.knowledge=S(status=AsyncMock(return_value={'kind':'wiki'}))
    tool=FunctionTool(name='autonomous_growth',description='test',parameters={},handler=owner.growth_tool)
    ctx=S(context=S(event=Event()),tool_call_timeout=30)
    async def execute(operation,**kwargs):
        results=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation=operation,**kwargs)]
        return json.loads(results[0].content[0].text)
    for operation in ('status','tasks'):
        assert (await execute(operation))['ok']
        assert (await execute(operation,arguments={}))['ok']
    assert not (await execute('propose'))['ok']
    assert not (await execute('propose',arguments=[]))['ok']
    ctx.context.event=Event('chuxiaoqing','2510093206','1207862759')
    assert not (await execute('status'))['ok']
    assert owner.tasks.list(__import__('astrbot_plugin_autonomous_development.integrations',fromlist=['owner_identity']).owner_identity())==[]
    owner.store.close()

@pytest.mark.asyncio
async def test_approval_notice_uses_persona_candidate_once_and_cancels_stale_approval(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import asyncio
    from astrbot_plugin_autonomous_development.integrations import owner_identity
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'state.sqlite3');owner.tasks=Tasks(owner.store);owner.context=object()
    task=owner.tasks.create(owner_identity(),'修复表情包认知的纠正召回')
    owner.tasks.plan(owner_identity(),task['id'],{'paths':['plugins/example/recall.py'],'summary':'先验证纠正信息的读取路径'})
    approval=owner.tasks.request_approval(owner_identity(),task['id'],'modify')
    offers=[];saves=[]
    cp=S(data={'users':{'1005145948':{'umo':'xiaoxiaoqing:FriendMessage:1005145948'}},'proactive_candidate_pool':[]},_data_lock=asyncio.Lock())
    def offer(uid,user,candidate):
        candidate={**candidate,'status':'pending'};offers.append(candidate);cp.data['proactive_candidate_pool'].append(candidate);return True
    cp._offer_proactive_candidate=offer;cp._schedule_data_save=lambda **kw:saves.append(kw)
    @contextmanager
    def context(_):yield cp
    monkeypatch.setattr('astrbot_plugin_autonomous_development.main.companion_context',context)
    await owner.offer_approval_notices();await owner.offer_approval_notices()
    assert len(offers)==1 and offers[0]['context']['id']==approval['id']
    assert '人格' in offers[0]['motive'] and '/开发批准 '+approval['id'] in offers[0]['motive']
    assert owner.tasks.get(owner_identity(),task['id'])['state']=='awaiting_approval'
    owner.tasks.decide(owner_identity(),approval['id'],'denied')
    await owner.offer_approval_notices()
    assert offers[0]['status']=='cancelled' and owner.pending_development_approvals()==[]
    assert len(offers)==1
    owner.store.close()

@pytest.mark.asyncio
async def test_real_astrbot_knowledge_create_index_and_retrieve_are_confirmed(tmp_path,monkeypatch):
    """Real DB/vector pipeline; deterministic fixture vectors are not a production model."""
    import numpy as np
    import astrbot.core.knowledge_base.kb_mgr as module
    from astrbot.core.provider.provider import EmbeddingProvider
    from astrbot_plugin_autonomous_development.knowledge import Knowledge
    from astrbot_plugin_autonomous_development.integrations import owner_identity
    class FixtureEmbedding(EmbeddingProvider):
        def __init__(self):
            super().__init__({'id':'fixture-embedding','model':'fixture-vector'},{});self.model_name='fixture-vector'
        def meta(self):return S(id='fixture-embedding')
        def get_dim(self):return 8
        async def get_embedding(self,text):
            v=np.zeros(8,dtype=float)
            for c in text:v[ord(c)%8]+=1
            length=float(np.linalg.norm(v)) or 1
            return (v/length).tolist()
        async def get_embeddings(self,texts):return [await self.get_embedding(t) for t in texts]
    embedding=FixtureEmbedding()
    monkeypatch.setattr(module,'DB_PATH',tmp_path/'native-kb.sqlite')
    monkeypatch.setattr(module,'FILES_PATH',str(tmp_path/'native-files'))
    manager=module.KnowledgeBaseManager(S(get_provider_by_id=AsyncMock(return_value=embedding)))
    await manager.initialize()
    store=Store(tmp_path/'governance.sqlite3')
    knowledge=Knowledge(S(kb_manager=manager,get_all_embedding_providers=lambda:[embedding]),store)
    created=await knowledge.create(owner_identity(),'隔离测试资料','fixture-embedding')
    config=knowledge.config();config['enabled']=True;store.set_setting('knowledge',config)
    text='这是隔离测试中的公开资料正文，用来验证AstrBot原生知识库的文档、向量索引和来源回查。'*10
    source={'url':'https://example.org/isolated-fixture','text':text,'public':True,'evidence_kind':'article_text_acquired'}
    knowledge.enqueue(source)
    result=await knowledge.save(owner_identity(),created['kb_id'],text,source['url'],'索引与检索验证')
    assert result['indexed'] and result['document']['retrieval_confirmed']
    repeated=await knowledge.save(owner_identity(),created['kb_id'],text,source['url'],'索引与检索验证')
    assert repeated['duplicate'] and len(store.rows('SELECT * FROM documents'))==1
    found=await knowledge.search(owner_identity(),'索引与检索验证')
    assert any(r['doc_id']==result['document']['astrbot_doc_id'] for r in found['results'])
    from astrbot_plugin_autonomous_development.instrumentation import usage_scope
    with usage_scope(bot='2510093206',persona='初小晴'):
        assert await manager.retrieve(query='索引与检索验证',kb_names=['隔离测试资料']) is None
    assert await manager.retrieve(query='索引与检索验证',kb_names=['隔离测试资料']) is None
    knowledge.unbind_manager()
    store.close()

@pytest.mark.asyncio
async def test_management_requires_actual_dashboard_login_and_rejects_stale_setting(tmp_path,monkeypatch):
    import astrbot_plugin_autonomous_development.api as api
    from astrbot_plugin_autonomous_development.usage import UsageLedger
    store=Store(tmp_path/'admin.sqlite3')
    plugin=S(stopping=False,store=store,tasks=Tasks(store),ledger=UsageLedger(store))
    admin=api.Administration(plugin)
    unauth=S(username='',method='POST',body=AsyncMock(side_effect=AssertionError('must not read anonymous body')))
    monkeypatch.setattr(api,'request',unauth)
    response=await admin.handle()
    assert response.status_code==401 and unauth.body.await_count==0
    plugin.configuration=S(enabled=lambda:{'enabled':True,'cooldown_seconds':3600,'daily_changes':8})
    plugin.knowledge=S(config=lambda:{'enabled':True,'readable_kbs':[],'writable_kbs':[],'shared_kbs':[],'topics':[],'allowed_domains':[],'embedding_provider_id':'','daily_documents':5})
    plugin.background=lambda coroutine:coroutine.close()
    value=plugin.tasks.mode()
    first=await admin.dispatch('setting',{'key':'development','value':value,'revision':0},'dashboard:admin')
    assert first['revision']==1
    with pytest.raises(ValueError):await admin.dispatch('setting',{'key':'development','value':value,'revision':0},'dashboard:admin')
    store.close()

@pytest.mark.asyncio
async def test_live_knowledge_readiness_is_in_owner_prompt_and_cancelled_goals_are_excluded(tmp_path):
    from astrbot.api.provider import ProviderRequest
    from astrbot_plugin_autonomous_development.knowledge import Knowledge
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'awareness.sqlite3');owner.tasks=Tasks(owner.store)
    companion=S(_primary_persona_id=lambda:'小小情');memory=S(service=object())
    owner.context=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=companion),S(name='astrbot_plugin_memory_companion',activated=True,star_cls=memory)],
        get_all_providers=lambda:[],get_all_embedding_providers=lambda:[],kb_manager=S(),
        get_config=lambda **kw:{'provider_settings':{}},conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value=None)),
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情',object(),None))))
    owner.configuration=S(enabled=lambda:{'enabled':True})
    owner.knowledge=Knowledge(owner.context,owner.store)
    from astrbot_plugin_autonomous_development.awareness import CapabilityAwareness
    owner.awareness=CapabilityAwareness(owner.context,owner.store,owner.knowledge)
    from astrbot_plugin_autonomous_development.integrations import owner_identity
    task=owner.tasks.create(owner_identity(),'已取消的检查目标');owner.tasks.update(owner_identity(),task['id'],state='cancelled')
    req=ProviderRequest(prompt='资料是否保存了',system_prompt='原有人格')
    await owner.on_llm_request(Event(),req)
    state=json.loads(req.system_prompt.split('[自主成长实际能力]\n',1)[1].splitlines()[0])
    assert state['knowledge']['ready'] and state['knowledge']['type']=='local_wiki' and state['knowledge']['ai_access']=='read_only'
    assert state['pending_tasks']==[] and '资料不可自主改写' in req.system_prompt
    assert '[统一功能目录' in req.system_prompt and 'registered_reference_only' not in req.system_prompt
    other=ProviderRequest(prompt='资料',system_prompt='初小晴人格')
    await owner.on_llm_request(Event('chuxiaoqing','2510093206','1207862759'),other)
    assert other.system_prompt=='初小晴人格'
    owner.store.close()

@pytest.mark.asyncio
async def test_capability_catalog_uses_real_dispatcher_and_keeps_secondary_bot_denied(tmp_path):
    from astrbot_plugin_autonomous_development.awareness import CapabilityAwareness
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'capability-dispatch.sqlite3')
    companion=S(_primary_persona_id=lambda:'小小情');memory=S(service=object())
    owner.context=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=companion),S(name='astrbot_plugin_memory_companion',activated=True,star_cls=memory)],
        get_config=lambda **kw:{'provider_settings':{}},
        conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value=None)),
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情',object(),None))))
    owner.awareness=CapabilityAwareness(owner.context,owner.store)
    tool=FunctionTool(name='autonomous_growth',description='test',parameters={},handler=owner.growth_tool)
    ctx=S(context=S(event=Event()),tool_call_timeout=30)
    results=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation='capabilities',arguments={'section':'plugins'})]
    data=json.loads(results[0].content[0].text)
    assert data['ok'] and data['data']['total']==2 and data['data']['coverage']=='partial'
    ctx.context.event=Event('chuxiaoqing','2510093206','1207862759')
    denied=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation='capabilities',arguments={'section':'plugins'})]
    assert not json.loads(denied[0].content[0].text)['ok']
    owner.store.close()

@pytest.mark.asyncio
async def test_wiki_read_and_proposal_use_real_dispatcher_without_model_approval_or_direct_write(tmp_path):
    from astrbot_plugin_autonomous_development.knowledge import Knowledge
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'wiki-dispatch.sqlite3')
    companion=S(_primary_persona_id=lambda:'小小情');memory=S(service=object())
    owner.context=S(get_all_stars=lambda:[S(name='astrbot_plugin_private_companion',activated=True,star_cls=companion),S(name='astrbot_plugin_memory_companion',activated=True,star_cls=memory)],
        get_config=lambda **kw:{'provider_settings':{}},conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value=None)),
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情',object(),None))))
    owner.knowledge=Knowledge(owner.context,owner.store);wiki=owner.knowledge.wiki
    topic=wiki.topic_create('dashboard:admin','固定资料')['id'];doc=wiki.save('dashboard:admin',topic,'确认设定','这是人工维护的固定内容')['id']
    tool=FunctionTool(name='autonomous_growth',description='test',parameters={},handler=owner.growth_tool)
    ctx=S(context=S(event=Event()),tool_call_timeout=30)
    async def execute(op,args):
        values=[v async for v in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation=op,arguments=args)]
        return json.loads(values[0].content[0].text)
    assert (await execute('wiki_read',{'doc_id':doc}))['data']['body']=='这是人工维护的固定内容'
    proposal=await execute('wiki_propose',{'doc_id':doc,'title':'确认设定','body':'建议修改，待人工批准','expected_revision':1,'reason':'核对资料'})
    assert proposal['ok'] and not proposal['data']['applied']
    assert not (await execute('wiki_decide',{'id':proposal['data']['id'],'decision':'approved'}))['ok']
    assert not (await execute('knowledge_save',{'kb_id':topic,'text':'不允许直接写入'}))['ok']
    assert wiki.document(doc)['revision']==1
    ctx.context.event=Event('chuxiaoqing','2510093206','1207862759')
    assert not (await execute('wiki_read',{'doc_id':doc}))['ok']
    owner.store.close()

@pytest.mark.asyncio
async def test_readonly_wiki_tool_honors_both_bot_acls_without_granting_secondary_development(tmp_path):
    from astrbot_plugin_autonomous_development.knowledge import Knowledge
    from astrbot.api.provider import ProviderRequest
    owner=AutonomousDevelopment.__new__(AutonomousDevelopment)
    owner.store=Store(tmp_path/'dual-wiki.sqlite3')
    async def resolve(**kwargs):
        return ('初小晴' if kwargs['umo'].startswith('chuxiaoqing:') else '小小情',object(),None)
    owner.context=S(get_all_stars=lambda:[],get_config=lambda **kw:{'provider_settings':{}},
        conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value=None)),
        persona_manager=S(resolve_selected_persona=resolve))
    owner.knowledge=Knowledge(owner.context,owner.store);wiki=owner.knowledge.wiki
    library=wiki.library_create('dashboard:admin','授权测试',['2893214983','2510093206'])['id']
    main=wiki.topic_create('dashboard:admin','小小情资料',library=library,readers=['2893214983'])['id']
    secondary=wiki.topic_create('dashboard:admin','初小晴资料',library=library,readers=['2510093206'])['id']
    main_doc=wiki.save('dashboard:admin',main,'主Bot秘密','只有小小情可以读到的正文')['id']
    secondary_doc=wiki.save('dashboard:admin',secondary,'初小晴设定','已授权初小晴读取的正文')['id']
    tool=FunctionTool(name='local_wiki',description='read only',parameters={},handler=owner.wiki_tool)
    event=Event('chuxiaoqing','2510093206','1207862759');ctx=S(context=S(event=event),tool_call_timeout=30)
    async def execute(op,args):
        rows=[r async for r in FunctionToolExecutor.execute(tool=tool,run_context=ctx,operation=op,arguments=args)]
        return json.loads(rows[0].content[0].text)
    assert (await execute('read',{'doc_id':secondary_doc}))['data']['body']=='已授权初小晴读取的正文'
    denied=await execute('read',{'doc_id':main_doc,'bot':'2893214983'})
    assert not denied['ok'] and '只有小小情' not in str(denied)
    assert not (await execute('save',{'doc_id':secondary_doc,'body':'不能直接保存'}))['ok']
    req=ProviderRequest(prompt='查资料',system_prompt='初小晴人格');await owner.wiki_context(event,req)
    assert '本地资料Wiki' in req.system_prompt and '自主成长实际能力' not in req.system_prompt
    wiki.access_update('dashboard:admin','library',library,'授权测试',['2893214983'],1)
    assert not (await execute('read',{'doc_id':secondary_doc}))['ok']
    assert (await execute('list',{}))['data']['total']==0
    event.binding=object()
    assert not (await execute('read',{'doc_id':main_doc}))['ok']
    owner.store.close()
