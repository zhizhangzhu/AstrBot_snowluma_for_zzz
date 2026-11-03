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
    return host.real_schedule_control(request) if host else {'managed':False,'enabled':None}


def account(context, **usage):
    host=companion(context)
    if host:return host.real_schedule_record_external_usage(**usage)
    return False


def automatic_blocked(context, *, force=False, **request):
    status=control(context,**request)
    return bool(status.get('managed') and (not force or status.get('selected'))
        and (status.get('halted') or not status.get('execution_allowed')))
