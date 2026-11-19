"""Memory evidence survives retrieval, publication and concurrent learning."""
from copy import deepcopy
from dataclasses import replace
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as S
from unittest.mock import AsyncMock
import asyncio

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name in ('astrbot_plugin_memory_companion', 'astrbot_plugin_private_companion'):
    if name not in sys.modules:
        package = ModuleType(name)
        package.__path__ = [str(ROOT / 'vendor/installed-plugins' / name)]
        package.__file__ = str(Path(package.__path__[0]) / '__init__.py')
        sys.modules[name] = package

from astrbot_plugin_memory_companion.core.models import EntityRef, MemoryRecord, SessionContext, SearchResult
from astrbot_plugin_memory_companion.core.service import MemoryCompanionService
from astrbot_plugin_memory_companion.core.memory_revision import memory_ref
from astrbot_plugin_memory_companion.core.grounded_portrait import validate_normalization
from astrbot_plugin_memory_companion.core.visibility import VisibilityPolicy
from astrbot_plugin_memory_companion.core.astrbot_compat import detect_private_companion_request
from astrbot_plugin_private_companion.conversation_facts import self_names, learn_address
from astrbot_plugin_private_companion.companion_growth import review_can_apply, CompanionGrowthMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


def ctx(**changes):
    return replace(SessionContext(scope='private', session_id='primary:FriendMessage:u',
        platform='aiocqhttp', user_id='u', user_name='测试用户', bot_id='b', persona_id='p',
        message_text='乌龙茶', message_id='new'), **changes)


def memory(**changes):
    return replace(MemoryRecord(id='pref', memory_type='user_preference',
        scope='private', session_id='primary:FriendMessage:u', platform='primary',
        subject=EntityRef(kind='bot', id='b'), object=EntityRef(kind='user', id='u'),
        visibility='private_pair', reality_level='llm_tool_assertion', lifecycle='stable_memory',
        content='用户喜欢乌龙茶', evidence='我喜欢乌龙茶', confidence=.95, importance=.9,
        metadata={'owner_bot_id':'b','persona_id':'p','tool':'memory_companion_remember'}), **changes).ensure_defaults()


def service(tmp_path):
    directory = tmp_path / 'memory'
    directory.mkdir()
    companion = tmp_path / 'astrbot_plugin_private_companion'
    companion.mkdir()
    (companion/'bot_accounts.json').write_text(json.dumps({'p':{'adapter_id':'primary','bot_id':'b'},
        'other':{'adapter_id':'secondary','bot_id':'b2'}}))
    return MemoryCompanionService(context=None, config={'retrieval':{'mode':'basic'},
        'portrait':{'daily_dispatch_enabled':False}, 'memory_injection':{'max_chars':4000},
        'core_memory':{'enabled':False}, 'conversation_memory':{'recent_fact_guard_enabled':False,
        'cross_window_recent_continuity_enabled':False}},
        plugin_root=ROOT/'vendor/installed-plugins/astrbot_plugin_memory_companion', data_dir=directory)


@pytest.mark.asyncio
async def test_actual_retrieval_compose_publish_and_revalidate_keep_qq_adapter_alias(tmp_path):
    s=service(tmp_path)
    await s.initialize_database()
    try:
        record=memory()
        await s.store.insert_memory(record)
        s._p5_gate=AsyncMock(return_value={'ok':True})
        req=S(prompt='乌龙茶', system_prompt='<!-- private_companion_state_v1 -->',contexts=[])
        event=S()
        await s.inject_memories(ctx(),req,event=event)
        state=req.memory_companion_injection_state
        assert state['injected'], state
        assert 'pref' in state['injected_memory_ids']
        assert state['memory_refs'] and state['snapshot_id']
        assert '乌龙茶' in str(getattr(req,'extra_user_content_parts',''))+req.prompt
        # Actual reply-source validation still rejects another Bot and stale versions.
        refs=state['memory_refs']
        ok,_=await s._validate_reply_memory_refs(ctx(bot_id='b2',persona_id='other'),refs,{})
        assert not ok
        ok,_=await s._validate_reply_memory_refs(ctx(user_id='other',session_id='primary:FriendMessage:other'),refs,{})
        assert not ok
        record.content='用户已更正饮品偏好'
        await s.store.insert_memory(record)
        ok,_=await s._validate_reply_memory_refs(ctx(),refs,{})
        assert not ok
    finally:s.close()


