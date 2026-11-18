from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import json
import pytest

from astrbot_plugin_autonomous_development.awareness import CapabilityAwareness
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.integrations import owner_identity
from astrbot_plugin_autonomous_development.policy import Identity


def setup(tmp_path):
    store = Store(tmp_path/'awareness.sqlite3')
    context = S(get_all_stars=lambda:[S(name='companion',activated=True), S(name='disabled-plugin',activated=False,module_path='disabled')],
                get_llm_tool_manager=lambda:S(func_list=[S(name='existing_tool',active=True),S(name='hidden_tool',active=True,handler_module_path='disabled')]))
    awareness=CapabilityAwareness(context, store, S(awareness=lambda:{'ready':False,'reason':'缺少向量服务'}),
                                  S(catalogue=lambda:{'safe_setting':{'locked':True,'secret':'should-never-copy'}}))
    awareness.companion_inventory=lambda:({'capabilities':[
        {'id':'extension:test:run','title':'关闭的现有功能','enabled':False,'available':False,'reason':'未授权'},
        {'id':'knowledge.read','title':'知识阅读','enabled':True,'available':False,'reason':'未选库'}],
        'features':[{'key':f'enable_feature_{n}','title':f'功能{n}','enabled':n%2==0,'role':'event_or_configuration'} for n in range(85)],
        'executable_skills':[{'name':'existing-skill','description':'真实说明'}],
        'services':['天气缓存'], 'knowledge_sources':[]}, set())
    return store,context,awareness


def test_readonly_inventory_distinguishes_registration_enablement_and_execution(tmp_path):
    store,ctx,aware=setup(tmp_path);snapshot=aware.snapshot(owner_identity())
    assert snapshot['counts']['features']==85
    assert snapshot['sections']['activities'][0]['authorized'] is False
    assert snapshot['sections']['activities'][1]['enabled'] and not snapshot['sections']['activities'][1]['available']
    assert snapshot['sections']['tools'][0]['status']=='registered_reference_only'
    assert not snapshot['sections']['tools'][1]['enabled']
    assert 'secret' not in json.dumps(snapshot)
    assert not snapshot['knowledge']['ready']
    assert store.rows('SELECT * FROM audit')==[]
    store.close()


def test_all_switches_are_queryable_without_silent_truncation_and_owner_is_required(tmp_path):
    store,ctx,aware=setup(tmp_path)
    first=aware.query(owner_identity(),'features',limit=60);second=aware.query(owner_identity(),'features',offset=first['next_offset'],limit=60)
    assert len(first['items'])+len(second['items'])==85 and second['next_offset'] is None
    assert aware.query(owner_identity(),'features',query='enable_feature_84')['total']==1
    assert aware.query(owner_identity(),'plugins')['total']==2
    with pytest.raises(PermissionError):aware.query(Identity('2510093206','初小晴','1207862759','chuxiaoqing'))
    store.close()


def test_desktop_inventory_filters_identity_and_discards_offline_catalog(tmp_path):
    store,ctx,aware=setup(tmp_path)
    binding=S(bot_id='2893214983',persona_id='小小情',user_id='1005145948',qq_platform_id='xiaoxiaoqing',conversation_origin='xiaoxiaoqing:FriendMessage:1005145948')
    other=S(bot_id='2510093206',persona_id='初小晴',user_id='1207862759',qq_platform_id='chuxiaoqing')
    peer=S(state={'locked':True,'screen_off':True,'private_window_title':'never forward'},catalog={'worlds':[{'id':'desktop-pet','supported':True,'tools':[{'name':'pet_move','description':'移动'}]}]})
    desktop=S(store=S(bindings=lambda:[other,binding],enabled=lambda b:True),hub=S(peer=lambda b:peer,preference=lambda o:'qq'))
    ctx.get_all_stars=lambda:[S(name='astrbot_plugin_desktop_pet',activated=True,star_cls=desktop)]
    result=aware.snapshot(owner_identity())
    assert result['desktop']['status']=='online' and not result['desktop']['interactive'] and result['desktop']['screen_off']
    assert result['counts']['desktop_tools']==1 and 'private_window_title' not in json.dumps(result)
    desktop.hub.peer=lambda b:None
    result=aware.snapshot(owner_identity())
    assert result['desktop']['status']=='offline' and result['sections']['desktop_tools']==[]
    desktop.store.bindings=lambda:[other]
    assert aware.snapshot(owner_identity())['desktop']['status']=='not_paired'
    store.close()


