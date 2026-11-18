"""Select remembered experiences without deleting low-level audit receipts."""
from __future__ import annotations

import hashlib
import json
import time

TECHNICAL_ACTIONS = ('set_input_status', 'set_online_status', 'set_custom_online_status',
    'set_qq_custom_status', 'set_diy_online_status', 'set_qq_status', 'heartbeat', 'send_heartbeat')
AUDIT_ONLY_KINDS = ('confirmed_chat_reply', 'transport_read')


def category(row):
    body = row['body']
    kind = body.get('evidence_kind', '')
    if kind in ('article_text', 'article_text_acquired', 'search_snippet', 'subtitle_text',
                'qzone_text', 'knowledge_excerpt', 'skill_document'):
        origin = str(body.get('function_origin') or '')
        if kind == 'qzone_text':return 'reading:qzone'
        if kind in ('knowledge_excerpt','skill_document'):return 'reading:learning'
        if kind == 'search_snippet' or origin in ('web_idle','chat_search','proactive_tool_search'):
            return 'reading:search'
        return 'reading:news'
    if kind in ('commitment_resolution', 'shared_experience', 'saved_conversation_summary','qzone_publish_ack',
                'qzone_interaction_ack', 'qzone_interaction_verified', 'qq_action_ack'):
        return 'interaction'
    return 'creation'


def eligible(row, session_id=''):
    body = row.get('body', {})
    if body.get('verified') is not True or not body.get('summary'):
        return False
    if body.get('evidence_kind') in AUDIT_ONLY_KINDS or body.get('evidence_kind') == 'saved_chat_presence':
        return False
    if body.get('source') in TECHNICAL_ACTIONS:
        return False
    # Privately requested reads are not a shared experience with another user.
    for key in ('session_id', 'origin_session_id'):
        origin = str(body.get(key) or '')
        if origin and origin != session_id:
            return False
    function = str(body.get('function_origin') or '')
    if function in ('chat_search', 'qzone_chat_feed', 'qzone_chat_profile', 'news_user'):
        if not session_id or session_id not in (body.get('session_id'), body.get('origin_session_id')):
            return False
    return True


def select(rows, *, limit=12, session_id='', now=None):
    """Deduplicate sources, prefer evidence depth, and reserve topic diversity."""
    now = time.time() if now is None else now
    unique = {}
    for row in rows:
        if not eligible(row, session_id):
            continue
        body = row['body']
        source = body.get('source_id') or body.get('source_url')
        if not source:
            source = hashlib.sha256(json.dumps([body.get('evidence_kind'), body['summary']],
                ensure_ascii=False).encode()).hexdigest()
        strength = (body.get('partial') is not True,
                    body.get('evidence_kind') != 'search_snippet', row['created'])
        previous = unique.get(str(source))
        if previous is None or strength > previous[0]:
            unique[str(source)] = (strength, row)
    groups = {}
    for _, row in unique.values():
        groups.setdefault(category(row), []).append(row)
    for bucket in groups.values():
        bucket.sort(key=lambda r: (1 / (1 + max(0, now-r['created'])/86400)
            + (.2 if category(r) == 'interaction' else 0), r['created']), reverse=True)
    chosen = []
    # One active source cannot crowd out shared interaction and creations.
    while len(chosen) < max(0, limit) and any(groups.values()):
        for kind in ('interaction', 'reading:news', 'reading:search', 'reading:qzone',
                     'reading:learning', 'creation'):
            if groups.get(kind) and len(chosen) < limit:
                chosen.append(groups[kind].pop(0))
    return sorted(chosen, key=lambda r: r['created'], reverse=True)
