"""Image I/O must not publish incomplete buffers or mutate consumed generations."""
import asyncio
from contextlib import asynccontextmanager
import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def modules(monkeypatch):
    root = Path(__file__).resolve().parents[2] / 'vendor/installed-plugins/astrbot_plugin_private_companion'
    package = ModuleType('image_buffer_probe')
    package.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    return (importlib.import_module('image_buffer_probe.message_pipeline'),
            importlib.import_module('image_buffer_probe.event_dispatch'))


def make_host(dispatch, images):
    class Host(dispatch.EventDispatchMixin):
        enable_message_debounce = True
        def __init__(self):
            self._data_lock = asyncio.Lock()
            self._semantic_message_buffers = {}
            self.entered = asyncio.Event()
            self.resume = asyncio.Event()
        @asynccontextmanager
        async def _temporarily_release_data_lock(self):
            self._data_lock.release()
            try:
                yield
            finally:
                await self._data_lock.acquire()
        async def _persist_private_inbound_images(self, event, user):
            self.entered.set()
            await self.resume.wait()
            if isinstance(images, Exception):
                raise images
            return images
        def _message_debounce_seconds(self, kind): return 4
        def _semantic_buffer_identity(self, key): return key.rsplit(':', 1)
        def _message_debounce_max_merge_messages(self, kind): return 10
        def _message_debounce_max_wait_seconds(self, kind): return 10
    return Host()


@pytest.mark.asyncio
@pytest.mark.parametrize('persona,user', [('小小情','1005145948'), ('初小晴','1207862759')])
@pytest.mark.parametrize('interleave', ['consume', 'replace'])
async def test_image_storage_reacquires_current_buffer(modules, persona, user, interleave):
    pipeline, dispatch = modules
    host = make_host(dispatch, ['stored.gif'])
    key = f'private:{user}:{user}'
    old = {'first_ts': 100, 'updated_ts': 100, 'messages': []}
    host._semantic_message_buffers[key] = old
    async def prepare():
        async with host._data_lock:
            return await pipeline._prepare_private_image_buffer(host, None, user, key, 101)
    task = asyncio.create_task(prepare())
    await host.entered.wait()
    # No image placeholder has escaped while its file is still being persisted.
    assert old['messages'] == []
    async with host._data_lock:
        host._semantic_message_buffers.pop(key)
        if interleave == 'replace':
            host._note_semantic_message_buffer(key, '补充说明', now=101, wait_seconds=4)
        host.resume.set()
    buffer, images = await asyncio.wait_for(task, 1)
    assert images == ['stored.gif']
    assert buffer is host._semantic_message_buffers[key] and buffer is not old
    assert old['messages'] == [] and 'images' not in old
    if interleave == 'replace':
        assert buffer['messages'][0]['text'] == '补充说明'
    assert '单独发送了一张图片' in buffer['messages'][-1]['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('images', [[], RuntimeError('image download failed')])
async def test_failed_storage_preserves_followup_buffer(modules, images):
    pipeline, dispatch = modules
    host = make_host(dispatch, images)
    key = 'private:1005145948:1005145948'
    host._note_semantic_message_buffer(key, '普通文字', now=101, wait_seconds=4)
    original = host._semantic_message_buffers[key]
    host.resume.set()
    async with host._data_lock:
        if isinstance(images, Exception):
            with pytest.raises(RuntimeError):
                await pipeline._prepare_private_image_buffer(host, None, '1005145948', key, 101)
        else:
            assert await pipeline._prepare_private_image_buffer(host, None, '1005145948', key, 101) == (None, [])
    assert host._semantic_message_buffers[key] is original
    assert len(original['messages']) == 1 and original['messages'][0]['text'] == '普通文字'
    assert not host._data_lock.locked()
