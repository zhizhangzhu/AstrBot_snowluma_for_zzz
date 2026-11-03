"""Scoped host integration. No global patches to AstrBot or third-party plugins."""
from __future__ import annotations

import asyncio
from functools import wraps
import inspect
import json
import time

from .catalog import METHODS, setting
from .runtime import EXECUTION, enabled, selected, service


def section(host):
    from ..conversation_prompt_section import prompt_section
    return prompt_section(key='real_schedule.execution_facts',title='真实活动与未执行计划',source='real_schedule',content=service(host).context())


SYNC_SECTIONS = {'_format_state_prompt_section','_format_detail_injection_prompt_section',
    '_format_companion_scene_snapshot_prompt_section','_format_self_timeline_context_for_reply_section',
    '_format_state_continuity_prompt_section','_format_passive_state_style_prompt_section'}
SYNC_TEXT = {'_format_current_detail_view','_format_current_detail_brief','_format_state_for_message'}
PLAN_READERS = {'_plan_item_runtime_status','_plan_item_display_status','_get_current_plan_item','_format_plan_for_diary'}
PLAN_READERS.add('_qzone_life_publish_daily_plan')
SPECIAL = {'_ensure_daily_state','_ensure_daily_plan','_ensure_detail_enhancement',
           '_ensure_current_detail_presence_status','_generate_daily_diary','_maybe_settle_skill_growth',
           '_maybe_settle_personal_goals','_tick','_llm_call','_call_onebot_action_with_error'}
SPECIAL |= {'_bot_currently_bored_enough_for_news','_bot_currently_bored_enough_for_bilibili',
            '_bot_currently_idle_for_creative_writing','_qzone_browse_should_visit','_publish_qzone_text',
            '_run_agenda_maintenance_tick','_read_news_candidates','_persist_final_outbound_delivery'}
SPECIAL |= {'_send_proactive_message_chain','_send_media_proactive_chain',
            '_send_segmented_proactive_forward_message','_execute_proactive_action'}
SPECIAL |= {'_qzone_request','_qzone_like_post','_qzone_comment_post','_generate_photo_image'}
SPECIAL |= {'_generate_daily_plan','_generate_detail_enhancement',
            '_cancel_daily_plan_segment_by_selector','_regenerate_daily_plan_segment_by_selector'}
SPECIAL.add('_refresh_dialogue_episode_batch')
SPECIAL.update({'_choose_web_exploration_query','_run_astrbot_web_search'})


def install(cls):
    """Wrap only documented host boundaries; disabled mode delegates unchanged."""
    names=set(METHODS)|SPECIAL|SYNC_SECTIONS|SYNC_TEXT|PLAN_READERS|{'_scheduler_maintenance_tasks','_record_llm_usage','_proactive_generation_disabled'}
    for name in names:
        original=getattr(cls,name,None)
        if not callable(original) or getattr(original,'_real_schedule_hook',False):continue
        wrapper=async_hook(name,original) if inspect.iscoroutinefunction(original) else sync_hook(name,original)
        wrapper._real_schedule_hook=True
        wrapper._real_schedule_original=original
        setattr(cls,name,wrapper)
    return cls


