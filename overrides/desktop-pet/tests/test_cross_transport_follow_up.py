"""Load the patched AstrBot module in a separate test process."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('active,incoming', [('aiocqhttp', 'desktop_pet'), ('desktop_pet', 'aiocqhttp')])
def test_cross_transport_inputs_keep_a_separate_reply_target(active, incoming):
    name = 'astrbot.core.pipeline.process_stage.desktop_follow_up_contract'
    spec = importlib.util.spec_from_file_location(name, Path('/tmp/desktop-follow-up-test.py'))
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    origin = 'sample:FriendMessage:person'
    def event(platform):
        return SimpleNamespace(unified_msg_origin=origin, get_sender_id=lambda: 'person',
            get_platform_name=lambda: platform, get_extra=lambda *args: False)
    class Runner:
        run_context = SimpleNamespace(context=SimpleNamespace(event=event(active)))
        def follow_up(self, **kwargs):
            raise AssertionError('A cross-transport message must wait for its own turn')
    module._ACTIVE_AGENT_RUNNERS[origin] = Runner()
    assert module.try_capture_follow_up(event(incoming)) is None
