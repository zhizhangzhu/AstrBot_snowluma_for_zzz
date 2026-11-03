import pytest
from plugins.astrbot_plugin_desktop_pet.avatar import AvatarCache, read_avatar

@pytest.mark.asyncio
async def test_avatar_stream_reads_all_chunks_and_rejects_oversize():
    class Stream:
        def __init__(self,chunks):self.chunks=chunks
        async def iter_chunked(self,size):
            for chunk in self.chunks:yield chunk
    assert await read_avatar(Stream([b'first',b'second']))==b'firstsecond'
    with pytest.raises(ValueError,match='avatar_size'):
        await read_avatar(Stream([b'x'*(256*1024),b'y'*(256*1024+1)]))

@pytest.mark.asyncio
async def test_avatar_cache_is_scoped_refreshes_and_keeps_last_good_image(tmp_path):
    calls=[];current=b'\x89PNG\r\n\x1a\nfirst'
    async def fetch(qq):
        calls.append(qq)
        if current is None:raise OSError('offline')
        return current+qq.encode()
    cache=AvatarCache(tmp_path,fetch=fetch,ttl=100)
    first=await cache.get('12345');await cache.get('12345')
    assert len(calls)==1
    assert (await cache.get('67890'))['revision']!=first['revision']
    current=b'\x89PNG\r\n\x1a\nchanged';cache.at.clear()
    changed=await cache.get('12345');assert changed['revision']!=first['revision']
    current=None;cache.at.clear()
    assert (await cache.get('12345'))['revision']==changed['revision']
    assert await cache.get('../other')=={}
