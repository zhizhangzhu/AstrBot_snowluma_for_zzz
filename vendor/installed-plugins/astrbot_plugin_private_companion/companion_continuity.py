"""Evidence-bound commitment follow-through and adaptive proactive preferences.

No model calls, timers or sending occur here. Existing growth review proposes
updates; the caller retains persona locks, revisions, budgets and persistence.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
import time

TERMINAL = {'已完成', '已取消'}


def clean(value, limit=180):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def stamp(value):
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else 0
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


def key(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        default=str).encode()).hexdigest()[:24]


def loop_id(loop):
    return str(loop.get('continuity_id') or 'loop_' + key([loop.get('text'), loop.get('created_ts')]))


def later_evidence(proposal, sources, *, after=0, role=None):
    index = {s['id']: s for s in sources}
    evidence = []
    refs = proposal.get('evidence')
    for ref in (refs if isinstance(refs, list) else [])[:6]:
        if not isinstance(ref, dict):
            continue
        source = index.get(str(ref.get('id') or ''))
        quote = str(ref.get('quote') or '').strip()
        if not source or not quote or quote not in source['text']:
            continue
        if role and source.get('role') != role:
            continue
        if stamp(source.get('at')) < after:
            continue
        evidence.append({'id': source['id'], 'quote': quote, 'at': source['at']})
    return evidence


def confidence(item):
    try:
        return .9 <= float(item.get('confidence', 0)) <= 1
    except (ValueError, TypeError):
        return False


def scope_of(user):
    return [str(user.get(k) or '') for k in ('umo', 'identity_bot_id', 'identity_persona_id')]


def enabled(host, feature):
    from .persona_config import runtime_persona_setting as setting
    return bool(setting(host, 'enable_companion_growth', False)
        and setting(host, 'enable_growth_interaction_learning' if feature == 'feedback'
            else 'enable_growth_experience_closure', True)
        and setting(host, 'enable_adaptive_proactive_feedback' if feature == 'feedback'
            else 'enable_commitment_followthrough', True))


def completion_supported(loop, refs, sources):
    index = {s['id']: s for s in sources}
    for ref in refs:
        source = index.get(ref['id'], {})
        quote = ref['quote']
        if source.get('role') == 'user':
            if re.search(r'没(?:有)?(?:成功|收到|修好|解决)|还(?:没|不能)|尚未|并未|失败', quote):
                continue
            if re.search(r'成功|收到|看到了|修好|解决了|完成了|搞定|发出来|可以用了|恢复了', quote):
                return True
        # Only an appropriate confirmed effect can close an action promise.
        kind = source.get('proof_kind')
        task = str(loop.get('text') or '')
        if source.get('role') == 'tool' and source.get('partial') is not True:
            if kind == 'qzone_publish_ack' and re.search(r'发.*(?:空间|说说)|发布.*动态', task):
                return True
            if kind == 'saved_text_artifact' and re.search(r'写.*日记|整理.*日记', task):
                return True
            if kind == 'meme_owned_ack' and re.search(r'收录|收藏|入库|存入图库',task) and not re.search(r'代码|修复|改进',task):
                return True
    return False


def action_id(action):
    return str(action.get('continuity_delivery_id') or 'delivery_' + key([
        action.get('ts'), action.get('action'), action.get('source'), action.get('text')]))


def feedback_context(user, now):
    """Only actual sends, never plans or failed actions, are review candidates."""
    result = []
    for action in (user.get('action_consequences') or [])[-18:]:
        if not isinstance(action, dict) or not action.get('ts'):
            continue
        age = now - stamp(action['ts'])
        if age < 0 or age > 30 * 86400:
            continue
        result.append({**{k: action.get(k) for k in ('ts', 'action', 'source', 'reason', 'text',
            'reply_text', 'reply_ts', 'status', 'feedback')}, 'delivery_id': action_id(action)})
    return result


def apply_commitments(host, user, proposals, sources, now):
    state = user.setdefault('companion_growth', {})
    loops = user.setdefault('open_loops', [])
    count = 0
    for proposal in proposals[:8]:
        if not isinstance(proposal, dict) or not confidence(proposal):
            continue
        kind = proposal.get('kind')
        role = 'assistant' if kind == 'bot_promise' else 'user'
        if kind not in ('bot_promise', 'user_plan', 'standing_preference'):
            continue
        refs = later_evidence(proposal, sources, role=role)
        summary = clean(proposal.get('summary'), 120)
        if not refs or not summary:
            continue
        evidence_key = key([kind, sorted(r['id'] for r in refs)])
        known = state.setdefault('commitment_evidence', [])
        if evidence_key in known:
            continue
        closed_source_ids={r['id'] for old in state.get('commitment_history',[])
            for r in (old.get('commitment_evidence') or []) if isinstance(r,dict) and r.get('id')}
        if any(r['id'] in closed_source_ids for r in refs):
            continue
        known.append(evidence_key)
        if kind == 'standing_preference':
            prefs = state.setdefault('interaction_preferences', [])
            prefs.append({'summary': summary, 'evidence': refs, 'at': now,
                'kind': 'standing_preference'})
            del prefs[:-12]
        else:
            # No timed reminder or social action is created by remembering a promise.
            existing = next((l for l in loops if isinstance(l, dict) and l.get('text') == summary), None)
            if existing is None:
                # Do not evict a pending commitment to make room for another.
                if len([l for l in loops if isinstance(l, dict) and l.get('status') not in TERMINAL]) >= 12:
                    known.remove(evidence_key)
                    continue
                created = max(stamp(r['at']) for r in refs)
                existing = {'text': summary, 'created_ts': created, 'status': '待自然延续',
                    'source': 'grounded_commitment', 'commitment_kind': kind,
                    'commitment_evidence': refs, 'continuity_id': 'loop_' + evidence_key}
                loops.append(existing)
            elif existing.get('status') in TERMINAL:
                continue  # Replaying an old promise cannot reopen a completed task.
        count += 1
    del state.setdefault('commitment_evidence', [])[:-120]
    return count


def review_loops(user, proposals, sources, now):
    count = 0
    for proposal in proposals[:12]:
        if not isinstance(proposal, dict) or not confidence(proposal):
            continue
        loop = next((l for l in user.get('open_loops', []) if isinstance(l, dict)
            and loop_id(l) == proposal.get('loop_id')), None)
        if not loop or loop.get('status') in TERMINAL:
            continue
        outcome = proposal.get('state')
        after=0 if outcome=='standing_preference' else stamp(loop.get('created_ts'))
        refs = later_evidence(proposal, sources, after=after)
        if not refs:
            continue
        if outcome not in ('progress', 'waiting_user', 'deferred', 'standing_preference'):
            continue
        # A bot's own suggestion cannot create a user boundary or postpone their request.
        if outcome != 'progress' and not later_evidence(proposal, sources,
                after=after, role='user'):
            continue
        if (loop.get('continuity_state') == outcome and loop.get('next_step','') == clean(proposal.get('next_step'))
            and loop.get('progress_evidence') == refs):
            continue
        loop.update(continuity_id=loop_id(loop), continuity_state=outcome,
            next_step=clean(proposal.get('next_step')), progress_evidence=refs,
            progress_updated_at=now)
        if outcome == 'progress' and later_evidence(proposal, sources,
                after=stamp(loop.get('created_ts')), role='user'):
            loop['followup_suppressed'] = False
            loop['followup_sent_count'] = 0
            delivery=user.setdefault('companion_growth',{}).setdefault('followup_delivery',{}).get(loop_id(loop))
            if isinstance(delivery,dict):delivery['count']=0
        if outcome == 'standing_preference':
            loop['followup_suppressed'] = True
            loop.update(status='已完成',resolved_ts=now,resolution='持续相处偏好已保存，不再作为待询问任务',resolution_evidence=refs)
            prefs = user.setdefault('companion_growth', {}).setdefault('interaction_preferences', [])
            prefs.append({'summary': clean(loop['text']), 'evidence': refs, 'at': now,
                'kind': 'standing_preference'})
            del prefs[:-12]
        count += 1
    return count


def followup_allowed(loop, now, user=None):
    if loop.get('status') in TERMINAL or loop.get('followup_suppressed'):
        return False
    if loop.get('continuity_state') in ('waiting_user', 'deferred', 'standing_preference'):
        return False
    sent = stamp(loop.get('followup_sent_at'))
    delivered=(user or {}).get('companion_growth',{}).get('followup_delivery',{}).get(loop_id(loop),{})
    sent=max(sent,stamp(delivered.get('at')))
    count=max(int(loop.get('followup_sent_count',0)),int(delivered.get('count',0)))
    user_update = stamp(loop.get('progress_updated_at'))
    if sent and user_update <= sent and count >= 2:
        return False
    return not sent or now - sent >= 36 * 3600


def note_followup_sent(user, now):
    if user.get('planned_proactive_source') != 'open_loop':
        return
    topic = str(user.get('planned_proactive_topic') or '')
    for loop in user.get('open_loops', []):
        if not isinstance(loop, dict) or not loop.get('text') or loop['text'] not in topic:
            continue
        loop['followup_sent_at'] = now
        loop['followup_sent_count'] = int(loop.get('followup_sent_count', 0)) + 1
        deliveries=user.setdefault('companion_growth',{}).setdefault('followup_delivery',{})
        entry=deliveries.setdefault(loop_id(loop),{'count':0})
        entry.update(at=now,count=int(entry.get('count',0))+1)
        for identity in sorted(deliveries,key=lambda k:deliveries[k].get('at',0))[:-64]:
            deliveries.pop(identity,None)


def cancel_stale_followups(host, user, now, *, include_global=False):
    """Only cancel this exact user's matching loop, preserving unrelated plans."""
    closed = [l for l in user.get('open_loops', []) if isinstance(l, dict)
        and (l.get('status') in TERMINAL or not followup_allowed(l, now,user))]
    uid = str(user.get('user_id') or '')
    global_items = list(host.data.get('proactive_candidate_pool') or []) if include_global else []
    for item in list(user.get('proactive_impulses') or []) + global_items:
        if not isinstance(item, dict) or item.get('source') != 'open_loop':
            continue
        if str(item.get('user_id') or '') != uid and item not in (user.get('proactive_impulses') or []):
            continue
        topic = str(item.get('topic') or '')
        if any(l.get('text') and l['text'] in topic for l in closed):
            if item.get('state') in ('queued', 'deferred', 'pending', 'planned'):
                item.update(state='cancelled', note='约定已有后续或暂不追问', updated_ts=now)
            if item.get('status') in ('queued', 'deferred', 'pending', 'planned', 'accepted'):
                item.update(status='cancelled', note='约定已有后续或暂不追问', updated_ts=now)


