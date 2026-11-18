import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def seam(name):
    path = Path(__file__).resolve().parents[2]/'overrides/desktop-pet/terminal_integration.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_passive_messages_keep_qq_while_only_proactive_delivery_routes_to_desktop():
    module = seam('terminal_seam_a')
    deliveries = []
    class Terminal:
        async def deliver_proactive(self, owner, origin, chain):
            deliveries.append((origin, chain)); return True
    terminal = Terminal()
    owner = SimpleNamespace(context=SimpleNamespace(get_all_stars=lambda: [SimpleNamespace(
        activated=True, root_dir_name='astrbot_plugin_desktop_pet', star_cls=terminal)]))
    @module.proactive_delivery
    async def proactive(host, origin):
        await asyncio.sleep(0)
        return await module.deliver(host, origin, ['proactive'])
    async def passive():
        return await module.deliver(owner, 'qq:FriendMessage:person', ['passive'])
    routed, ordinary = await asyncio.gather(proactive(owner, 'qq:FriendMessage:person'), passive())
    assert routed is True and ordinary is None
    assert deliveries == [('qq:FriendMessage:person', ['proactive'])]


@pytest.mark.asyncio
async def test_reloaded_seam_keeps_context_and_failed_activity_releases_it():
    old, new = seam('terminal_seam_old'), seam('terminal_seam_new')
    assert old._runtime.proactive_origin is new._runtime.proactive_origin
    @old.proactive_delivery
    async def activity(owner, origin):
        assert new._runtime.proactive_origin.get() == origin
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError): await activity(None, 'qq:FriendMessage:person')
    assert new._runtime.proactive_origin.get() == ''


def test_missing_optional_terminal_preserves_existing_poke_behavior():
    module = seam('terminal_seam_no_plugin')
    owner = SimpleNamespace(context=SimpleNamespace(get_all_stars=lambda: []))
    assert module.allow_poke(owner, 'qq:FriendMessage:person') is True
