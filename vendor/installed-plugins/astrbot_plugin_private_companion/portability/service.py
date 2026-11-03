"""Export, preflight, reviewed import and durable rollback coordination."""
from __future__ import annotations
import asyncio
import base64
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
import uuid

from . import archive,binding,components,dependencies,resources

TOKEN_TTL=15*60


def atomic(path,body):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+secrets.token_hex(6))
    try:
        with temporary.open('xb') as out:
            os.chmod(temporary,0o600);out.write(body);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def manager(host):
    return getattr(getattr(host,'context',None),'persona_manager',None)


def service(api):
    host=api.plugin
    value=getattr(host,'_portable_archive_service',None)
    if value is None:value=host._portable_archive_service=MigrationService(api)
    return value


def paused(host):
    if getattr(host,'_portable_import_in_progress',False):return True
    path=Path(getattr(host,'data_dir',None) or '.')/'.portability/control.json'
    try:
        value=json.loads(path.read_text())
        # An incomplete transaction fences all automatic work until recovery.
        if value.get('transaction'):return True
        scopes=value.get('paused_scopes',{})
        if not scopes:return False
        try:
            from ..information_sources import scope_for
            current=scope_for(host)['scope']
        except (ValueError,AttributeError,TypeError):return True
        return current in scopes
    except FileNotFoundError:return False
    except (OSError,ValueError,TypeError,AttributeError):return True


