import asyncio
import ssl
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest
import pytest_asyncio

from plugins.astrbot_plugin_desktop_pet.hub import Hub, DeliveryUnknown
from plugins.astrbot_plugin_desktop_pet.protocol import Binding, ProtocolError, catalog, decode, frame, route
from plugins.astrbot_plugin_desktop_pet.store import Store
from plugins.astrbot_plugin_desktop_pet.manage import certificates


@pytest_asyncio.fixture
async def terminal(tmp_path):
    certificates(tmp_path, "127.0.0.1")
    store = Store(tmp_path / 'devices.sqlite3')
    a = Binding('device-a', 'persona-a', 'bot-a', 'user-a', 'adapter-a')
    b = Binding('device-b', 'persona-b', 'bot-b', 'user-b', 'adapter-b')
    tokens = {x.device_id: store.pair(x) for x in (a, b)}
    events = []
    async def receive(binding, message): events.append((binding, message))
    hub = Hub(store, receive)
    app = web.Application()
    app.router.add_get('/terminal/v1', hub.socket)
    runner = web.AppRunner(app)
    await runner.setup()
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(tmp_path/'server.pem', tmp_path/'server.key')
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=tls)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    trust = ssl.create_default_context(cafile=str(tmp_path/'ca.pem'))
    async with aiohttp.ClientSession() as session:
        async def connect(binding=a, token=None):
            socket = await session.ws_connect(f'wss://127.0.0.1:{port}/terminal/v1', ssl=trust,
                         headers={'Authorization': 'Bearer '+(token or tokens[binding.device_id])})
            welcome = await socket.receive_json()
            assert welcome['payload']['device_id'] == binding.device_id
            await socket.send_json(frame('hello', 'h', {'ready': True, 'body': True, 'visible': True}))
            await socket.send_json(frame('catalog', 'cat', {'worlds': [{'id': 'desktop-pet', 'supported': True,
                                  'tools': [{'name': 'pet_say', 'description': 'Speak', 'parameters': {'type': 'object'}}]}]}))
            await socket.send_json(frame('ping', 'barrier', {}))
            assert (await socket.receive_json())['type'] == 'pong'
            return socket
        yield hub, store, connect, a, b, events, session, port
        await hub.stop()
    await runner.cleanup()
    store.close()


@pytest.mark.asyncio
async def test_wss_identity_is_bound_and_retransmissions_are_not_redelivered(terminal):
    hub, store, connect, a, b, events, *_ = terminal
    socket = await connect()
    payload = {'world': 'desktop-pet', 'text': 'Hello', 'user_id': b.user_id, 'persona_id': b.persona_id}
    for _ in range(2):
        await socket.send_json(frame('event', 'same-id', payload))
        assert (await socket.receive_json())['payload']['status'] == 'queued'
    assert len(events) == 1 and events[0][0] == a
    await socket.send_json(frame('event', 'another-id', payload))
    await socket.receive_json()
    assert len(events) == 2
    assert hub.for_origin(b.conversation_origin) is None


@pytest.mark.asyncio
async def test_server_can_initiate_tool_and_reports_actual_receipt(terminal):
    hub, store, connect, a, *_ = terminal
    socket = await connect()
    task = asyncio.create_task(hub.call(a, 'desktop-pet', 'pet_say', {'script': 'hello'}, call_id='call-a'))
    request = await socket.receive_json()
    assert request['type'] == 'call' and request['payload']['arguments'] == {'script': 'hello'}
    assert not task.done()
    await socket.send_json(frame('result', request['id'], {'status': 'submitted', 'text': 'queued in the pet'}))
    assert (await task)['status'] == 'submitted'
    assert (await hub.call(a, 'desktop-pet', 'pet_say', {}, call_id='call-a'))['status'] == 'submitted'


@pytest.mark.asyncio
async def test_disconnect_marks_inflight_unknown_and_keeps_other_persona_working(terminal):
    hub, store, connect, a, b, *_ = terminal
    socket, other = await connect(a), await connect(b)
    task = asyncio.create_task(hub.call(a, 'desktop-pet', 'pet_say', {}, call_id='call-a'))
    await socket.receive_json()
    await socket.close()
    with pytest.raises(DeliveryUnknown): await task
    assert store.get(a.device_id, 'call-a')['state'] == 'unknown'
    assert hub.preference(b.conversation_origin) == 'desktop'
    await other.send_json(frame('ping', 'still-working', {}))
    assert (await other.receive_json())['type'] == 'pong'


