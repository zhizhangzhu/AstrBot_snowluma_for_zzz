"""Observed Qzone interactions, scoped memory and repair of legacy comment IDs."""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from .bot_accounts import account_binding
from .logging_util import get_module_logger
from .qzone_native_actions import bound_client, call

logger = get_module_logger(__name__)


def migrate_comment_state(owner, state, posts, own_uin):
    """Repair ambiguous native post:N markers without replaying old deliveries."""
    if state.get('native_comment_identity_version') == 2:
        return
    fields = ['seen', 'replied', 'retry', 'delivery_unknown']
    before = {kind: set(state.get('comment_inbox_'+kind+'_keys', [])) for kind in fields}
    old_replied = set(state.get('comment_inbox_replied_ids', []))
    old_unknown = set(state.get('comment_inbox_delivery_unknown_ids', []))
    now = time.time()
    if state.get('comment_inbox_initialized_at'):
        state['native_comment_migration_cutoff'] = now-86400
    for post in posts:
        if not getattr(post, 'raw', {}).get('_snowluma_native_list'):
            continue
        for comment in post.comments:
            raw = comment.raw
            generic = owner._qzone_comment_fingerprint('', raw)
            cid, key, ids = owner._qzone_comment_identifiers(post, comment)
            raw_id = str(raw.get('comment_id', ''))
            # A shared ID was marked replied, but this author's text never was.
            blocked = ('post:'+raw_id in old_replied and generic not in before['replied']
                       and generic not in before['delivery_unknown']
                       and now-86400 <= float(getattr(comment,'create_time',0)) <= now+60)
            for kind in fields:
                if generic in before[kind] and not (kind == 'seen' and blocked):
                    state.setdefault('comment_inbox_'+kind+'_keys', []).append(key)
                    state.setdefault('comment_inbox_'+kind+'_ids', []).extend(ids)
            if 'post:'+raw_id in old_unknown and not before['delivery_unknown']:
                state.setdefault('comment_inbox_delivery_unknown_keys', []).append(key)
                blocked = False
            if blocked:
                state.setdefault('comment_inbox_retry_keys', []).append(key)
                state.setdefault('comment_inbox_retry_ids', []).extend(ids)
    for kind in fields:
        for suffix in ('ids', 'keys'):
            name = 'comment_inbox_'+kind+'_'+suffix
            state[name] = owner._qzone_trim_id_list(
                [x for x in state.get(name, []) if not str(x).startswith('post:')], limit=500)
    state['native_comment_identity_version'] = 2


