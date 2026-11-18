"""Local, read-only checks. No dependency is installed from an uploaded archive."""
from __future__ import annotations
import re

RULES = {
    'companion':('astrbot_plugin_private_companion','我会永远陪着你',(6,7,0),7,True),
    'persona':('astrbot_plugin_private_companion','AstrBot 人格接口',(6,7,0),7,True),
    'history':('astrbot_plugin_private_companion','真实经历与信息来源',(6,7,0),7,False),
    'resources':('astrbot_plugin_private_companion','陪伴图片资源',(6,7,0),7,False),
    'memory':('astrbot_plugin_memory_companion','我会牢牢记住你',(2,3,1),3,True),
    'memes':('meme_manager','表情包管理器',(5,2,5),6,False),
}


def version(value):
    match=re.match(r'^v?(\d+)\.(\d+)\.(\d+)(?:[-+.]|$)',str(value or ''))
    return tuple(map(int,match.groups())) if match else None


def inventory(host):
    result={}
    getter=getattr(getattr(host,'context',None),'get_all_stars',None)
    for item in (getter() if callable(getter) else []) or []:
        name=str(getattr(item,'root_dir_name','') or getattr(item,'name','') or getattr(item,'plugin_name',''))
        name={'我会永远陪着你':'astrbot_plugin_private_companion','我会牢牢记住你':'astrbot_plugin_memory_companion','表情包管理器':'meme_manager'}.get(name,name)
        result[name]={'version':str(getattr(item,'version','')),'active':bool(getattr(item,'activated',False)),
            'instance':getattr(item,'star_cls',None)}
    # The current companion instance is also the live migration API owner.
    result.setdefault('astrbot_plugin_private_companion',{'version':'6.7.0','active':True,'instance':host})
    return result


def check(host, manifest, selected):
    installed=inventory(host);rows=[]
    declarations=manifest.get('components',{})
    for key in declarations:
        if key not in RULES:
            required=declarations[key].get('required',True) is not False
            included=key in selected
            rows.append({'component':key,'plugin':'未知扩展','label':key,'required':required,'selected':included,
                'installed_version':None,'required_version':'需要对应的迁移适配器','ready':False,
                'blocking':included or required,'reason':'当前版本不支持此模块；可选模块可明确跳过，必需模块需升级后导入','install_url':None})
            continue
        plugin,label,minimum,max_major,required=RULES[key]
        meta=installed.get(plugin);actual=version(meta.get('version')) if meta else None
        ready=bool(meta and meta['active'] and meta['instance'] is not None and actual and actual>=minimum and actual[0]<max_major)
        reason='' if ready else '未安装、未启用或版本不兼容'
        if ready and key=='memory':
            ready=getattr(getattr(meta['instance'],'service',None),'store',None) is not None
            if not ready:reason='记忆插件服务尚未初始化'
        if ready and key=='persona':
            mgr=getattr(getattr(host,'context',None),'persona_manager',None)
            ready=all(callable(getattr(mgr,k,None)) for k in ('get_persona','create_persona','update_persona','delete_persona'))
            if not ready:reason='当前 AstrBot 缺少兼容的人格读写接口'
        included=key in selected
        rows.append({'component':key,'plugin':plugin,'label':label,'required':required,
            'selected':included,'installed_version':meta.get('version') if meta else None,
            'required_version':'>=' + '.'.join(map(str,minimum))+',<'+str(max_major)+'.0.0',
            'ready':ready,'blocking':(required and not included) or (included and not ready),
            'reason':('必需模块不能跳过' if required and not included else reason),
            'install_url':'https://github.com/menglimi/'+plugin if plugin.startswith('astrbot_plugin_') else 'https://github.com/anka-afk/astrbot_plugin_meme_manager'})
    return rows


def legacy_preflight(api,normalized):
    """Old JSON backups remain usable, without bypassing requested integrations."""
    requested=dict(normalized.get('settings') or {})
    requested.update(normalized.get('features') or {})
    if api._normalize_bool_value(requested.get('enable_livingmemory_integration',False)):
        rows=check(api.plugin,{'components':{'memory':{}}},{'memory'})
        if rows[0]['blocking']:raise ValueError('旧版备份请求启用长期记忆协同，请先安装并启用兼容的“我会牢牢记住你”（>=2.3.1,<3.0.0）')