def apply_feedback(user, proposals, sources, now):
    state = user.setdefault('companion_growth', {})
    profile = state.setdefault('adaptive_feedback', {'version': 1, 'events': []})
    events = profile['events']
    actions = {r['delivery_id']: r for r in feedback_context(user, now)}
    count = 0
    for proposal in proposals[:12]:
        if not isinstance(proposal, dict) or not confidence(proposal):
            continue
        action = actions.get(proposal.get('delivery_id'))
        if not action:
            continue
        refs = later_evidence(proposal, sources, after=stamp(action['ts']), role='user')
        # An unrelated message next day cannot be treated as liking this send.
        refs = [r for r in refs if stamp(r['at']) - stamp(action['ts']) <= 24 * 3600]
        if not refs:
            continue
        verdict = proposal.get('verdict')
        dimension = proposal.get('dimension')
        if verdict not in ('positive', 'negative', 'neutral', 'unrelated'):
            continue
        if dimension not in ('topic', 'action', 'time', 'source'):
            continue
        # Semantic review supersedes the old substring classifier, rather than voting again.
        identity = key([action['delivery_id'], dimension])
        raw_terms = proposal.get('topic_terms')
        terms = raw_terms if isinstance(raw_terms, list) else []
        grounded_text = str(action.get('text') or '') + ' '.join(r['quote'] for r in refs)
        entry = {'id': identity, 'delivery_id': action['delivery_id'], 'dimension': dimension,
            'verdict': verdict, 'source': clean(action['source'], 40), 'action': clean(action['action'], 40),
            'topic': clean(proposal.get('topic'), 60) if dimension == 'topic' else '',
            'topic_terms': [clean(t, 24) for t in terms[:5]
                if len(clean(t, 24)) >= 2 and clean(t,24) not in ('新闻','资讯','分享','消息','聊天','内容','东西','这个','那个')
                and clean(t, 24).casefold() in grounded_text.casefold()],
            'hour': datetime.fromtimestamp(stamp(action['ts']), __import__('zoneinfo').ZoneInfo('Asia/Shanghai')).hour,
            'summary': clean(proposal.get('summary')), 'evidence': refs,
            'sent_at': action['ts'], 'updated_at': now}
        if dimension == 'topic' and not entry['topic']:
            continue
        old = next((e for e in events if e.get('id') == identity), None)
        if old and {k:v for k,v in old.items() if k!='updated_at'} == {k:v for k,v in entry.items() if k!='updated_at'}:
            continue
        events[:] = [e for e in events if e.get('id') != identity]
        events.append(entry)
        count += 1
    # Do not derive a negative preference from missing replies; native slowdown remains separate.
    events[:] = [e for e in events if now - stamp(e.get('sent_at')) <= 90 * 86400][-120:]
    return count


