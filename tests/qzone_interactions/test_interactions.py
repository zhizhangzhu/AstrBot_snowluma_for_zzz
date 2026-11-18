import asyncio
from contextvars import ContextVar
import json
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from astrbot_plugin_private_companion import qzone_native_actions as native
from astrbot_plugin_private_companion import qzone_interactions as interactions
from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


def post(tid='a', comments=None, uin='2893214983'):
    return NS(tid=tid,uin=uin,text='空间动态',comments=comments or [],raw={'_snowluma_native_list':True})


def comment(cid='1',uid=1005145948,content='新评论',parent=None):
    raw={'comment_id':cid,'uin':uid,'name':'访客','time':time.time(),'content':content,'replies':[]}
    if parent:raw.update(parent)
    return QzoneMixin._qzone_parse_comments_from_msg({'tid':'a','commentlist':[raw]})[0]


def owner(tmp_path):
    o=QzoneMixin();o.data_dir=tmp_path;o.data={'users':{},'qzone_integration':{}}
    (tmp_path/'bot_accounts.json').write_text(json.dumps({
        '甲':{'bot_id':'2893214983','adapter_id':'xiaoxiaoqing','session_mode':'onebot','primary_user_id':'1005145948'},
        '乙':{'bot_id':'2510093206','adapter_id':'chuxiaoqing','session_mode':'onebot','primary_user_id':'1207862759'}}))
    scope=ContextVar('scope',default='甲');o._active_persona_scope=scope.get;o._primary_persona_id=lambda:'甲'
    o._save_data_sync=lambda **kw:None
    o._memory_companion_bridge=lambda:NS(record_event=AsyncMock(return_value='saved'))
    o._qzone_watch_binding=lambda:('scope','xiaoxiaoqing','2893214983',None)
    o._qzone_available=lambda *args:True
    o.persona_setting=lambda k,d=None:{'enable_qzone_comment_inbox':True}.get(k,d)
    o._qzone_get_cookies=AsyncMock(return_value='')
    o._qzone_context_from_cookies=lambda _: {'uin':2893214983}
    o._qzone_decide_comment_reply=AsyncMock(return_value={'decision':'reply','reply':'自然回应','reason':'接话'})
    o._qzone_reply_to_comment=AsyncMock(return_value='自然回应')
    o._qzone_post_time_text=lambda _:'today'
    return o,scope


@pytest.mark.asyncio
async def test_all_posts_pages_and_inline_replies_keep_post_identity():
    o=QzoneMixin();calls=[]
    async def run(action,**kw):
        calls.append((action,kw))
        if action=='get_qzone_msg_list':return {'msglist':[{'tid':'a','content':'first'},{'tid':'b','content':'second'}]}
        if action=='get_qzone_feeds':return {'feeds':[]}
        cid='1' if kw['pos']==0 else '2'
        replies=[{'comment_id':'1','uin':1005145948,'content':'楼中楼','time':2}] if cid=='1' else []
        return {'tid':kw['tid'],'comment_num':2,'comments':[{'comment_id':cid,'uin':1005145948,'content':'正文','time':1,'replies':replies}]}
    client=NS(call_action=run)
    result=await native.query(o,client,'2893214983',0,5,True)
    assert [p.comments[0].comment_id for p in result]==['a:1','b:1']
    assert {p for a,p in [(a,k['tid']) for a,k in calls if a=='get_qzone_msg_detail']}=={'a','b'}
    assert any(c.comment_id=='a:1:1' and c.raw['parent_comment_id']=='1' for c in result[0].comments)
    assert all(p.raw['_snowluma_comments_complete'] for p in result)


def test_legacy_collision_migration_recovers_only_recent_blocked_comment(tmp_path):
    o,_=owner(tmp_path);c=comment();p=post(comments=[c]);s=o.data['qzone_integration']
    s.update(comment_inbox_replied_ids=['post:1'],comment_inbox_seen_ids=['post:1'],
             comment_inbox_seen_keys=[o._qzone_comment_fingerprint('',c.raw)],comment_inbox_replied_keys=['post:fp:old'])
    interactions.migrate_comment_state(o,s,[p],2893214983)
    assert 'a:1' in s['comment_inbox_retry_ids'] and 'a:1' not in s['comment_inbox_replied_ids']
    assert 'a:1' not in s['comment_inbox_seen_ids']
    before=json.dumps(s,sort_keys=True);interactions.migrate_comment_state(o,s,[p],2893214983)
    assert json.dumps(s,sort_keys=True)==before


