import asyncio
import copy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

import pytest
from test_interactions import owner,post,comment
from astrbot_plugin_private_companion import qzone_notifications as notices
from astrbot_plugin_private_companion.qzone_errors import QzoneIntegrationError

BOT='2893214983';USER='1005145948'


def row(tid='older-post',stamp=800,label='回复',cid='1',actor=USER,host=BOT):
    return {'appid':'311','typeid':'2','key':tid+'b','uin':actor,'opuin':actor,'abstime':str(stamp),
      'html':f'''<li><div class="f-nick"><a class="f-name">访客</a><span class="state">{label}</span></div>
      <span class="state">3分钟前</span><i name="feed_data" data-subappid="311" data-uin="{host}" data-tid="{tid}"></i>
      <p class="txt-box-title"><a class="nickname">Bot</a><span class="state">：</span>原说说</p>
      <li class="comments-item" data-uin="{actor}" data-tid="{cid}">评论</li></li>'''}


def page(rows,more=False,offset=0,bot=BOT):
    return {'self_uin':bot,'items':rows,'has_more':more,'next_offset':offset,'begin_time':'0','end_time':'0'}


def host(tmp_path):
    o,scope=owner(tmp_path);o._qzone_state_dict()['last_comment_inbox_checked_at']=900
    client=NS(call_action=AsyncMock());o._qzone_watch_binding=lambda:('scope','xiaoxiaoqing',BOT,client)
    return o,scope,client


def test_parser_uses_hidden_original_author_not_notification_actor():
    n=notices.parse_notice(row());assert n['actor_uin']==USER and n['post_uin']==BOT
    assert n['tid']=='older-post' and n['label']=='回复' and n['preview']=='原说说'
    assert notices.parse_notice({'key':'advertisement','uin':'0','html':''}) is None
    assert notices.parse_notice(row(label='赞了我的说说'))['kind']=='like'
    assert notices.parse_notice(row(label='提到了我'))['kind']=='mention'


def test_relative_time_and_html_layout_do_not_create_new_notifications():
    a=row();b=copy.deepcopy(a);b['html']=b['html'].replace('3分钟前','8分钟前').replace('<li>','<li class="layout-changed">',1)
    assert notices.parse_notice(a)['id']==notices.parse_notice(b)['id']
    b=row(cid='2');assert notices.parse_notice(a)['id']!=notices.parse_notice(b)['id']


@pytest.mark.asyncio
async def test_initial_baseline_and_unchanged_notifications_do_not_read_posts(tmp_path):
    o,_,client=host(tmp_path);client.call_action.return_value=page([row()])
    with patch.object(notices.time,'time',return_value=1000),patch.object(notices,'enrich_detail',new=AsyncMock()) as enrich:
        assert await notices.notified_posts(o,int(BOT))==[]
        assert await notices.notified_posts(o,int(BOT))==[]
        enrich.assert_not_awaited();o._qzone_get_cookies.assert_not_awaited()
    assert [c.args[0] for c in client.call_action.call_args_list]==['get_qzone_notifications']*2


@pytest.mark.asyncio
async def test_new_notification_on_old_post_reads_only_that_post_and_survives_restart(tmp_path):
    o,_,client=host(tmp_path);client.call_action.return_value=page([row()])
    with patch.object(notices.time,'time',return_value=1000):await notices.poll(o,int(BOT))
    client.call_action.return_value=page([row(stamp=1010,cid='2')])
    async def enrich(owner,client,p):
        c=comment('2');c.create_time=1010;c.comment_id=p.tid+':2';p.comments=[c];p.raw['content']='旧说说正文'
    with patch.object(notices.time,'time',return_value=1020),patch.object(notices,'enrich_detail',side_effect=enrich) as detail:
        posts=await notices.notified_posts(o,int(BOT))
        assert len(posts)==1 and posts[0].tid=='older-post'
        assert posts[0].text=='旧说说正文';detail.assert_awaited_once()
        saved=copy.deepcopy(o.data)
    o2,_,client2=host(tmp_path);o2.data=saved;client2.call_action.return_value=page([row(stamp=1010,cid='2')])
    with patch.object(notices.time,'time',return_value=1030),patch.object(notices,'enrich_detail',side_effect=enrich):
        posts=await notices.notified_posts(o2,int(BOT));notices.finish(o2,posts)
    assert not o2.data['qzone_integration']['related_notifications']['pending']
    with patch.object(notices,'enrich_detail',new=AsyncMock()) as detail:
        assert await notices.notified_posts(o2,int(BOT))==[];detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_baseline_carries_only_interactions_since_last_old_scan(tmp_path):
    o,_,client=host(tmp_path);client.call_action.return_value=page([row('newer',950),row('ancient',800)])
    with patch.object(notices.time,'time',return_value=1000):state=await notices.poll(o,int(BOT))
    assert list(state['pending'])==[BOT+'/newer']


@pytest.mark.asyncio
async def test_pagination_uses_server_cursor_and_does_not_skip_new_tail(tmp_path):
    o,_,client=host(tmp_path);client.call_action.side_effect=[page([row('a')],True,6),page([row('b')])]
    with patch.object(notices.time,'time',return_value=1000):state=await notices.poll(o,int(BOT))
    assert state['baseline_complete'];assert len(state['seen'])==2
    assert [c.kwargs['offset'] for c in client.call_action.call_args_list]==[0,6]


