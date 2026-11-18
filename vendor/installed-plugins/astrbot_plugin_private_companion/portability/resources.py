"""Portable image files, with explicit roots and no archive-controlled filenames."""
from __future__ import annotations
import json
from pathlib import Path,PurePosixPath
from . import archive,binding
from .components import roots

IMAGE={'.png','.jpg','.jpeg','.gif','.webp','.avif'}
JSON_NAMES={'registry.json','selection_rules.json','manifest.json','memes_data.json','semantic_metadata.json','character_references.json','asset_index.json','index.json'}
EXCLUDE={'backups','backup','temp','exports','.portability','private_inbound_images','private_image_cache_previews','recall_message_images','sent_image_context','qzone_watch','reactive_poke','function_awareness'}


def safe_relative(value):
    if not isinstance(value,str) or not value or len(value)>200 or '\\' in value:return False
    p=PurePosixPath(value)
    return not p.is_absolute() and all(x not in ('','.','..') for x in value.split('/'))


def collect(host,kind,documents=None,source=None):
    root=roots(host)['memes' if kind=='memes' else 'companion'];items=[];assets={};total=0
    if not root.is_dir():return {'files':[]},assets
    candidates=set()
    references=set()
    def reference_walk(value):
        if isinstance(value,dict):
            for v in value.values():reference_walk(v)
        elif isinstance(value,list):
            for v in value:reference_walk(v)
        elif isinstance(value,str):
            if value.lstrip().startswith(('{','[')):
                try:reference_walk(json.loads(value))
                except (ValueError,TypeError):pass
            local=value[7:] if value.startswith('file:///') else value
            path=Path(local) if local.startswith('/') else root/local
            try:
                if path.suffix.lower() in IMAGE and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):references.add(path.resolve())
            except (OSError,ValueError):pass
    if kind=='resources':
        reference_walk(documents or {})
        candidates.update(references)
        for image in references:
            parent=image.parent
            while parent.is_relative_to(root):
                for name in ('asset_index.json','index.json'):
                    if (parent/name).is_file():candidates.add(parent/name)
                if parent==root:break
                parent=parent.parent
    else:candidates.update(root.rglob('*'))
    def prune(value,parent):
        if isinstance(value,dict):
            for field in ('persona_id','owner_persona_id'):
                if source and value.get(field) and value[field]!=source['persona_id']:return None
            for v in value.values():
                if isinstance(v,str) and Path(v).suffix.lower() in IMAGE:
                    path=Path(v) if v.startswith('/') else parent/v
                    if path.is_file() and path.resolve() not in references:return None
            return {k:clean for k,v in value.items() if (clean:=prune(v,parent)) is not None}
        if isinstance(value,list):return [clean for v in value if (clean:=prune(v,parent)) is not None]
        return value
    for p in sorted(candidates):
        relative=p.relative_to(root)
        if any(x in EXCLUDE for x in relative.parts) or p.is_symlink() or not p.is_file():continue
        if p.suffix.lower() not in IMAGE and not((kind=='memes' and p.name in JSON_NAMES) or (kind=='resources' and p.name in {'asset_index.json','index.json'})):continue
        if not p.resolve().is_relative_to(root.resolve()):continue
        size=p.stat().st_size
        if size>16*1024*1024:raise ValueError('单个资源超过16MiB：'+p.name)
        body=p.read_bytes();total+=len(body)
        if total>archive.MAX_EXPANDED:raise ValueError('资源总量过大，请分组导出表情图库')
        if p.suffix.lower()=='.json':
            document=binding.scrub(json.loads(body.decode('utf-8-sig')))
            if kind=='resources':document=prune(document,p.parent)
            if kind=='memes' and p.name=='selection_rules.json' and source:
                document['rules']=[item for item in document.get('rules',[]) if item.get('scope')=='default' or (item.get('scope')=='persona' and item.get('target')==source['persona_id']) or (item.get('scope')=='session' and str(item.get('target','')).startswith(source['platform_id']+':'))]
            body=archive.encode(document)
        member='assets/'+archive.digest(body)+p.suffix.lower();assets[member]=body
        items.append({'relative':relative.as_posix(),'member':member,'original_path':str(p),'bytes':len(body)})
    return {'files':items},assets


