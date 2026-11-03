"""Narrow public bridge for cooperating plugins; never a model-callable config API."""
from __future__ import annotations

from .runtime import enabled, selected, service


def control(host, request=None):
    request=request or {}
    if not enabled(host):return {'managed':False,'enabled':False}
    runtime=service(host)
    try:binding=runtime.binding()
    except ValueError:return {'managed':False,'enabled':True,'reason':'QQ身份尚未就绪'}
    # Explicit namespaces are never borrowed from another account/persona.
    for key in ('bot_id','persona_id'):
        if request.get(key) and str(request[key])!=binding[key]:
            return {'managed':False,'enabled':True,'reason':'作用域不匹配'}
    umo=str(request.get('umo') or '')
    if umo and umo.split(':',1)[0]!=binding['platform_id']:
        return {'managed':False,'enabled':True,'reason':'平台不匹配'}
    if umo and not request.get('persona_id'):
        personas=getattr(host,'_scheduler_persona_ids',lambda:[])()
        if len(set(personas))>1:
            return {'managed':False,'enabled':True,'reason':'插件须提供明确人格身份'}
    return {'managed':True,'enabled':True,**binding,'halted':runtime.halted(),
        'selected':selected(host),'execution_allowed':runtime.execution_allowed(),
        'day':runtime.day()}


def record_usage(host, **usage):
    if not enabled(host) or not selected(host):return False
    # Use the native accounting path as well as the autonomous ledger. Calling
    # Runtime.usage separately here would count a provider call twice.
    host._record_llm_usage(provider_id=str(usage.get('provider_id') or ''),
        task='external_'+str(usage.get('task') or 'background'),
        prompt=str(usage.get('prompt') or ''),completion=str(usage.get('completion') or ''),
        elapsed_ms=int(usage.get('elapsed_ms') or 0),success=usage.get('success',True),
        error=str(usage.get('error') or ''),resp=usage.get('resp'),budget_exempt=False)
    return True


def checkpoint(host, progress):
    from .runtime import EXECUTION
    ctx=EXECUTION.get()
    if not enabled(host) or not selected(host) or not ctx.get('activity_id'):return False
    runtime=service(host)
    if not runtime.execution_allowed():return False
    return runtime.ledger.checkpoint(ctx['scope'],ctx['activity_id'],ctx['lease'],progress)
