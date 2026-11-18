"""A paired terminal's body reference, kept distinct from user photos and screen capture."""
import base64
import hashlib
import io
import re
import struct
from functools import lru_cache

from .protocol import ProtocolError

MAX_BYTES = 160 * 1024
MARKER = '[Desktop body reference sha256='

@lru_cache(maxsize=16)
def _verified_png(encoded, digest, width, height):
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
        raise ProtocolError('portrait_size')
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError()
        if len(raw)<33 or not raw.startswith(b'\x89PNG\r\n\x1a\n') or raw[12:16]!=b'IHDR' or struct.unpack('>II',raw[16:24])!=(width,height):
            raise ValueError()
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or image.size != (width, height):
                raise ValueError()
            image.verify()
    except (ValueError, OSError) as error:
        raise ProtocolError('invalid_portrait_image') from error
    return raw

def validate_portrait(value):
    if not isinstance(value, dict) or value.get('mime') != 'image/png':
        raise ProtocolError('invalid_portrait')
    width, height = value.get('width'), value.get('height')
    if any(type(n) is not int or not 1 <= n <= 512 for n in (width, height)):
        raise ProtocolError('portrait_dimensions')
    digest = value.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ProtocolError('portrait_digest')
    if not isinstance(value.get('base64'), str):
        raise ProtocolError('invalid_portrait')
    _verified_png(value['base64'], digest, width, height)
    for field in ('figure_id', 'figure_version', 'scheme'):
        if not isinstance(value.get(field), str) or not 1 <= len(value[field]) <= 200:
            raise ProtocolError('portrait_look')
    return {k: value[k] for k in ('mime','base64','sha256','width','height','figure_id','figure_version','scheme')}

def portrait_summary(peer):
    world = next((w for w in peer.catalog['worlds'] if w['id'] == 'desktop-pet' and w.get('supported') is True), {})
    picture = world.get('portrait')
    if not picture:
        return {'available': False}
    return {'available': True, **{k: picture[k] for k in ('sha256','figure_id','figure_version','scheme','width','height')}}

def _part(value):
    return value.model_dump() if hasattr(value, "model_dump") else value

def _is_managed_pair(parts, i):
    if i+1 >= len(parts):
        return False
    a,b=_part(parts[i]),_part(parts[i+1])
    if not isinstance(a,dict) or not isinstance(b,dict):
        return False
    text=a.get('text','')
    return (a.get('type') == 'text' and isinstance(text,str) and text.startswith(MARKER)
            and b.get('type') == 'image_url'
            and isinstance(b.get('image_url'),dict)
            and str(b['image_url'].get('url','')).startswith('data:image/png;base64,'))

def attach_self_portrait(request, peer):
    world = next((w for w in peer.catalog['worlds'] if w['id'] == 'desktop-pet' and w.get('supported') is True), {})
    picture = world.get('portrait')
    if not picture:
        return False
    # Catalog validation verified bytes once; never fetch a terminal-supplied URL or local path.
    digest=picture['sha256'];url='data:image/png;base64,'+picture['base64']
    original=list(getattr(request,'extra_user_content_parts',None) or [])
    parts=[];i=0
    while i<len(original):
        if _is_managed_pair(original,i):i+=2
        else:parts.append(original[i]);i+=1
    label=(f'{MARKER}{digest}] Current figure={picture["figure_id"]}; outfit={picture["scheme"]}. '
           'This rendered portrait shows YOUR OWN desktop body in its current selected outfit, '
           'not a person sending a photo or a screenshot of the user’s desktop. '
           'Use it together with the current body description. A changed outfit supersedes earlier body references; '
           'the image establishes appearance, not successful movement, screen access or tool execution. '
           'User-supplied images remain separate; answer questions about their attachments rather than this reference unless they ask about your body.')
    # Reuse an actual matching visual already in this session's input, not a text-only recollection.
    for message in getattr(request,'contexts',None) or []:
        content=message.get('content') if isinstance(message,dict) else None
        if isinstance(content,list) and any(_is_managed_pair(content,j) and digest in _part(content[j])['text']
                                          and _part(content[j+1])['image_url']['url']==url for j in range(len(content))):
            request.extra_user_content_parts=parts
            return True
    from astrbot.core.agent.message import TextPart, ImageURLPart
    request.extra_user_content_parts=parts+[
        TextPart(text=label),
        ImageURLPart(image_url=ImageURLPart.ImageURL(url=url)),
    ]
    return True