def test_migration_preserves_unknown_delivery_fingerprint(tmp_path):
    o,_=owner(tmp_path);c=comment();p=post(comments=[c]);s=o.data['qzone_integration']
    key=o._qzone_comment_fingerprint('',c.raw)
    s.update(comment_inbox_replied_ids=['post:1'],comment_inbox_delivery_unknown_keys=[key])
    interactions.migrate_comment_state(o,s,[p],2893214983)
    assert c.comment_key in s['comment_inbox_delivery_unknown_keys']
    assert not s['comment_inbox_retry_ids']


@pytest.mark.asyncio
async def test_memory_retry_and_two_persona_isolation(tmp_path):
    o,scope=owner(tmp_path);records=[]
    async def record(**kw):records.append(kw);return 'saved'
    sink=NS(record_event=record);o._memory_companion_bridge=lambda:sink
    c=comment();p=post(comments=[c])
    assert await interactions.remember_interaction(o,p,c,'comment')
    assert await interactions.remember_interaction(o,p,c,'comment')
    assert len(records)==1
    assert records[0]['session_id']=='xiaoxiaoqing:FriendMessage:1005145948'
    assert records[0]['metadata']['persona_id']=='甲'
    token=scope.set('乙')
    try:
        assert not await interactions.remember_interaction(o,p,c,'comment')
        foreign=comment(uid=1005145948)
        assert not await interactions.remember_interaction(o,post(uin='2510093206'),foreign,'comment')
        assert await interactions.remember_interaction(o,post(uin='2510093206'),comment(uid=1207862759),'like')
    finally:scope.reset(token)
    assert len(records)==2 and records[-1]['metadata']['owner_bot_id']=='2510093206'
    assert records[-1]['session_id']=='chuxiaoqing:FriendMessage:1207862759'
    assert '具体点赞时间未知' in records[-1]['content']
    c2=comment(cid='2');o._memory_companion_bridge=lambda:NS(record_event=AsyncMock(side_effect=ConnectionError()))
    assert not await interactions.remember_interaction(o,p,c2,'comment')
    o._memory_companion_bridge=lambda:sink
    assert await interactions.remember_interaction(o,p,c2,'comment')


@pytest.mark.asyncio
async def test_reply_targets_parent_and_never_retries_uncertain_write(tmp_path):
    o,_=owner(tmp_path);p=post();c=comment('1:2',parent={'parent_comment_id':'0','parent_comment_uin':1005145948})
    client=NS(call_action=AsyncMock(side_effect=ConnectionError()))
    with patch.object(native,'bound_client',return_value=client):
        with pytest.raises(native.QzoneIntegrationError) as error:await native.reply(o,None,p,c,'回复')
    assert error.value.delivery_unknown
    client.call_action.assert_awaited_once_with('reply_qzone_comment',target_uin=2893214983,tid='a',
        comment_id='0',comment_uin=1005145948,content='回复')


@pytest.mark.asyncio
async def test_like_read_failure_preserves_prior_observation(tmp_path):
    o,_=owner(tmp_path);s=o.data['qzone_integration'];s['like_observations']={'a':{'uins':['1005145948'],'complete':True}}
    with patch.object(interactions,'bound_client',return_value=NS(call_action=AsyncMock(side_effect=TimeoutError()))):
        await interactions.observe_interactions(o,[post()],2893214983)
    assert s['like_observations']['a']['uins']==['1005145948']
    assert s['last_like_observation_status']=='partial_failure'


@pytest.mark.asyncio
async def test_reply_budget_retains_pending_comments_for_next_tick(tmp_path):
    o,_=owner(tmp_path);c1=comment('1',content='第一条');c2=comment('2',uid=1005145949,content='第二条')
    p=post(comments=[c1,c2]);o._qzone_query_feeds=AsyncMock(return_value=[p])
    s=o.data['qzone_integration'];s.update(comment_inbox_initialized_at=time.time()-500,native_comment_identity_version=2,
        comment_inbox_seen_ids=['older:1'])
    with patch.object(interactions,'observe_interactions',new=AsyncMock()),patch.object(interactions,'remember_interaction',new=AsyncMock()):
        await o._maybe_process_qzone_comment_inbox_locked()
        assert o._qzone_reply_to_comment.await_count==1
        assert c2.comment_key in s['comment_inbox_retry_keys']
        s['last_comment_inbox_checked_at']=0
        await o._maybe_process_qzone_comment_inbox_locked()
        assert o._qzone_reply_to_comment.await_count==2
        assert c2.comment_key in s['comment_inbox_replied_keys']


