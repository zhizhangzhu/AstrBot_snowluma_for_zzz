import asyncio
from contextlib import contextmanager
from types import SimpleNamespace as S
import pytest
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.activities import migrate_defaults, DEFAULT_ACTIVITIES

@pytest.mark.asyncio
async def test_explicit_default_grants_preserve_old_and_secondary_permissions_and_do_not_resurrect_optout(tmp_path,monkeypatch):
    primary=['extension:memory:consolidate'];secondary=['extension:meme:collect']
    cp=S(persona_setting=lambda key,default=None:list(primary),_configured_multi_persona_ids=lambda:['小小情','初小晴'],
         _persona_profile_snapshot_if_exists=lambda pid:{'persona_settings':{'real_schedule_allowed_extensions':secondary}})
    async def save():return True
    def apply(key,value,*args):primary[:]=value
    cp.page_api=S(_apply_config_value=apply,_save_config_if_possible=save)
    cp._get_function_awareness=lambda:S(control=S(lock=asyncio.Lock()))
    @contextmanager
    def context(ctx):yield cp
    monkeypatch.setattr('astrbot_plugin_autonomous_development.activities.companion_context',context)
    store=Store(tmp_path/'grants.sqlite3')
    await migrate_defaults(None,store)
    assert set(primary)=={'extension:memory:consolidate',*DEFAULT_ACTIVITIES}
    assert secondary==['extension:meme:collect']
    primary.remove(DEFAULT_ACTIVITIES[0]);await migrate_defaults(None,store)
    assert DEFAULT_ACTIVITIES[0] not in primary
    store.close()
