"""Typed bindings: never rewrite narrative text, authors, or arbitrary QQ numbers."""
from __future__ import annotations
from copy import deepcopy
import json
import hashlib
import re

BINDING_FIELDS = {'owner_bot_id', 'bot_id', 'self_id', 'account_id'}
PERSONA_FIELDS = {'persona_id', 'owner_persona_id', 'active_persona_id', 'plugin_specific_persona_id'}
SESSION_FIELDS = {'umo', 'session_id', 'unified_msg_origin', 'unified_message_origin', 'origin'}
HISTORICAL_TEXT = {'content', 'evidence', 'text', 'summary', 'prompt', 'system_prompt', 'title', 'author_id', 'uin', 'nickname'}
SECRET = re.compile(r'(?:api[_-]?key|password|passwd|cookie|secret|access[_-]?token|refresh[_-]?token|authorization|private[_-]?key|standalone_webui_access_token)', re.I)


def validate(binding):
    if not isinstance(binding, dict): raise ValueError('缺少账号绑定')
    result = {key: str(binding.get(key) or '').strip() for key in ('platform_id','bot_id','persona_id')}
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',result['platform_id']): raise ValueError('平台实例ID无效')
    if not re.fullmatch(r'[1-9]\d{4,14}',result['bot_id']): raise ValueError('QQ账号格式无效')
    if not result['persona_id'] or len(result['persona_id']) > 120 or any(ord(c) < 32 for c in result['persona_id']):
        raise ValueError('人格ID无效')
    return result


def scope(binding):
    return json.dumps([binding[k] for k in ('platform_id','bot_id','persona_id')],ensure_ascii=False,separators=(',',':'))


def scrub(value, field=''):
    if isinstance(value,dict):return {k:scrub(v,k) for k,v in value.items() if not SECRET.search(k)}
    if isinstance(value,list):return [scrub(v,field) for v in value]
    if isinstance(value,str) and ('headers' in field.lower() or field.lower().endswith('_json')):
        try:
            parsed=json.loads(value)
            if isinstance(parsed,(dict,list)):return json.dumps(scrub(parsed),ensure_ascii=False,separators=(',',':'))
        except (ValueError,TypeError):pass
    return value


def rebind(value, source, target, *, field='', path_map=None):
    if isinstance(value,dict):
        # Entity IDs only change when explicitly typed as the bot itself.
        result = {k:rebind(v,source,target,field=k,path_map=path_map) for k,v in value.items()}
        if value.get('kind') in ('bot','self','assistant') and str(value.get('id')) == source['bot_id']:
            result['id']=target['bot_id']
        if value.get('scope')=='persona' and value.get('target')==source['persona_id']:result['target']=target['persona_id']
        if value.get('scope')=='session' and str(value.get('target','')).startswith(source['platform_id']+':'):result['target']=target['platform_id']+value['target'][len(source['platform_id']):]
        if value.get('owner_kind') in ('persona','bot_persona') and value.get('owner_id')==source['persona_id']:result['owner_id']=target['persona_id']
        for prefix in ('subject','object','entity'):
            if value.get(prefix+'_kind') in ('bot','self','assistant') and str(value.get(prefix+'_id'))==source['bot_id']:
                result[prefix+'_id']=target['bot_id']
        return result
    if isinstance(value,list):return [rebind(v,source,target,field=field,path_map=path_map) for v in value]
    if not isinstance(value,str):return value
    if field in ('photo_reference_catalog','bot_relationship_cards','wardrobe_items','wardrobe_outfits') and value.lstrip().startswith(('{','[')):
        try:return json.dumps(rebind(json.loads(value),source,target,path_map=path_map),ensure_ascii=False,separators=(',',':'))
        except (ValueError,TypeError):return value
    if path_map and value in path_map:return path_map[value]
    if field in HISTORICAL_TEXT:return value
    if field in BINDING_FIELDS and value==source['bot_id']:return target['bot_id']
    if field in PERSONA_FIELDS and value==source['persona_id']:return target['persona_id']
    if field in ('platform_id','adapter_id') and value==source['platform_id']:return target['platform_id']
    if field=='session_id' and value.startswith('bot_personal:'):
        old='bot_personal:'+hashlib.sha256((source['bot_id']+'|'+source['persona_id']).encode()).hexdigest()[:20]
        if value==old:return 'bot_personal:'+hashlib.sha256((target['bot_id']+'|'+target['persona_id']).encode()).hexdigest()[:20]
    if field=='canonical_key' and value.startswith('bot_personal|'+source['bot_id']+'|'+source['persona_id']+'|'):
        return 'bot_personal|'+target['bot_id']+'|'+target['persona_id']+'|'+value.split('|',3)[3]
    if field in SESSION_FIELDS and value.startswith(source['platform_id']+':'):
        return target['platform_id']+value[len(source['platform_id']):]
    if field in ('scope','namespace_scope','source_scope','scope_ref'):
        if value==scope(source):return scope(target)
        if value.startswith('{'):
            try:
                body=json.loads(value)
                return json.dumps(rebind(body,source,target),ensure_ascii=False,sort_keys=True,separators=(',',':'))
            except (ValueError,TypeError):pass
    return value


def historical_metadata(value, source):
    result=deepcopy(value)
    result.setdefault('migration_original_binding',dict(source))
    return result


def export_config(value):
    """Remove host-local topology while retaining portable feature choices."""
    if isinstance(value,dict):
        return {k:export_config(v) for k,v in scrub(value).items()
            if 'proxy' not in k.lower() and not k.lower().startswith('standalone_webui_')
            and not k.lower().endswith('_port') and k.lower() not in {'data_dir','workspace_dir','storage_backend','storage_sqlite_path','bind_address','listen_host'}}
    if isinstance(value,list):return [export_config(v) for v in value]
    return value