@pytest.mark.asyncio
async def test_cancel_during_send_is_persisted_and_not_replayed(tmp_path):
    o,_=owner(tmp_path);c=comment();p=post(comments=[c]);o._qzone_query_feeds=AsyncMock(return_value=[p])
    s=o.data['qzone_integration'];s.update(comment_inbox_initialized_at=time.time()-500,
        native_comment_identity_version=2,comment_inbox_seen_ids=['older:1'])
    o._qzone_reply_to_comment=AsyncMock(side_effect=asyncio.CancelledError())
    with patch.object(interactions,'observe_interactions',new=AsyncMock()):
        with pytest.raises(asyncio.CancelledError):await o._maybe_process_qzone_comment_inbox_locked()
        assert c.comment_key in s['comment_inbox_delivery_unknown_keys']
        s['last_comment_inbox_checked_at']=0
        await o._maybe_process_qzone_comment_inbox_locked()
        o._qzone_reply_to_comment.assert_awaited_once()


@pytest.mark.asyncio
async def test_likes_memory_survives_desktop_origin_and_deduplicates_across_ticks(tmp_path):
    o,_=owner(tmp_path);o.data['users']['1005145948']={'umo':'desktop_pet:FriendMessage:1005145948'}
    record=AsyncMock(return_value='saved');o._memory_companion_bridge=lambda:NS(record_event=record)
    client=NS(call_action=AsyncMock(return_value={'tid':'a','target_uin':'2893214983','total':1,
        'users':[{'uin':'1005145948','name':'访客'}],'next_uin':0}))
    with patch.object(interactions,'bound_client',return_value=client):
        await interactions.observe_interactions(o,[post()],2893214983)
        await interactions.observe_interactions(o,[post()],2893214983)
    record.assert_awaited_once()
    assert record.call_args.kwargs['session_id']=='xiaoxiaoqing:FriendMessage:1005145948'
    assert o.data['qzone_integration']['like_observations']['a']['complete']


@pytest.mark.asyncio
async def test_upgrade_runs_one_repair_scan_without_waiting_old_poll_interval(tmp_path):
    o,_=owner(tmp_path);c=comment();p=post(comments=[c]);o._qzone_query_feeds=AsyncMock(return_value=[p])
    s=o.data['qzone_integration'];s.update(comment_inbox_initialized_at=time.time()-500,
        last_comment_inbox_checked_at=time.time(),comment_inbox_seen_ids=['post:1'],
        comment_inbox_seen_keys=[o._qzone_comment_fingerprint('',c.raw)],comment_inbox_replied_ids=['post:1'])
    with patch.object(interactions,'observe_interactions',new=AsyncMock()),patch.object(interactions,'remember_interaction',new=AsyncMock()):
        await o._maybe_process_qzone_comment_inbox_locked()
        o._qzone_reply_to_comment.assert_awaited_once()
        assert s['native_comment_identity_version']==2
        await o._maybe_process_qzone_comment_inbox_locked()
        o._qzone_reply_to_comment.assert_awaited_once()


def test_primary_default_scope_runs_its_own_registered_qzone_maintenance(tmp_path):
    o,scope=owner(tmp_path);o.enable_multi_persona_mode=True
    token=scope.set('')
    try:
        assert o._qzone_primary_persona_id()=='甲'
        assert o._qzone_automatic_persona_active()
    finally:scope.reset(token)
    token=scope.set('乙')
    try:
        assert o._qzone_primary_persona_id()=='乙'
        assert o._qzone_automatic_persona_active()
    finally:scope.reset(token)


@pytest.mark.asyncio
async def test_one_failed_detail_does_not_block_other_posts_or_become_empty_success():
    o=QzoneMixin();o.data={'qzone_integration':{}}
    client=object();posts=[post('a'),post('b')]
    async def enrich(owner,client,item):
        if item.tid=='b':raise native.QzoneIntegrationError('原生读取失败','network',retryable=True)
        item.comments=[comment()]
    with patch.object(native,'bound_client',return_value=client),patch.object(native,'query',new=AsyncMock(return_value=posts)),patch.object(native,'enrich_detail',side_effect=enrich):
        result=await native.inbox_posts(o,2893214983,5)
        assert [x.tid for x in result]==['a']
        assert o.data['qzone_integration']['comment_inbox_read_errors'][0]['tid']=='b'
    with patch.object(native,'bound_client',return_value=client),patch.object(native,'query',new=AsyncMock(return_value=[post('b')])),patch.object(native,'enrich_detail',side_effect=enrich):
        with pytest.raises(native.QzoneIntegrationError):await native.inbox_posts(o,2893214983,5)
