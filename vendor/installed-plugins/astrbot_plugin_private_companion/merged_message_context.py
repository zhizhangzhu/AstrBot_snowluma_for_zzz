"""Keep consumed user follow-ups in the official history, not just a temp prompt."""
from __future__ import annotations


def restore_merged_user_text(event, run_context) -> bool:
    merged = str(getattr(event, "_private_companion_merged_user_text", "") or "")
    original = str(getattr(event, "message_str", "") or "").strip()
    if not merged or not original:
        return False
    messages = getattr(run_context, "messages", None)
    if not isinstance(messages, list):
        return False
    user = next((m for m in reversed(messages) if getattr(m, "role", "") == "user"), None)
    if user is None:
        return False
    if isinstance(user.content, str):
        if user.content.strip() != original:
            return False
        user.content = merged
        return True
    for part in user.content or []:
        if (getattr(part, "type", "") == "text" and
                not getattr(part, "_no_save", False) and
                str(getattr(part, "text", "")).strip() == original):
            part.text = merged
            return True
    return False