def adaptive_effect(user, *, source='', action='', topic='', now=None):
    now = time.time() if now is None else now
    events = user.get('companion_growth', {}).get('adaptive_feedback', {}).get('events', [])
    matches = []
    for e in events:
        if e.get('verdict') not in ('positive', 'negative'):
            continue
        dimension = e.get('dimension')
        match = (dimension == 'source' and source and e.get('source') == source
            or dimension == 'action' and action and e.get('action') == action
            or dimension == 'topic' and topic and any(t.casefold() in topic.casefold()
                for t in [e.get('topic', '')] + e.get('topic_terms', []) if len(t) >= 2)
            or dimension == 'time' and e.get('hour') == datetime.fromtimestamp(now,
                __import__('zoneinfo').ZoneInfo('Asia/Shanghai')).hour)
        if not match:
            continue
        weight = .5 ** (max(0, now-stamp(e.get('sent_at'))) / (30 * 86400))
        matches.append((e, weight))
    # Multiple dimensions for one delivery are still one feedback event.
    grouped = {}
    for e, weight in matches:
        current = grouped.get(e['delivery_id'])
        sign = 1 if e['verdict'] == 'positive' else -1
        if current is None or sign < current[0]:
            grouped[e['delivery_id']] = (sign, weight)
    evidence = sum(w for _, w in grouped.values())
    if len(grouped) < 2 or evidence < 1:
        return {'bias': 0.0, 'interval_multiplier': 1.0, 'evidence_count': len(grouped)}
    signed = sum(s*w for s,w in grouped.values()) / evidence
    return {'bias': max(-.15, min(.10, signed*.10)),
        'interval_multiplier': 1 + min(.75, max(0, -signed)*.5),
        'evidence_count': len(grouped)}


