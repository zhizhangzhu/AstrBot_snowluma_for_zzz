"""Host-recorded provenance for acquired information; never treat a model note as evidence."""
from __future__ import annotations
import asyncio
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from astrbot.api import logger
from .persona_config import runtime_persona_setting

LABELS={'web_exploration':'主动搜索','news':'新闻阅读','ai_daily':'AI日报','qzone':'QQ空间阅读','bilibili':'视频阅读','self_diary':'自己的日记','user_requested_search':'聊天中要求查询','tool_search':'对话工具搜索'}
EVIDENCE={'search_title_only':'仅拿到搜索标题与链接','search_snippet':'拿到搜索摘要，未确认阅读正文','feed_excerpt':'拿到资讯源摘要','headline_only':'仅拿到标题','article_text':'读取到文字正文，尚未独立核实事实','subtitle_text':'读取到字幕，尚未独立核实事实','qzone_text':'读取到QQ动态文字；只证明作者这样说过','model_generated':'自我整理/创作内容，不是外部消息源','unknown':'原始证据不完整，不能声称已读正文'}


def clean(value,limit=500):return re.sub(r'[\x00-\x1f\x7f]+',' ',str(value or '')).strip()[:limit]
def number(value):
    try:return max(0,float(value or 0))
    except (ValueError,TypeError,OverflowError):return 0

def safe_url(value):
    try:
        u=urlsplit(str(value or ''))
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:return ''
        query=[(k,v) for k,v in parse_qsl(u.query,keep_blank_values=True) if not re.search(r'token|skey|cookie|signature|secret|password|authorization|api.?key|g_tk',k,re.I)]
        return urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),''))[:1500]
    except (ValueError,TypeError):return ''

