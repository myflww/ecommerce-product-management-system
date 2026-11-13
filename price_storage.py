"""Price tracking storage separate from product identity and manual base prices.

No network calls happen here. Listing IDs deliberately have no foreign key: the
product editor replaces its listing rows transactionally while preserving IDs.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

import backend
import exchange_rates

SETTINGS_KEY = "price_tracking:settings"
DEFAULT_SETTINGS = {"enabled": False, "daily_time": "09:00", "timezone": "Asia/Shanghai", "allow_ai": False}
CONFIG_FIELDS = ("source_url", "region", "variant_hint", "enabled")
IDENTITY_FIELDS = ("product_code", "product_number", "currency")


def init_storage() -> None:
    """Additive and repeatable; no product data or existing price is rewritten."""
    with backend._connection(write=True) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS price_tracking (
            listing_id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
            variant_hint TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 1,
            current_price TEXT NOT NULL DEFAULT '', current_currency TEXT NOT NULL DEFAULT '',
            last_success_at TEXT NOT NULL DEFAULT '', last_checked_at TEXT NOT NULL DEFAULT '',
            last_success_source_url TEXT NOT NULL DEFAULT '', last_success_context TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id TEXT NOT NULL,
            checked_at TEXT NOT NULL, source_url TEXT NOT NULL,
            region TEXT NOT NULL DEFAULT '', variant_hint TEXT NOT NULL DEFAULT '',
            price TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS price_history_listing_idx ON price_history(listing_id,id DESC);
        """)


def _settings(conn) -> dict:
    row = conn.execute("SELECT value FROM app_meta WHERE key=?", (SETTINGS_KEY,)).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
        if not isinstance(saved, dict):
            raise ValueError
        result = {**DEFAULT_SETTINGS, **{key: saved[key] for key in DEFAULT_SETTINGS if key in saved}}
        _validate_settings(result)
        return result
    except (ValueError, TypeError, HTTPException):
        raise HTTPException(500, "价格追踪设置无法读取，请联系管理员检查数据备份。") from None


def get_settings() -> dict:
    with backend._connection() as conn:
        return _settings(conn)


