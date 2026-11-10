"""Explicit historical targeting and paraphrases should not be rejected as new-topic drift."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import pytest
from astrbot_plugin_private_companion.private_quotes.relevance import supports_reply,validate_reply


def test_return_to_old_topic_is_not_required_to_match_latest_inbound():
    state={};event=SimpleNamespace(message_str='现在在听歌')
    assert supports_reply(None,event,state,{'mid':'1','text':'芝麻口味会不会太甜'},'再接你前面那个问题，芝麻口味没有那么甜')
    assert not supports_reply(None,event,{}, {'mid':'1','text':'芝麻口味会不会太甜'},'今晚去看电影')


@pytest.mark.asyncio
async def test_paraphrase_verified_once_and_unrelated_reply_rejected():
    host=SimpleNamespace(_llm_call=AsyncMock(return_value='{"related": true}'),_extract_json_payload=json.loads)
    event=SimpleNamespace(message_str='你还记得之前那件事吗');state={};target={'mid':'1','text':'今晚提醒我二游换抽数'}
    assert await validate_reply(host,event,state,target,'游戏里的兑换额度别忘了领取')
    assert await validate_reply(host,event,state,target,'游戏里的兑换额度别忘了领取')
    host._llm_call.assert_awaited_once()
    host._llm_call.return_value='{"related": false}'
    assert not await validate_reply(host,event,{},target,'窗外的落日真好看')


@pytest.mark.asyncio
async def test_semantic_failure_preserves_answer_but_does_not_invent_quote():
    host=SimpleNamespace(_llm_call=AsyncMock(side_effect=TimeoutError()))
    assert not await validate_reply(host,SimpleNamespace(message_str='别的话题'),{},
        {'mid':'1','text':'今晚提醒我二游换抽数'},'再说另外一件事')


@pytest.mark.asyncio
async def test_quote_checks_full_answer_and_attaches_only_to_first_segment(tmp_path):
    import sys
    from pathlib import Path
    from astrbot.api.message_components import Plain, Reply
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot_plugin_private_companion.private_quotes import transport
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'data/astrbot/plugins/astrbot_plugin_private_companion/tests'))
    from test_private_quote_experience import Owner, Event, choose
    owner=Owner(tmp_path);event=Event('现在在听歌')
    _,state,_=await choose(owner,event,text='芝麻口味会不会太甜')
    state['reply_text']='再补充一句，芝麻口味没有那么甜'
    await transport.send(owner,event,event.send,MessageChain([Plain('再补充一句')]),(),{})
    await transport.send(owner,event,event.send,MessageChain([Plain('芝麻口味没有那么甜')]),(),{})
    assert isinstance(event.sent[0].chain[0],Reply)
    assert not any(isinstance(c,Reply) for c in event.sent[1].chain)


def test_local_anchor_search_is_scoped_withdrawn_and_age_limited(tmp_path):
    import time
    from astrbot_plugin_private_companion.private_quotes.store import QuoteStore
    store=QuoteStore(tmp_path/'quotes.db')
    binding=dict(platform='xiaoxiaoqing',bot='2893214983',persona='小小情',
        umo='xiaoxiaoqing:FriendMessage:1005145948',cid='one',user='1005145948')
    store.save(binding,'1','1005145948','芝麻甜度的旧问题')
    store.save(binding,'2','1005145948','芝麻已经撤回');store.withdraw(binding,'2')
    store.save(binding,'3','1005145948','芝麻早于窗口',stamp=time.time()-25*3600)
    for k,v in [('persona','初小晴'),('bot','2510093206'),('cid','other')]:
        other={**binding,k:v};store.save(other,'4','1005145948','芝麻另一作用域')
    for n in range(30):store.save(binding,str(100+n),'2893214983','最新的其它话题')
    assert all(r['mid']!='1' for r in store.recent(binding))
    assert [r['mid'] for r in store.search(binding,'芝麻',hours=24)]==['1']
    assert store.search(binding,"' OR 1=1 --")==[]
