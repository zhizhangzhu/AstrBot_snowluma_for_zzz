import importlib
import io
import json
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace
import hashlib
import pytest

pytest.importorskip('astrbot.api')
from astrbot.api.message_components import Image
from PIL import Image as PIL

def test_image_identity_is_private_on_wire_live_in_metadata_and_scoped(tmp_path,monkeypatch):
    root=next((parent/'data/astrbot/plugins/meme_manager' for parent in Path(__file__).parents if (parent/'data/astrbot/plugins/meme_manager').exists()),Path('/AstrBot/data/plugins/meme_manager'))
    package=ModuleType('cognition_meme_probe');package.__path__=[str(root)]
    monkeypatch.setitem(sys.modules,package.__name__,package)
    cognition=importlib.import_module('cognition_meme_probe.backend.image_cognition')
    resolver=importlib.import_module('cognition_meme_probe.backend.packs.resolver')
    storage=importlib.import_module('cognition_meme_probe.backend.semantic.storage')
    search=importlib.import_module('cognition_meme_probe.backend.semantic.metadata_search')
    raw=io.BytesIO();PIL.new('RGB',(2,2),'blue').save(raw,format='PNG');data=raw.getvalue();digest=hashlib.sha256(data).hexdigest()
    (tmp_path/'meme.png').write_bytes(data)
    metadata={'images':{'entry':{'content_sha256':digest,'relative_path':'meme.png','caption':'A joyful greeting','visible_text':'Hello','tags':['角色:Example','情绪:开心'],'category':'happy','manual_override':True}}}
    monkeypatch.setattr(storage,'load_metadata',lambda p:metadata)
    monkeypatch.setattr(resolver,'get_pack_paths',lambda p:{'pack_dir':tmp_path})
    monkeypatch.setattr(search,'load_character_references',lambda p:[])
    plugin=SimpleNamespace(_get_media_receipts=lambda:SimpleNamespace(packs=lambda event:['allowed']))
    info=cognition.describe(plugin,object(),image=Image.fromBytes(data))
    assert info['character']['name']=='Example' and info['visible_text']=='Hello'
    assert info['emotions'] and info['meaning'] and info['character']['status']=='confirmed'
    tagged=cognition.attach(Image.fromBytes(data),info)
    assert 'image:'+digest[:16] in tagged._companion_image_summary
    assert 'cognition' not in json.dumps(tagged.toDict()) and 'summary' not in json.dumps(tagged.toDict())
    revision=info['revision'];metadata['images']['entry'].update(caption='A corrected greeting',visible_text='Welcome')
    updated=cognition.describe(plugin,object(),digest=digest[:16])
    assert updated['revision']!=revision and updated['visible_text']=='Welcome'
    plugin._get_media_receipts=lambda:SimpleNamespace(packs=lambda event:[])
    assert not cognition.describe(plugin,object(),digest=digest)['known']
