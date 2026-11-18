import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
import subprocess

import pytest

from astrbot_plugin_autonomous_development.db import Store
from astrbot_plugin_autonomous_development.policy import Identity, validate_files
from astrbot_plugin_autonomous_development.tasks import Tasks
from astrbot_plugin_autonomous_development.usage import UsageLedger, BudgetExceeded, calculate, PRICES, normalize_usage
from astrbot_plugin_autonomous_development.runner import Workspace


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'governance.sqlite3')
    yield value
    value.close()


@pytest.fixture
def identity():
    return Identity('2893214983', '小小情', '1005145948', 'xiaoxiaoqing')


def test_secondary_identity_cannot_create_or_approve(store):
    other = Identity('2510093206', '初小晴', '1207862759', 'chuxiaoqing')
    with pytest.raises(PermissionError):
        Tasks(store).create(other, '增加一个阅读工具')
    assert store.rows('SELECT * FROM tasks') == []


def test_approval_is_bound_to_plan_version_and_cannot_be_replayed(store, identity):
    tasks = Tasks(store)
    store.set_setting('development', {'mode': 'ask'})
    row = tasks.create(identity, '增加天气文字排版')
    tasks.plan(identity, row['id'], {'paths': ['plugins/weather/formatter.py'], 'goal': '原方案'})
    approval = tasks.request_approval(identity, row['id'], 'modify')
    tasks.decide(identity, approval['id'], 'approved')
    tasks.authorize(identity, row['id'], 'modify')
    tasks.plan(identity, row['id'], {'paths': ['plugins/weather/formatter.py'], 'goal': '扩大方案'})
    with pytest.raises(PermissionError):
        tasks.authorize(identity, row['id'], 'modify')
    with pytest.raises(PermissionError):
        tasks.decide(identity, approval['id'], 'approved')


def test_automatic_mode_still_requires_core_changes_to_be_approved(store, identity):
    store.set_setting('development', {'mode': 'auto'})
    tasks = Tasks(store)
    row = tasks.create(identity, '改进普通问候排版')
    tasks.plan(identity, row['id'], {'paths': ['plugins/greetings/formatter.py']})
    tasks.authorize(identity, row['id'], 'modify')
    tasks.plan(identity, row['id'], {'paths': ['plugins/astrbot_plugin_private_companion/main.py']})
    with pytest.raises(PermissionError):
        tasks.authorize(identity, row['id'], 'modify')


def test_budget_reservations_are_atomic_and_unknown_is_not_free(store):
    ledger = UsageLedger(store)
    store.set_setting('budget', {'monthly_usd': '.001', 'chat_reserve_usd': '0', 'enforce': True, 'billing_day': 1})
    def reserve(_):
        try:
            return ledger.reserve({'plugin': 'test'}, 'gpt-6.1-sol', 0, 60)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(reserve, range(8)))
    assert len([i for i in ids if i]) == 1
    ledger.finish(next(i for i in ids if i), state='failed')
    with pytest.raises(BudgetExceeded):
        ledger.reserve({'plugin': 'test'}, 'gpt-6.1-sol', 0, 60)
    assert ledger.summary()['totals']['missing'] == 1


def test_usage_counts_reasoning_and_cache_only_once():
    raw = {'input_tokens': 100, 'output_tokens': 20, 'input_tokens_details': {'cached_tokens': 80},
           'output_tokens_details': {'reasoning_tokens': 15}}
    value = normalize_usage(raw)
    assert value['reasoning'] == 15
    assert calculate('gpt-6.1-sol', value, PRICES) == 248
    assert normalize_usage({'input_tokens': 100}) is None


@pytest.mark.parametrize('path', ['../other/a.py', '/etc/passwd', 'data/config.py', '.git/config', 'a/.env', 'a\\b.py', 'Coopanion/../escape'])
def test_private_and_escaping_paths_are_rejected(path):
    with pytest.raises((PermissionError, ValueError)):
        validate_files({path: 'test'})


@pytest.mark.asyncio
@pytest.mark.host_isolation
async def test_real_isolation_blocks_host_files_and_preserves_test_exit(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'base'], check=True)
    ws = Workspace(repo, tmp_path / 'work')
    ws.prepare()
    result = await ws.run(['/usr/bin/python3', '-c', "from pathlib import Path; assert not Path('/home/yingjxu/workspace').exists(); Path('result.txt').write_text('done')"])
    assert result['exit_code'] == 0 and result['isolated'] is True
    assert (ws.directory / 'result.txt').read_text() == 'done'
    failed = await ws.run(['/usr/bin/python3', '-c', 'raise SystemExit(7)'])
    assert failed['exit_code'] == 7
    (ws.directory / 'link').symlink_to(tmp_path)
    with pytest.raises(PermissionError):
        ws.write({'link/escape.txt': 'bad'}, ['link/escape.txt'])


def test_resume_uses_actual_receipts_instead_of_restarting_a_committed_workspace():
    assert Tasks.resume_state({})=='proposed'
    assert Tasks.resume_state({'plan':{'paths':['docs/a.md']},'base_commit':'a'*40})=='running'
    assert Tasks.resume_state({'plan':{},'artifact':{'checks':['actual']}})=='verified'
    assert Tasks.resume_state({'commit':'a'*40,'artifact':{}})=='committed'
    assert Tasks.resume_state({'commit':'a'*40,'uploaded':True})=='uploaded'
    assert Tasks.resume_state({'windows':{'state':'awaiting_windows_channel'}})=='awaiting_windows_channel'
    assert Tasks.resume_state({'resume_state':'running','plan':{}})=='running'
