"""Account routing metadata only; credentials stay in each QQ instance."""
import json
from pathlib import Path

def account_binding(owner):
    persona = getattr(owner, '_active_persona_scope', lambda: '')() or getattr(owner, '_primary_persona_id', lambda: '')()
    data_dir = getattr(owner, 'data_dir', None)
    if data_dir is None:
        return None
    path = Path(data_dir) / 'bot_accounts.json'
    if not path.exists():
        return None
    bindings = json.loads(path.read_text())
    value = bindings.get(persona)
    if not isinstance(value, dict):
        raise RuntimeError('当前人格尚未绑定 QQ 实例')
    return value

def browser_session(owner):
    from .qzone_browser_session import configured_session, load_session, BrowserSessionError
    binding = account_binding(owner)
    if binding is None:
        return configured_session()
    if not binding.get('bot_id') or not binding.get('session_file'):
        raise BrowserSessionError('当前人格尚未完成 QQ 登录绑定')
    return load_session(binding['session_file'], str(binding['bot_id']))