@pytest.mark.asyncio
async def test_failed_page_never_seeds_or_clears_seen_state(tmp_path):
    o,_,client=host(tmp_path);client.call_action.return_value=page([row()])
    with patch.object(notices.time,'time',return_value=1000):await notices.poll(o,int(BOT))
    original=copy.deepcopy(o.data['qzone_integration']['related_notifications'])
    client.call_action.side_effect=ConnectionError()
    with pytest.raises(QzoneIntegrationError):await notices.poll(o,int(BOT))
    assert o.data['qzone_integration']['related_notifications']==original


@pytest.mark.asyncio
async def test_pagination_failure_retains_new_work_already_observed(tmp_path):
    o,_,client=host(tmp_path);client.call_action.side_effect=[page([row('new',950)],True,6),ConnectionError()]
    with patch.object(notices.time,'time',return_value=1000),pytest.raises(QzoneIntegrationError):await notices.poll(o,int(BOT))
    state=o.data['qzone_integration']['related_notifications'];assert BOT+'/new' in state['pending']
    assert state['baseline_cursor']['offset']==6 and not state.get('baseline_complete')


@pytest.mark.asyncio
async def test_bot_mismatch_and_rebind_are_rejected(tmp_path):
    o,scope,client=host(tmp_path);client.call_action.return_value=page([],bot='2510093206')
    with pytest.raises(QzoneIntegrationError):await notices.poll(o,int(BOT))
    client.call_action.return_value=page([])
    await notices.poll(o,int(BOT));token=scope.set('乙')
    try:
        with pytest.raises(QzoneIntegrationError):notices.store(o,2510093206)
    finally:scope.reset(token)


@pytest.mark.asyncio
async def test_mentions_do_not_guess_a_foreign_public_reply_target(tmp_path):
    o,_,client=host(tmp_path);client.call_action.return_value=page([])
    with patch.object(notices.time,'time',return_value=1000):await notices.poll(o,int(BOT))
    client.call_action.return_value=page([row('foreign',1010,'提到了我',host=USER)])
    sink=NS(record_event=AsyncMock(return_value='saved'));o._memory_companion_bridge=lambda:sink
    with patch.object(notices.time,'time',return_value=1020),patch.object(notices,'enrich_detail',new=AsyncMock()) as detail:
        assert await notices.notified_posts(o,int(BOT))==[];detail.assert_not_awaited()
    args=sink.record_event.call_args.kwargs
    assert args['session_id']==f'xiaoxiaoqing:FriendMessage:{USER}'
    assert args['metadata']['target_verified'] is False


@pytest.mark.asyncio
async def test_detail_failure_keeps_job_without_scanning_recent_posts(tmp_path):
    o,_,client=host(tmp_path);client.call_action.return_value=page([row('old',950)])
    with patch.object(notices.time,'time',return_value=1000),patch.object(notices,'enrich_detail',new=AsyncMock(side_effect=QzoneIntegrationError('读取失败','network'))):
        assert await notices.notified_posts(o,int(BOT))==[]
    state=o.data['qzone_integration']['related_notifications'];assert BOT+'/old' in state['pending']
    assert state['pending'][BOT+'/old']['retry_after']==1300
    assert o.data['qzone_integration']['comment_inbox_read_errors']


@pytest.mark.asyncio
async def test_actual_inbox_routes_notifications_at_five_minutes_without_cookie_or_profile_scan(tmp_path):
    o,_,client=host(tmp_path);o.persona_setting=lambda k,d=None:{'enable_qzone_comment_inbox':True,
        'qzone_use_related_notifications':True,'qzone_comment_inbox_interval_minutes':5}.get(k,d)
    o._qzone_query_feeds=AsyncMock(side_effect=AssertionError('must not scan profiles'))
    o.data['qzone_integration'].update(native_comment_identity_version=2,comment_inbox_initialized_at=1)
    with patch.object(notices,'notified_posts',new=AsyncMock(return_value=[])) as fetch,patch('astrbot_plugin_private_companion.qzone_interactions.observe_interactions',new=AsyncMock(return_value=True)):
        with patch('astrbot_plugin_private_companion.qzone_comments._now_ts',return_value=1000):await o._maybe_process_qzone_comment_inbox_locked()
        with patch('astrbot_plugin_private_companion.qzone_comments._now_ts',return_value=1299):await o._maybe_process_qzone_comment_inbox_locked()
        assert fetch.await_count==1
        with patch('astrbot_plugin_private_companion.qzone_comments._now_ts',return_value=1300):await o._maybe_process_qzone_comment_inbox_locked()
        assert fetch.await_count==2
    o._qzone_get_cookies.assert_not_awaited();o._qzone_query_feeds.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[True,False])
async def test_notification_mode_is_polled_each_scheduler_cycle(enabled):
    from astrbot_plugin_private_companion.daily_state import DailyStateMixin
    owner=NS(persona_setting=lambda k,d=None:enabled if k=='qzone_use_related_notifications' else d,
        _maybe_process_qzone_comment_inbox=AsyncMock(),_poll_qzone_watch=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):await DailyStateMixin._tick(owner)
    assert owner._maybe_process_qzone_comment_inbox.await_count==int(enabled)


def test_new_notification_setting_materializes_false_for_existing_secondary_persona():
    from astrbot_plugin_private_companion.persona_config import migrate_persona_settings,load_scope_manifest
    manifest=load_scope_manifest()
    assert manifest['qzone_use_related_notifications']['scope']=='persona'
    values,version,_=migrate_persona_settings({'bot_name':'乙'},stored_version=16,manifest=manifest)
    assert version>=17 and values['qzone_use_related_notifications'] is False


def test_real_comment_notifications_may_omit_subappid():
    notice=row();notice['html']=notice['html'].replace(' data-subappid="311"','')
    parsed=notices.parse_notice(notice)
    assert parsed['post_uin']==BOT and parsed['tid']=='older-post'