async def remember_interaction(owner, post, actor, kind, reply=''):
    """An unavailable memory sink never turns a delivered reply into a retry."""
    try:
        binding = account_binding(owner)
        if not binding or str(post.uin) != str(binding.get('bot_id')):
            return False
        uid = str(actor.uin)
        if uid == str(binding['bot_id']):
            return False
        user = owner.data.get('users', {}).get(uid)
        # Never attribute a visitor's comment to the primary user.
        if not isinstance(user, dict) and uid != str(binding.get('primary_user_id')):
            return False
        adapter = str(binding['adapter_id'])
        # Qzone is the actual source; pair identity uses this Bot's QQ session.
        origin = f'{adapter}:FriendMessage:{uid}'
        persona = str(owner._active_persona_scope() or owner._primary_persona_id())
        if not persona:
            return False
        raw = getattr(actor, 'raw', {}) or {}
        content = str(getattr(actor, 'content', '') or '')[:800]
        identity = str(raw.get('comment_id', '')) if kind != 'like' else uid
        digest = hashlib.sha256(f'{binding["bot_id"]}|{persona}|{post.tid}|{uid}|{kind}|{identity}|{content}'.encode()).hexdigest()[:40]
        memory_id = 'qzone_interaction_'+digest
        state = owner._qzone_state_dict()
        saved = state.setdefault('interaction_memory_ids', [])
        if memory_id in saved:
            return True
        bridge = owner._memory_companion_bridge()
        recorder = getattr(bridge, 'record_event', None)
        if not callable(recorder):
            return False
        name = str(getattr(actor, 'name', '') or uid)[:80]
        post_text = str(getattr(post, 'text', '') or '')[:500]
        if kind == 'like':
            text = f'{name}曾给我的QQ空间说说「{post_text}」点过赞；这是读取点赞列表时确认的互动，具体点赞时间未知。'
        elif kind == 'reply':
            text = f'{name}在我的QQ空间说说「{post_text}」下评论：「{content}」。我在对应评论楼层回复：「{reply}」。'
        else:
            text = f'{name}在我的QQ空间说说「{post_text}」下评论：「{content}」。这是对方在空间的公开表达，不等同于私聊指令。'
        stamp = float(getattr(actor, 'create_time', 0) or 0)
        occurred = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp > 0 else ''
        result = await recorder(content=text, memory_id=memory_id, message_id=memory_id,
            memory_type='qzone_interaction', scope='private', session_id=origin, platform=adapter,
            subject={'kind':'user','id':uid,'name':name},
            object={'kind':'bot','id':str(binding['bot_id']),'name':persona},
            visibility='private_pair', sayability='direct', reality_level='bot_action',
            source_plugin='private_companion', confidence=1.0, importance=0.6,
            tags=['qzone','空间互动',kind,'评论' if kind != 'like' else '点赞'], occurred_at=occurred,
            metadata={'owner_bot_id':str(binding['bot_id']),'persona_id':persona,
                      'namespace_adapter_id':adapter,'event_type':'qzone_'+kind,'source_platform':'qzone',
                      'tid':post.tid,'actor_uin':uid,'comment_id':identity,
                      'observed_at':time.time(),'occurred_time_known':stamp>0,
                      'query_anchors':['QQ空间','评论','点赞','说说',content[:100]],
                      'clean_visible_text':text})
        if not result:
            return False
        state['interaction_memory_ids'] = list(dict.fromkeys([*saved, memory_id]))[-1000:]
        owner._save_data_sync(sections={'qzone_integration'})
        return True
    except Exception as exc:
        logger.warning('QQ空间互动记忆暂未保存: %s', type(exc).__name__)
        return False


async def observe_interactions(owner, posts, own_uin):
    """Record observed facts; this function never sends messages or reactions."""
    state = owner._qzone_state_dict()
    binding=account_binding(owner) or {}
    known=set(owner.data.get('users',{})) | {str(binding.get('primary_user_id') or '')}
    memory_ok=True
    for post in posts:
        for comment in getattr(post, 'comments', []):
            if str(comment.uin) != str(own_uin):
                saved=await remember_interaction(owner, post, comment, 'comment')
                if str(comment.uin) in known and not saved:memory_ok=False
                for reply in (getattr(comment,'raw',{}) or {}).get('replies',[]):
                    if str(reply.get('uin')) == str(own_uin) and reply.get('content'):
                        saved=await remember_interaction(owner,post,comment,'reply',str(reply['content']))
                        if str(comment.uin) in known and not saved:memory_ok=False
    client = bound_client(owner)
    if client is None:
        return memory_ok
    failed = False
    for post in posts:
        try:
            cursor = 0
            users = {}
            total = None
            for _ in range(10):
                data = await call(client, 'get_qzone_likes', target_uin=int(own_uin), tid=post.tid,
                                  begin_uin=cursor, num=100)
                if not isinstance(data, dict) or str(data.get('tid')) != post.tid or not isinstance(data.get('users'), list):
                    raise ValueError('invalid like page')
                total = int(data['total'])
                for row in data['users']:
                    uid = str(row['uin'])
                    if not uid.isdigit() or int(uid) <= 0:
                        raise ValueError('invalid liker')
                    users[uid] = row
                    actor = SimpleNamespace(uin=uid,name=row.get('name',''),content='',raw={},create_time=0)
                    saved=await remember_interaction(owner, post, actor, 'like')
                    if uid in known and not saved:memory_ok=False
                next_cursor = int(data.get('next_uin') or 0)
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            # Partial reads cannot prove absence of any user.
            state.setdefault('like_observations', {})[post.tid] = {
                'observed_at':time.time(),'total':total,'uins':list(users),
                'complete':total is not None and len(users)>=total}
        except Exception as exc:
            failed = True
            state['last_like_observation_error'] = type(exc).__name__
    state['last_like_observation_status'] = 'partial_failure' if failed else 'ok'
    state['like_observations'] = dict(list(state.get('like_observations', {}).items())[-20:])
    owner._save_data_sync(sections={'qzone_integration'})

    return memory_ok and not failed