def test_platform_alias_is_bound_to_the_exact_registered_namespace():
    policy=VisibilityPolicy(namespace_bindings=(('primary','b','p'),('secondary','b2','other')))
    assert policy.registered_platform_alias(memory(),ctx())
    assert not policy.registered_platform_alias(memory(),ctx(bot_id='b2',persona_id='other'))
    assert not policy.registered_platform_alias(memory(platform='discord'),ctx())
    assert not VisibilityPolicy().registered_platform_alias(memory(),ctx())


@pytest.mark.asyncio
async def test_remember_user_fact_is_not_a_bot_self_event(tmp_path):
    s=service(tmp_path);await s.initialize_database()
    try:
        s.identity.resolve_event_context=AsyncMock(return_value=ctx())
        s._schedule_memory_embedding=lambda *a,**k:None
        result=await s.tool_remember(S(),'用户喜欢乌龙茶',note_type='user_preference')
        assert result['ok']
        saved=await s.store.get_memory(result['memory_id'])
        assert saved.subject.kind=='user' and saved.subject.id=='u'
    finally:s.close()


def test_normalization_retains_verified_relationship_expression_and_transport(tmp_path):
    s=service(tmp_path)
    try:
        original=ctx(source_platform='desktop_pet',companion_expression_contract='verified',
            companion_expression_allowed_behaviors=('shared_ritual',),relationship_authority_source='registry')
        normalized=s._normalized_session_context(original)
        assert normalized.source_platform=='desktop_pet'
        assert normalized.companion_expression_contract=='verified'
        assert normalized.companion_expression_allowed_behaviors==('shared_ritual',)
        assert normalized.relationship_authority_source=='registry'
    finally:s.close()


def test_state_marker_does_not_prove_a_user_fact_is_already_supplied(tmp_path):
    s=service(tmp_path)
    try:
        slots={'user_profile':[SearchResult(memory(),1.0)]}
        state={'has_state':True,'has_companion_memory':True}
        kept,_=s._dedupe_slots_for_companion(slots,state,True)
        assert len(kept['user_profile'])==1
        kept,_=s._dedupe_slots_for_companion(slots,{**state,'current_profile_text':memory().content},True)
        assert not kept['user_profile']
        detected=detect_private_companion_request(S(prompt='你还记得'+memory().content+'吗？',
            system_prompt='<!-- private_companion_state_v1 -->'))
        assert memory().content not in detected['current_profile_text']
    finally:s.close()


def test_habit_question_is_recall_and_not_an_unrelated_new_topic(tmp_path):
    s=service(tmp_path)
    try:
        assert s._message_is_contextual_memory_request('我平时午饭吃什么')
        assert s._message_requests_personalized_context('我平时午饭吃什么')
        assert not s._message_is_contextual_memory_request('我平时吃面条')
    finally:s.close()


def test_object_first_phrase_survives_third_person_normalization_without_guessing():
    original='记住啦，栗安我经常说'
    row={'id':'t','scope':'private','session_id':'primary:FriendMessage:u','event_type':'user_message',
        'subject_id':'u','content':original,'metadata':{'owner_bot_id':'b','persona_id':'p'}}
    item={'source_id':'t','quote':original,'normalized_statement':'用户经常用栗安问早安','confidence':.99}
    params=dict(user_id='u',bot_id='b',persona_id='p',session_id=row['session_id'])
    candidates=validate_normalization(item,row,**params)
    assert len(candidates)==1 and candidates[0]['profile_value']=='栗安'
    assert not validate_normalization(item,{**row,'subject_id':'another'},**params)
    assert not validate_normalization({**item,'quote':'我喜欢不存在的东西'},row,**params)


@pytest.mark.parametrize('statement',['我是不可能迟到的哦ww','我是很开心的ww','我是准备去吃饭的'])
def test_normal_assertions_never_become_names(statement):
    assert self_names(statement)==[]


def test_bad_automatic_alias_is_archived_but_manual_name_preserved():
    bad='不可能迟到的哦ww'
    user={'nickname':'猪猪','aliases':[bad,'手动别名'],'alias_sources':{bad:{'source':'explicit_user_message','evidence':'我是'+bad}}}
    assert learn_address(user,'早安')
    assert user['aliases']==['手动别名'] and user['nickname']=='猪猪'
    assert user['rejected_aliases'][0]['name']==bad
    assert self_names('我是小明')==[('小明',False)]
    assert self_names('以后叫我小雨')==[('小雨',True)]


class Habits(UserMemoryMixin):
    user_habit_max_items=24


