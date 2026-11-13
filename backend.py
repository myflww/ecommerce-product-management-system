"""Shared local product library. No cloud calls or seeded product records."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import uuid
import zipfile
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

from image_engine import compare_features, extract_features, make_thumbnail

try:
    import semantic_engine
except ImportError:
    semantic_engine = None

try:
    from image_engine import fast_compare_features
except ImportError:
    fast_compare_features = compare_features

VERSION = "1.6.0"
DATA_DIR: Path | None = None
DB_PATH: Path | None = None
COOKIE_NAME = "midoo_session"
MAX_IMAGE_SIZE = 20 * 1024 * 1024
MAX_BATCH_SIZE = 250 * 1024 * 1024
MAX_BATCH_IMAGES = 300
SESSION_DAYS = 7
_login_attempts: dict[str, deque] = defaultdict(deque)
_login_lock = threading.Lock()
_backup_extras: dict[str, Path] = {}
_index_lock = threading.RLock()
_index_jobs: dict[str, dict] = {}
# Cosine cutoffs are retrieval gates, not SKU probabilities. Keep them separately
# tunable from the obsolete handcrafted appearance score.
SEMANTIC_MIN_SIMILARITY = 0.65
SEMANTIC_RELATIVE_GAP = 0.12
SEMANTIC_WEAK_MIN_SIMILARITY = 0.45
SEMANTIC_WEAK_RELATIVE_GAP = 0.20


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def init_storage(data_dir: Path) -> None:
    global DATA_DIR, DB_PATH
    DATA_DIR = Path(data_dir).resolve()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for child in ("images", "thumbnails", "backups"):
        (DATA_DIR / child).mkdir(exist_ok=True)
    DB_PATH = DATA_DIR / "library.db"
    with _connection(write=True) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY, created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            owner TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            category TEXT NOT NULL DEFAULT '',
            specs TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL REFERENCES users(id), batch_id TEXT REFERENCES batches(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
            name TEXT NOT NULL, filename TEXT NOT NULL, thumb_filename TEXT NOT NULL,
            media_type TEXT NOT NULL, sha256 TEXT NOT NULL, features TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS images_product_idx ON images(product_id);
        CREATE INDEX IF NOT EXISTS images_sha_idx ON images(sha256);
        CREATE TABLE IF NOT EXISTS image_embeddings (
            image_id TEXT NOT NULL REFERENCES images(id), model_id TEXT NOT NULL,
            sha256 TEXT NOT NULL, embedding TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(image_id, model_id)
        );
        CREATE INDEX IF NOT EXISTS image_embeddings_sha_idx ON image_embeddings(model_id,sha256);
        CREATE INDEX IF NOT EXISTS products_updated_idx ON products(deleted, updated_at DESC);
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
            decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', query_fingerprint TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS decisions_lookup_idx ON decisions(query_fingerprint,product_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT REFERENCES users(id),
            actor TEXT NOT NULL, action TEXT NOT NULL, description TEXT NOT NULL,
            product_id TEXT, batch_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS product_listings (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
            platform TEXT NOT NULL, store TEXT NOT NULL DEFAULT '',
            product_code TEXT NOT NULL DEFAULT '', product_number TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending', price TEXT NOT NULL DEFAULT '',
            currency TEXT NOT NULL DEFAULT 'CNY', link TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS listings_product_idx ON product_listings(product_id,position);
        CREATE INDEX IF NOT EXISTS listings_platform_idx ON product_listings(platform,status);
        """)
        # Existing records retain their historical global status until an operator
        # supplies platform records. Never invent platforms or prices on upgrade.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        if "listings_initialized" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN listings_initialized INTEGER NOT NULL DEFAULT 0")
        if "category" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''")


def get_connection() -> sqlite3.Connection:
    if DB_PATH is None:
        raise RuntimeError("请先调用 init_storage 初始化商品库")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def _connection(write: bool = False):
    conn = get_connection()
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _public_user(row) -> dict:
    return {k: row[k] for k in ("id", "name", "role")}


def authenticate_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 200:
        return None
    with _connection() as conn:
        row = conn.execute("""SELECT u.id,u.name,u.role FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>?""", (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
    return _public_user(row) if row else None


def check_origin(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从本软件页面执行操作")
    origin = request.headers.get("origin")
    if origin:
        actual = urlsplit(origin)
        if actual.scheme != request.url.scheme or actual.netloc.lower() != request.url.netloc.lower():
            raise HTTPException(403, "已拒绝跨站操作，请从本软件页面重试")


def require_user(request: Request) -> dict | None:
    check_origin(request)
    user = authenticate_request(request)
    if request.url.path in {"/api/bootstrap", "/api/setup", "/api/login"}:
        return user
    if user is None:
        raise HTTPException(401, "请先登录")
    request.state.user = user
    return user


def _user(request: Request) -> dict:
    return request.state.user


def _admin(user: dict) -> None:
    if user["role"] != "admin":
        raise HTTPException(403, "此操作需要管理员权限")


def _may_edit(product, user: dict) -> None:
    if user["role"] != "admin" and product["created_by"] != user["id"] and product["owner"] != user["name"]:
        raise HTTPException(403, "只有管理员、录入人或商品负责人可以修改此商品")


def _password_hash(password: str) -> str:
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise HTTPException(400, "密码需要 8–128 个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260000).hex()
    return f"pbkdf2_sha256$260000${salt}${digest}"


def _password_matches(password: str, stored: str) -> bool:
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        _, rounds, salt, expected = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(expected, actual)
    except (ValueError, TypeError):
        return False


def _name(value: Any) -> str:
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= 40:
        raise HTTPException(400, "成员名称需要 2–40 个字符")
    return value.strip()


def _session(conn, user_id: str, response: Response, request: Request) -> None:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="microseconds")
    conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now(),))
    conn.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/")


def _audit(conn, user, action, description, product_id=None, batch_id=None):
    conn.execute("""INSERT INTO activity(user_id,actor,action,description,product_id,batch_id,created_at)
                    VALUES(?,?,?,?,?,?,?)""", (user["id"], user["name"], action, description, product_id, batch_id, now()))


router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/api/bootstrap")
def bootstrap():
    with _connection() as conn:
        return {"needs_setup": not bool(conn.execute("SELECT 1 FROM users LIMIT 1").fetchone())}


@router.post("/api/setup")
def setup(request: Request, response: Response, payload: dict = Body(...)):
    name = _name(payload.get("name"))
    password_hash = _password_hash(payload.get("password"))
    with _connection(write=True) as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(409, "管理员已创建，请直接登录")
        user = {"id": uuid.uuid4().hex, "name": name, "role": "admin"}
        conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user["id"], name, password_hash, "admin", now()))
        _session(conn, user["id"], response, request)
        _audit(conn, user, "setup", "创建公司商品库")
    return {"user": user}


@router.post("/api/login")
def login(request: Request, response: Response, payload: dict = Body(...)):
    name = _name(payload.get("name"))
    key = (request.client.host if request.client else "local") + ":" + name.casefold()
    with _login_lock:
        attempts = _login_attempts[key]
        while attempts and time.monotonic() - attempts[0] > 300:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "尝试次数过多，请 5 分钟后重试")
        attempts.append(time.monotonic())
    with _connection(write=True) as conn:
        row = conn.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        stored = row["password_hash"] if row else "pbkdf2_sha256$260000$00000000000000000000000000000000$" + "0" * 64
        valid = _password_matches(payload.get("password"), stored)
        if not row or not valid:
            raise HTTPException(401, "成员名称或密码不正确")
        user = _public_user(row)
        _session(conn, user["id"], response, request)
    with _login_lock:
        _login_attempts.pop(key, None)
    return {"user": user}


