"""Discover registered QQ instances without reading credentials or importing friends."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .logging_util import get_module_logger

logger = get_module_logger(__name__)
INTERVAL = 10


def _path(owner):
    directory = getattr(owner, 'data_dir', None)
    return Path(directory) / 'bot_accounts.json' if directory else None


def _read(owner):
    path = _path(owner)
    value = json.loads(path.read_text()) if path and path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError('invalid account registration')
    return value


def _write(path, value):
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _qq(value):
    value = str(value or '')
    return value if re.fullmatch(r'[1-9]\d{4,14}', value) else ''


def candidates_allowed(owner, candidates):
    """An opted-in instance may never dispatch under a different QQ identity."""
    bindings = _read(owner)
    for binding in bindings.values():
        if not isinstance(binding, dict) or not binding.get('auto_bind'):
            continue
        adapter = str(binding.get('adapter_id') or '').casefold()
        if adapter and adapter in candidates:
            expected = str(binding.get('bot_id') or '')
            observed = {_qq(value) for value in candidates if _qq(value)}
            if not expected or observed != {expected}:
                return False
    return True


async def _probe(platform):
    client = platform.get_client()
    connections = getattr(client, '_wsr_api_clients', None)
    if not isinstance(connections, dict) or not connections:
        return {'state': 'waiting_for_connection'}
    ids = {_qq(key) for key in connections if _qq(key)}
    if len(ids) != 1 or len(connections) != 1:
        return {'state': 'ambiguous_connection'}
    account = next(iter(ids))
    snapshot = dict(connections)
    try:
        result = await asyncio.wait_for(
            client.call_action('get_login_info', self_id=account), timeout=5,
        )
    except Exception:
        return {'state': 'login_check_failed'}
    if not isinstance(result, dict):
        return {'state': 'invalid_login_response'}
    if 'retcode' in result and result['retcode'] != 0:
        return {'state': 'login_check_failed'}
    payload = result.get('data', result)
    verified = _qq(payload.get('user_id')) if isinstance(payload, dict) else ''
    # The connection must still be the very same socket after the awaited RPC.
    if verified != account or getattr(client, '_wsr_api_clients', {}) != snapshot:
        return {'state': 'connection_identity_mismatch'}
    return {'state': 'verified', 'observed_bot_id': verified}


async def _allow_registered_account(owner, account):
    """Extend only the explicit allowlist for an opted-in, verified instance."""
    if getattr(owner, 'bot_scope_mode', 'all') != 'allowlist':
        return
    current = getattr(owner, 'bot_scope_ids', [])
    values = re.split(r'[,\s、;；]+', current) if isinstance(current, str) else list(current)
    if account in values:
        return
    config = getattr(owner, 'config', None)
    if config is None or not callable(getattr(config, 'save_config', None)):
        raise RuntimeError('scope_config_not_writable')
    section = config.get('basic_config')
    target = section if isinstance(section, dict) else config
    before = target.get('bot_scope_ids', [])
    target['bot_scope_ids'] = [*values, account]
    try:
        result = config.save_config()
        if inspect.isawaitable(result):
            result = await result
        if result is False:
            raise RuntimeError('scope_config_save_failed')
    except Exception:
        target['bot_scope_ids'] = before
        raise
    owner.bot_scope_ids = [*values, account]


async def reconcile(owner, *, force=False):
    """Bind once from a registered adapter; never replace an existing account."""
    if not _path(owner):
        return {}
    lock = getattr(owner, '_bot_account_sync_lock', None)
    if lock is None:
        lock = owner._bot_account_sync_lock = asyncio.Lock()
    async with lock:
        now = time.monotonic()
        if not force and now - getattr(owner, '_bot_account_last_scan', -INTERVAL) < INTERVAL:
            return getattr(owner, '_bot_account_status', {})
        owner._bot_account_last_scan = now
        bindings = _read(owner)
        original = json.dumps(bindings, sort_keys=True)
        enabled = {pid: value for pid, value in bindings.items()
                   if isinstance(value, dict) and value.get('auto_bind')}
        manager = getattr(getattr(owner, 'context', None), 'platform_manager', None)
        getter = getattr(manager, 'get_insts', None)
        platforms = list(getter() if callable(getter) else getattr(manager, 'platform_insts', []) or [])
        by_adapter = {}
        for platform in platforms:
            meta = platform.meta()
            if str(meta.name) == 'aiocqhttp':
                by_adapter.setdefault(str(meta.id), []).append(platform)
        async def probe_registered(binding):
            adapter = str(binding.get('adapter_id') or '')
            matches = by_adapter.get(adapter, [])
            if not adapter or len(matches) != 1:
                return {'state': 'adapter_unavailable' if len(matches) < 2 else 'ambiguous_adapter'}
            return await _probe(matches[0])

        results = dict(zip(enabled, await asyncio.gather(
            *(probe_registered(binding) for binding in enabled.values()),
        )))
        changed = False
        primaries = {str(b.get('primary_user_id') or '') for b in bindings.values() if isinstance(b, dict)}
        for pid, result in results.items():
            binding = enabled[pid]
            if result['state'] != 'verified':
                continue
            account = result['observed_bot_id']
            expected = str(binding.get('bot_id') or '')
            if expected and expected != account:
                result['state'] = 'account_change_blocked'
                continue
            if account in primaries:
                result['state'] = 'primary_user_account_blocked'
                continue
            duplicates = [other for other, b in bindings.items() if other != pid and isinstance(b, dict)
                          and (str(b.get('bot_id') or '') == account or
                               (b.get('auto_bind') and b.get('adapter_id') == binding.get('adapter_id')))]
            # Include other simultaneous discoveries, before either has been persisted.
            duplicates += [other for other, r in results.items() if other != pid and r.get('observed_bot_id') == account]
            if duplicates:
                result['state'] = 'account_or_adapter_conflict'
                continue
            if not expected:
                binding['bot_id'] = account
                binding.setdefault('session_mode', 'onebot')
                changed = True
            result['state'] = 'bound'
        if changed:
            if json.dumps(_read(owner), sort_keys=True) != original:
                owner._bot_account_last_scan = -INTERVAL
                return {}
            _write(_path(owner), bindings)
        for pid, result in results.items():
            if result['state'] == 'bound':
                try:
                    await _allow_registered_account(owner, result['observed_bot_id'])
                except Exception:
                    result['state'] = 'scope_update_failed'
        # Only state transitions write the diagnostics file; no credentials or chat data.
        previous = getattr(owner, '_bot_account_status', {})
        owner._bot_account_status = results
        if results != previous:
            _write(_path(owner).with_name('bot_account_status.json'),
                   {'checked_at': int(time.time()), 'accounts': results})
            for pid, result in results.items():
                if result != previous.get(pid):
                    logger.info('[QQ自动绑定] 人格=%s 状态=%s', pid, result['state'])
        return results


async def before_event(owner, event):
    """Handle the first actual message even before the periodic connection scan."""
    origin = str(getattr(event, 'unified_msg_origin', '') or '')
    adapter = origin.partition(':')[0]
    for binding in _read(owner).values():
        if isinstance(binding, dict) and binding.get('auto_bind') and binding.get('adapter_id') == adapter:
            getter = getattr(event, 'get_self_id', None)
            own = str(getter() if callable(getter) else '')
            await reconcile(owner, force=not binding.get('bot_id') or own != str(binding.get('bot_id')))
            break


async def watch_connections(owner):
    while not owner._stop_event.is_set():
        try:
            await reconcile(owner)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[QQ自动绑定] 连接检查失败: %s', type(exc).__name__)
        try:
            await asyncio.wait_for(owner._stop_event.wait(), timeout=INTERVAL)
        except asyncio.TimeoutError:
            pass
