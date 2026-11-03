import asyncio
from contextlib import contextmanager
from types import SimpleNamespace as S
from unittest.mock import AsyncMock
import pytest
from astrbot_plugin_autonomous_development.agent import Agent
from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.integrations import owner_identity
from astrbot_plugin_autonomous_development.tasks import Tasks

@pytest.mark.asyncio
async def test_cancelling_during_step_never_restores_running_state(tmp_path,monkeypatch):
    store=Store(tmp_path/'tasks.sqlite3');tasks=Tasks(store);identity=owner_identity()
    store.set_setting('development',{'mode':'auto'})
    row=tasks.create(identity,'改进文档排版');row=tasks.plan(identity,row['id'],{'paths':['docs/sample.md'],'checks':[['/usr/bin/true']]})
    writing=asyncio.Event();continue_write=asyncio.Event()
    class Worker:
        async def call(self,who,tid,op,**kwargs):
            if op=='prepare':return {'base_commit':'a'*40}
            if op=='write':writing.set();await continue_write.wait();return {'written':['docs/sample.md']}
    monkeypatch.setattr('astrbot_plugin_autonomous_development.agent.dependencies',lambda ctx:())
    agent=Agent(None,store,tasks,Worker());agent.call=AsyncMock(return_value={'action':'write','files':{'docs/sample.md':'after'}})
    job=asyncio.create_task(agent.step(row['id']));await writing.wait()
    tasks.update(identity,row['id'],state='cancelled');continue_write.set();await job
    assert tasks.get(identity,row['id'])['state']=='cancelled'
    store.close()