@router.post("/api/logout")
def logout(request: Request, response: Response):
    with _connection(write=True) as conn:
        token_hash = hashlib.sha256(request.cookies.get(COOKIE_NAME, "").encode()).hexdigest()
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(request: Request):
    return {"user": _user(request)}


@router.put("/api/me/password")
def change_password(request: Request, response: Response, payload: dict = Body(...)):
    user = _user(request)
    hashed = _password_hash(payload.get("new_password"))
    with _connection(write=True) as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not _password_matches(payload.get("current_password"), row["password_hash"]):
            raise HTTPException(400, "当前密码不正确")
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        _session(conn, user["id"], response, request)
        _audit(conn, user, "password", "更新登录密码，其他设备需要重新登录")
    return {"ok": True}


def _product(conn, product_id: str, include_deleted=False) -> sqlite3.Row:
    sql = "SELECT * FROM products WHERE id=?" + ("" if include_deleted else " AND deleted=0")
    row = conn.execute(sql, (product_id,)).fetchone()
    if not row:
        raise HTTPException(404, "商品不存在或已移除")
    return row


def _serialize_product(conn, row) -> dict:
    item = {k: row[k] for k in ("id", "code", "name", "owner", "status", "category", "specs", "notes", "link", "created_at", "updated_at", "created_by", "batch_id")}
    item["listings"] = [dict(listing) for listing in conn.execute(
        "SELECT id,platform,store,product_code,product_number,status,price,currency,link FROM product_listings WHERE product_id=? ORDER BY position,id", (row["id"],))]
    has_tracking = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_tracking'").fetchone()
    tracked = {entry["listing_id"]: entry for entry in conn.execute(
        "SELECT t.listing_id,t.source_url,t.region,t.variant_hint FROM price_tracking t JOIN product_listings l ON l.id=t.listing_id WHERE l.product_id=?", (row["id"],))} if has_tracking else {}
    for listing in item["listings"]:
        config = tracked.get(listing["id"])
        if config:
            listing["variant_hint"] = config["variant_hint"]
            listing["region"] = config["region"]
            listing["link"] = config["source_url"]
    item["workflow_state"] = _workflow_state(row, item["listings"])
    item["workflow_states"] = _workflow_memberships(row, item["listings"])
    item["images"] = [{"id": im["id"], "url": f"/media/{im['id']}", "thumb_url": f"/media/{im['id']}?thumb=1", "name": im["name"]}
                      for im in conn.execute("SELECT id,name FROM images WHERE product_id=? ORDER BY created_at,id", (row["id"],))]
    return item


WORKFLOW_STATES = ("unconfigured", "pricing", "pending", "listed", "archived", "legacy")


def _workflow_state(product, listings: list[dict]) -> str:
    if not listings:
        return "unconfigured" if product["listings_initialized"] else "legacy"
    # Availability is an operator's confirmation, independent of whether the
    # baseline price or a storefront link has been supplied.
    if any(item["status"] == "listed" for item in listings):
        return "listed"
    active = [item for item in listings if item["status"] != "archived"]
    if any(not item["price"] for item in active):
        return "pricing"
    return "pending" if active else "archived"


def _workflow_sql(alias="p") -> str:
    """Keep list filters/counts aligned with the per-product summary above."""
    scope = f"l.product_id={alias}.id"
    return f"""CASE
        WHEN EXISTS(SELECT 1 FROM product_listings l WHERE {scope} AND l.status='listed') THEN 'listed'
        WHEN EXISTS(SELECT 1 FROM product_listings l WHERE {scope} AND l.status<>'archived' AND l.price='') THEN 'pricing'
        WHEN EXISTS(SELECT 1 FROM product_listings l WHERE {scope} AND l.status<>'archived') THEN 'pending'
        WHEN EXISTS(SELECT 1 FROM product_listings l WHERE {scope}) THEN 'archived'
        WHEN {alias}.listings_initialized=1 THEN 'unconfigured'
        ELSE 'legacy' END"""


def _workflow_memberships(product, listings: list[dict]) -> list[str]:
    """Operational queues overlap; old global status is never a platform fact."""
    missing_details = not listings or any(item["status"] != "archived" and not item["price"] for item in listings)
    memberships = []
    if missing_details:
        memberships.extend(("unconfigured", "pricing"))
    if not listings or any(item["status"] == "pending" for item in listings):
        memberships.append("pending")
    primary = _workflow_state(product, listings)
    if primary in {"listed", "archived"}:
        memberships.append(primary)
    return memberships


def _workflow_filter_sql(workflow: str, alias="p") -> str:
    scope = f"l.product_id={alias}.id"
    missing = f"(NOT EXISTS(SELECT 1 FROM product_listings l WHERE {scope}) OR EXISTS(SELECT 1 FROM product_listings l WHERE {scope} AND l.status<>'archived' AND l.price=''))"
    if workflow in {"unconfigured", "pricing"}:
        return missing
    if workflow == "pending":
        return f"(NOT EXISTS(SELECT 1 FROM product_listings l WHERE {scope}) OR EXISTS(SELECT 1 FROM product_listings l WHERE {scope} AND l.status='pending'))"
    return f"({_workflow_sql(alias)})='{workflow}'"


def _stats(conn) -> dict:
    primary_counts = dict.fromkeys(WORKFLOW_STATES, 0)
    for row in conn.execute(f"SELECT {_workflow_sql()} AS workflow,COUNT(*) AS count FROM products p WHERE p.deleted=0 GROUP BY workflow"):
        primary_counts[row["workflow"]] = row["count"]
    counts = {workflow: conn.execute(f"SELECT COUNT(*) FROM products p WHERE p.deleted=0 AND {_workflow_filter_sql(workflow)}").fetchone()[0]
              for workflow in WORKFLOW_STATES}
    return {
        "total_products": conn.execute("SELECT COUNT(*) FROM products WHERE deleted=0").fetchone()[0],
        "total_images": conn.execute("SELECT COUNT(*) FROM images i JOIN products p ON p.id=i.product_id WHERE p.deleted=0").fetchone()[0],
        "pending_products": counts["pending"],
        "workflow_counts": counts,
        "workflow_primary_counts": primary_counts,
        "workflow_categories_overlap": True,
        "platforms": [row[0] for row in conn.execute("SELECT DISTINCT l.platform FROM product_listings l JOIN products p ON p.id=l.product_id WHERE p.deleted=0 ORDER BY l.platform COLLATE NOCASE")],
        "recent_imports": conn.execute("SELECT COUNT(*) FROM batches WHERE undone=0 AND created_at>?", ((datetime.now(timezone.utc)-timedelta(days=7)).isoformat(),)).fetchone()[0],
        "last_updated": conn.execute("SELECT MAX(updated_at) FROM products WHERE deleted=0").fetchone()[0],
    }


@router.get("/api/stats")
def stats():
    with _connection() as conn:
        return _stats(conn)


@router.get("/api/categories")
def categories():
    """User-entered merchandise categories, separate from workflow queues."""
    with _connection() as conn:
        items = [dict(row) for row in conn.execute("""SELECT category AS name,COUNT(*) AS count
            FROM products WHERE deleted=0 AND category<>'' GROUP BY category ORDER BY category COLLATE NOCASE,category""")]
        uncategorized = conn.execute("SELECT COUNT(*) FROM products WHERE deleted=0 AND category=''").fetchone()[0]
        return {"items": items, "uncategorized_count": uncategorized,
                "total": uncategorized + sum(item["count"] for item in items)}


