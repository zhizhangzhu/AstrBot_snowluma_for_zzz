"""Late callbacks from an unloaded terminal must leave ordinary QQ handling available."""
from types import SimpleNamespace
import pytest
from astrbot_plugin_desktop_pet.main import DesktopPetPlugin
from astrbot_plugin_desktop_pet.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('bot', ['sample-bot-a', 'sample-bot-b'])
async def test_retired_instance_does_not_query_closed_storage_or_redirect_a_normal_turn(tmp_path, bot):
    plugin=object.__new__(DesktopPetPlugin)
    plugin.store=Store(tmp_path/'terminal.sqlite3')
    plugin.store.close()
    plugin.hub=SimpleNamespace(stopping=True)
    event=SimpleNamespace(unified_msg_origin=f'{bot}:FriendMessage:partner')
    assert await plugin.authorized_binding(event) is None
    assert await plugin.authorized_peer(event) is None
    assert plugin.proactive_preference(None,event.unified_msg_origin)=='wait'
    assert plugin.real_schedule_capabilities_for_scope({})==[]
    assert (await plugin.run_real_schedule_activity('activity',{}))['verified'] is False
