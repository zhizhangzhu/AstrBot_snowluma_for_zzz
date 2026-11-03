import asyncio
from types import SimpleNamespace
import pytest
from plugins.astrbot_plugin_desktop_pet.interactions import InteractionBuffer

@pytest.mark.asyncio
async def test_figure_and_adjacent_touches_have_one_ordered_turn_and_individual_receipts():
    commits=[];receipts=[]
    async def commit(b,e,**kwargs):commits.append(e)
    async def settle(b,e,status):receipts.append((e['id'],status))
    buffer=InteractionBuffer(commit,settle,delay=.01,max_wait=.02)
    a=SimpleNamespace(device_id='a');b=SimpleNamespace(device_id='b')
    def event(mid,text,kind='desktop-pet.touch',trigger='debounce'):
        return {'id':mid,'payload':{'text':text,'event_type':kind,'trigger':trigger}}
    buffer.figure(a,event('figure','new clothes','desktop-pet.figure'))
    buffer.offer(a,event('first','poked once'))
    buffer.offer(a,event('second','patted twice',trigger='piggyback'))
    buffer.offer(b,event('other','other persona'))
    await asyncio.sleep(.04)
    assert len(commits)==2
    turn=next(c for c in commits if c['id']=='second')
    assert turn['payload']['text'].index('new clothes')<turn['payload']['text'].index('poked once')<turn['payload']['text'].index('patted twice')
    assert 'other persona' not in turn['payload']['text']
    assert turn['payload']['trigger']=='debounce'
    assert ('first','observed') in receipts and ('second','queued') in receipts
    await buffer.close()

@pytest.mark.asyncio
async def test_shutdown_does_not_commit_to_retired_plugin():
    calls=[]
    async def effect(*a,**k):calls.append(a)
    buffer=InteractionBuffer(effect,effect,delay=.01)
    buffer.offer(SimpleNamespace(device_id='a'),{'id':'pending','payload':{'text':'touch'}})
    await buffer.close();await asyncio.sleep(.02)
    assert not calls