def archive_closures(user, now):
    """Retain completed commitments even after the small native topic list rotates."""
    state = user.setdefault('companion_growth', {})
    archive = state.setdefault('commitment_history', [])
    for loop in user.get('open_loops', []):
        if not isinstance(loop, dict) or loop.get('status') not in TERMINAL:
            continue
        identity = loop_id(loop)
        entry = {k: loop.get(k) for k in ('text','status','commitment_kind','commitment_evidence','resolution',
            'resolution_evidence','resolved_at','resolved_ts','created_ts')}
        entry.update(continuity_id=identity)
        archive[:] = [e for e in archive if e.get('continuity_id') != identity]
        archive.append(entry)
    del archive[:-100]


def export_shared(host, user, now, *, episode=None):
    """Export persisted, private-owner artifacts; never copy another chat's text."""
    from .real_schedule.runtime import enabled as real_enabled, service
    if not real_enabled(host) or user.get('relationship_role') != 'owner':
        return
    try:
        runtime=service(host);binding=runtime.binding()
    except ValueError:
        return
    umo=str(user.get('umo') or '')
    if not umo.startswith(binding['platform_id']+':FriendMessage:'):
        return
    if str(user.get('identity_bot_id') or '') != binding['bot_id']:
        return
    state=user.get('companion_growth',{})
    for entry in state.get('completed_experiences',[])[-20:]:
        summary=clean(entry.get('summary'),300)
        at=stamp(entry.get('at'))
        refs=entry.get('evidence')
        if not summary or not isinstance(refs,list) or not refs or not at or at>now+5:
            continue
        runtime.ledger.receipt(binding['scope'],'','shared:'+umo+':'+key([summary,refs]),{
            'verified':True,'evidence_kind':'commitment_resolution','summary':summary,
            'session_id':umo,'evidence':refs,'skills':[],
            'note':'来自有原话或执行依据的约定结案，不等于新增动作'},now=at)
    if isinstance(episode,dict) and episode.get('summary'):
        summary=clean(episode['summary'],350)
        recorded_at=stamp(episode.get('created_ts')) or now
        if recorded_at>now+5:return
        runtime.ledger.receipt(binding['scope'],'','dialogue-summary:'+umo+':'+key(episode),{
            'verified':True,'evidence_kind':'saved_conversation_summary',
            'summary':'保存了一段与你的对话摘要：'+summary,
            'session_id':umo,'skills':[],'note':'这是已保存的聊天归纳，不证明其中提及的现实动作曾被执行'},now=recorded_at)


