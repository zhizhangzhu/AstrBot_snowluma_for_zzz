"""One life entry, independent children, and compatibility with old profiles."""
from __future__ import annotations

LIFE_FLAGS = frozenset({
    'enable_real_schedule', 'enable_humanized_states', 'enable_daily_diary',
    'enable_daily_review', 'enable_daily_greetings', 'enable_enhanced_dreams',
    'inject_passive_states', 'enable_rest_reply_simulation', 'enable_busy_reply_gate',
    'enable_health_state', 'enable_hunger_state', 'enable_cycle_state',
})
BODY_FLAGS = frozenset({'inject_passive_states', 'enable_health_state',
    'enable_hunger_state', 'enable_cycle_state', 'enable_rest_reply_simulation',
    'enable_busy_reply_gate'})


def resolve(key, value, raw):
    master = raw('enable_companion_life', None)
    if master is None:
        return value  # An unmigrated/standalone host keeps its old contract.
    if key in ('enable_daily_plan', 'enable_detail_enhancement'):
        return False  # Legacy simulated schedules are archived, never parallel.
    if key in LIFE_FLAGS and not master:
        return False
    if key in BODY_FLAGS and not raw('enable_humanized_states', True):
        return False
    return value


def configured(host):
    from .persona_config import runtime_persona_setting
    return runtime_persona_setting(host, 'enable_companion_life', None) is not None


def active(host):
    from .persona_config import runtime_persona_setting
    return runtime_persona_setting(host, 'enable_companion_life', None) is not False


def migrate_config(host, config):
    """Preserve every child's existing value; never enable a body/social feature."""
    version = host._cfg_raw(config, 'companion_life_config_version', 0)
    if int(version or 0) < 1:
        master = bool(host._cfg_raw(config, 'enable_real_schedule', False) or
                      host._cfg_raw(config, 'enable_humanized_states', True))
        group = config.setdefault('companion_life_config', {})
        group.update(enable_companion_life=master, companion_life_config_version=1)
        config['enable_companion_life'] = master
        config['companion_life_config_version'] = 1
        save = getattr(config, 'save_config', None)
        if callable(save):
            save()
    host.enable_companion_life = host._cfg_bool(config, 'enable_companion_life', False)
    host.real_schedule_max_retry_attempts = host._cfg_int(config, 'real_schedule_max_retry_attempts', 3, 1, 8)
    host.real_schedule_retry_backoff_minutes = host._cfg_int(config, 'real_schedule_retry_backoff_minutes', 30, 5, 240)


async def generate_dream(host):
    """A creative artifact, explicitly separate from actual external experiences."""
    from .persona_config import runtime_persona_setting as setting
    if not active(host) or not setting(host, 'enable_enhanced_dreams', False):
        return None
    day = host._environment_now().strftime('%Y-%m-%d')
    if host.data.get('daily_dream', {}).get('date') == day:
        return None
    lock = getattr(host, '_life_dream_lock', None)
    if lock is None:
        import asyncio
        lock = host._life_dream_lock = asyncio.Lock()
    async with lock:
        if host.data.get('daily_dream', {}).get('date') == day:
            return None
        pick = await host._generate_enhanced_dream_pick(host.data.get('daily_weather', {}))
        if not pick:
            return None
        host._remember_daily_dream_pick(pick)
        dream = host.data.get('daily_dream', {})
        dream.update(fictional=True, source='dream_creation', real_experience=False)
        host._save_data_sync(sections={'daily_dream'})
        return dream


def roleplay_context(host, *, for_reply=False):
    from .persona_config import runtime_persona_setting as setting
    if not active(host) or (for_reply and not setting(host,'inject_passive_states',True)):
        return ''
    state = host.data.get('daily_state', {}) if setting(host, 'enable_humanized_states', False) else {}
    dream = host.data.get('daily_dream', {}) if setting(host, 'enable_enhanced_dreams', False) else {}
    values = {k: state[k] for k in ('energy', 'mood_bias', 'sleep', 'health', 'hunger', 'body_cycle') if state.get(k) not in (None, '')}
    for field,flag in (('health','enable_health_state'),('hunger','enable_hunger_state'),('body_cycle','enable_cycle_state')):
        if not setting(host,flag,True):values.pop(field,None)
    if dream.get('date') == host._environment_now().strftime('%Y-%m-%d'):
        values['dream'] = str(dream.get('content') or dream.get('label') or '')[:600]
    if not values:
        return ''
    import json
    return '\n[角色状态与梦境：仅为表达和创作参考，不是现实身体状态或外界经历，不得当作完成日程的证据]\n' + json.dumps(values, ensure_ascii=False)