def sync_hook(name, original):
    @wraps(original)
    def wrapped(host,*args,**kwargs):
        if name=='_proactive_generation_disabled':
            from ..portability.service import paused
            if paused(host):return True
        from ..life_features import active, configured
        if configured(host) and not active(host) and name in SYNC_SECTIONS|SYNC_TEXT:
            return None if name in SYNC_SECTIONS else ''
        if name=='_scheduler_maintenance_tasks' and configured(host) and active(host) and not enabled(host):
            from ..life_features import generate_dream
            return tuple(original(host,*args,**kwargs))+(('独立梦境创作',lambda:generate_dream(host)),)
        if not enabled(host):
            if name=='_qzone_life_publish_daily_plan':
                state=args[0] if args else kwargs.get('state',{})
                plan=state.get('life_publish_daily_plan') or {}
                if plan.get('real_mode'):
                    for item in plan.get('items',[]):
                        if item.get('status')=='planned':item.update(status='cancelled',failed_reason='real_schedule_disabled')
                    plan['config_signature']='real_schedule_disabled'
            return original(host,*args,**kwargs)
        runtime=service(host)
        if name=='_qzone_life_publish_daily_plan' and selected(host,'qzone.publish'):
            from .publishing import chosen_plan
            return chosen_plan(host,*args,**kwargs)
        # Native chat receives one scoped snapshot in on_agent_begin. Helper
        # model calls receive it in _llm_call; legacy state collectors must not
        # add a second, stale copy to the same system prompt.
        if name in SYNC_SECTIONS:return None
        if name in SYNC_TEXT:
            from ..life_features import roleplay_context
            return runtime.context()+roleplay_context(host)
        if name=='_format_plan_for_diary':return runtime.context(owner=True)
        if name=='_get_current_plan_item':
            plan=args[0] if args else kwargs.get('plan',{})
            return next((i for i in (plan or {}).get('items',[]) if i.get('execution_status')=='running'),None)
        if name in ('_plan_item_runtime_status','_plan_item_display_status'):
            item=args[1] if len(args)>1 else kwargs.get('item',{})
            return {'running':'active','succeeded':'completed','cancelled':'cancelled',
                'paused':'deferred','deferred':'deferred','failed':'unknown','uncertain':'unknown'}.get((item or {}).get('execution_status'),'planned')
        if name=='_proactive_generation_disabled':
            ctx=EXECUTION.get()
            if ctx and ctx['host'] is host and ctx['capability'] not in ('background','planner'):
                return runtime.halted()
            return runtime.halted() or original(host,*args,**kwargs)
        if name=='_bot_currently_bored_enough_for_news' and (selected(host,'search') or selected(host,'news')):return runtime.idle()
        if name=='_bot_currently_bored_enough_for_bilibili' and selected(host,'video'):return runtime.idle()
        if name=='_bot_currently_idle_for_creative_writing' and selected(host,'creative'):return runtime.idle()
        result=original(host,*args,**kwargs)
        if name=='_scheduler_maintenance_tasks':
            return tuple(result)+(('真实日程协调',runtime.tick),)
        if name=='_record_llm_usage' and selected(host):
            usage=host._extract_llm_usage(kwargs.get('resp'),kwargs.get('prompt',''),kwargs.get('completion',''))
            runtime.usage(usage.get('total_tokens',0))
        return result
    return wrapped


