"""Explicit self-names and same-account conversation evidence, without model guesses."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import time


def message_text(content):
    parts = [content] if isinstance(content, str) else [
        p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text'
    ] if isinstance(content, list) else []
    # Platform-injected identity/reminder blocks are not user self-statements.
    return '\n'.join(re.sub(r'<system_reminder>.*?</system_reminder>', '', p, flags=re.S)
                     for p in parts if isinstance(p, str)).strip()


def self_names(text):
    """Only literal first-person introductions; never infer names from quoted people."""
    found = []
    for clause in re.split(r'[，。！？!?\n,]', str(text or '')):
        clause = clause.strip()
        if any(word in clause for word in ('假设', '例如', '举例', '他说', '她说', '转发', '不是', '不叫', '别叫', '不要叫', '如果')):
            continue
        preferred = re.match(r'^(?:以后)?(?:你可以|你就|你|可以)?(?:称呼我|叫我)(.+)$', clause)
        alias = re.match(r'^(?:我的(?:名字|昵称|外号|别名)(?:就)?(?:叫|是)|我也叫|我叫)(.+)$', clause)
        introduction = re.match(r'^我是(?:来自群聊[“「\"].*?[”」\"]的)?(.+)$', clause)
        match = preferred or alias or introduction
        if not match:
            continue
        name = match.group(1).strip(' “「\"”」')
        name = re.sub(r'(?:就好啦|就好了|就好|哦|啦|呀|呢|吧|哟|喔|哈|~|～)+$', '', name)
        if not re.fullmatch(r'[\w\u4e00-\u9fff·]{1,24}', name) or name.isdigit():
            continue
        if any(word in name for word in ('来自', '朋友', '恋人', '同学', '学生', '老师', '机器人', '模型', '你的', '一个', '管理员', '主人', '大人', '主子', '系统', '什么', '怎么', '哪里', '谁', '啥')):
            continue
        if name.endswith('吗') or introduction and not preferred and not alias and (name.endswith(('人', '岁', '生', '的')) or name.startswith(('一只', '一名', '来', '想', '觉得', '说', '第一次', '新来的', '准备'))):
            continue
        found.append((name, bool(preferred) or clause.startswith('我的名字')))
    return found


def learn_address(user, text, *, message_id='', now=None, block=None):
    if not isinstance(user, dict):
        return False
    claims = [(name, preferred) for name, preferred in self_names(text)
              if not callable(block) or not block(name)]
    if not claims:
        return False
    before = json.dumps(user, ensure_ascii=False, sort_keys=True, default=str)
    aliases = user.setdefault('aliases', [])
    sources = user.setdefault('alias_sources', {})
    if not isinstance(aliases, list) or not isinstance(sources, dict):
        return False
    for name, preferred in claims:
        if name not in aliases:
            aliases.append(name)
            sources[name] = {'source': 'explicit_user_message', 'evidence': text[:240],
                             'message_id': message_id, 'at': now or time.time()}
        current = str(user.get('nickname') or '')
        placeholder = current in ('', '你', '用户', str(user.get('user_id') or ''))
        # A direct request changes the preferred address; self-introductions
        # replace only an unlearned platform/default name.
        if preferred or placeholder or user.get('nickname_source') == 'platform_display_name' or not user.get('nickname_source') and current == str(user.get('last_display_name') or ''):
            user['nickname'] = name
            user['nickname_source'] = 'explicit_user_message'
    del aliases[:-24]
    for name in list(sources):
        if name not in aliases:
            sources.pop(name, None)
    return json.dumps(user, ensure_ascii=False, sort_keys=True, default=str) != before


async def own_conversations(host):
    from .bot_accounts import account_binding
    binding = account_binding(host)
    if not binding or not binding.get('bot_id') or not binding.get('adapter_id'):
        raise ValueError('当前人格尚未绑定QQ实例')
    manager = host.context.conversation_manager
    rows = []
    for uid, user in list(host.data.get('users', {}).items()):
        if not isinstance(user, dict):
            continue
        umo = str(user.get('umo') or '')
        if umo != f"{binding['adapter_id']}:FriendMessage:{uid}":
            continue
        cid = await manager.get_curr_conversation_id(umo)
        if not cid:
            continue
        conversation = await manager.get_conversation(umo, cid)
        if conversation is None or str(conversation.user_id) != umo:
            continue
        raw = conversation.history
        history = json.loads(raw or '[]') if isinstance(raw, str) else raw
        if isinstance(history, list):
            rows.append((str(uid), user, conversation, history))
    return rows


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).timestamp() if value.tzinfo is None else value.timestamp()
    return 0.0


async def repair_addresses(host):
    changed = []
    for uid, _, conversation, history in await own_conversations(host):
        async with host._data_lock:
            user = host.data['users'][uid]
            updated = False
            for index, item in enumerate(history):
                if not isinstance(item, dict) or item.get('role') != 'user':
                    continue
                text = message_text(item.get('content'))
                updated |= learn_address(user, text, message_id=f'{conversation.cid}:{index}',
                                         block=getattr(host, '_worldbook_self_registration_block_word_hit', None))
            sections = {'users'} if updated else set()
            updater = getattr(host, '_req041_update_unified_profile_facts', None)
            if callable(updater) and user.get('nickname_source') == 'explicit_user_message':
                result = updater(user, {'preferred_address': user['nickname'], 'display_name': user['nickname']},
                                 actor_id='explicit_user_message', schedule_save=False)
                if result.get('state') != 'skipped' and result.get('ok') is not True:
                    raise ValueError(result.get('code') or '称呼同步到统一档案失败')
                if result.get('changed'):
                    sections.add('unified_person')
            if sections:
                host._save_data_sync(sections=sections)
            changed.append({'user_id': uid, 'nickname': user.get('nickname'),
                            'aliases': list(user.get('aliases') or []), 'updated': updated})
    return changed


async def recover_conversation_receipts(host, runtime):
    """Recover existence/counts only when the entire saved session is dated to one day."""
    recovered = []
    for uid, user, conversation, history in await own_conversations(host):
        first, last = timestamp(conversation.created_at), timestamp(conversation.updated_at)
        if not first or not last or last < first:
            continue
        day = host._environment_fromtimestamp(last).strftime('%Y-%m-%d')
        if host._environment_fromtimestamp(first).strftime('%Y-%m-%d') != day:
            continue
        incoming = sum(isinstance(m, dict) and m.get('role') == 'user' for m in history)
        outgoing = sum(isinstance(m, dict) and m.get('role') == 'assistant' and bool(message_text(m.get('content'))) for m in history)
        if not incoming:
            continue
        name = user.get('nickname') or uid
        # No private message text is copied into a persona-wide diary source.
        body = {'verified': True, 'evidence_kind': 'saved_chat_presence',
                'summary': f'与{name}有已保存的私聊：用户消息{incoming}条，回复{outgoing}条',
                'session_id': user['umo'], 'user_id': uid, 'participant_name': name,
                'conversation_id': conversation.cid, 'recovered_from': 'astrbot_core',
                'message_count': incoming + outgoing}
        digest = hashlib.sha256(json.dumps(history, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        rid = runtime.ledger.receipt(runtime.scope(), '', f'core-chat:{conversation.cid}:{digest}', body, now=last)
        recovered.append({'id': rid, 'day': day, 'user_id': uid})
    return recovered