@pytest.mark.asyncio
async def test_wrong_token_and_untrusted_certificate_rejected(terminal):
    hub, store, connect, a, b, events, session, port = terminal
    with pytest.raises(aiohttp.WSServerHandshakeError): await connect(a, 'wrong-token')
    with pytest.raises(aiohttp.ClientConnectorCertificateError):
        await session.ws_connect(f'wss://127.0.0.1:{port}/terminal/v1', headers={'Authorization': 'Bearer wrong'})


@pytest.mark.asyncio
async def test_revocation_disables_route_and_new_connections(terminal):
    hub, store, connect, a, *_ = terminal
    await connect(a)
    assert hub.preference(a.conversation_origin) == 'desktop'
    store.revoke(a.device_id)
    assert hub.preference(a.conversation_origin) == 'qq'
    with pytest.raises(aiohttp.WSServerHandshakeError): await connect(a)


@pytest.mark.asyncio
async def test_dnd_survives_disconnect_without_suppressing_other_persona(terminal):
    hub, store, connect, a, b, *_ = terminal
    socket = await connect(a)
    other = await connect(b)
    await socket.send_json(frame('state', 'quiet', {'ready': True, 'visible': True, 'body': True, 'dnd': True}))
    await socket.send_json(frame('ping', 'barrier', {})); await socket.receive_json()
    await socket.close()
    assert hub.preference(a.conversation_origin) == 'wait'
    assert hub.preference(b.conversation_origin) == 'desktop'
    assert not other.closed


@pytest.mark.asyncio
async def test_reload_shutdown_settles_inflight_call_before_database_retirement(terminal):
    hub, store, connect, a, *_ = terminal
    socket = await connect(a)
    task = asyncio.create_task(hub.call(a, 'desktop-pet', 'pet_say', {}, call_id='before-reload'))
    await socket.receive_json()
    await asyncio.wait_for(hub.stop(), 2)
    with pytest.raises(DeliveryUnknown): await task
    assert store.get(a.device_id, 'before-reload')['state'] == 'unknown'
    assert not hub.inflight


@pytest.mark.parametrize('state,expected', [
    (None, 'qq'), ({'ready': False}, 'qq'),
    ({'ready': True, 'body': True, 'visible': True}, 'desktop'),
    ({'ready': True, 'body': True, 'visible': True, 'dnd': True}, 'wait'),
    ({'ready': True, 'body': True, 'visible': False}, 'wait'),
    ({'ready': True, 'body': True, 'visible': True, 'locked': True}, 'wait'),
])
def test_routing_waits_instead_of_bypassing_quiet_state(state, expected):
    assert route(state) == expected


def test_store_persists_deduplication_and_pairing_revokes_previous_device(tmp_path):
    path = tmp_path/'state.sqlite3'
    store = Store(path)
    binding = Binding('old-device', 'persona', 'bot', 'user', 'qq')
    old_token = store.pair(binding)
    assert store.claim(binding.device_id, 'event-1', 'event', {'text': 'a'})
    store.close()
    store = Store(path)
    assert store.authenticate(old_token) == binding
    assert not store.claim(binding.device_id, 'event-1', 'event', {'text': 'a'})
    store.pair(Binding('new-device', 'persona', 'bot', 'user', 'qq'))
    assert store.authenticate(old_token) is None
    store.close()


@pytest.mark.parametrize('raw', ['null', '[]', '{}', '{"v":2}', '{"v":1,"type":"event","id":"x","payload":[]}'])
def test_invalid_wire_frames_fail_at_boundary(raw):
    with pytest.raises(ProtocolError): decode(raw)


def test_gif_is_transferred_as_one_unchanged_dynamic_media_attachment():
    from plugins.astrbot_plugin_desktop_pet.media import pack, unpack, mime_for
    raw = b'GIF89a' + b'original animation bytes'
    media = pack(raw, mime_for(raw), 'animation.gif')
    received = unpack([media])
    assert len(received) == 1 and received[0][1] == raw
    assert received[0][0]['mime'] == 'image/gif'


@pytest.mark.asyncio
async def test_proactive_call_rechecks_persisted_dnd_before_sending(terminal):
    hub, store, connect, a, *_ = terminal
    socket = await connect()
    store.set_dnd(a.device_id, True)
    with pytest.raises(DeliveryUnknown, match='proactive_deferred'):
        await hub.call(a, 'desktop-pet', 'pet_say', {'script': 'Hello'}, proactive=True)
    assert not store.outstanding(a.device_id)
    await socket.send_json(frame('ping', 'no-call-was-sent', {}))
    assert (await socket.receive_json())['type'] == 'pong'