def async_hook(name, original):
    @wraps(original)
    async def wrapped(host,*args,**kwargs):
        from ..portability.service import paused
        if paused(host) and (name in METHODS or name in ('_tick','_run_agenda_maintenance_tick','_send_proactive_message_chain','_send_media_proactive_chain','_send_segmented_proactive_forward_message','_execute_proactive_action')):
            if not kwargs.get('force',False):
                if name in ('_send_proactive_message_chain','_send_media_proactive_chain'):
                    from ..proactive_message import _ProactiveSendOutcome
                    return _ProactiveSendOutcome(False,False,note='迁移检查中，自动发送暂停')
                if name=='_send_segmented_proactive_forward_message':return False
                if name=='_execute_proactive_action':return {'success':False,'context':'迁移后等待检查','extra_components':[],'summary':'自动行为暂停','effective_action':''}
                return None
        from ..life_features import active, configured
        unified=configured(host)
        if unified and not active(host) and name in set(METHODS)|{'_ensure_daily_state','_ensure_detail_enhancement'}:
            return {} if name in ('_ensure_daily_state','_ensure_daily_plan') else None
        if unified and name in ('_ensure_daily_plan','_generate_daily_plan','_generate_detail_enhancement','_ensure_detail_enhancement') and not enabled(host):
            old=getattr(host,'_real_schedule_runtime',None)
            if old is not None:
                try:
                    scope=old.scope()
                    task=old.tasks.get(scope)
                    running=next((r for r in old.ledger.list(scope) if r['state']=='running'),None)
                    if task is not None and not task.done() and (not running or running['effect']=='read'):task.cancel()
                    for row in old.ledger.list(scope,day=old.day()):
                        old.ledger.reschedule(scope,row['id'],earliest=time.time(),note='自主日程已关闭',cancel=True)
                except ValueError:pass
            host.data['daily_plan']={'date':host._environment_now().strftime('%Y-%m-%d'),'real_mode':True,'source':'autonomy_disabled','items':[]}
            return host.data['daily_plan'] if name=='_ensure_daily_plan' else {}
        if not enabled(host):
            if name=='_ensure_daily_plan':
                previous=getattr(host,'_real_schedule_runtime',None)
                if previous is not None and not unified:previous.restore_simulation()
            if selected(host) and name=='_llm_call':return None
            if selected(host) and name=='_call_onebot_action_with_error':return False,'真实日程已关闭，未继续提交自主动作'
            if selected(host):
                if name=='_qzone_request':
                    raise RuntimeError('真实日程已关闭，未继续提交空间请求')
                if name=='_publish_qzone_text':
                    return {'success':False,'message':'真实日程已关闭，未发布'}
                if name=='_generate_photo_image':
                    return '真实日程','','真实日程已关闭，未提交生图'
                if name=='_execute_proactive_action':
                    return {'success':False,'context':'真实日程已关闭','extra_components':[],
                            'summary':'原自主活动已停止','effective_action':''}
                if name in ('_send_proactive_message_chain','_send_media_proactive_chain'):
                    from ..proactive_message import _ProactiveSendOutcome
                    return _ProactiveSendOutcome(False,False,note='真实日程已关闭，未继续主动发送')
                if name=='_send_segmented_proactive_forward_message':return False
            return await original(host,*args,**kwargs)
        runtime=service(host)
        if name=='_tick':
            state=host.data.setdefault('proactive_runtime',{})
            try:
                runtime.scope()
            except ValueError:
                state.update(awaiting_qq_identity=True,last_tick_finished_at=time.time())
                return
            state['awaiting_qq_identity']=False
        if name=='_refresh_dialogue_episode_batch':
            uid=str(args[0] if args else kwargs.get('user_id',''))
            before=float(host.data.get('users',{}).get(uid,{}).get('last_episode_refresh_at') or 0)
            result=await original(host,*args,**kwargs)
            current=host.data.get('users',{}).get(uid,{})
            stamp=float(current.get('last_episode_refresh_at') or 0)
            episodes=current.get('dialogue_episodes') or []
            if stamp>before and episodes and not current.get('dialogue_episode_last_error'):
                from ..companion_continuity import export_shared
                try:
                    export_shared(host,current,stamp,episode=episodes[-1])
                except Exception as exc:
                    from ..logging_util import get_module_logger
                    get_module_logger(__name__).warning('对话摘要经历索引暂未同步，原始记忆仍保留: %s',type(exc).__name__)
            return result
        if name in ('_cancel_daily_plan_segment_by_selector','_regenerate_daily_plan_segment_by_selector'):
            selector=args[0] if args else kwargs.get('selector','')
            result=await manage_activity(host,runtime,selector,regenerate=name.startswith('_regenerate'))
            return (*result,{}) if name.startswith('_regenerate') else result
        if name=='_choose_web_exploration_query' and selected(host,'search'):
            from .interests import activity
            row=activity(runtime)
            if row and row['payload'].get('question'):
                return {'query':row['payload']['question'],'topic':'general','reason':row['payload'].get('reason',''),'planned_topic':row['payload'].get('topic','')}
        if name=='_run_astrbot_web_search' and selected(host,'search'):
            from .interests import enrich_search
            return await enrich_search(runtime,await original(host,*args,**kwargs))
        if name=='_generate_detail_enhancement':return {}
        if name=='_generate_daily_plan':
            runtime.ledger.put(runtime.scope(),'last_plan_at',0)
            await runtime.tick();runtime.project()
            return host.data.get('daily_plan',{})
        if name=='_qzone_request' and selected(host):
            if not runtime.execution_allowed():raise RuntimeError('真实日程已停止，未提交空间请求')
            method=str(args[1] if len(args)>1 else kwargs.get('method','GET')).upper()
            ctx=EXECUTION.get()
            if method not in ('GET','HEAD','OPTIONS') and ctx.get('activity_id'):
                runtime.ledger.mark_external_write(ctx['scope'],ctx['activity_id'])
            return await original(host,*args,**kwargs)
        if name=='_generate_photo_image' and selected(host) and not runtime.execution_allowed():
            return '真实日程','','当日Token硬上限或活动已停止，未提交生图'
        if name in ('_qzone_like_post','_qzone_comment_post'):
            result=await original(host,*args,**kwargs)
            acknowledged=(isinstance(result,dict) and result.get('success') is True) if name=='_qzone_like_post' else bool(isinstance(result,str) and result)
            if acknowledged:
                verified=isinstance(result,dict) and result.get('verified') is True
                runtime.record('qzone-action:'+name+':'+str(time.time_ns()),{'verified':True,
                    'evidence_kind':'qzone_interaction_verified' if verified else 'qzone_interaction_ack',
                    'summary':'QQ空间点赞'+('已核实' if verified else '收到服务端回执，显示效果未独立核实') if name=='_qzone_like_post' else 'QQ空间评论收到服务端回执',
                    'skills':[]})
            return result
        if name=='_persist_final_outbound_delivery':
            result=await original(host,*args,**kwargs)
            event=args[0] if args else kwargs.get('event')
            if result is True and event is not None:
                delivery=str(getattr(event,'_private_companion_delivery_id','') or host._event_message_id(event))
                if delivery:
                    runtime.record('chat-delivery:'+event.unified_msg_origin+':'+delivery,{
                        'verified':True,'evidence_kind':'confirmed_chat_reply','summary':'完成一次实际聊天回复',
                        'session_id':event.unified_msg_origin,'delivery_id':delivery,'skills':['聊天表达'],
                        'user_id':str(getattr(event,'get_sender_id',lambda:'')()),
                        'participant_name':host.data.get('users',{}).get(str(getattr(event,'get_sender_id',lambda:'')()),{}).get('nickname',''),
                        'note':'具体聊天内容使用原对话记忆检索，不在日程里复制其他人的私聊正文'})
            return result
        if name=='_run_agenda_maintenance_tick':
            flush=getattr(host,'_memory_companion_flush_bot_personal_outbox',None)
            if callable(flush):await flush(limit=24)
            return []
        if name=='_read_news_candidates':
            result=await original(host,*args,**kwargs)
            for item in result if isinstance(result,list) else []:
                if not isinstance(item,dict):continue
                text=str(item.get('article_text') or item.get('video_subtitle_text') or '')
                if len(text)<200:continue
                import hashlib
                key=hashlib.sha256((str(item.get('link'))+text).encode()).hexdigest()
                runtime.record('acquired-text:'+key,{'verified':True,'evidence_kind':'article_text_acquired',
                    'partial':True,
                    'summary':'已获取正文：《'+str(item.get('title') or '')[:160]+'》',
                    'source_url':str(item.get('link') or ''),'evidence':text[:2500],
                    'note':'已获取内容，不代表已读完整理或独立核验','skills':[]})
            return result
        if name in ('_send_proactive_message_chain','_send_media_proactive_chain') and runtime.halted():
            from ..proactive_message import _ProactiveSendOutcome
            return _ProactiveSendOutcome(False,False,note='当日Token硬上限，已取消主动发送；被动回复保留')
        if name=='_send_segmented_proactive_forward_message' and runtime.halted():return False
        if name=='_execute_proactive_action' and runtime.halted():
            return {'success':False,'context':'当日Token硬上限','extra_components':[],'summary':'自主活动已停止','effective_action':''}
        if name=='_qzone_browse_should_visit' and selected(host,'qzone.browse'):
            return runtime.idle() and not runtime.halted()
        if name=='_publish_qzone_text':
            auto_reason=str(kwargs.get('publish_reason') or 'manual_publish')
            if (selected(host) and not runtime.execution_allowed()) or (auto_reason!='manual_publish' and runtime.halted()):
                return {'success':False,'message':'真实日程已停止，未发布'}
            result=await original(host,*args,**kwargs)
            if isinstance(result,dict) and result.get('success') is True:
                runtime.record('qzone.publish:'+str(result.get('tid') or result.get('post_id') or time.time_ns()),{
                    'verified':True,'evidence_kind':'qzone_publish_ack','summary':'成功发表QQ空间动态',
                    'post_id':str(result.get('tid') or result.get('post_id') or ''),'skills':[]})
            return result
        if name=='_llm_call':
            if selected(host) and not runtime.execution_allowed():return None
            # All helper generations see the same reality boundary. Native
            # foreground replies receive it at the final agent context hook.
            from .interests import context as interest_context
            from ..life_features import roleplay_context
            kwargs['system_prompt']=str(kwargs.get('system_prompt') or '')+'\n'+runtime.context(owner=selected(host))+interest_context(runtime)+roleplay_context(host)
            result=await original(host,*args,**kwargs)
            if selected(host):runtime.halted()
            return result
        if name=='_call_onebot_action_with_error':
            ctx=EXECUTION.get()
            if ctx and ctx['host'] is host:
                if not runtime.execution_allowed():
                    return False,'真实日程已停止或作用域改变，未提交动作'
                action=str(args[1] if len(args)>1 else kwargs.get('action',''))
                if ctx.get('activity_id') and not action.startswith(('get_','can_')):
                    runtime.ledger.mark_external_write(ctx['scope'],ctx['activity_id'])
            result=await original(host,*args,**kwargs)
            action=str(args[1] if len(args)>1 else kwargs.get('action',''))
            if isinstance(result,tuple) and result[0] is True and action and not action.startswith(('get_','can_')):
                error=str(result[1] or '').lower()
                if not any(t in error for t in ('uncertain','timeout','不确定','超时')):
                    runtime.record('onebot:'+action+':'+str(time.time_ns()),{
                        'verified':True,'evidence_kind':'qq_action_ack','summary':'QQ动作收到成功回执：'+action,
                        'source':action,'skills':[]})
            return result
        if name=='_tick':runtime.archive_simulation()
        if name=='_tick' and runtime.halted():
            data=host.data.setdefault('proactive_runtime',{})
            data.update(generation_disabled=True,generation_disabled_reason='real_schedule_token_hard_limit',last_tick_finished_at=time.time())
            runtime.project();return
        if name=='_tick':
            task=runtime.tasks.get(runtime.scope())
            active=(task is not None and not task.done()) or any(r['state']=='running' for r in runtime.ledger.list(runtime.scope(),day=runtime.day()))
            if active:
                # Keep detection reliable; leave decisions pending until the
                # current foreground activity yields or finishes.
                await host._poll_qzone_watch(baseline_only=True)
                await runtime.tick()
                return
        if name=='_ensure_daily_state':
            runtime.archive_simulation()
            if selected(host,'roleplay.state'):
                return await original(host,*args,**kwargs)
            if setting(host,'enable_humanized_states',False) and host.data.get('state_generated_day')==runtime.day():
                return await original(host,passive_fast=True,skip_conversation_summary=True)
            return host.data.get('daily_state',{})
        if name=='_ensure_daily_plan':
            runtime.archive_simulation()
            if kwargs.get('force'):
                runtime.ledger.put(runtime.scope(),'last_plan_at',0)
                await runtime.tick()
            runtime.project();return host.data.get('daily_plan',{})
        if name=='_ensure_detail_enhancement':
            runtime.project();return None
        if name=='_ensure_current_detail_presence_status':
            await presence(host,runtime);return
        if name=='_maybe_settle_skill_growth':return await runtime.settle_skills()
        if name=='_maybe_settle_personal_goals':return await runtime.settle_goals()
        if name=='_generate_daily_diary':return await generate_diary(host,runtime)
        cap=METHODS.get(name)
        if cap and not selected(host,cap):
            # Explicit administrator/user operations still use their native
            # command entry; periodic checks only offer planning references.
            if kwargs.get('force') is True:
                return await original(host,*args,**kwargs)
            # Existing timers are reference triggers, not second executors.
            # Central planning refreshes the live inventory and chooses work.
            await runtime.tick()
            return None
        if name=='_tick':
            scope=runtime.scope();runtime.native_busy[scope]=runtime.native_busy.get(scope,0)+1
            try:return await original(host,*args,**kwargs)
            finally:
                runtime.native_busy[scope]-=1
                await runtime.tick()
        return await original(host,*args,**kwargs)
    return wrapped


