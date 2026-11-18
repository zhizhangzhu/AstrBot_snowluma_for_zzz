"""Exercise the installed parser with isolated persona adapters and real durable records."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace as S
import asyncio
import sys
import os
import pytest
from astrbot_plugin_autonomous_development.configuration import Configuration
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.integrations import owner_identity

SOURCE=Path(os.environ.get('EVOLUTION_SOURCE_ROOT',Path(__file__).resolve().parents[2]))/'vendor/installed-plugins/astrbot_plugin_private_companion/function_awareness/config_control.py'
spec=importlib.util.spec_from_file_location('evolution_native_config_parser',SOURCE)
native=importlib.util.module_from_spec(spec);sys.modules[spec.name]=native;spec.loader.exec_module(native)

@pytest.fixture
def setup(tmp_path,monkeypatch):
    cp=S(config={'min_interval_minutes':60,'manual_other':0},other={'min_interval_minutes':90})
    cp.persona_setting=lambda k,default=None:cp.config.get(k,default)
    cp._configured_multi_persona_ids=lambda:['小小情','初小晴']
    cp._persona_profile_snapshot_if_exists=lambda pid:{'persona_settings':cp.other}
    cp._persona_scope_manifest=lambda:{'min_interval_minutes':{'scope':'persona','type':'int'}}
    control=native.ConfigControl(S(owner=cp));cp._get_function_awareness=lambda:S(control=control)
    class Adapter:
        fail=False;interleave=False
        def _apply_config_value(self,key,value,*args):cp.config[key]=value
        async def _save_config_if_possible(self):
            if self.interleave:cp.config.update(min_interval_minutes=120,manual_other=7)
            if self.fail:raise RuntimeError('disk failure')
            return True
    cp.page_api=Adapter()
    @contextmanager
    def scope(ctx):yield cp
    monkeypatch.setattr('astrbot_plugin_autonomous_development.configuration.companion_context',scope)
    store=Store(tmp_path/'config.sqlite3');cfg=Configuration(None,store)
    yield cfg,cp,store
    store.close()

@pytest.mark.asyncio
async def test_self_config_is_independent_of_development_and_isolated_and_undoable(setup):
    cfg,cp,store=setup;store.set_setting('development',{'mode':'off'})
    change=await cfg.apply(owner_identity(),'min_interval_minutes',75,60,'降低打扰频率',autonomous=True)
    assert cp.config['min_interval_minutes']==75 and cp.other['min_interval_minutes']==90
    assert change['changed'] and cfg.enabled()['enabled']
    await cfg.undo(owner_identity(),change['change_id'])
    assert cp.config['min_interval_minutes']==60

@pytest.mark.asyncio
async def test_manual_lock_stale_write_and_inherited_secondary_protect_settings(setup):
    cfg,cp,store=setup;cfg.lock(owner_identity(),'min_interval_minutes',True)
    with pytest.raises(PermissionError):await cfg.apply(owner_identity(),'min_interval_minutes',75,60,'测试',autonomous=True)
    with pytest.raises(ValueError):await cfg.apply(owner_identity(),'min_interval_minutes',75,59,'旧版本')
    cfg.lock(owner_identity(),'min_interval_minutes',False);cp.other.clear()
    with pytest.raises(PermissionError):await cfg.apply(owner_identity(),'min_interval_minutes',75,60,'不得改变另一人格',autonomous=True)
    assert cp.config['min_interval_minutes']==60

@pytest.mark.asyncio
async def test_failed_save_does_not_restore_a_stale_whole_configuration(setup):
    cfg,cp,store=setup;cp.page_api.fail=True;cp.page_api.interleave=True
    with pytest.raises(RuntimeError):await cfg.apply(owner_identity(),'min_interval_minutes',75,60,'测试保存失败')
    assert cp.config['min_interval_minutes']==120 and cp.config['manual_other']==7
    assert store.rows('SELECT state FROM config_changes')[0]['state']=='failed'