class MigrationService:
    def __init__(self,api):
        self.api=api;self.host=api.plugin
        self.root=Path(self.host.data_dir)/'.portability'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.lock=asyncio.Lock();self.secret=secrets.token_bytes(32);self.previews={}

    def control(self):
        try:return json.loads((self.root/'control.json').read_text())
        except FileNotFoundError:return {'paused_scopes':{}}

    def save_control(self,value):atomic(self.root/'control.json',archive.encode(value))

    def path(self,key,suffix='.zip'):
        if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{32}',key):raise ValueError('档案编号无效')
        p=self.root/'packages'/(key+suffix)
        if p.is_symlink():raise ValueError('档案路径无效')
        return p

    def binding(self):
        from ..information_sources import scope_for
        return binding.validate(scope_for(self.host))

    def stable_id(self,source):
        p=self.root/'identities.json'
        values=json.loads(p.read_text()) if p.is_file() else {}
        scope=binding.scope(source)
        if scope not in values:
            values[scope]=str(uuid.uuid4());atomic(p,archive.encode(values))
        return values[scope]

    async def status(self):
        current=None
        try:current=self.binding()
        except (ValueError,AttributeError):pass
        installed=dependencies.inventory(self.host)
        providers=self.api._available_provider_items()
        return {'api_version':1,'source':current,'max_bytes':archive.MAX_BYTES,'chunk_bytes':archive.CHUNK_BYTES,
            'plugins':{n:{k:v for k,v in i.items() if k!='instance'} for n,i in installed.items()},
            'providers':[{'id':str(p.get('id') or ''),'label':str(p.get('name') or p.get('id') or '')} for p in providers],
            'paused':paused(self.host),'recovery_pending':bool(self.control().get('transaction')),
            'backups':self.backups(),'modes':[{'id':'host','label':'换电脑，保留原QQ与人格'},
                {'id':'account','label':'更换QQ或人格绑定'}],
            'excluded':['QQ、浏览器和模型登录凭据','密码、Cookie、API Key','实时缓存、输入状态与Token统计',
                '未完成的发送队列与执行租约','向量索引与检索缓存（使用目标模型重建）','原机网络代理、端口与路径','原生AstrBot会话缓存（长期记忆时间线单独保留）']}

    def backups(self):
        result=[]
        folder=self.root/'transactions'
        for p in sorted(folder.glob('*/journal.json'),key=lambda x:x.stat().st_mtime,reverse=True)[:10]:
            try:
                v=json.loads(p.read_text());result.append({k:v.get(k) for k in ('id','state','created','target','components','rollback_error')})
            except (ValueError,OSError):continue
        return result

    def cleanup(self):
        now=time.time();folder=self.root/'packages';folder.mkdir(exist_ok=True,mode=0o700)
        for p in folder.iterdir():
            if p.is_file() and not p.is_symlink() and now-p.stat().st_mtime>3600:p.unlink()
        if sum(p.stat().st_size for p in folder.iterdir() if p.is_file())>512*1024*1024:
            raise ValueError('临时档案占用超过512MiB，请稍后重试或清理过期档案')
        self.previews={k:v for k,v in self.previews.items() if v['expires']>now}

    async def export(self,options):
        async with self.lock:
            self.cleanup();source=self.binding();host=self.host
            if self.control().get('transaction'):raise ValueError('存在未完成的迁移，请先回滚')
            allowed=set(archive.COMPONENTS)
            values=options.get('components')
            if values is not None and (not isinstance(values,list) or not all(isinstance(v,str) for v in values)):raise ValueError('导出模块选择格式无效')
            selected=set(allowed if values is None else values)
            if not {'companion','persona'}<=selected or not selected<=allowed:raise ValueError('必须包含陪伴档案和人格正文')
            docs={};assets={};available=dependencies.inventory(host)
            async with host._data_lock:
                data=deepcopy(host.data)
            docs['companion']={'data':binding.scrub({k:components.clean_data(v) for k,v in data.items() if k in components.DATA_SECTIONS}),
                'settings':binding.export_config(self.api._runtime_settings()),'providers':self.api._provider_settings(),
                'persona_settings':binding.scrub(data.get('persona_settings',{}))}
            # Only settings accepted by the target public configuration API are portable.
            allowed_settings=self.api._allowed_setting_keys()|self.api._allowed_feature_keys()
            docs['companion']['settings']={k:v for k,v in docs['companion']['settings'].items() if k in allowed_settings and k not in ('storage_backend','storage_sqlite_path')}
            original=await manager(host).get_persona(source['persona_id'])
            native=original.model_dump(mode='json') if callable(getattr(original,'model_dump',None)) else dict(original)
            docs['persona']={k:native.get(k) for k in ('persona_id','system_prompt','begin_dialogs','tools','skills','custom_error_message')}
            for name in ('memory','history'):
                if name not in selected:continue
                if name=='memory' and not available.get('astrbot_plugin_memory_companion',{}).get('instance'):
                    raise ValueError('长期记忆插件未就绪；可取消长期记忆模块，或先安装兼容版本')
                docs[name]=await asyncio.to_thread(components.export_databases,host,source,data,memory=name=='memory')
            for name in ('resources','memes'):
                if name not in selected:continue
                if name=='memes' and not available.get('meme_manager',{}).get('instance'):
                    raise ValueError('表情包管理器未就绪；可取消表情资源模块')
                doc,binary=await asyncio.to_thread(resources.collect,host,name,documents=docs['companion'],source=source);docs[name]=doc;assets.update(binary)
            if 'memory' in docs:
                item=available['astrbot_plugin_memory_companion']['instance']
                docs['memory']['settings']=binding.export_config(deepcopy(dict(getattr(item,'config',{}))))
            if 'memes' in docs:
                docs['memes']['settings']=binding.export_config(deepcopy(dict(getattr(available['meme_manager']['instance'],'config',{}))))
            manifest={'companion_id':self.stable_id(source),'exported_at':datetime.now(timezone.utc).isoformat(),
                'source':source,'primary_persona':host._primary_persona_id()==source['persona_id'],'components':{key:{'adapter_version':1,'plugin':dependencies.RULES[key][0],
                    'source_version':available.get(dependencies.RULES[key][0],{}).get('version',''),'required':dependencies.RULES[key][4]} for key in docs},
                'excluded':(await self.status())['excluded']}
            raw=await asyncio.to_thread(archive.build,manifest,docs,assets)
            key=uuid.uuid4().hex;atomic(self.path(key),raw)
            return {'archive_id':key,'bytes':len(raw),'sha256':archive.digest(raw),
                'filename':'companion-'+source['persona_id']+'-'+time.strftime('%Y%m%d-%H%M%S')+'.zip',
                'components':list(docs),'source':source}

    def begin_upload(self,size):
        self.cleanup()
        if not isinstance(size,int) or not 0<size<=archive.MAX_BYTES:raise ValueError('文件大小无效')
        key=uuid.uuid4().hex;atomic(self.path(key,'.part'),b'');atomic(self.path(key,'.upload.json'),archive.encode({'bytes':size}))
        return {'archive_id':key,'chunk_bytes':archive.CHUNK_BYTES}

    async def upload(self,key,offset,encoded):
        async with self.lock:
            p=self.path(key,'.part');spec=json.loads(self.path(key,'.upload.json').read_text())
            if not isinstance(offset,int) or offset!=p.stat().st_size:raise ValueError('上传偏移不一致，请重新选择文件')
            if not isinstance(encoded,str) or len(encoded)>archive.CHUNK_BYTES*2:raise ValueError('分片过大')
            try:body=base64.b64decode(encoded,validate=True)
            except (ValueError,TypeError) as exc:raise ValueError('分片编码无效') from exc
            if not body or len(body)>archive.CHUNK_BYTES or offset+len(body)>spec['bytes']:raise ValueError('分片长度无效')
            with p.open('ab') as out:out.write(body);out.flush();os.fsync(out.fileno())
            complete=p.stat().st_size==spec['bytes']
            if complete:os.replace(p,self.path(key));self.path(key,'.upload.json').unlink()
            return {'offset':offset+len(body),'complete':complete}

    def download(self,key,offset):
        p=self.path(key)
        if not isinstance(offset,int) or not 0<=offset<p.stat().st_size:raise ValueError('下载偏移无效')
        with p.open('rb') as f:f.seek(offset);body=f.read(archive.CHUNK_BYTES)
        return {'offset':offset,'data':base64.b64encode(body).decode(),'complete':offset+len(body)==p.stat().st_size}

    def read(self,key):return archive.read(self.path(key).read_bytes())

    def inspect(self,key):
        manifest,docs,_=self.read(key)
        binding.validate(manifest.get('source'))
        return {k:manifest.get(k) for k in ('source','components','companion_id','exported_at','excluded')}

    def request_options(self,payload,manifest):
        source=binding.validate(manifest.get('source'));target=binding.validate(payload.get('target'))
        mode=payload.get('mode')
        if mode not in ('host','account'):raise ValueError('请选择迁移方式')
        if mode=='host' and source!=target:raise ValueError('换电脑恢复要求保留原QQ、平台实例和人格；更改绑定请选择换号模式')
        selected=set(payload.get('components') if isinstance(payload.get('components'),list) else manifest['components'])
        if not selected<=set(manifest['components']):raise ValueError('选择了档案不存在的模块')
        if not {'companion','persona'}<=selected:raise ValueError('必须导入陪伴档案与人格正文')
        policy=payload.get('conflict','use_archive')
        if policy not in ('keep_current','use_archive'):raise ValueError('字段冲突处理无效')
        providers=payload.get('provider_map',{})
        if not isinstance(providers,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in providers.items()):raise ValueError('模型映射格式无效')
        return {'mode':mode,'source':source,'target':target,'components':sorted(selected),'conflict':policy,'provider_map':providers}

    def target_profile(self,target):
        pid=target['persona_id']
        if pid==self.host._primary_persona_id():return deepcopy(self.host._data_default)
        cached=getattr(self.host,'_persona_data_profiles',{}).get(pid)
        if cached is not None:return deepcopy(cached)
        exists=getattr(self.host,'_secondary_persona_store_exists',lambda p:False)
        if exists(pid):raise ValueError('请先在陪伴面板切换到目标人格并加载其档案，再进行导入预览')
        return {}

    async def prepare(self,key,payload):
        manifest,all_docs,assets=await asyncio.to_thread(self.read,key)
        if not isinstance(manifest.get('companion_id'),str) or len(manifest['companion_id'])>120:raise ValueError('缺少稳定档案标识')
        if not all(isinstance(v,dict) for v in manifest['components'].values()):raise ValueError('模块声明格式无效')
        options=self.request_options(payload,manifest);source=options['source'];target=options['target'];selected=options['components']
        if any(manifest['components'][key].get('adapter_version')!=1 for key in selected if key in dependencies.RULES):raise ValueError('存在不兼容的模块导入接口版本')
        docs={k:v for k,v in all_docs.items() if k in selected}
        native=docs['persona']
        if not isinstance(native.get('system_prompt'),str) or len(native['system_prompt'])>100000:raise ValueError('人格正文格式无效')
        if native.get('persona_id')!=source['persona_id']:raise ValueError('人格正文与档案归属不一致')
        for field,limit,length in (('begin_dialogs',100,10000),('tools',500,160),('skills',500,160)):
            value=native.get(field)
            if value is not None and (not isinstance(value,list) or len(value)>limit or any(not isinstance(v,str) or len(v)>length for v in value)):raise ValueError('人格字段格式无效：'+field)
        error=native.get('custom_error_message')
        if error is not None and (not isinstance(error,str) or len(error)>4000):raise ValueError('人格错误提示格式无效')
        companion=docs['companion']
        if not isinstance(companion.get('data'),dict) or not isinstance(companion.get('settings'),dict) or not isinstance(companion.get('providers'),dict):raise ValueError('陪伴档案结构无效')
        for profile_section in ('users','groups'):
            value=companion['data'].get(profile_section,{})
            if not isinstance(value,dict) or any(not isinstance(v,dict) for v in value.values()):raise ValueError('对象资料格式无效：'+profile_section)
        deps=dependencies.check(self.host,manifest,set(selected))
        if 'memory' not in manifest['components'] and docs['companion'].get('settings',{}).get('enable_livingmemory_integration') is True:
            extra=dependencies.check(self.host,{'components':{'memory':{}}},{'memory'})
            for row in extra:row['runtime_only']=True
            deps.extend(extra)
        blockers=[r['label']+'：'+r['reason'] for r in deps if r['blocking']]
        if not self.host._primary_persona_id():blockers.append('请先在陪伴插件配置中选定本机主人格，可创建空人格作为导入目标；导入后会按所选策略恢复正文')
        if self.control().get('transaction'):blockers.append('有迁移事务等待恢复，请先回滚')
        identity_path=self.root/'identities.json'
        identities=json.loads(identity_path.read_text()) if identity_path.is_file() else {}
        if identities.get(binding.scope(target)) not in (None,manifest['companion_id']):blockers.append('目标已绑定另一份独立人格档案，请选择新的人格目标，避免混合记忆')
        try:
            live=self.binding()
            if live['platform_id']==target['platform_id'] and live['bot_id']!=target['bot_id']:
                blockers.append('目标平台当前登录的是另一个QQ，请先切换账号或使用独立部署，避免改动正在运行的账号')
        except (ValueError,AttributeError):pass
        provider_items=self.api._available_provider_items();available={str(p.get('id') or '') for p in provider_items}
        provider_values=set(str(v) for v in docs['companion'].get('providers',{}).values() if v)
        provider_values.update(str(v) for k,v in docs['companion'].get('settings',{}).items() if k.endswith('_PROVIDER_ID') and v)
        missing=[]
        for old in sorted(provider_values):
            chosen=options['provider_map'].get(old,old)
            if chosen and chosen not in available:missing.append(old)
        if missing:blockers.append('仍有模型ID未映射到当前可用提供商')
        path_map,writes=resources.plan(self.host,docs,assets,source,target,policy=options['conflict'])
        docs=components.mapped_databases(docs,source,target,manifest['companion_id'],source_primary=manifest.get('primary_persona',True),target_primary=target['persona_id']==self.host._primary_persona_id())
        components.rebase_control_epoch(self.host,docs)
        components.normalize_memory_rows(self.host,docs)
        components.align_natural_keys(self.host,docs)
        docs['companion']['data']=binding.rebind(components.clean_data(docs['companion'].get('data',{})),source,target,path_map=path_map)
        docs['companion']['data']={k:v for k,v in docs['companion']['data'].items() if k in components.DATA_SECTIONS}
        docs['companion']['settings']=binding.rebind(docs['companion'].get('settings',{}),source,target,path_map=path_map)
        checks={'conflicts':{},'missing_schemas':[]}
        if not any(r['blocking'] and r['component'] in ('memory','history') for r in deps):
            checks=await asyncio.to_thread(components.database_conflicts,self.host,docs,target)
            if checks['missing_schemas']:blockers.append('目标数据库尚无兼容结构：'+', '.join(checks['missing_schemas']))
        existing=await self._persona_or_none(target['persona_id'])
        profile=self.target_profile(target) if existing is not None else {}
        fingerprint=archive.digest(archive.encode({'profile':profile,'persona':existing,'providers':sorted(available),'settings':self.api._runtime_settings()}))
        stat=self.path(key).stat()
        summary={'can_import':not blockers,'blockers':blockers,'dependencies':deps,'source':source,'target':target,
            'mode':options['mode'],'companion_id':manifest['companion_id'],'selected_components':selected,
            'skipped_components':[k for k in all_docs if k not in selected],
            'missing_provider_ids':missing,'providers':provider_items,'database_checks':checks,
            'persona_exists':existing is not None,'persona_prompt_will_replace':existing is not None and options['conflict']=='use_archive',
            'profile_sections':list(docs['companion']['data']),'resource_files':len(writes),
            'omitted_records':{name:all_docs.get(name,{}).get('excluded_unrelated_or_ambiguous',{}) for name in ('memory','history')},
            'records':{name:sum(len(v) for tables in docs.get(name,{}).get('databases',{}).values() for v in tables.values()) for name in ('memory','history')},
            'excluded':manifest.get('excluded',[]),'conflict':options['conflict'],
            'notes':['导入后自动行为保持暂停；请检查账号、人格、模型和权限后手动恢复。',
                '历史作者、正文中的QQ号和原始来源保留；只调整明确的账号与会话绑定。',
                '未完成的主动发送与公开写操作不会恢复执行。',
                '管理员权限沿用目标 AstrBot 配置，不随用户画像授予；账号与聊天人格路由需要在目标环境核对。']}
        digest_payload=archive.encode({'key':key,'options':options,'fingerprint':fingerprint,'size':stat.st_size,'mtime':stat.st_mtime_ns,
            'dependencies':deps,'database_checks':checks,'prepared_documents':archive.digest(archive.encode(docs)),'files':[(str(p),archive.digest(b),archive.digest(p.read_bytes()) if p.is_file() else None) for p,b in writes]})
        signature=hmac.new(self.secret,digest_payload,hashlib.sha256).hexdigest()
        return summary,signature,options,docs,writes,existing,profile

    async def preview(self,key,payload):
        async with self.lock:
            summary,signature,*_=await self.prepare(key,payload)
            token=secrets.token_urlsafe(32)
            if summary['can_import']:
                self.previews[token]={'signature':signature,'expires':time.time()+TOKEN_TTL,'key':key}
                summary['preview_token']=token;summary['expires_in_seconds']=TOKEN_TTL
            return summary

    async def _persona_or_none(self,pid):
        try:
            value=await manager(self.host).get_persona(pid)
            if value is None:return None
            return value.model_dump(mode='json') if callable(getattr(value,'model_dump',None)) else dict(value)
        except ValueError:return None

    async def write_persona(self,pid,body):
        current=await self._persona_or_none(pid)
        fields={k:deepcopy(body.get(k)) for k in ('system_prompt','begin_dialogs','tools','skills','custom_error_message')}
        if not isinstance(fields['system_prompt'],str) or len(fields['system_prompt'])>100000:raise ValueError('人格正文无效或过长')
        if current is None:await manager(self.host).create_persona(persona_id=pid,**fields)
        else:await manager(self.host).update_persona(persona_id=pid,**fields)

    async def _backup(self,identifier,target,selected,writes,existing,profile,docs,policy):
        directory=self.root/'transactions'/identifier;directory.mkdir(parents=True,mode=0o700)
        from ..config_migration import _config_root_mapping
        values={'profile':deepcopy(profile),'persona':existing,'config':deepcopy(_config_root_mapping(self.host.config)),
            'target':target,'external_configs':{},'resource_files':[],'databases':[], 'identities':(self.root/'identities.json').read_text() if (self.root/'identities.json').is_file() else None}
        for plugin,component in (('astrbot_plugin_memory_companion','memory'),('meme_manager','memes')):
            if component not in selected:continue
            inst=dependencies.inventory(self.host).get(plugin,{}).get('instance')
            if inst is not None:values['external_configs'][plugin]=deepcopy(dict(inst.config))
        for name in components.DB_SPECS:
            if components.DB_SPECS[name][0]=='memory' and 'memory' not in selected:continue
            if components.DB_SPECS[name][0]=='companion' and 'history' not in selected:continue
            p=components.db_path(self.host,name)
            if p.is_file():
                dest=directory/(name+'.db')
                with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=10) as source,sqlite3.connect(dest) as backup:source.backup(backup)
                values['databases'].append({'name':name,'backup':dest.name})
        for index,(p,body) in enumerate(writes):
            item={'path':str(p),'existed':p.is_file(),'backup':'resource-'+str(index),'after_digest':archive.digest(body)}
            if p.is_file():atomic(directory/item['backup'],p.read_bytes())
            values['resource_files'].append(item)
        values['database_undo']=components.prepare_undo(self.host,docs,policy)
        atomic(directory/'before.json',archive.encode(values))
        journal={'id':identifier,'state':'prepared','created':time.time(),'target':target,'components':list(selected)}
        atomic(directory/'journal.json',archive.encode(journal))
        return directory,journal

    async def apply(self,key,payload):
        async with self.lock:
            token=payload.get('preview_token');ticket=self.previews.get(token)
            if not ticket or ticket['key']!=key or ticket['expires']<time.time():raise ValueError('请重新预览，确认令牌已过期或已使用')
            summary,signature,options,docs,writes,existing,profile=await self.prepare(key,payload)
            if not summary['can_import']:raise ValueError('导入条件已变化：'+'；'.join(summary['blockers']))
            if not hmac.compare_digest(signature,ticket['signature']):raise ValueError('档案、依赖或目标资料已变化，请重新预览')
            del self.previews[token]
            target=options['target'];identifier=uuid.uuid4().hex
            self.host._portable_import_in_progress=True
            try:
                await self.quiesce()
                directory,journal=await self._backup(identifier,target,options['components'],writes,existing,profile,docs,options['conflict'])
                control=self.control();control['transaction']=identifier;self.save_control(control)
                journal['state']='applying';atomic(directory/'journal.json',archive.encode(journal))
                try:
                    self._active_journal=(directory,journal)
                    await self._commit(options,docs,writes,existing,profile,json.loads((directory/'before.json').read_text())['database_undo'])
                    journal['after_config_hash']=archive.digest(archive.encode(self.api.plugin.config))
                    ids_path=self.root/'identities.json';ids=json.loads(ids_path.read_text()) if ids_path.is_file() else {}
                    ids[binding.scope(target)]=summary['companion_id'];atomic(ids_path,archive.encode(ids))
                    journal['state']='committed';journal['finished']=time.time();atomic(directory/'journal.json',archive.encode(journal))
                    control=self.control();control.pop('transaction',None);control.setdefault('paused_scopes',{})[binding.scope(target)]=identifier;self.save_control(control)
                    return {'imported':True,'transaction_id':identifier,'paused':True,'requires_reload_plugins':[k for k,n in (('astrbot_plugin_memory_companion','memory'),('meme_manager','memes')) if n in docs],
                        'target':target,'skipped_components':summary['skipped_components'],'message':'档案已导入，自动行为保持暂停。检查目标账号和各插件后再恢复。'}
                except BaseException:
                    try:await self._rollback(directory,journal)
                    except BaseException as failure:
                        journal.update(state='rollback_failed',rollback_error=type(failure).__name__)
                        atomic(directory/'journal.json',archive.encode(journal))
                        raise RuntimeError('迁移失败且回滚未完成，自动行为已锁定，请到迁移中心恢复备份') from failure
                    raise
            finally:self.host._portable_import_in_progress=False

    async def quiesce(self):
        runtime=getattr(self.host,'_real_schedule_runtime',None)
        if runtime is not None:
            try:
                running=runtime.ledger.list(runtime.scope(),day=runtime.day())
                if any(row['state']=='running' and row['effect']=='social_write' for row in running):raise ValueError('有对外写操作正在执行，请等待实际回执后再导入')
            except AttributeError:pass
            tasks=[t for t in runtime.tasks.values() if t and not t.done()]
            for task in tasks:task.cancel()
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        # Busy outward writes cannot be safely restarted by an import.
        for value in self.host.data.get('users',{}).values():
            if isinstance(value,dict) and value.get('proactive_sending'):
                raise ValueError('有主动消息正在发送，请稍后导入')
        await asyncio.sleep(0)

    async def _commit(self,options,docs,writes,existing,profile,undo):
        target=options['target'];pid=target['persona_id'];source=options['source'];policy=options['conflict']
        if existing is None or policy=='use_archive':await self.write_persona(pid,docs['persona'])
        incoming=deepcopy(docs['companion']['data'])
        merged=deepcopy(profile) if profile else self.host._new_store() if callable(getattr(self.host,'_new_store',None)) else {}
        for key,value in incoming.items():
            if key not in merged:merged[key]=value
            elif isinstance(value,dict) and isinstance(merged[key],dict):self.merge_data(merged[key],value,policy)
            elif policy=='use_archive' or merged[key] is None or merged[key]=='':merged[key]=value
        merged.setdefault('version',profile.get('version',1));merged=components.clean_data(merged)
        # Save via the host's native snapshot machinery, preserving its backend.
        self.host._save_persona_profile_sync(pid,merged)
        if pid==self.host._primary_persona_id():self.host._data_default=merged
        else:self.host._persona_data_profiles[pid]=merged
        if getattr(self,'_active_journal',None):
            folder,journal=self._active_journal
            journal['after_profile_hash']=self.profile_hash(merged)
            atomic(folder/'journal.json',archive.encode(journal))
        allowed=self.api._allowed_setting_keys()|self.api._allowed_feature_keys()
        settings=binding.rebind(docs['companion'].get('settings',{}),source,target)
        providers=docs['companion'].get('providers',{})
        settings.update(providers)
        settings={k:v for k,v in settings.items() if k in allowed or k in self.api._allowed_provider_keys()}
        for k in list(settings):
            if binding.SECRET.search(k) or 'proxy' in k.lower() or k.startswith('standalone_webui_') or k in ('storage_backend','storage_sqlite_path','bot_scope_mode','bot_scope_ids','plugin_specific_persona_id','enable_multi_persona_mode','multi_persona_ids'):settings.pop(k,None)
            elif k.endswith('_PROVIDER_ID') or k in self.api._allowed_provider_keys():settings[k]=options['provider_map'].get(str(settings[k]),settings[k])
        if pid==self.host._primary_persona_id():
            for k,v in settings.items():
                if policy=='use_archive' or self.api._migration_current_setting_value(k) in (None,''):self.api._apply_config_value(k,self.api._normalize_setting_value(k,v),settings)
            if not await self.api._save_config_if_possible():raise RuntimeError('配置持久化失败')
        else:
            manifest=self.host._persona_scope_manifest()
            persona_settings={k:v for k,v in settings.items() if manifest.get(k,{}).get('scope')=='persona'}
            merged['persona_settings']={**persona_settings,**merged.get('persona_settings',{})}
            self.host._save_persona_profile_sync(pid,merged)
            if getattr(self,'_active_journal',None):
                folder,journal=self._active_journal;journal['after_profile_hash']=self.profile_hash(merged);atomic(folder/'journal.json',archive.encode(journal))
            current_ids=list(getattr(self.host,'multi_persona_ids',[]) or [])
            if pid not in current_ids:current_ids.append(pid)
            for key,value in (('multi_persona_ids',current_ids),('enable_multi_persona_mode',True)):
                self.api._apply_config_value(key,value,{})
            if not await self.api._save_config_if_possible():raise RuntimeError('多人格绑定配置保存失败')
        for name,plugin in (('memory','astrbot_plugin_memory_companion'),('memes','meme_manager')):
            if name not in docs:continue
            instance=dependencies.inventory(self.host)[plugin]['instance'];incoming=binding.scrub(docs[name].get('settings',{}))
            # Do not import another plugin's runtime/auth/installation topology.
            if incoming and isinstance(instance.config,dict):
                self.merge_config(instance.config,incoming,policy)
                saver=getattr(instance.config,'save_config',None)
                if callable(saver):saver()
        await asyncio.to_thread(components.apply_databases,self.host,docs,policy,undo)
        for p,body in writes:
            if p.suffix.lower()=='.json' or not p.exists() or policy=='use_archive':atomic(p,body)
        self.host._cached_persona_prompt=''

    @classmethod
    def merge_data(cls,current,incoming,policy):
        for key,value in incoming.items():
            if key not in current:current[key]=deepcopy(value)
            elif isinstance(value,dict) and isinstance(current[key],dict):cls.merge_data(current[key],value,policy)
            elif policy=='use_archive' or current[key] is None or current[key]=='':current[key]=deepcopy(value)

    @classmethod
    def merge_config(cls,current,incoming,policy):
        for k,v in incoming.items():
            if binding.SECRET.search(k) or re.search(r'(?:path|directory|proxy|host|port|bind|webui|url)$',k,re.I):continue
            if k not in current:continue
            if isinstance(v,dict) and isinstance(current[k],dict):cls.merge_config(current[k],v,policy)
            elif policy=='use_archive' or current[k] in ('',None,[]):current[k]=deepcopy(v)

    @staticmethod
    def profile_hash(profile):
        return archive.digest(archive.encode(components.clean_data({k:v for k,v in profile.items() if k in components.DATA_SECTIONS})))

    async def rollback(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('备份编号无效')
        async with self.lock:
            directory=self.root/'transactions'/identifier
            journal=json.loads((directory/'journal.json').read_text())
            if journal.get('state')=='rolled_back':return {'rolled_back':True,'already_done':True}
            control=self.control()
            if control.get('transaction') not in (None,identifier):raise ValueError('另一个迁移事务尚未恢复，不能回滚其它备份')
            original_state=journal.get('state')
            control['transaction']=identifier;self.save_control(control)
            self.host._portable_import_in_progress=True
            try:
                await self.quiesce();await self._rollback(directory,journal)
                return {'rolled_back':True,'paused':True,'message':'已恢复迁移前快照；请重新加载受影响插件，检查后恢复自动行为。'}
            except BaseException as exc:
                if journal.get('state')=='rolling_back':
                    journal.update(state='rollback_failed',rollback_error=type(exc).__name__);atomic(directory/'journal.json',archive.encode(journal))
                elif original_state=='committed':
                    current=self.control();current.pop('transaction',None);self.save_control(current)
                raise
            finally:self.host._portable_import_in_progress=False

    async def _rollback(self,directory,journal):
        from ..config_migration import _config_root_mapping
        before=json.loads((directory/'before.json').read_text());target=before['target'];pid=target['persona_id']
        current_profile=self.target_profile(target)
        allowed_hashes={self.profile_hash(before['profile'])}
        if journal.get('after_profile_hash'):allowed_hashes.add(journal['after_profile_hash'])
        if self.profile_hash(current_profile) not in allowed_hashes:raise RuntimeError('目标档案已有后续修改，停止回滚并保留现场')
        current=_config_root_mapping(self.host.config)
        if journal.get('state')=='committed' and journal.get('after_config_hash'):
            value=archive.digest(archive.encode(current))
            if value not in (journal['after_config_hash'],archive.digest(archive.encode(before['config']))):raise RuntimeError('配置已有后续修改，停止回滚并保留现场')
        await asyncio.to_thread(components.validate_undo,self.host,before.get('database_undo',[]))
        for item in before['resource_files']:
            path=Path(item['path']);now=path.read_bytes() if path.is_file() else None
            old=(directory/item['backup']).read_bytes() if item['existed'] else None
            if now!=old and (now is None or archive.digest(now)!=item['after_digest']):raise RuntimeError('资源已被后续操作修改，停止回滚并保留现场')
        journal['state']='rolling_back';atomic(directory/'journal.json',archive.encode(journal))
        current.clear();current.update(before['config'])
        if not await self.api._save_config_if_possible():raise RuntimeError('回滚配置保存失败')
        for k,v in self.api._runtime_settings().items():
            if hasattr(self.host,k):
                try:setattr(self.host,k,v)
                except (AttributeError,TypeError):pass
        self.host._save_persona_profile_sync(pid,before['profile'])
        if pid==self.host._primary_persona_id():self.host._data_default=before['profile']
        else:self.host._persona_data_profiles[pid]=before['profile']
        if before['persona'] is not None:await self.write_persona(pid,before['persona'])
        elif await self._persona_or_none(pid) is not None:await manager(self.host).delete_persona(pid)
        for name,cfg in before.get('external_configs',{}).items():
            inst=dependencies.inventory(self.host).get(name,{}).get('instance')
            if inst is None:raise RuntimeError('回滚依赖未加载：'+name)
            inst.config.clear();inst.config.update(cfg)
            save=getattr(inst.config,'save_config',None)
            if callable(save):save()
        await asyncio.to_thread(components.rollback_databases,self.host,before.get('database_undo',[]))
        for item in before['resource_files']:
            p=Path(item['path'])
            current=p.read_bytes() if p.is_file() else None
            original=(directory/item['backup']).read_bytes() if item['existed'] else None
            if current!=original and (current is None or archive.digest(current)!=item['after_digest']):raise RuntimeError('资源已被后续操作修改，停止回滚并保留现场')
            if item['existed']:atomic(p,(directory/item['backup']).read_bytes())
            else:p.unlink(missing_ok=True)
        if before.get('identities') is None:(self.root/'identities.json').unlink(missing_ok=True)
        else:atomic(self.root/'identities.json',before['identities'].encode())
        journal.update(state='rolled_back',finished=time.time());atomic(directory/'journal.json',archive.encode(journal))
        control=self.control();control.pop('transaction',None);control.setdefault('paused_scopes',{})[binding.scope(before['target'])]=journal['id'];self.save_control(control)

    async def resume(self,expected_scope):
        async with self.lock:
            control=self.control()
            if control.get('transaction'):raise ValueError('未完成事务恢复前不能继续自动行为')
            current=self.binding()
            if binding.scope(current)!=expected_scope:raise ValueError('当前账号或人格与导入目标不匹配，不能恢复自动行为')
            identifier=control.get('paused_scopes',{}).get(expected_scope)
            if identifier:
                journal=json.loads((self.root/'transactions'/identifier/'journal.json').read_text())
                selected=journal.get('components',[])
                checks=dependencies.check(self.host,{'components':{k:{} for k in selected}},set(selected))
                if any(r['blocking'] for r in checks):raise ValueError('迁移依赖未就绪，不能恢复')
            control.setdefault('paused_scopes',{}).pop(expected_scope,None);self.save_control(control)
            return {'resumed':True,'scope':expected_scope}
