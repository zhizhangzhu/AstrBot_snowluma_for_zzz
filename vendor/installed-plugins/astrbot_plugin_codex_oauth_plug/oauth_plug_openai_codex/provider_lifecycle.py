"""Rebuild only runtime instances owned by this OAuth adapter after reload."""
from __future__ import annotations
from copy import deepcopy
from .service import PROVIDER_TYPE


async def rebind_loaded_providers(context, service):
    manager=getattr(context,'provider_manager',None)
    if manager is None:return []
    instances=getattr(manager,'inst_map',None)
    configs=getattr(manager,'providers_config',None)
    reload_provider=getattr(manager,'reload',None)
    if not isinstance(instances,dict) or not isinstance(configs,list) or not callable(reload_provider):return []
    current=getattr(manager,'curr_provider_inst',None)
    current_id=next((key for key,value in instances.items() if value is current),None)
    rebuilt=[]
    for raw in list(configs):
        if not isinstance(raw,dict):continue
        resolver=getattr(manager,'get_merged_provider_config',None)
        merged=resolver(deepcopy(raw)) if callable(resolver) else raw
        if merged.get('type')!=PROVIDER_TYPE or not merged.get('enable',False):continue
        provider_id=str(raw.get('id') or '')
        previous=instances.get(provider_id)
        # At cold startup AstrBot loads providers after plugin initialization.
        # Do not eagerly load missing instances or create duplicate adapters.
        if previous is None:continue
        if getattr(previous,'_oauth_service',None) is service and not getattr(previous,'_oauth_media_closed',False):continue
        await reload_provider(deepcopy(raw))
        replacement=instances.get(provider_id)
        if replacement is None or replacement is previous or getattr(replacement,'_oauth_service',None) is not service or getattr(replacement,'_oauth_media_closed',False):
            raise RuntimeError('OAuth 模型实例重建失败：'+provider_id)
        rebuilt.append(provider_id)
    if current_id and current_id in instances:
        manager.curr_provider_inst=instances[current_id]
    return rebuilt
