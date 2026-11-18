"""Live, persona-scoped image semantics. Stable content IDs survive renames.

No model calls: reads the current semantic metadata on each lookup. Corrections
and character confirmations become visible without rewriting old messages.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import re

_image_class=None

def attach(image,info):
    """Keep annotations in Pydantic private slots, never in QQ wire data."""
    global _image_class
    if _image_class is None:
        from astrbot.api.message_components import Image
        from pydantic.v1 import PrivateAttr
        class CognizedImage(Image):
            _companion_image_cognition: dict = PrivateAttr(default_factory=dict)
            _companion_image_summary: str = PrivateAttr(default='')
        _image_class=CognizedImage
    result=_image_class(**image.dict())
    result._companion_image_cognition=info
    result._companion_image_summary=summary(info)
    return result

def digest_bytes(component):
    import base64
    from urllib.parse import unquote,urlparse
    for raw in (getattr(component,'file',''),getattr(component,'url','')):
        value=str(raw or '')
        try:
            if value.startswith('base64://'):payload=base64.b64decode(value[9:],validate=True)
            elif value.startswith('data:image/') and ';base64,' in value:payload=base64.b64decode(value.split(',',1)[1],validate=True)
            else:
                p=Path(unquote(urlparse(value).path)) if value.startswith('file://') else Path(value)
                if not value or not p.is_file() or p.stat().st_size>8*1024*1024:continue
                payload=p.read_bytes()
            if 0<len(payload)<=8*1024*1024:return hashlib.sha256(payload).hexdigest(),payload
        except (OSError,ValueError):continue
    return '',b''

def describe(plugin,event,*,digest='',image=None,pack_ids=None):
    from .packs.resolver import get_pack_paths
    from .semantic.storage import load_metadata,safe_relative_path
    from .semantic.metadata_search import load_character_references
    from ..config import PLUGIN_DATA_DIR
    from PIL import Image
    raw=b''
    if image is not None:digest,raw=digest_bytes(image)
    if not re.fullmatch(r'[a-f0-9]{16,64}',str(digest)):return {}
    result={'image_id':'image:'+digest[:16],'content_sha256':digest,
            'character':{'name':'','status':'unknown'},'emotions':[],
            'visible_text':'','meaning':'','known':False}
    if raw:
        try:
            with Image.open(io.BytesIO(raw)) as im:
                result.update(media_type=str(im.format or '').lower(),is_animated=bool(getattr(im,'is_animated',False)),logical_image_count=1)
        except (OSError,ValueError):pass
    getter=getattr(plugin,'_get_media_receipts',None)
    packs=pack_ids if pack_ids is not None else getter().packs(event) if callable(getter) else []
    for pack in packs:
        folder=get_pack_paths(pack)['pack_dir']
        try:metadata=load_metadata(folder)
        except (OSError,ValueError):continue
        for entry_id,item in metadata.get('images',{}).items():
            if not isinstance(item,dict):continue
            sha=str(item.get('content_sha256') or '')
            if not sha.startswith(digest):continue
            path=safe_relative_path(folder,item.get('relative_path',''))
            if not path or not path.is_file() or path.is_symlink():continue
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:continue
            tags=[str(t) for t in item.get('tags',[])]
            names=[re.split('[:：]',t,maxsplit=1)[1] for t in tags if t.startswith(('角色:','人物:','角色：','人物：'))]
            confirmed=False
            for ref in load_character_references(PLUGIN_DATA_DIR):
                if ref.get('pack_id')!=pack and ref.get('target_pack_id')!=pack:continue
                aliases=[str(ref.get('name') or ''),*ref.get('aliases',[])]
                if any(alias and (alias in tags or alias in str(item.get('caption',''))) for alias in aliases):
                    if ref['name'] not in names:names.append(ref['name'])
                    confirmed |= ref.get('content_sha256')==sha
            emotions=[re.split('[:：]',t,maxsplit=1)[-1] for t in tags if t.startswith(('情绪:','情感:','情绪：')) or re.search('开心|高兴|难过|委屈|害羞|撒娇|惊讶|生气|无奈|困惑|亲昵|调侃|吐槽|不满|嫌弃|拒绝|安慰',t)]
            category=str(item.get('category') or '')
            if category and category!='needs_review' and category not in emotions:emotions.append(category)
            fields={k:item.get(k) for k in ('caption','tags','visible_text','updated_at','manual_override','provenance')}
            result.update(content_sha256=sha,pack_id=pack,entry_id=str(entry_id),filename=path.name,
                character={'name':'、'.join(names),'status':('confirmed' if confirmed or item.get('manual_override') or item.get('provenance') in ('manual','mixed') else 'model_candidate') if names else 'unknown'},
                emotions=emotions,tags=tags,visible_text=str(item.get('visible_text') or ''),
                meaning=str(item.get('caption') or ''),known=bool(item.get('caption')),
                revision=hashlib.sha256(json.dumps(fields,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16])
            return result
    return result

def summary(value):
    if not value:return '[已发送图片；图片身份暂不可读取]'
    character=value.get('character',{})
    name=character.get('name') or '尚未确认'
    if character.get('status')=='model_candidate':name+='（模型候选，非用户确认）'
    parts=['已发送'+('GIF动图' if value.get('is_animated') else '图片'),value['image_id']]
    for key,label in [('filename','文件'),('visible_text','文字'),('meaning','含义')]:
        if value.get(key):parts.append(label+'：'+str(value[key])[:250])
    parts.append('人物：'+name)
    if value.get('emotions'):parts.append('情绪：'+'、'.join(value['emotions']))
    if value.get('revision'):parts.append('认知版本：'+value['revision'])
    parts.append('可用 meme_collection_status(query="'+value['image_id']+'") 查询当前认知')
    return '['+' | '.join(parts)+']'
