"""Bounded byte transfer; a Windows path is never treated as a Linux attachment."""
import base64
import hashlib
from pathlib import Path

from .protocol import ProtocolError

MAX_BYTES = 1024 * 1024
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def unpack(attachments):
    if not isinstance(attachments, list) or len(attachments) > 8:
        raise ProtocolError("invalid_attachments")
    total = 0
    result = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or not isinstance(attachment.get("base64"), str):
            raise ProtocolError("invalid_attachment")
        try:
            raw = base64.b64decode(attachment["base64"], validate=True)
        except ValueError:
            raise ProtocolError("invalid_attachment_encoding") from None
        total += len(raw)
        mime = attachment.get("mime")
        if total > MAX_BYTES or not raw or mime not in IMAGE_MIMES | {"text/plain", "application/pdf"}:
            raise ProtocolError("attachment_limit_or_mime")
        if attachment.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ProtocolError("attachment_digest_mismatch")
        result.append((attachment, raw))
    return result


def pack(raw, mime, name=""):
    if len(raw) > MAX_BYTES:
        raise ProtocolError("attachment_too_large")
    return {"mime": mime, "base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest(),
            "name": name, "fallbackText": name or mime}


def mime_for(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'): return "image/png"
    if raw.startswith(b'\xff\xd8\xff'): return "image/jpeg"
    if raw.startswith((b'GIF87a', b'GIF89a')): return "image/gif"
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP': return "image/webp"
    raise ProtocolError("unsupported_image_encoding")
