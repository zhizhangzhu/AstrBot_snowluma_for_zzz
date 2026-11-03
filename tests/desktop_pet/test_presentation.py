import json
from types import SimpleNamespace
from plugins.astrbot_plugin_desktop_pet.presentation import EMBODIMENT, snapshot_for, prepare_speech

def test_touch_snapshot_keeps_capability_discovery_without_repeating_full_descriptions():
    peer=SimpleNamespace(state={'connected':True},catalog={'worlds':[{'id':'desktop-pet','supported':True,
        'environment':'appearance '*400,'tools':[{'name':'pet_say','description':'long description '*20}]}]})
    observations=[{'text':str(n)} for n in range(8)]
    regular=snapshot_for(peer,observations)
    touch=snapshot_for(peer,observations,light=True)
    assert len(json.dumps(touch))<len(json.dumps(regular))/3
    assert touch['worlds'][0]['tools']==[{'name':'pet_say'}]
    assert touch['worlds'][0]['tool_count']==1 and touch['recent_observations']==observations[-3:]
    assert 'YOUR OWN embodied form' in EMBODIMENT and 'never your identity' in EMBODIMENT

def test_visible_tool_speech_uses_common_cleanup_without_rewriting_other_tool_arguments():
    calls=[]
    def common(text,**options):
        calls.append(options)
        return [text.rstrip('。')]
    owner=SimpleNamespace(_split_proactive_text=common)
    binding=SimpleNamespace(conversation_origin='verified-owner-session')
    source={'script':'A title《中间。保留》结束。','other':'unchanged'}
    cleaned=prepare_speech(owner,binding,'desktop-pet','pet_say',source)
    assert cleaned['script']=='A title《中间。保留》结束' and source['script'].endswith('。')
    assert cleaned['other']=='unchanged'
    assert calls[0]['umo']==binding.conversation_origin and calls[0]['common_transforms_only'] is True
    assert prepare_speech(owner,binding,'other-world','write_file',source) is source
