"""Read the private, short-lived session exported by the Linux QQ browser.

This file is not a settings store. No browser credentials are copied into the
plugin config, logs, model context, or public capability descriptors.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

SOURCE = "linux_qq_browser"
LABEL = "Linux QQ 空间浏览器"
ALLOWED = frozenset(("uin", "p_uin", "skey", "p_skey", "pt4_token", "ptcz", "RK"))


class BrowserSessionError(RuntimeError):
    pass


def session_path() -> str:
    return os.environ.get("XIAOXIAOQING_QZONE_SESSION_FILE", "").strip()


def expected_account() -> str:
    value = os.environ.get("XIAOXIAOQING_QZONE_UIN", "").strip()
    if not re.fullmatch(r"[1-9]\d{4,14}", value):
        raise BrowserSessionError("尚未绑定 Linux QQ 空间账号")
    return value


def load_session(path: str, expected: str, *, now: float | None = None) -> dict:
    current = time.time() if now is None else now
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized")
        value = json.loads(raw)
    except (OSError, ValueError, TypeError):
        raise BrowserSessionError("Linux QQ 空间会话尚未同步，请在 QQ 桌面打开空间") from None
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("source") != SOURCE:
        raise BrowserSessionError("Linux QQ 空间会话格式不兼容")
    if str(value.get("uin", "")) != expected:
        raise BrowserSessionError("Linux QQ 空间登录账号与绑定账号不一致，已停止使用")
    try:
        captured = float(value.get("captured_at", 0))
        validated = float(value.get("validated_at", 0))
        expiry = float(value.get("expires_at", 0))
        if not (-30 <= current - captured <= 180 and -30 <= current - validated <= 600):
            raise ValueError("stale")
        if expiry and expiry <= current:
            raise ValueError("expired")
    except (TypeError, ValueError, OverflowError):
        raise BrowserSessionError("Linux QQ 空间会话已过期或同步停止，请重新打开空间") from None
    if value.get("state") != "ready":
        raise BrowserSessionError("Linux QQ 空间尚未确认登录，请在 QQ 桌面打开空间完成登录")
    fields = value.get("cookies")
    if not isinstance(fields, dict):
        raise BrowserSessionError("Linux QQ 空间会话缺少凭据")
    fields = {k: v for k, v in fields.items() if k in ALLOWED}
    for key, text in fields.items():
        if not isinstance(text, str) or not text or len(text) > 4096 or any(ord(c) < 32 or c == ";" for c in text):
            raise BrowserSessionError("Linux QQ 空间会话凭据格式错误")
        if key in ("uin", "p_uin") and text.lstrip("oO").lstrip("0") != expected:
            raise BrowserSessionError("Linux QQ 空间凭据账号不一致，已停止使用")
    if not fields.get("uin") or not fields.get("p_skey"):
        raise BrowserSessionError("Linux QQ 空间缺少有效的账号和登录凭据")
    header = "; ".join(f"{k}={fields[k]}" for k in sorted(fields))
    return {
        "source": SOURCE, "uin": expected, "cookie_header": header,
        "generation": hashlib.sha256(header.encode()).hexdigest(),
        "captured_at": captured, "validated_at": validated,
    }


def configured_session() -> dict:
    return load_session(session_path(), expected_account())


def public_status() -> dict:
    if not session_path():
        return {}
    status = {"source": SOURCE, "label": LABEL, "configured": True}
    try:
        session = configured_session()
        status.update(state="ready", uin=session["uin"], captured_at=session["captured_at"],
                      validated_at=session["validated_at"], reason="")
    except BrowserSessionError as exc:
        status.update(state="unavailable", reason=str(exc))
    return status
