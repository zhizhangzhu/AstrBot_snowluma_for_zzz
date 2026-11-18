"""Explicit component adapters; uploaded data cannot name arbitrary tables/files."""
from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import importlib
from pathlib import Path
import re
import sqlite3
import uuid

from . import archive, binding, dependencies

# Stored data has been extended locally. Export durable sections, while execution
# queues, credentials and cached input state deliberately stay on the old host.
DATA_SECTIONS = frozenset('''version users groups worldbook_entries worldbook_member_profiles
worldbook_group_profiles worldbook_deleted_member_ids worldbook_deleted_group_ids
skill_growth can_do important_dates food_menu memo_notes creative_projects creative_memory_pool
bot_diaries dream_fragments daily_dream diary_generated_day daily_diary_deleted_days
body_cycle_state body_cycle_strategy_mode self_meal_log state_conditions
news_integration web_exploration bilibili_integration reading_archive_integration bookshelf_items
personal_goal_state personal_goals daily_review_reports daily_review_active_guidance daily_review_completed_day
expression_learning_runtime expression_voice_profile photo_reference_assets photo_reference_feedback
unified_person _req041_private_memory _req041_persona_expression_profile
persona_settings persona_settings_schema_version persona_settings_revision
calendar_version calendar_events calendar_rules calendar_exceptions calendar_candidates calendar_observations
observed_activities place_cognitive_maps yesterday_conversation_summary
boundary_feedback_reports boundary_feedback_vent_history'''.split())
VOLATILE_FIELDS = frozenset('''proactive_plan next_proactive_at planned_proactive_action planned_proactive_reason
planned_proactive_impulse_id planned_proactive_burst proactive_burst_index pending_followup_event
pending_actions pending_confirmation pending_atrelay_requests pending_atrelay_receipts
recent_messages message_history last_user_message last_user_message_at last_companion_message
last_companion_message_at typing_pending typing_expires_at suspended_proactive
life_publish_daily_plan pending_share_candidates pending_news_share last_probe_at
last_read_at last_explore_at last_fetch_at'''.split())
DB_SPECS = {
    'activities':('companion','real_schedule/activities.db',('activities','receipts','settings_state','consumer_offsets')),
    'sources':('companion','information_sources/sources.db',('information_sources',)),
    'relationships':('companion','req041_relationship.db',('relationship_accounts','relationship_events','relationship_account_changes')),
    'memory':('memory','memory_companion.db',('memories','identities','relationship_edges','timeline','memory_acl_rules','memory_acl_policies')),
    'scoped_memory':('memory','req041_scoped.db',('scoped_records',)),
}


def clean_data(value):
    if isinstance(value,dict):return {k:clean_data(v) for k,v in value.items() if k not in VOLATILE_FIELDS and not binding.SECRET.search(k)}
    if isinstance(value,list):return [clean_data(v) for v in value]
    return value


def roots(host):
    own=Path(host.data_dir).resolve()
    memory=dependencies.inventory(host).get('astrbot_plugin_memory_companion',{}).get('instance')
    service=getattr(memory,'service',None)
    return {'companion':own,'memory':Path(service.data_dir).resolve() if service else own.parent/'astrbot_plugin_memory_companion',
        'memes':own.parent/'meme_manager'}


def db_path(host, name):
    root,rel,_=DB_SPECS[name]
    if name=='memory':
        item=dependencies.inventory(host).get('astrbot_plugin_memory_companion',{}).get('instance')
        store=getattr(getattr(item,'service',None),'store',None)
        path=getattr(store,'db_path',None)
        if path:return Path(path).resolve()
    return roots(host)[root]/rel


