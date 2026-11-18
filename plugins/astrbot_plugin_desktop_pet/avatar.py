"""QQ avatar snapshots for verified bindings; independent of model inference."""
import asyncio
import base64
import hashlib
import io
from pathlib import Path
import re
import time
import aiohttp

async def read_avatar(stream):
    chunks=[];size=0
    async for chunk in stream.iter_chunked(65536):
        size+=len(chunk)
        if size>512*1024:raise ValueError('avatar_size')
        chunks.append(chunk)
    if not size:raise ValueError('avatar_size')
    return b''.join(chunks)

class AvatarCache:
    def __init__(self,root,fetch=None,ttl=300):
        self.root=Path(root);self.fetch=fetch or self._fetch;self.ttl=ttl
        self.at={};self.locks={}

    async def _fetch(self,qq):
        from PIL import Image
        url=f'https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640&t={int(time.time()//self.ttl)}'
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5),trust_env=True) as session:
            async with session.get(url,allow_redirects=False) as response:
                response.raise_for_status()
                raw=await read_avatar(response.content)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width*image.height>4_000_000:raise ValueError('avatar_dimensions')
                image.thumbnail((256,256));out=io.BytesIO();image.convert('RGBA').save(out,format='PNG')
                return out.getvalue()
        except Image.DecompressionBombError as exc:raise ValueError('avatar_dimensions') from exc

    async def get(self,qq):
        qq=str(qq)
        if not re.fullmatch(r'[1-9]\d{4,14}',qq):return {}
        path=self.root/(qq+'.png')
        async with self.locks.setdefault(qq,asyncio.Lock()):
            if qq not in self.at or time.monotonic()-self.at[qq]>self.ttl:
                self.at[qq]=time.monotonic()
                try:
                    raw=await self.fetch(qq)
                    if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)>512*1024:raise ValueError('avatar_format')
                    self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
                    temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.chmod(0o600);temporary.replace(path)
                except (OSError,ValueError,TimeoutError,aiohttp.ClientError):pass
            if not path.exists():return {}
            raw=path.read_bytes()
            return {'source':'qq','bot_id':qq,'mime':'image/png','base64':base64.b64encode(raw).decode(),
                    'revision':hashlib.sha256(raw).hexdigest()[:20]}