def time_label(value):
    return datetime.fromtimestamp(value,ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds') if value else '获取时间未记录'


def scope_for(owner,event=None):
    pid=str(owner._effective_plugin_persona_id() or '')
    if event is not None:
        platform=str(event.unified_msg_origin).split(':',1)[0];bot=str(event.get_self_id())
    else:
        _,platform,bot,_=owner._qzone_watch_binding()
    if not platform or not bot or not pid:raise ValueError('source owner unresolved')
    return {'platform_id':platform,'bot_id':bot,'persona_id':pid,'scope':json.dumps([platform,bot,pid],ensure_ascii=False,separators=(',',':'))}


FUNCTIONS={
 'web_idle':('空闲时主动搜索','enable_web_exploration','autonomous'),
 'news_idle':('空闲时主动阅读新闻','enable_news_boredom_read','autonomous'),
 'news_daily':('每日新闻阅读','enable_news_daily_hot_read','scheduled'),
 'news_user':('聊天中要求读取新闻','enable_news_integration','user_requested'),
 'news_read':('新闻阅读','enable_news_integration','unknown'),
 'ai_daily':('AI日报跟踪阅读','enable_ai_daily_watch','unknown'),
 'qzone_browse':('后台自主浏览好友动态','enable_qzone_browse','autonomous'),
 'qzone_watch':('重点关注用户空间监听','enable_qzone_watch','background'),
 'qzone_chat_feed':('聊天中要求浏览好友动态','enable_qzone_integration','user_requested'),
 'qzone_chat_profile':('聊天中要求查看指定账号空间','enable_qzone_integration','user_requested'),
 'qzone_panel':('管理面板手动查看空间','enable_qzone_integration','admin_panel'),
 'qzone_history':('QQ空间历史记录，具体触发入口不完整','enable_qzone_integration','unknown'),
 'chat_search':('聊天中调用联网搜索工具','web_search','conversation_triggered'),
 'proactive_tool_search':('生成主动消息时调用搜索工具','','proactive_message'),
 'self_diary':('日记整理/创作','', 'self_generated'),
 'bilibili':('视频阅读','enable_bilibili_integration','unknown'),
 'unknown':('具体触发功能未记录','','unknown')}
ORIGIN_FIELDS=['function_id','function_label','feature_key','trigger_mode','trigger_reason','origin_basis','requested_by_id','origin_session_id','tool_name']

def origin_for(kind,payload,channel='',*,function_origin='',trigger_reason='',event=None,legacy=False):
    reason=clean(trigger_reason or payload.get('reason'),80)
    old=payload.get('provenance') if isinstance(payload.get('provenance'),dict) else {}
    fid=function_origin or old.get('function_id','')
    if not fid:
        if kind in ('web_exploration','user_requested_search','tool_search'):fid='web_idle'
        elif kind in ('user_requested_search','tool_search'):fid='chat_search'
        elif kind=='news':
            fid={'boredom':'news_idle','daily':'news_daily','user_query':'news_user','ai_daily':'ai_daily'}.get(reason,'news_read')
            if 'boredom' in channel:fid='news_idle'
            elif 'daily' in channel:fid='news_daily'
            elif 'user_query' in channel:fid='news_user'
        elif kind in ('ai_daily','self_diary','bilibili'):fid=kind
        elif kind=='qzone':
            if payload.get('discovery_channel')=='browse' or 'background_fetch' in channel:fid='qzone_browse'
            elif 'chat_read' in channel:fid='qzone_chat_feed'
            elif '重点关注' in channel:fid='qzone_watch'
            elif '指定账号' in channel:fid='qzone_chat_profile'
            else:fid='qzone_history'
    proactive=bool(event is not None and (getattr(event,'private_companion_proactive_framework',False) or getattr(event,'_private_companion_external_proactive_source','')))
    if fid=='chat_search' and proactive:fid='proactive_tool_search'
    if fid not in FUNCTIONS:fid='unknown'
    label,flag,mode=FUNCTIONS[fid]
    if fid=='ai_daily' and reason in ('scheduled','user_requested'):mode=reason
    result={'function_id':fid,'function_label':label,'feature_key':flag,'trigger_mode':mode,'trigger_reason':reason,
        'origin_basis':'live_call' if function_origin and not legacy else 'stored_history_category' if fid not in ('unknown','qzone_history') else 'history_incomplete',
        'requested_by_id':'','origin_session_id':'','tool_name':clean(payload.get('tool_name') or old.get('tool_name'),80)}
    if event is not None:
        result['requested_by_id']='' if proactive else str(event.get_sender_id());result['origin_session_id']=str(event.unified_msg_origin)
    else:
        result['requested_by_id']=str(old.get('requested_by_id') or '');result['origin_session_id']=str(old.get('origin_session_id') or '')
    return result


def make_record(binding,kind,payload,*,items=None,channel='',observed_at=0,legacy=False,function_origin='',trigger_reason='',event=None):
    if kind not in LABELS or not isinstance(payload,dict):raise ValueError('invalid source kind')
    p=payload;items=items if isinstance(items,list) else (p.get('results') or p.get('items') or [])
    items=[x for x in items if isinstance(x,dict)]
    observed=number(observed_at) or number(p.get('created_ts'))
    title=clean(p.get('topic') or p.get('headline') or p.get('title'),160)
    summary=clean(p.get('note') or p.get('impression') or p.get('summary') or p.get('text') or p.get('body'),1600)
    url=safe_url(p.get('source_url') or p.get('selected_link') or p.get('link') or p.get('url'))
    source_name=clean(p.get('source_title') or p.get('selected_source') or p.get('source_name'),180)
    query=clean(p.get('query'),180);author_id='';entry_id='';published=0
    retrieval_service=next((clean(x.get('provider'),40) for x in items if x.get('provider')),'')
    selected=next((x for x in items if clean(x.get('key'),100)==clean(p.get('selected_key'),100)),{}) if p.get('selected_key') else {}
    if not selected and url:selected=next((x for x in items if safe_url(x.get('url') or x.get('link'))==url),{})
    if not url and selected:url=safe_url(selected.get('link') or selected.get('url'))
    if not source_name:source_name=clean(selected.get('source') or selected.get('title'),180)
    evidence=[]
    for x in items[:8]:
        evidence.append({'title':clean(x.get('title'),160),'url':safe_url(x.get('url') or x.get('link')),
            'excerpt':clean(x.get('snippet') or x.get('summary'),400)})
    original_body=str(selected.get('article_text') or selected.get('video_subtitle_text') or '')
    if original_body and (selected.get('article_readable') or selected.get('video_subtitle_readable')):
        evidence.insert(0,{'title':clean(selected.get('title'),160),'url':safe_url(selected.get('article_link') or selected.get('link') or selected.get('url')),'excerpt':clean(original_body,1000)})
    if kind in ('web_exploration','user_requested_search','tool_search'):
        level='article_text' if selected.get('article_readable') and original_body else 'search_snippet' if any(x['excerpt'] for x in evidence) else 'search_title_only'
    elif kind in ('news','ai_daily','bilibili'):
        if selected.get('article_readable') and selected.get('article_text'):level='article_text'
        elif selected.get('video_subtitle_readable') and (selected.get('video_subtitle_text') or selected.get('video_context_text')):level='subtitle_text'
        elif any(x['excerpt'] for x in evidence):level='feed_excerpt'
        else:level='headline_only' if url else 'unknown'
    elif kind=='qzone':
        author_id=clean(p.get('uin') or p.get('author_id'),32);entry_id=clean(p.get('tid') or p.get('entry_id'),160)
        if not re.fullmatch(r'[1-9]\d{4,14}',author_id) or not re.fullmatch(r'[\w-]{1,160}',entry_id):raise ValueError('unverified qzone entry identity')
        url=f'https://user.qzone.qq.com/{author_id}/mood/{entry_id}'
        source_name=clean(p.get('name'),80) or author_id;title=summary[:100] or 'QQ空间动态'
        published=number(p.get('create_time'));level='qzone_text'
        evidence=[{'title':source_name,'url':url,'excerpt':summary[:800]}]
    else:level='model_generated';url='';source_name='当前人格自己的日记/创作';evidence=[]
    if legacy and kind in ('web_exploration','user_requested_search','tool_search','news','ai_daily','bilibili') and not any(x['excerpt'] for x in evidence):level='unknown'
    # Evidence does not assert factual truth, even when body text was fetched.
    flags=[];host=urlsplit(url).hostname or ''
    if any(host==d or host.endswith('.'+d) for d in ('taobao.com','tmall.com','jd.com','pinduoduo.com')):flags.append('shopping_or_commercial_page')
    if level in ('search_title_only','headline_only','unknown'):flags.append('insufficient_original_evidence')
    identity=[binding['scope'],kind,author_id,entry_id] if kind=='qzone' else [binding['scope'],kind,observed,query,url,title]
    sid='src_'+hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:28]
    origin=origin_for(kind,p,channel,function_origin=function_origin,trigger_reason=trigger_reason,event=event,legacy=legacy)
    return {**binding,**origin,'function_origins':[dict(origin,first_seen_at=observed)],'visibility':'session' if kind in ('user_requested_search','tool_search') or origin['trigger_mode']=='user_requested' else 'owner',
        'source_id':sid,'source_kind':kind,'channel':channel or LABELS[kind],
        'acquired_at':observed,'acquired_at_local':time_label(observed),'source_title':source_name,'source_url':url,
        'title':title,'query':query,'model_note':summary,'evidence_level':level,'evidence_description':EVIDENCE[level],
        'evidence':evidence,'verification':'not_independently_verified' if kind!='self_diary' else 'self_generated',
        'quality_flags':flags,'author_id':author_id,'entry_id':entry_id,'published_at':published,
        'legacy_import':bool(legacy),'acquisition_recorded':bool(observed),'retrieval_service':retrieval_service,
        'original_text_chars':len(original_body),'original_text_sha256':hashlib.sha256(original_body.encode()).hexdigest() if original_body else ''}


