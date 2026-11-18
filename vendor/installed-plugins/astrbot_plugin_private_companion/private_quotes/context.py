"""Private quote provenance and per-message associations through debounce."""
import asyncio
from copy import deepcopy
import json
import time

from ..persona_config import runtime_persona_setting
from ..logging_util import get_module_logger
from ..helpers import _redact_outbound_secrets
from .store import key,store

logger=get_module_logger(__name__)


def eligible(host,event):
    try:
        if not getattr(host,'enabled',False) or not event.is_private_chat():return False
        if not event.get_self_id() or not event.get_sender_id():return False
        if getattr(host,'_is_onebot_poke_notice_event',lambda e:False)(event):return False
        if getattr(event,'private_companion_proactive_framework',False) or getattr(event,'_private_companion_external_proactive_source',''):return False
        if not getattr(host,'_bot_scope_allows_event',lambda e:True)(event):return False
        umo=str(event.unified_msg_origin)
        if ':FriendMessage:' not in umo or umo.rsplit(':',1)[-1]!=str(event.get_sender_id()):return False
        profile_check=getattr(host,'_private_passive_profile_available',None)
        if callable(profile_check):
            uid=str(event.get_sender_id())
            uid=getattr(host,'_canonical_private_user_id',lambda value:value)(uid)
            profile=getattr(host,'data',{}).get('users',{}).get(uid)
            if not profile_check(uid,profile):return False
        return True
    except (AttributeError,TypeError,ValueError):return False


async def binding_for(host,event):
    if not eligible(host,event):return None
    umo=str(event.unified_msg_origin);pid=str(host._effective_plugin_persona_id() or '')
    if not pid:return None
    manager=host.context.conversation_manager
    cid=await manager.get_curr_conversation_id(umo)
    if not cid:return None
    conversation=await manager.get_conversation(umo,cid)
    if conversation is None or str(getattr(conversation,'user_id',''))!=umo:return None
    selected=str(getattr(conversation,'persona_id','') or '')
    if selected and selected not in ('default','[default]',pid):return None
    return {'platform':umo.split(':',1)[0],'bot':str(event.get_self_id()),'persona':pid,
        'umo':umo,'cid':str(cid),'user':str(event.get_sender_id())}


def summary(host,value,limit=500):
    return _redact_outbound_secrets(str(value or ''),owner=host).replace('\x00','')[:limit]


async def capture(host,event):
    """Read the current event only. Never search other QQ chats for an anchor."""
    if not eligible(host,event):return None
    binding=await binding_for(host,event)
    raw=getattr(host,'_event_raw_payload',lambda e:{})(event) or {}
    if raw.get('post_type')=='notice':
        if binding and raw.get('notice_type')=='friend_recall' and raw.get('message_id'):
            await asyncio.to_thread(store(host).withdraw,binding,str(raw['message_id']))
        return None
    # The first event can precede AstrBot's conversation creation. Its quote
    # associations are still retained; the request hook will bind it later.
    scope=key(binding) if binding else ''
    prior=getattr(event,'_private_quote_entry',None)
    if isinstance(prior,dict):
        if prior.get('scope')==scope:return prior
        if prior.get('scope'):return None  # never rebind a queued old event after /new/persona change
    quotes=[];has_quote=bool(getattr(host,'_event_has_reply_component',lambda e:False)(event))
    if has_quote:
        getter=getattr(host,'_reply_message_chain_for_event',None)
        if callable(getter):
            try:
                chain=await asyncio.wait_for(getter(event,max_depth=3),4)
                for row in chain[:3]:
                    quotes.append({'mid':str(row.get('message_id') or ''),'depth':int(row.get('depth') or 1),
                        'actor':str(row.get('sender_id') or ''),'role':str(row.get('author_role') or 'unknown'),
                        'text':summary(host,row.get('text') or '[无可读文字]',500)})
            except asyncio.CancelledError:raise
            except Exception as exc:logger.debug('私聊引用读取暂不可用: %s',type(exc).__name__)
    entry={'scope':scope,'mid':str(getattr(host,'_event_message_id',lambda e:'')(event) or ''),
        'text':summary(host,getattr(event,'message_str',''),1800),'ts':time.time(),'quotes':quotes,
        'quote_unavailable':has_quote and not quotes,
        'owner':json.dumps([str(event.unified_msg_origin).split(':',1)[0],str(event.get_self_id()),
            str(host._effective_plugin_persona_id() or ''),str(event.unified_msg_origin)],ensure_ascii=False)}
    event._private_quote_entry=entry
    if binding and entry['mid'] and runtime_persona_setting(host,'enable_private_quote_reply',True):
        actor=binding['bot'] if raw.get('post_type')=='message_sent' else binding['user']
        await asyncio.to_thread(store(host).save,binding,entry['mid'],actor,entry['text'])
        for quote in quotes:
            if quote['depth']==1 and quote['mid'] and quote['actor'] in (binding['bot'],binding['user']):
                await asyncio.to_thread(store(host).save,binding,quote['mid'],quote['actor'],quote['text'],kind='quoted')
    return entry


def enrich(event,item):
    entry=getattr(event,'_private_quote_entry',None)
    if isinstance(entry,dict):
        item={**item,'quote_scope':entry.get('scope',''),'message_id':entry.get('mid',''),
            'quote_owner':entry.get('owner',''),'quotes':deepcopy(entry.get('quotes',[])),
            'quote_unavailable':entry.get('quote_unavailable',False)}
    return item


def same_message(left,right):
    if left.get('message_id') and right.get('message_id'):
        return left['message_id']==right['message_id'] and left.get('quote_scope')==right.get('quote_scope')
    return left.get('text')==right.get('text') and left.get('quotes',[])==right.get('quotes',[])


def render_entry(entry):
    text=str(entry.get('text') or '')
    if entry.get('quote_unavailable'):
        text+='\n<Quoted Message>\n【本条直接引用；原消息不可见，不猜测原文】\n</Quoted Message>'
    for quote in entry.get('quotes',[])[:3]:
        label='直接引用' if quote.get('depth',1)==1 else '嵌套背景引用'
        author={'bot_self':'Bot自己','current_user':'当前用户','other':'其他人','unknown':'作者未知'}.get(quote.get('role'),'作者未知')
        # Escape structural delimiters, while preserving the quoted wording.
        body=str(quote.get('text') or '').replace('<','‹').replace('>','›')
        text+=f'\n<Quoted Message>\n【本条{label}；原作者：{author}】{body}\n</Quoted Message>'
    return text


def consume_entries(event,entries):
    current=getattr(event,'_private_quote_entry',{})
    scope=current.get('scope') if isinstance(current,dict) else ''
    clean=[]
    for item in entries[:8]:
        if not isinstance(item,dict):continue
        if item.get('quote_owner') and item['quote_owner']!=current.get('owner'):continue
        if item.get('quote_scope') and item['quote_scope']!=scope:continue
        # An unbound first-message snapshot may carry quotes, but must not
        # silently inherit a different later conversation after a reset.
        if (item.get('quotes') and not item.get('quote_scope') and item.get('message_id')!=current.get('mid')
                and (not item.get('quote_owner') or time.time()-float(item.get('ts') or 0)>30)):continue
        clean.append(deepcopy(item))
    event._private_quote_merged_entries=clean
    return clean


def entries_for(event):
    rows=getattr(event,'_private_quote_merged_entries',None)
    if isinstance(rows,list) and rows:return rows
    one=getattr(event,'_private_quote_entry',None)
    return [enrich(event,{'text':one.get('text',''),'ts':one.get('ts',0),'sender_name':''})] if isinstance(one,dict) else []
