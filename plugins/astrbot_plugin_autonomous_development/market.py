"""Read native marketplace metadata; installing code still goes through an approved task."""
import json
import re
from pathlib import Path
from urllib.parse import quote
from .research import read_public

REPO = re.compile(r'^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$')

async def search(context, query):
    from astrbot.dashboard.services.plugin_service import PluginService
    source = PluginService.build_registry_source(None)
    path = Path(source.cache_file)
    if path.is_file() and path.stat().st_size <= 10_000_000:
        entries = json.loads(path.read_text())
        freshness = 'native_cache'
    else:
        acquired = await read_public(source.urls[0])
        entries = json.loads(acquired['text']); freshness = 'live_registry'
    if isinstance(entries, dict): entries = [{'id': key, **value} for key, value in entries.items() if isinstance(value, dict)]
    terms = [t for t in re.split(r'\s+',str(query).lower()) if t]
    result=[]
    for entry in entries if isinstance(entries,list) else []:
        if not isinstance(entry,dict):continue
        repo = str(entry.get('repo') or entry.get('repository') or '')
        if not REPO.fullmatch(repo):continue
        public = {k:str(entry.get(k,''))[:800] for k in ('id','name','desc','version','author','repo')}
        public['repo']=repo
        text=' '.join(public.values()).lower()
        if terms and not any(t in text for t in terms):continue
        public.update(install_state='not_authorized_by_discovery',autonomous_execution=False)
        result.append(public)
    installed=[{'name':str(s.name),'version':str(getattr(s,'version','')),'active':bool(getattr(s,'activated',False))} for s in context.get_all_stars()]
    return {'source':freshness,'matches':result[:15],'installed':installed,
        'note':'只发现与比较；版本、依赖、许可证、隔离测试和人格影响核验后，才可走任务审批安装。'}

async def pin(repository, ref='HEAD'):
    match=REPO.fullmatch(str(repository))
    if not match:raise PermissionError('仅接受不带登录凭据的GitHub仓库地址')
    owner,name=match.groups()
    acquired=await read_public(f'https://api.github.com/repos/{owner}/{name}/commits/{quote(str(ref),safe="")}')
    body=json.loads(acquired['text'])
    sha=body.get('sha','')
    if not re.fullmatch(r'[a-f0-9]{40}',sha):raise ValueError('仓库版本没有得到确认')
    return {'repository':f'https://github.com/{owner}/{name}','commit':sha,
        'source_prefix':f'https://raw.githubusercontent.com/{owner}/{name}/{sha}/','installed':False,
        'autonomous_execution':False,'required_checks':['entrypoint','dependencies','license','persona_isolation','framework_compatibility']}
