"""Expression can be reciprocal without changing identity, permission or real history."""
from copy import deepcopy
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.qzone_publish import QzonePublishMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin
from astrbot_plugin_private_companion.persona_config import load_scope_manifest,resolve_persona_setting


class Host(UserMemoryMixin,QzonePublishMixin,ProactiveMessageMixin):
    def __init__(self,mode='peer',pid='小小情'):
        self.mode=mode;self.pid=pid;self.data={}
    def persona_setting(self,key,default=None):
        return {'persona_interaction_orientation':self.mode,'persona_peer_relationship_prompt':'平等相处，共同探索',
            'enable_custom_relationship_stage_policy':True,
            'enable_companion_growth':True,'enable_growth_semantic_portrait':True}.get(key,default)
    def _effective_plugin_persona_id(self):return self.pid


def test_only_opted_in_persona_gets_free_expression():
    peer=Host();legacy=Host(mode='persona',pid='初小晴')
    assert '平等交流' in peer._proactive_expression_shape_prompt_section({},reason='quiet_care',action='message').content
    assert '20 个汉字' in legacy._proactive_expression_shape_prompt_section({},reason='quiet_care',action='message').content
    current=peer._format_proactive_generation_intent_prompt_section({},reason='web_exploration_share',action='message',motive='读过的文章')
    assert '读过的文章' in current.content and '输出空文本' in current.content and '已' in current.content
    assert '不是取消发送的理由' not in current.content
    full_prompt=peer._default_proactive_prompt_template()
    assert '平等交流' in full_prompt
    assert '一两句即可' not in full_prompt and '一个画面' not in full_prompt
    assert '一两句即可' in legacy._default_proactive_prompt_template()


def test_creative_topics_are_free_and_legacy_persona_still_uses_its_style():
    peer=Host();legacy=Host(mode='persona',pid='初小晴')
    assert '结构由兴趣和表达意愿决定' in peer._qzone_publish_style_prompt()
    assert '只写一个具体画面加一个动作' not in peer._qzone_publish_style_prompt()
    assert '只写一个具体画面加一个动作' in legacy._qzone_publish_style_prompt()
    assert peer._qzone_publish_theme_hint()=='按当前人格的兴趣和当下表达意愿选择主题'
    assert legacy._qzone_publish_theme_hint()


def test_relationship_style_is_private_exactly_scoped_and_keeps_learned_facts():
    peer=Host();user={'user_id':'owner','relationship_role':'owner','relationship_mode':'normal',
        'relationship_score':174,'persona_relationship_prompts':{'小小情':{
            'persona_id':'小小情','stable_user_id':'owner','source':'learned_dialogue',
            'relationship_mode':'normal','text':'双方已确认的相处事实'}}}
    before=deepcopy(user)
    section=peer._format_owner_exclusive_relationship_prompt_section(user,stable_user_id='owner')
    assert '平等相处' in section.content and '双方已确认的相处事实' in section.content
    assert user==before
    assert peer._format_owner_exclusive_relationship_prompt_section(user,stable_user_id='owner',channel_scope='group') is None
    assert peer._format_owner_exclusive_relationship_prompt_section(user,stable_user_id='other') is None
    assert Host(pid='初小晴')._format_owner_exclusive_relationship_prompt_section(user,stable_user_id='owner') is None


def test_orientation_and_private_relation_do_not_inherit_from_primary():
    manifest=load_scope_manifest()
    for key,value,default in [('persona_interaction_orientation','peer','persona'),
                              ('persona_peer_relationship_prompt','主人格关系','')]:
        assert manifest[key]['scope']=='persona'
        assert manifest[key]['default']==default
        assert resolve_persona_setting(key,{}, {key:value},manifest=manifest,default=default)==default


def test_verified_facts_influence_expression_but_keep_score_and_contact_boundary():
    user={'user_id':'owner','relationship_role':'owner','relationship_mode':'normal',
        'relationship_score':174,'persona_relationship_prompts':{'小小情':{
            'persona_id':'小小情','stable_user_id':'owner','source':'learned_dialogue',
            'relationship_mode':'normal','text':'双方已确认的相处事实'}}}
    host=Host()
    decision=host._build_expression_decision_for_user(user,_authoritative_relationship_view=True)
    assert '平等' in decision.tone
    assert user['relationship_score']==174 and user['relationship_mode']=='normal'
    legacy=Host(mode='persona')._build_expression_decision_for_user(deepcopy(user),_authoritative_relationship_view=True)
    assert decision.proactive_target==legacy.proactive_target
    assert decision.proactive_budget==legacy.proactive_budget
    user['contact_preference']={'no_contact':True}
    blocked=host._build_expression_decision_for_user(user,_authoritative_relationship_view=True)
    assert blocked.blocker and blocked.initiative!='allowed'
    unknown=deepcopy(user);unknown.pop('persona_relationship_prompts');unknown.pop('contact_preference')
    unverified=host._build_expression_decision_for_user(unknown,_authoritative_relationship_view=True)
    assert '平等' not in unverified.tone