def provenance(record):
    keys=['source_id','source_kind','channel','acquired_at','acquired_at_local','source_title','source_url','evidence_level','evidence_description','verification','quality_flags','author_id','entry_id','legacy_import','retrieval_service','original_text_chars','original_text_sha256']
    keys+=ORIGIN_FIELDS+['function_origins','visibility']
    result={k:record[k] for k in keys if k in record}
    result['tool_origin']={key:record.get(key,'') for key in ORIGIN_FIELDS}
    return result


def query_terms(query):
    chunks=re.findall(r'[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}',str(query).lower())
    stop='从哪里|是哪里|在哪里|从哪|哪来|哪里|哪儿|来自|渠道|获得|得到|看过|读过|搜了|看了|了啥|哪些|新东西|新消息|为什么|这件事|这些|网站|网页|到底|关于|哪个|来源|出处|消息源|信息|刚才|之前|那个|这个|那条|这条|是不是|自己|真的|假的|尊嘟假嘟|真的假的|真假|当时|搜过|搜索|搜到|了解|最近|今天|新闻|什么|怎么|告诉|知道|主动|一下|你的|我的|是从|看到|获取|内容|请问|链接|网址|说的|说过|提到'
    terms=[v.strip('这一那你我她它的了呢啊呀吗是也才就么哦喔吖') for t in chunks for v in re.split(stop,t)]
    return list(dict.fromkeys(v for v in terms if len(v)>=2))[:24]


def brief_records(records):
    out=[]
    for r in records:
        out.append({**provenance(r),'title':r['title'],'query':r['query'],'model_note':r['model_note'][:500],
            'evidence':[dict(x,excerpt=x.get('excerpt','')[:200]) for x in r['evidence'][:2]]})
    return out


