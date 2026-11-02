"""Function awareness: observations do not grant permission or execute actions."""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import time
from types import SimpleNamespace

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import request
from astrbot.core.agent.message import TextPart

from .storage import Records
from .integrations import companion_scope, known_people, plugin_instance, setting

ID='astrbot_plugin_function_awareness'
PREFIX='/'+ID
MARKER='[功能自知：当前会话运行信息]'
OWN_TOOLS={'get_function_awareness','get_function_actions','get_function_contacts'}
SAFE_NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_.:-]{0,100}$')
LABELS={'available':'本轮有工具入口（执行结果仍须核实）','background_only':'后台自动能力，聊天不能直接调用',
        'disabled':'已关闭','unavailable':'暂不可用','unverified':'未验证/本轮未提供工具','cooldown':'冷却中',
        'unsupported':'当前平台不支持'}


def compact(value,limit=100):
    return re.sub(r'[\x00-\x1f\x7f]+',' ',str(value or '')).strip()[:limit]


def safe_reason(error):
    text=str(error).lower()
    if any(x in text for x in ('login','登录','cookie','skey')):return '空间登录态未被服务端接受'
    if any(x in text for x in ('timeout','超时','timed out')):return '网络请求超时'
    if any(x in text for x in ('connect','网络','ssl')):return '网络连接失败'
    if any(x in text for x in ('permission','权限','denied','forbidden')):return '当前账号或会话无权限'
    return '调用未成功；需要实际重试确认'


