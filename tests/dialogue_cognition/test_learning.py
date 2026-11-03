import importlib.util
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace
from copy import deepcopy
import pytest

ROOT=Path(__file__).resolve().parents[2]
package=ModuleType('cognition_probe');package.__path__=[str(ROOT/'overrides/dialogue-cognition')];sys.modules[package.__name__]=package
continuity=ModuleType('cognition_probe.companion_continuity');continuity.loop_id=lambda row:row.get('continuity_id') or str(row.get('text'));sys.modules[continuity.__name__]=continuity
spec=importlib.util.spec_from_file_location('cognition_probe.learning_consistency',ROOT/'overrides/dialogue-cognition/learning_consistency.py');learning=importlib.util.module_from_spec(spec);spec.loader.exec_module(learning)

def test_equivalent_reminders_merge_but_distinct_times_stay_separate():
    user={'open_loops':[{'text':'晚上提醒我二游换抽数','status':'待自然延续'}, {'text':'提醒用户晚上二游兑换抽数','status':'待自然延续'}]}
    assert learning.consolidate_loops(user)==1
    assert len(user['open_loops'])==1
    assert not learning.same_topic('今晚8点提醒二游兑换抽数','今晚9点提醒二游兑换抽数')

def test_closed_topic_is_not_reopened_by_episode_replay_but_user_can_request_again():
    user={'open_loops':[{'text':'检查表情包收藏结果','status':'已完成'}]}
    assert not learning.add_loop(user,{'text':'检查表情包收藏结果','source':'dialogue_episode','status':'待自然延续'})
    assert learning.add_loop(user,{'text':'检查表情包收藏结果','source':'user_message','status':'待自然延续'})

def test_relationship_requires_real_user_evidence_and_preserves_manual_text():
    host=SimpleNamespace(_effective_plugin_persona_id=lambda:'persona-a',_normalize_owner_exclusive_relationship_prompt=lambda s:s)
    user={'user_id':'u','relationship_role':'owner','identity_persona_id':'persona-a'}
    sources=[{'id':'bot','role':'assistant','text':'we are partners','at':'2026-11-02'}, {'id':'user','role':'user','text':'I agree we are partners','at':'2026-11-02'}]
    payload={'relationship_updates':[{'text':'A mutually agreed partnership.','confidence':.98,'evidence':[{'id':'bot','quote':'we are partners'}]}]}
    learning.apply_learning(host,user,payload,sources,10)
    assert not user.get('persona_relationship_prompts')
    payload['relationship_updates'][0]['evidence'].append({'id':'user','quote':'I agree we are partners'})
    learning.apply_learning(host,user,payload,sources,11)
    assert user['persona_relationship_prompts']['persona-a']['source']=='learned_dialogue'
    user['persona_relationship_prompts']['persona-a'].update(source='manual',text='Manual boundary.')
    learning.apply_learning(host,user,payload,sources,12)
    assert user['persona_relationship_prompts']['persona-a']['text']=='Manual boundary.'
    other=deepcopy(user);other['identity_persona_id']='persona-b'
    assert learning.apply_learning(host,other,payload,sources,13)['relationship_updates']==0

def test_semantic_merge_rejects_stale_text_and_invalid_confidence():
    host=SimpleNamespace(_effective_plugin_persona_id=lambda:'p')
    user={'user_id':'u','relationship_role':'owner','open_loops':[{'text':'save a meme','continuity_id':'a'},{'text':'verify that meme saved','continuity_id':'b'}]}
    payload={'loop_merges':[{'loop_ids':['a','b'],'texts':['old text'],'confidence':.99}], 'relationship_updates':[{'confidence':'bad'}]}
    assert learning.apply_learning(host,user,payload,[],10)['merged_loops']==0

def test_temporal_narrative_is_not_an_occupation():
    path=ROOT/'data/astrbot/plugins/astrbot_plugin_memory_companion/core/profile_quality.py'
    if not path.exists():pytest.skip('Installed memory source required')
    spec=importlib.util.spec_from_file_location('quality_probe',path);quality=importlib.util.module_from_spec(spec);spec.loader.exec_module(quality)
    for text in ['我当时给朋友列了三个方案','我的职业是时给朋友列了三个方案','我是一个喜欢游戏的人']:
        assert not any(c.get('dimension')=='occupation' for c in quality.extract_profile_candidates(text))
    assert any(c.get('dimension')=='occupation' for c in quality.extract_profile_candidates('我从事软件开发'))

def test_source_selection_retains_relationship_evidence_outside_recent_chatter():
    import ast,json,re
    path=ROOT/'data/astrbot/plugins/astrbot_plugin_private_companion/companion_growth.py'
    if not path.exists():pytest.skip('Installed companion source required')
    method=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='own_sources')
    namespace={'json':json,'re':re};exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
    def row(i,text):return {'id':str(i),'scope':'private','session_id':'session','subject_id':'u','event_type':'user_message','content':text,'metadata':{'owner_bot_id':'bot','persona_id':'p'}}
    rows=[row(0,'我们之间是共同读书的伙伴关系。')]+[row(n,'普通闲聊') for n in range(1,100)]
    result=namespace['own_sources'](rows,uid='u',bot='bot',persona='p',umo='session')
    assert any(r['id']=='0' for r in result)
