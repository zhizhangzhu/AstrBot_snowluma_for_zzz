"""Exercise the installed meme sender's actual delivery method without sending anything."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

@pytest.mark.asyncio
@pytest.mark.parametrize('failed',[False,True])
async def test_desktop_meme_never_falls_back_to_qq(failed):
    path=Path(__file__).parents[2]/'data/astrbot/plugins/meme_manager/mixins/event_handlers.py'
    if not path.exists():pytest.skip('Installed meme plugin is required for deployment acceptance')
    tree=ast.parse(path.read_text())
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_send_meme_image')
    import logging
    namespace={'__name__':'probe','logger':logging.getLogger('probe'),'AstrMessageEvent':object,'Image':object,'MessageChain':lambda values:values}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),namespace)
    sender=SimpleNamespace(_ensure_image_send_format=AsyncMock(side_effect=lambda image:image),context=SimpleNamespace(send_message=AsyncMock()))
    event=SimpleNamespace(get_platform_name=lambda:'desktop_pet',send=AsyncMock(side_effect=RuntimeError('desktop disconnected') if failed else None))
    image=object()
    if failed:
        with pytest.raises(RuntimeError,match='desktop disconnected'):await namespace['_send_meme_image'](sender,event,image)
    else:await namespace['_send_meme_image'](sender,event,image)
    event.send.assert_awaited_once_with([image])
    sender.context.send_message.assert_not_awaited()