@register(ID,'yingjxu','功能自知：当前会话的工具、后台功能、运行状态和只读联系人查询。','0.1.0')
class FunctionAwareness(Star):
    def __init__(self,context:Context,config=None):
        super().__init__(context)
        self.context=context
        self.config=config if config is not None else {}
        directory=StarTools.get_data_dir(ID)
        directory.mkdir(parents=True,exist_ok=True)
        directory.chmod(0o700)
        self.records=Records(directory/'awareness.db')
        context.register_web_api(PREFIX+'/status',self.api_status,['GET'],'Function awareness scoped status')
        context.register_web_api(PREFIX+'/probe',self.api_probe,['POST'],'Read-only QQ friend/Qzone probe')

    def enabled(self):return bool(self.config.get('enabled',True))

    def scope(self,event,req=None):
        umo=str(event.unified_msg_origin)
        cfg=self.context.get_config(umo=umo)
        persona=compact(getattr(getattr(req,'conversation',None),'persona_id',''),96)
        if not persona or persona in ('[default]','default'):
            persona=compact(cfg.get('agent_runner',{}).get('config',{}).get('persona',{}).get('persona_id',''),96)
        bot=compact(event.get_self_id(),96)
        platform=umo.split(':',1)[0]
        group=str(event.get_group_id() or '')
        user=str(event.get_sender_id() or '')
        admins=self.context.get_config().get('admins_id',[])
        if isinstance(admins,str):admins=[admins]
        result={'platform_id':platform,'bot_id':bot,'persona_id':persona,'umo':umo,'user_id':user,
                'private':not bool(group),'admin_private':not bool(group) and user in [str(x) for x in admins]}
        result['key']=json.dumps([platform,bot,persona],ensure_ascii=False,separators=(',',':'))
        return result,cfg

    def tool_names(self,req):
        tools=getattr(getattr(req,'func_tool',None),'tools',[]) or []
        return sorted({str(t.name) for t in tools if getattr(t,'active',True) and SAFE_NAME.fullmatch(str(getattr(t,'name','')))})

    def client(self,scope):
        platform=self.context.get_platform_inst(scope['platform_id'])
        if platform is None:return None
        getter=getattr(platform,'get_client',None)
        return getter() if callable(getter) else None

    @filter.on_llm_request(priority=-310000)
    async def remember_request(self,event:AstrMessageEvent,req:ProviderRequest):
        event.set_extra('function_awareness_request',req)
        scoped,_=self.scope(event,req)
        # Hide our own tools rather than changing any other plugin's permissions.
        remove=set()
        if not self.enabled():remove=OWN_TOOLS
        else:
            if not scoped['admin_private']:
                remove.update({'get_function_actions','get_function_contacts'})
            if not self.config.get('enable_contacts_query',True):
                remove.add('get_function_contacts')
            if not self.config.get('include_recent_actions',True):
                remove.add('get_function_actions')
        tools=getattr(req,'func_tool',None)
        if tools and remove:
            from copy import copy
            clone=copy(tools)
            clone.tools=[t for t in tools.tools if t.name not in remove]
            req.func_tool=clone

    @filter.on_agent_begin(priority=-310000)
    async def inject_final_snapshot(self,event:AstrMessageEvent,run_context,*args,**kwargs):
        if not self.enabled():return
        req=event.get_extra('function_awareness_request') or event.get_extra('provider_request')
        if req is None:return
        try:
            snapshot=self.build_snapshot(event,req)
            scoped,_=self.scope(event,req)
            await asyncio.to_thread(self.records.snapshot,scoped['key'],scoped['umo'],snapshot)
            if not self.config.get('inject_summary',True):return
            body=self.prompt(snapshot)
            messages=getattr(run_context,'messages',None)
            if not isinstance(messages,list):return
            # Attach to the existing system message, not a new mid-conversation system turn.
            system=next((m for m in messages if getattr(m,'role','')=='system'),None)
            if system is None:
                from astrbot.core.agent.message import SystemMessageSegment
                system=SystemMessageSegment(content=[])
                system._no_save=True
                messages.insert(0,system)
            if isinstance(system.content,str):system.content=[TextPart(text=system.content)]
            if not isinstance(system.content,list):return
            system.content=[p for p in system.content if not (isinstance(p,TextPart) and p.text.startswith(MARKER))]
            system.content.append(TextPart(text=body).mark_as_temp())
        except Exception as exc:
            logger.warning('[功能自知] 能力摘要暂不可用: %s',type(exc).__name__)

    def build_snapshot(self,event,req=None):
        scoped,cfg=self.scope(event,req)
        names=self.tool_names(req)
        items=[]
        def add(key,title,status,reason='',tools=()):
            items.append({'id':key,'title':title,'status':status,'reason':reason,'tools':[n for n in tools if n in names]})
        web=[n for n in names if n.startswith(('web_search_','anysearch_'))]
        add('search','联网搜索','available' if web else 'unverified','使用当轮搜索工具，不能把未执行的搜索说成已完成',web)
        mem=[n for n in names if n.startswith('memory_companion_')]
        add('memory','长期记忆查询','available' if mem else 'unverified','遵守记忆工具自身的身份和范围限制',mem)
        for key,title,tools in [('meme','表情包检索',['search_memes']),('reminder','提醒/定时任务',['future_task','pc_manage_memo']),
                                ('image','生图/发送图片',['pc_generate_photo','pc_send_current_media'])]:
            add(key,title,'available' if any(n in names for n in tools) else 'unverified','有入口不代表后端或素材一定可用',tools)
        if scoped['admin_private'] and self.config.get('enable_contacts_query',True):
            add('friends','QQ好友名单','available' if 'get_function_contacts' in names else 'unverified','只读、管理员私聊；好友关系不等于曾经交谈',['get_function_contacts'])
            add('people','已认识/已建档人物名单','available' if 'get_function_contacts' in names else 'unverified','名单可能不完整，来源需区分，不能把名单当作完整聊天记忆',['get_function_contacts'])
        with companion_scope(self.context,scoped['persona_id']) as cp:
            if cp is not None:
                self._companion_snapshot(cp,scoped,names,add)
        result={'scope':{k:v for k,v in scoped.items() if k not in ('key','admin_private')},'as_of':time.time(),
                'tools_verified_for_request':req is not None,'tools':names,'capabilities':items}
        if scoped['admin_private'] and self.config.get('include_recent_actions',True):
            result['recent_actions']=self.records.recent(scoped['key'],scoped['umo'],5)
        return result

    def _companion_snapshot(self,cp,scoped,names,add):
        data=cp.data
        user=data.get('users',{}).get(scoped['user_id'],{})
        if user.get('umo') and user['umo'] != scoped['umo']:user={}
        for key,title,flag in [('autonomous_search','自主探索/搜索','enable_web_exploration'),('news','自主阅读新闻','enable_news_integration')]:
            active=bool(setting(cp,flag,False))
            add(key,title,'background_only' if active else 'disabled','由后台时机、间隔和概率控制；没有分享也可能已经阅读')
        poke_on=bool(setting(cp,'enable_poke_action',False))
        supported=':FriendMessage:' in scoped['umo'] or ':GroupMessage:' in scoped['umo']
        poke_status='disabled' if not poke_on else ('background_only' if supported else 'unsupported')
        reason='可以由陪伴后台执行；当前没有供聊天模型直接调用的戳一戳工具'
        if poke_on and supported and user:
            limit=cp._effective_user_poke_daily_limit(user)
            if limit<=0:poke_status,reason='disabled','当前用户的主动戳一戳权限或额度为0'
            elif cp._poke_action_cooldown_remaining(user)>0:poke_status,reason='cooldown','当前处于戳一戳冷却期'
        add('poke','主动戳一戳',poke_status,reason)
        proactive=bool(setting(cp,'enable_llm_proactive_message',False))
        permitted=bool(user and cp._user_enabled_for_proactive(scoped['user_id'],user))
        add('proactive','主动消息','background_only' if proactive and permitted else 'disabled',
            '遵守用户许可、配额、免打扰、冷却与发送审核' if permitted else '当前对象没有已启用的私聊主动联系档案')
        zone_tools=[n for n in names if n.startswith('pc_qzone_')]
        zone_on=bool(setting(cp,'enable_qzone_integration',False))
        health=self.records.health(scoped['key'],'qzone')
        state=data.get('qzone_integration',{})
        failed=health and health['status']=='unavailable'
        if state.get('last_auth_status') in ('failed','invalid','login_required'):failed=True
        status='disabled' if not zone_on else ('unsupported' if not (':FriendMessage:' in scoped['umo'] or ':GroupMessage:' in scoped['umo']) else ('unavailable' if failed else ('available' if zone_tools else 'unverified')))
        reason=(health['reason'] if failed and health else '读取、发布、评论都需要有效空间登录态；未探测时不能保证可用')
        if health:reason+='；最近检查时间戳='+str(int(health['checked_at']))
        add('qzone','QQ空间访问',status,reason,zone_tools)
        add('qzone_auto_publish','QQ空间自动发布','background_only' if zone_on and setting(cp,'enable_qzone_life_publish',False) else 'disabled','独立于空间总开关；发布前仍需有效登录态')
        add('qzone_comments','QQ空间评论监听','background_only' if zone_on and setting(cp,'enable_qzone_comment_inbox',False) else 'disabled','独立开关；监听不等于对所有评论自动回复')
        if not scoped['admin_private']:return
        if self.config.get('include_recent_actions',True):
            poke_at=float(user.get('last_poke_action_at') or 0)
            if poke_at>time.time()-7*86400:
                identity=hashlib.sha256((scoped['key']+'poke'+str(poke_at)).encode()).hexdigest()
                self.records.action(scoped['key'],scoped['umo'],identity,'poke',
                                    '陪伴后台主动戳一戳（具体动机未记录）','platform_ack',poke_at)
            for row in data.get('proactive_audit_log',[])[-50:]:
                if row.get('umo') != scoped['umo'] or row.get('status') not in ('sent','success','completed'):continue
                stamp=float(row.get('updated_ts') or row.get('created_ts') or 0)
                if stamp<time.time()-7*86400:continue
                self.records.action(scoped['key'],scoped['umo'],'companion:'+str(row.get('id')),'proactive',
                                    compact(row.get('action') or '主动消息'), 'sent',stamp)
            for state_key,kind,title in [('web_exploration','search','自主搜索'),('news_integration','news','新闻阅读')]:
                state=data.get(state_key,{})
                stamp=float(state.get('last_explore_at') or state.get('last_read_at') or 0)
                if stamp<=0 or stamp<time.time()-7*86400:continue
                if state.get('last_status') not in ('explored','read'):continue
                digest=state.get('last_digest',{})
                label=title+'：'+compact(digest.get('topic') or digest.get('headline') or '已取得内容',70)
                identity=hashlib.sha256((scoped['key']+kind+str(stamp)).encode()).hexdigest()
                self.records.action(scoped['key'],scoped['umo'],identity,kind,label,'completed',stamp)

    def prompt(self,snapshot):
        lines=[MARKER,
            '以下是当前Bot/人格/会话的运行信息，作为人设之外的能力事实；不要因人设没有写某功能就否认它。',
            '区分聊天工具、后台自动动作和实际执行结果。后台能力可用不表示你能当场调用；工具入口存在也不保证服务登录、网络或调用结果成功。',
            '状态只是此刻观察，未知或暂不可用时如实说明；没有动作记录不要捏造此前行为或其动机。不要照搬技术清单到普通闲聊。']
        for item in snapshot['capabilities']:
            tools=('；入口='+','.join(item['tools'])) if item['tools'] else ''
            lines.append(item['title']+'：'+LABELS[item['status']]+'；'+item['reason']+tools)
        if snapshot.get('recent_actions'):
            lines.append('已记录的近期动作：'+json.dumps(snapshot['recent_actions'],ensure_ascii=False,separators=(',',':')))
        lines.append('需要详细能力状态用 get_function_awareness。')
        if 'get_function_actions' in snapshot['tools']:
            lines.append('回看已记录的实际动作用 get_function_actions。')
        lines.append('本轮实际工具名称：'+','.join(snapshot['tools'][:70]))
        return '\n'.join(lines)[:max(1000,min(8000,int(self.config.get('max_prompt_chars',2800))))]

    def current_request(self,event):
        return event.get_extra('function_awareness_request') or event.get_extra('provider_request')

    @filter.llm_tool(name='get_function_awareness')
    async def awareness_tool(self,event:AstrMessageEvent,query:str=''):
        """只读查询自己当前会话的真实工具、后台功能、关闭原因和最近健康状态，不执行任何功能。

        Args:
            query(string): 可选功能关键词，如戳一戳、新闻、空间、搜索、记忆；留空查看全部。
        """
        if not self.enabled():return json.dumps({'ok':False,'reason':'功能自知已关闭'},ensure_ascii=False)
        req=self.current_request(event)
        if req is None:return json.dumps({'ok':False,'reason':'没有当前请求，不能推断工具权限'},ensure_ascii=False)
        result=self.build_snapshot(event,req)
        if query:
            query=compact(query,80)
            result['capabilities']=[x for x in result['capabilities'] if query.lower() in json.dumps(x,ensure_ascii=False).lower()]
        return json.dumps(result,ensure_ascii=False)

    @filter.llm_tool(name='get_function_actions')
    async def actions_tool(self,event:AstrMessageEvent,limit:int=8):
        """只读查询当前Bot/人格与你这一私聊已记录的实际后台动作和工具结果；没有记录时不能猜动机。

        Args:
            limit(number): 最近记录数量，1至30，默认8。
        """
        if not self.enabled():return '{"ok":false,"reason":"disabled"}'
        scoped,_=self.scope(event,self.current_request(event))
        if not scoped['admin_private']:return '{"ok":false,"reason":"仅管理员私聊可读取后台动作"}'
        self.build_snapshot(event,self.current_request(event))
        items=await asyncio.to_thread(self.records.recent,scoped['key'],scoped['umo'],limit)
        return json.dumps({'items':items,'note':'只代表已被观察并记录的动作；未记录不等于从未发生。没有记录动机时不得自行推断。'},ensure_ascii=False)

    @filter.llm_tool(name='get_function_contacts')
    async def contacts_tool(self,event:AstrMessageEvent,kind:str='known_people',offset:int=0,limit:int=20):
        """管理员私聊只读查询当前QQ好友或当前Bot/人格已建档认识的人，返回名字和来源，不读取聊天正文。

        Args:
            kind(string): friends 表示QQ好友；known_people表示互动或记忆中已建档人物。
            offset(number): 分页起点，默认0。
            limit(number): 每页1至50人，默认20。
        """
        scoped,_=self.scope(event,self.current_request(event))
        if not self.enabled() or not self.config.get('enable_contacts_query',True) or not scoped['admin_private']:
            return '{"ok":false,"reason":"此名单查询仅限已授权管理员私聊"}'
        if kind not in ('friends','known_people'):return '{"ok":false,"reason":"kind需为friends或known_people"}'
        try:
            if kind=='friends':
                client=self.client(scoped)
                if client is None:return '{"ok":false,"reason":"没有对应QQ连接"}'
                raw=await asyncio.wait_for(client.get_friend_list(),10)
                items=raw.get('data',[]) if isinstance(raw,dict) else raw
                if not isinstance(items,list):raise ValueError('invalid friend response')
                result={'items':[{'user_id':str(x.get('user_id','')),'name':compact(x.get('nickname'),80),'remark':compact(x.get('remark'),80)} for x in items],
                        'note':'QQ好友名单，不代表每位好友都聊过天或已有记忆。','complete':True}
            else:
                with companion_scope(self.context,scoped['persona_id']) as cp:
                    result=known_people(self.context,scoped,cp)
            offset=max(0,int(offset));limit=min(50,max(1,int(limit)))
            items=result.pop('items');result.update(ok=True,kind=kind,total=len(items),offset=offset,items=items[offset:offset+limit],next_offset=offset+limit if offset+limit<len(items) else None)
            return json.dumps(result,ensure_ascii=False)
        except Exception as exc:
            logger.warning('[功能自知] 名单查询失败: %s',type(exc).__name__)
            return json.dumps({'ok':False,'reason':safe_reason(exc)},ensure_ascii=False)

    @filter.on_llm_tool_respond(priority=-310000)
    async def record_tool_result(self,event,tool,tool_args,tool_result,*args,**kwargs):
        if not self.enabled() or not self.config.get('include_recent_actions',True):return
        name=str(getattr(tool,'name',''))
        if name in OWN_TOOLS or not SAFE_NAME.fullmatch(name):return
        scoped,_=self.scope(event,self.current_request(event))
        if not scoped['admin_private']:return
        failed=bool(getattr(tool_result,'isError',False))
        text=''
        for block in getattr(tool_result,'content',[]) or []:
            if getattr(block,'type',None)=='text':text+=str(getattr(block,'text',''))[:2000]
        status='failed' if failed else 'returned_unverified'
        try:
            payload=json.loads(text)
            if isinstance(payload,dict):
                if payload.get('ok') is False or payload.get('status') in ('error','failed','unavailable'):status='failed'
                elif payload.get('ok') is True or payload.get('status') in ('ok','success','sent','completed'):status='success'
        except (ValueError,TypeError):pass
        if name.startswith('pc_qzone_'):
            if status=='failed':self.records.health(scoped['key'],'qzone','unavailable',safe_reason(text))
            elif status=='success':self.records.health(scoped['key'],'qzone','success','最近一次实际调用成功，后续仍需核实')
        await asyncio.to_thread(self.records.action,scoped['key'],scoped['umo'],hashlib.sha256((scoped['key']+name+str(time.time_ns())).encode()).hexdigest(),'tool',name,status)

    async def diagnostic_event(self,umo):
        parts=umo.split(':')
        if len(parts)!=3 or parts[1]!='FriendMessage':raise ValueError('只支持明确的管理员私聊UMO')
        cfg=self.context.get_config(umo=umo)
        admins=self.context.get_config().get('admins_id',[])
        if parts[2] not in [str(x) for x in admins]:raise ValueError('必须是当前管理员会话')
        platform=self.context.get_platform_inst(parts[0])
        client=platform.get_client() if platform and callable(getattr(platform,'get_client',None)) else None
        if client is None:raise ValueError('QQ连接不存在')
        login=await asyncio.wait_for(client.get_login_info(),10)
        if isinstance(login,dict) and isinstance(login.get('data'),dict):login=login['data']
        bot=str(login.get('user_id',''))
        extras={}
        event=SimpleNamespace(unified_msg_origin=umo,bot=client,message_str='',
            get_self_id=lambda:bot,get_group_id=lambda:'',get_sender_id=lambda:parts[2],
            get_platform_id=lambda:parts[0],get_platform_name=lambda:'aiocqhttp',
            is_private_chat=lambda:True,get_extra=lambda k:extras.get(k),set_extra=lambda k,v:extras.__setitem__(k,v))
        return event

    async def api_status(self):
        umo=str(request.query.get('umo',''))
        event=await self.diagnostic_event(umo)
        scoped,_=self.scope(event)
        saved=await asyncio.to_thread(self.records.snapshot,scoped['key'],umo)
        current=self.build_snapshot(event)
        return {'status':'ok','data':{'enabled':self.enabled(),'current_background':current,'last_real_request':saved,
                                     'note':'未出现真实聊天请求前，不能把全局已注册工具当作本轮可调用工具。'}}

    async def api_probe(self):
        if not self.enabled():return {'status':'error','message':'功能自知已关闭'}
        payload=await request.json(default={}) or {}
        event=await self.diagnostic_event(str(payload.get('umo','')))
        scoped,_=self.scope(event)
        kind=payload.get('kind')
        if kind=='friends':
            value=json.loads(await self.contacts_tool(event,kind='friends',limit=1))
            return {'status':'ok','data':{'ok':value.get('ok'), 'friend_count':value.get('total'),'reason':value.get('reason')}}
        if kind=='known_people':
            value=json.loads(await self.contacts_tool(event,kind='known_people',limit=50))
            return {'status':'ok','data':value}
        if kind!='qzone':return {'status':'error','message':'仅支持friends、known_people或qzone只读探测'}
        with companion_scope(self.context,scoped['persona_id']) as cp:
            if cp is None or not setting(cp,'enable_qzone_integration',False):
                return {'status':'ok','data':{'ok':False,'reason':'空间整合未启用或人格不匹配'}}
            try:
                posts=await asyncio.wait_for(cp._qzone_query_feeds(event,target_id=scoped['bot_id'],pos=0,num=1,with_detail=False),30)
                self.records.health(scoped['key'],'qzone','success','空间读取请求成功；写入操作未测试')
                return {'status':'ok','data':{'ok':True,'post_count':len(posts),'writes_tested':False}}
            except Exception as exc:
                reason=safe_reason(exc)
                self.records.health(scoped['key'],'qzone','unavailable',reason)
                return {'status':'ok','data':{'ok':False,'reason':reason,'writes_tested':False}}
