"""Desktop activity adapter under Companion's existing persona and budget coordinator."""
from __future__ import annotations

import json
import uuid

from .integrations import installed, record_outputs


class ScheduleMixin:
    def real_schedule_capabilities(self):
        # Unscoped discovery never exposes another persona's terminal.
        return []

    def real_schedule_capabilities_for_scope(self, request):
        if self.hub.stopping:
            return []
        binding = next((b for b in self.store.bindings() if b.persona_id == request.get("persona_id")
                        and b.bot_id == request.get("bot_id")), None)
        if binding is None:
            return []
        peer = self.hub.peer(binding)
        available = self.hub.preference(binding.conversation_origin) == "desktop"
        tools = [t["name"] for w in peer.catalog["worlds"] if w.get("supported") for t in w.get("tools", [])] if peer else []
        return [{"id": "activity", "title": "桌宠自主活动：" + "、".join(tools)[:70],
                 "enabled": True, "available": available, "effect": "execution",
                 "min_interval_seconds": 3600, "terminal_activity": True,
                 "reason": "" if available else "桌宠当前不可交互，保留目标并等待能力恢复"}]

    async def run_real_schedule_activity(self, activity_id, request):
        if self.hub.stopping:
            return {"verified": False, "reason": "terminal_reloading"}
        if activity_id != "activity":
            return {"verified": False, "reason": "unknown_activity"}
        binding = next((b for b in self.store.bindings() if b.persona_id == request.get("persona_id")
                        and b.bot_id == request.get("bot_id")), None)
        owner = installed(self.context, "astrbot_plugin_private_companion")
        if owner is None or binding is None:
            return {"verified": False, "reason": "activity_scope_unavailable"}
        previous_id = (request.get("checkpoint") or {}).get("request_id")
        if previous_id:
            previous = self.store.get(binding.device_id, str(previous_id))
            status = previous["state"] if previous else "unknown"
            return {"verified": status == "completed", "partial": status != "completed",
                    "evidence_kind": "desktop_execution_receipt" if status == "completed" else "",
                    "artifact_id": previous_id, "summary": previous["payload"].get("text", "") if previous else "",
                    "checkpoint": {"request_id": previous_id, "status": status}}
        control = owner.real_schedule_control(request)
        if not control.get("managed") or not control.get("selected") or not control.get("execution_allowed"):
            return {"verified": False, "reason": "activity_not_admitted"}
        if owner._active_persona_scope() != binding.persona_id or self.proactive_preference(owner, binding.conversation_origin) != "desktop":
            return {"verified": False, "partial": True, "checkpoint": {"waiting_for": "desktop"}}
        peer = self.hub.peer(binding)
        user = owner.data.get("users", {}).get(binding.user_id, {})
        persona = await owner._resolve_proactive_persona_prompt(user, umo=binding.conversation_origin)
        prompt = (
            "Choose whether to use your available desktop body or extension abilities for this activity. "
            "Use your persona, interests and the supplied goal. Inaction is allowed. "
            "Return only JSON: {\"act\":false,\"reason\":\"...\"} or "
            "{\"act\":true,\"world\":\"...\",\"tool\":\"...\",\"arguments\":{...}}. "
            "Generate any words you say in your own persona voice. Do not claim an outcome before execution.\n"
            "The execution time below is authoritative for greetings and references to today. "
            "A planned time describes an intention, never the current time. Do not replay missed greetings or "
            "describe an afternoon/evening activity as happening now when it is currently morning.\n"
            + json.dumps({"execution_time": owner._environment_now().isoformat(),
                          "goal": request.get("intent", {}), "capabilities": peer.catalog}, ensure_ascii=False)
        )
        raw = await owner._llm_call(prompt, system_prompt=persona, task="real_schedule_desktop", max_tokens=1000, timeout_seconds=60)
        choice = owner._extract_json_payload(raw or "")
        if not isinstance(choice, dict) or choice.get("act") is not True:
            return {"verified": False, "reason": "persona_chose_no_action"}
        if not owner.real_schedule_control(request).get("execution_allowed") or self.proactive_preference(owner, binding.conversation_origin) != "desktop":
            return {"verified": False, "partial": True, "checkpoint": {"waiting_for": "desktop"}}
        if not isinstance(choice.get("arguments"), dict):
            return {"verified": False, "reason": "invalid_action_arguments"}
        request_id = uuid.uuid4().hex
        result = await self.hub.call(binding, str(choice.get("world", "")), str(choice.get("tool", "")),
                                     choice["arguments"], call_id=request_id, proactive=True)
        await record_outputs(self.context, binding, result, request_id, archive=True)
        completed = result.get("status") == "completed"
        return {"verified": completed, "evidence_kind": "desktop_execution_receipt" if completed else "",
                "summary": result.get("text", ""), "artifact_id": request_id,
                "partial": not completed, "checkpoint": {"request_id": request_id, "status": result.get("status")},
                "terminal": "desktop", "device_id": binding.device_id}
