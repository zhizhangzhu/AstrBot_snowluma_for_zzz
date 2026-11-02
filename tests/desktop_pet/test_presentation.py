import json
from types import SimpleNamespace
from plugins.astrbot_plugin_desktop_pet.presentation import EMBODIMENT, snapshot_for

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
