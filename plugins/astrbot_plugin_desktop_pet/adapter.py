"""Full AstrBot events with an explicit shared conversation and desktop transport."""
from __future__ import annotations

import time
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain, Image, Reply
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, Platform, PlatformMetadata

from .protocol import Binding
from .media import unpack, pack, mime_for
from pathlib import Path
import uuid


class DesktopEvent(AstrMessageEvent):
    def __init__(self, platform, binding: Binding, envelope: dict):
        payload = envelope["payload"]
        message = AstrBotMessage()
        message.type = MessageType.FRIEND_MESSAGE
        message.self_id, message.session_id = binding.bot_id, binding.user_id
        message.sender = MessageMember(binding.user_id, binding.user_id)
        message.message_id = "desktop:" + envelope["id"]
        message.message_str = payload["text"]
        message.message = [Plain(message.message_str)]
        for attachment, raw in unpack(payload.get("attachments", [])):
            if attachment["mime"].startswith("image/"):
                message.message.append(Image.fromBytes(raw))
        message.timestamp = int(time.time())
        message.raw_message = {"source": "desktop", "device_id": binding.device_id,
                               "event_id": envelope["id"], "desktop_event": payload}
        super().__init__(message.message_str, message, platform.meta(), binding.user_id)
        self.binding, self.desktop_platform = binding, platform
        # Session identity shares history and serialization; platform_meta and send remain desktop.
        self.unified_msg_origin = binding.conversation_origin
        self.persona_id = binding.persona_id
        self.set_extra("desktop_pet_source", {"device_id": binding.device_id,
                       "world": payload["world"], "type": payload.get("event_type", "message")})

    def verified_companion_identity(self, owner) -> dict:
        if owner._active_persona_scope() != self.binding.persona_id:
            raise ValueError("desktop_persona_mismatch")
        return {
            "companion_instance_id": owner._unified_persona_scoped_value("astrbot_plugin_private_companion"),
            "bot_account_id": "aiocqhttp:" + self.binding.bot_id,
            "adapter_instance_id": "aiocqhttp:" + str(owner.target_platform or "aiocqhttp"),
            "subject_namespace": "aiocqhttp:user",
            "platform_subject_id": self.binding.user_id,
        }

    def verified_memory_namespace(self) -> dict:
        # Only this server-constructed event can assert a paired identity; payloads cannot.
        return {"session_id": self.binding.conversation_origin, "bot_id": self.binding.bot_id,
                "user_id": self.binding.user_id, "persona_id": self.binding.persona_id,
                "platform": "aiocqhttp"}

    def verified_companion_user_context(self, owner, subject_id):
        if str(subject_id) != self.binding.user_id:
            return None
        if owner._active_persona_scope() != self.binding.persona_id:
            raise ValueError("desktop_persona_mismatch")
        return {"subject": self.binding.user_id, "platform": "onebot",
                "adapter": self.binding.qq_platform_id, "bot_id": self.binding.bot_id}

    async def send(self, message):
        await self.desktop_platform.deliver(self.binding, message)
        await super().send(message)


class DesktopPlatform(Platform):
    def __init__(self, context, hub):
        super().__init__({"id": "desktop_pet", "type": "desktop_pet"}, context.get_event_queue())
        self.hub = hub

    def meta(self):
        return PlatformMetadata(name="desktop_pet", description="已配对的 Windows 桌宠终端",
                                id="desktop_pet", support_streaming_message=False)

    async def run(self):
        return

    async def send_by_session(self, session, message_chain):
        # Proactive sends use a verified binding selected by the server, not a client session id.
        raise RuntimeError("desktop_delivery_requires_binding")

    async def deliver(self, binding, message, *, proactive=False):
        chain = getattr(message, "chain", [])
        unsupported = [c for c in chain if not isinstance(c, (Plain, Image, Reply))]
        if unsupported:
            raise RuntimeError("desktop_media_transport_not_ready")
        text = "".join(c.text for c in chain if isinstance(c, Plain))
        images = []
        for component in chain:
            if isinstance(component, Image):
                raw = Path(await component.convert_to_file_path()).read_bytes()
                images.append(pack(raw, mime_for(raw)))
        if not text.strip() and not images:
            return
        request_id = uuid.uuid4().hex
        result = await self.hub.call(binding, "desktop-pet", "pet_say", {"script": text}, attachments=images,
                                     call_id=request_id, proactive=proactive)
        result["request_id"] = request_id
        if result.get("status") not in {"completed", "submitted"}:
            raise RuntimeError("desktop_delivery_" + str(result.get("status", "unknown")))
        return result