@router.get("/api/products")
def products(q: str = Query("", max_length=500), status: str = "", owner: str = "", limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0),
             workflow_state: str = "", platform: str = Query("", max_length=60), listing_status: str = "",
             category: str = Query("", max_length=60), uncategorized: bool = False):
    where, args = ["p.deleted=0"], []
    category = _category_name(category)
    if category:
        where.append("p.category=?")
        args.append(category)
    if uncategorized:
        where.append("p.category=''")
    if q.strip():
        where.append("(p.name LIKE ? ESCAPE '\\' OR p.code LIKE ? ESCAPE '\\' OR p.specs LIKE ? ESCAPE '\\' OR p.notes LIKE ? ESCAPE '\\' OR p.owner LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM product_listings l WHERE l.product_id=p.id AND (l.platform LIKE ? ESCAPE '\\' OR l.store LIKE ? ESCAPE '\\' OR l.product_code LIKE ? ESCAPE '\\' OR l.product_number LIKE ? ESCAPE '\\')))")
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args.extend([pattern] * 9)
    if status:
        if status not in {"pending", "listed", "archived"}:
            raise HTTPException(400, "商品状态不正确")
        where.append("p.status=?")
        args.append(status)
    if workflow_state:
        if workflow_state not in WORKFLOW_STATES:
            raise HTTPException(400, "商品运营分类不正确")
        where.append(_workflow_filter_sql(workflow_state))
    if platform or listing_status:
        listing_conditions = ["l.product_id=p.id"]
        if platform:
            listing_conditions.append("l.platform=? COLLATE NOCASE")
            args.append(platform.strip())
        if listing_status:
            if listing_status not in {"pending", "listed", "archived"}:
                raise HTTPException(400, "平台上架状态不正确")
            listing_conditions.append("l.status=?")
            args.append(listing_status)
        where.append("EXISTS(SELECT 1 FROM product_listings l WHERE " + " AND ".join(listing_conditions) + ")")
    if owner:
        where.append("p.owner=?")
        args.append(owner)
    condition = " AND ".join(where)
    with _connection() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM products p WHERE {condition}", args).fetchone()[0]
        rows = conn.execute(f"SELECT p.* FROM products p WHERE {condition} ORDER BY p.updated_at DESC,p.id LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"items": [_serialize_product(conn, row) for row in rows], "total": total}


@router.get("/api/products/{product_id}")
def get_product(product_id: str):
    with _connection() as conn:
        return _serialize_product(conn, _product(conn, product_id))


def _category_name(value: Any) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f-\x9f]", value):
        raise HTTPException(400, "商品分类须为文字，不能包含换行或控制字符")
    value = value.strip()
    if len(value) > 60:
        raise HTTPException(400, "商品分类最多 60 个字符")
    return value


def _metadata(raw: Any, user: dict, partial=False) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(400, "商品信息格式不正确")
    limits = {"name": 160, "owner": 80, "specs": 3000, "notes": 6000, "link": 2000, "status": 20}
    result = {}
    if "category" in raw or not partial:
        result["category"] = _category_name(raw.get("category", ""))
    for key, maximum in limits.items():
        if key not in raw and partial:
            continue
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > maximum:
            raise HTTPException(400, f"{key} 字段过长或格式不正确")
        result[key] = value.strip()
    if not partial:
        result["owner"] = result["owner"] or user["name"]
        result["status"] = result["status"] or "pending"
        result["name"] = result["name"] or "未命名商品"
    if "name" in result and not result["name"]:
        raise HTTPException(400, "商品名称不能为空")
    if "owner" in result and not result["owner"]:
        raise HTTPException(400, "商品负责人不能为空")
    if "status" in result and result["status"] not in {"pending", "listed", "archived"}:
        raise HTTPException(400, "商品状态不正确")
    if result.get("link") and urlsplit(result["link"]).scheme not in {"http", "https"}:
        raise HTTPException(400, "商品链接需要以 http:// 或 https:// 开头")
    if "listings" in raw or not partial:
        result["listings"] = _listing_metadata(raw.get("listings", []))
    return result


def _listing_metadata(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 100:
        raise HTTPException(400, "平台记录须为列表，每个商品最多 100 条")
    normalized, ids = [], set()
    limits = {"platform": 60, "store": 80, "product_code": 120, "product_number": 120, "status": 20, "currency": 3, "link": 2000}
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise HTTPException(400, f"第 {index + 1} 条平台记录格式不正确")
        item = {}
        for key, maximum in limits.items():
            field = value.get(key, "pending" if key == "status" else "CNY" if key == "currency" else "")
            if not isinstance(field, str) or len(field) > maximum:
                raise HTTPException(400, f"平台记录的 {key} 字段过长或格式不正确")
            item[key] = field.strip()
        if not item["platform"]:
            raise HTTPException(400, "每条平台记录都需要填写平台名称")
        if item["status"] not in {"pending", "listed", "archived"}:
            raise HTTPException(400, "平台状态须为准备上架、已上架或已下架")
        item["currency"] = item["currency"].upper()
        if not re.fullmatch(r"[A-Z]{3}", item["currency"]):
            raise HTTPException(400, "币种需要 3 位字母，例如 CNY 或 USD")
        if item["link"] and urlsplit(item["link"]).scheme not in {"http", "https"}:
            raise HTTPException(400, "平台商品链接需要以 http:// 或 https:// 开头")
        price = value.get("price", "")
        if price is None:
            price = ""
        if isinstance(price, bool) or not isinstance(price, (str, int, float)):
            raise HTTPException(400, "上架价格格式不正确")
        price = str(price).strip()
        if price and (not re.fullmatch(r"\d{1,9}(?:\.\d{1,4})?", price) or Decimal(price) <= 0):
            raise HTTPException(400, "已确定的上架价格须大于 0，最多 9 位整数和 4 位小数；未定价请留空")
        item["price"] = price
        for key, maximum in (("variant_hint", 200), ("region", 80)):
            if key in value:
                field = value[key]
                if not isinstance(field, str) or len(field) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in field):
                    raise HTTPException(400, "SKU 规格或目标地区格式不正确或过长")
                item[key] = field.strip()
        listing_id = value.get("id")
        if listing_id is not None:
            if not isinstance(listing_id, str) or not re.fullmatch(r"[a-f0-9]{32}", listing_id) or listing_id in ids:
                raise HTTPException(400, "平台记录编号无效或重复")
            ids.add(listing_id)
            item["id"] = listing_id
        normalized.append(item)
    return normalized


