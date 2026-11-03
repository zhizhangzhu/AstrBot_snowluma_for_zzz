"""Record only successfully delivered original library assets in the matching persona."""
async def remember_sent(host,event,image):
    from astrbot.api import logger
    token=None
    try:
        token,_=await host._activate_persona_for_event_context(event)
        import importlib
        module=importlib.import_module(type(host).__module__.rsplit('.',1)[0]+'.sent_image_context')
        module.record_confirmed(host,event.unified_msg_origin,[image],event)
    except Exception:
        logger.warning('已发送表情原图上下文未能保存',exc_info=True)
    finally:
        if token is not None:host._deactivate_persona_for_event(token)