class SourceIndex:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.path.parent.chmod(0o700)
        with closing(self.connect()) as db,db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS information_sources(id TEXT PRIMARY KEY,scope TEXT NOT NULL,kind TEXT NOT NULL,acquired REAL NOT NULL,body TEXT NOT NULL,memory_state TEXT DEFAULT 'pending');
            CREATE INDEX IF NOT EXISTS information_sources_scope ON information_sources(scope,acquired);
            CREATE TABLE IF NOT EXISTS followup_sources(scope TEXT,umo TEXT,cid TEXT,ts REAL,body TEXT,PRIMARY KEY(scope,umo,cid));
            """)
        self.path.chmod(0o600)
    def connect(self):return sqlite3.connect(self.path,timeout=4)
    def put(self,records):
        with closing(self.connect()) as db,db:
            for r in records:
                db.execute('INSERT OR IGNORE INTO information_sources(id,scope,kind,acquired,body,memory_state) VALUES(?,?,?,?,?,?)',
                    (r['source_id'],r['scope'],r['source_kind'],r['acquired_at'],json.dumps(r,ensure_ascii=False),'legacy' if r['legacy_import'] else 'pending'))
                row=db.execute('SELECT body FROM information_sources WHERE id=?',(r['source_id'],)).fetchone()
                old=json.loads(row[0]);origins=list(old.get('function_origins') or [])
                if not old.get('function_id'):
                    first=origin_for(old['source_kind'],old,old.get('channel',''),legacy=True)
                    old.update(first);origins=[dict(first,first_seen_at=old.get('acquired_at',0))]
                for origin in r.get('function_origins',[]):
                    key=(origin['function_id'],origin.get('trigger_mode'),origin.get('origin_session_id'),origin.get('requested_by_id'),origin.get('tool_name'))
                    known=next((x for x in origins if (x['function_id'],x.get('trigger_mode'),x.get('origin_session_id'),x.get('requested_by_id'),x.get('tool_name'))==key),None)
                    if known is None:origins.append(origin)
                    elif origin.get('first_seen_at') and (not known.get('first_seen_at') or origin['first_seen_at']<known['first_seen_at']):known['first_seen_at']=origin['first_seen_at']
                    # Only refine the first acquisition when the matching historical event supports it.
                    if old.get('trigger_mode')=='unknown' and origin.get('trigger_mode')!='unknown' and abs(number(old.get('acquired_at'))-number(origin.get('first_seen_at')))<=1:
                        old.update({k:origin.get(k,'') for k in ORIGIN_FIELDS})
                origins=[o for o in origins if not (o.get('trigger_mode')=='unknown' and any(n.get('trigger_mode')!='unknown' and abs(number(n.get('first_seen_at'))-number(o.get('first_seen_at')))<=1 for n in origins))]
                old['function_origins']=sorted(origins,key=lambda x:number(x.get('first_seen_at')))[:12]
                if old.get('visibility')=='session' and r.get('visibility')=='owner':old['visibility']='owner'
                if json.dumps(old,ensure_ascii=False)!=row[0]:db.execute('UPDATE information_sources SET body=? WHERE id=?',(json.dumps(old,ensure_ascii=False),r['source_id']))
    def query(self,scope,query='',source_id='',kind='',limit=5,umo=''):
        limit=max(1,min(12,int(limit or 5)))
        with closing(self.connect()) as db:
            if source_id:
                rows=db.execute('SELECT body FROM information_sources WHERE scope=? AND id=?',(scope,source_id)).fetchall()
            else:
                sql='SELECT body FROM information_sources WHERE scope=?';args=[scope]
                if kind:sql+=' AND kind=?';args.append(kind)
                rows=db.execute(sql+' ORDER BY acquired DESC LIMIT 1500',args).fetchall()
        records=[json.loads(row[0]) for row in rows]
        records=[r for r in records if r.get('visibility')!='session' or r.get('origin_session_id')==umo]
        for r in records:
            origins=[o for o in r.get('function_origins',[]) if not o.get('origin_session_id') or o.get('origin_session_id')==umo]
            r['function_origins']=origins
            if r.get('origin_session_id') and r['origin_session_id']!=umo:
                replacement=origins[0] if origins else origin_for(r['source_kind'],{},legacy=True)
                r.update({key:replacement.get(key,'') for key in ORIGIN_FIELDS})
        for r in records:r['tool_origin']={key:r.get(key,'') for key in ORIGIN_FIELDS}
        if source_id or not query.strip():return records[:limit]
        terms=query_terms(query)
        if not terms:return records[:limit]
        ranked=[]
        for r in records:
            title=(r['title']+' '+r['query']).lower();body=(title+' '+r['model_note']+' '+r['source_title']).lower();score=0
            for term in terms:
                if term in body:score+=len(term)*(3 if term in title else 1)
                elif len(term)>3:
                    score+=sum(1 for n in range(len(term)-2) if term[n:n+3] in body)
            if score:ranked.append((score,r['acquired_at'],r))
        ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
        return [x[2] for x in ranked[:limit]]
    def remember(self,binding,umo,cid,records):
        with closing(self.connect()) as db,db:
            db.execute('INSERT OR REPLACE INTO followup_sources VALUES(?,?,?,?,?)',(binding['scope'],umo,cid,time.time(),json.dumps([r['source_id'] for r in records[:6]])))
    def recall(self,binding,umo,cid):
        with closing(self.connect()) as db:
            row=db.execute('SELECT ts,body FROM followup_sources WHERE scope=? AND umo=? AND cid=?',(binding['scope'],umo,cid)).fetchone()
        if not row or time.time()-row[0]>3600:return []
        return [r for sid in json.loads(row[1]) for r in self.query(binding['scope'],source_id=sid,umo=umo)]
    def memory_state(self,sid,value=None):
        with closing(self.connect()) as db,db:
            if value is not None:db.execute('UPDATE information_sources SET memory_state=? WHERE id=?',(value,sid))
            row=db.execute('SELECT memory_state FROM information_sources WHERE id=?',(sid,)).fetchone()
            return row[0] if row else ''


class InformationSources:
    def __init__(self,owner):
        self.owner=owner;self.index=SourceIndex(Path(owner.data_dir)/'information_sources'/'sources.db')
    def allowed(self,event):
        if not self.owner.enabled or not event.is_private_chat():return False
        admins=self.owner.context.get_config().get('admins_id',[])
        if isinstance(admins,str):admins=[admins]
        return str(event.get_sender_id()) in [str(x) for x in admins] and self.owner._bot_scope_allows_event(event)
    async def sync_existing(self,binding):
        # Legacy persona data had no Bot partition: attribute it only if the actual single QQ binding agrees.
        try:
            _,platform,bot,_=self.owner._qzone_watch_binding()
            if (platform,bot)!=(binding['platform_id'],binding['bot_id']):return
        except Exception:return
        data=self.owner.data;records=[]
        for key,kind,field in [('web_exploration','web_exploration','notes'),('news_integration','news','digests')]:
            state=data.get(key,{}) or {};items=list(state.get(field,[]) or [])
            if isinstance(state.get('last_digest'),dict):items.append(state['last_digest'])
            for item in items:
                if not isinstance(item,dict) or item.get('search_failed') or item.get('no_results'):continue
                if not (item.get('source_url') or item.get('selected_link') or item.get('query')):continue
                records.append(make_record(binding,kind,item,legacy=True))
        news=data.get('news_integration',{}).get('ai_daily',{}) or {}
        if isinstance(news.get('last_digest'),dict) and news.get('last_success_date'):
            p={**news['last_digest'],'source_title':news.get('last_source_name'),'source_url':news.get('last_text_link') or news.get('last_video_link')}
            records.append(make_record(binding,'ai_daily',p,legacy=True))
        for item in data.get('bot_diaries',[]) or []:
            if isinstance(item,dict):
                p={**item,'title':str(item.get('date',''))+' 日记'}
                generated=item.get('generated_at');timestamp=number(generated)
                if not timestamp and isinstance(generated,str):
                    try:parsed=datetime.fromisoformat(generated);timestamp=(parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))).timestamp()
                    except ValueError:pass
                records.append(make_record(binding,'self_diary',p,observed_at=timestamp,legacy=True))
        path=Path(self.owner.data_dir)/'qzone_watch'/'watch.db'
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                for body,discovered in db.execute('SELECT body,discovered FROM posts WHERE scope=? ORDER BY discovered DESC LIMIT 1000',(binding['scope'],)):
                    p=json.loads(body);records.append(make_record(binding,'qzone',p,observed_at=discovered,channel='后台好友浏览' if p.get('discovery_channel')=='browse' else '重点关注空间监听',legacy=True))
                row=db.execute('SELECT ts,origin,body FROM browse_snapshots WHERE scope=?',(binding['scope'],)).fetchone()
                if row:
                    for p in json.loads(row[2]):records.append(make_record(binding,'qzone',p,observed_at=row[0],channel='好友动态流：'+str(row[1]),legacy=True))
        await asyncio.to_thread(self.index.put,records)
    async def capture_digest(self,kind,payload,*,items=None,channel='',event=None,function_origin='',trigger_reason=''):
        try:
            binding=scope_for(self.owner,event);record=make_record(binding,kind,payload,items=items,channel=channel,function_origin=function_origin,trigger_reason=trigger_reason,event=event)
            await asyncio.to_thread(self.index.put,[record])
            from .real_schedule import observe_source
            observe_source(self.owner,record)
            payload['information_source_id']=record['source_id'];payload['provenance']=provenance(record)
            if kind in ('web_exploration','news','ai_daily','bilibili'):
                await asyncio.wait_for(self.export_memory(record),timeout=5)
            return record
        except asyncio.CancelledError:raise
        except Exception as exc:
            logger.warning('[信息来源] 获取记录保存/记忆传递暂不可用 kind=%s error=%s',kind,type(exc).__name__)
            return None
    async def capture_qzone(self,posts,*,channel,observed_at=0,event=None,function_origin=''):
        try:
            binding=scope_for(self.owner,event)
            records=[]
            for post in posts:
                r=make_record(binding,'qzone',post,observed_at=observed_at or time.time(),channel=channel,function_origin=function_origin,event=event)
                post['information_source_id']=r['source_id'];post['provenance']=provenance(r);records.append(r)
            await asyncio.to_thread(self.index.put,records)
            from .real_schedule import observe_source
            for record in records:observe_source(self.owner,record)
        except asyncio.CancelledError:raise
        except Exception as exc:logger.warning('[信息来源] 空间来源记录暂不可用 error=%s',type(exc).__name__)
    async def export_memory(self,r):
        if not self.owner._memory_companion_bridge_enabled():return
        if await asyncio.to_thread(self.index.memory_state,r['source_id']) in ('stored','legacy'):return
        bridge=self.owner._memory_companion_bridge()
        recorder=getattr(bridge,'record_search_action' if r['source_kind']=='web_exploration' else 'record_reading',None) if bridge else None
        if not callable(recorder):return
        content=f"Bot于{r['acquired_at_local']}通过{r['channel']}获取了《{r['title']}》的资料；来源：{r['source_title']}。{r['evidence_description']}；资料尚未独立核验。整理笔记（不等于已证实事实）：{r['model_note']}。来源编号：{r['source_id']}"
        metadata={'owner_bot_id':r['bot_id'],'bot_id':r['bot_id'],'persona_id':r['persona_id'],
            'information_sources':[provenance(r)],'information_source_ids':[r['source_id']],
            'query_anchors':[r['title'],r['query'],r['source_title'],'消息来源','哪里看到的'],
            'external_claim_verified':False,'source_record_type':'acquisition_event_not_verified_fact'}
        result=await recorder(content=content,scope='unknown',session_id='private_companion:information_sources',platform=r['platform_id'],
            subject={'kind':'bot','id':r['bot_id'],'name':r['persona_id'],'role':'bot_self'},
            memory_id='companion_'+r['source_id'],message_id='companion_'+r['source_id'],metadata=metadata,
            confidence=0.9,importance=0.5,source_plugin='private_companion',occurred_at=r['acquired_at_local'])
        if result:await asyncio.to_thread(self.index.memory_state,r['source_id'],'stored')
    async def get_records(self,event,req=None,query='',source_id='',kind='',limit=5,remember=True):
        if not self.allowed(event):raise PermissionError('仅管理员私聊可查信息来源')
        binding=scope_for(self.owner,event);await self.sync_existing(binding)
        if kind and kind not in LABELS:raise ValueError('不支持的来源类别')
        records=await asyncio.to_thread(self.index.query,binding['scope'],query,source_id,kind,limit,str(event.unified_msg_origin))
        cid=str(getattr(getattr(req,'conversation',None),'cid','') or await self.owner.context.conversation_manager.get_curr_conversation_id(str(event.unified_msg_origin)) or '')
        if records and cid and remember:await asyncio.to_thread(self.index.remember,binding,str(event.unified_msg_origin),cid,records)
        return binding,cid,records
    async def capture_search_tool(self,event,tool,tool_args,result):
        name=str(getattr(tool,'name',''))
        if not self.owner.enabled or not self.owner._bot_scope_allows_event(event):return
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,100}',name):return
        tool_origins=list(getattr(event,'_companion_tool_origins',[]) or [])
        proactive=bool(getattr(event,'private_companion_proactive_framework',False) or getattr(event,'_private_companion_external_proactive_source',''))
        tool_origins.append({'function_id':'proactive_tool_call' if proactive else 'chat_tool_call','function_label':'聊天中调用工具','tool_name':name,
            'trigger_mode':'proactive_message' if proactive else 'conversation_triggered','requested_by_id':'' if proactive else str(event.get_sender_id()),'origin_session_id':str(event.unified_msg_origin),
            'acquired_at_local':time_label(time.time()),'verification':'tool_failed' if bool(getattr(result,'isError',False)) else 'tool_returned_not_independently_verified'})
        event._companion_tool_origins=tool_origins[-12:]
        if not re.search(r'^(?:anysearch_.*search|web_search_|internet_search)',name):return
        if bool(getattr(result,'isError',False)):return
        text=''
        if isinstance(result,str):text=result
        else:
            for block in getattr(result,'content',[]) or []:
                if isinstance(block,dict) and block.get('type')=='text':text+=str(block.get('text',''))
                elif getattr(block,'type',None)=='text':text+=str(getattr(block,'text',''))
        if not text:return
        try:raw=json.loads(text)
        except (ValueError,TypeError):raw=None
        items=[]
        def visit(value):
            if isinstance(value,dict):
                url=safe_url(value.get('url') or value.get('link'))
                if url:items.append({'title':clean(value.get('title'),160),'url':url,'snippet':clean(value.get('snippet') or value.get('content') or value.get('description'),400),'provider':name})
                else:
                    for v in value.values():
                        if isinstance(v,(dict,list)):visit(v)
            elif isinstance(value,list):
                for v in value[:30]:visit(v)
        visit(raw)
        if not items:
            for link in re.findall(r'https?://[^\s<>"\)\]]+',text)[:8]:
                url=safe_url(link.rstrip('，。；;'))
                if url:items.append({'title':'工具返回的网页链接','url':url,'provider':name})
        if not items:return
        args=tool_args if isinstance(tool_args,dict) else {}
        query=clean(args.get('query') or args.get('queries') or args.get('q'),180)
        payload={'topic':query or '聊天请求的联网查询','query':query,'note':'此记录保存本轮联网工具返回的原始摘录，不代表已阅读网页全文或已核验事实。',
            'source_title':items[0]['title'],'source_url':items[0]['url'],'created_ts':time.time(),'tool_name':name,'results':items[:8]}
        record=await self.capture_digest('tool_search',payload,items=items,channel='聊天联网工具查询',event=event,function_origin='chat_search')
        if record:
            refs=list(getattr(event,'_companion_information_sources',[]) or [])
            if not any(r.get('source_id')==record['source_id'] for r in refs):refs.append(provenance(record))
            event._companion_information_sources=refs[-8:]

    async def tool(self,event,query='',source_id='',source_kind='',limit=5):
        if not self.allowed(event):return json.dumps({'ok':False,'reason':'仅管理员私聊可用'},ensure_ascii=False)
        req=event.get_extra('provider_request') or event.get_extra('function_awareness_request')
        _,_,records=await self.get_records(event,req,clean(query,200),clean(source_id,80),str(source_kind),max(1,min(12,int(limit))))
        event._companion_information_sources=[provenance(r) for r in records]
        return json.dumps({'ok':True,'items':records,'note':'获取记录证明当时执行过获取，不证明外部陈述为真。model_note是模型整理，原始evidence优先。标题/摘要不等于读过正文；无记录就说无法核实，不编造网站/时间/搜索经历。'},ensure_ascii=False)
    async def inject(self,event,req):
        if not self.allowed(event):return
        text=str(getattr(event,'message_str','') or '')
        asks=bool(re.search(r'来源|出处|从哪|哪[里儿].*(?:看|搜|读|消息)|消息源|网址|链接|自己.*搜|搜过|主动.*(?:获取|搜索|浏览)|(?:最近|今天|这两天|刚才).{0,20}(?:搜|查|读|看|听|了解|发现|获取|新东西|新闻)|之前.*(?:搜|看)|真[的嘟].*假',text))
        if not asks:return
        binding=scope_for(self.owner,event);await self.sync_existing(binding)
        cid=str(getattr(getattr(req,'conversation',None),'cid','') or await self.owner.context.conversation_manager.get_curr_conversation_id(str(event.unified_msg_origin)) or '')
        broad=bool(re.search(r'(?:最近|今天|这两天).{0,20}(?:什么|啥|哪些|新东西|新消息)|最近.*(?:了解|发现)',text))
        records=[]
        explicit=query_terms(text) if not broad else []
        if explicit:
            records=await asyncio.to_thread(self.index.query,binding['scope'],' '.join(explicit),'','',6,str(event.unified_msg_origin))
        elif not broad:
            records=await asyncio.to_thread(self.index.recall,binding,str(event.unified_msg_origin),cid)
        if not records and not explicit:
            contexts=getattr(req,'contexts',[]) or [];prior=[]
            for row in contexts[-8:]:
                if isinstance(row,dict):
                    value=row.get('content','')
                    if isinstance(value,list):value=''.join(str(x.get('text','')) for x in value if isinstance(x,dict) and x.get('type')=='text')
                    if row.get('role')=='assistant' and value:prior.append(str(value))
            query='' if broad else text+' '+(' '.join(prior[-1:])[:700])
            records=await asyncio.to_thread(self.index.query,binding['scope'],query,'','',6,str(event.unified_msg_origin))
        if records and cid:await asyncio.to_thread(self.index.remember,binding,str(event.unified_msg_origin),cid,records)
        event._companion_information_sources=[provenance(r) for r in records]
        from .conversation_prompt_section import prompt_section,prompt_cdata
        body=('下面是当前Bot和人格实际留存的来源记录。它们说明何时从什么渠道获取，不等于内容已核实。'
            '工具来源（tool_origin）里的function_label/feature_key/trigger_mode说明由哪个功能触发、是自主/定时/用户要求/被动获取；requested_by_id是查询请求者。原始信息来源和获取功能要分清，不能把手动查询说成自主获取。function_origins记录同一信息被不同功能获取的经历。搜索服务只是渠道，source_title/source_url才是原网页；QQ动态按作者和动态编号归属；自己的日记是自我整理，不是外部证据。'
            'model_note是模型笔记；evidence_level为标题或摘要时，不得声称读过全文。被问来源时给对应网站/作者、获取时间及可用链接，不编造。'
            '购物导购等弱来源要说明尚未核实。没有对应记录就说无法确认；不要把这次聊天临时检索当作过去主动浏览。'
            '正常闲聊不必每句话附引用。需要历史或更精确来源可调用pc_recall_information_sources，用具体话题或source_id查询。\n'
            +json.dumps(brief_records(records),ensure_ascii=False))
        if not records:body+='\n自动匹配未命中不代表没有历史记录；先调用pc_recall_information_sources按当前具体话题或上文关键词查询，再判断能否核实来源。'
        section=prompt_section(key='information_sources.recent',title='可核对的信息来源与工具来源',source='host_acquisition_records',content=prompt_cdata(body))
        marker='<!-- companion_information_sources_v1 -->'
        if not self.owner._append_turn_prompt_fragment_by_position(req,marker,section,priority=86):
            self.owner._materialize_conversation_system_block(req,section=section,marker=marker,priority=86)


def service(owner):
    result=getattr(owner,'_information_sources_service',None)
    if result is None:result=InformationSources(owner);owner._information_sources_service=result
    return result


def shared_metadata(owner,user,reason):
    keys={'web_exploration_share':('web_exploration_context','web_exploration'), 'news_share':('news_context','news'), 'qzone_watch_share':('qzone_watch_context','qzone')}
    selected=keys.get(str(reason));p=user.get(selected[0],{}) if selected else {}
    if not isinstance(p,dict) or not p:return {}
    ref=p.get('provenance')
    if not isinstance(ref,dict):
        try:ref=provenance(make_record(scope_for(owner),selected[1],p,legacy=True))
        except Exception:return {}
    return {'information_sources':[ref],'information_source_ids':[ref.get('source_id')],'external_claim_verified':False}


async def api_sources(owner):
    from astrbot.api.web import request
    try:
        event=await owner._get_function_awareness().diagnostic_event(str(request.query.get('umo','')))
        token,_=await owner._activate_persona_for_event_context(event)
        try:
            binding,cid,records=await service(owner).get_records(event,query=str(request.query.get('query','')),source_id=str(request.query.get('source_id','')),kind=str(request.query.get('source_kind','')),limit=int(request.query.get('limit',6)),remember=False)
            return {'status':'ok','data':{'scope':binding,'conversation_id':cid,'count':len(records),'items':records,'mode':'local_records_only_no_external_request'}}
        finally:owner._deactivate_persona_for_event(token)
    except Exception as exc:
        logger.warning('[信息来源] 本地来源查询失败 error=%s',type(exc).__name__)
        return {'status':'error','message':'来源查询暂不可用：'+type(exc).__name__}
