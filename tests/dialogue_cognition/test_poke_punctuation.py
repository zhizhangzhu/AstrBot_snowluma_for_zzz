"""Poke reactions and return pokes share ordinary-chat punctuation on wire/history."""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageEventResult


@pytest.fixture
def modules(monkeypatch):
    root = Path(__file__).resolve().parents[2] / 'vendor/installed-plugins/astrbot_plugin_private_companion'
    package = ModuleType('poke_punctuation_probe')
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    return (importlib.import_module('poke_punctuation_probe.reactive_poke_context'),
            importlib.import_module('poke_punctuation_probe.reactive_poke_memes'))


@pytest.mark.asyncio
@pytest.mark.parametrize('pid,bot,user', [('小小情','2893214983','1005145948'),('初小晴','2510093206','1207862759')])
@pytest.mark.parametrize('kind', ['reaction','poke_back'])
@pytest.mark.parametrize('raw,expected', [
    ('猪猪，戳我啦？我在呢。', '猪猪，戳我啦？我在呢'),
    ('戳回去啦.', '戳回去啦'),
    ('收到啦。”', '收到啦”'),
    ('怎么啦？', '怎么啦？'),
    ('收到啦！', '收到啦！'),
    ('让我想想……', '让我想想……'),
])
async def test_sent_selected_and_archived_text_agree(modules,monkeypatch,raw,expected,kind,pid,bot,user):
    poke, bridge = modules
    binding = dict(umo=f'qq:FriendMessage:{user}',bot_id=bot,sender_id=user,group_id='',
                   persona_id=pid,scope=f'{bot}:{pid}',cid='current',event_id='notice',history=[])
    monkeypatch.setattr(poke,'prepare_turn',AsyncMock(return_value=binding))
    monkeypatch.setattr(poke,'still_current',AsyncMock(return_value=True))
    saved = []
    monkeypatch.setattr(poke,'journal',lambda owner:SimpleNamespace(save=saved.append))
    archive = AsyncMock(return_value=True)
    monkeypatch.setattr(poke,'archive',archive)
    prepared = SimpleNamespace(images=[],remember=AsyncMock(),cleanup=Mock())
    select = AsyncMock(return_value=prepared)
    monkeypatch.setattr(bridge,'prepare',select)
    incoming = SimpleNamespace(send=AsyncMock(return_value=True),
        plain_result=lambda text:MessageEventResult(chain=[Plain(text)]))
    assert await poke.send_reply(SimpleNamespace(),incoming,raw,kind=kind)
    assert incoming.send.call_args.args[0].chain[0].text == expected
    assert select.call_args.args[3] == expected
    assert saved[0]['text'] == archive.call_args.args[1]['text'] == expected
    assert saved[0]['persona_id'] == pid and saved[0]['kind'] == kind
