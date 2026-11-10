"""Final, transport-independent companion chat filtering and segmentation.

No platform fallback, model calls or retry lives here. Already planned chunks
are marked on components so decorating hooks and send wrappers are idempotent.
"""
from copy import copy
import asyncio
from .persona_config import runtime_persona_setting
from .segmented_message import (plan_component_chunks, component_strategies_from_owner,
    component_order_from_owner, component_kind)
from astrbot.api.message_components import Plain

MARK = '_private_companion_outbound_rules_v1'


def mark(chunks):
    for chain in chunks:
        for component in chain:
            if isinstance(component, Plain):
                object.__setattr__(component, MARK, True)
    return chunks


def plan(owner, event, chain, *, umo=''):
    """Use the same persona-scoped planner as decorating-result, for direct sends."""
    chain = [copy(c) if isinstance(c, Plain) else c for c in chain]
    redactor = getattr(owner, '_redact_outbound_chain_secrets', None)
    if callable(redactor):
        chain, _ = redactor(chain)
    hit = getattr(owner, '_forbidden_recall_hit', None)
    extract = getattr(owner, '_chain_text_for_forbidden_recall', None)
    if callable(hit) and callable(extract) and hit(extract(chain)):
        return []
    plains = [c for c in chain if isinstance(c, Plain)]
    if not plains or all(getattr(c, MARK, False) for c in plains):
        return [chain] if chain else []
    enabled = runtime_persona_setting(owner, 'enable_segmented_proactive_reply', False)
    allow = getattr(owner, '_segmented_scope_allows_event' if event else '_segmented_scope_allows_umo', None)
    if not enabled or callable(allow) and not allow(event if event else umo):
        return [chain]
    platform = getattr(owner, '_segmented_platform_allows', None)
    if callable(platform) and not platform(**({'event': event} if event else {'umo': umo})):
        return [chain]
    if event is not None:
        planner = getattr(owner, '_segment_llm_reply_chain', None)
        if not callable(planner): return [chain]
        state = getattr(event, '_private_quote_request', None)
        if state and not state.get('text_sent'):
            state['reply_text'] = ''.join(c.text for c in plains)
        chunks, _, _ = planner(event, chain)
    else:
        splitter = getattr(owner, '_split_proactive_text', None)
        controlled = getattr(owner, '_split_llm_controlled_text_for_event', None)
        if not callable(splitter): return [chain]
        def split(text):
            if callable(controlled) and runtime_persona_setting(owner, 'enable_llm_controlled_segmenting', False):
                return controlled(None, text, umo=umo)
            return splitter(text, umo=umo)
        chunks, _, _, _ = plan_component_chunks(chain, plain_type=Plain, split_text=split,
            strategies=component_strategies_from_owner(owner),
            component_order=component_order_from_owner(owner), classify=component_kind)
    # Filtering is also checked after a transform, before any segment is sent.
    if callable(hit) and callable(extract) and any(hit(extract(c)) for c in chunks):
        return []
    return mark(chunks)


async def send_event(owner, event, message, dispatch, *, umo=''):
    """Dispatch planned chunks once, retaining confirmed partial delivery evidence."""
    chain = list(getattr(message, 'chain', []) or [])
    if not chain:
        if not isinstance(message, str):
            return {'result': await dispatch(message), 'chains': [], 'error': None}
        chain = [Plain(message)]
    chunks = plan(owner, event, chain, umo=umo or str(getattr(event, 'unified_msg_origin', '') or ''))
    confirmed = []
    result = False
    for index, chunk in enumerate(chunks):
        if index:
            interval = getattr(owner, '_calc_segmented_proactive_interval', None)
            if callable(interval):
                await asyncio.sleep(await interval(''.join(c.text for c in chunks[index-1] if isinstance(c, Plain)),
                    **({'event': event} if event is not None else {'umo': umo})))
        if hasattr(message, 'chain'):
            outbound = copy(message);outbound.chain = chunk
        elif callable(getattr(event, 'chain_result', None)):
            outbound = event.chain_result(chunk)
        elif all(isinstance(c, Plain) for c in chunk):
            outbound = ''.join(c.text for c in chunk)
        else:
            from astrbot.core.message.message_event_result import MessageChain
            outbound = MessageChain(chunk)
        try:
            result = await dispatch(outbound)
        except Exception as exc:
            return {'result': False, 'chains': confirmed, 'error': exc}
        if result is False:
            return {'result': False, 'chains': confirmed, 'error': None}
        confirmed.append(chunk)
    return {'result': result, 'chains': confirmed, 'error': None}
