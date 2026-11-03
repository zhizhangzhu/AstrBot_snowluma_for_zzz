"""Read and like through the registered SnowLuma account, without browser HTTP."""
from __future__ import annotations
import asyncio
from .qzone_errors import QzoneIntegrationError
from .qzone_recent_parser import parse_recent_feeds


def bound_client(owner,event=None):
    if not callable(getattr(owner,'_qzone_watch_binding',None)):return None
    from .bot_accounts import account_binding
    binding=account_binding(owner)
    if not binding or binding.get('session_mode')!='onebot':return None
    _scope,adapter,bot,client=owner._qzone_watch_binding()
    if str(bot)!=str(binding.get('bot_id')) or str(adapter)!=str(binding.get('adapter_id')):
        raise QzoneIntegrationError('账号不匹配','原生空间账号与当前人格不一致')
    if event is not None and (str(event.get_self_id())!=str(bot) or str(event.unified_msg_origin).split(':',1)[0]!=str(adapter)):
        raise QzoneIntegrationError('账号不匹配','当前聊天与原生空间账号不一致')
    return client


async def call(client,action,**params):
    # Exactly one transport invocation, including after an uncertain write.
    try:raw=await asyncio.wait_for(client.call_action(action,**params),timeout=15)
    except asyncio.CancelledError:raise
    except Exception as exc:
        write=action=='like_qzone'
        raise QzoneIntegrationError('投递结果未知' if write else '原生读取失败',type(exc).__name__,
            retryable=not write,delivery_unknown=write) from exc
    # AioCQHTTP unwraps a successful OneBot {data:null} to None; failed
    # acknowledgements raise ActionFailed before returning from call_action.
    if raw is None and action=='like_qzone':return None
    if not isinstance(raw,dict) or raw.get('status')=='failed' or str(raw.get('retcode',0))!='0':
        raise QzoneIntegrationError('原生接口拒绝','QQ空间未返回成功回执')
    data=raw.get('data',raw)
    if isinstance(data,dict) and str(data.get('code',0)) not in ('0','None'):
        raise QzoneIntegrationError('原生接口拒绝','QQ空间业务接口返回失败')
    return data


async def query(owner,client,target,pos,num,with_detail):
    data=await call(client,'get_qzone_msg_list',target_uin=int(target),pos=max(0,int(pos)),num=max(1,min(100,int(num))))
    if not isinstance(data,dict) or not isinstance(data.get('msglist'),list):
        raise QzoneIntegrationError('原生读取失败','说说列表格式无效')
    rows=[]
    for original in data['msglist']:
        if not isinstance(original,dict):continue
        if original.get('uin') and str(original['uin'])!=str(target):continue
        row={**original,'uin':str(target),'created_time':original.get('time') or original.get('created_time') or 0,
             'name':original.get('nickname') or original.get('name') or '', '_snowluma_native_list':True}
        if not row.get('pic') and isinstance(row.get('images'),list):
            row['pic']=[{'url1':image} for image in row['images'] if isinstance(image,str)]
        rows.append(row)
    posts=owner._qzone_parse_feeds(rows)
    # The native profile list has no own-like flag. Join a fresh friend feed;
    # absence is unknown, never a fabricated "not liked".
    for post in posts:post.liked=None
    try:
        stream=await call(client,'get_qzone_feeds',page_num=1,count=20)
        by_id={(str(p.uin),p.tid):p for p in parse_recent_feeds(stream)}
        for post in posts:
            seen=by_id.get((str(post.uin),post.tid))
            if seen is not None and getattr(seen,'like_state_known',False):post.liked=bool(getattr(seen,'liked',False))
    except QzoneIntegrationError:pass
    if with_detail:
        for post in posts[:1]:
            await enrich_detail(owner,client,post)
    return posts


async def enrich_detail(owner,client,post):
    detail=await call(client,'get_qzone_msg_detail',target_uin=int(post.uin),tid=post.tid,pos=0,num=30)
    if not isinstance(detail,dict) or str(detail.get('tid'))!=post.tid:
        raise QzoneIntegrationError('原生读取失败','说说详情与目标不一致')
    post.raw.update(detail);post.raw['_snowluma_detail_loaded']=True
    post.comments=owner._qzone_parse_comments_from_msg({'commentlist':detail.get('comments',[])})


async def enrich_selected(owner,event,post):
    raw=getattr(post,'raw',{})
    if not isinstance(raw,dict) or not raw.get('_snowluma_native_list') or raw.get('_snowluma_detail_loaded'):return
    client=bound_client(owner,event)
    if client is not None:await enrich_detail(owner,client,post)


async def verify_like(client,uin,tid):
    for delay in (0,.6,1.2):
        if delay:await asyncio.sleep(delay)
        try:
            data=await call(client,'get_qzone_feeds',page_num=1,count=20)
            post=next((p for p in parse_recent_feeds(data) if str(p.uin)==str(uin) and p.tid==tid),None)
            if post is not None and getattr(post,'liked',False):
                return {'verified':True,'liked':True,'message':'已从当前QQ账号好友流反查点赞状态'}
        except QzoneIntegrationError:pass
    return {'verified':False,'liked':None,'message':'QQ已受理点赞，但好友流尚未核实；不自动重复提交'}


async def like(owner,client,post):
    uin=str(getattr(post,'uin',''));tid=str(getattr(post,'tid',''))
    if not uin.isdigit() or not tid:raise ValueError('说说归属或ID无效')
    if str(getattr(post,'appid','311') or '311')!='311':
        raise QzoneIntegrationError('不支持','当前原生点赞接口仅支持说说')
    if getattr(post,'liked',False) or getattr(post,'already_liked',False):
        return {'success':True,'verified':True,'liked':True,'already_liked':True,'transport':'snowluma_native'}
    stamp=max(0,int(getattr(post,'abstime',0) or getattr(post,'create_time',0) or 0))
    await call(client,'like_qzone',target_uin=int(uin),tid=tid,abstime=stamp)
    verification=await verify_like(client,uin,tid)
    if verification['verified']:post.liked=True
    return {'success':True,'transport':'snowluma_native','verify_message':verification.get('message',''),**verification}