@pytest.mark.parametrize('text',['我看看','嗯？没有工具吗，不应该吧，那我亲自看一下ww','这是小猫在玩逗猫棒','我跟你开玩笑呢'])
def test_inspection_third_party_and_jokes_are_not_entertainment(text):
    assert Habits()._classify_user_habit_message(text)[0]!='娱乐习惯'


@pytest.mark.parametrize('text',['我正在玩王者荣耀','我在看电影','我去刷视频','我正在玩PJSK'])
def test_actual_user_entertainment_is_still_observed(text):
    assert Habits()._classify_user_habit_message(text)[0]=='娱乐习惯'


def test_polluted_habit_aggregate_does_not_keep_false_independent_day_count():
    user={'behavior_habits':{'patterns':[{'category':'娱乐习惯','examples':['我看看','我在看电影'],
        'last_seen_text':'我在看电影','evidence_days':['2026-11-18','2026-11-19','2026-11-20'],'count':7}]}}
    assert Habits()._sanitize_user_behavior_habit_patterns(user)
    assert not user['behavior_habits']['patterns']
    assert user['behavior_habits']['rejected_patterns']


def test_growth_rebases_append_only_chat_without_overwriting_new_samples():
    before={'user_id':'u','enabled':True,'relationship_role':'owner','unified_person_id':'person',
        'expression_profile':{'pending_samples':[{'id':'a','text':'旧原话'}]},'open_loops':[]}
    current=deepcopy(before)
    current['relationship_score']=123
    current['expression_profile']['pending_samples'].append({'id':'b','text':'新原话'})
    assert review_can_apply(before,current)
    current['expression_profile']['pending_samples'][0]['text']='经人工修改'
    assert not review_can_apply(before,current)
    current=deepcopy(before);current['unified_person_id']='another'
    assert not review_can_apply(before,current)
    current=deepcopy(before);current['open_loops']=[{'text':'新的承诺'}]
    assert not review_can_apply(before,current)


@pytest.mark.asyncio
async def test_explicit_evidence_repair_commits_through_authority_and_is_idempotent():
    class Repair(CompanionGrowthMixin, Habits):
        def __init__(self):
            bad='不可能迟到的哦ww'
            self.data={'users':{'u':{'user_id':'u','enabled':True,'relationship_role':'owner',
                'nickname':'手动称呼','aliases':[bad], 'alias_sources':{bad:{'source':'explicit_user_message',
                'evidence':'我是'+bad}},'behavior_habits':{'patterns':[]}}}}
            self._data_lock=asyncio.Lock()
            self.commits=[]
        def _req041_private_memory_person_key(self,uid):return uid
        def _req041_person_write_lock(self,key):return asyncio.Lock()
        def _req041_prepare_authoritative_private_memory(self,u):return 7
        def _req041_commit_authoritative_private_memory(self,u,**kw):self.commits.append(kw);return True
        def _save_data_sync(self,**kw):pass
    h=Repair()
    result=await h._repair_companion_learning_evidence('u')
    assert result['ok'] and result['aliases_repaired']
    assert h.data['users']['u']['nickname']=='手动称呼'
    assert h.commits[0]['expected_revision']==7 and h.commits[0]['fields']==('behavior_habits',)
    assert not (await h._repair_companion_learning_evidence('u'))['changed']
    h.data['users']['u']['relationship_role']='friend'
    assert not (await h._repair_companion_learning_evidence('u'))['ok']


@pytest.mark.asyncio
async def test_night_scene_tone_gate_keeps_the_real_evidence_version(tmp_path):
    s=service(tmp_path);await s.initialize_database()
    try:
        record=memory();record.metadata.update(scar_weight=.8,mention_policy='soft_echo')
        await s.store.insert_memory(record)
        stored=await s.store.get_memory(record.id);ref=memory_ref(stored)
        slots=s._apply_scar_scene_gate(ctx(),{'user_profile':[SearchResult(stored,1.0)]},time_of_day='late_night')
        item=slots['user_profile'][0]
        assert 'expression=tone' in item.reason
        assert memory_ref(item.memory)==ref and stored.metadata['mention_policy']=='soft_echo'
        ok,_=await s._validate_reply_memory_refs(ctx(),[ref],slots)
        assert ok
        uncertain=SearchResult(stored,.1,reason='expression=uncertain;expression_reason=low_confidence')
        gated=s._apply_scar_scene_gate(ctx(),{'user_profile':[uncertain]},time_of_day='late_night')
        assert 'expression=uncertain' in gated['user_profile'][0].reason
    finally:s.close()
