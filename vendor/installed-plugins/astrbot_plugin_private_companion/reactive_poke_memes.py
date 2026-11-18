"""Use the existing meme selector for a single, scoped poke reply.

Selection cannot send. Delivery stays on the original OneBot event, and only
confirmed images are retained for the next conversation turn.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.api.provider import LLMResponse
from astrbot.api.message_components import Image, Plain
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType


class SelectionEvent:
    def __init__(self, event, binding, text, kind):
        self._event = event
        self.unified_msg_origin = binding['umo']
        self._result = MessageEventResult(chain=[Plain(text)], result_content_type=ResultContentType.LLM_RESULT)
        fact = (f"用户QQ{binding['sender_id']}戳了BotQQ{binding['bot_id']}"
                if kind == 'reaction' else
                f"BotQQ{binding['bot_id']}刚刚向用户QQ{binding['sender_id']}反戳，QQ已确认；这是为Bot自己的反戳选图")
        from .reactive_poke_context import recent_dialogue
        lines = [f"{row['role']}: {row['text']}" for row in recent_dialogue(binding['history'])]
        previous = str(getattr(event, '_private_companion_last_poke_text', '') or '')
        if previous:
            lines.append('Bot刚刚已发的第一句回复：' + previous)
        lines.append('当前QQ动作事实（非用户文字）：' + fact)
        self._extras = {
            'meme_manager_llm_request_origin': 'plugin',
            'private_companion_persona_id': binding['persona_id'],
            'meme_manager_resolved_persona_id': binding['persona_id'],
            # The same preparation contract used by companion messages: return
            # attachments in the chain, rather than queue an after-send action.
            'meme_manager_companion_prepared': True,
            'meme_manager_emotion_context_lines': lines,
        }

    def __getattr__(self, name):
        return getattr(self._event, name)

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_result(self):
        return self._result

    async def send(self, *args, **kwargs):
        raise RuntimeError('Poke meme selection must not send messages')


class PreparedMeme:
    def __init__(self, manager=None, view=None):
        self.manager = manager
        self.view = view
        self.images = []

    def cleanup(self):
        if self.view is None:
            return
        for path in self.view.get_extra('meme_manager_temp_files') or []:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logger.warning('[戳一戳表情] 临时文件清理失败')
        self.view.set_extra('meme_manager_temp_files', None)

    async def remember(self, owner):
        if not self.images:
            return
        try:
            from .sent_image_context import record_confirmed
            await asyncio.to_thread(record_confirmed, owner, self.view.unified_msg_origin, self.images, self.view)
            receipts = getattr(self.manager, '_get_media_receipts', None)
            if callable(receipts):
                self.view._meme_confirmed_outbound_images = self.images
                await receipts().capture(self.view, outgoing=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[戳一戳表情] 已发图片上下文保存失败，禁止重发 error=%s', type(exc).__name__)


async def prepare(owner, event, binding, text, *, kind):
    prepared = PreparedMeme()
    # The two captions belong to one poke interaction. At most one successful
    # meme attachment is added, even if the return-poke caption runs afterward.
    if getattr(event, '_private_companion_poke_meme_sent', False):
        return prepared
    try:
        lookup = getattr(owner.context, 'get_registered_star', None)
        registration = lookup('meme_manager') if callable(lookup) else None
        manager = getattr(registration, 'star_cls', None)
        if manager is None or not getattr(registration, 'activated', True):
            return prepared
        if (getattr(manager, 'trigger_scope', '') != 'chat_and_plugin_llm'
                or not getattr(manager, 'emotion_llm_enabled', False)):
            return prepared
        view = SelectionEvent(event, binding, text, kind)
        prepared = PreparedMeme(manager, view)
        response = LLMResponse(role='assistant', completion_text=text)
        # This selector rolls the configured probability once and caches the
        # outcome for decorating; do not roll a second probability here.
        await manager._resp_impl(view, response)
        view.get_result().chain = [Plain(response.completion_text)] if response.completion_text else []
        await manager._on_decorating_result_impl(view)
        prepared.images = [item for item in view.get_result().chain if isinstance(item, Image)][:1]
        return prepared
    except asyncio.CancelledError:
        prepared.cleanup()
        raise
    except Exception as exc:
        prepared.cleanup()
        logger.warning('[戳一戳表情] 选图暂不可用，使用原文字 error=%s', type(exc).__name__)
        return PreparedMeme()
