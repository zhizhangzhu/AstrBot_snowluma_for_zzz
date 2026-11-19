"""Deliver a user-requested, prewritten reminder without an LLM at its deadline.

Uses the shared persona-scoped outbound rules and desktop/QQ router. This is
not a proactive-chat candidate and must not wait for social quotas or idle time.
"""
from __future__ import annotations

import time
from astrbot.api.message_components import Plain
from . import terminal_integration
from .outbound_rules import plan


@terminal_integration.proactive_delivery
async def _send(owner, umo, chunk):
    # Deterministic filtering, redaction and segmentation remain in the shared
    # sender. Optional emotion/media generation is intentionally not on the
    # deadline path; the actual text was already written by the persona model.
    return await owner._send_chain_components(umo, chunk, apply_decorating_hooks=False)


async def deliver(host, job, database, started):
    payload = dict(job.payload or {})
    umo = str(payload.get('session') or '')
    persona = str(payload.get('persona_id') or '')
    text = str(payload.get('reminder_text') or '').strip()
    if not umo or not persona or not text or not payload.get('bot_id'):
        raise ValueError('Prepared reminder lacks verified binding or text')
    if persona not in host._scheduler_persona_ids():
        raise PermissionError('Reminder persona is paused or disabled')
    token = host._activate_persona_id(persona)
    try:
        _, platform, bot, _ = host._qzone_watch_binding()
        if str(bot) != str(payload['bot_id']) or not umo.startswith(str(platform)+':'):
            raise PermissionError('Reminder bot binding changed')
        cm = host.context.conversation_manager
        cid = await cm.get_curr_conversation_id(umo)
        conv = await cm.get_conversation(umo, cid) if cid else None
        cfg = host.context.get_config(umo=umo)
        selected = await host.context.persona_manager.resolve_selected_persona(umo=umo,
            conversation_persona_id=getattr(conv, 'persona_id', None), platform_name='aiocqhttp',
            provider_settings=cfg.get('provider_settings', {}))
        if selected[0] != persona or selected[1] is None:
            raise PermissionError('Reminder conversation persona changed')
        chunks = plan(host, None, [Plain(text)], umo=umo)
        if not chunks:
            raise RuntimeError('Reminder blocked by outbound rules')
        receipt = dict(payload.get('delivery_receipt') or {})
        if receipt.get('state') in ('delivered', 'sending', 'uncertain', 'partial'):
            raise RuntimeError('Prior reminder delivery exists; verify receipt before retrying')
        receipt.update(state='sending', started_at=started.isoformat(), delivered_chunks=0)
        payload['delivery_receipt'] = receipt
        await database.update_cron_job(job.job_id, payload=payload)
        try:
            for chunk in chunks:
                if not await _send(host, umo, chunk):
                    raise RuntimeError('Reminder was not accepted by the delivery pipeline')
                receipt['delivered_chunks'] += 1
                receipt['last_delivered_at'] = time.time()
                await database.update_cron_job(job.job_id, payload=payload)
                visible = ''.join(c.text for c in chunk if isinstance(c, Plain))
                if visible:
                    # This method reads the latest conversation under the existing
                    # session lock; it never writes a pre-model history snapshot.
                    archived = await host._archive_proactive_message_to_conversation(
                        user={'umo':umo}, user_prompt='', assistant_response=visible, umo=umo)
                    receipt['history_recorded'] = bool(archived)
            receipt.update(state='delivered', delivered_at=time.time())
            await database.update_cron_job(job.job_id, payload=payload)
        except BaseException as exc:
            receipt.update(state='partial' if receipt['delivered_chunks'] else 'uncertain', error_type=type(exc).__name__)
            await database.update_cron_job(job.job_id, payload=payload)
            raise
    finally:
        host._deactivate_persona_for_event(token)