async def manage_activity(host,runtime,selector,*,regenerate=False):
    if regenerate and runtime.halted():return False,'今日自主活动已因Token硬上限取消，次日重新规划'
    runtime.project()
    segment,error=host._resolve_daily_plan_segment_selector(selector,include_cancelled=True)
    if not segment:return False,error
    index=int(segment.get('index',-1))
    items=host.data.get('daily_plan',{}).get('items',[])
    if index<0 or index>=len(items):return False,'没有匹配到真实活动'
    activity_id=items[index].get('real_activity_id','')
    row=runtime.ledger.activity(runtime.scope(),activity_id)
    if not row:return False,'活动不存在'
    if row['state']=='running':
        if row['effect']!='read':return False,'活动已有写入操作，等待实际回执后再处理，避免重复执行'
        task=runtime.tasks.get(runtime.scope())
        if task is not None:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    changed=runtime.ledger.reschedule(runtime.scope(),activity_id,earliest=time.time(),
        note='主要用户取消当前意图'+('并请求重新考虑' if regenerate else ''),cancel=True)
    if not changed:return False,'此活动已结束，不会删除其真实执行记录'
    if regenerate:
        runtime.ledger.put(runtime.scope(),'last_plan_at',0);await runtime.tick()
    runtime.project()
    return True,'已取消原活动；将根据当前可用功能重新考虑安排' if regenerate else '已取消活动，保留已有的实际成果'


