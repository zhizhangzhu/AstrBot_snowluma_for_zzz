"""Only verified event identities and public, scoped plugin bridges are accepted."""
from contextlib import contextmanager
import inspect

from .policy import Identity, BOT, PERSONA, PLATFORM, USER


def installed(context, name):
    for star in context.get_all_stars():
        if getattr(star, 'activated', False) and name in (getattr(star, 'name', ''), getattr(star, 'root_dir_name', '')):
            value = getattr(star, 'star_cls', None)
            if value is not None and not inspect.isclass(value):
                return value
    return None


def dependencies(context):
    companion = installed(context, 'astrbot_plugin_private_companion')
    memory = installed(context, 'astrbot_plugin_memory_companion')
    if companion is None or memory is None or getattr(memory, 'service', None) is None:
        raise RuntimeError('陪伴或记忆依赖尚未就绪')
    if companion._primary_persona_id() != PERSONA:
        raise RuntimeError('当前主人格不是已登记的小小情')
    return companion, memory


@contextmanager
def companion_context(context):
    cp, memory = dependencies(context)
    token = cp._activate_persona_id(PERSONA, allow_inactive=True) if getattr(cp, 'enable_multi_persona_mode', False) else None
    if getattr(cp, 'enable_multi_persona_mode', False) and token is None:
        raise PermissionError('小小情作用域不可用')
    try:
        binding = cp._qzone_watch_binding()
        if str(binding[1]) != PLATFORM or str(binding[2]) != BOT:
            raise PermissionError('陪伴账号绑定不匹配')
        yield cp
    finally:
        if token is not None:
            cp._deactivate_persona_for_event(token)


async def event_identity(context, event):
    origin = str(event.unified_msg_origin)
    if origin.split(':', 1)[0] != PLATFORM or str(event.get_self_id()) != BOT or str(event.get_sender_id()) != USER or event.get_group_id():
        raise PermissionError('仅小小情主要用户私聊可管理自主开发')
    manager = context.conversation_manager
    cid = await manager.get_curr_conversation_id(origin)
    conv = await manager.get_conversation(origin, cid) if cid else None
    cfg = context.get_config(umo=origin)
    resolved = await context.persona_manager.resolve_selected_persona(umo=origin,
        conversation_persona_id=getattr(conv, 'persona_id', None), platform_name='aiocqhttp',
        provider_settings=cfg.get('provider_settings', {}))
    if resolved[0] != PERSONA or resolved[1] is None:
        raise PermissionError('当前会话人格已改变')
    if getattr(event, 'binding', None) is not None:
        desktop = installed(context, 'astrbot_plugin_desktop_pet')
        binding = await desktop.authorized_binding(event) if desktop else None
        if binding is None or not desktop.store.enabled(binding) or binding.bot_id != BOT or binding.user_id != USER:
            raise PermissionError('桌宠设备未验证或已撤销')
    identity = Identity(BOT, PERSONA, USER, PLATFORM)
    identity.assert_owner()
    return identity


def owner_identity():
    return Identity(BOT, PERSONA, USER, PLATFORM)


async def wiki_event_identity(context, event):
    """Read-only Bot identity; deliberately does not grant development rights."""
    from .wiki import BOTS
    bot, user = str(event.get_self_id()), str(event.get_sender_id())
    origin = str(event.unified_msg_origin);platform = origin.split(':', 1)[0]
    entry = BOTS.get(bot)
    if entry is None or platform != entry['platform'] or not user:
        raise PermissionError('未登记的Wiki读取Bot')
    manager = context.conversation_manager
    cid = await manager.get_curr_conversation_id(origin)
    conv = await manager.get_conversation(origin, cid) if cid else None
    config = context.get_config(umo=origin)
    selected = await context.persona_manager.resolve_selected_persona(umo=origin,
        conversation_persona_id=getattr(conv, 'persona_id', None), platform_name='aiocqhttp',
        provider_settings=config.get('provider_settings', {}))
    if selected[0] != entry['name'] or selected[1] is None:
        raise PermissionError('当前人格与Wiki读取Bot不匹配')
    if getattr(event, 'binding', None) is not None:
        desktop = installed(context, 'astrbot_plugin_desktop_pet')
        binding = await desktop.authorized_binding(event) if desktop else None
        if binding is None or not desktop.store.enabled(binding) or (binding.bot_id,binding.persona_id,binding.user_id,binding.qq_platform_id) != (bot,entry['name'],user,platform):
            raise PermissionError('桌宠设备未验证或已撤销')
    return Identity(bot,entry['name'],user,platform,not bool(event.get_group_id()))
