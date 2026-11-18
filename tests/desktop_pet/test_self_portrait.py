import base64
import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from plugins.astrbot_plugin_desktop_pet.protocol import ProtocolError, catalog
from plugins.astrbot_plugin_desktop_pet.self_portrait import attach_self_portrait, portrait_summary, validate_portrait, _verified_png
from plugins.astrbot_plugin_desktop_pet.presentation import snapshot_for

def portrait(color='mintcream',scheme='default-sunflowers'):
    out=io.BytesIO();Image.new('RGBA',(24,32),color).save(out,format='PNG');raw=out.getvalue()
    return {'mime':'image/png','base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),
            'width':24,'height':32,'figure_id':'xiaoxiaoqing','figure_version':'1.10.4','scheme':scheme}

def peer(picture=None):
    world={'id':'desktop-pet','supported':True,'tools':[],'portrait':picture or portrait()}
    return SimpleNamespace(catalog=catalog({'worlds':[world]}),state={})

def test_catalog_checks_image_bytes_dimensions_and_reuses_decode_cache():
    pic=portrait();validate_portrait(pic);before=_verified_png.cache_info().hits;validate_portrait(pic)
    assert _verified_png.cache_info().hits>before
    for bad in [dict(pic,sha256='0'*64),dict(pic,width=999),dict(pic,width=25),dict(pic,base64='!'),dict(pic,base64='A'*230000),dict(pic,mime='image/jpeg')]:
        with pytest.raises(ProtocolError):validate_portrait(bad)
    with pytest.raises(ProtocolError):catalog({'worlds':[{'id':'other','portrait':pic,'tools':[]}]})

def test_snapshots_expose_reference_identity_without_base64_or_requesting_new_cognition():
    source=peer();summary=portrait_summary(source)
    for light in (True,False):
        body=snapshot_for(source,[],light=light)
        assert body['body_reference']==summary and summary['available']
        assert 'base64' not in str(body)
    assert not portrait_summary(SimpleNamespace(catalog={'worlds':[]}))['available']

def test_attaches_typed_visual_once_changes_outfit_and_preserves_user_media():
    pytest.importorskip('astrbot.api')
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.agent.message import TextPart,ImageURLPart
    user=ImageURLPart(image_url=ImageURLPart.ImageURL(url='https://example.invalid/user-photo.png'))
    req=ProviderRequest(prompt='Hello',extra_user_content_parts=[TextPart(text='User attachment'),user])
    source=peer();assert attach_self_portrait(req,source)
    assert isinstance(req.extra_user_content_parts[-1],ImageURLPart)
    first=req.extra_user_content_parts[-1].image_url.url
    assert attach_self_portrait(req,source) and len(req.extra_user_content_parts)==4
    assert req.extra_user_content_parts[1] is user
    changed=peer(portrait('gold','default-sunhat'));attach_self_portrait(req,changed)
    assert len(req.extra_user_content_parts)==4 and req.extra_user_content_parts[-1].image_url.url!=first
    assert 'default-sunhat' in req.extra_user_content_parts[-2].text
    history=[part.model_dump() for part in req.extra_user_content_parts[-2:]]
    req.contexts=[{'role':'user','content':history}];attach_self_portrait(req,changed)
    assert req.extra_user_content_parts==[req.extra_user_content_parts[0],user]
    # A text-only memory of seeing it cannot replace actual visual input.
    req.contexts=[{'role':'user','content':[history[0]]}];attach_self_portrait(req,changed)
    assert len(req.extra_user_content_parts)==4
    other=ProviderRequest(prompt='Other persona');attach_self_portrait(other,source)
    assert other.extra_user_content_parts[-1].image_url.url==first

@pytest.mark.asyncio
async def test_real_provider_assembler_keeps_portrait_as_image_without_calling_a_model():
    pytest.importorskip('astrbot.api')
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    req=ProviderRequest(prompt='What do you look like?');attach_self_portrait(req,peer())
    provider=ProviderOpenAIOfficial.__new__(ProviderOpenAIOfficial)
    message=await provider.assemble_context(req.prompt,extra_user_content_parts=req.extra_user_content_parts)
    images=[part for part in message['content'] if part['type']=='image_url']
    assert len(images)==1 and images[0]['image_url']['url'].startswith('data:image/png;base64,')
    assert any('YOUR OWN desktop body' in part.get('text','') for part in message['content'])

@pytest.mark.asyncio
async def test_actual_context_hook_preserves_ordinary_qq_inputs_for_both_personas():
    pytest.importorskip('astrbot.api')
    import ast
    import json
    import importlib.util
    from pathlib import Path
    from unittest.mock import AsyncMock
    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.provider.entities import ProviderRequest
    from plugins.astrbot_plugin_desktop_pet.protocol import Binding
    from plugins.astrbot_plugin_desktop_pet.presentation import EMBODIMENT
    spec=importlib.util.find_spec('astrbot_plugin_desktop_pet')
    source=Path(spec.submodule_search_locations[0])/'main.py'
    cls=next(node for node in ast.parse(source.read_text()).body if isinstance(node,ast.ClassDef) and node.name=='DesktopPetPlugin')
    method=next(node for node in cls.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='terminal_context');method.decorator_list=[]
    namespace={'AstrMessageEvent':AstrMessageEvent,'ProviderRequest':ProviderRequest,'json':json,
               'attach_self_portrait':attach_self_portrait,'snapshot_for':snapshot_for,'EMBODIMENT':EMBODIMENT}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),namespace)
    seen=[]
    for index,color in enumerate(('red','blue')):
        binding=Binding(f'device-{index}',f'persona-{index}',f'bot-{index}',f'user-{index}',f'adapter-{index}')
        current=peer(portrait(color));current.binding=binding
        owner=SimpleNamespace(authorized_binding=AsyncMock(return_value=binding),hub=SimpleNamespace(peer=lambda b:current),
                              store=SimpleNamespace(observations=lambda b:[]))
        event=SimpleNamespace(get_extra=lambda key:None)
        request=ProviderRequest(prompt='Ordinary QQ message',system_prompt=f'Persona {index}')
        await namespace['terminal_context'](owner,event,request)
        assert request.prompt=='Ordinary QQ message' and request.system_prompt.startswith(f'Persona {index}')
        assert len(request.extra_user_content_parts)==2
        seen.append(request.extra_user_content_parts[-1].image_url.url)
        owner.hub.peer=lambda b:None
        offline=ProviderRequest(prompt='Still a normal message')
        await namespace['terminal_context'](owner,event,offline)
        assert not offline.extra_user_content_parts and 'offline' in offline.system_prompt
    assert seen[0]!=seen[1]
