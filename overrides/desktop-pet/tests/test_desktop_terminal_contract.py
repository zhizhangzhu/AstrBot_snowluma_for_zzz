"""Run in the patched Companion checkout with the desktop plugin on PYTHONPATH."""
import asyncio
from types import SimpleNamespace

import pytest
from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_desktop_pet.adapter import DesktopEvent, DesktopPlatform
from astrbot_plugin_desktop_pet.protocol import Binding


class Owner(CoreStoreMixin):
    def __init__(self, binding):
        self.binding = binding
        self.data = {'users': {binding.user_id: {
            'identity_subject_id': binding.user_id, 'identity_platform_kind': 'onebot',
            'identity_adapter_instance_id': binding.qq_platform_id, 'identity_bot_id': binding.bot_id,
            'nickname': 'Known partner', 'umo': binding.conversation_origin,
        }}}
    def _active_persona_scope(self): return self.binding.persona_id
    def _normalize_private_identity_id(self, value): return str(value)
    def _canonical_private_user_id(self, value): return str(value)


@pytest.mark.parametrize('binding', [Binding('device-a', 'persona-a', 'bot-a', 'user-a', 'qq-a'),
                                     Binding('device-b', 'persona-b', 'bot-b', 'user-b', 'qq-b')])
def test_desktop_keeps_real_transport_and_reuses_the_exact_qq_user_profile(binding):
    owner = Owner(binding)
    context = SimpleNamespace(get_event_queue=lambda: asyncio.Queue())
    platform = DesktopPlatform(context, None)
    event = DesktopEvent(platform, binding, {'id': 'message-one', 'payload': {'world': 'desktop-pet', 'text': 'Hello',
                         'source': 'QQ', 'notice_type': 'poke'}})
    assert event.get_platform_name() == 'desktop_pet'
    assert event.unified_msg_origin == binding.conversation_origin
    assert owner._event_private_user_storage_id(event, binding.user_id) == binding.user_id
    assert len(owner.data['users']) == 1
    assert event.message_obj.raw_message['source'] == 'desktop'
    assert 'notice_type' not in event.message_obj.raw_message
    assert owner.desktop_terminal_contract_version == 1


def test_an_event_cannot_reuse_a_different_personas_profile():
    a = Binding('device-a', 'persona-a', 'bot-a', 'user-a', 'qq-a')
    b = Binding('device-b', 'persona-b', 'bot-b', 'user-b', 'qq-b')
    owner = Owner(b)
    event = DesktopEvent(DesktopPlatform(SimpleNamespace(get_event_queue=lambda: asyncio.Queue()), None), a,
                         {'id': 'message-one', 'payload': {'world': 'desktop-pet', 'text': 'Hello'}})
    with pytest.raises(ValueError, match='desktop_persona_mismatch'):
        owner._private_event_identity_context(event, a.user_id)
