"""Authenticated duplex sessions. Socket writes never prove display or execution."""
from __future__ import annotations

import asyncio
import time
import uuid
import contextvars
from dataclasses import dataclass, field
from collections import deque
from typing import Awaitable, Callable

from aiohttp import web, WSMsgType

from .protocol import Binding, MAX_FRAME, ProtocolError, catalog, decode, frame, route, terminal_state
from .store import Store
from .media import unpack


class DeliveryUnknown(RuntimeError):
    pass


@dataclass
class Peer:
    binding: Binding
    socket: web.WebSocketResponse
    state: dict = field(default_factory=dict)
    catalog: dict = field(default_factory=lambda: {"worlds": []})
    revision: str = ""
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    heartbeat: float = field(default_factory=time.monotonic)
    event_times: deque = field(default_factory=deque)


class Hub:
    def __init__(self, store: Store, receive: Callable[[Binding, dict], Awaitable[None]], changed=None):
        self.store, self.receive, self.changed = store, receive, changed
        self.peers: dict[str, Peer] = {}
        self.inflight: set[asyncio.Future] = set()
        self.stopping = False
        self.remote = None
        self.background = set()
        self.active_calls = {}

    def peer(self, binding: Binding) -> Peer | None:
        peer = self.peers.get(binding.device_id)
        return peer if peer and self.store.enabled(binding) and time.monotonic()-peer.heartbeat < 65 else None

    def for_origin(self, origin: str) -> Peer | None:
        return next((p for p in self.peers.values() if p.binding.conversation_origin == origin and self.peer(p.binding)), None)

    def preference(self, origin: str) -> str:
        if any(b.conversation_origin == origin and (self.store.dnd(b.device_id) or self.store.quiet(b.device_id)) for b in self.store.bindings()):
            return "wait"
        peer = self.for_origin(origin)
        return route(peer.state if peer else None)

    async def socket(self, request: web.Request) -> web.StreamResponse:
        auth = request.headers.get("Authorization", "")
        binding = self.store.authenticate(auth[7:]) if auth.startswith("Bearer ") else None
        if binding is None:
            raise web.HTTPUnauthorized(text="device_authentication_required")
        if not request.secure:
            raise web.HTTPForbidden(text="tls_required")
        socket = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_FRAME)
        await socket.prepare(request)
        peer = Peer(binding, socket)
        old = self.peers.get(binding.device_id)
        if old:
            await old.socket.close(code=4001, message=b"replaced")
        self.peers[binding.device_id] = peer
        await socket.send_json(frame("welcome", uuid.uuid4().hex, {"device_id": binding.device_id,
                               "persona_id": binding.persona_id, "bot_id": binding.bot_id, "user_id": binding.user_id}))
        unresolved = self.store.outstanding(binding.device_id)
        if unresolved:
            await socket.send_json(frame("reconcile", uuid.uuid4().hex, {"ids": unresolved}))
        try:
            async for raw in socket:
                if raw.type != WSMsgType.TEXT:
                    break
                if not self.store.enabled(binding):
                    break
                peer.heartbeat = time.monotonic()
                try:
                    await self.handle(peer, decode(raw.data))
                except ProtocolError as exc:
                    await socket.send_json(frame("error", uuid.uuid4().hex, {"code": str(exc)}))
                except Exception:
                    await socket.send_json(frame("error", uuid.uuid4().hex, {"code": "event_processing_failed"}))
        finally:
            if self.peers.get(binding.device_id) is peer:
                self.peers.pop(binding.device_id, None)
            for pending in peer.pending.values():
                if not pending.done():
                    pending.set_exception(DeliveryUnknown("terminal_disconnected"))
            await socket.close()
            await self.notify(binding)
        return socket

    async def notify(self, binding: Binding) -> None:
        if self.changed:
            await self.changed(binding)

    async def handle(self, peer: Peer, message: dict) -> None:
        kind, mid, payload = message["type"], message["id"], message["payload"]
        if kind == "ping":
            await peer.socket.send_json(frame("pong", mid, {}))
        elif kind in {"state", "hello"}:
            peer.state = terminal_state(payload)
            self.store.set_dnd(peer.binding.device_id, peer.state["dnd"])
            self.store.set_quiet(peer.binding.device_id, peer.state["ready"] and (
                peer.state["locked"] or peer.state["suspended"] or (peer.state["body"] and not peer.state["visible"])))
            await self.notify(peer.binding)
        elif kind == "catalog":
            peer.catalog = catalog(payload)
            peer.revision = mid
            await self.notify(peer.binding)
        elif kind in {"query", "cognition"}:
            if self.remote is None:raise ProtocolError("remote_requests_unavailable")
            if sum(1 for task in self.background if not task.done())>=8:raise ProtocolError("remote_request_limit")
            parent=self.active_calls.get(str(payload.get("parent_call_id", "")))
            context=parent["context"] if parent and parent["binding"].device_id==peer.binding.device_id else contextvars.copy_context()
            task=context.run(asyncio.create_task,self.reply_remote(peer,kind,mid,payload))
            self.background.add(task);task.add_done_callback(self.background.discard)
        elif kind == "result":
            if "attachments" in payload:
                unpack(payload["attachments"])
            if payload.get("status") not in {"completed", "failed", "submitted", "cancelled", "unknown"}:
                raise ProtocolError("invalid_result_status")
            prior = self.store.get(peer.binding.device_id, mid)
            if not prior or prior["kind"] != "call":
                raise ProtocolError("unknown_call")
            if prior["state"] == "completed":
                return
            self.store.update(peer.binding.device_id, mid, payload["status"], payload)
            pending = peer.pending.get(mid)
            if pending and not pending.done():
                pending.set_result(payload)
        elif kind == "event":
            now = time.monotonic()
            while peer.event_times and now - peer.event_times[0] > 10:
                peer.event_times.popleft()
            if len(peer.event_times) >= 20:
                raise ProtocolError("event_rate_limit")
            peer.event_times.append(now)
            text = payload.get("text")
            attachments = unpack(payload.get("attachments", []))
            if not isinstance(text, str) or len(text) > 16000 or (not text.strip() and not attachments):
                raise ProtocolError("invalid_event_text")
            world_id = payload.get("world")
            if world_id not in {w["id"] for w in peer.catalog["worlds"] if w.get("supported") is True}:
                raise ProtocolError("world_not_available")
            fresh = self.store.claim(peer.binding.device_id, mid, "event", payload)
            if fresh:
                try:
                    disposition=await self.receive(peer.binding, message)
                except Exception:
                    self.store.update(peer.binding.device_id, mid, "failed")
                    await peer.socket.send_json(frame("ack", mid, {"status": "failed"}))
                    return
                self.store.update(peer.binding.device_id, mid, "observed" if disposition=="observed" else "queued")
            record = self.store.get(peer.binding.device_id, mid)
            await peer.socket.send_json(frame("ack", mid, {"status": record["state"]}))

    async def reply_remote(self, peer, kind, mid, payload):
        try:
            if kind=="cognition" and not self.store.claim(peer.binding.device_id,mid,"cognition",payload):
                record=self.store.get(peer.binding.device_id,mid)
                result=record["payload"] if record["state"]=="completed" else {"error":"cognition_outcome_unconfirmed"}
            else:
                result=await self.remote(peer.binding,kind,payload)
                if kind=="cognition" and not self.stopping:self.store.update(peer.binding.device_id,mid,"completed",result)
            if self.peer(peer.binding) is peer:await peer.socket.send_json(frame(kind+"_result",mid,result))
        except asyncio.CancelledError:
            if not self.stopping and kind=="cognition":self.store.update(peer.binding.device_id,mid,"unknown")
            raise
        except Exception:
            if not self.stopping and self.peer(peer.binding) is peer:
                await peer.socket.send_json(frame(kind+"_result",mid,{"error":"remote_request_failed"}))

    async def notify_terminal(self,binding,kind,payload):
        peer=self.peer(binding)
        if peer:await peer.socket.send_json(frame(kind,uuid.uuid4().hex,payload))

    async def call(self, binding: Binding, world: str, tool: str, arguments: dict,
                   *, timeout: float = 120, call_id: str | None = None, attachments=None, proactive=False, parent_call_id=None) -> dict:
        if self.stopping:
            raise DeliveryUnknown("terminal_hub_stopped")
        if parent_call_id:
            parent=self.active_calls.get(str(parent_call_id))
            if not parent or parent["binding"]!=binding or parent["world"]!=world:raise DeliveryUnknown("parent_call_ended")
        if attachments:
            unpack(attachments)
        peer = self.peer(binding)
        if not peer or not peer.state.get("ready"):
            raise DeliveryUnknown("terminal_unavailable")
        if peer.state.get("locked") or peer.state.get("suspended"):
            raise DeliveryUnknown("terminal_not_interactive")
        if proactive and self.preference(binding.conversation_origin) != "desktop":
            raise DeliveryUnknown("terminal_proactive_deferred")
        spec = next((t for w in peer.catalog["worlds"] if w["id"] == world and w.get("supported") is True
                     for t in w.get("tools", []) if t["name"] == tool), None)
        if spec is None:
            raise ProtocolError("tool_not_available")
        mid = call_id or uuid.uuid4().hex
        if not self.store.claim(binding.device_id, mid, "call", {"world": world, "tool": tool}):
            previous = self.store.get(binding.device_id, mid)
            return {**previous["payload"], "status": previous["state"], "request_id":mid}
        future = asyncio.get_running_loop().create_future()
        settled = asyncio.get_running_loop().create_future()
        self.inflight.add(settled)
        peer.pending[mid] = future
        self.active_calls[mid] = {"binding":binding,"world":world,"context":contextvars.copy_context()}
        try:
            await peer.socket.send_json(frame("call", mid, {"world": world, "tool": tool, "arguments": arguments,
                                        "revision": peer.revision, "expires_at": time.time()+timeout,
                                        **({"parent_call_id":parent_call_id} if parent_call_id else {}),
                                        **({"attachments": attachments} if attachments else {})}))
            return {**await asyncio.wait_for(asyncio.shield(future), timeout), "request_id":mid}
        except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionError, DeliveryUnknown):
            current = self.store.get(binding.device_id, mid)
            if current is None or current["state"] != "completed":
                self.store.update(binding.device_id, mid, "unknown")
            if not peer.socket.closed:
                await peer.socket.send_json(frame("cancel", mid, {}))
            raise
        finally:
            peer.pending.pop(mid, None)
            self.active_calls.pop(mid, None)
            if not future.done():
                future.cancel()
            self.inflight.discard(settled)
            settled.set_result(None)

    async def stop(self) -> None:
        self.stopping = True
        for task in list(self.background):task.cancel()
        if self.background:await asyncio.gather(*list(self.background),return_exceptions=True)
        await asyncio.gather(*(p.socket.close(code=1001) for p in list(self.peers.values())), return_exceptions=True)
        if self.inflight:
            await asyncio.gather(*list(self.inflight), return_exceptions=True)
