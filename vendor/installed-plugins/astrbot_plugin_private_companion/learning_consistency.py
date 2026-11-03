"""Evidence-bound relationship learning and idempotent pending-topic maintenance.

Called under the companion's existing persona/revision/write locks. No sends,
model calls or permission changes occur here.
"""
from __future__ import annotations
import re
import unicodedata

TERMINAL = {'已完成', '已取消'}

def high_confidence(value,minimum=.9):
    try:return minimum<=float(value)<=1
    except (TypeError,ValueError):return False

def canonical(value):
    text=unicodedata.normalize('NFKC',str(value or '')).lower()
    for old,new in [('收录','收藏'),('存入图库','收藏'),('存进图库','收藏'),('换抽数','兑换抽数'),('未完成','待处理')]:text=text.replace(old,new)
    text=re.sub(r'用户(?:说|提到|表示)|之后若|之后|之后再|届时|可再|可自然|再确认|是否已经|是否已|是否|目前|相关进展|待自然延续|片段整理|提醒我|提醒用户','',text)
    return re.sub(r'[^\w\u3400-\u9fff]','',text)

def same_topic(a,b):
    for pattern in [r'“([^”]+)”',r'《([^》]+)》']:
        x,y=set(re.findall(pattern,str(a))),set(re.findall(pattern,str(b)))
        if x and y and x!=y:return False
    a,b=canonical(a),canonical(b)
    if not a or not b:return False
    if a==b:return True
    # Do not merge reminders for different concrete dates/times or quoted titles.
    for pattern in [r'\d{1,4}(?:年|月|日|号|点|:)[\d:]*',r'“([^”]+)”',r'《([^》]+)》']:
        x,y=set(re.findall(pattern,a)),set(re.findall(pattern,b))
        if x and y and x!=y:return False
    if all('二游' in value and '抽数' in value and ('换' in value or '兑换' in value) for value in (a,b)):return True
    if all('那句重要的话' in value and ('表达' in value or '说' in value or '告诉' in value) for value in (a,b)):return True
    if min(len(a),len(b))>=8 and (a in b or b in a):return True
    x={a[i:i+2] for i in range(len(a)-1)};y={b[i:i+2] for i in range(len(b)-1)}
    return len(x&y)>=6 and len(x&y)/max(1,min(len(x),len(y)))>=.72

def consolidate_loops(user):
    result=[];changed=0
    for row in user.get('open_loops',[]):
        if not isinstance(row,dict):continue
        target=next((r for r in result if r.get('status') not in TERMINAL and row.get('status') not in TERMINAL and same_topic(r.get('text'),row.get('text'))),None)
        if target is None:result.append(row);continue
        aliases=target.setdefault('aliases',[])
        aliases[:]=list(dict.fromkeys([*aliases,*row.get('aliases',[]),str(row.get('text',''))]))[-8:]
        for key in ('commitment_evidence','progress_evidence','resolution_evidence'):
            values=target.setdefault(key,[])
            for ref in row.get(key,[]):
                if ref not in values:values.append(ref)
            del values[:-12]
        changed+=1
    closed=[r for r in [*result,*user.get('companion_growth',{}).get('commitment_history',[])] if isinstance(r,dict) and r.get('status') in TERMINAL and r.get('resolution_evidence')]
    for row in result:
        if row.get('status') in TERMINAL:continue
        completed=next((old for old in closed if float(old.get('resolved_ts') or old.get('resolved_at') or 0)>=float(row.get('created_ts') or 0) and same_topic(old.get('text'),row.get('text'))),None)
        if completed:
            for field in ('status','resolution','resolution_evidence','resolved_at','resolved_ts'):
                if field in completed:row[field]=completed[field]
            changed+=1
    user['open_loops']=result[-12:]
    return changed

def add_loop(user,row):
    if row.get('source')!='user_message':
        closed=[*user.get('open_loops',[]),*user.get('companion_growth',{}).get('commitment_history',[])]
        if any(isinstance(old,dict) and old.get('status') in TERMINAL and same_topic(old.get('text'),row.get('text')) for old in closed):return False
    user.setdefault('open_loops',[]).append(row)
    return consolidate_loops(user)==0

