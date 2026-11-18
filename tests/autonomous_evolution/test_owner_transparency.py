"""Real Companion rules: the per-persona owner grant is not a global leak bypass."""
from types import SimpleNamespace as S
import importlib.util
from pathlib import Path

import pytest
pytest.importorskip('astrbot.api')
from astrbot_plugin_private_companion.owner_transparency import allowed, protocol_payload, DIRECTIVE
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class Host(ProactiveEngineMixin):
    enable_maslow_motivation_experiment = False
    def __init__(self):
        self.allow_owner_internal_discussion = False
        self.user = {'user_id':'1005145948','relationship_role':'owner','umo':'xiaoxiaoqing:FriendMessage:1005145948'}
        self.data = {'users':{'1005145948':self.user}}
    def _relationship_owner_user_ids(self):return ['1005145948']
    def _qzone_watch_binding(self):return None,'xiaoxiaoqing','2893214983',None
    def _private_user_role(self,u):return u.get('relationship_role','friend')
    def _is_vague_seek_user_motive(self,*a,**kw):return False
    def _unverified_social_relay_plan_reason(self,*a,**kw):return False
    def _friend_can_receive_proactive_reason(self,*a,**kw):return True
    def _proactive_text_is_intimate(self,*a,**kw):return False
    def _proactive_reason_is_intimate(self,*a,**kw):return False
    def _proactive_action_is_intimate(self,*a,**kw):return False
    def _persona_action_profile(self):return {}
    def _current_emotion_gate_mode(self,*a,**kw):return ''
    def _current_relationship_gate_mode(self,*a,**kw):return ''


def test_owner_and_persona_gate_does_not_follow_role_strings_alone():
    h=Host();assert not allowed(h,h.user)
    h.allow_owner_internal_discussion=True
    assert allowed(h,h.user) and allowed(h,umo=h.user['umo'])
    assert not allowed(h,{**h.user,'user_id':'another'})
    assert not allowed(h,{**h.user,'relationship_role':'friend'})
    assert not allowed(h,h.user,umo='xiaoxiaoqing:GroupMessage:123')
    assert not allowed(h,{**h.user,'umo':'chuxiaoqing:FriendMessage:1005145948'})
    h.persona_setting=lambda key,default:False  # Secondary persona's explicit override wins over the root value.
    assert not allowed(h,h.user)


def test_real_local_candidate_and_alignment_rules_allow_authorized_mechanism_discussion():
    h=Host();kw=dict(reason='development_approval',action='message',topic='我想改进记忆插件的失败说明',motive='和你讨论模型与工具接口的真实反馈',source='development')
    assert h._proactive_candidate_semantics(h.user,context={'summary':'后台方案待审批'},**kw)['blocker']
    assert h._proactive_persona_alignment(h.user,**kw)['blocker']
    h.allow_owner_internal_discussion=True
    assert not h._proactive_candidate_semantics(h.user,context={'summary':'后台方案待审批'},**kw)['blocker']
    assert not h._proactive_persona_alignment(h.user,**kw)['blocker']
    other={**h.user,'user_id':'1207862759','relationship_role':'friend'}
    assert h._proactive_candidate_semantics(other,context={},**kw)['blocker']


@pytest.mark.asyncio
async def test_persona_and_judge_prompts_explain_the_grant():
    h=Host();h.allow_owner_internal_discussion=True
    h._get_default_persona_prompt=lambda *a:'小小情原有人格'
    prompt=await ProactiveMessageMixin._resolve_proactive_persona_prompt(h,h.user)
    assert prompt.startswith(DIRECTIVE) and '小小情原有人格' in prompt
    # Use the existing judge harness, while invoking the actual complete prompt builder.
    import astrbot_plugin_private_companion as package
    path=Path(package.__file__).parent/'tests/test_proactive_persona_judge_efficiency.py'
    spec=importlib.util.spec_from_file_location('transparency_judge_fixture',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    judge=mod._LocationJudgeHarness();judge.allow_owner_internal_discussion=True
    judge._relationship_owner_user_ids=h._relationship_owner_user_ids;judge._qzone_watch_binding=h._qzone_watch_binding
    user=mod._user(**h.user)
    prompt=ProactiveEngineMixin._format_proactive_model_judge_prompt(judge,user)
    assert '主要用户已授权机制讨论' in prompt and '- 不得放行内部机制泄露、工具名' not in prompt
    judge.allow_owner_internal_discussion=False
    assert '- 不得放行内部机制泄露、工具名' in ProactiveEngineMixin._format_proactive_model_judge_prompt(judge,user)


def test_rewrite_allows_human_explanation_but_retains_structured_protocol_checks():
    text='我刚才的工具调用失败了，记忆插件还没有改动，想请你看看方案'
    assert ProactiveMessageMixin._proactive_rewrite_blacklist_reason(text)
    assert not ProactiveMessageMixin._proactive_rewrite_blacklist_reason(text,allow_internal_discussion=True)
    assert protocol_payload('{"tool_calls":[]}')
    assert ProactiveMessageMixin._proactive_rewrite_blacklist_reason('{"decision":"send"}',allow_internal_discussion=True)


def test_final_delivery_keeps_owner_failure_explanations_and_rejects_protocol_payload():
    class Sender(Host,ProactiveMessageMixin):
        def _is_proactive_delivery_receipt_text(self,text):return '工具调用' in text
        def _is_proactive_instruction_leak_text(self,text):return '插件' in text or 'tool_calls' in text
        def _framework_agent_meta_summary_leak(self,text):return '插件' in text or 'tool_calls' in text
    h=Sender();umo=h.user['umo'];text='刚才的工具调用失败了，记忆插件尚未修改，我想请你看看方案'
    assert h._validate_proactive_outbound_candidate(text,umo=umo)['decision']=='drop'
    h.allow_owner_internal_discussion=True
    assert h._validate_proactive_outbound_candidate(text,umo=umo)['decision']=='send'
    assert h._sanitize_captured_plain_text(text,user=h.user)==text
    assert h._sanitize_captured_plain_text(text,umo=umo)==text
    assert not h._sanitize_captured_plain_text(text,umo='xiaoxiaoqing:FriendMessage:other')
    assert h._validate_proactive_outbound_candidate(text,umo='xiaoxiaoqing:FriendMessage:other')['decision']=='drop'
    assert h._validate_proactive_outbound_candidate('{"tool_calls":[]}',umo=umo)['decision']=='drop'


def test_schema_default_and_scope_materialize_per_persona():
    from astrbot_plugin_private_companion.persona_config import load_scope_manifest, PERSONA_SETTINGS_NEW_KEYS_BY_VERSION
    entry=load_scope_manifest()['allow_owner_internal_discussion']
    assert entry['scope']=='persona' and entry['new_key_default'] is False
    assert 'allow_owner_internal_discussion' in PERSONA_SETTINGS_NEW_KEYS_BY_VERSION[18]
