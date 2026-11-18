"""Convert independently persisted persona config to the plugin's runtime units."""
from copy import deepcopy

# The config and page store integer percentages; bootstrap runtime attributes
# use fractions, including -0.01 for the per-channel inheritance sentinel.
PERCENT_RUNTIME_KEYS = frozenset({
    'group_repeat_follow_probability', 'group_repeat_interrupt_probability',
    'group_repeat_interrupt_probability_step', 'group_wakeup_interest_probability',
    'group_wakeup_topic_interest_max_boost', 'group_wakeup_debounce_pending_penalty',
    'tts_trigger_probability', 'auto_voice_probability',
    'main_user_mention_voice_probability', 'rest_reply_probability',
    'proactive_photo_text_probability', 'proactive_share_probability',
    'tts_private_trigger_probability', 'tts_group_trigger_probability',
    'main_user_voice_probability',
})
INHERIT_PERCENT_KEYS = frozenset({
    'tts_private_trigger_probability', 'tts_group_trigger_probability',
    'main_user_voice_probability',
})
LEGACY_RUNTIME_ALIASES = {
    'allow_photo_text_action': 'enable_photo_text_action',
    'allow_screen_peek_action': 'enable_screen_glance_action',
    'allow_poke_action': 'enable_poke_action',
    'allow_voice_action': 'enable_voice_action',
    'creative_base_chars_per_hour': 'creative_chars_per_session',
    'semantic_message_debounce_seconds': 'text_message_debounce_seconds',
    'DIARY_PROVIDER_ID': 'DREAM_DIARY_PROVIDER_ID',
    'DREAM_PROVIDER_ID': 'DREAM_DIARY_PROVIDER_ID',
}

QUICK_COMPLEX_KEYS = frozenset({
    'LLM_PROVIDER_ID', 'DAILY_PLAN_PROVIDER_ID', 'DETAIL_ENHANCEMENT_PROVIDER_ID',
    'HISTORY_SUMMARY_PROVIDER_ID', 'RELATIONSHIP_ANALYSIS_PROVIDER_ID',
    'COMPANION_MEMORY_PROVIDER_ID', 'DIALOGUE_EPISODE_PROVIDER_ID',
    'GROUP_EPISODE_PROVIDER_ID', 'FORWARD_MESSAGE_PROVIDER_ID',
    'PROACTIVE_PERSONA_JUDGE_PROVIDER_ID', 'TROUBLESHOOTING_PROVIDER_ID', 'DAILY_REVIEW_PROVIDER_ID',
})
QUICK_FAST_KEYS = frozenset({
    'MAI_STYLE_PROVIDER_ID', 'RESPONSE_REVIEW_PROVIDER_ID', 'SMART_SILENCE_PROVIDER_ID',
    'EMOTION_JUDGEMENT_PROVIDER_ID', 'SMART_MESSAGE_DEBOUNCE_PROVIDER_ID',
    'REST_WAKEUP_PROVIDER_ID', 'GROUP_INTERJECT_PROVIDER_ID', 'GROUP_SLANG_PROVIDER_ID',
    'VOICE_PROMPT_PROVIDER_ID', 'tts_conversion_provider_id', 'NARRATION_PROVIDER_ID',
    'NEWS_PROVIDER_ID', 'WEB_EXPLORATION_PROVIDER_ID',
})
QUICK_CREATIVE_KEYS = frozenset({
    'CREATIVE_PROVIDER_ID', 'CREATIVE_OUTLINE_PROVIDER_ID', 'CREATIVE_REVIEW_PROVIDER_ID',
    'DREAM_DIARY_PROVIDER_ID', 'PHOTO_PROMPT_PROVIDER_ID',
})


def normalize_runtime_override(owner, key, value, persona_id):
    quick_keys = QUICK_COMPLEX_KEYS | QUICK_FAST_KEYS | QUICK_CREATIVE_KEYS | {'GROUP_FOLLOWUP_JUDGE_PROVIDER_ID'}
    if key in quick_keys and not value and owner.get_persona_setting('provider_config_mode', persona_id, 'quick') == 'quick':
        complex_model = owner.get_persona_setting('COMPLEX_REASONING_PROVIDER_ID', persona_id, '')
        if key in QUICK_COMPLEX_KEYS:
            return complex_model
        if key in QUICK_CREATIVE_KEYS:
            return owner.get_persona_setting('CREATIVE_MODEL_PROVIDER_ID', persona_id, '') or complex_model
        fast = owner.get_persona_setting('FAST_RESPONSE_PROVIDER_ID', persona_id, '')
        return fast if key == 'GROUP_FOLLOWUP_JUDGE_PROVIDER_ID' else fast or complex_model
    if key in PERCENT_RUNTIME_KEYS:
        entry = owner._persona_scope_manifest()[key]
        minimum = -1 if key in INHERIT_PERCENT_KEYS else 0
        maximum = 150 if key == 'group_wakeup_topic_interest_max_boost' else 100
        try:
            percent = int(value)
        except (ValueError, TypeError, OverflowError):
            percent = int(entry.get('default') or 0)
        return max(minimum, min(maximum, percent)) / 100.0
    if key in LEGACY_RUNTIME_ALIASES:
        return owner.get_persona_setting(LEGACY_RUNTIME_ALIASES[key], persona_id)
    if key == 'worldbook_self_registration_block_words':
        return owner._parse_text_list_config(value, limit=120)
    if key == 'main_user_mention_voice_keywords':
        return owner._parse_text_list_config(value, limit=80)
    if key == 'tts_trigger_keywords':
        return owner._normalize_tts_trigger_keywords(value)
    if key == 'hdsi_experiment_window_modes':
        from .hdsi_experiment import normalize_window_modes
        return normalize_window_modes(value)
    if key == 'relationship_stage_policy':
        from .relationship_policy import normalize_relationship_stage_policy
        return normalize_relationship_stage_policy(value)
    if key == 'photo_generation_allowed_scopes':
        from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS, normalize_photo_generation_scope_limit
        return {scope: normalize_photo_generation_scope_limit(owner.get_persona_setting(limit_key, persona_id, -1))
                for scope, limit_key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.items()}
    return deepcopy(value)