async def presence(host,runtime,*,force=False):
    basic=bool(setting(host,'enable_qq_presence_sync',True))
    custom=bool(setting(host,'enable_qq_custom_presence_sync',False))
    if not basic and not custom:return
    rows=runtime.ledger.list(runtime.scope(),day=runtime.day())
    running=next((r for r in rows if r['state']=='running'),None)
    labels={'news':'看新闻','search':'查资料','qzone.browse':'逛空间','reading':'阅读中',
            'video':'看视频','ai.daily':'看资讯','knowledge.read':'读知识库',
            'skill.study':'读技能说明','creative':'写作中','diary':'写日记',
            'memory.consolidate':'整理记忆'}
    label=labels.get(running['capability'],'整理中') if running else '空闲中'
    mode='custom' if custom else 'online'
    state=host.data.get('qq_presence_state') or {}
    age=time.time()-float(state.get('updated_at') or 0)
    if not force and state.get('real_mode') and state.get('custom_text')==label and state.get('mode')==mode:
        if state.get('ok') and age<600 or not state.get('ok') and age<3600:return
    if custom:
        ok,note=await host._set_qq_custom_presence(label)
    else:ok,note=await host._set_qq_online_presence('online')
    host.data['qq_presence_state']={'date':runtime.day(),'real_mode':True,'mode':mode,
        'custom_text':label,'reason':'同步实际运行活动','updated_at':time.time(),'ok':bool(ok),'note':str(note)[:120]}
    host._save_data_sync(sections={'qq_presence_state'})


