"""Activity adapters and a live inventory. Installed names never grant execution rights."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from contextvars import ContextVar
import inspect
import time


@dataclass
class Capability:
    id: str
    title: str
    flags: tuple[str, ...]
    methods: tuple[str, ...]
    effect: str = 'read'
    interval: int = 3600
    interval_setting: str = ''
    interval_unit: int = 60
    skills: tuple[str, ...] = ()
    owner: str = 'astrbot_plugin_private_companion'
    enabled: bool = False
    available: bool = False
    reason: str = ''
    reference: str = ''
    autonomous: bool = True
    metadata: dict = field(default_factory=dict)

    def public(self):
        return asdict(self)


BUILTINS = (
    Capability('news', '阅读新闻', ('enable_news_integration',),
        ('_perform_news_reading','_ensure_daily_news_reading','_maybe_trigger_news_boredom_read'),
        interval=21600,interval_setting='news_min_interval_hours',interval_unit=3600,skills=('资料阅读',)),
    Capability('search', '按兴趣联网探索', ('enable_web_exploration','enable_web_exploration_boredom_search'),
        ('_maybe_trigger_web_exploration',),interval=28800,interval_setting='web_exploration_min_interval_hours',interval_unit=3600,skills=('资料阅读','电脑操作')),
    Capability('qzone.browse', '浏览好友动态', ('enable_qzone_integration','enable_qzone_browse'),
        ('_poll_qzone_browse',),interval=7200,interval_setting='qzone_browse_interval_minutes',skills=('生活观察',)),
    Capability('ai.daily', '阅读AI日报', ('enable_news_integration','enable_ai_daily_watch'),
        ('_maybe_track_ai_daily',),interval=1800,interval_setting='ai_daily_check_interval_minutes',skills=('资料阅读',)),
    Capability('video', '阅读视频内容', ('enable_bilibili_integration','enable_bilibili_boredom_watch'),
        ('_maybe_trigger_bilibili_boredom_watch',),interval=28800,interval_setting='bilibili_boredom_min_interval_hours',interval_unit=3600,skills=('资料阅读',)),
    Capability('reading', '阅读资料柜', ('enable_reading_archive_integration','enable_reading_archive_boredom_read'),
        ('_maybe_trigger_reading_archive_boredom_read',),interval=7200,skills=('资料阅读',)),
    Capability('creative', '推进创作', ('enable_creative_writing',),
        ('_maybe_advance_creative_projects','_maybe_start_creative_project'),effect='local_write',interval=7200,skills=('文本创作',)),
    Capability('diary', '整理今日日记', ('enable_daily_diary',),
        ('_ensure_daily_diary',),effect='local_write',interval=86400,skills=('文本创作',)),
    Capability('growth', '整理记忆和相处偏好', ('enable_companion_growth',),
        ('_maintain_companion_growth',),effect='local_write',interval=1800,interval_setting='companion_growth_interval_minutes',skills=('聊天表达',)),
    Capability('expression.review', '复审已启用表达', ('enable_companion_growth','enable_expression_rule_reassessment','enable_expression_persona_review'),
        ('_maintain_expression_reassessment',),effect='local_write',interval=604800,interval_setting='expression_reassessment_interval_days',interval_unit=86400,skills=('聊天表达',)),
    Capability('daily.review', '回顾实际互动', ('enable_daily_review',),
        ('_ensure_daily_review',),effect='local_write',interval=86400),
    Capability('outfit', '生成已配置的穿搭图', ('enable_daily_outfit_photo','enable_photo_text_action'),
        ('_ensure_daily_outfit_photo',),effect='local_write',interval=86400,skills=('绘画',)),
    Capability('qzone.publish', '发表生活动态', ('enable_qzone_integration','enable_qzone_life_publish'),
        ('_maybe_publish_qzone_life_post',),effect='social_write',interval=86400),
    Capability('knowledge.read', '阅读已选知识库', ('enable_real_schedule_knowledge_read',),
        ('_format_roleplay_knowledge_context_body',),interval=7200,skills=('资料阅读',)),
    Capability('skill.study', '阅读可用Skill指导', ('enable_real_schedule_skill_study',),(),interval=14400),
    Capability('dream', '创作梦境片段', ('enable_enhanced_dreams',),(),effect='local_write',interval=86400),
    Capability('roleplay.state', '生成角色状态', ('enable_humanized_states',),('_ensure_daily_state',),effect='local_write',interval=86400),
)

METHODS = {name: c.id for c in BUILTINS for name in c.methods}
MAINTENANCE = ('消息接收和被动回复','登录与OAuth刷新','消息和记忆持久化','独立提醒与未来任务',
               'QQ空间关注监听','QQ空间评论收件箱','撤回与隐私控制','配额与冷却','天气缓存和预警')


def setting(host, key, default=None):
    from ..persona_config import runtime_persona_setting
    return runtime_persona_setting(host,key,default)


def probe(host, name):
    value=getattr(host,name,None)
    try:return bool(value()) if callable(value) else False
    except Exception:return False


_DISCOVERING = ContextVar('companion_capability_discovery', default=frozenset())


def live_catalog(host):
    # A cooperating plugin may query coordinator permissions while describing
    # its capabilities. Permission checks must not recursively rediscover the
    # same host. Fail closed for that nested probe; never grant execution from
    # an incomplete inventory. Context-local ownership isolates other hosts.
    owners = _DISCOVERING.get()
    if id(host) in owners:
        return []
    token = _DISCOVERING.set(owners | {id(host)})
    try:
        return _build_live_catalog(host)
    finally:
        _DISCOVERING.reset(token)


def _build_live_catalog(host):
    from copy import deepcopy
    result=[]
    for source in BUILTINS:
        cap=deepcopy(source)
        cap.enabled=all(bool(setting(host,k,False)) for k in cap.flags)
        cap.available=cap.enabled and all(callable(getattr(host,m,None)) for m in cap.methods[:1])
        cap.reason='' if cap.available else '功能开关关闭或执行入口不可用'
        if cap.id=='knowledge.read':
            cap.available=cap.enabled and bool(setting(host,'roleplay_knowledge_source_ids',[]))
            cap.reason='' if cap.available else '当前人格尚未选择可读取的知识库'
        if cap.id=='skill.study':
            try:
                from astrbot.core.skills.skill_manager import SkillManager
                cap.available=cap.enabled and bool(SkillManager().list_skills(active_only=True))
            except (ImportError,TypeError,AttributeError,OSError):cap.available=False
            cap.reason='' if cap.available else '没有启用的可读Skill'
        if cap.interval_setting:
            cap.interval=max(60,int(float(setting(host,cap.interval_setting,cap.interval/cap.interval_unit) or 1)*cap.interval_unit))
        if cap.id=='video' and cap.enabled:
            cap.available=probe(host,'_bilibili_available')
            cap.reason='' if cap.available else '视频插件或登录后端未就绪'
        if cap.id=='reading' and cap.enabled:
            cap.available=probe(host,'_reading_archive_available')
            cap.reason='' if cap.available else '资料柜执行后端未接入'
        if cap.id=='outfit' and cap.enabled:
            # A wardrobe or a text/vision model is not an image generator.
            cap.available=probe(host,'_photo_text_available')
            cap.reason='' if cap.available else '尚未验证可用的生图后端'
        if cap.id.startswith('qzone.') and cap.enabled:
            cap.available=probe(host,'_qzone_available')
            cap.reason='' if cap.available else 'QQ空间后端未就绪'
            browser=getattr(host,'_qzone_browser_session_status',lambda:{})()
            if browser and browser.get('state')!='ready':
                cap.available=False
                cap.reason=str(browser.get('reason') or 'QQ空间浏览器会话暂未就绪')
        if cap.id=='qzone.publish':
            cap.metadata={'reference_daily_probability':setting(host,'qzone_life_publish_probability',0.18),
                'daily_limit':setting(host,'qzone_life_publish_max_daily',1),
                'note':'频率与旧时间表供规划参考；账号授权、最小间隔、每日额度仍为硬约束'}
        if cap.id=='news':
            cap.enabled=cap.enabled and bool(setting(host,'enable_news_daily_hot_read',True) or setting(host,'enable_news_boredom_read',True))
            cap.available=cap.available and cap.enabled
        if cap.id in ('diary','daily.review'):
            cap.reference=str(setting(host,'daily_diary_time' if cap.id=='diary' else 'daily_review_time','23:00'))
        result.append(cap)
    # Explicit extension protocol: metadata alone does not make arbitrary LLM
    # tools safe to execute in an unattended, synthetic-admin context.
    context=getattr(host,'context',None)
    try:
        stars=context.get_all_stars()
    except (AttributeError,RuntimeError):
        stars=[]
    for star in stars:
        if not getattr(star,'activated',True):continue
        plugin=getattr(star,'star_cls',None)
        if plugin is None or inspect.isclass(plugin) or plugin is host:
            continue
        getter=getattr(plugin,'real_schedule_capabilities',None)
        runner=getattr(plugin,'run_real_schedule_activity',None)
        if not callable(getter) or not callable(runner):
            continue
        try:
            scoped_getter = getattr(plugin, 'real_schedule_capabilities_for_scope', None)
            if callable(scoped_getter):
                _, platform_id, bot_id, _ = host._qzone_watch_binding()
                descriptors = scoped_getter({'platform_id': str(platform_id), 'bot_id': str(bot_id),
                    'persona_id': str(host._effective_plugin_persona_id() or '')})
            else:
                descriptors=getter()
            if inspect.isawaitable(descriptors):
                descriptors.close();continue
            for descriptor in descriptors[:30]:
                key=str(descriptor['id'])
                owner=str(getattr(star,'name',''))
                if not owner or not key or not key.replace('_','').replace('.','').isalnum():
                    continue
                flags=tuple(descriptor.get('flags') or ())
                admitted='extension:'+owner+':'+key in (setting(host,'real_schedule_allowed_extensions',[]) or [])
                enabled=admitted and descriptor.get('enabled') is True
                effect=str(descriptor.get('effect','read'))
                if effect not in ('read','local_write','social_write','execution'):
                    continue
                available=descriptor.get('available') is True and enabled
                cap=Capability('extension:'+owner+':'+key,str(descriptor.get('title') or key)[:100],flags,(),
                    effect=effect,interval=max(60,int(descriptor.get('min_interval_seconds',3600))),owner=owner,
                    enabled=enabled,available=available,reason=str(descriptor.get('reason') or ''),
                    skills=tuple(descriptor.get('skills') or ()),metadata={'extension_id':key, 'terminal_activity': owner == 'astrbot_plugin_desktop_pet' and descriptor.get('terminal_activity') is True})
                result.append(cap)
        except Exception:
            # Broken extensions are unavailable; they do not break ordinary QQ chat.
            continue
    return result


def inventory(host, schema):
    """Every canonical feature is accounted for; event-driven features stay so."""
    caps=live_catalog(host)
    flags={key:cap.id for cap in caps for key in cap.flags}
    features=[]
    def visit(items):
        for key,entry in items.items():
            if not isinstance(entry,dict):continue
            if isinstance(entry.get('items'),dict):visit(entry['items']);continue
            if entry.get('type')!='bool':continue
            if key in {r['key'] for r in features}:continue
            if key in flags:role='planner_activity'
            elif any(w in key for w in ('watch','inbox','reminder','memo','token','balance','weather','environment')):role='background_service'
            elif key.startswith('enable_real_schedule'):role='coordination'
            else:role='event_or_configuration'
            features.append({'key':key,'title':entry.get('description',key),'enabled':bool(setting(host,key,entry.get('default',False))),
                             'role':role,'activity_id':flags.get(key,'')})
    visit(schema)
    tools=[]
    try:
        manager=host.context.get_llm_tool_manager()
        for tool in getattr(manager,'func_list',getattr(manager,'tools',[])) or []:
            if not getattr(tool,'active',True):continue
            tools.append({'name':str(getattr(tool,'name','')),'description':str(getattr(tool,'description',''))[:250],
                          'status':'chat_scoped','note':'聊天权限由原工具控制；后台执行须显式活动适配'})
    except (AttributeError,TypeError):pass
    knowledge=[]
    getter=getattr(host,'_astrbot_knowledge_sources',None)
    if callable(getter):
        selected=set(setting(host,'roleplay_knowledge_source_ids',[]) or [])
        try:sources=getter()
        except Exception:sources=[]
        for source in sources:
            if str(source.get('id') or source.get('source_id') or '') not in selected:continue
            knowledge.append({k:source.get(k) for k in ('id','source_id','name','title','kind','type') if k in source})
    skills=[]
    try:
        from astrbot.core.skills.skill_manager import SkillManager
        for item in SkillManager().list_skills(active_only=True):
            skills.append({'name':item.name,'description':item.description[:300],'source':item.source_type,
                           'execution_requires':'已配置的工具和活动适配器'})
    except (ImportError,TypeError,AttributeError,OSError):pass
    return {'capabilities':[c.public() for c in caps], 'features':features,'tools':tools,
            'knowledge_sources':knowledge,'executable_skills':skills,'services':list(MAINTENANCE),
            'updated_at':time.time()}