def _replace_listings(conn, product_id: str, listings: list[dict]) -> None:
    existing = {row["id"]: row for row in conn.execute("SELECT * FROM product_listings WHERE product_id=?", (product_id,))}
    if any(item.get("id") and item["id"] not in existing for item in listings):
        raise HTTPException(400, "平台记录已失效或不属于此商品，请刷新后重试")
    has_tracking = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_tracking'").fetchone()
    conn.execute("DELETE FROM product_listings WHERE product_id=?", (product_id,))
    for position, item in enumerate(listings):
        old = existing.get(item.get("id"))
        listing_id = item.get("id") or uuid.uuid4().hex
        conn.execute("""INSERT INTO product_listings(id,product_id,platform,store,product_code,product_number,status,price,currency,link,position)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (listing_id, product_id, item["platform"], item["store"], item["product_code"], item["product_number"], item["status"], item["price"], item["currency"], item["link"], position))
        if has_tracking:
            import price_storage
            price_storage.sync_product_listing(conn, {**item, "id": listing_id}, old)
    conn.execute("UPDATE products SET listings_initialized=1 WHERE id=?", (product_id,))


def _json_form(text: str, label: str) -> dict:
    if len(text) > 200000:
        raise HTTPException(400, f"{label}过大")
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        raise HTTPException(400, f"{label}格式不正确")
    if not isinstance(result, dict):
        raise HTTPException(400, f"{label}格式不正确")
    return result


def _semantic_model_status() -> dict:
    """Never claim a semantic search when weights/runtime have not loaded."""
    if semantic_engine is None:
        return {"loaded": False, "available": False, "model_id": "", "error": "本地视觉模型组件尚未安装。"}
    try:
        status = semantic_engine.availability()
        if not isinstance(status, dict):
            raise ValueError("Invalid model status")
        return status
    except Exception:
        return {"loaded": False, "available": False, "model_id": "", "error": "无法读取本地视觉模型状态。"}


def _normalized_embedding(vector) -> list[float]:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not 8 <= array.size <= 8192 or not np.isfinite(array).all():
        raise ValueError("本地视觉特征格式无效")
    norm = float(np.linalg.norm(array))
    if norm < 1e-8:
        raise ValueError("本地视觉特征为空")
    return (array / norm).tolist()


@lru_cache(maxsize=8192)
def _cached_embedding(serialized: str) -> np.ndarray:
    vector = np.asarray(_normalized_embedding(json.loads(serialized)), dtype=np.float32)
    vector.setflags(write=False)
    return vector


def _extract_semantic(content: bytes) -> dict | None:
    status = _semantic_model_status()
    if not status.get("loaded") or not status.get("model_id"):
        return None
    vector = _normalized_embedding(semantic_engine.extract_embedding(content))
    return {"model_id": status["model_id"], "embedding": vector}


def _index_key(database: Path, model_id: str) -> str:
    return str(database.resolve()) + ":" + model_id


def search_index_status() -> dict:
    status = _semantic_model_status()
    model_id = str(status.get("model_id") or "")
    with _connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM images i JOIN products p ON p.id=i.product_id WHERE p.deleted=0").fetchone()[0]
        indexed = conn.execute("""SELECT COUNT(*) FROM images i JOIN products p ON p.id=i.product_id
            JOIN image_embeddings e ON e.image_id=i.id AND e.model_id=? AND e.sha256=i.sha256
            WHERE p.deleted=0""", (model_id,)).fetchone()[0] if model_id else 0
    with _index_lock:
        job = dict(_index_jobs.get(_index_key(DB_PATH, model_id), {}))
    model_ready = bool(status.get("loaded") and status.get("available", True) and model_id)
    running = bool(job.get("running"))
    failed = int(job.get("failed_images", 0))
    if not model_ready:
        state = "unavailable"
        message = "本地视觉模型尚未就绪，目前仅能查找原图一致或此前人工确认的商品。"
    elif indexed < total:
        state = "indexing" if running or not failed else "unavailable"
        message = f"正在为已有图片建立本地视觉索引（{indexed}/{total}），完成前结果可能不完整。" if state == "indexing" else f"有 {total-indexed} 张图片索引尚未完成，请重建索引或检查原图文件。"
    elif running:
        state = "indexing"
        message = "正在更新本地视觉索引，商品资料保持不变。"
    else:
        state = "ready"
        message = "本地视觉模型及商品图片索引已就绪。"
    return {"state": state, "ready": state == "ready", "model_ready": model_ready, "model_id": model_id,
            "model": status.get("model", "本地视觉模型"), "indexed_images": indexed, "total_images": total,
            "indexing": running, "failed_images": failed, "message": message}


@contextmanager
def _index_connection(database: Path):
    conn = sqlite3.connect(database, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _build_search_index(database: Path, directory: Path, model_id: str, force: bool = False) -> None:
    """Derived-only, incremental indexing. Image/product business rows never change."""
    key = _index_key(database, model_id)
    failed = 0
    try:
        with _index_connection(database) as conn:
            rows = conn.execute("""SELECT i.id,i.filename,i.sha256 FROM images i JOIN products p ON p.id=i.product_id
                LEFT JOIN image_embeddings e ON e.image_id=i.id AND e.model_id=?
                WHERE p.deleted=0 AND (? OR e.image_id IS NULL OR e.sha256<>i.sha256)
                ORDER BY i.created_at,i.id""", (model_id, int(force))).fetchall()
        reused: dict[str, str] = {}
        for row in rows:
            try:
                current_status = _semantic_model_status()
                if not current_status.get("loaded") or current_status.get("model_id") != model_id:
                    break
                serialized = reused.get(row["sha256"])
                if serialized is None and not force:
                    with _index_connection(database) as conn:
                        previous = conn.execute("SELECT embedding FROM image_embeddings WHERE model_id=? AND sha256=? LIMIT 1", (model_id, row["sha256"])).fetchone()
                    if previous:
                        _cached_embedding(previous[0])
                        serialized = previous[0]
                if serialized is None:
                    content = (directory / "images" / row["filename"]).read_bytes()
                    if hashlib.sha256(content).hexdigest() != row["sha256"]:
                        raise ValueError("原始图片校验不一致")
                    vector = _normalized_embedding(semantic_engine.extract_embedding(content))
                    serialized = json.dumps(vector, separators=(",", ":"), allow_nan=False)
                with _index_connection(database) as conn:
                    conn.execute("""INSERT INTO image_embeddings(image_id,model_id,sha256,embedding,created_at)
                        SELECT id,?,?,?,? FROM images WHERE id=? AND sha256=?
                        ON CONFLICT(image_id,model_id) DO UPDATE SET sha256=excluded.sha256,
                            embedding=excluded.embedding,created_at=excluded.created_at""",
                        (model_id, row["sha256"], serialized, now(), row["id"], row["sha256"]))
                reused[row["sha256"]] = serialized
            except Exception:
                failed += 1
            with _index_lock:
                _index_jobs[key]["failed_images"] = failed
    except Exception:
        failed += 1
    finally:
        with _index_lock:
            _index_jobs[key].update(running=False, failed_images=failed)


def start_indexing(force: bool = False) -> dict:
    status = _semantic_model_status()
    model_id = str(status.get("model_id") or "")
    if not status.get("loaded") or not model_id:
        return search_index_status()
    database, directory = Path(DB_PATH), Path(DATA_DIR)
    key = _index_key(database, model_id)
    with _index_lock:
        if _index_jobs.get(key, {}).get("running"):
            return search_index_status()
        _index_jobs[key] = {"running": True, "failed_images": 0}
        thread = threading.Thread(target=_build_search_index, args=(database, directory, model_id, force), daemon=True,
                                  name="midoo-image-index")
        thread.start()
    return search_index_status()


@router.get("/api/search/status")
def search_status():
    return search_index_status()


@router.post("/api/search/reindex")
def reindex(request: Request):
    _admin(_user(request))
    return start_indexing(force=True)


def _prepare_image(content: bytes, name: str) -> dict:
    if not content or len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(400, "单张图片需小于 20 MB，且不能为空")
    try:
        features = extract_features(content)
        thumb = make_thumbnail(content)
        with Image.open(io.BytesIO(content)) as image:
            mime = Image.MIME.get(image.format, "image/jpeg")
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, f"图片“{Path(name).name[:80]}”无法读取：{str(exc)[:180]}")
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff", "image/avif"}:
        raise HTTPException(400, "暂不支持这种图片格式，请使用 JPG、PNG 或 WebP")
    try:
        semantic = _extract_semantic(content)
    except Exception:
        # Preserve valid uploads if inference is temporarily unavailable. The
        # missing derived index remains visible and can be rebuilt afterwards.
        semantic = None
    return {"data": content, "thumb": thumb, "features": features, "semantic": semantic,
            "sha256": hashlib.sha256(content).hexdigest(), "name": Path(name.replace("\\", "/")).name[:200] or "商品图片", "mime": mime}


async def _prepare_uploads(images: list[UploadFile], max_count=MAX_BATCH_IMAGES) -> list[dict]:
    if not 1 <= len(images) <= max_count:
        raise HTTPException(400, f"请选择 1–{max_count} 张图片")
    result, total = [], 0
    try:
        for upload in images:
            content = await upload.read(MAX_IMAGE_SIZE + 1)
            total += len(content)
            if total > MAX_BATCH_SIZE:
                raise HTTPException(400, "一次导入总大小需小于 250 MB，请分批导入")
            prepared = await run_in_threadpool(_prepare_image, content, upload.filename or "商品图片")
            prepared["upload_path"] = (upload.filename or "").replace("\\", "/")
            result.append(prepared)
        return result
    finally:
        for upload in images:
            await upload.close()


def _save_image(conn, product_id: str, prepared: dict, written: list[Path]):
    image_id = uuid.uuid4().hex
    filename, thumbname = image_id + ".bin", image_id + ".jpg"
    original, thumb = DATA_DIR / "images" / filename, DATA_DIR / "thumbnails" / thumbname
    original.write_bytes(prepared["data"])
    written.append(original)
    thumb.write_bytes(prepared["thumb"])
    written.append(thumb)
    conn.execute("INSERT INTO images VALUES(?,?,?,?,?,?,?,?,?)", (image_id, product_id, prepared["name"], filename, thumbname, prepared["mime"], prepared["sha256"], json.dumps(prepared["features"], ensure_ascii=False, separators=(",", ":")), now()))
    semantic = prepared.get("semantic")
    if semantic and semantic.get("model_id"):
        conn.execute("INSERT INTO image_embeddings(image_id,model_id,sha256,embedding,created_at) VALUES(?,?,?,?,?)",
                     (image_id, semantic["model_id"], prepared["sha256"],
                      json.dumps(semantic["embedding"], separators=(",", ":"), allow_nan=False), now()))


def _new_code(conn) -> str:
    conn.execute("INSERT INTO counters(name,value) VALUES('product',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
    return "MD-" + str(conn.execute("SELECT value FROM counters WHERE name='product'").fetchone()[0]).zfill(6)


def _insert_product(conn, metadata, images, user, batch_id, written):
    product_id, stamp = uuid.uuid4().hex, now()
    conn.execute("""INSERT INTO products(id,code,name,owner,status,category,specs,notes,link,created_by,batch_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (product_id, _new_code(conn), metadata["name"], metadata["owner"], metadata["status"], metadata.get("category", ""), metadata["specs"], metadata["notes"], metadata["link"], user["id"], batch_id, stamp, stamp))
    _replace_listings(conn, product_id, metadata.get("listings", []))
    seen = set()
    for image in images:
        if image["sha256"] not in seen:
            _save_image(conn, product_id, image, written)
            seen.add(image["sha256"])
    return product_id


def _cleanup(paths):
    for path in paths:
        path.unlink(missing_ok=True)


def _create_product_sync(metadata, images, user):
    written = []
    try:
        with _connection(write=True) as conn:
            duplicate_matches = []
            for sha in {image["sha256"] for image in images}:
                existing = conn.execute("""SELECT DISTINCT p.id,p.code,p.name,p.owner FROM images i JOIN products p ON p.id=i.product_id
                    WHERE i.sha256=? AND p.deleted=0 LIMIT 5""", (sha,)).fetchall()
                duplicate_matches.extend(dict(row) for row in existing)
            product_id = _insert_product(conn, metadata, images, user, None, written)
            _audit(conn, user, "create", "录入商品：" + metadata["name"], product_id)
            result = _serialize_product(conn, _product(conn, product_id))
            # Rechecked while holding the write transaction, so an identical image
            # registered by another operator between search and submit is visible.
            result["duplicate_matches"] = list({item["id"]: item for item in duplicate_matches}.values())
            return result
    except Exception:
        _cleanup(written)
        raise


@router.post("/api/products")
async def create_product(request: Request, metadata: str = Form("{}"), images: list[UploadFile] = File(...)):
    user = _user(request)
    info = _metadata(_json_form(metadata, "商品信息"), user)
    prepared = await _prepare_uploads(images, 30)
    return await run_in_threadpool(_create_product_sync, info, prepared, user)


def _import_sync(groups, prepared, user):
    uploads = {item["upload_path"]: item for item in prepared}
    if len(uploads) != len(prepared):
        raise HTTPException(400, "存在重名图片路径，请按商品文件夹分组后重新导入")
    normalized, used = [], set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("paths"), list) or not group["paths"]:
            raise HTTPException(400, "每个商品分组需要至少一张图片")
        paths = group["paths"]
        if len(paths) > 30 or any(not isinstance(p, str) or p not in uploads or p in used for p in paths) or len(set(paths)) != len(paths):
            raise HTTPException(400, "图片分组无效或同一张图片被分到多个商品；每商品最多 30 张图")
        used.update(paths)
        normalized.append((_metadata(group, user), [uploads[p] for p in paths]))
    if used != set(uploads):
        raise HTTPException(400, "有图片尚未分组，请完成分组后导入")
    batch_id, written = uuid.uuid4().hex, []
    try:
        with _connection(write=True) as conn:
            conn.execute("INSERT INTO batches VALUES(?,?,?,0)", (batch_id, user["id"], now()))
            duplicates, matches, ids = 0, [], []
            for metadata, images in normalized:
                for image in images:
                    existing = conn.execute("""SELECT DISTINCT p.id,p.code,p.name FROM images i JOIN products p ON p.id=i.product_id
                        WHERE i.sha256=? AND p.deleted=0 LIMIT 3""", (image["sha256"],)).fetchall()
                    if existing:
                        duplicates += 1
                        matches.append({"image": image["name"], "products": [dict(r) for r in existing]})
                ids.append(_insert_product(conn, metadata, images, user, batch_id, written))
            _audit(conn, user, "import", f"批量入库 {len(ids)} 个商品、{len(written)//2} 张图片", batch_id=batch_id)
            return {"batch_id": batch_id, "created": len(ids), "images": len(written)//2, "duplicates": duplicates,
                    "duplicate_matches": matches[:30], "items": [_serialize_product(conn, _product(conn, pid)) for pid in ids]}
    except Exception:
        _cleanup(written)
        raise