def rows(path,tables):
    if not path.is_file():return {}
    result={}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        present={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in present:continue
            selected=db.execute('SELECT * FROM "'+table+'" LIMIT 100001').fetchall()
            if len(selected)>100000:raise ValueError('数据表过大，需要分批迁移：'+table)
            result[table]=[dict(r) for r in selected]
    return result


def parse(value):
    try:return json.loads(value) if isinstance(value,str) else value
    except (ValueError,TypeError):return {}


def canonical_persona(pid,primary):
    if primary:return 'default'
    source_scope='persona:'+hashlib.sha256(pid.encode()).hexdigest()[:24]
    return 'persona-'+hashlib.sha256(source_scope.encode()).hexdigest()[:32]


def scoped_rows(data, source, users, *, single_owner=False, primary=True):
    result={};excluded={}
    for table,items in data.items():
        keep=[]
        for row in items:
            match=False
            meta=parse(row.get('metadata','{}'));meta=meta if isinstance(meta,dict) else {}
            if 'scope' in row and str(row['scope']).startswith('['):
                match=row['scope']==binding.scope(source)
            elif 'namespace_scope' in row:
                ns=parse(row['namespace_scope'])
                match=isinstance(ns,dict) and ns.get('persona_id') in {source['persona_id'],canonical_persona(source['persona_id'],primary)}
            elif table=='settings_state':match=False
            elif table.startswith('relationship_') and 'identity_id' in row:
                match=single_owner
            elif table=='identities':match=str(row.get('entity_id')) in users|{source['bot_id']}
            elif row.get('owner_bot_id') or meta.get('owner_bot_id') or meta.get('bot_id'):
                owner=str(row.get('owner_bot_id') or meta.get('owner_bot_id') or meta.get('bot_id'))
                pid=meta.get('persona_id') or meta.get('owner_persona_id')
                match=owner==source['bot_id'] and (not pid or pid==source['persona_id'])
                # Older memory-plugin versions used "self" as the bot owner.
                # An exact platform session plus a sole persona is evidence of
                # ownership; never infer this across multiple persona archives.
                concrete={str(v) for v in (row.get('owner_bot_id'),meta.get('owner_bot_id'),meta.get('bot_id')) if v and str(v)!='self'}
                if (owner=='self' and single_owner and not concrete-{source['bot_id']}
                        and str(row.get('session_id','')).startswith(source['platform_id']+':')
                        and (not pid or pid==source['persona_id'])):
                    match=True;row=deepcopy(row);meta=deepcopy(meta)
                    meta['migration_legacy_owner_bot_id']='self'
                    meta['owner_bot_id']=source['bot_id']
                    if meta.get('bot_id')=='self':meta['bot_id']=source['bot_id']
                    if 'owner_bot_id' in row:row['owner_bot_id']=source['bot_id']
                    for role in ('subject','object','target'):
                        if row.get(role+'_kind') in ('bot','self','assistant') and row.get(role+'_id')=='self':
                            row[role+'_id']=source['bot_id']
                    row['metadata']=json.dumps(meta,ensure_ascii=False,separators=(',',':'))
            elif meta.get('persona_id'):
                match=meta['persona_id']==source['persona_id']
            elif str(row.get('session_id','')).startswith(source['platform_id']+':'):
                match=single_owner
            elif table=='memory_acl_rules':
                match=single_owner and str(row.get('owner_id')) in users|{source['bot_id']} and str(row.get('reader_id')) in users|{source['bot_id']}
            elif table=='memory_acl_policies':
                window=str(row.get('window_id') or '')
                match=single_owner and (window in users or window.startswith(source['platform_id']+':'))
            elif table=='relationship_edges':
                match=single_owner and (str(row.get('subject_id')) in users|{source['bot_id']} or str(row.get('object_id')) in users|{source['bot_id']})
            elif single_owner and table=='timeline':
                match=str(row.get('subject_id')) in users|{source['bot_id']}
            if match:keep.append(row)
        result[table]=keep
        if len(keep)<len(items):excluded[table]=len(items)-len(keep)
    if 'identities' in data:
        related=set(users)|{source['bot_id']}
        for table,items in result.items():
            if table=='identities':continue
            for row in items:
                related.update(str(row[k]) for k in ('subject_id','object_id','target_user_id') if row.get(k))
        result['identities']=[row for row in data['identities'] if str(row.get('entity_id')) in related]
        excluded.pop('identities',None)
        if len(result['identities'])<len(data['identities']):excluded['identities']=len(data['identities'])-len(result['identities'])
    return result,excluded


def export_databases(host, source, data, *, memory=False):
    result={};excluded={}
    multi=bool(getattr(host,'enable_multi_persona_mode',False))
    others=[p for p in getattr(host,'multi_persona_ids',[]) if str(p)!=source['persona_id']]
    single=not multi or not others
    for name,(root,rel,tables) in DB_SPECS.items():
        if (root=='memory') != memory:continue
        table_data=rows(db_path(host,name),tables)
        selected,skipped=scoped_rows(table_data,source,set(map(str,data.get('users',{}))),single_owner=single,primary=host._primary_persona_id()==source['persona_id'])
        result[name]=selected
        if skipped:excluded[name]=skipped
    return {'databases':result,'excluded_unrelated_or_ambiguous':excluded}


def normalize_memory_rows(host,docs):
    instance=dependencies.inventory(host).get('astrbot_plugin_memory_companion',{}).get('instance')
    service=getattr(instance,'service',None)
    module=getattr(type(service),'__module__','')
    if '.core.' not in module:return
    models=importlib.import_module(module.rsplit('.',1)[0]+'.models')
    for row in docs.get('memory',{}).get('databases',{}).get('memory',{}).get('memories',[]):
        # The installed plugin owns canonical-key/fingerprint derivation. This
        # also preserves compatibility with future additive minor releases.
        normalized=models.MemoryRecord.from_row(row).to_db()
        for key in ('canonical_key','content_fingerprint','owner_bot_id','metadata'):
            if key in row:row[key]=normalized[key]


def rebase_control_epoch(host,docs):
    rows=docs.get('memory',{}).get('databases',{}).get('scoped_memory',{}).get('scoped_records',[])
    if not rows:return
    path=db_path(host,'scoped_memory')
    if not path.is_file():return
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        try:value=db.execute('SELECT migration_epoch,policy_version FROM scoped_epoch_state WHERE singleton=1').fetchone()
        except sqlite3.OperationalError:return
    if value:
        for row in rows:row['migration_epoch'],row['policy_version']=value


def database_conflicts(host,docs,target):
    conflicts=[];missing=[];target_hash=hashlib.sha256()
    for component in ('memory','history'):
        for name,tables in docs.get(component,{}).get('databases',{}).items():
            if name not in DB_SPECS:raise ValueError('不支持的数据库模块')
            allowed=set(DB_SPECS[name][2])
            if not set(tables)<=allowed:raise ValueError('数据库表超出模块范围')
            path=db_path(host,name)
            if not path.is_file():
                if any(tables.values()):missing.append(name)
                continue
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                present={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table,items in tables.items():
                    if not isinstance(items,list) or len(items)>100000 or not all(isinstance(r,dict) for r in items):raise ValueError('数据库记录格式无效')
                    if table not in present:
                        if items:missing.append(name+':'+table)
                        continue
                    info=list(db.execute('PRAGMA table_info("'+table+'")'))
                    columns={r[1] for r in info};pk=[r[1] for r in info if r[5]]
                    for row in items:
                        if not row.keys()<=columns:raise ValueError('目标数据库字段不兼容：'+name+':'+table)
                        if not pk:raise ValueError('数据表缺少安全合并主键')
                        if row and not set(pk)<=row.keys():raise ValueError('档案记录缺少主键')
                        old=db.execute('SELECT '+','.join('"'+c+'"' for c in row)+' FROM "'+table+'" WHERE '+' AND '.join('"'+k+'"=?' for k in pk),[row[k] for k in pk]).fetchone()
                        target_hash.update(archive.encode([name,table,[row[k] for k in pk],list(old) if old is not None else None]))
                        if old is not None and list(old)!=list(row.values()):conflicts.append(name+':'+table)
    return {'conflicts':dict((x,conflicts.count(x)) for x in set(conflicts)),'missing_schemas':sorted(set(missing)),'target_digest':target_hash.hexdigest()}


def remap_refs(value, ids, field=''):
    if isinstance(value,dict):return {k:remap_refs(v,ids,k) for k,v in value.items()}
    if isinstance(value,list):return [remap_refs(v,ids,field) for v in value]
    if field in ('id','activity_id','receipt_id','memory_id','source_memory_id','source_id','supersedes_id','source_ids','receipt_ids','memory_ids'):
        return ids.get(str(value),value)
    return value


def mapped_databases(docs,source,target,companion_id,*,source_primary=True,target_primary=True):
    result=deepcopy(docs);ids={}
    changed=binding.scope(source)!=binding.scope(target)
    if changed:
        for part in ('memory','history'):
            for tables in result.get(part,{}).get('databases',{}).values():
                for name,items in tables.items():
                    for row in items:
                        if 'id' in row and (name!='identities' or (row.get('entity_kind') in ('bot','self','assistant') and str(row.get('entity_id'))==source['bot_id'])):
                            old=str(row['id'])
                            meta=next((parse(row[k]) for k in ('metadata','payload','body','profile') if k in row and isinstance(parse(row[k]),dict)),{})
                            seed=str(meta.get('migration_original_record_id') or old)
                            ids[old]=str(uuid.uuid5(uuid.NAMESPACE_URL,companion_id+binding.scope(target)+name+seed))
    ref_fields={'id','activity_id','receipt_id','memory_id','source_memory_id','source_id','supersedes_id'}
    for part in ('memory','history'):
        for dbname,tables in result.get(part,{}).get('databases',{}).items():
            for table,items in tables.items():
                for index,original in enumerate(items):
                    row=binding.rebind(original,source,target)
                    for col,value in list(row.items()):
                        if col in ref_fields and str(value) in ids and (table!='identities' or (original.get('entity_kind') in ('bot','self','assistant') and str(original.get('entity_id'))==source['bot_id'])):row[col]=ids[str(value)]
                        if col in ('body','payload','metadata','payload_json','profile') and isinstance(value,str):
                            decoded=parse(value)
                            if isinstance(decoded,(dict,list)):
                                decoded=remap_refs(binding.rebind(decoded,source,target),ids)
                                if isinstance(decoded,dict):
                                    if col!='payload_json':
                                        decoded.setdefault('migration_original_binding',dict(source))
                                        if 'id' in original:decoded.setdefault('migration_original_record_id',str(original['id']))
                                    if isinstance(decoded.get('payload'),dict) and 'payload_fingerprint' in decoded:decoded['payload_fingerprint']=archive.digest(archive.encode(decoded['payload']))
                                    for key in ref_fields:
                                        if str(decoded.get(key)) in ids:decoded[key]=ids[str(decoded[key])]
                                row[col]=json.dumps(decoded,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                                if col=='payload_json' and 'payload_hash' in row:row['payload_hash']=archive.digest(row[col].encode())
                    if table=='scoped_records':
                        ns=parse(row.get('namespace_scope',''))
                        if isinstance(ns,dict):
                            old_ref=canonical_persona(source['persona_id'],source_primary)
                            new_ref=canonical_persona(target['persona_id'],target_primary)
                            if ns.get('persona_id')==old_ref:ns['persona_id']=new_ref
                            # Group identifiers in this canonical table are hashes of
                            # persona namespace and a known group; raw QQ IDs stay intact.
                            for gid in result.get('companion',{}).get('data',{}).get('groups',{}):
                                old_group='group-'+hashlib.sha256((old_ref+':'+str(gid)).encode()).hexdigest()[:32]
                                if ns.get('group_id')==old_group:ns['group_id']='group-'+hashlib.sha256((new_ref+':'+str(gid)).encode()).hexdigest()[:32]
                            row['namespace_scope']=json.dumps(ns,ensure_ascii=False,sort_keys=True,separators=(',',':'))
                    if table=='activities':
                        if row.get('state') in ('planned','queued','paused','deferred','running'):
                            row.update(state='cancelled',note='迁移保留历史，旧计划不恢复执行')
                        row.update(lease='',lease_until=0)
                    if table=='settings_state' and not str(row.get('key','')).startswith(('interest_progress','skill:','goal:','verified_skill_migration')):
                        continue
                    items[index]=row
            if 'settings_state' in tables:
                tables['settings_state']=[r for r in tables['settings_state'] if str(r.get('key','')).startswith(('interest_progress','skill:','goal:','verified_skill_migration'))]
    return result




def prepare_undo(host,docs,policy):
    """Persist row-level preimages; whole-DB recovery must not lose new chats."""
    result=[]
    for part in ('memory','history'):
        for name,tables in docs.get(part,{}).get('databases',{}).items():
            if not any(tables.values()):continue
            with sqlite3.connect(db_path(host,name),timeout=10) as db:
                db.row_factory=sqlite3.Row
                for table,items in tables.items():
                    pk=[r[1] for r in db.execute('PRAGMA table_info("'+table+'")') if r[5]]
                    for row in items:
                        keys={k:row[k] for k in pk}
                        old=db.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'"=?' for k in pk),list(keys.values())).fetchone()
                        if old is not None and policy=='keep_current':continue
                        result.append({'database':name,'table':table,'keys':keys,'before':dict(old) if old is not None else None,'after':row})
    return result


def _lookup(db,table,keys):
    value=db.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'"=?' for k in keys),list(keys.values())).fetchone()
    return dict(value) if value is not None else None


