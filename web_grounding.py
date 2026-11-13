"""Bounded, explicit public-web grounding through the official DeepSeek API.

No import, capability check or validation performs network I/O. The chat route
owns authorization, quota and credential decryption; this module only accepts
the user's explicit public search query, never company records or chat history.
Returned page text is untrusted source material, never executable instructions.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
MAX_RESULTS = 6
MAX_QUERY_CHARS = 128
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CONTEXT_CHARS = 14000
MAX_EXCERPT_CHARS = 2000
TOTAL_TIMEOUT_SECONDS = 25
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid", ".example", ".onion")


def _eligible(provider: Any) -> bool:
    if not isinstance(provider, dict) or provider.get("id") != "deepseek":
        return False
    if provider.get("enabled") is not True or not isinstance(provider.get("secret"), str) or not provider["secret"]:
        return False
    base = provider.get("base_url")
    if not isinstance(base, str) or len(base) > 500 or _CONTROL.search(base):
        return False
    try:
        parts = urlsplit(base)
        return (parts.scheme == "https" and parts.hostname == "api.deepseek.com"
                and parts.port in {None, 443} and parts.username is None and parts.password is None
                and not parts.query and not parts.fragment
                and parts.path.rstrip("/") in {"", "/v1", "/beta"})
    except ValueError:
        return False


def capabilities(providers: list[dict]) -> dict:
    """Describe availability without decrypting a key or querying the network."""
    available = isinstance(providers, list) and any(_eligible(item) for item in providers)
    return {"available": available, "provider_id": "deepseek" if available else None,
            "label": "DeepSeek Search",
            "note": ("联网关键词将发送给 DeepSeek 搜索，可能额外计费；聊天模型可单独选择。" if available else
                     "联网搜索需启用官方 DeepSeek 服务并保存密钥；聊天模型可单独选择。")}


def validate_query(query: Any) -> str:
    """Validate before reserving a chat request. Never infer a query from data."""
    if not isinstance(query, str) or _CONTROL.search(query):
        raise ValueError("请输入有效的公开联网关键词，不包含控制字符。")
    query = query.strip()
    if not 1 <= len(query) <= MAX_QUERY_CHARS:
        raise ValueError("联网关键词需要 1 至 128 个字符，请缩短后重试。")
    return query


def _public_url(value: Any) -> str | None:
    """Filter links only; never resolve or fetch a result URL from this host."""
    if (not isinstance(value, str) or not value or len(value) > 2048 or value != value.strip()
            or _CONTROL.search(value) or any(c.isspace() for c in value) or "\\" in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        if parts.port not in {None, 80, 443}:
            return None
        host = parts.hostname.lower().rstrip(".")
        if not host or "%" in host or host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Reject alternate numeric spellings of loopback/private addresses
            # (such as 2130706433 or 127.1) and non-public single-label names.
            if "." not in host or re.fullmatch(r"[\d.]+", host) or host.startswith("0x"):
                return None
            ascii_host = host.encode("idna").decode("ascii")
            labels = ascii_host.split(".")
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                return None
            if len(ascii_host) > 253 or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1]):
                return None
            host = ascii_host
        else:
            if not address.is_global or address.is_multicast or getattr(address, "ipv4_mapped", None) is not None:
                return None
            host = "[" + host + "]" if address.version == 6 else host
        authority = host + (":" + str(parts.port) if parts.port else "")
        return urlunsplit((parts.scheme, authority, parts.path, parts.query, parts.fragment))
    except (ValueError, UnicodeError):
        return None


def _text(value: Any, limit: int, key: str) -> str:
    if not isinstance(value, str):
        return ""
    # A compromised or misconfigured upstream may reflect authorization text.
    # Neither a page excerpt nor a title may expose that credential to the UI.
    value = value.replace(key, "[已隐藏密钥]")
    return _TEXT_CONTROL.sub("", value).strip()[:limit]


def _published(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64 or _CONTROL.search(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _sources(payload: Any, key: str) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("Invalid search response")
    sources, seen = [], set()
    for item in payload["results"][:50]:
        if not isinstance(item, dict):
            continue
        url = _public_url(item.get("url"))
        if not url or key in unquote(url):
            continue
        canonical = urlsplit(url)._replace(fragment="").geturl()
        if canonical in seen:
            continue
        title = _text(item.get("title"), 240, key)
        excerpt = next((_text(item.get(name), MAX_EXCERPT_CHARS, key)
                        for name in ("content", "summary", "snippet")
                        if isinstance(item.get(name), str) and item[name].strip()), "")
        if not title and not excerpt:
            continue
        source = {"id": "W" + str(len(sources) + 1), "kind": "web", "title": title or urlsplit(url).hostname,
                  "url": url, "excerpt": excerpt, "published_at": _published(item.get("date"))}
        # Count the whole JSON source set, not just excerpts. Large links may
        # consume part of the model-context budget as well.
        size = len(json.dumps(sources + [source], ensure_ascii=False))
        if size > MAX_CONTEXT_CHARS:
            remaining = len(excerpt) - (size - MAX_CONTEXT_CHARS)
            if remaining < 1:
                continue
            source["excerpt"] = excerpt[:remaining]
        sources.append(source)
        seen.add(canonical)
        if len(sources) == MAX_RESULTS:
            break
    return sources


def _result(status: str, query: str, note: str, sources: list[dict] | None = None) -> dict:
    return {"status": status, "query": query, "provider": "DeepSeek Search", "sources": sources or [], "note": note}


async def search(provider: dict, key: str, query: str) -> dict:
    """Perform at most one explicit, bounded official search; no result fetch."""
    try:
        query = validate_query(query)
    except ValueError as exc:
        return _result("error", "", str(exc))
    if (not isinstance(key, str) or not 1 <= len(key) <= 2048 or _CONTROL.search(key)):
        return _result("error", query, "联网搜索密钥不可用，请由管理员检查 DeepSeek 服务设置。")
    if key in query:
        return _result("error", query.replace(key, "[已隐藏密钥]"), "联网关键词包含密钥，请移除后重试。")
    if not _eligible(provider):
        return _result("error", query, "联网搜索需要已启用的官方 DeepSeek 服务，未向自定义地址发送密钥。")
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", SEARCH_URL,
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"},
                                         json={"query": query, "count": MAX_RESULTS, "need_content": True}) as response:
                    if response.status_code != 200:
                        notes = {400: "搜索服务未接受这些关键词，请缩短或调整后重试。",
                                 401: "DeepSeek 搜索密钥无效或已失效，请由管理员检查。",
                                 402: "DeepSeek 搜索账户余额不足，请由管理员检查。",
                                 403: "当前账户没有搜索权限，或请求被搜索服务拒绝。",
                                 404: "当前 DeepSeek 服务未提供搜索接口，请检查账户与官方服务。",
                                 429: "联网搜索已限流，请稍后重试。"}
                        return _result("error", query, notes.get(response.status_code, "联网搜索服务暂时不可用，请稍后重试。"))
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                        raise ValueError("Invalid response size")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ValueError("Search response too large")
                        chunks.append(chunk)
                    sources = _sources(json.loads(b"".join(chunks)), key)
        return _result("ok" if sources else "empty", query,
                       "已检索公开网页，外部内容请结合原文核实。" if sources else
                       "没有找到可引用的公开结果；这不代表网上不存在相关信息。", sources)
    except (httpx.TimeoutException, TimeoutError):
        return _result("error", query, "联网搜索超时，本次没有取得网页资料；仍可继续普通问答。")
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError, RecursionError):
        return _result("error", query, "联网搜索连接或返回资料异常，本次没有取得网页资料；仍可继续普通问答。")