@router.post("/api/import")
async def import_products(request: Request, manifest: str = Form(...), images: list[UploadFile] = File(...)):
    payload = _json_form(manifest, "导入分组")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_BATCH_IMAGES:
        raise HTTPException(400, "请先完成商品图片分组")
    prepared = await _prepare_uploads(images)
    return await run_in_threadpool(_import_sync, groups, prepared, _user(request))


@router.post("/api/import/{batch_id}/undo")
def undo_import(batch_id: str, request: Request):
    user = _user(request)
    with _connection(write=True) as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not batch or batch["undone"]:
            raise HTTPException(404, "此导入批次不存在或已撤销")
        if user["role"] != "admin" and batch["created_by"] != user["id"]:
            raise HTTPException(403, "只能撤销自己导入的批次")
        changed = conn.execute("SELECT 1 FROM products WHERE batch_id=? AND (updated_at<>created_at OR deleted=1)", (batch_id,)).fetchone()
        if changed:
            raise HTTPException(409, "批次中的商品已被后续修改，请逐条核对，不能整批撤销")
        count = conn.execute("SELECT COUNT(*) FROM products WHERE batch_id=? AND deleted=0", (batch_id,)).fetchone()[0]
        conn.execute("UPDATE products SET deleted=1,updated_at=? WHERE batch_id=?", (now(), batch_id))
        conn.execute("UPDATE batches SET undone=1 WHERE id=?", (batch_id,))
        _audit(conn, user, "undo_import", f"撤销批次，移除 {count} 个商品", batch_id=batch_id)
    return {"ok": True, "removed": count}


