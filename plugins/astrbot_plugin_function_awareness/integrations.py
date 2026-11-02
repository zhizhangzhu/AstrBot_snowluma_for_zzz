"""Optional, read-only integration with Companion 6.7 and Memory 2.3."""
from contextlib import contextmanager, closing
import json
import sqlite3


def plugin_instance(context, folder):
    for item in context.get_all_stars():
        if not getattr(item,'activated',False):
            continue
        if getattr(item,'root_dir_name','') == folder or getattr(item,'name','') == folder:
            return getattr(item,'star_cls',None)
    return None


@contextmanager
def companion_scope(context, persona):
    plugin=plugin_instance(context,'astrbot_plugin_private_companion')
    token=None
    if plugin is not None:
        try:
            primary=plugin._primary_persona_id()
            if persona != primary and not plugin._persona_config_exists(persona):
                plugin=None
            elif getattr(plugin,'enable_multi_persona_mode',False):
                token=plugin._activate_persona_id(persona,allow_inactive=True)
                if token is None:
                    plugin=None
        except (AttributeError,TypeError,ValueError):
            plugin=None
    try:
        yield plugin
    finally:
        if token is not None:
            plugin._deactivate_persona_for_event(token)


def setting(plugin, key, default=False):
    getter=getattr(plugin,'persona_setting',None)
    return getter(key,default) if callable(getter) else getattr(plugin,key,default)


def known_people(context, scoped, companion):
    """Names only. Never scan historical text or cross a Bot/persona partition."""
    people={}
    def add(uid,name,source):
        uid=str(uid or '').strip()
        if not uid or uid in ('self',scoped['bot_id']):
            return
        item=people.setdefault(uid,{'user_id':uid,'name':str(name or uid)[:80],'sources':[]})
        if source not in item['sources']:
            item['sources'].append(source)
    if companion is not None:
        data=companion.data
        for uid,user in data.get('users',{}).items():
            if not isinstance(user,dict):
                continue
            # Stored contact must belong to the same adapter instance.
            if str(user.get('umo','')).startswith(scoped['platform_id']+':'):
                add(uid,user.get('nickname'), '陪伴互动档案')
        root=data.get('unified_person',{})
        profiles=root.get('profiles',{}) if isinstance(root,dict) else {}
        for link in root.get('identity_links',{}).values() if isinstance(root,dict) else []:
            identity=link.get('identity',{})
            # Unknown legacy identities are not attributed to this Bot.
            if str(identity.get('bot_id','')) != scoped['bot_id']:
                continue
            profile=profiles.get(link.get('person_id'),{})
            add(identity.get('user_id'),profile.get('display_name'),'统一人物档案')
    memory=plugin_instance(context,'astrbot_plugin_memory_companion')
    store=getattr(getattr(memory,'service',None),'store',None)
    path=getattr(store,'db_path',None)
    omitted_legacy=True
    if path and scoped['bot_id'] and scoped['persona_id']:
        # A separate read-only connection avoids mutating the owner's store.
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            sql='''SELECT subject_kind,subject_id,subject_name,object_kind,object_id,object_name
                   FROM memories WHERE owner_bot_id=? AND json_valid(metadata)
                   AND json_extract(metadata,'$.persona_id')=?
                   AND review_status NOT IN ('rejected','deleted')
                   AND validity_status NOT IN ('retracted','superseded','invalid','deleted')
                   LIMIT 5000'''
            for sk,si,sn,ok,oi,oname in db.execute(sql,(scoped['bot_id'],scoped['persona_id'])):
                if sk in ('user','person','member'):add(si,sn,'当前Bot和人格的记忆')
                if ok in ('user','person','member'):add(oi,oname,'当前Bot和人格的记忆')
    return {'items':list(people.values()),'complete':False,
            'note':'仅列已建档互动对象及明确归属当前Bot/人格的记忆人物；未建档、已清除或归属不明的旧记录不计入。'}
