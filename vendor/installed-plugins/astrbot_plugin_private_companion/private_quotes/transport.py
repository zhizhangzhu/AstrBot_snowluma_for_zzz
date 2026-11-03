"""Observe actual OneBot message IDs and attach a validated quote at send time."""
from contextvars import ContextVar
import asyncio
from copy import copy
import functools
import inspect
import weakref

from ..logging_util import get_module_logger
from .context import summary
from .store import store

logger = get_module_logger(__name__)
BRIDGE = '_private_companion_quote_transport_v1'


def observe_receipt(active, action, params, result):
    """Metadata recording must never retry a successfully delivered message."""
    if not active or action not in ('send_private_msg', 'send_msg'):
        return
    owner_ref, binding = active
    owner = owner_ref()
    if owner is None or not isinstance(result, dict):
        return
    if str(params.get('user_id') or '') != binding['user'] or params.get('group_id'):
        return
    if str(params.get('self_id') or binding['bot']) != binding['bot']:
        return
    raw = result.get('data', result)
    if (not isinstance(raw, dict) or raw.get('message_id') is None
            or result.get('status') == 'failed' or str(result.get('retcode', 0)) != '0'):
        return
    message = params.get('message', [])
    texts, kinds = [], []
    for part in message if isinstance(message, list) else []:
        if not isinstance(part, dict):
            continue
        kind, data = part.get('type'), part.get('data', {})
        if not isinstance(data, dict):
            continue
        if kind == 'text':
            texts.append(str(data.get('text') or ''))
        elif kind not in ('reply', 'at'):
            kinds.append(str(kind))
    text = ''.join(texts) or ''.join('[' + k + ']' for k in kinds)
    if text.strip():
        store(owner).save(binding, str(raw['message_id']), binding['bot'],
                          summary(owner, text), kind=','.join(kinds) or 'text')


def install(host, event):
    client = getattr(event, 'bot', None)
    if client is None:
        return None
    namespace = vars(client)
    bridge = namespace.get(BRIDGE)
    if bridge is not None:
        # The client outlives plugin hot reloads; use the latest observer without
        # stacking wrappers or retaining the old plugin instance.
        bridge['observer'] = observe_receipt
        return bridge
    original = getattr(client, 'call_action', None)
    if not callable(original):
        return None
    bridge = {'active': ContextVar('private_quote_send', default=None),
              'original': original, 'observer': observe_receipt}

    @functools.wraps(original)
    async def observed(action, **params):
        active = bridge['active'].get()
        result = await original(action, **params)
        try:
            bridge['observer'](active, action, params, result)
        except Exception as exc:
            logger.warning('私聊引用发送回执记录失败: %s', type(exc).__name__)
        return result

    namespace[BRIDGE] = bridge
    client.call_action = observed
    return bridge


async def prepare_message(host, event, state, message):
    from .service import valid_target
    original_chain = list(getattr(message, 'chain', []) or [])
    components = list(original_chain)
    selected = state.get('choice')
    current_mid = str(getattr(host, '_event_message_id', lambda e: '')(event) or '')
    # A generated historical reply component is not authority. Retain only a
    # direct reply to this inbound message; selected history is re-added below.
    components = [c for c in components if not getattr(host, '_is_reply_component', lambda c: False)(c)
                  or current_mid and str(getattr(host, '_extract_reply_message_id', lambda c: '')(c)) == current_mid]
    visible = (bool(message.strip()) if isinstance(message, str) else any(
        c.__class__.__name__.lower() == 'plain' and str(getattr(c, 'text', '')).strip()
        for c in components))
    quoted = False
    if (selected and not state['sent'] and not state.get('text_sent') and visible
            and hasattr(message, 'chain') and await valid_target(host, event, state, selected)):
        from .relevance import supports_reply
        reply_text = '\n'.join(str(getattr(c, 'text', '') or '') for c in components
                               if c.__class__.__name__.lower() == 'plain')
        if not supports_reply(host, event, state, selected, reply_text):
            state['choice'] = None
            if components != original_chain:
                message = copy(message)
                message.chain = components
            return message, visible, False
        component = host._make_reply_component(selected['mid'], event=event)
        if component is not None:
            components = [c for c in components if not getattr(host, '_is_reply_component', lambda c: False)(c)]
            components.insert(0, component)
            quoted = True
    if components != original_chain:
        message = copy(message)
        message.chain = components
    return message, visible, quoted


async def send(host, event, original, message, args, kwargs):
    """Late binding survives segmentation/TTS decorators; dispatch exactly once."""
    from .service import settings
    from .context import binding_for
    state = getattr(event, '_private_quote_request', None)
    if not state:
        return await original(message, *args, **kwargs), message
    task=asyncio.current_task()
    if state.get('_sending_task') is task:
        # Legacy nested send wrappers must not reacquire their own quote lock.
        # The outer wrapper already validated the quote and observes delivery.
        return await original(message,*args,**kwargs),message
    bridge = token = persona_token = None
    enabled = False
    try:
        try:
            activate = getattr(host, '_activate_persona_for_event_context', None)
            if callable(activate):
                activation = activate(event)
                if inspect.isawaitable(activation):
                    activation = await activation
                persona_token, _ = activation
            enabled = settings(host)['enabled']
            current_binding = await binding_for(host, event) if enabled else None
            bridge = install(host, event) if current_binding == state['binding'] else None
            if bridge:
                try:
                    owner_ref = weakref.ref(host)
                except TypeError:
                    owner_ref = lambda: host  # task-local; released in finally
                token = bridge['active'].set((owner_ref, dict(state['binding'])))
        except Exception as exc:
            logger.warning('私聊引用发送准备失败，保留普通回复: %s', type(exc).__name__)
        if not enabled:
            return await original(message, *args, **kwargs), message
        async with state['lock']:
            state['_sending_task']=task
            try:
                visible = quoted = False
                try:
                    message, visible, quoted = await prepare_message(host, event, state, message)
                except Exception as exc:
                    logger.warning('私聊引用校验失败，保留普通回复: %s', type(exc).__name__)
                # Dispatch once; persistence/metadata failures never resend.
                result = await original(message, *args, **kwargs)
                if result is not False:
                    if visible:state['text_sent'] = True
                    if quoted:state['sent'] = True
                return result, message
            finally:
                if state.get('_sending_task') is task:state.pop('_sending_task',None)
    finally:
        if bridge and token is not None:
            bridge['active'].reset(token)
        if persona_token is not None:
            host._deactivate_persona_for_event(persona_token)
