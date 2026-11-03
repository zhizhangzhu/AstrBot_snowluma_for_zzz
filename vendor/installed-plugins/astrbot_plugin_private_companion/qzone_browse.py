"""Browse the visible friend feed independently of focused author monitoring."""
from __future__ import annotations
import asyncio
import json
import re
import time
from types import SimpleNamespace

from .qzone_recent_parser import parse_recent_feeds
from .qzone_watch import clean, number, normalize_posts, user_ids

BROWSE_DEFAULTS={
 'enable_qzone_browse':False,
 'qzone_browse_interval_minutes':30,
 'qzone_browse_fetch_count':10,
 'qzone_browse_max_decisions_per_round':2,
 'qzone_browse_allow_message':True,
 'qzone_browse_allow_like':False,
 'qzone_browse_allow_comment':False,
 'qzone_browse_max_daily_actions':3,
 'qzone_browse_action_cooldown_minutes':60,
}
STREAM='@friends'

class QzoneBrowseMixin:
    def _qzone_browse_setting(self,key):
        return self.persona_setting(key,BROWSE_DEFAULTS[key])

    def _qzone_focused_author_ids(self):
        return set(user_ids(self._qzone_watch_setting('qzone_watch_user_ids'))) if self._qzone_watch_setting('enable_qzone_watch') else set()

    async def _qzone_browse_friend_ids(self,client,*,refresh=False):
        cache=getattr(self,'_qzone_browse_friend_cache',{})
        item=cache.get(id(client))
        if not refresh and item and time.time()-item[0]<600:return item[1]
        raw=await asyncio.wait_for(client.get_friend_list(),12)
        if isinstance(raw,dict):
            if raw.get('status')=='failed' or str(raw.get('retcode',0))!='0':raise ValueError('friend list unavailable')
            raw=raw.get('data',raw)
        if not isinstance(raw,list):raise ValueError('invalid friend list')
        ids={str(x.get('user_id')) for x in raw if isinstance(x,dict) and str(x.get('user_id','')).isdigit()}
        cache[id(client)]=(time.time(),ids);self._qzone_browse_friend_cache=cache
        return ids

    async def _qzone_query_friend_stream(self,client,limit=10,*,page=1,function_origin='',source_event=None):
        """Browser mode uses the shared browser session for every space request."""
        friends=await self._qzone_browse_friend_ids(client)
        _,_,bot_id,_=self._qzone_watch_binding()
        limit=number(limit,10,1,20)
        page=number(page,1,1,10)
        def normalize(candidates):
            out={}
            for p in candidates:
                uid=str(getattr(p,'uin',''))
                if uid==bot_id or uid not in friends:continue
                values=normalize_posts([{'tid':p.tid,'uin':uid,'name':p.name,'text':p.text or getattr(p,'rt_con',''),
                    'create_time':p.create_time,'images':p.images}],uid)
                for item in values:
                    item.update({k:getattr(p,k,'') for k in ['appid','typeid','fid','unikey','curkey','abstime']})
                    item['already_liked']=bool(getattr(p,'liked',False))
                    item['liked']=item['already_liked']
                    item['image_items']=list(getattr(p,'image_items',[]) or [])[:4]
                    raw=getattr(p,'raw',{})
                    if isinstance(raw,dict):
                        likes=raw.get('like_count') or raw.get('likecount') or (raw.get('like',{}).get('count') if isinstance(raw.get('like'),dict) else None)
                        if likes is not None:item['like_count']=number(likes,0,0,1000000000)
                    item['public_action_supported']=str(getattr(p,'appid','311') or '311')=='311'
                    out[(uid,item['tid'])]=item
            return list(out.values())[:limit]
        native_backoff=getattr(self,'_qzone_browse_native_backoff',{})
        if not getattr(self,'_qzone_browser_session_enabled',lambda:False)() and native_backoff.get(id(client),0)<=time.time():
            try:
                raw=await asyncio.wait_for(self._qzone_call_onebot_action(client,'get_qzone_feeds',page_num=page,count=limit),12)
                if not isinstance(raw,dict) or raw.get('status')=='failed' or str(raw.get('retcode',0))!='0':raise ValueError('native feed failed')
                data=raw.get('data',raw)
                if not isinstance(data,dict) or self._qzone_response_code(data) not in (0,'0'):raise ValueError('native feed error')
                posts=normalize(parse_recent_feeds(data))
                if posts:
                    from .information_sources import service as information_sources
                    await information_sources(self).capture_qzone(posts,channel='好友动态流：snowluma',function_origin=function_origin,event=source_event)
                    return posts,'snowluma'
            except Exception:
                native_backoff[id(client)]=time.time()+3600;self._qzone_browse_native_backoff=native_backoff
        cookie=await self._qzone_get_cookies(None)
        ctx=self._qzone_context_from_cookies(cookie)
        posts=[];token=str(ctx.get('qzonetoken') or '')
        h5_error=None
        try:
            if page!=1:raise ValueError('H5 snapshot is first-page only')
            snapshot=await self._qzone_h5_index_snapshot(None,cookie_header=cookie,ctx=ctx,max_cache_age=15)
            token=str(snapshot.get('token') or token)
            posts=normalize(parse_recent_feeds(snapshot.get('payload') or {}))
        except Exception as exc:h5_error=type(exc).__name__
        if posts:
            from .information_sources import service as information_sources
            await information_sources(self).capture_qzone(posts,channel='好友动态流：h5',function_origin=function_origin,event=source_event)
            return posts,'h5'
        params={'uin':ctx['uin'],'scope':0,'view':1,'filter':'all','flag':1,'applist':'all','pagenum':page,
            'count':limit,'refresh':0,'aisortEndTime':0,'aisortOffset':0,'getAisort':0,'aisortBeginTime':0,
            'begintime':0,'format':'json','g_tk':ctx['gtk'],'useutf8':1,'outputhtmlfeed':1}
        if token:params['qzonetoken']=token
        raw=await self._qzone_request(None,'GET',
            'https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more',
            params=params,headers={'Accept':'application/json, text/javascript, */*; q=0.01','X-Requested-With':'XMLHttpRequest'},cookie_header=cookie)
        if self._qzone_response_code(raw) not in (0,'0'):raise RuntimeError('QQ空间好友动态接口未成功返回')
        posts=normalize(parse_recent_feeds(raw))
        # Empty is reported honestly; never substitute our own space for friends.
        from .information_sources import service as information_sources
        await information_sources(self).capture_qzone(posts,channel='好友动态流：legacy',function_origin=function_origin,event=source_event)
        return posts,'legacy' if not h5_error else 'legacy_after_h5_error'

    def _qzone_contact_identity(self,uid,display_name='',*,event=None):
        """Join names only through a verified account ID in this Bot/persona partition."""
        uid=str(uid or '');scope,platform,bot_id,_=self._qzone_watch_binding()
        if event is not None and (str(event.get_self_id())!=bot_id or str(event.unified_msg_origin).split(':',1)[0]!=platform):
            return {'user_id':uid,'known_contact':False,'reason':'当前会话与QQ账号不匹配'}
        sender=str(event.get_sender_id()) if event is not None else ''
        role='bot_self' if uid==bot_id else 'current_user' if uid==sender else 'third_party'
        if event is not None and event.get_group_id():
            return {'user_id':uid,'owner_role':role,'observed_author_name':clean(display_name,40),
                'account_id_verified':bool(uid and uid.isdigit()),'known_contact':False,
                'match_basis':'仅QQ账号归属；群聊不提供私聊称呼和别名档案'}
        user=self.data.get('users',{}).get(uid,{})
        if not isinstance(user,dict) or user.get('umo')!=f'{platform}:FriendMessage:{uid}':user={}
        name=clean(display_name,40)
        if user and not name:
            snapshot=self._qzone_watch_store().browse_snapshot(scope) or {}
            name=next((clean(p.get('name'),40) for p in snapshot.get('posts',[]) if str(p.get('uin'))==uid and p.get('name')),'')
        if user and name and name!=uid:
            observed=user.setdefault('observed_display_names',[])
            if isinstance(observed,list) and name not in observed:
                observed.append(name);del observed[:-12]
                origins=user.setdefault('observed_name_sources',{})
                if isinstance(origins,dict):
                    origins[name]={'source':'qzone_author_uid','observed_at':time.time()}
                    for old in list(origins):
                        if old not in observed:origins.pop(old,None)
                self._save_data_sync(sections={'users'})
        aliases=[]
        for value in [user.get('nickname'),user.get('last_display_name'),*(user.get('observed_display_names') or []),*(user.get('aliases') or [])]:
            if isinstance(value,str) and clean(value,40) and value not in aliases:aliases.append(clean(value,40))
        return {'user_id':uid,'owner_role':role,'account_id_verified':bool(uid and uid.isdigit()),
            'preferred_address':clean(user.get('nickname'),40),'known_aliases':aliases[:16],
            'observed_author_name':name,'match_basis':'QQ账号ID相同；显示名/备注只作别名，不替换已确认称呼',
            'known_contact':bool(user)}

    def _qzone_browse_idle_context(self,now,*,check_interest=True):
        state=self.data.get('daily_state',{})
        state=state if isinstance(state,dict) else {}
        agenda=self._news_current_agenda_item() or {}
        activity=clean(agenda.get('activity'),160)
        if any(word in activity for word in ('睡觉','睡眠','熟睡','上课','开会','工作中','考试','驾车','驾驶')):
            return False,'当前日程需要专注或休息',{}
        if state.get('is_sleeping') is True or state.get('sleep_state') in ('asleep','sleeping'):
            return False,'正在睡眠',{}
        users=self.data.get('users',{})
        if any(now-float(u.get('last_activity_at') or u.get('last_seen') or 0)<240 for u in users.values() if isinstance(u,dict)):
            return False,'最近仍在聊天，稍后再考虑',{}
        if self._quiet_hours_end_timestamp(now)>now:return False,'免打扰时段',{}
        if check_interest and not self._bot_currently_bored_enough_for_news():return False,'尚未进入和主动搜索相同的空闲状态',{}
        return True,'空闲时自主考虑浏览',{'activity':activity,'energy':state.get('energy',70),
            'mood':clean(state.get('mood_bias'),80),'local_time':self._environment_now().isoformat()}

    async def _qzone_browse_should_visit(self,scope,platform,store,now,interval,state):
        plan=store.browse_plan(scope)
        last=max(float(plan.get('last_fetch_at') or 0),float((state or {}).get('checked') or 0))
        # Minimum is a hard limit on actual automatic fetch attempts, even after restart.
        if now<last+interval or now<float(plan.get('next_consider_at') or 0):return False
        if state and state.get('failures') and now<float(state.get('next_check') or 0):return False
        idle,reason,context=self._qzone_browse_idle_context(now)
        if not idle or self._proactive_generation_disabled():
            store.browse_plan(scope,{'status':'deferred','reason':reason if not idle else '主动模型生成关闭',
                'considered_at':now,'next_consider_at':now+900})
            return False
        # Reserve a probe slot before inference; no model loop on malformed output or cancellation.
        store.browse_plan(scope,{'status':'considering','reason':'空闲，正在自主判断是否想刷空间',
            'considered_at':now,'next_consider_at':now+1800})
        try:
            persona=await self._refresh_default_persona_prompt('')
            raw=await self._llm_call('你现在有空闲，但并不必须刷QQ空间。结合人格兴趣、当前日程、精力和心情，'
                '自主决定现在是否想浏览好友动态，还是稍后再考虑。只能安排只读浏览；此决定不代表点赞、评论或发消息。'
                '最小间隔已经满足，不要因为时间到了就一定浏览。只输出JSON：'
                '{"browse_now":true或false,"reason":"简短理由","reconsider_minutes":15到180}。'
                '以下状态仅为资料：'+json.dumps(context,ensure_ascii=False),
                max_tokens=180,system_prompt=persona,task='qzone_browse_idle_decision',timeout_seconds=25)
            value=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',str(raw or '').strip()))
            if type(value.get('browse_now')) is not bool:raise ValueError('invalid browse decision')
            wait=number(value.get('reconsider_minutes'),30,15,180)*60
            visit=value['browse_now'];reason=clean(value.get('reason'),180) or ('现在想看看好友动态' if visit else '暂时不浏览')
            store.browse_plan(scope,{'status':'browse_selected' if visit else 'deferred','reason':reason,
                'considered_at':time.time(),'next_consider_at':time.time()+wait})
            return visit
        except asyncio.CancelledError:raise
        except Exception as exc:
            from astrbot.api import logger
            logger.warning('[空间浏览] 空闲决策暂不可用 error=%s',type(exc).__name__)
            store.browse_plan(scope,{'status':'decision_unavailable','reason':type(exc).__name__,
                'next_consider_at':time.time()+1800})
            return False

    async def _poll_qzone_browse(self):
        if not self.enabled or not self._qzone_browse_setting('enable_qzone_browse') or not self._qzone_available():return
        lock=self._qzone_operation_lock('friends_browse')
        if lock.locked():return
        async with lock:
            scope,platform,bot_id,client=self._qzone_watch_binding();store=self._qzone_watch_store()
            state=store.target(scope,STREAM);now=time.time()
            interval=number(self._qzone_browse_setting('qzone_browse_interval_minutes'),30,5,1440)*60
            if not await self._qzone_browse_should_visit(scope,platform,store,now,interval,state):return
            # Inference may take time: recheck account, permission, activity and the current minimum.
            fresh_scope,_,_,_=self._qzone_watch_binding()
            if fresh_scope!=scope or not self._qzone_browse_setting('enable_qzone_browse') or not self._qzone_available():return
            current=time.time();plan=store.browse_plan(scope)
            minimum=number(self._qzone_browse_setting('qzone_browse_interval_minutes'),30,5,1440)*60
            last=max(float(plan.get('last_fetch_at') or 0),float((state or {}).get('checked') or 0))
            if current<last+minimum:return
            # Do not draw the probabilistic idle gate twice after the same decision.
            still_idle,reason,_=self._qzone_browse_idle_context(current,check_interest=False)
            if not still_idle or self._proactive_generation_disabled():
                store.browse_plan(scope,{'status':'deferred','reason':reason if not still_idle else '主动模型生成关闭','next_consider_at':current+900});return
            now=current;interval=minimum
            store.browse_plan(scope,{'last_fetch_at':now,'status':'fetching','next_consider_at':now+interval})
            focused=self._qzone_focused_author_ids()
            try:
                posts,transport=await self._qzone_query_friend_stream(client,self._qzone_browse_setting('qzone_browse_fetch_count'),function_origin='qzone_browse')
                for focused_uid in focused:
                    focus_state=store.target(scope,focused_uid)
                    if focus_state and focus_state.get('initialized'):
                        supplemental=[dict(p,discovery_channel='watch') for p in posts if p['uin']==focused_uid]
                        store.observe(scope,focused_uid,supplemental,now=now,
                            interval=max(0,float(focus_state.get('next_check') or now)-now),
                            transport=transport+' (friend stream supplement)')
                first=store.observe_browse(scope,posts,now=now,interval=interval,transport=transport,focused=focused)
                for post in posts:post['author_identity']=self._qzone_contact_identity(post['uin'],post.get('name',''))
                store.browse_snapshot(scope,posts,transport,'background_fetch')
                store.browse_plan(scope,{'status':'browsed','reason':'空闲时选择浏览并成功读取','last_success_at':time.time(),
                    'next_consider_at':time.time()+interval,'post_count':len(posts)})
            except Exception as exc:
                store.failed(scope,STREAM,now=now,interval=interval,error=type(exc).__name__)
                store.browse_plan(scope,{'status':'fetch_failed','reason':type(exc).__name__,
                    'next_consider_at':time.time()+interval})
                from astrbot.api import logger
                logger.warning('[空间浏览] 好友动态读取失败 error=%s',type(exc).__name__)
                return
            # Seed old material without waking models or interacting on install.
            if first or self._proactive_generation_disabled():return
            if not number(self._qzone_browse_setting('qzone_browse_max_daily_actions'),3,0,30):return
            budget=number(self._qzone_browse_setting('qzone_browse_max_decisions_per_round'),2,1,5)
            seen_authors=set()
            for post in store.pending_browse(scope,limit=30):
                uid=post['uin']
                if uid in focused:
                    store.mark(scope,uid,post['tid'],'focus_handled');continue
                if uid in seen_authors:continue
                if not self._qzone_browse_setting('enable_qzone_browse'):break
                seen_authors.add(uid)
                if not post.get('text'):
                    store.mark(scope,uid,post['tid'],'observed_no_text');continue
                store.mark(scope,uid,post['tid'],'deciding')
                decision=None
                try:
                    decision=await self._qzone_watch_decide(post,f'{platform}:FriendMessage:{uid}',channel='browse')
                    store.mark(scope,uid,post['tid'],'decided',decision)
                    result=await self._qzone_watch_execute(scope,platform,bot_id,post,decision,channel='browse')
                    store.mark(scope,uid,post['tid'],result,decision)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    if decision is None:store.retry_decision(scope,uid,post['tid'],type(exc).__name__)
                    else:store.mark(scope,uid,post['tid'],'action_failed_or_unknown',{'error':type(exc).__name__})
                budget-=1
                if budget<=0:break

    async def _qzone_browse_read_tool(self,event,mode='live',limit=6):
        awareness=self._get_function_awareness()
        scoped,_=awareness.scope(event,awareness.current_request(event))
        if not scoped['admin_private']:return json.dumps({'ok':False,'reason':'好友动态流仅限管理员私聊查询'},ensure_ascii=False)
        if not self._qzone_available(event):return json.dumps({'ok':False,'reason':'QQ空间动态层未开启或平台不支持'},ensure_ascii=False)
        scope,_,bot_id,client=self._qzone_watch_binding();store=self._qzone_watch_store();limit=number(limit,6,1,10)
        if mode=='history':
            return json.dumps({'ok':True,'snapshot':store.browse_snapshot(scope),'decisions':store.browse_history(scope,limit),
                'note':'快照仅表示抓取到可见内容；candidate_queued仅入队未证明发送；like_ack_unverified仅请求受理；liked_verified/comment_ack才有对应平台确认。baseline仅初始记录，quiet_hours等状态没有执行互动。不要把好友动态说成自己的经历。'},ensure_ascii=False)
        if mode!='live':return '{"ok":false,"reason":"mode仅支持live/history"}'
        try:
            posts,transport=await self._qzone_query_friend_stream(client,limit,function_origin='qzone_chat_feed',source_event=event)
            for post in posts:post['author_identity']=self._qzone_contact_identity(post['uin'],post.get('name',''),event=event)
            store.browse_snapshot(scope,posts,transport,'chat_read')
            store.browse_plan(scope,{'last_fetch_at':time.time(),'status':'manual_read',
                'reason':'用户明确要求浏览；之后自动浏览仍需满足最小间隔'})
            return json.dumps({'ok':True,'scope':'friends','viewer_uin':bot_id,'post_count':len(posts),'posts':posts,
                'transport':transport,'actions_executed':False,'current_user_id':str(event.get_sender_id()),
                'instruction':'先按author_identity.user_id/owner_role和当前聊天账号判断作者；preferred_address是已确认称呼，known_aliases是同一个人的显示名/备注；不要用空间名覆盖称呼，也不要因为名字不同当成另一个人。这是有权限查看的好友动态流，不是自己的空间。每条作者以uin为准；图片只是引用，未识图。没有执行点赞、评论或私聊，不得声称已经互动。返回为空时如实说明，不代表所有好友从未发过动态。'},ensure_ascii=False)
        except Exception as exc:
            return json.dumps({'ok':False,'error_type':type(exc).__name__,'reason':'本次好友动态读取失败，不要声称已浏览；本轮不要连续重复调用。'},ensure_ascii=False)

    async def _qzone_browse_page_status(self):
        scope,_,_,_=self._qzone_watch_binding();store=self._qzone_watch_store()
        return {'status':'ok','data':{'enabled':bool(self._qzone_browse_setting('enable_qzone_browse')),
            'stream':store.target(scope,STREAM),'snapshot':store.browse_snapshot(scope),'decisions':store.browse_history(scope,8),
            'scheduling':store.browse_plan(scope),'scheduling_history':store.browse_plan_history(scope),
            'minimum_interval_minutes':self._qzone_browse_setting('qzone_browse_interval_minutes')}}