@router.patch("/api/products/{product_id}/listings/{listing_id}/status")
def update_listing_status(product_id: str, listing_id: str, request: Request, payload: dict = Body(...)):
    """Confirm one SKU's availability locally, without fetching its storefront."""
    if set(payload) != {"status", "expected_status"}:
        raise HTTPException(400, "请只提交目标状态和当前状态")
    status, expected = payload["status"], payload["expected_status"]
    if not isinstance(status, str) or status not in {"listed", "archived"}:
        raise HTTPException(400, "请手动确认已上架或已下架")
    if not isinstance(expected, str) or expected not in {"pending", "listed", "archived"}:
        raise HTTPException(400, "当前平台状态不正确，请刷新后重试")
    user = _user(request)
    with _connection(write=True) as conn:
        product = _product(conn, product_id)
        _may_edit(product, user)
        listing = conn.execute("SELECT * FROM product_listings WHERE id=? AND product_id=?", (listing_id, product_id)).fetchone()
        if listing is None:
            raise HTTPException(404, "平台档案不存在或已移除")
        current = listing["status"]
        # A repeated click/request for an already recorded result is harmless.
        if current != status:
            if current != expected:
                raise HTTPException(409, "此 SKU 的上下架状态已被其他操作更新，请刷新后重试")
            conn.execute("UPDATE product_listings SET status=? WHERE id=? AND product_id=?", (status, listing_id, product_id))
            conn.execute("UPDATE products SET updated_at=? WHERE id=?", (now(), product_id))
            names = {"pending": "准备上架", "listed": "已上架", "archived": "已下架"}
            identity = " · ".join(filter(None, (listing["platform"], listing["store"],
                "SKU " + (listing["product_code"] or "未填写"), "SKC " + (listing["product_number"] or "未填写"))))
            _audit(conn, user, "listing_status", f"人工确认 {product['name']} · {identity}：{names[current]} → {names[status]}", product_id)
        result = _serialize_product(conn, _product(conn, product_id))
        return {"product": result, "listing": next(row for row in result["listings"] if row["id"] == listing_id)}


@router.patch("/api/products/{product_id}")
def update_product(product_id: str, request: Request, payload: dict = Body(...)):
    user = _user(request)
    fields = _metadata(payload, user, partial=True)
    with _connection(write=True) as conn:
        product = _product(conn, product_id)
        _may_edit(product, user)
        has_listings = "listings" in fields
        listings = fields.pop("listings", [])
        if has_listings:
            _replace_listings(conn, product_id, listings)
        if fields or has_listings:
            sql = ",".join(key + "=?" for key in fields)
            conn.execute(f"UPDATE products SET {sql + ',' if sql else ''}updated_at=? WHERE id=?", list(fields.values()) + [now(), product_id])
            _audit(conn, user, "update", "更新商品：" + fields.get("name", product["name"]), product_id)
        return _serialize_product(conn, _product(conn, product_id))


@router.delete("/api/products/{product_id}")
def delete_product(product_id: str, request: Request):
    user = _user(request)
    with _connection(write=True) as conn:
        product = _product(conn, product_id)
        _may_edit(product, user)
        conn.execute("UPDATE products SET deleted=1,updated_at=? WHERE id=?", (now(), product_id))
        _audit(conn, user, "delete", "移除商品：" + product["name"], product_id)
    return {"ok": True}


def _add_images_sync(product_id, prepared, user):
    written = []
    try:
        with _connection(write=True) as conn:
            product = _product(conn, product_id)
            _may_edit(product, user)
            existing = {r[0] for r in conn.execute("SELECT sha256 FROM images WHERE product_id=?", (product_id,))}
            unique = [im for i, im in enumerate(prepared) if im["sha256"] not in existing and im["sha256"] not in {x["sha256"] for x in prepared[:i]}]
            if len(existing) + len(unique) > 30:
                raise HTTPException(400, "每个商品最多保留 30 张图片")
            for image in unique:
                _save_image(conn, product_id, image, written)
            if unique:
                conn.execute("UPDATE products SET updated_at=? WHERE id=?", (now(), product_id))
                _audit(conn, user, "add_images", f"为 {product['name']} 补充 {len(unique)} 张图片", product_id)
            return _serialize_product(conn, _product(conn, product_id))
    except Exception:
        _cleanup(written)
        raise


@router.post("/api/products/{product_id}/images")
async def add_images(product_id: str, request: Request, images: list[UploadFile] = File(...)):
    prepared = await _prepare_uploads(images, 30)
    return await run_in_threadpool(_add_images_sync, product_id, prepared, _user(request))