async def generate_diary(host,runtime,*,day=None):
    if runtime.halted():raise asyncio.CancelledError()
    from .diary_sources import dated_receipts
    day = day or runtime.day()
    receipts=dated_receipts(host,runtime,day)
    users=[u for u in host.data.get('users',{}).values() if isinstance(u,dict) and u.get('relationship_role')=='owner']
    user=users[0] if users else {}
    persona=await host._resolve_proactive_persona_prompt(user,umo=user.get('umo',''))
    data=[{'id':r['id'],'time':host._environment_fromtimestamp(r['created']).isoformat(),**r['body']} for r in receipts]
    prompt='''根据真实活动回执写今天的日记，可以有符合人格的主观感想，但不能编造实体生活、未发生互动或未执行活动。
只输出JSON，结构{"summary":"短摘要","body":"日记正文","share_seed":"可为空","tags":[],"receipt_ids":["引用的实际回执id"]}。
没有活动就如实写没有留下活动记录；计划不能当作经历。收到聊天与阅读资料分别表述，不把资料内容当自身经历。'''
    from ..dreaming import _daily_diary_form_instruction, _daily_diary_length_instruction, _daily_diary_creativity_instruction
    form,form_note=_daily_diary_form_instruction(host,[{'level':'confirmed'} for _ in receipts])
    min_chars,max_chars=_daily_diary_length_instruction(host)
    creative_note=_daily_diary_creativity_instruction(host)
    direction=str(setting(host,'daily_diary_custom_direction','') or '')[:500]
    prompt+='\n日记日期：'+day+'。有聊天回执时，要用该回执中已确认的对方称呼写清和谁聊过，不要笼统写成某人或对方，更不能说没有聊天。回执只确认交谈就只写交谈，不补造内容。外部资料和聊天均不是自己的实体生活。'
    prompt+='\n写作形式：'+form_note+'\n篇幅：'+str(min_chars)+'—'+str(max_chars)+'字左右，材料不足可更短。\n'+creative_note
    if direction:prompt+='\n写作方向（不得改变实际来源与事实边界）：'+direction
    prompt+='\n正文使用自然的日记口吻，不写回执、来源字段、系统、实体活动记录等后台术语。没有提供聊天原文只意味着本次不展开细节，不能写成聊天内容没有保存、没有记录或没有资料。若只确认聊过，就自然写和谁聊过，不必解释后台材料的限制。'
    if not setting(host,'daily_diary_generate_share_seed',True):prompt+='\n本次不提取分享素材，share_seed必须留空。'
    raw=await host._llm_call(prompt+'\n回执：'+json.dumps(data,ensure_ascii=False),system_prompt=persona,
        task='real_schedule_diary',max_tokens=max(500,max_chars*3+300),timeout_seconds=75)
    value=host._extract_json_payload(raw or '')
    if not isinstance(value,dict) or not str(value.get('body') or '').strip():
        raise ValueError('真实日记生成未返回有效正文')
    allowed={r['id'] for r in receipts}
    refs=value.get('receipt_ids')
    if not isinstance(refs,list) or any(not isinstance(i,str) or i not in allowed for i in refs):
        raise ValueError('日记包含未确认的活动来源')
    if receipts and not refs:raise ValueError('日记未提供真实活动来源')
    body=str(value['body']).strip()
    # Link every named conversation to its actual evidence, even if the model
    # omitted a reference. No invented or cross-scope IDs are introduced.
    refs = list(dict.fromkeys(refs))
    for receipt in receipts:
        name = receipt['body'].get('participant_name')
        if name and name in body and receipt['id'] not in refs:
            refs.append(receipt['id'])
    value['receipt_ids'] = refs
    if len(body)>max_chars:
        import re
        head=body[:max_chars]
        cuts=[m.end() for m in re.finditer(r'[。！？!?\n]',head)]
        body=head[:cuts[-1]] if cuts and cuts[-1]>=max_chars//2 else head[:-1]+'…'
    value.update(body=body,form=form)
    if not setting(host,'daily_diary_generate_share_seed',True):value['share_seed']=''
    value.update(date=day,real_mode=True,evidence_kind='receipt_based_diary',
        generated_at=host._environment_fromtimestamp(time.time()).isoformat())
    return value


