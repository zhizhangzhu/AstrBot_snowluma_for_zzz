import asyncio
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace as S
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_autonomous_development.db import Store, ZONE
from astrbot_plugin_autonomous_development.discovery import CAPABILITY, ORIGIN
from astrbot_plugin_autonomous_development.integrations import owner_identity
from astrbot_plugin_autonomous_development.schedule import Schedule
from astrbot_plugin_autonomous_development.tasks import Tasks


@pytest.fixture
def owner(tmp_path, monkeypatch):
    value = Schedule()
    value.store = Store(tmp_path / 'governance.sqlite3')
    value.tasks = Tasks(value.store)
    value.context = S(
        conversation_manager=S(get_curr_conversation_id=AsyncMock(return_value='owner-chat'),
            get_conversation=AsyncMock(return_value=S(persona_id='小小情', content=[
                {'role': 'user', 'content': [{'type': 'text', 'text': '希望改进表情包记忆机制'}, {'type': 'image_url', 'image_url': 'private'}]},
                {'role': 'assistant', 'content': '可以先设计方案'},
                {'role': 'tool', 'content': '不能当作用户需求或审批'},
            ]))),
        get_config=lambda **kw: {'provider_settings': {}},
        persona_manager=S(resolve_selected_persona=AsyncMock(return_value=('小小情', object(), None))))
    cp = S(real_schedule_control=lambda request: {'execution_allowed': True})
    @contextmanager
    def context(_): yield cp
    monkeypatch.setattr('astrbot_plugin_autonomous_development.schedule.companion_context', context)
    monkeypatch.setattr('astrbot_plugin_autonomous_development.integrations.companion_context', context)
    value.cp = cp
    yield value
    value.store.close()


@pytest.mark.asyncio
async def test_current_chat_is_scoped_and_cancelled_acceptance_is_not_an_active_goal(owner):
    old = owner.tasks.create(owner_identity(), '验收：禁止修改任何文件，仅限生成格式方案')
    owner.tasks.update(owner_identity(), old['id'], state='cancelled')
    context = await owner.development_context()
    assert context['existing_tasks'] == []
    assert context['recent_dialogue'][0]['text'] == '希望改进表情包记忆机制'
    assert len(context['recent_dialogue']) == 2 and context['conversation_origin'] == ORIGIN
    assert 'private' not in str(context) and '不能当作' not in str(context)
    inputs = owner.store.setting('development_inputs')[0]
    await owner.development_context()
    assert owner.store.setting('development_inputs')[0]['serial'] == inputs['serial'] == 1
    owner.context.persona_manager.resolve_selected_persona.return_value = ('初小晴', object(), None)
    with pytest.raises(PermissionError): await owner.development_context()


@pytest.mark.asyncio
async def test_model_failure_does_not_consume_the_day_and_new_feedback_can_be_reviewed(owner, monkeypatch):
    clock = [datetime(2026, 11, 18, 17, tzinfo=ZONE).timestamp()]
    monkeypatch.setattr('astrbot_plugin_autonomous_development.discovery.time.time', lambda: clock[0])
    owner.ordinary_choice = AsyncMock(return_value={})
    with pytest.raises(ValueError): await owner.review_development_goal({})
    assert owner.store.setting('last_proposal_day')[0] is None
    assert not owner.proposal_status()['due']
    clock[0] += 601
    owner.ordinary_choice.return_value = {'act': False, 'reason': '先记录单次反馈，已有功能需要进一步验证'}
    result = await owner.review_development_goal({})
    assert result['verified'] and not owner.proposal_status()['due']
    prompt = owner.ordinary_choice.call_args.args[0]
    assert '希望改进表情包记忆机制' in prompt
    owner.remember_development_hint('请增加一个桌宠功能', 'verified_owner_message')
    assert not owner.proposal_status()['due']  # No call per keystroke or per chat message.
    clock[0] += 3601
    assert owner.proposal_status()['due']
    owner.ordinary_choice.return_value = {'act': True, 'goal': '验证表情包纠正信息能否在下一次识图前召回'}
    result = await owner.review_development_goal({})
    assert result['evidence_kind'] == 'development_goal_created'
    assert owner.tasks.list(owner_identity())[0]['state'] == 'proposed'
    assert owner.store.rows('select * from approvals') == []
    assert owner.tasks.mode()['mode'] == 'ask' and not owner.tasks.mode()['allow_push']