def _validate_settings(value: dict) -> None:
    for key in ("enabled", "allow_ai"):
        if type(value[key]) is not bool:
            raise HTTPException(400, "价格追踪开关须为 true 或 false。")
    if not isinstance(value["daily_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value["daily_time"]):
        raise HTTPException(400, "每日更新时间须为 HH:MM，例如 09:00。")
    if value["timezone"] != "Asia/Shanghai":
        raise HTTPException(400, "每日更新使用北京时间 Asia/Shanghai。")


def save_settings(payload: Any, user: dict) -> dict:
    backend._admin(user)
    if not isinstance(payload, dict) or set(payload) - set(DEFAULT_SETTINGS):
        raise HTTPException(400, "价格追踪设置字段不正确。")
    with backend._connection(write=True) as conn:
        result = {**_settings(conn), **payload}
        _validate_settings(result)
        conn.execute("INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (SETTINGS_KEY, json.dumps(result, ensure_ascii=False)))
        backend._audit(conn, user, "price_tracking_settings", "更新价格追踪的每日更新与 AI 提取设置")
        return result


def _listing(conn, listing_id: str):
    if not isinstance(listing_id, str) or not listing_id or len(listing_id) > 64:
        raise HTTPException(404, "平台记录不存在或已移除。")
    listing = conn.execute("SELECT * FROM product_listings WHERE id=?", (listing_id,)).fetchone()
    if not listing:
        raise HTTPException(404, "平台记录不存在或已移除。")
    product = backend._product(conn, listing["product_id"])
    return listing, product


def _can_edit(product, user: dict) -> bool:
    return bool(user and (user["role"] == "admin" or product["created_by"] == user["id"] or product["owner"] == user["name"]))


def _tracking(conn, listing_id: str):
    return conn.execute("SELECT * FROM price_tracking WHERE listing_id=?", (listing_id,)).fetchone()


def sync_product_listing(conn, listing: dict, old_listing=None) -> None:
    """Apply explicit editor context changes in the caller's product transaction."""
    old = _tracking(conn, listing["id"])
    explicit = any(key in listing for key in ("region", "variant_hint"))
    link_changed = old_listing is not None and listing["link"] != old_listing["link"]
    identity_changed = old_listing is not None and any(listing[key] != old_listing[key]
        for key in ("platform", "store", "product_code", "product_number", "currency"))
    if old is None and not explicit:
        return
    source = old["source_url"] if old else listing["link"]
    if explicit or link_changed:
        source = _validate_url(listing["link"])
    region = listing.get("region", old["region"] if old else "")
    variant = listing.get("variant_hint", old["variant_hint"] if old else "")
    changed = bool(old and (identity_changed or source != old["source_url"] or region != old["region"] or variant != old["variant_hint"]))
    stamp = backend.now()
    revision = int(old["revision"]) + int(changed) if old else 1
    conn.execute("""INSERT INTO price_tracking(listing_id,source_url,region,variant_hint,revision,updated_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET source_url=excluded.source_url,
        region=excluded.region,variant_hint=excluded.variant_hint,revision=excluded.revision,updated_at=excluded.updated_at""",
        (listing["id"], source, region, variant, revision, stamp))
    if changed:
        conn.execute("""UPDATE price_tracking SET current_price='',current_currency='',last_success_at='',
            last_checked_at='',last_success_source_url='',last_success_context='',source='',evidence='',title='',error=''
            WHERE listing_id=?""", (listing["id"],))


def _target(listing, tracked=None) -> dict:
    result = {"id": listing["id"], "listing_id": listing["id"], "product_id": listing["product_id"],
            "platform": listing["platform"], "store": listing["store"], "product_code": listing["product_code"],
            "product_number": listing["product_number"], "original_price": listing["price"], "currency": listing["currency"],
            "source_url": tracked["source_url"] if tracked else listing["link"],
            "region": tracked["region"] if tracked else "", "variant_hint": tracked["variant_hint"] if tracked else "",
            "enabled": bool(tracked["enabled"]) if tracked else False,
            "revision": tracked["revision"] if tracked else 0, "configured": tracked is not None}
    identity = {key: result[key] for key in ("id", "revision", "platform", "store", "product_code", "product_number",
        "currency", "original_price", "source_url", "region", "variant_hint")}
    result["context_token"] = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def _context(target: dict) -> str:
    data = {key: target.get(key, "") for key in ("source_url", "region", "variant_hint", "currency")}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _view(listing, tracked=None) -> dict:
    target = _target(listing, tracked)
    fields = ("current_price", "current_currency", "last_success_at", "last_checked_at", "last_success_source_url", "error", "source", "evidence", "title")
    result = {**target, **{key: tracked[key] if tracked else "" for key in fields}}
    success_matches = bool(tracked and tracked["current_price"] and tracked["last_success_context"] == _context(target))
    ratio = None
    conversion = None
    comparable = result["current_price"]
    if success_matches and target["currency"] != result["current_currency"]:
        conversion = exchange_rates.convert_cached(result["current_price"], result["current_currency"], target["currency"])
        comparable = conversion["converted_amount"] if conversion.get("available") and conversion.get("status") == "ok" else None
    if success_matches and target["original_price"] and comparable is not None:
        original = Decimal(target["original_price"])
        if original > 0:
            with localcontext() as calculation:
                calculation.prec = 60
                ratio = format((Decimal(comparable) / original * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")
    expired = False
    if result["last_success_at"]:
        try:
            expired = datetime.now(timezone.utc) - datetime.fromisoformat(result["last_success_at"]) > timedelta(hours=26)
        except ValueError:
            expired = True
    stale = bool(result["current_price"] and (not success_matches or result["error"] or expired))
    if not target["source_url"] and not result["current_price"]:
        status = "unconfigured"
    elif not result["current_price"]:
        status = "error" if result["error"] else "never"
    elif not success_matches or stale:
        status = "stale"
    elif target["currency"] != result["current_currency"] and not (conversion and conversion.get("available")):
        status = "currency_mismatch"
    else:
        status = "ok"
    result.update(ratio_percent=ratio, status=status, stale=stale, fx=conversion)
    return result


def get_product_tracking(product_id: str, user: dict) -> dict:
    with backend._connection() as conn:
        product = backend._product(conn, product_id)
        rows = conn.execute("SELECT * FROM product_listings WHERE product_id=? ORDER BY position,id", (product_id,)).fetchall()
        return {"product_id": product_id, "can_edit": _can_edit(product, user),
                "listings": [_view(row, _tracking(conn, row["id"])) for row in rows]}


def get_target(listing_id: str, user: dict | None = None) -> dict:
    with backend._connection() as conn:
        listing, product = _listing(conn, listing_id)
        if user is not None:
            backend._may_edit(product, user)
        return _target(listing, _tracking(conn, listing_id))


def eligible_targets() -> list[dict]:
    with backend._connection() as conn:
        listings = conn.execute("""SELECT l.* FROM product_listings l JOIN products p ON p.id=l.product_id
            JOIN price_tracking t ON t.listing_id=l.id WHERE p.deleted=0 AND t.source_url<>''
            ORDER BY p.id,l.position,l.id""").fetchall()
        return [_target(row, _tracking(conn, row["id"])) for row in listings]


def _validate_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(400, "请填写有效的公开商品前端链接。")
    value = value.strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme not in {"https", "http"} or not host or parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError
        if parts.port not in {None, 80 if parts.scheme == "http" else 443} or "\\" in value:
            raise ValueError
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or any(c in host for c in "% "):
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
    except ValueError:
        raise HTTPException(400, "仅支持不含登录凭证的公开商品 HTTP(S) 链接；不能使用本机、内网地址或非标准端口。") from None
    return value


def _price(value: Any, *, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise HTTPException(400, "价格格式不正确。")
    value = str(value).strip()
    if not value and allow_empty:
        return ""
    if not re.fullmatch(r"\d{1,9}(?:\.\d{1,4})?", value) or Decimal(value) <= 0:
        raise HTTPException(400, "价格须大于 0，最多 9 位整数和 4 位小数；原定价尚未确定可留空。")
    return value


def _identity(listing, payload: dict) -> dict:
    result = {key: listing[key] for key in IDENTITY_FIELDS}
    for key in IDENTITY_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        limit = 3 if key == "currency" else 120
        if not isinstance(value, str) or len(value) > limit:
            raise HTTPException(400, "SKU、SKC 或币种格式不正确。")
        result[key] = value.strip()
    result["currency"] = result["currency"].upper()
    if not re.fullmatch(r"[A-Z]{3}", result["currency"]):
        raise HTTPException(400, "币种需要 3 位字母，例如 CNY 或 USD。")
    return result


def _check_duplicate_sku(conn, listing, identity: dict, *, exclude_id: str = "") -> None:
    if not identity["product_code"]:
        return
    duplicate = conn.execute("""SELECT id FROM product_listings WHERE product_id=? AND platform=?
        AND store=? AND product_number=? AND product_code=? AND id<>? LIMIT 1""",
        (listing["product_id"], listing["platform"], listing["store"], identity["product_number"],
         identity["product_code"], exclude_id)).fetchone()
    if duplicate:
        raise HTTPException(409, "同一平台、店铺和 SKC 下已存在这个 SKU，请编辑已有记录。")


def add_listing_variant(listing_id: str, payload: Any, user: dict) -> dict:
    """Append an independent SKU without copying the parent's observed price."""
    allowed = {"product_code", "variant_hint", "original_price", "currency"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise HTTPException(400, "SKU 资料字段不正确。")
    with backend._connection(write=True) as conn:
        listing, product = _listing(conn, listing_id)
        backend._may_edit(product, user)
        identity = _identity(listing, {"product_code": payload.get("product_code", ""),
                                       "currency": payload.get("currency", listing["currency"])})
        if not identity["product_code"]:
            raise HTTPException(400, "请填写新变体的 SKU。")
        variant = payload.get("variant_hint", "")
        if not isinstance(variant, str) or len(variant) > 200 or not variant.strip():
            raise HTTPException(400, "请填写具体规格，最多 200 个字符。")
        base = _price(payload.get("original_price", ""), allow_empty=True)
        _check_duplicate_sku(conn, listing, identity)
        count = conn.execute("SELECT COUNT(*) FROM product_listings WHERE product_id=?", (product["id"],)).fetchone()[0]
        if count >= 100:
            raise HTTPException(400, "每个商品最多 100 条平台或 SKU 记录。")
        parent = _target(listing, _tracking(conn, listing_id))
        source_url = _validate_url(parent["source_url"])
        new_id = uuid.uuid4().hex
        position = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM product_listings WHERE product_id=?", (product["id"],)).fetchone()[0]
        conn.execute("""INSERT INTO product_listings(id,product_id,platform,store,product_code,product_number,
            status,price,currency,link,position) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id, product["id"], listing["platform"], listing["store"], identity["product_code"],
             listing["product_number"], "pending", base, identity["currency"], source_url, position))
        stamp = backend.now()
        conn.execute("""INSERT INTO price_tracking(listing_id,source_url,region,variant_hint,enabled,revision,updated_at)
            VALUES(?,?,?,?,?,1,?)""", (new_id, source_url, parent["region"], variant.strip(),
                                      int(parent["enabled"] and bool(source_url)), stamp))
        conn.execute("UPDATE products SET listings_initialized=1,updated_at=? WHERE id=?", (stamp, product["id"]))
        backend._audit(conn, user, "price_variant_add", "新增价格追踪 SKU：" + product["name"] + " · " + identity["product_code"], product["id"])
        added, _ = _listing(conn, new_id)
        return _view(added, _tracking(conn, new_id))


def save_listing_tracking(listing_id: str, payload: Any, user: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - set(CONFIG_FIELDS) - set(IDENTITY_FIELDS) - {"original_price"}:
        raise HTTPException(400, "价格追踪资料字段不正确。")
    with backend._connection(write=True) as conn:
        listing, product = _listing(conn, listing_id)
        backend._may_edit(product, user)
        old = _tracking(conn, listing_id)
        target = _target(listing, old)
        identity = _identity(listing, payload)
        identity_changed = any(identity[key] != listing[key] for key in IDENTITY_FIELDS)
        if identity_changed:
            _check_duplicate_sku(conn, listing, identity, exclude_id=listing_id)
        config = {key: target[key] for key in CONFIG_FIELDS}
        config.update({key: payload[key] for key in CONFIG_FIELDS if key in payload})
        config["source_url"] = _validate_url(config["source_url"])
        for field, limit in (("region", 80), ("variant_hint", 200)):
            if not isinstance(config[field], str) or len(config[field]) > limit:
                raise HTTPException(400, "地区或规格说明过长或格式不正确。")
            config[field] = config[field].strip()
        if type(config["enabled"]) is not bool:
            raise HTTPException(400, "价格追踪开关须为 true 或 false。")
        if config["enabled"] and not config["source_url"]:
            raise HTTPException(400, "启用追踪前请填写商品前端链接。")
        if "original_price" in payload:
            base = _price(payload["original_price"], allow_empty=True)
            conn.execute("UPDATE product_listings SET price=? WHERE id=?", (base, listing_id))
        if identity_changed:
            conn.execute("UPDATE product_listings SET product_code=?,product_number=?,currency=? WHERE id=?",
                         (identity["product_code"], identity["product_number"], identity["currency"], listing_id))
        changed = old is not None and (identity_changed or any(config[key] != target[key] for key in CONFIG_FIELDS))
        context_changed = old is not None and (identity_changed or any(config[key] != target[key] for key in ("source_url", "region", "variant_hint")))
        revision = int(target["revision"]) + (1 if changed or not old else 0)
        stamp = backend.now()
        conn.execute("""INSERT INTO price_tracking(listing_id,source_url,region,variant_hint,enabled,revision,updated_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(listing_id) DO UPDATE SET source_url=excluded.source_url,
            region=excluded.region,variant_hint=excluded.variant_hint,enabled=excluded.enabled,
            revision=excluded.revision,updated_at=excluded.updated_at""",
                     (listing_id, config["source_url"], config["region"], config["variant_hint"], int(config["enabled"]), revision, stamp))
        if context_changed:
            conn.execute("""UPDATE price_tracking SET current_price='',current_currency='',last_success_at='',
                last_checked_at='',last_success_source_url='',last_success_context='',source='',evidence='',title='',error=''
                WHERE listing_id=?""", (listing_id,))
        conn.execute("UPDATE products SET updated_at=? WHERE id=?", (stamp, product["id"]))
        backend._audit(conn, user, "price_tracking_update", "更新平台价格追踪：" + product["name"] + " · " + listing["platform"], product["id"])
        updated_listing, _ = _listing(conn, listing_id)
        return _view(updated_listing, _tracking(conn, listing_id))


def record_attempt(listing_id: str, result: dict | None = None, error: str = "", *, expected_target: dict | None = None) -> bool:
    """Save success or failure atomically; never apply an obsolete URL response."""
    if result is not None and error:
        raise ValueError("A price attempt cannot succeed and fail at the same time")
    if result is None and not error:
        raise ValueError("A price attempt needs a result or an error")
    if not isinstance(error, str):
        raise ValueError("Price attempt error must be text")
    data = None
    if result is not None:
        if not isinstance(result, dict):
            raise HTTPException(400, "价格采集结果格式不正确。")
        currency = result.get("currency", "")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
            raise HTTPException(400, "采集结果缺少有效币种。")
        data = {"price": _price(result.get("price")), "currency": currency.upper()}
        for key, limit in (("source", 80), ("evidence", 2000), ("title", 500)):
            value = result.get(key, "")
            if not isinstance(value, str):
                raise HTTPException(400, "价格采集结果格式不正确。")
            data[key] = value[:limit]
    with backend._connection(write=True) as conn:
        try:
            listing, _ = _listing(conn, listing_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return False
            raise
        tracked = _tracking(conn, listing_id)
        if not tracked:
            return False
        current = _target(listing, tracked)
        if expected_target is not None and any(current.get(key) != expected_target.get(key) for key in ("listing_id", "revision", "source_url", "region", "variant_hint", "currency", "product_code", "product_number", "platform", "store")):
            return False
        stamp = backend.now()
        if data:
            conn.execute("""UPDATE price_tracking SET current_price=?,current_currency=?,last_success_at=?,last_checked_at=?,
                last_success_source_url=?,last_success_context=?,source=?,evidence=?,title=?,error='',updated_at=? WHERE listing_id=?""",
                         (data["price"], data["currency"], stamp, stamp, current["source_url"], _context(current), data["source"], data["evidence"], data["title"], stamp, listing_id))
        else:
            conn.execute("UPDATE price_tracking SET last_checked_at=?,error=?,updated_at=? WHERE listing_id=?",
                         (stamp, error[:1000], stamp, listing_id))
        conn.execute("""INSERT INTO price_history(listing_id,checked_at,source_url,region,variant_hint,price,currency,source,evidence,error)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (listing_id, stamp, current["source_url"], current["region"], current["variant_hint"],
                                               data["price"] if data else "", data["currency"] if data else "", data["source"] if data else "",
                                               data["evidence"] if data else "", error[:1000]))
        conn.execute("DELETE FROM price_history WHERE listing_id=? AND id NOT IN (SELECT id FROM price_history WHERE listing_id=? ORDER BY id DESC LIMIT 90)", (listing_id, listing_id))
    return True


def save_manual_price(listing_id: str, payload: Any, user: dict) -> dict:
    """Record a user-entered front-end quote atomically, independent of the base."""
    if not isinstance(payload, dict) or set(payload) != {"price", "currency", "expected_context"}:
        raise HTTPException(400, "请填写前端价格、币种，并提供当前 SKU 的版本。")
    amount = _price(payload["price"])
    currency = payload["currency"]
    token = payload["expected_context"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
        raise HTTPException(400, "币种需要 3 位字母，例如 CNY 或 USD。")
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
        raise HTTPException(400, "SKU 版本无效，请刷新价格弹窗后重试。")
    currency = currency.upper()
    with backend._connection(write=True) as conn:
        listing, product = _listing(conn, listing_id)
        backend._may_edit(product, user)
        tracked = _tracking(conn, listing_id)
        target = _target(listing, tracked)
        if target["context_token"] != token:
            raise HTTPException(409, "SKU、链接或价格资料已变化，请刷新弹窗后重新填写。")
        stamp = backend.now()
        if tracked is None:
            conn.execute("""INSERT INTO price_tracking(listing_id,source_url,revision,updated_at) VALUES(?,?,0,?)""",
                         (listing_id, target["source_url"], stamp))
            tracked = _tracking(conn, listing_id)
            target = _target(listing, tracked)
        evidence = "用户手动录入前端价格：" + currency + " " + amount
        conn.execute("""UPDATE price_tracking SET current_price=?,current_currency=?,last_success_at=?,last_checked_at=?,
            last_success_source_url=?,last_success_context=?,source='manual_entry',evidence=?,title='',error='',revision=revision+1,updated_at=? WHERE listing_id=?""",
            (amount, currency, stamp, stamp, target["source_url"], _context(target), evidence, stamp, listing_id))
        conn.execute("""INSERT INTO price_history(listing_id,checked_at,source_url,region,variant_hint,price,currency,source,evidence,error)
            VALUES(?,?,?,?,?,?,?,'manual_entry',?,'')""",
            (listing_id, stamp, target["source_url"], target["region"], target["variant_hint"], amount, currency, evidence))
        conn.execute("DELETE FROM price_history WHERE listing_id=? AND id NOT IN (SELECT id FROM price_history WHERE listing_id=? ORDER BY id DESC LIMIT 90)", (listing_id, listing_id))
        backend._audit(conn, user, "price_manual_entry", "手动记录前端价格：" + product["name"] + " · " + listing["product_code"] + " · " + currency + " " + amount, product["id"])
        return _view(listing, _tracking(conn, listing_id))


def get_history(listing_id: str, user: dict, limit: int = 30) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 90:
        raise HTTPException(400, "价格历史最多查看 90 次记录。")
    with backend._connection() as conn:
        listing, product = _listing(conn, listing_id)
        rows = conn.execute("SELECT checked_at,source_url,region,variant_hint,price,currency,source,evidence,error FROM price_history WHERE listing_id=? ORDER BY id DESC LIMIT ?", (listing_id, limit)).fetchall()
        return {"listing_id": listing_id, "product_id": product["id"], "items": [dict(row) for row in rows]}