def observe_source(host,record):
    if not enabled(host) or not isinstance(record,dict):return
    runtime=service(host);binding=runtime.binding()
    if any(str(record.get(k,''))!=str(binding[k]) for k in ('bot_id','persona_id','platform_id')):return
    level=record.get('evidence_level')
    if level not in ('search_snippet','article_text','subtitle_text','qzone_text'):return
    descriptions={'search_snippet':'获取搜索摘要','article_text':'读取文章正文','subtitle_text':'读取视频字幕','qzone_text':'读取空间动态'}
    runtime.record('source:'+record['source_id'],{
        'verified':True,'evidence_kind':level,'summary':descriptions[level]+'：《'+str(record.get('title') or '')[:180]+'》',
        'source_id':record['source_id'],'function_origin':record.get('function_id',''),
        'origin_session_id':record.get('origin_session_id',''),
        'trigger_mode':record.get('trigger_mode',''),
        'requested_by_id':record.get('requested_by_id',''),
        'source_url':record.get('url',''),'evidence':str(record.get('evidence') or '')[:1800],
        'skills':['生活观察'] if level=='qzone_text' else ['资料阅读'],
        'external_claim_verified':False})


async def inject_request(host,event,req):
    if not enabled(host):return
    runtime=service(host)
    try:scope=runtime.scope()
    except ValueError:return
    task=runtime.tasks.get(scope)
    if task and not task.done():
        active=next((r for r in runtime.ledger.list(runtime.scope()) if r['state']=='running'),None)
        if active and active['effect']=='read':
            task.cancel()
            await asyncio.sleep(0)
    runtime.ledger.put(runtime.scope(),'last_inbound',{'at':time.time(),'umo':event.unified_msg_origin})


