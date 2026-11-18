"""One scoped configuration writer, with CAS, locks, effective values and undo."""
from contextvars import ContextVar
from contextlib import asynccontextmanager
import functools
import json
import sys
import time
import uuid

from .db import dumps
from .integrations import companion_context
from .policy import PERSONA

SOURCE = ContextVar('evolution_config_source', default='manual')
PROTECTED = {'enable_companion_memory'}


class Configuration:
    def __init__(self, context, store):
        self.context, self.store = context, store
        self.bound = []

    def bind(self, cp):
        """Keep old UI/chat entry points, but share the same owner-scoped writer."""
        from .integrations import owner_identity
        control = cp._get_function_awareness().control
        marker = getattr(control, '_evolution_controller', None)
        if marker is self:
            return
        if marker is not None:
            marker.unbind()
        persist = control.persist
        @functools.wraps(persist)
        async def governed(scope, key, target):
            if scope.get('persona_id') != PERSONA or SOURCE.get().startswith('controller:'):
                return await persist(scope, key, target)
            # ConfigControl.run already holds this exact lock.
            return await self.apply(owner_identity(), key, target, cp.persona_setting(key),
                '原聊天配置入口', _lock_held=True)
        control.persist = governed
        control._evolution_controller = self
        self.bound.append((control, 'persist', persist, governed))
        adapter = cp.page_api
        original = adapter._apply_config_value
        @functools.wraps(original)
        def manual(key, value, *args, **kwargs):
            primary = (cp._active_persona_scope() or cp._primary_persona_id()) == PERSONA
            registered = primary and key in control.specs() and key not in PROTECTED
            current = cp.persona_setting(key) if registered else None
            result = original(key, value, *args, **kwargs)
            if registered and not SOURCE.get().startswith('controller:') and current != cp.persona_setting(key):
                self.store.audit('manual_configuration', 'existing_dashboard', {'key': key, 'before': current,
                    'after': cp.persona_setting(key), 'persona': PERSONA})
                revision, ver = self.store.setting('config_revision:' + key, 0)
                self.store.set_setting('config_revision:' + key, revision + 1, ver)
            return result
        adapter._apply_config_value = manual
        self.bound.append((adapter, '_apply_config_value', original, manual))
        tune = adapter._maybe_apply_personality_iteration_auto_tune
        @functools.wraps(tune)
        async def unified_tune(users, groups):
            if (cp._active_persona_scope() or cp._primary_persona_id()) != PERSONA:
                return await tune(users, groups)
            if not cp.persona_setting('enable_personality_iteration_experiment', False) or not cp.persona_setting('enable_personality_iteration_auto_tune', False):
                return {'changed': False, 'reason': '自主配置统一治理；角色调节开关未开启'}
            suggestions = adapter._personality_iteration_suggestions(users, groups)
            refs = {k: adapter._personality_auto_tune_current_value(k) for k in adapter.PERSONALITY_AUTO_TUNE_KEYS}
            plan = adapter._personality_iteration_auto_tune_plan(suggestions, refs)
            changes = []
            for key, entry in plan.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    changes.append(await self.apply(owner_identity(), key, entry['value'],
                        cp.persona_setting(key), entry.get('reason', '角色贴合观察'), autonomous=True))
                except (PermissionError, ValueError) as exc:
                    changes.append({'changed': False, 'reason': str(exc)})
            return {'changed': any(c.get('changed') for c in changes), 'changes': changes, 'governance': 'unified'}
        adapter._maybe_apply_personality_iteration_auto_tune = unified_tune
        self.bound.append((adapter, '_maybe_apply_personality_iteration_auto_tune', tune, unified_tune))

    def unbind(self):
        for owner, name, original, wrapper in reversed(self.bound):
            if getattr(owner, name, None) is wrapper:
                setattr(owner, name, original)
                if name == 'persist':
                    owner._evolution_controller = None
        self.bound.clear()

    def enabled(self):
        return self.store.setting('self_configuration', {'enabled': True, 'cooldown_seconds': 3600, 'daily_changes': 8})[0]

    async def persist_value(self, cp, key, target):
        """Save only this field and its documented aliases; never restore an old whole config."""
        updates = {key: target}
        if key == 'text_message_debounce_seconds': updates['semantic_message_debounce_seconds'] = target
        if key == 'enable_message_debounce': updates['enable_semantic_message_debounce'] = target
        before = {name: cp.persona_setting(name) for name in updates}
        adapter = cp.page_api
        try:
            for name, value in updates.items(): adapter._apply_config_value(name, value, {})
            if not await adapter._save_config_if_possible(): raise RuntimeError('配置保存失败')
        except BaseException:
            # A newer manual edit wins. Only undo a value that still equals our own write.
            for name, value in updates.items():
                if cp.persona_setting(name) == value:
                    adapter._apply_config_value(name, before[name], {})
            try: await adapter._save_config_if_possible()
            except Exception: pass
            raise
        if cp.persona_setting(key) != target:
            raise ValueError('保存期间出现新的人工修改，保留人工值，当前调整未确认')

    def catalogue(self):
        with companion_context(self.context) as cp:
            control = cp._get_function_awareness().control
            locks, _ = self.store.setting('configuration_locks', {})
            return {k: {**v, 'locked': bool(locks.get(k)), 'revision': self.store.setting('config_revision:' + k, 0)[0]}
                    for k, v in control.specs().items() if k not in PROTECTED}

    def lock(self, identity, key, locked):
        identity.assert_owner()
        if key not in self.catalogue():
            raise ValueError('配置项未登记')
        values, revision = self.store.setting('configuration_locks', {})
        values[key] = bool(locked)
        self.store.set_setting('configuration_locks', values, revision)
        self.store.audit('configuration_lock', identity.user, {'key': key, 'locked': bool(locked)})
        return {'ok': True}

    async def apply(self, identity, key, value, expected_value, reason, *, autonomous=False, _lock_held=False):
        identity.assert_owner()
        if not str(reason).strip():
            raise ValueError('需要说明修改原因')
        config = self.enabled()
        with companion_context(self.context) as cp:
            control = cp._get_function_awareness().control
            @asynccontextmanager
            async def hold():
                if _lock_held:
                    yield
                else:
                    async with control.lock:
                        yield
            async with hold():
                specs = self.catalogue()
                if key not in specs:
                    raise PermissionError('不在当前人格可自主修改的字段范围')
                if autonomous and (not config.get('enabled') or specs[key]['locked']):
                    raise PermissionError('自主配置已关闭或此项被手动锁定')
                module = sys.modules[type(control).__module__]
                encode = lambda v: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                target = module.parse_value(key, encode(value))
                expected = module.parse_value(key, encode(expected_value))
                current = cp.persona_setting(key)
                if type(current) is not type(expected) or current != expected:
                    raise ValueError('当前值已变化，请重新读取')
                if target == current:
                    return {'ok': True, 'changed': False, 'effective': current}
                if autonomous:
                    recent = self.store.rows('SELECT at FROM config_changes WHERE persona=? AND state=? AND at>?',
                        (PERSONA, 'applied', time.time()-86400))
                    if len(recent) >= int(config.get('daily_changes', 8)):
                        raise PermissionError('已达到每日自主调节次数')
                    last = self.store.rows('SELECT at FROM config_changes WHERE persona=? AND key=? AND state=? ORDER BY at DESC LIMIT 1',
                        (PERSONA, key, 'applied'))
                    if last and time.time() - last[0]['at'] < int(config.get('cooldown_seconds', 3600)):
                        raise PermissionError('此项仍处于调整观察期')
                for pid in cp._configured_multi_persona_ids():
                    if pid == PERSONA:
                        continue
                    other = cp._persona_profile_snapshot_if_exists(pid) or {}
                    if key not in other.get('persona_settings', {}):
                        raise PermissionError('其他人格仍继承此项，不能自主修改共享值')
                cid = 'config_' + uuid.uuid4().hex
                with self.store.transaction() as db:
                    db.execute('INSERT INTO config_changes VALUES(?,?,?,?,?,?,?,?,?)',
                        (cid, 'astrbot_plugin_private_companion', PERSONA, key, dumps(current), dumps(target), str(reason)[:500], time.time(), 'pending'))
                token = SOURCE.set('controller:autonomous' if autonomous else 'controller:manual')
                try:
                    await self.persist_value(cp, key, target)
                except BaseException:
                    with self.store.transaction() as db:
                        db.execute("UPDATE config_changes SET state='failed' WHERE id=?", (cid,))
                    raise
                finally:
                    SOURCE.reset(token)
                with self.store.transaction() as db:
                    db.execute("UPDATE config_changes SET state='applied' WHERE id=?", (cid,))
                revision, version = self.store.setting('config_revision:' + key, 0)
                self.store.set_setting('config_revision:' + key, revision + 1, version)
                effective = cp._proactive_intensity_effect(key, target) if hasattr(cp, '_proactive_intensity_effect') else target
                return {'ok': True, 'changed': True, 'change_id': cid, 'before': current, 'saved': cp.persona_setting(key),
                        'effective': effective if effective is not None else cp.persona_setting(key), 'reason': reason}

    async def undo(self, identity, change_id):
        rows = self.store.rows('SELECT * FROM config_changes WHERE id=? AND persona=?', (change_id, PERSONA))
        if not rows or rows[0]['state'] != 'applied':
            raise ValueError('没有可撤销的配置变更')
        row = rows[0]
        result = await self.apply(identity, row['key'], json.loads(row['before_value']), json.loads(row['after_value']), '撤销变更 ' + change_id)
        with self.store.transaction() as db:
            db.execute("UPDATE config_changes SET state='reverted' WHERE id=?", (change_id,))
        return result
