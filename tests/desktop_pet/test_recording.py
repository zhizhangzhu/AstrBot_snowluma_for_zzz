"""A history failure must not turn confirmed delivery into a request to resend."""
from types import SimpleNamespace
import pytest
from plugins.astrbot_plugin_desktop_pet.integrations import record_outputs
from plugins.astrbot_plugin_desktop_pet.protocol import Binding


@pytest.mark.asyncio
async def test_recording_failure_preserves_the_confirmed_execution_result(tmp_path):
    class Bridge:
        async def record_visible_turn(self, **row):
            # A real unavailable storage destination, after the renderer confirmed delivery.
            (tmp_path/'missing-storage'/'turn').write_text(row['content'])
    memory = SimpleNamespace(memory_companion=Bridge())
    context = SimpleNamespace(get_all_stars=lambda: [SimpleNamespace(activated=True,
        root_dir_name='astrbot_plugin_memory_companion', star_cls=memory)])
    binding = Binding('device','persona','bot','partner','qq')
    result = {'status':'completed','text':'Shown.', 'outputs':[{'text':'Would you like to draw?', 'meta':{'kind':'ask'}}]}
    assert await record_outputs(context,binding,result,'request') is False
    assert result['status']=='completed' and result['text']=='Shown.'
    assert result['shared_recording']=='pending'


@pytest.mark.asyncio
async def test_unconfirmed_submission_is_not_archived_as_visible_speech(tmp_path):
    class Bridge:
        async def record_visible_turn(self, **row):
            (tmp_path/'visible-turn').write_text(row['content'])
    context=SimpleNamespace(get_all_stars=lambda:[SimpleNamespace(activated=True,
        root_dir_name='astrbot_plugin_memory_companion',star_cls=SimpleNamespace(memory_companion=Bridge()))])
    result={'status':'submitted','outputs':[{'text':'A question.', 'meta':{'kind':'ask'}}]}
    assert await record_outputs(context,Binding('device','persona','bot','partner','qq'),result,'request') is False
    assert not (tmp_path/'visible-turn').exists()
