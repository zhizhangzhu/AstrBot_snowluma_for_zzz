"""Diary evidence includes actual chats; ordinary recall keeps its existing ACL."""
from datetime import datetime, timedelta

from .experience import select


def dated_receipts(host, runtime, day):
    clock = host._environment_now()
    start = datetime.fromisoformat(day).replace(tzinfo=clock.tzinfo)
    end = start + timedelta(days=1)
    rows = runtime.ledger.diary_receipts(runtime.scope(), start.timestamp(), end.timestamp())
    users = host.data.get('users', {})
    own_binding = runtime.binding()
    owner_umos = {str(u.get('umo')) for u in users.values()
                  if isinstance(u, dict) and u.get('relationship_role') == 'owner' and u.get('umo')}
    owner_umo = next(iter(owner_umos)) if len(owner_umos) == 1 else ''
    chats = {}
    others = []
    for receipt in rows:
        body = receipt['body']
        if body.get('evidence_kind') not in ('confirmed_chat_reply', 'saved_chat_presence'):
            others.append(receipt)
            continue
        session = str(body.get('session_id') or '')
        uid = session.rsplit(':', 1)[-1]
        user = users.get(uid)
        if not isinstance(user, dict) or session != str(user.get('umo') or ''):
            continue
        if session != f"{own_binding['platform_id']}:FriendMessage:{uid}":
            continue
        name = str(user.get('nickname') or uid)
        # Keep only a conversation's existence and known name. Another user's
        # private statements are never copied into a persona-wide diary prompt.
        safe_body = {'verified': True, 'evidence_kind': body['evidence_kind'],
                     'summary': f'与{name}有已确认的私聊往来', 'participant_name': name,
                     'user_id': uid, 'session_id': session}
        if body.get('evidence_kind') == 'saved_chat_presence':
            safe_body['summary'] += f"；该会话保存了{int(body.get('message_count') or 0)}条消息"
        if uid not in chats:
            chats[uid] = {**receipt, 'body': safe_body}
    # Reserve a slot for each correspondent before selecting other activities.
    # select() still enforces session ACL for reading/private-content receipts.
    selected = select(others, limit=max(0, 40 - len(chats)), session_id=owner_umo)
    return list(chats.values()) + selected
