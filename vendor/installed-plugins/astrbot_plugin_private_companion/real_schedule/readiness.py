"""Read-only execution conditions. Waiting never increments execution attempts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
from pathlib import Path
import time


@dataclass
class Condition:
    kind: str = 'ready'  # ready, wait, skip
    reason: str = ''
    next_at: float = 0
    data: dict = field(default_factory=dict)


def unread_skills(host, runtime):
    try:
        from astrbot.core.skills.skill_manager import SkillManager
        items = SkillManager().list_skills(active_only=True)
    except (ImportError, AttributeError, OSError, TypeError):
        return []
    result = []
    for item in items[:60]:
        path = Path(item.path)
        if path.is_dir(): path /= 'SKILL.md'
        if not path.is_file() or path.suffix.lower() != '.md': continue
        body = path.read_text(encoding='utf-8', errors='replace')[:16000]
        if not body.strip(): continue
        key = 'skill-document:' + hashlib.sha256((item.name + body).encode()).hexdigest()
        if not runtime.ledger.has_receipt(runtime.scope(),key):
            result.append({'name': item.name, 'body': body, 'source_key': key})
    return result


def review_day(host):
    from .catalog import setting
    now = host._environment_fromtimestamp(time.time())
    clock = str(setting(host, 'daily_review_time', '04:00') or '04:00')
    try: hour = int(clock.split(':')[0])
    except (TypeError, ValueError): hour = 4
    # A pre-noon closing review covers the preceding complete calendar day.
    return (now - timedelta(days=1) if hour < 12 else now).strftime('%Y-%m-%d')


def check(host, runtime, cap, row=None, *, now=None):
    from .catalog import setting
    now = time.time() if now is None else now
    day = runtime.day()
    if cap.id == 'skill.study':
        choices = unread_skills(host, runtime)
        return Condition(data={'skills': choices}) if choices else Condition('skip', '没有尚未阅读或发生更新的Skill说明')
    if cap.id == 'roleplay.state' and host.data.get('state_generated_day') == day:
        return Condition('skip', '今日角色状态已生成')
    if cap.id == 'news' and not setting(host,'enable_news_boredom_read',True):
        if not setting(host,'enable_news_daily_hot_read',True) or host.data.get('news_integration',{}).get('last_daily_read_day') == day:
            return Condition('skip', '每日新闻已处理且空闲阅读未开启')
    if cap.id == 'dream':
        if host.data.get('daily_dream', {}).get('date') == day:
            return Condition('skip', '今天已经生成梦境创作')
    if cap.id == 'diary':
        if host.data.get('diary_generated_day') == day or host._daily_diary_was_manually_deleted(day):
            return Condition('skip', '今日日记已生成或已被手动删除')
    if cap.id == 'daily.review' and host.data.get('daily_review_completed_day') == review_day(host):
        return Condition('skip', '目标日期的互动回顾已完成')
    if cap.id == 'ai.daily' and callable(getattr(host, '_ai_daily_source_items', None)):
        sources = host._ai_daily_source_items()
        if not sources:
            return Condition('wait', '没有配置可读取的AI日报来源', now + 6 * 3600)
        source_states = host.data.get('news_integration', {}).get('ai_daily', {}).get('source_states', {})
        getter = getattr(host, '_ai_daily_state', None)
        if callable(getter): source_states = getter().get('source_states', {})
        dt = host._environment_fromtimestamp(now)
        available = []
        times = []
        for source in sources:
            key = source.get('key') or host._ai_daily_source_key(source)
            state = source_states.get(key, {})
            from ..ai_daily_timing import source_ready_at
            at = source_ready_at(host, source, state, day, dt)
            if at is None: continue
            if at <= now: available.append(source)
            else: times.append(at)
        if available: return Condition()
        if times: return Condition('wait', '等待日报发布时间或失败后的检查间隔', min(times))
        return Condition('skip', '今天的日报来源均已处理；不重复请求同一日内容')
    if cap.id == 'qzone.publish':
        state = host.data.get('qzone_integration', {})
        plan = state.get('life_publish_daily_plan') or {}
        limit = max(1, int(setting(host, 'qzone_life_publish_max_daily', 1) or 1))
        if plan.get('date') == day and int(plan.get('published_count') or 0) >= limit:
            return Condition('skip', '今日空间发布额度已使用')
        last = float(state.get('last_life_publish_at') or 0)
        if last:
            same_day = host._environment_fromtimestamp(last).strftime('%Y-%m-%d') == day
            getter = getattr(host, '_qzone_intra_day_gap_seconds' if same_day else '_qzone_cross_day_gap_seconds', None)
            gap = float(getter()) if callable(getter) else float(setting(host, 'qzone_life_publish_min_interval_hours', 24)) * 3600
            if now < last + gap: return Condition('wait', '等待空间发布最小间隔', last + gap)
        night = getattr(host, '_qzone_slot_is_night', None)
        permitted = getattr(host, '_qzone_night_publish_allowed', None)
        if callable(night) and night(now) and not (callable(permitted) and permitted()):
            at = now + 300
            for _ in range(288):
                if not night(at): break
                at += 300
            return Condition('wait', '当前不允许夜间发布，等待可用时段', at)
        failed_at = float(state.get('last_life_publish_failed_at') or 0)
        if failed_at and now < failed_at + 900:
            return Condition('wait', '空间发布失败后的冷却尚未结束', failed_at + 900)
        until = float(state.get('auth_block_until') or 0)
        if until > now: return Condition('wait', '空间登录状态正在等待恢复', until)
    native = {'search': ('web_exploration', 'last_explore_at'),
              'news': ('news_integration', 'last_read_at'),
              'video': ('bilibili_integration', 'last_boredom_watch_at')}.get(cap.id)
    if native:
        last = float(host.data.get(native[0], {}).get(native[1]) or 0)
        if last and now < last + cap.interval:
            return Condition('wait', '等待活动最小间隔', last + cap.interval)
    if cap.id == 'qzone.browse' and callable(getattr(host,'_qzone_watch_store',None)):
        plan=host._qzone_watch_store().browse_plan(host._qzone_watch_binding()[0])
        last=float(plan.get('last_fetch_at') or 0)
        if last and now < last+cap.interval:
            return Condition('wait','等待好友动态浏览最小间隔',last+cap.interval)
    if cap.id == 'search':
        last = float(host.data.get('web_exploration', {}).get('last_probe_at') or 0)
        if last and now < last + 2700:
            return Condition('wait', '搜索探测冷却尚未结束', last + 2700)
    return Condition()
