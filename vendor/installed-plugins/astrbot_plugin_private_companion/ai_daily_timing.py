"""One successful read per source/day; missing publications may be retried."""
from .persona_config import runtime_persona_setting

RETRYABLE = frozenset({'waiting_today_video','digest_failed','fetch_failed','failed','error','checking','today_video_without_text'})


def source_ready_at(host, source, state, day, now_dt):
    if state.get('last_success_date') == day:
        return None
    schedule = max(0, min(1439, int(source.get('schedule_minutes') or 0)))
    at = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() + schedule * 60
    if state.get('last_attempt_date') == day:
        if state.get('status') not in RETRYABLE:
            return None  # Legacy completed attempts keep their once/day contract.
        minutes = max(5, int(runtime_persona_setting(host,'ai_daily_check_interval_minutes',30) or 30))
        at = max(at, float(state.get('last_checked_at') or 0) + minutes * 60)
    return at
