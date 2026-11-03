"""A chosen publishing intent replaces the old random slot, not its quotas."""
from copy import deepcopy
import time

from .catalog import setting
from .runtime import EXECUTION,service


def chosen_plan(host,state,*,now=None):
    now=time.time() if now is None else now
    runtime=service(host);ctx=EXECUTION.get();day=runtime.day()
    old=state.get('life_publish_daily_plan') or {}
    if old.get('real_activity_id')==ctx.get('activity_id') and any(i.get('status')=='planned' for i in old.get('items',[])) and not old.get('skip_reason'):return old
    limit=max(1,int(setting(host,'qzone_life_publish_max_daily',1) or 1))
    history=[deepcopy(i) for i in old.get('items',[]) if isinstance(i,dict) and i.get('status')!='planned'] if old.get('date')==day else []
    count=int(old.get('published_count',0)) if old.get('date')==day else 0
    last=float(state.get('last_life_publish_at') or 0)
    gap=host._qzone_intra_day_gap_seconds() if last and host._environment_fromtimestamp(last).strftime('%Y-%m-%d')==day else host._qzone_cross_day_gap_seconds()
    plan={'date':day,'real_mode':True,'real_activity_id':ctx['activity_id'],
        'config_signature':host._qzone_life_publish_plan_signature(),'configured_limit':limit,
        'target_count':count+1,'published_count':count,'created_at':now,
        'used_schedule_keys':[],'items':history,'night_allowed':host._qzone_night_publish_allowed()}
    if count>=limit:plan['skip_reason']='daily_limit'
    elif last and now-last<gap:plan['skip_reason']='minimum_interval'
    elif runtime.halted():plan['skip_reason']='token_hard_limit'
    else:
        plan['items'].append({'id':'real:'+ctx['activity_id'],'planned_at':now,'status':'planned',
            'attempts':0,'length_profile':'medium','schedule_key':'','schedule_label':'',
            'night':host._qzone_slot_is_night(now)})
    state['life_publish_daily_plan']=plan
    return plan