def apply_learning(host,user,payload,sources,now,*,allow_loops=True,allow_relationship=True):
    index={str(s.get('id')):s for s in sources if isinstance(s,dict)}
    def refs(value,role=None):
        found=[]
        for ref in (value.get('evidence') or [])[:8]:
            if not isinstance(ref,dict):continue
            source=index.get(str(ref.get('id')));quote=str(ref.get('quote') or '').strip()
            if source and quote and quote in source.get('text','') and (role is None or source.get('role')==role):
                found.append({'id':source['id'],'quote':quote,'role':source.get('role'),'at':source.get('at')})
        return found
    counts={'merged_loops':0,'relationship_updates':0}
    getter=getattr(host,'_effective_plugin_persona_id',None)
    if not callable(getter):return counts
    pid=str(getter() or '__single__');uid=str(user.get('user_id') or '')
    if not uid or user.get('relationship_role')!='owner' or user.get('identity_persona_id') not in (None,'',pid):return counts
    if allow_loops:counts['merged_loops']=consolidate_loops(user)
    for proposal in (payload.get('relationship_updates',[])[:1] if allow_relationship else []):
        if not isinstance(proposal,dict) or not high_confidence(proposal.get('confidence')):continue
        evidence=refs(proposal);user_refs=refs(proposal,'user')
        text=str(proposal.get('text') or '').strip()[:2400]
        # A model's romantic assertion alone is not mutual agreement.
        if not text or not user_refs or not evidence:continue
        records=user.setdefault('persona_relationship_prompts',{})
        old=records.get(pid,{})
        learned={'persona_id':pid,'stable_user_id':uid,'relationship_mode':str(user.get('relationship_mode') or 'normal'),
                 'text':host._normalize_owner_exclusive_relationship_prompt(text),
                 'source':'learned_dialogue','evidence':evidence,'updated_at':now}
        user.setdefault('learned_persona_relationships',{})[pid]=learned
        if not old or old.get('source')=='learned_dialogue':records[pid]=learned;counts['relationship_updates']+=1
    loops=user.get('open_loops',[])
    from .companion_continuity import loop_id
    lookup={loop_id(row):row for row in loops}
    for proposal in (payload.get('loop_merges',[])[:6] if allow_loops else []):
        if not isinstance(proposal,dict) or not high_confidence(proposal.get('confidence'),.95):continue
        chosen=[lookup[k] for k in proposal.get('loop_ids',[]) if k in lookup]
        chosen=[row for row in chosen if row.get('status') not in TERMINAL]
        if len(chosen)<2:continue
        # Require exact current texts; stale model output cannot merge changed tasks.
        quoted=proposal.get('texts',[])
        if any(row.get('text') not in quoted for row in chosen):continue
        first=chosen[0]
        for row in chosen[1:]:
            first.setdefault('aliases',[]).append(row['text'])
            row.update(status='已取消',resolved_ts=now,resolution='同一事项已合并',merged_into=loop_id(first))
            counts['merged_loops']+=1
    return counts

REVIEW_PROMPT='''
补充整理（仍只使用本次 source_messages 和当前人格）：
relationship_updates：最多1项 {text,confidence,evidence:[{id,quote}]}。仅总结当前人格与当前主要用户双方已经表达并有依据的关系、共同定位和相处分寸；必须有用户本人的原话支持，不能凭Bot单方面自称升级关系。没有变化就空数组。已有手动关系资料不可覆盖。
loop_merges：同一件尚未完成的事仅保留一项。可提出 {loop_ids:[当前编号],texts:[这些编号对应的完整原文],confidence:0.99}；不同日期、对象或尚待分别兑现的承诺不能合并。对已经完成或取消的任务用现有 closures，不能仅因为提到过结果或Bot声称成功就结案；用户明确确认收到、完成、解决或取消才是依据。持续相处约定和表达偏好不当作待办追问。
'''