@pytest.mark.asyncio
async def test_concurrent_review_is_single_and_midflight_revocation_is_respected(owner):
    entered, release = asyncio.Event(), asyncio.Event()
    async def choice(*args):
        entered.set()
        await release.wait()
        return {'act': True, 'goal': '不能在中途关闭开发之后创建任务'}
    owner.ordinary_choice = choice
    first = asyncio.create_task(owner.review_development_goal({}))
    await entered.wait()
    second = await owner.review_development_goal({})
    assert not second['verified']
    owner.store.set_setting('development', {'mode': 'off'})
    release.set()
    assert not (await first)['verified']
    assert owner.tasks.list(owner_identity()) == []
    assert owner.store.rows('select * from leases') == []


@pytest.mark.asyncio
async def test_missing_llm_result_is_not_a_successful_empty_review(owner, monkeypatch):
    owner.cp.data = {'users': {}}
    owner.cp._resolve_proactive_persona_prompt = AsyncMock(return_value='人格')
    owner.cp._llm_call = AsyncMock(return_value=None)
    owner.awareness = S(prompt=lambda: '')
    with pytest.raises(RuntimeError): await owner.ordinary_choice('检查需求', 'goal_discovery')


@pytest.mark.asyncio
async def test_daily_and_feedback_admission_deduplicate_and_obey_grants(owner, monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[2] / 'vendor/installed-plugins/astrbot_plugin_private_companion/real_schedule/ledger.py'
    spec = importlib.util.spec_from_file_location('discovery_fixture_ledger', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    ledger = module.Ledger(tmp_path / 'activities.db')
    clock = datetime(2026, 11, 18, 17, tzinfo=ZONE).timestamp()
    monkeypatch.setattr('astrbot_plugin_autonomous_development.discovery.time.time', lambda: clock)
    grants = [CAPABILITY + 'propose', CAPABILITY + 'develop']
    runtime = S(ledger=ledger, scope=lambda: 'primary-scope', day=lambda: '2026-11-18',
        end_of_day=lambda: clock + 3600, hard_limit=lambda: False, tick=AsyncMock())
    owner.cp.persona_setting = lambda key, default: grants
    owner.cp._environment_fromtimestamp = lambda now: datetime.fromtimestamp(now, ZONE)
    @contextmanager
    def context(_): yield owner.cp
    monkeypatch.setattr('astrbot_plugin_autonomous_development.integrations.companion_context', context)
    monkeypatch.setattr('astrbot_plugin_autonomous_development.discovery.importlib.import_module',
        lambda _: S(enabled=lambda cp: True, service=lambda cp: runtime))
    await owner.maintain_development_queue(); await owner.maintain_development_queue()
    assert len(ledger.list('primary-scope')) == 1
    assert runtime.tick.await_count == 2  # Both admission and recovery wake the SAME coordinator.
    old = ledger.list('primary-scope')[0]
    with ledger.connect() as db:
        db.execute("UPDATE activities SET state='succeeded' WHERE id=?", (old['id'],))
    ledger.receipt('primary-scope', old['id'], 'presence-only',
                   {'verified': True, 'evidence_kind': 'qq_action_ack', 'source': 'set_diy_online_status', 'summary': 'QQ在线状态更新成功'})
    await owner.maintain_development_queue(); await owner.maintain_development_queue()
    reviews = ledger.list('primary-scope')
    assert len(reviews) == 2 and sum(r['state'] == 'planned' for r in reviews) == 1
    assert owner.store.setting('development_review')[0] is None
    task = owner.tasks.create(owner_identity(), '先设计可审核的改进方案')
    await owner.maintain_development_queue(); await owner.maintain_development_queue()
    assert len(ledger.list('primary-scope')) == 3
    owner.tasks.plan(owner_identity(), task['id'], {'paths': ['plugins/example/format.py']})
    # Awaiting approval is not made executable by the admission watcher.
    assert owner.ready_development_tasks() == []
    assert sum(r['state'] == 'planned' for r in ledger.list('primary-scope')) == 2
    grants.clear()
    before = len(ledger.list('primary-scope'))
    runtime.tick.reset_mock()
    await owner.maintain_development_queue()
    assert len(ledger.list('primary-scope')) == before
    runtime.tick.assert_not_awaited()
    owner.store.set_setting('development', {'mode': 'off'})
    owner.context = None
    await owner.maintain_development_queue()  # Does not even open another persona when disabled.


def test_indirect_owner_bug_feedback_is_retained(tmp_path):
    from astrbot_plugin_autonomous_development.db import Store
    from astrbot_plugin_autonomous_development.discovery import DevelopmentDiscovery
    owner=DevelopmentDiscovery();owner.store=Store(tmp_path/'inputs.sqlite3')
    text='你可以自己后台看一看，是不是哪里逻辑有什么问题呀！有问题的话你可以自行修复一下哦'
    owner.remember_development_hint(text,'verified_owner_message')
    saved=owner.store.setting('development_inputs',{})[0]
    assert saved['serial']==1 and saved['items'][0]['text']==text
    owner.store.close()