def _search_sync(prepared, user, limit: int = 3, include_weak: bool = False):
    start = time.perf_counter()
    limit = max(1, min(5, int(limit)))
    status = search_index_status()
    if status["model_ready"] and status["indexed_images"] < status["total_images"] and not status["indexing"]:
        status = start_indexing()
    model_id = status["model_id"]
    fingerprints = [item["sha256"] for item in prepared]
    query_vectors = []
    for index, item in enumerate(prepared):
        semantic = item.get("semantic")
        if status["model_ready"] and (not semantic or semantic.get("model_id") != model_id):
            try:
                semantic = _extract_semantic(item["data"])
            except Exception:
                semantic = None
        if semantic and semantic.get("model_id") == model_id:
            try:
                query_vectors.append((index, np.asarray(_normalized_embedding(semantic["embedding"]), dtype=np.float32)))
            except (ValueError, TypeError):
                pass
    if status["model_ready"] and not query_vectors:
        status = {**status, "state": "unavailable", "ready": False,
                  "message": "本次图片未能生成视觉特征，目前仅查找原图一致或此前人工确认的商品。"}

    with _connection() as conn:
        rows = conn.execute("""SELECT i.id,i.product_id,i.sha256,e.embedding FROM images i
            JOIN products p ON p.id=i.product_id
            LEFT JOIN image_embeddings e ON e.image_id=i.id AND e.model_id=? AND e.sha256=i.sha256
            WHERE p.deleted=0""", (model_id,)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM products WHERE deleted=0").fetchone()[0]
        # Explicit feedback applies to any image in this query group. Most recent
        # judgement wins if the group contains conflicting earlier judgements.
        placeholders = ",".join("?" for _ in fingerprints)
        decisions = conn.execute(f"""SELECT d.product_id,d.decision,d.note,d.query_fingerprint,d.created_at,u.name AS actor
            FROM decisions d JOIN users u ON u.id=d.created_by JOIN products p ON p.id=d.product_id
            WHERE p.deleted=0 AND d.query_fingerprint IN ({placeholders}) ORDER BY d.created_at DESC,d.id DESC""", fingerprints).fetchall()
        prior_by_product = {}
        for decision in decisions:
            prior_by_product.setdefault(decision["product_id"], dict(decision))
        excluded = {pid for pid, prior in prior_by_product.items() if prior["decision"] == "different"}
        best_by_product = {}
        invalid_embeddings = 0
        for row in rows:
            pid = row["product_id"]
            if pid in excluded:
                continue
            exact_indices = [index for index, sha in enumerate(fingerprints) if sha == row["sha256"]]
            similarity, query_index = -1.0, 0
            if query_vectors and row["embedding"]:
                try:
                    vector = _cached_embedding(row["embedding"])
                    scores = [(float(np.clip(np.dot(query_vector, vector), -1, 1)), index)
                              for index, query_vector in query_vectors if query_vector.shape == vector.shape]
                    if scores:
                        similarity, query_index = max(scores)
                except (ValueError, TypeError, json.JSONDecodeError):
                    invalid_embeddings += 1
            prior = prior_by_product.get(pid)
            feedback = bool(prior and prior["decision"] == "same")
            if not exact_indices and not feedback and similarity < SEMANTIC_WEAK_MIN_SIMILARITY:
                continue
            match_type = "feedback" if feedback else "exact" if exact_indices else "semantic"
            if feedback:
                query_index = fingerprints.index(prior["query_fingerprint"])
            elif exact_indices:
                query_index = exact_indices[0]
            rank = (2 if feedback else 1 if exact_indices else 0, similarity)
            match = {"product_id": pid, "score": 100.0 if exact_indices or feedback else round(max(0.0, similarity) * 100, 1),
                     "semantic_similarity": round(similarity, 5) if similarity >= -0.99 else None,
                     "exact": bool(exact_indices), "query_index": query_index, "match_type": match_type,
                     "matched_image_url": f"/media/{row['id']}", "matched_image_id": row["id"], "_rank": rank}
            if pid not in best_by_product or rank > best_by_product[pid]["_rank"]:
                best_by_product[pid] = match

        if invalid_embeddings:
            status = {**status, "state": "unavailable", "ready": False,
                      "message": "部分视觉索引格式无效，结果可能不完整，请在设置中重建视觉索引。"}
        ordered = sorted(best_by_product.values(), key=lambda item: item["_rank"], reverse=True)
        top_semantic = max((item["semantic_similarity"] for item in ordered if item["semantic_similarity"] is not None), default=-1.0)
        primary, extras = [], []
        for match in ordered:
            raw = match["semantic_similarity"] if match["semantic_similarity"] is not None else -1.0
            forced = match["match_type"] in {"exact", "feedback"}
            is_primary = forced or (raw >= SEMANTIC_MIN_SIMILARITY and raw >= top_semantic - SEMANTIC_RELATIVE_GAP)
            is_extra = forced or (raw >= SEMANTIC_WEAK_MIN_SIMILARITY and raw >= top_semantic - SEMANTIC_WEAK_RELATIVE_GAP)
            if not is_primary and not is_extra:
                continue
            pid = match.pop("product_id")
            match.pop("_rank")
            match["product"] = _serialize_product(conn, _product(conn, pid))
            match["query_fingerprint"] = fingerprints[match["query_index"]]
            match["previous_decision"] = prior_by_product.get(pid)
            match["score_kind"] = "retrieval_rank"
            if match["match_type"] == "feedback":
                match.update(grade="feedback", evidence_label="人工确认过同款", reason="此前团队已针对本次查询图片确认同款，请复核规格及当前状态。")
            elif match["match_type"] == "exact":
                match.update(grade="exact", evidence_label="原图一致", reason="图片文件完全一致，仍需核对尺寸、材质与套装数量。")
            else:
                match.update(grade="semantic" if is_primary else "weak", evidence_label="疑似同款 / 同系列" if is_primary else "较弱外观候选",
                             reason="本地视觉模型发现商品外观或系列接近，颜色、尺寸、材质和件数仍需核实。")
            if is_primary and len(primary) < limit:
                primary.append(match)
            elif is_extra and len(extras) < 5:
                extras.append(match)

    if include_weak:
        combined = (primary + extras)[:5]
        shown_ids = {item["product"]["id"] for item in combined}
        primary = combined
        extras = [item for item in extras if item["product"]["id"] not in shown_ids]
    visible = len(primary) + len(extras)
    hidden = max(0, total - visible - len(excluded))
    with _connection(write=True) as conn:
        _audit(conn, user, "search", f"以 {len(prepared)} 张图片检索 {total} 个商品，返回 {len(primary)} 个主要候选")
    notice = "候选由本地视觉模型按外观检索，不是同款概率；颜色、尺寸和件数仍需核对。"
    if status["state"] != "ready":
        notice = status["message"] + " 原图一致和人工确认仍可查到。"
    return {"items": primary, "additional_items": extras, "additional_count": len(extras), "has_more": bool(extras),
            "hidden_count": hidden, "filtered_count": hidden + len(excluded), "excluded_count": len(excluded),
            "total_searched": total, "total_images_searched": len(rows), "elapsed_ms": round((time.perf_counter()-start)*1000),
            "query_fingerprints": fingerprints, "engine": "本地视觉语义检索" if status["model_ready"] and query_vectors else "原图与人工核对检索",
            "engine_status": status, "index_status": status, "threshold": SEMANTIC_MIN_SIMILARITY, "top_k": limit,
            "score_is_probability": False, "notice": notice}


@router.post("/api/search")
async def search(request: Request, images: list[UploadFile] = File(...),
                 limit: int = Form(3, ge=1, le=5), include_weak: bool = Form(False)):
    started = time.perf_counter()
    prepared = await _prepare_uploads(images, 5)
    result = await run_in_threadpool(_search_sync, prepared, _user(request), limit, include_weak)
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result


@router.post("/api/decisions")
def record_decision(request: Request, payload: dict = Body(...)):
    user = _user(request)
    decision = payload.get("decision")
    if decision not in {"same", "different", "uncertain"}:
        raise HTTPException(400, "请选择同款、不同商品或待核实")
    note, fingerprint = payload.get("note", ""), payload.get("query_fingerprint", "")
    product_id = payload.get("product_id", "")
    if not isinstance(product_id, str) or len(product_id) > 64:
        raise HTTPException(400, "商品编号格式不正确")
    if not isinstance(note, str) or len(note) > 2000 or not isinstance(fingerprint, str) or len(fingerprint) > 128:
        raise HTTPException(400, "核对备注格式不正确")
    with _connection(write=True) as conn:
        product = _product(conn, product_id)
        conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, product["id"], decision, note, fingerprint, user["id"], now()))
        conn.execute("UPDATE products SET updated_at=? WHERE id=?", (now(), product["id"]))
        label = {"same": "确认同款", "different": "标记不同商品", "uncertain": "标记待核实"}[decision]
        _audit(conn, user, "decision", label + "：" + product["name"], product["id"])
    return {"ok": True}


