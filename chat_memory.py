"""Bounded, session-owned attachment memory. Nothing here is written to disk."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

from fastapi import HTTPException

TTL_SECONDS = 3600
MAX_BYTES = 64 * 1024 * 1024
MAX_SESSION_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 240
_lock = threading.RLock()
_entries: dict[str, dict] = {}


def owner(session_cookie: str) -> str:
    return hashlib.sha256(session_cookie.encode()).hexdigest()


def provider_scope(provider: dict) -> str:
    value = "\0".join(str(provider.get(k, "")) for k in ("id", "base_url", "model"))
    return hashlib.sha256(value.encode()).hexdigest()


def clear(session_owner: str | None = None) -> None:
    with _lock:
        for token in list(_entries):
            if session_owner is None or _entries[token]["owner"] == session_owner:
                del _entries[token]


def _prune() -> None:
    cutoff = time.monotonic() - TTL_SECONDS
    for token in list(_entries):
        if _entries[token]["touched"] < cutoff:
            del _entries[token]


def public(item: dict) -> dict:
    return {k: item[k] for k in ("id", "name", "kind", "characters", "truncated") if k in item}


def remember(session_owner: str, scope: str, items: list[dict]) -> list[dict]:
    """Commit only after successful completion; preserve no failed-upload content."""
    with _lock:
        _prune()
        for original in items:
            item = dict(original)
            size = len(item.get("text", "").encode("utf-8")) + len(item.get("image", {}).get("data", ""))
            # Each chat request is already size-bounded; eviction is least-recently-used.
            while _entries and (len(_entries) >= MAX_ENTRIES or
                    sum(e["size"] for e in _entries.values()) + size > MAX_BYTES or
                    sum(e["size"] for e in _entries.values() if e["owner"] == session_owner) + size > MAX_SESSION_BYTES):
                own_over = sum(e["size"] for e in _entries.values() if e["owner"] == session_owner) + size > MAX_SESSION_BYTES
                choices = {k: v for k, v in _entries.items() if not own_over or v["owner"] == session_owner}
                if not choices:
                    break
                del _entries[min(choices, key=lambda k: choices[k]["touched"])]
            item["id"] = secrets.token_urlsafe(24)
            _entries[item["id"]] = {"owner": session_owner, "scope": scope,
                "touched": time.monotonic(), "size": size, "item": item}
            original["id"] = item["id"]
        return [public(item) for item in items]


def resolve(session_owner: str, scope: str, references: list) -> list[dict]:
    if not isinstance(references, list) or len(references) > 6:
        raise HTTPException(400, "对话附件引用格式不正确。")
    result = []
    with _lock:
        _prune()
        for ref in references:
            token = ref.get("id") if isinstance(ref, dict) else None
            entry = _entries.get(token) if isinstance(token, str) else None
            if not entry or entry["owner"] != session_owner:
                raise HTTPException(409, "之前的聊天附件已过期或不可用，请清空对话并重新上传。")
            if entry["scope"] != scope:
                raise HTTPException(409, "当前模型已变更，请清空对话并重新选择要发送的附件。")
            entry["touched"] = time.monotonic()
            result.append(entry["item"])
    return result
