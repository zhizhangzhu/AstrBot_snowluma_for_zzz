"""Companion integration seam for the independently installed desktop plugin."""
from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import sys
from types import ModuleType

_runtime = sys.modules.setdefault("_astrbot_desktop_routing_v1", ModuleType("_astrbot_desktop_routing_v1"))
if not hasattr(_runtime, "proactive_origin"):
    _runtime.proactive_origin = ContextVar("desktop_proactive_origin", default="")


def plugin(owner):
    for star in owner.context.get_all_stars():
        if getattr(star, "activated", False) and getattr(star, "root_dir_name", "") == "astrbot_plugin_desktop_pet":
            return getattr(star, "star_cls", None)
    return None


def proactive_delivery(function):
    @wraps(function)
    async def run(owner, umo, *args, **kwargs):
        token = _runtime.proactive_origin.set(str(umo))
        try:
            return await function(owner, umo, *args, **kwargs)
        finally:
            _runtime.proactive_origin.reset(token)
    return run


async def deliver(owner, umo, chain):
    if _runtime.proactive_origin.get() != str(umo):
        return None
    terminal = plugin(owner)
    if terminal is None:
        return None
    return await terminal.deliver_proactive(owner, str(umo), chain)


def allow_poke(owner, umo):
    terminal = plugin(owner)
    return terminal is None or terminal.proactive_preference(owner, str(umo)) == "qq"
