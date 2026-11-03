"""Local, conservative topic checks for optional quotes; no model or network call."""
import re


_GENERIC = (
    '我觉得', '你觉得', '我知道', '你知道', '能看到', '看到了', '能看见',
    '这条消息', '那条消息', '那个问题', '这个问题', '聊天记录', '当前用户',
    '之前', '刚才', '以前', '现在', '已经', '还是', '所以', '不过', '但是',
    '这个', '那个', '这里', '那里', '这张', '那张', '这样', '那样', '什么',
    '怎么', '为什么', '是不是', '能不能', '可以', '应该', '时候', '一下',
    '你好', '好呀', '好哦', '好哒', '晚上好', '早上好', '嗯嗯', '谢谢',
    '说过', '说的', '说了', '看到', '知道', '记得', '记下', '记录', '消息',
    '问题', '回答', '回复', '正文', '第一段', '第二段', '一段', '这段',
)


def topics(text):
    value = str(text or '').lower()
    result = {'poke_action'} if re.search(r'戳|拍一拍|拍了拍', value) else set()
    for term in sorted(_GENERIC, key=len, reverse=True):
        value = value.replace(term, ' ')
    value = re.sub(r'[我你他她它们的了呢啊呀吧啦哦很就才也都又还在是有和与把给]', ' ', value)
    result.update(token for token in re.findall(r'[a-z][a-z0-9_-]{1,}', value) if token not in {'qq', 'bot'})
    for chunk in re.findall(r'[\u4e00-\u9fff]{2,}', value):
        for width in (2, 3):
            result.update(chunk[i:i+width] for i in range(len(chunk)-width+1))
    return result


def supports_reply(host, event, state, selected, reply):
    """Drop uncertain attachment while preserving the complete ordinary answer."""
    from .context import entries_for
    anchor = topics(selected.get('text'))
    current = topics(getattr(event, 'message_str', ''))
    output = topics(reply)
    direct = any(str(q.get('mid') or '') == str(selected['mid']) and int(q.get('depth', 1)) == 1
                 for entry in entries_for(event) for q in entry.get('quotes', []))
    keep = bool(anchor) and (direct or not current or bool(anchor & current)) and (
        bool(anchor & output) or not output and (direct or bool(anchor & current)))
    state['relevance'] = {'keep': keep, 'reason': 'local_topic_match' if keep else 'unrelated_or_uncertain'}
    return keep
