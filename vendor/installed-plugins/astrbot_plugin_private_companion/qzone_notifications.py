"""Durable polling of Qzone's scope=1 related notifications, never its default feed."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import re
import time
from types import SimpleNamespace
from datetime import datetime, timezone
from urllib.parse import parse_qs

from .bot_accounts import account_binding
from .qzone_native_actions import bound_client, call, enrich_detail
from .qzone_errors import QzoneIntegrationError


def clean(value, size=800):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:size]


def valid_uin(value):
    return bool(re.fullmatch(r'[1-9]\d{4,14}', str(value or '')))


class NoticeHtml(HTMLParser):
    def __init__(self, source_appid=''):
        super().__init__(convert_charrefs=True)
        self.source_appid=str(source_appid)
        self.stack=[];self.refs=set();self.comments=set()
        self.actor=[];self.action=[];self.preview=[]

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs);classes=set(attrs.get('class','').split())
        if tag not in {'img','i','br','input','hr','meta','link','wbr','source','area','embed'}:
            self.stack.append((tag,classes))
        if attrs.get('name')=='feed_data':
            # The outer uin is the actor, not necessarily the original author.
            sub=attrs.get('data-subappid','')
            if sub=='311' or (not sub and self.source_appid=='311'):
                self.add_ref(attrs.get('data-uin'),attrs.get('data-tid'))
        if attrs.get('data-param') and ('emotion_cgi_re_feeds' in attrs.get('data-action','') or attrs.get('data-cmd')=='qz_reply'):
            params=parse_qs(attrs['data-param'])
            if len(params.get('t1_uin',[]))==len(params.get('t1_tid',[]))==1:
                self.add_ref(params['t1_uin'][0],params['t1_tid'][0])
        if 'comments-item' in classes:
            self.comments.add((attrs.get('data-uin',''),attrs.get('data-tid','')))

    def add_ref(self, uin, tid):
        if valid_uin(uin) and re.fullmatch(r'[\w-]{1,160}',str(tid or '')):
            self.refs.add((str(uin),str(tid)))

    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:];break

    def handle_data(self,text):
        classes=set().union(*(c for _,c in self.stack)) if self.stack else set()
        if 'f-nick' in classes and 'f-name' in classes:self.actor.append(text)
        if 'f-nick' in classes and 'state' in classes:self.action.append(text)
        if 'txt-box-title' in classes and not ({'nickname','state'} & classes):self.preview.append(text)


def parse_notice(row):
    if not isinstance(row,dict) or not valid_uin(row.get('uin')) or not row.get('key'):
        return None
    dom=NoticeHtml(row.get('appid'));dom.feed(str(row.get('html') or ''))
    label=clean(' '.join(dom.action),80)
    kind=('like' if '赞' in label else 'comment' if '评论' in label or '回复' in label
          else 'mention' if any(x in label for x in ['提到','提及','@','艾特']) else 'unknown')
    identity=[str(row.get(k) or '') for k in ['appid','typeid','key','uin','abstime']]
    identity.extend([label,sorted(dom.refs),sorted(dom.comments)])
    marker=hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    ref=next(iter(dom.refs)) if len(dom.refs)==1 else ('','')
    try:stamp=max(0,int(row.get('abstime') or 0))
    except (TypeError,ValueError):stamp=0
    return {'id':marker,'actor_uin':str(row['uin']),'actor_name':clean(' '.join(dom.actor),80),
            'kind':kind,'label':label,'time':stamp,'post_uin':ref[0],'tid':ref[1],
            'preview':clean(' '.join(dom.preview)), 'reference_ambiguous':len(dom.refs)>1}


def namespace(owner, own_uin):
    binding=account_binding(owner)
    if not binding or str(binding.get('bot_id'))!=str(own_uin):
        raise QzoneIntegrationError('账号不匹配','通知账号与当前人格不一致')
    persona=owner._active_persona_scope() or owner._primary_persona_id()
    return [str(binding['adapter_id']),str(own_uin),str(persona)]


def store(owner, own_uin):
    root=owner._qzone_state_dict();ns=namespace(owner,own_uin)
    state=root.setdefault('related_notifications',{'namespace':ns,'seen':[],'pending':{},'observed':[]})
    if state.get('namespace')!=ns:
        raise QzoneIntegrationError('账号不匹配','通知历史属于另一个QQ账号，停止处理')
    return state


def save(owner):
    owner._save_data_sync(sections={'qzone_integration'})


async def poll(owner, own_uin):
    """Only new notifications create detail jobs; empty/unchanged polls create none."""
    client=bound_client(owner)
    if client is None:raise QzoneIntegrationError('通知不可用','没有当前账号的原生连接')
    state=store(owner,own_uin);root=owner._qzone_state_dict();now=time.time()
    if not state.get('started_at'):
        state['started_at']=now
        # Carry genuine work since the last successful old scan; don't replay old history.
        previous=float(root.get('last_comment_inbox_checked_at') or 0)
        failed=bool(root.get('last_comment_inbox_failed_at'))
        state['cutoff']=int(max(now-86400,previous)) if previous and not failed else int(now)
        save(owner)
    baseline=not state.get('baseline_complete')
    cursor=dict(state.get('baseline_cursor') or {}) if baseline else dict(state.get('scan_cursor') or {})
    # A catch-up resumes a bounded older tail after checking the head once.
    head_resume=cursor if not baseline and cursor else None
    if head_resume:cursor={}
    seen=set(state.get('seen',[]));new_count=0;read_count=0
    for index in range(10):
        offset=int(cursor.get('offset') or 0)
        page=await call(client,'get_qzone_notifications',offset=offset,count=20,
                        begin_time=str(cursor.get('begin_time') or '0'),end_time=str(cursor.get('end_time') or '0'))
        if not isinstance(page,dict) or str(page.get('self_uin'))!=str(own_uin) or not isinstance(page.get('items'),list):
            raise QzoneIntegrationError('通知读取失败','通知页结构或账号无效')
        if not isinstance(page.get('has_more'),bool):raise QzoneIntegrationError('通知读取失败','分页状态缺失')
        more=page['has_more'];next_offset=int(page.get('next_offset') or 0)
        if more and next_offset<=offset:raise QzoneIntegrationError('通知读取失败','分页游标未前进')
        notices=[n for row in page['items'] if (n:=parse_notice(row)) is not None]
        changed=[n for n in notices if n['id'] not in seen]
        for n in changed:
            seen.add(n['id']);state.setdefault('seen',[]).append(n['id'])
            state.setdefault('observed',[]).append(n)
            fresh=n['time']>state['cutoff']
            if fresh and n['actor_uin']!=str(own_uin):
                new_count+=1
                if n['tid'] and n['post_uin']==str(own_uin) and n['kind'] in {'comment','like','mention'}:
                    key=n['post_uin']+'/'+n['tid']
                    job=state.setdefault('pending',{}).setdefault(key,{'uin':n['post_uin'],'tid':n['tid'],
                        'discovered_at':now,'actors':[],'kinds':[],'attempts':0})
                    job['actors']=list(dict.fromkeys([*job['actors'],n['actor_uin']]))
                    job['kinds']=list(dict.fromkeys([*job['kinds'],n['kind']]))
                    job['retry_after']=0
                else:
                    # Unknown formats and third-party mentions stay observable; never guess a reply target.
                    state.setdefault('unresolved',[]).append(n)
                    state.setdefault('memory_pending',{})[n['id']]=n
        state['observed']=state.get('observed',[])[-40:]
        state['unresolved']=state.get('unresolved',[])[-40:]
        state['seen']=list(dict.fromkeys(state.get('seen',[])))[-2000:]
        read_count+=len(notices)
        next_cursor={'offset':next_offset,'begin_time':str(page.get('begin_time') or '0'),
                     'end_time':str(page.get('end_time') or '0')}
        if baseline:
            if more:state['baseline_cursor']=next_cursor
            else:state['baseline_complete']=True;state.pop('baseline_cursor',None)
        elif more:state['scan_cursor']=next_cursor
        else:state.pop('scan_cursor',None)
        save(owner)
        if head_resume:
            cursor=head_resume;head_resume=None
            continue
        # Baseline must finish before old pages can be interpreted as new notifications.
        if not more or (not baseline and notices and not changed):
            if not baseline:state.pop('scan_cursor',None)
            break
        cursor=next_cursor
    await remember_notices(owner,state)
    state['last_checked_at']=now;state['last_new_count']=new_count;state['last_read_count']=read_count
    state['status']='ready' if state.get('baseline_complete') else 'seeding'
    save(owner)
    return state


async def notified_posts(owner, own_uin):
    state=await poll(owner,own_uin)
    if not state.get('baseline_complete'):return []
    client=bound_client(owner);posts=[];now=time.time();root=owner._qzone_state_dict()
    retries=set(root.get('comment_inbox_retry_ids',[]))
    # Existing confirmed pending work survives the switch, including an old post.
    for identity in retries:
        tid=identity.split(':',1)[0]
        if re.fullmatch(r'[\w-]{1,160}',tid) and tid!='post':
            state.setdefault('pending',{}).setdefault(str(own_uin)+'/'+tid,
                {'uin':str(own_uin),'tid':tid,'discovered_at':now,'actors':[],'kinds':['comment'],'attempts':0})
    errors=[]
    due=[(key,job) for key,job in state.get('pending',{}).items() if float(job.get('retry_after') or 0)<=now]
    for key,job in due[:10]:
        if now-float(job['discovered_at'])>86400:
            state.setdefault('expired',[]).append(key);state['expired']=state['expired'][-40:]
            del state['pending'][key];continue
        post=SimpleNamespace(uin=job['uin'],tid=job['tid'],text='',comments=[],images=[],create_time=0,
                             raw={'_snowluma_native_list':True})
        try:
            await enrich_detail(owner,client,post)
            post.text=str(post.raw.get('content') or '')
            post.comments=[c for c in post.comments if
                c.comment_id in retries or (c.create_time>=state['cutoff'] and
                (not job['actors'] or str(c.uin) in job['actors'] or str(c.uin)==str(own_uin)))]
            posts.append(post)
            job['awaiting_comment']='comment' in job['kinds'] and not any(str(c.uin)!=str(own_uin) for c in post.comments)
            job['loaded']=True
        except QzoneIntegrationError as exc:
            job['attempts']+=1;job['retry_after']=now+min(1800,300*2**min(job['attempts']-1,3))
            errors.append({'tid':post.tid,'stage':exc.stage})
    root['comment_inbox_read_errors']=errors
    save(owner)
    return posts


def finish(owner, posts, *, memory_ok=True):
    state=owner._qzone_state_dict().get('related_notifications',{})
    retries=owner._qzone_state_dict().get('comment_inbox_retry_ids',[])
    for post in posts:
        key=str(post.uin)+'/'+post.tid
        job=state.get('pending',{}).get(key,{})
        pending=[c for c in post.comments if c.comment_id in retries]
        if pending and all(owner._qzone_author_post_recently_replied(owner._qzone_state_dict(),post,c,now=time.time()) for c in pending):
            keys={owner._qzone_comment_author_post_key(post,c) for c in pending}
            records=owner._qzone_recent_author_reply_records(owner._qzone_state_dict(),now=time.time())
            deadlines=[float(r['ts'])+6*3600 for r in records if r.get('key') in keys]
            if deadlines:job['retry_after']=min(deadlines)
        if memory_ok and not job.get('awaiting_comment') and not any(str(x).startswith(post.tid+':') for x in retries):
            state.get('pending',{}).pop(key,None)
    save(owner)


async def remember_notices(owner,state):
    """Retain mentions/unsupported notifications without inventing a public reply target."""
    binding=account_binding(owner) or {}
    known=set(owner.data.get('users',{})) | {str(binding.get('primary_user_id') or '')}
    for key,notice in list(state.get('memory_pending',{}).items())[:20]:
        uid=notice['actor_uin']
        if uid not in known:
            del state['memory_pending'][key]
            continue
        try:
            bridge=owner._memory_companion_bridge();record=getattr(bridge,'record_event',None)
            if not callable(record):continue
            adapter,bot,persona=state['namespace']
            memory_id='qzone_notice_'+hashlib.sha256((json.dumps(state['namespace'])+key).encode()).hexdigest()[:40]
            content=(f"QQ空间与我相关通知：{notice['actor_name'] or uid}触发了「{notice['label'] or '类型尚未识别'}」提醒。"
                     f"通知中的动态预览：「{notice['preview']}」。具体评论或回复目标尚未核对，不代表已公开回应。")
            result=await record(content=content,memory_type='qzone_notification',memory_id=memory_id,message_id=memory_id,
                scope='private',session_id=f'{adapter}:FriendMessage:{uid}',platform=adapter,
                subject={'kind':'user','id':uid,'name':notice['actor_name']},object={'kind':'bot','id':bot,'name':persona},
                visibility='private_pair',reality_level='bot_action',sayability='direct',source_plugin='private_companion',
                tags=['qzone','与我相关',notice['kind']],confidence=1.0,importance=0.55,
                occurred_at=datetime.fromtimestamp(notice['time'],timezone.utc).isoformat() if notice['time'] else '',
                metadata={'owner_bot_id':bot,'persona_id':persona,'namespace_adapter_id':adapter,
                          'source_platform':'qzone','event_type':'qzone_notification','notification_id':key,
                          'target_verified':False,'target_uin':notice['post_uin'],'tid':notice['tid']})
            if result:del state['memory_pending'][key]
        except Exception:
            # Observed notifications remain durable for a later memory-only retry.
            continue
    save(owner)