def plan(host,docs,assets,source,target,policy='use_archive'):
    paths={};writes=[]
    for name in ('resources','memes'):
        if name not in docs:continue
        root=roots(host)['memes' if name=='memes' else 'companion']
        seen=set()
        for item in docs[name].get('files',[]):
            if not isinstance(item,dict) or not safe_relative(item.get('relative')) or item.get('member') not in assets:
                raise ValueError('资源清单路径或内容无效')
            rel=Path(item['relative'])
            if any(x in EXCLUDE for x in rel.parts):raise ValueError('资源试图写入保留目录')
            if rel.suffix.lower() not in IMAGE and not((name=='memes' and rel.name in JSON_NAMES) or (name=='resources' and rel.name in {'asset_index.json','index.json'})):raise ValueError('不支持的资源类型')
            dest=root/rel
            if dest.is_symlink() or not dest.resolve().is_relative_to(root.resolve()):raise ValueError('资源目标路径不安全')
            if str(dest) in seen:raise ValueError('重复的资源目标')
            seen.add(str(dest));body=assets[item['member']]
            if len(body)!=item.get('bytes'):raise ValueError('资源长度不一致')
            if rel.suffix.lower()=='.json':
                value=json.loads(body);value=binding.rebind(value,source,target)
                if isinstance(value,dict):
                    if name=='memes' and rel.name=='selection_rules.json':
                        existing_persona=any(item.get('scope')=='persona' and item.get('target')==target['persona_id'] for item in value.get('rules',[]))
                        rules=[]
                        for rule in value.get('rules',[]):
                            if rule.get('scope')=='default':
                                if existing_persona:continue
                                rule=dict(rule,scope='persona',target=target['persona_id'])
                            if rule.get('scope')=='persona':rule['id']='portable-persona-'+archive.digest(target['persona_id'].encode())[:20]
                            rules.append(rule)
                        value['rules']=rules
                    for key in ('personas','persona_rules','rules_by_persona'):
                        rules=value.get(key)
                        if isinstance(rules,dict) and source['persona_id'] in rules and source['persona_id']!=target['persona_id']:
                            rules[target['persona_id']]=rules.pop(source['persona_id'])
                body=archive.encode(value)
            paths[str(item.get('original_path') or '')]=str(dest)
            paths['file://'+str(item.get('original_path') or '')]='file://'+str(dest)
            paths[item['relative']]=str(dest)
            writes.append((dest,body))
    writes=[(p,archive.encode(binding.rebind(json.loads(body),source,target,path_map=paths)) if p.suffix.lower()=='.json' else body) for p,body in writes]
    writes=[(p,archive.encode(merge_json(json.loads(p.read_text(encoding='utf-8-sig')),json.loads(body),policy))) if p.suffix.lower()=='.json' and p.is_file() else (p,body) for p,body in writes]
    return paths,writes


def merge_json(current,incoming,policy):
    """Keep unrelated library entries and protect another persona's ownership."""
    if isinstance(current,dict) and isinstance(incoming,dict):
        for key in ('persona_id','owner_persona_id'):
            if current.get(key) and incoming.get(key) and current[key]!=incoming[key]:
                raise ValueError('同名资源属于另一人格，请跳过该资源模块或使用独立部署')
        result=dict(current)
        for key,value in incoming.items():
            if key in ('schema_version','schema') and key in result:continue
            result[key]=merge_json(result[key],value,policy) if key in result else value
        return result
    if isinstance(current,list) and isinstance(incoming,list):
        result=list(current)
        def key(item):
            if isinstance(item,dict):
                if item.get('scope') in ('persona','session'):return ('rule',item['scope'],item.get('target'))
                if item.get('id') is not None:return ('id',item['id'])
            return ('value',archive.encode(item))
        positions={key(item):i for i,item in enumerate(result)}
        for item in incoming:
            identifier=key(item)
            if identifier in positions:
                index=positions[identifier];result[index]=merge_json(result[index],item,policy)
            else:positions[identifier]=len(result);result.append(item)
        return result
    return incoming if policy=='use_archive' or current is None else current