@router.get("/api/activity")
def activity(request: Request, limit: int = Query(40, ge=1, le=200),
             offset: int = Query(0, ge=0), actor_id: str | None = Query(None, max_length=200),
             start_date: str | None = None, end_date: str | None = None):
    user = _user(request)
    # The library has used UTC ISO timestamps since its first release in 2026.
    # Shanghai has used UTC+08:00 throughout this period. Use explicit local-day
    # bounds, independent of the Windows host timezone and SQLite date coercion.
    china = timezone(timedelta(hours=8), "Asia/Shanghai")
    dates = {}
    for field, value in (("start_date", start_date), ("end_date", end_date)):
        if not value:
            continue
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise HTTPException(400, "日期格式应为 YYYY-MM-DD。")
        try:
            dates[field] = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=china)
        except ValueError:
            raise HTTPException(400, "日期无效，请检查月份与日期。") from None
    if dates.get("start_date") and dates.get("end_date") and dates["start_date"] > dates["end_date"]:
        raise HTTPException(400, "开始日期不能晚于结束日期。")
    clauses, params = [], []
    if actor_id:
        if actor_id == "__system__":
            clauses.append("a.user_id IS NULL")
        else:
            clauses.append("a.user_id=?")
            params.append(actor_id)
    try:
        if "start_date" in dates:
            clauses.append("a.created_at>=?")
            params.append(dates["start_date"].astimezone(timezone.utc).isoformat(timespec="seconds"))
        if "end_date" in dates:
            clauses.append("a.created_at<?")
            params.append((dates["end_date"] + timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds"))
    except (ValueError, OverflowError):
        raise HTTPException(400, "日期超出可查询范围，请选择有效的起止日期。") from None
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM activity a" + where, params).fetchone()[0]
        rows = conn.execute("""SELECT a.*,b.undone,b.created_by AS batch_creator FROM activity a LEFT JOIN batches b ON b.id=a.batch_id
                            """ + where + " ORDER BY a.created_at DESC,a.id DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        # Choices come from all audit records, rather than the current page,
        # time filter or team roster. A rename retains the same filter identity.
        actor_rows = conn.execute("""WITH recorded_actors AS (
            SELECT a.user_id,COALESCE(u.name,a.actor) AS name,
                   ROW_NUMBER() OVER (PARTITION BY a.user_id ORDER BY a.created_at DESC,a.id DESC) AS rank
            FROM activity a LEFT JOIN users u ON u.id=a.user_id
        ) SELECT user_id,name FROM recorded_actors WHERE rank=1 ORDER BY name COLLATE NOCASE,user_id""").fetchall()
        actors = [{"id": row["user_id"] if row["user_id"] is not None else "__system__", "name": row["name"]} for row in actor_rows]
        items = []
        for row in rows:
            item = dict(row)
            item["can_undo"] = bool(row["action"] == "import" and not row["undone"] and (user["role"] == "admin" or user["id"] == row["batch_creator"]))
            if item["can_undo"]:
                item["can_undo"] = not bool(conn.execute("SELECT 1 FROM products WHERE batch_id=? AND (updated_at<>created_at OR deleted=1) LIMIT 1", (row["batch_id"],)).fetchone())
            item.pop("batch_creator", None)
            items.append(item)
        return {"items": items, "total": total, "actors": actors}


@router.get("/api/team")
def team():
    with _connection() as conn:
        rows = conn.execute("SELECT id,name,role,created_at FROM users ORDER BY created_at").fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/team")
def create_member(request: Request, payload: dict = Body(...)):
    user = _user(request)
    _admin(user)
    name, role = _name(payload.get("name")), payload.get("role", "operator")
    if role not in {"admin", "operator"}:
        raise HTTPException(400, "成员角色不正确")
    password_hash = _password_hash(payload.get("password"))
    member = {"id": uuid.uuid4().hex, "name": name, "role": role, "created_at": now()}
    try:
        with _connection(write=True) as conn:
            conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (member["id"], name, password_hash, role, member["created_at"]))
            _audit(conn, user, "team", "添加成员：" + name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "此成员名称已存在")
    return member


@router.get("/media/{image_id}")
def media(image_id: str, thumb: bool = False):
    with _connection() as conn:
        row = conn.execute("""SELECT i.* FROM images i JOIN products p ON p.id=i.product_id
                             WHERE i.id=? AND p.deleted=0""", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "图片不存在")
    path = DATA_DIR / ("thumbnails" if thumb else "images") / (row["thumb_filename"] if thumb else row["filename"])
    if not path.is_file():
        raise HTTPException(404, "图片文件缺失，请检查备份或联系管理员")
    return FileResponse(path, media_type="image/jpeg" if thumb else row["media_type"], headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


def register_backup_file(path: Path, archive_name: str) -> None:
    if Path(archive_name).is_absolute() or ".." in Path(archive_name).parts:
        raise ValueError("无效备份文件名")
    _backup_extras[archive_name] = Path(path)


def _make_backup(user):
    backup_name = "midoo-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3) + ".zip"
    target = DATA_DIR / "backups" / backup_name
    snapshot = DATA_DIR / "backups" / (uuid.uuid4().hex + ".sqlite.tmp")
    # A reserved writer transaction makes database and media membership a single snapshot.
    # File writers use BEGIN IMMEDIATE too, and soft deletion keeps old image files recoverable.
    with _connection(write=True) as guard:
        try:
            with _connection() as source:
                destination = sqlite3.connect(snapshot)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                image_rows = source.execute("SELECT filename,thumb_filename FROM images").fetchall()
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                archive.write(snapshot, "library.db")
                for row in image_rows:
                    for folder, key in (("images", "filename"), ("thumbnails", "thumb_filename")):
                        path = DATA_DIR / folder / row[key]
                        if not path.is_file():
                            raise HTTPException(409, "部分图片文件缺失，请检查资料完整性后重新备份")
                        archive.write(path, folder + "/" + row[key])
                for arcname, path in _backup_extras.items():
                    if path.is_file():
                        archive.write(path, arcname)
                archive.writestr("backup.json", json.dumps({"application": "Midoo", "version": VERSION, "created_at": now(), "images": len(image_rows), "restore": "停止服务后将备份解压到一个新资料目录，再指定 MIDOO_DATA_DIR 启动。"}, ensure_ascii=False))
            stamp = now()
            guard.execute("INSERT INTO app_meta(key,value) VALUES('last_backup',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (stamp,))
            _audit(guard, user, "backup", "创建完整商品库备份")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            snapshot.unlink(missing_ok=True)
    return target


def create_backup() -> Path:
    """Create a full snapshot for the application's local backup scheduler."""
    with _connection() as conn:
        row = conn.execute("SELECT id,name,role FROM users WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
    actor = _public_user(row) if row else {"id": None, "name": "系统", "role": "admin"}
    return _make_backup(actor)


@router.post("/api/backup")
def backup(request: Request):
    user = _user(request)
    _admin(user)
    path = _make_backup(user)
    return FileResponse(path, filename=path.name, media_type="application/zip", headers={"Cache-Control": "no-store"})


@router.get("/api/system")
def system(request: Request):
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    ips = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = item[4][0]
            if not ip.startswith("127.") and ip != "0.0.0.0":
                ips.add(ip)
    except OSError:
        pass
    with _connection() as conn:
        last = conn.execute("SELECT value FROM app_meta WHERE key='last_backup'").fetchone()
    return {"data_path": str(DATA_DIR) if _user(request)["role"] == "admin" else "公司主机集中保存", "local_url": f"http://127.0.0.1:{port}",
            "lan_urls": [f"http://{ip}:{port}" for ip in sorted(ips)], "version": VERSION,
            "engine": "DINOv2 本地视觉语义检索" if _semantic_model_status().get("loaded") else "本地视觉模型未就绪：仅原图与人工核对检索",
            "search_index": search_index_status(), "last_backup": last[0] if last else None,
            "max_image_mb": 20, "max_batch_images": MAX_BATCH_IMAGES, "max_batch_mb": 250, "max_product_images": 30}


def ai_context(query: str = "") -> dict:
    words = [w[:60] for w in str(query).strip().split()[:6] if w]
    with _connection() as conn:
        if words:
            clauses, args = [], []
            for word in words:
                pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                clauses.append("(name LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\' OR specs LIKE ? ESCAPE '\\')")
                args += [pattern] * 3
            rows = conn.execute("SELECT id,code,name,owner,status,specs,created_at FROM products WHERE deleted=0 AND (" + " OR ".join(clauses) + ") ORDER BY updated_at DESC LIMIT 30", args).fetchall()
        else:
            rows = []
        if not rows:
            rows = conn.execute("SELECT id,code,name,owner,status,specs,created_at FROM products WHERE deleted=0 ORDER BY updated_at DESC LIMIT 30").fetchall()
        return {"stats": _stats(conn), "products": [{**dict(row), "specs": row["specs"][:500]} for row in rows],
                "scope": "最多30条公司商品摘要；不代表全库检索结果。无外部平台价格或核价数据。"}