def test_partial_discovery_is_explicit_and_current_chat_tools_are_separate(tmp_path):
    store,ctx,aware=setup(tmp_path)
    def fail():raise RuntimeError('credential value must not leak')
    aware.companion_inventory=fail
    body=aware.prompt(S(func_tool=S(tools=[S(name='allowed_now',active=True)])))
    summary=json.loads(body.splitlines()[-1])
    assert summary['coverage']=='partial' and summary['errors'][0]['section']=='companion'
    assert summary['request_tools_at_observation']==['allowed_now'] and 'credential value' not in body
    store.close()


@pytest.mark.asyncio
async def test_design_can_inspect_existing_capabilities_before_proposing_code(tmp_path,monkeypatch):
    from astrbot_plugin_autonomous_development.agent import Agent
    from astrbot_plugin_autonomous_development.tasks import Tasks
    store,ctx,aware=setup(tmp_path);tasks=Tasks(store);who=owner_identity()
    row=tasks.create(who,'改进已有资料功能')
    worker=S(call=AsyncMock(return_value={'base_commit':'a'*40,'files':['docs/sample.md']}))
    monkeypatch.setattr('astrbot_plugin_autonomous_development.agent.dependencies',lambda c:())
    agent=Agent(ctx,store,tasks,worker,awareness=aware)
    agent.call=AsyncMock(return_value={'action':'capabilities','section':'features','query':'enable_feature_84'})
    await agent.step(row['id'])
    row=tasks.get(who,row['id'])
    assert row['state']=='proposed' and row['payload']['design_queries'][0]['total']==1
    assert [c.args[2] for c in worker.call.call_args_list]==['inventory','inventory']
    assert store.rows('SELECT * FROM approvals')==[]
    store.close()

@pytest.mark.asyncio
async def test_engine_provider_receives_live_inventory_for_design_and_review(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import astrbot_plugin_autonomous_development.agent as module
    from astrbot_plugin_autonomous_development.tasks import Tasks
    store,ctx,aware=setup(tmp_path);tasks=Tasks(store);calls=[]
    async def text_chat(**kwargs):calls.append(kwargs);return S(completion_text='{}')
    provider=S(text_chat=text_chat)
    ctx.get_all_providers=lambda:[provider]
    cp=S(real_schedule_record_external_usage=lambda **kw:True)
    @contextmanager
    def companion(context):yield cp
    monkeypatch.setattr(module,'dependencies',lambda c:())
    monkeypatch.setattr(module,'companion_context',companion)
    monkeypatch.setattr(module,'instrument_provider',lambda p:None)
    monkeypatch.setattr(module.importlib,'import_module',lambda name:S(service=lambda cp:S(hard_limit=lambda:False)))
    agent=module.Agent(ctx,store,tasks,S(),awareness=aware)
    for stage in ('design','development','review'):
        await agent.call('test-task','test-prompt',stage)
    assert len(calls)==3
    for call in calls:
        assert '[统一功能目录' in call['prompt'] and '关闭的现有功能' in call['prompt'] and '缺少向量服务' in call['prompt']
        assert call['request_max_retries']==1
    store.close()


def test_plugin_subswitch_discovery_never_copies_private_values_or_persona_rule_lists(tmp_path):
    store,ctx,aware=setup(tmp_path)
    class Config(dict):
        schema={'collection':{'type':'object','items':{'enabled':{'type':'bool','description':'自动收集'},'key':{'type':'string'}}},
                'profiles':{'type':'list'},'token':{'type':'string'}}
    cfg=Config(collection={'enabled':False,'key':'never-forward-key'},profiles=[{'persona':'初小晴','private':True}],token='never-forward-token')
    ctx.get_all_stars=lambda:[S(name='meme_manager',activated=True,config=cfg)]
    result=aware.snapshot(owner_identity());rows=result['sections']['plugin_switches']
    assert len(rows)==1 and rows[0]['enabled'] is False and rows[0]['key']=='collection.enabled'
    assert rows[0]['scope']=='shared_plugin_configuration'
    body=json.dumps(result,ensure_ascii=False)
    assert 'never-forward' not in body and '初小晴' not in body
    store.close()