def test_ip_certificate_renewal_retains_the_paired_ca(tmp_path):
    from cryptography import x509
    certificates(tmp_path, '127.0.0.1')
    ca = (tmp_path/'ca.pem').read_bytes()
    old = (tmp_path/'server.pem').read_bytes()
    certificates(tmp_path, '127.0.0.1', renew=True)
    assert (tmp_path/'ca.pem').read_bytes() == ca
    current = x509.load_pem_x509_certificate((tmp_path/'server.pem').read_bytes())
    assert current.serial_number != x509.load_pem_x509_certificate(old).serial_number
    current.verify_directly_issued_by(x509.load_pem_x509_certificate(ca))
    assert len(list(tmp_path.glob('server.*.backup-*'))) == 2


@pytest.mark.asyncio
async def test_sleep_disconnect_waits_until_resume_and_normal_exit_allows_future_qq_delivery(terminal):
    hub, store, connect, a, *_ = terminal
    socket = await connect()
    await socket.send_json(frame('state', 'sleep', {'ready': True, 'body': True, 'visible': True, 'suspended': True}))
    await socket.send_json(frame('ping', 'sleep-recorded', {})); await socket.receive_json()
    await socket.close()
    assert hub.preference(a.conversation_origin) == 'wait'
    socket = await connect()
    assert hub.preference(a.conversation_origin) == 'desktop'
    await socket.send_json(frame('state', 'normal-exit', {'ready': False, 'body': False, 'visible': False}))
    await socket.send_json(frame('ping', 'exit-recorded', {})); await socket.receive_json()
    await socket.close()
    assert hub.preference(a.conversation_origin) == 'qq'
    assert not store.quiet(a.device_id)


@pytest.mark.asyncio
async def test_late_uncertain_result_cannot_erase_a_confirmed_completion(terminal):
    hub, store, connect, a, *_ = terminal
    socket = await connect()
    task = asyncio.create_task(hub.call(a, 'desktop-pet', 'pet_say', {'script':'Hello'}, call_id='confirmed'))
    await socket.receive_json()
    await socket.send_json(frame('result','confirmed',{'status':'completed','text':'The renderer confirmed it.'}))
    assert (await task)['status']=='completed'
    await socket.send_json(frame('result','confirmed',{'status':'unknown','text':'A late disconnect.'}))
    await socket.send_json(frame('ping','completion-barrier',{})); await socket.receive_json()
    record=store.get(a.device_id,'confirmed')
    assert record['state']=='completed' and record['payload']['text']=='The renderer confirmed it.'

@pytest.mark.asyncio
async def test_remote_cognition_can_receive_nested_tool_results_without_blocking_socket(terminal):
    import contextvars
    hub, store, connect, binding, *_ = terminal
    marker=contextvars.ContextVar('native_execution',default='absent')
    socket=await connect()
    async def remote(actual,kind,payload):
        assert actual==binding and marker.get()=='server-budget'
        result=await hub.call(actual,'desktop-pet','pet_say',{},parent_call_id='outer')
        return {'text':result['status']}
    hub.remote=remote
    token=marker.set('server-budget')
    try:outer=asyncio.create_task(hub.call(binding,'desktop-pet','pet_say',{},call_id='outer'))
    finally:marker.reset(token)
    await socket.receive_json()
    await socket.send_json(frame('cognition','think',{'world':'desktop-pet','brief':'choose','parent_call_id':'outer'}))
    nested=await socket.receive_json()
    assert nested['type']=='call' and nested['payload']['parent_call_id']=='outer'
    await socket.send_json(frame('result',nested['id'],{'status':'completed','text':'done'}))
    answer=await socket.receive_json()
    assert answer['type']=='cognition_result' and answer['payload']['text']=='completed'
    await socket.send_json(frame('result','outer',{'status':'completed','text':'finished'}))
    assert (await outer)['request_id']=='outer'
    # Cognition requests are durable; replay does not repeat an external tool.
    await socket.send_json(frame('cognition','think',{'world':'desktop-pet','brief':'choose','parent_call_id':'outer'}))
    assert (await socket.receive_json())['payload']['text']=='completed'

@pytest.mark.asyncio
async def test_observations_are_not_left_in_the_recoverable_chat_queue(terminal):
    hub,store,connect,binding,*_=terminal
    async def observe(*args):return 'observed'
    hub.receive=observe
    socket=await connect()
    await socket.send_json(frame('event','observation-one',{'world':'desktop-pet','text':'A setting changed.','trigger':'piggyback'}))
    assert (await socket.receive_json())['payload']['status']=='observed'
    assert store.queued_events(binding)==[]