REVIEW_PROMPT = '''
额外整理三类相处记录，仍只输出同一个JSON，不执行动作、不新增模型调用。
closed_commitments是已结案的历史，不要从旧原话重新建立同一承诺；只有用户后来明确提出新约定才可另记。
commitments: 最多4个真实约定。kind=bot_promise/user_plan/standing_preference，summary、confidence、evidence。
Bot承诺只引用实际assistant原话，用户计划/长期相处偏好只引用用户原话；不要把建议、假设、玩笑或事实总结当承诺。
loop_reviews: 对现有loop_id，state=progress/waiting_user/deferred/standing_preference，给next_step/confidence/evidence。
暂缓、等待用户主动再提、持续偏好都不该反复追问。只有明确的用户后续才能重新开启跟进；不能拿沉默当取消。
closures仍用于真正完成或取消。工具检索成功不代表图片发出，发布回执不证明用户已看到；Bot自己声称做完不算执行证据。
feedback_reviews: 每条给delivery_id、dimension(topic/action/time/source)、verdict(positive/negative/neutral/unrelated)、
topic、topic_terms（同主题的少量检索词）、summary、confidence、evidence。只审核实际主动发送和其后24小时内的用户原话。
“好困”“不对你刚才理解错了”“这个新闻说的不是我”不是喜欢或讨厌这类主动分享；一般问候、换话题属于unrelated。
维度必须有用户明确的评价依据：评价内容不扩大为讨厌整个来源，评价时机不扩大为讨厌话题。
没有回应没有评价，不产生负面反馈；一个事件不能因多次整理重复计数。当前用户纠正应替换对同一次互动的旧理解。
上述数组缺乏证据就留空；每个对象的evidence都是[{"id":"source_messages编号","quote":"逐字原话片段"}]。
'''
