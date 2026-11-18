"""Readable, explicit user grants over existing Companion activity identifiers."""
import importlib
from copy import deepcopy
from .configuration import SOURCE
from .integrations import companion_context
from .policy import PERSONA

DEFAULT_ACTIVITIES = ['extension:astrbot_plugin_autonomous_development:' + key
                      for key in ('develop', 'propose', 'learn', 'configure')]


def catalogue(context):
    with companion_context(context) as cp:
        module = importlib.import_module(type(cp).__module__.rsplit('.', 1)[0] + '.real_schedule.catalog')
        selected = cp.persona_setting('real_schedule_allowed_extensions', []) or []
        return [{**c.public(), 'authorized': c.id in selected, 'installed': True, 'implemented': True}
                for c in module.live_catalog(cp) if c.id.startswith('extension:')]


async def save_explicit_grants(context, values):
    """Used by authenticated grants and the explicitly authorized one-time default migration."""
    if not isinstance(values, list) or len(values)>100 or not all(isinstance(v,str) and v.startswith('extension:') and len(v)<200 for v in values):
        raise ValueError('活动授权须为完整标识列表')
    with companion_context(context) as cp:
        async with cp._get_function_awareness().control.lock:
            before = list(cp.persona_setting('real_schedule_allowed_extensions', []) or [])
            for persona in cp._configured_multi_persona_ids():
                if persona == PERSONA: continue
                profile = cp._persona_profile_snapshot_if_exists(persona) or {}
                if 'real_schedule_allowed_extensions' not in profile.get('persona_settings', {}):
                    effective = cp.get_persona_setting('real_schedule_allowed_extensions', persona, before)
                    result = await cp._update_persona_settings_async(persona,
                        changes={'real_schedule_allowed_extensions': deepcopy(effective)}, follow_primary_keys=[],
                        expected_revision=profile.get('persona_settings_revision', 0))
                    if not result.get('ok'): raise RuntimeError('无法先隔离其他人格的活动授权')
            token = SOURCE.set('controller:manual')
            try:
                target = list(dict.fromkeys(values))
                cp.page_api._apply_config_value('real_schedule_allowed_extensions', target, {})
                try:
                    if not await cp.page_api._save_config_if_possible():
                        raise RuntimeError('活动授权未能保存')
                except BaseException:
                    if cp.persona_setting('real_schedule_allowed_extensions', []) == target:
                        cp.page_api._apply_config_value('real_schedule_allowed_extensions', before, {})
                    raise
                if cp.persona_setting('real_schedule_allowed_extensions', []) != target:
                    raise ValueError('保存期间人工更新了活动授权，保留人工值，请刷新')
            finally: SOURCE.reset(token)
            return {'before': before, 'after': list(cp.persona_setting('real_schedule_allowed_extensions', []) or [])}


async def migrate_defaults(context, store):
    """One-time grant explicitly authorized by the user on 2026-11-03.

    Later user opt-outs are never restored by a restart. Development mode,
    stage approvals, knowledge selection and per-persona isolation still apply.
    """
    if store.setting('four_activity_defaults_v1', False)[0]:
        return
    with companion_context(context) as cp:
        before = list(cp.persona_setting('real_schedule_allowed_extensions', []) or [])
    result = await save_explicit_grants(context, list(dict.fromkeys([*before, *DEFAULT_ACTIVITIES])))
    store.audit('user_authorized_default_activities', 'user:1005145948', result)
    store.set_setting('four_activity_defaults_v1', True)
