"""Optional cooperation; absent/disabled companion leaves native behavior intact."""
def _registration(context):
    try:return context.get_registered_star('astrbot_plugin_private_companion')
    except (AttributeError,RuntimeError):return None


def companion(context):
    star=_registration(context)
    if star is not None and getattr(star,'activated',True) is False:return None
    host=getattr(star,'star_cls',None)
    return host if callable(getattr(host,'real_schedule_control',None)) else None


def control(context, **request):
    star=_registration(context)
    if star is not None and getattr(star,'activated',True) is False:
        return {'managed':False,'enabled':False,'reason':'陪伴插件已停用，使用本插件原生配置'}
    host=companion(context)
    if not host:return {'managed':False,'enabled':None}
    token=None
    try:
        origin=str(request.get('umo') or '')
        if origin:
            cfg=context.get_config(origin)
            persona=str(cfg.get('agent_runner',{}).get('config',{}).get('persona',{}).get('persona_id') or '')
            if not persona:return {'managed':False,'enabled':True,'reason':'当前人格尚未确定'}
            request.setdefault('persona_id',persona)
            token=host._activate_persona_id(persona)
        return host.real_schedule_control(request)
    finally:
        if token is not None:host._deactivate_persona_for_event(token)


def account(context, **usage):
    host=companion(context)
    if host:return host.real_schedule_record_external_usage(**usage)
    return False