def _matches(current,expected):
    if expected is None:return current is None
    return current is not None and all(current.get(k)==v for k,v in expected.items())


def _write_row(db,table,row,keys):
    current=_lookup(db,table,keys)
    if current is None:
        db.execute('INSERT INTO "'+table+'" ('+','.join('"'+k+'"' for k in row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
    else:
        db.execute('UPDATE "'+table+'" SET '+','.join('"'+k+'"=?' for k in row)+' WHERE '+' AND '.join('"'+k+'"=?' for k in keys),list(row.values())+list(keys.values()))


def apply_databases(host,docs,policy,undo):
    """CAS each touched row. The coordinator retains its journal across commits."""
    for name in dict.fromkeys(item['database'] for item in undo):
        with sqlite3.connect(db_path(host,name),timeout=15) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
            for item in undo:
                if item['database']!=name:continue
                current=_lookup(db,item['table'],item['keys'])
                if not _matches(current,item['before']):raise ValueError('目标记录在导入前被更新，请重新预览')
                _write_row(db,item['table'],item['after'],item['keys'])


def rollback_databases(host,undo):
    for name in dict.fromkeys(item['database'] for item in undo):
        if name not in DB_SPECS:raise ValueError('回滚数据库名称无效')
        with sqlite3.connect(db_path(host,name),timeout=15) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
            for item in reversed(undo):
                if item['database']!=name:continue
                current=_lookup(db,item['table'],item['keys'])
                if _matches(current,item['before']):continue
                if not _matches(current,item['after']):raise RuntimeError('回滚记录已被其他操作更新，保留现场等待人工处理')
                if item['before'] is None:
                    db.execute('DELETE FROM "'+item['table']+'" WHERE '+' AND '.join('"'+k+'"=?' for k in item['keys']),list(item['keys'].values()))
                else:_write_row(db,item['table'],item['before'],item['keys'])


def initialize_owned_storage(host):
    """Install local schemas at plugin startup, never as a preview side effect."""
    from ..real_schedule.ledger import Ledger
    from ..information_sources import SourceIndex
    Ledger(Path(host.data_dir)/'real_schedule/activities.db')
    SourceIndex(Path(host.data_dir)/'information_sources/sources.db')


def align_natural_keys(host,docs):
    """Match existing semantic unique keys before applying the chosen policy."""
    aliases={}
    for part in ('memory','history'):
        for name,tables in docs.get(part,{}).get('databases',{}).items():
            path=db_path(host,name)
            if not path.is_file():continue
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                db.row_factory=sqlite3.Row
                for table,items in tables.items():
                    info=list(db.execute('PRAGMA table_info("'+table+'")'));pk=[r[1] for r in info if r[5]]
                    if not pk:continue
                    unique=[]
                    for idx in db.execute('PRAGMA index_list("'+table+'")'):
                        if idx['unique']:
                            columns=[r[2] for r in db.execute('PRAGMA index_info("'+idx['name'].replace('"','""')+'")')]
                            if columns and columns!=pk and all(isinstance(c,str) for c in columns):unique.append(columns)
                    for row in items:
                        for columns in unique:
                            if not all(k in row and row[k] is not None for k in columns):continue
                            old=db.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+c+'"=?' for c in columns),[row[c] for c in columns]).fetchone()
                            if old is not None:
                                for key in pk:
                                    if key=='id' and row.get(key)!=old[key]:aliases[str(row[key])]=old[key]
                                    row[key]=old[key]
                                break
    if aliases:
        for part in ('memory','history'):
            for tables in docs.get(part,{}).get('databases',{}).values():
                for table,items in tables.items():
                    for row in items:
                        for key,value in list(row.items()):
                            if key in ('body','payload','metadata','payload_json') and isinstance(value,str):
                                decoded=parse(value)
                                if isinstance(decoded,(list,dict)):row[key]=json.dumps(remap_refs(decoded,aliases),ensure_ascii=False,sort_keys=True,separators=(',',':'))
                            elif key in ('id','activity_id','source_memory_id','memory_id','receipt_id','source_id','supersedes_id') and str(value) in aliases:row[key]=aliases[str(value)]


def validate_undo(host,undo):
    """Check all records before rollback starts changing any component."""
    for name in dict.fromkeys(item['database'] for item in undo):
        with sqlite3.connect(db_path(host,name).as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            for item in undo:
                if item['database']!=name:continue
                current=_lookup(db,item['table'],item['keys'])
                if not _matches(current,item['before']) and not _matches(current,item['after']):
                    raise RuntimeError('回滚记录已被其他操作更新，保留现场等待人工处理')
