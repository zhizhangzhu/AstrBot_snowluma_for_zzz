"""Run against the patched Memory checkout with the real desktop adapter available."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from .package_bootstrap import bootstrap_package
bootstrap_package()
from astrbot_plugin_desktop_pet.adapter import DesktopEvent, DesktopPlatform
from astrbot_plugin_desktop_pet.protocol import Binding
from astrbot_plugin_memory_companion.core.identity import IdentityResolver
from astrbot_plugin_memory_companion.core.models import MemoryRecord, EntityRef
from astrbot_plugin_memory_companion.core.visibility import VisibilityPolicy
from astrbot_plugin_memory_companion.core.retrieval import RetrievalEngine


@pytest.mark.asyncio
async def test_both_transports_recall_common_private_memory_without_exposing_other_persona():
    binding = Binding('device', 'persona', 'bot', 'partner', 'qq-adapter')
    platform = DesktopPlatform(SimpleNamespace(get_event_queue=lambda: asyncio.Queue()), None)
    event = DesktopEvent(platform, binding, {'id': 'input', 'payload': {'world': 'desktop-pet', 'text': 'Hello'}})
    desktop = await IdentityResolver().resolve_event_context(event)
    qq = replace(desktop, source_platform='aiocqhttp')
    assert desktop.platform == qq.platform == 'aiocqhttp'
    assert desktop.source_platform == 'desktop_pet'
    assert event.get_platform_name() == 'desktop_pet'
    registered = (('qq-adapter', 'bot', 'persona'), ('other-adapter', 'other-bot', 'other-persona'))
    policy = VisibilityPolicy(enable_acl_rules=False, namespace_bindings=registered)
    engine = RetrievalEngine(SimpleNamespace(), policy)
    memory = MemoryRecord(id='common', scope='private', session_id=binding.conversation_origin,
        platform='aiocqhttp', content='The partner prefers a short nickname.',
        owner_bot_id='bot', subject=EntityRef(kind='user', id='partner'), visibility='private_pair',
        lifecycle='stable_memory', metadata={'owner_bot_id': 'bot', 'persona_id': 'persona',
                                           'source_platform': 'desktop_pet'})
    for context in (qq, desktop):
        visible, blocked = await engine.filter_visible_candidates([memory], context)
        assert len(visible) == 1 and not blocked
    stranger = replace(desktop, session_id='other-adapter:FriendMessage:partner', bot_id='other-bot', persona_id='other-persona')
    visible, blocked = await engine.filter_visible_candidates([memory], stranger)
    assert not visible and blocked[0]['content'] == ''


@pytest.mark.asyncio
async def test_shared_identity_rejects_conflicting_persona_evidence():
    binding = Binding('device', 'persona', 'bot', 'partner', 'qq-adapter')
    event = DesktopEvent(DesktopPlatform(SimpleNamespace(get_event_queue=lambda: asyncio.Queue()), None), binding,
        {'id': 'input', 'payload': {'world': 'desktop-pet', 'text': 'Hello'}})
    event.persona_id = 'other-persona'
    with pytest.raises(ValueError, match='desktop_memory_namespace_conflict'):
        await IdentityResolver().resolve_event_context(event)
