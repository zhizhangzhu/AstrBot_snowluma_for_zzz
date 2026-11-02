"""Versioned wire boundary. Identity is supplied by pairing, never by a frame."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

VERSION = 1
MAX_FRAME = 2 * 1024 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
CLIENT_TYPES = frozenset({"hello", "state", "catalog", "event", "result", "ping", "query", "cognition"})


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    device_id: str
    persona_id: str
    bot_id: str
    user_id: str
    qq_platform_id: str

    @property
    def scope(self) -> str:
        return f"{self.persona_id}:{self.bot_id}:{self.user_id}"

    @property
    def conversation_origin(self) -> str:
        return f"{self.qq_platform_id}:FriendMessage:{self.user_id}"


def frame(kind: str, message_id: str, payload: dict) -> dict:
    return {"v": VERSION, "type": kind, "id": message_id, "payload": payload}


def decode(raw: str) -> dict:
    if len(raw.encode("utf-8")) > MAX_FRAME:
        raise ProtocolError("frame_too_large")
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ProtocolError("invalid_json") from None
    if not isinstance(value, dict) or value.get("v") != VERSION:
        raise ProtocolError("protocol_version")
    if value.get("type") not in CLIENT_TYPES:
        raise ProtocolError("unknown_frame")
    if not isinstance(value.get("id"), str) or not ID.fullmatch(value["id"]):
        raise ProtocolError("invalid_id")
    if not isinstance(value.get("payload"), dict):
        raise ProtocolError("invalid_payload")
    return value


def catalog(value: dict) -> dict:
    worlds = value.get("worlds")
    if not isinstance(worlds, list) or len(worlds) > 64:
        raise ProtocolError("invalid_worlds")
    seen: set[str] = set()
    count = 0
    for world in worlds:
        if not isinstance(world, dict) or not ID.fullmatch(str(world.get("id", ""))):
            raise ProtocolError("invalid_world")
        if world["id"] in seen:
            raise ProtocolError("duplicate_world")
        seen.add(world["id"])
        tools = world.get("tools", [])
        if not isinstance(tools, list):
            raise ProtocolError("invalid_tools")
        names: set[str] = set()
        for tool in tools:
            count += 1
            if count > 256 or not isinstance(tool, dict):
                raise ProtocolError("tool_limit")
            name = tool.get("name")
            if not isinstance(name, str) or not ID.fullmatch(name) or name in names:
                raise ProtocolError("invalid_tool_name")
            names.add(name)
            if not isinstance(tool.get("parameters"), dict) or not isinstance(tool.get("description"), str):
                raise ProtocolError("invalid_tool_schema")
    return value


def terminal_state(value: dict) -> dict[str, bool]:
    fields = ("ready", "body", "visible", "locked", "suspended", "dnd")
    return {key: value.get(key) is True for key in fields}


def route(state: dict[str, bool] | None) -> str:
    """Unavailable falls back; an online hidden/quiet terminal waits instead."""
    if not state or not state.get("ready"):
        return "qq"
    if state.get("dnd") or state.get("locked") or state.get("suspended"):
        return "wait"
    if not state.get("body"):
        return "qq"
    return "desktop" if state.get("visible") else "wait"