async def inject_agent(host,event,run_context):
    if not enabled(host):return
    try:service(host).scope()
    except ValueError:return
    from astrbot.core.agent.message import TextPart,SystemMessageSegment
    messages=getattr(run_context,'messages',None)
    if not isinstance(messages,list):return
    system=next((m for m in messages if getattr(m,'role','')=='system'),None)
    if system is None:
        system=SystemMessageSegment(content=[]);system._no_save=True;messages.insert(0,system)
    marker='[真实日程事实边界]'
    if isinstance(system.content,str):system.content=[TextPart(text=system.content)]
    system.content=[p for p in system.content if not (isinstance(p,TextPart) and p.text.startswith(marker))]
    from ..life_features import roleplay_context
    system.content.append(TextPart(text=marker+'\n'+service(host).context(owner=owner_event(host,event),umo=event.unified_msg_origin)+roleplay_context(host,for_reply=True)).mark_as_temp())


def owner_event(host,event):
    if not event.is_private_chat():return False
    uid=str(event.get_sender_id());user=host.data.get('users',{}).get(uid,{})
    admins=host.context.get_config().get('admins_id',[])
    if isinstance(admins,str):admins=[admins]
    guard=getattr(host,'_bot_scope_allows_event',None)
    if callable(guard) and not guard(event):return False
    return bool(uid in [str(v) for v in admins] and user.get('relationship_role')=='owner'
        and user.get('umo')==event.unified_msg_origin)


def begin_background(host):
    if not enabled(host):return None
    try:scope=service(host).scope()
    except ValueError:
        # Keep autonomous calls marked as background while waiting for QQ.
        # Otherwise helper generations can fall through as foreground work.
        return EXECUTION.set({'host':host,'scope':None,'capability':'background','activity_id':''})
    runtime=service(host);runtime.native_busy[scope]=runtime.native_busy.get(scope,0)+1
    return EXECUTION.set({'host':host,'scope':scope,'capability':'background','activity_id':''})


def end_background(token):
    if token is not None:
        ctx=EXECUTION.get();runtime=service(ctx['host']);scope=ctx['scope']
        if scope is not None:runtime.native_busy[scope]=max(0,runtime.native_busy.get(scope,1)-1)
        # Child task inherits this persona binding before the caller restores
        # the next one. Planning starts only after the native cycle yields.
        ctx['host']._create_lifecycle_background_task(runtime.tick(),label='real_schedule_after_native_cycle')
        EXECUTION.reset(token)


async def observe_tool(host,event,tool,result):
    if not enabled(host):return
    runtime=service(host)
    # Acquisition tools already save typed source records. Unknown tool output
    # is not interpreted as an execution acknowledgement by a language model.
    value=result
    if isinstance(value,str):
        try:value=json.loads(value)
        except (ValueError,TypeError):return
    if not isinstance(value,dict) or value.get('error') or value.get('queued') or value.get('pending'):return
    if not (value.get('ok') is True or value.get('success') is True):return
    name=str(getattr(tool,'name',''))
    if not name:return
    summary=str(value.get('message') or value.get('summary') or ('工具执行成功：'+name))[:500]
    runtime.record('tool:'+name+':'+str(event.unified_msg_origin)+':'+str(time.time_ns()),{
        'verified':True,'evidence_kind':'structured_tool_success','summary':summary,'tool_name':name,
        'session_id':event.unified_msg_origin,'skills':[]})
