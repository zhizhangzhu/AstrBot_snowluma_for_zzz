"""All panels project execution status, never infer completion from clock time."""
from __future__ import annotations

import time

from .catalog import inventory, setting
from .runtime import service


WAITING_REASON = 'QQ身份尚未就绪或有多个未明确绑定的账号，自主日程等待；被动回复保留'


def status(host, *, include_inventory=True):
    runtime=service(host)
    try:scope=runtime.scope()
    except ValueError:return {'enabled':True,'available':False,'activities':[],'receipts':[],
        'reason':WAITING_REASON}
    day=runtime.day()
    result={'enabled':True,'day':day,'scope':runtime.binding(),
        'halted':runtime.ledger.get(scope,'halt:'+day),
        'tokens':runtime.ledger.get(scope,'tokens:'+day,{'used':0}),
        'extra_hard_limit':int(setting(host,'real_schedule_daily_token_budget',0) or 0),
        'activities':runtime.ledger.list(scope,day=day),
        'receipts':runtime.experiences(limit=40),
        'receipt_selection':'meaningful_experiences' if setting(host,'enable_experience_memory_selection',True) else 'raw_audit',
        'continuity_summary':{
            'commitment_followthrough_enabled':bool(setting(host,'enable_commitment_followthrough',True)),
            'adaptive_feedback_enabled':bool(setting(host,'enable_adaptive_proactive_feedback',True)),
            'owners':[{'user_id':str(uid),
                'pending_commitments':sum(1 for l in u.get('open_loops',[]) if isinstance(l,dict)
                    and l.get('commitment_kind') and l.get('status') not in ('已完成','已取消')),
                'archived_commitments':len(u.get('companion_growth',{}).get('commitment_history',[])),
                'reviewed_feedback':len(u.get('companion_growth',{}).get('adaptive_feedback',{}).get('events',[]))}
                for uid,u in host.data.get('users',{}).items() if isinstance(u,dict) and u.get('relationship_role')=='owner']},
        'day_plan':runtime.ledger.get(scope,'day-plan:'+day),
        'day_plan_error':runtime.ledger.get(scope,'day-plan-error:'+day,''),
        'last_plan_at':runtime.ledger.get(scope,'last_plan_at',0),
        'last_error':runtime.ledger.get(scope,'last_error',''),
        'legacy_simulation_archived':runtime.ledger.get(scope,'legacy_simulation') is not None,
        'passive_reply_preserved':True,'updated_at':time.time()}
    if include_inventory:result['inventory']=inventory(host,runtime.schema())
    return result


def timeline(host):
    runtime=service(host)
    try:scope=runtime.scope()
    except ValueError:
        return {'real_mode':True,'available':False,'reason':WAITING_REASON,
            'plan_date':runtime.day(),'story_date':runtime.day(),'detail_day':runtime.day(),
            'segment_count':0,'segments':[],'story_today_events':[],
            'story_proactive_events':[],'adjustments':[],'qq_presence_state':{},
            'plan_quality':{'score':0,'level':'waiting','issues':[WAITING_REASON]}}
    rows=runtime.ledger.list(scope,day=runtime.day())
    names={'planned':'计划中','queued':'待执行','running':'执行中','paused':'已暂停','deferred':'已延期',
           'succeeded':'已完成','skipped':'本轮跳过','failed':'失败','cancelled':'已取消','uncertain':'结果待核实'}
    segments=[]
    for row in sorted(rows,key=lambda r:r['earliest']):
        start=host._environment_fromtimestamp(row['earliest'])
        receipts=runtime.ledger.receipts(runtime.scope(),activity_id=row['id'])
        segments.append({'key':row['id'],'real_mode':True,'window':start.strftime('%H:%M')+' · '+names[row['state']],
            'start':start.hour*60+start.minute,'end':start.hour*60+start.minute,
            'lifecycle':row['state'],'evidence_lifecycle':row['state'],'clock_status':'not_execution_evidence',
            'execution_status':row['state'],'activity':row['title'],'summary':row['note'],
            'status':row['state'],'basis':['execution_receipt'] if receipts else ['plan_only'],
            'confidence':1 if receipts else 0,'quality':{},'state_variables':[],
            'today_events':[{'text':r['body']['summary'],'window':host._environment_fromtimestamp(r['created']).strftime('%H:%M'),
                'lifecycle_status':'completed','evidence_kind':r['body']['evidence_kind'],
                'fact_eligibility':'history_observed','status':'completed','basis':['execution_receipt'],
                'confidence':1,'lifecycle':'completed','receipt_id':r['id']} for r in reversed(receipts)],
            'proactive_events':[],'presence_status':{}})
    return {'real_mode':True,'plan_date':runtime.day(),'story_date':runtime.day(),'detail_day':runtime.day(),
        'segment_count':len(segments),'segments':segments,'story_today_events':[],
        'story_proactive_events':[],'adjustments':[],'qq_presence_state':host.data.get('qq_presence_state',{}),
        'plan_quality':{'score':100,'level':'execution_based','issues':[]}}


def observation(host, data):
    runtime=service(host)
    diaries=[d for d in data.get('bot_diaries',[]) if isinstance(d,dict) and d.get('real_mode')] if isinstance(data.get('bot_diaries'),list) else []
    try:scope=runtime.scope()
    except ValueError:
        return {'real_mode':True,'available':False,'reason':WAITING_REASON,
            'dream':{},'dream_fragments':[],'diaries':diaries,'current_plan':{},
            'story':{'date':runtime.day(),'today_events':[]},
            'legacy_note':'QQ连接恢复前不读取其他账号或人格的活动记录。'}
    receipts=runtime.experiences(limit=40)
    return {'real_mode':True,'dream':dict(data.get('daily_dream',{}),fictional=True) if setting(host,'enable_enhanced_dreams',False) else {},'dream_fragments':data.get('dream_fragments',[]) if setting(host,'enable_enhanced_dreams',False) else [],
        'diaries':diaries,'current_plan':data.get('daily_plan',{}),
        'story':{'date':runtime.day(),'today_events':[{'text':r['body']['summary'],
            'window':host._environment_fromtimestamp(r['created']).strftime('%H:%M'),
            'status':'completed','lifecycle_status':'completed','evidence_kind':r['body']['evidence_kind'],
            'receipt_id':r['id']} for r in reversed(receipts)]},
        'legacy_note':'旧模拟日程、梦境和技能经验已独立归档，未转换成真实执行历史。'}
