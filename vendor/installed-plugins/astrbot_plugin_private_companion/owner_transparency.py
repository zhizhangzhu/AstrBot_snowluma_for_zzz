"""Explicit per-persona permission to explain this Bot's mechanisms to its owner."""
from __future__ import annotations

import re
from .persona_config import runtime_persona_setting

KEY = 'allow_owner_internal_discussion'
DIRECTIVE = ('[主要用户已授权的机制讨论]\n'
    '本人格已开启向当前主要用户说明自身内部机制。可以自然、准确地讨论自己的模型、插件、记忆、工具、配置、'
    '开发方案、失败原因和待审批事项；这些讨论不是身份或内部机制泄露，不得仅因出现相关词语而取消、淡化或改成闲聊。'
    '只依据可确认事实说明进度，不把申请说成已获准或已完成。仍保留阶段审批、账号隔离和秘密字段脱敏。'
    '后台原始控制标记、工具协议封套不应冒充聊天正文；用自己的语气解释，不照搬执行指令。')


def allowed(host, user=None, *, umo=''):
    if not runtime_persona_setting(host, KEY, False):
        return False
    users = getattr(host, 'data', {}).get('users', {})
    if not isinstance(user, dict):
        user = next((u for u in users.values() if isinstance(u, dict) and u.get('umo') == umo), {})
    uid = str(user.get('user_id') or user.get('id') or '')
    if not uid or user.get('relationship_role') != 'owner':
        return False
    try:
        owners = {str(x) for x in host._relationship_owner_user_ids()}
        _, platform, bot, _ = host._qzone_watch_binding()
    except (AttributeError, RuntimeError, ValueError, TypeError):
        return False
    canonical = str(platform) + ':FriendMessage:' + uid
    return bool(bot and uid in owners and user.get('umo') == canonical and (not umo or umo == canonical))


def protocol_payload(text):
    return bool(re.search(r'```(?:json|python|javascript)|<\|(?:im_start|im_end)|</?(?:tool_call|tool_result|analysis)>|'
        r'^[ \t]*(?:assistant\s+to=|\{\s*"(?:decision|tool_calls|function|arguments)"\s*:)',
        str(text or ''), re.I | re.M))
