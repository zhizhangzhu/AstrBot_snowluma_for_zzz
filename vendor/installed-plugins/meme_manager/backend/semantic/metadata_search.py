"""Local caption/tag lookup when no embedding provider is configured."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .models import IMAGE_EXTENSIONS, REVIEW_CATEGORY, build_id_map
from .storage import file_sha256, load_metadata, safe_relative_path


def _normalize(value: Any) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def _entries(pack_contexts: list[dict[str, Any]]):
    for context in pack_contexts:
        try:
            metadata = load_metadata(context["pack_dir"])
            for entry_id, item in metadata.get("images", {}).items():
                if not isinstance(item, dict) or item.get("category") == REVIEW_CATEGORY:
                    continue
                if item.get("caption_status") != "done" or not item.get("caption"):
                    continue
                path = safe_relative_path(context["pack_dir"], item.get("relative_path", ""))
                if path and path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                    yield context, str(entry_id), item, path
        except Exception:
            logger.warning("Metadata lookup unavailable for pack %s", context.get("pack_id"), exc_info=True)


def has_searchable_metadata(pack_contexts: list[dict[str, Any]]) -> bool:
    return next(_entries(pack_contexts), None) is not None


def load_character_references(plugin_data_dir: Path | str) -> list[dict[str, Any]]:
    path = Path(plugin_data_dir) / "character_references.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return [item for item in payload.get("characters", []) if isinstance(item, dict) and item.get("name")]
    except Exception:
        logger.warning("Could not read local character references", exc_info=True)
        return []


def character_reference_images(plugin_data_dir: Path | str, target_pack_id: str):
    """Read explicit user-confirmed identities; never infer identity from hair alone."""
    from ..packs.resolver import get_pack_paths

    results = []
    for reference in load_character_references(plugin_data_dir):
        if reference.get("target_pack_id") != target_pack_id:
            continue
        try:
            pack_dir = get_pack_paths(reference["pack_id"])["pack_dir"]
            images = load_metadata(pack_dir).get("images", {})
            item = images.get(reference.get("entry_id")) or next(
                (entry for entry in images.values() if entry.get("content_sha256") == reference.get("content_sha256")), {})
            path = safe_relative_path(pack_dir, item.get("relative_path", ""))
            if item.get("category") == REVIEW_CATEGORY or not path or not path.is_file():
                continue
            digest = reference.get("content_sha256")
            if not digest or item.get("content_sha256") != digest or file_sha256(path) != digest:
                continue
            results.append({"name": str(reference["name"])[:80], "aliases": reference.get("aliases", [])[:6], "path": path})
            if len(results) >= 3:
                break
        except Exception:
            logger.warning("Character reference unavailable: %s", reference.get("name"), exc_info=True)
    return results


def search_metadata(pack_contexts: list[dict[str, Any]], plugin_data_dir: Path | str,
                    query: str, *, top_k: int = 5) -> dict[str, Any]:
    text = _normalize(str(query or "")[:1000])
    for filler in ("表情包", "表情", "图片", "动图", "刚才", "那个", "这张", "收集", "发给我", "发送"):
        # Preserve meaningful emotion words such as 无表情 / 面无表情 / 没表情.
        text = re.sub(r"(?<!无)(?<!没)表情", "", text) if filler == "表情" else text.replace(filler, "")
    if len(text) < 2:
        return {"ok": True, "candidates": [], "reason": "请提供人物、物品、动作或具体画面关键词"}
    entries = list(_entries(pack_contexts))
    known_characters = {}
    for reference in load_character_references(plugin_data_dir):
        known_characters[str(reference["name"])] = [str(reference["name"]), *reference.get("aliases", [])]
    for _, _, item, _ in entries:
        for tag in item.get("tags", []):
            if str(tag).startswith("角色:"):
                name = str(tag)[3:]
                known_characters.setdefault(name, [name])
    requested_characters = [aliases for aliases in known_characters.values()
                            if any(len(_normalize(alias)) >= 2 and _normalize(alias) in text for alias in aliases)]
    query_grams = {text[i:i + 2] for i in range(len(text) - 1)}
    ranked = []
    for context, entry_id, item, path in entries:
        tags = [str(tag) for tag in item.get("tags", []) if not str(tag).startswith("category:")]
        caption = str(item.get("caption", ""))
        document = _normalize(" ".join([caption, *tags, str(item.get("visible_text", ""))]))
        if requested_characters and not all(any(_normalize(alias) in document for alias in aliases) for aliases in requested_characters):
            continue
        tag_matches = sum(3 + min(len(_normalize(tag)), 12) for tag in tags
                          if len(_normalize(tag)) >= 2 and (_normalize(tag) in text or text in _normalize(tag)))
        overlap = len(query_grams & {document[i:i + 2] for i in range(len(document) - 1)})
        if not tag_matches and (overlap < 2 or overlap / max(1, len(query_grams)) < 0.3):
            continue
        score = tag_matches + overlap + (15 if text in document else 0)
        ranked.append((score, context, entry_id, item, path))
    ranked.sort(key=lambda row: (-row[0], row[2], row[1]["pack_id"]))
    ids = build_id_map(entry_id for _, entry_id, _, _ in entries)
    candidates = []
    seen = set()
    for _, context, entry_id, item, path in ranked:
        if entry_id in seen:
            continue
        if file_sha256(path) != item.get("content_sha256"):
            continue
        seen.add(entry_id)
        candidates.append({"id": ids[entry_id], "caption": item["caption"], "tags": item.get("tags", []),
                           "visible_text": item.get("visible_text", ""), "_source_pack_id": context["pack_id"], "content_sha256":item.get("content_sha256","")})
        if len(candidates) >= max(1, min(10, int(top_k))):
            break
    logger.info("[meme_manager] 本地描述/标签检索完成：%s 个候选，图包：%s", len(candidates), [c["pack_id"] for c in pack_contexts])
    return {"ok": True, "candidates": candidates, "max_selectable": 1, "backend": "local_metadata",
            "reason": "" if candidates else "已有描述和标签中没有找到匹配图片；不代表图库为空"}
