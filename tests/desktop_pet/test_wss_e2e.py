"""Python service ↔ TypeScript terminal ↔ native pet World, over real TLS sockets."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import ssl

from aiohttp import web
import pytest

from plugins.astrbot_plugin_desktop_pet.hub import Hub
from plugins.astrbot_plugin_desktop_pet.store import Store
from plugins.astrbot_plugin_desktop_pet.protocol import Binding
from plugins.astrbot_plugin_desktop_pet.manage import certificates


@pytest.mark.asyncio
async def test_real_windows_terminal_protocol_reaches_the_pet_world_and_returns_display_receipt(tmp_path):
    root = Path(__file__).resolve().parents[2]
    client = root/'Coopanion'
    node = os.environ.get('TERMINAL_TEST_NODE')
    if not node or not Path(node).exists() or not (client/'node_modules/tsx').exists():
        pytest.skip('Set TERMINAL_TEST_NODE to Node >=22 and install the client dependencies')
    certificates(tmp_path, '127.0.0.1')
    store = Store(tmp_path/'state.sqlite3')
    binding = Binding('sample-device', 'sample-persona', 'sample-bot', 'sample-user', 'sample-qq')
    token = store.pair(binding)
    received = asyncio.Event()
    captured = []
    async def receive(actual_binding, message):
        captured.append((actual_binding, message)); received.set()
    hub = Hub(store, receive)
    app = web.Application(); app.router.add_get('/terminal/v1', hub.socket)
    runner = web.AppRunner(app); await runner.setup()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(tmp_path/'server.pem', tmp_path/'server.key')
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=tls); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    pairing = tmp_path/'pair.local.json'
    pairing.write_text(json.dumps({'v': 1, 'url': f'wss://127.0.0.1:{port}/terminal/v1',
        'ca': (tmp_path/'ca.pem').read_text(), 'device_id': binding.device_id, 'token': token}))
    process = await asyncio.create_subprocess_exec(node, '--import', 'tsx', 'tests/fixtures/astrbot-terminal-peer.ts',
        str(pairing), str(tmp_path/'client'), cwd=client, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        await asyncio.wait_for(received.wait(), 20)
        assert captured[0][0] == binding
        assert captured[0][1]['payload']['text'] == 'A message from the desktop.'
        result = await hub.call(binding, 'desktop-pet', 'pet_say', {'script': 'A reply from the server.'}, timeout=10)
        assert result['status'] == 'completed'
        assert any(output['text'] == 'A reply from the server.' for output in result['outputs'])
        assert hub.preference(binding.conversation_origin) == 'desktop'
    except Exception:
        if process.returncode is not None:
            _, error = await process.communicate()
            pytest.fail(error.decode()[-4000:])
        raise
    finally:
        if process.returncode is None: process.terminate()
        await asyncio.wait_for(process.communicate(), 10)
        await hub.stop(); await runner.cleanup(); store.close()
