"""Narrow access to installed companion APIs; every write carries explicit ownership."""
from __future__ import annotations

import logging


def installed(context, folder):
    for star in context.get_all_stars():
        if getattr(star, "activated", False) and getattr(star, "root_dir_name", "") == folder:
            return getattr(star, "star_cls", None)
    return None


async def record_outputs(context, binding, result, request_id, *, archive=False):
    try:
        return await _record_outputs(context, binding, result, request_id, archive=archive)
    except Exception as exc:
        logging.getLogger(__name__).warning("Desktop delivery retained; shared recording pending: %s", type(exc).__name__)
        result["shared_recording"] = "pending"
        return False


async def _record_outputs(context, binding, result, request_id, *, archive=False):
    if result.get("status") != "completed":
        return False
    plugin = installed(context, "astrbot_plugin_memory_companion")
    bridge = getattr(plugin, "memory_companion", None)
    if bridge is None:
        return False
    spoken = []
    for index, output in enumerate(result.get("outputs", [])):
        meta = output.get("meta") or {}
        if meta.get("kind") not in {"say", "ask"} or not str(output.get("text", "")).strip():
            continue
        spoken.append(output["text"])
        await bridge.record_visible_turn(
            role="assistant", content=output["text"], scope="private", session_id=binding.conversation_origin,
            platform="aiocqhttp", user_id=binding.user_id, message_id=f"desktop:{request_id}:{index}",
            source="desktop_pet", metadata={"bot_id": binding.bot_id, "persona_id": binding.persona_id,
                "terminal": "desktop", "source_platform": "desktop_pet", "device_id": binding.device_id, "request_id": request_id,
                "delivery_status": result.get("status"), "interaction_type": meta["kind"]})
    if archive and spoken:
        owner = installed(context, "astrbot_plugin_private_companion")
        if owner is not None and owner._active_persona_scope() == binding.persona_id:
            await owner._archive_proactive_message_to_conversation(user={"umo": binding.conversation_origin},
                user_prompt="", assistant_response="\n".join(spoken), umo=binding.conversation_origin)
    return True
