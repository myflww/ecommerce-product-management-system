"""Local, persistent company knowledge and Chinese-aware catalog retrieval.

Only extracted text is stored. Search snippets are untrusted source material,
never commands; this module performs no model calls, network I/O or file writes.
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

import backend
import chat_attachments
import permissions

router = APIRouter(prefix="/api/knowledge", dependencies=[Depends(backend.require_user)])
MAX_DOCUMENTS = 2000
MAX_CONTENT = 100000
MAX_JSON_BYTES = 1024 * 1024
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 120
MAX_REFERENCES = 6
MAX_PRODUCT_LINKS = 30
_init_lock = threading.RLock()
_initialized: tuple[str, int] | None = None
_CJK = re.compile(r"[\u3400-\u9fff]+")
_LATIN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", re.I)
_STOP = (
    "帮我", "请问", "一下", "告诉我", "给我", "发给我", "发我", "公司", "内部", "现有", "已有", "目前",
    "有没有", "是否", "能否", "能不能", "可以", "一个", "一款", "这款", "这个", "那个", "把它", "它的",
    "样子", "图片", "照片", "查看", "查询", "找到", "查找", "找个", "介绍", "是什么", "怎样", "怎么", "哪些",
    "一些", "资料", "内容", "知识", "文档", "产品", "商品", "的", "吗", "呢", "吧", "我", "们", "有", "像", "和", "与",
)
_STOP_LATIN = {"a", "an", "the", "is", "are", "our", "company", "please", "show", "me", "find", "product", "products", "photo", "picture", "and", "or", "of", "to"}


def _words(text: str, *, query: bool = False) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    if query:
        for word in _STOP:
            normalized = normalized.replace(word, " ")
    words = set()
    for word in _LATIN.findall(normalized):
        if not query or word not in _STOP_LATIN:
            words.add(word)
            if "-" in word or "_" in word:
                words.update(part for part in re.split("[-_]", word) if len(part) > 1)
    for run in _CJK.findall(normalized):
        if len(run) == 1:
            if not query:
                words.add(run)
            continue
        for width in (2, 3):
            words.update(run[index:index + width] for index in range(len(run) - width + 1))
    # Hex tokens ensure FTS sees complete terms, including codes with punctuation.
    return ["x" + word.encode("utf-8").hex() for word in sorted(words) if word]


def _tokens(text: str) -> str:
    return " ".join(_words(text))


def _match(query: str) -> str:
    if not isinstance(query, str) or len(query) > 6000:
        raise HTTPException(400, "检索问题需要是不超过6000字的文字。")
    words = _words(query, query=True)[:80]
    return " OR ".join('"' + word + '"' for word in words)


def _limit(value: int, maximum: int = MAX_REFERENCES) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise HTTPException(400, f"一次最多读取{maximum}条资料。")
    return value


def _tracking_triggers(conn) -> None:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_tracking'").fetchone()
    if not exists:
        return
    triggers = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN ('knowledge_tracking_insert','knowledge_tracking_update','knowledge_tracking_delete')").fetchall()
    if len(triggers) == 3 and all('ON CONFLICT(product_id) DO NOTHING' in row['sql'] for row in triggers):
        return
    # An outer price_tracking UPSERT can override a trigger's INSERT OR IGNORE
    # policy and abort an otherwise valid SKU edit. An explicit inner UPSERT
    # owns its conflict handling; replace older trigger definitions in place.
    for action, ref in (("INSERT", "new"), ("UPDATE", "new"), ("DELETE", "old")):
        conn.execute(f"DROP TRIGGER IF EXISTS knowledge_tracking_{action.lower()}")
        conn.execute(f"""CREATE TRIGGER knowledge_tracking_{action.lower()} AFTER {action} ON price_tracking
            BEGIN INSERT INTO knowledge_product_dirty(product_id)
            SELECT product_id FROM product_listings WHERE id={ref}.listing_id
            ON CONFLICT(product_id) DO NOTHING; END""")
    conn.execute("INSERT OR IGNORE INTO knowledge_product_dirty SELECT DISTINCT l.product_id FROM product_listings l JOIN price_tracking t ON t.listing_id=l.id")


def init_storage() -> None:
    """Additive initialization; existing products, quotes and histories stay intact."""
    global _initialized
    if backend.DB_PATH is None:
        raise RuntimeError("请先初始化公司商品库。")
    marker = (str(backend.DB_PATH.resolve()), backend.DB_PATH.stat().st_ino)
    with _init_lock:
        if _initialized == marker:
            # A database restored over an existing path may retain its inode.
            # Verify the schema instead of treating process memory as authority.
            with backend._connection() as conn:
                present = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('knowledge_documents','knowledge_chunks','knowledge_product_dirty','knowledge_chunk_search','knowledge_product_search','knowledge_document_versions','knowledge_document_products')").fetchone()[0]
                has_revision = any(row["name"] == "revision" for row in conn.execute("PRAGMA table_info(knowledge_documents)"))
                has_version_links = any(row["name"] == "related_products" for row in conn.execute("PRAGMA table_info(knowledge_document_versions)"))
            if present == 7 and has_revision and has_version_links:
                return
        with backend._connection(write=True) as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL, source_name TEXT NOT NULL DEFAULT '', truncated INTEGER NOT NULL DEFAULT 0,
                characters INTEGER NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), owner TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS knowledge_documents_updated ON knowledge_documents(deleted,updated_at DESC);
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                position INTEGER NOT NULL, content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS knowledge_chunks_document ON knowledge_chunks(document_id,position);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunk_search USING fts5(chunk_id UNINDEXED,document_id UNINDEXED,tokens);
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_product_search USING fts5(product_id UNINDEXED,tokens);
            CREATE TABLE IF NOT EXISTS knowledge_product_dirty(product_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS knowledge_document_products (
                document_id TEXT NOT NULL REFERENCES knowledge_documents(id),
                product_id TEXT NOT NULL REFERENCES products(id), position INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(document_id,product_id)
            );
            CREATE INDEX IF NOT EXISTS knowledge_document_products_product ON knowledge_document_products(product_id,document_id);
            CREATE TABLE IF NOT EXISTS knowledge_document_versions (
                document_id TEXT NOT NULL REFERENCES knowledge_documents(id), revision INTEGER NOT NULL,
                title TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
                source_name TEXT NOT NULL, truncated INTEGER NOT NULL, characters INTEGER NOT NULL,
                created_by TEXT NOT NULL, owner TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, deleted INTEGER NOT NULL, product_ids TEXT NOT NULL,
                related_products TEXT NOT NULL DEFAULT '[]',
                action TEXT NOT NULL, changed_by TEXT, changed_by_name TEXT NOT NULL, recorded_at TEXT NOT NULL,
                PRIMARY KEY(document_id,revision)
            );
            CREATE TRIGGER IF NOT EXISTS knowledge_product_insert AFTER INSERT ON products
                BEGIN INSERT OR IGNORE INTO knowledge_product_dirty VALUES(new.id); END;
            CREATE TRIGGER IF NOT EXISTS knowledge_product_update AFTER UPDATE ON products
                BEGIN INSERT OR IGNORE INTO knowledge_product_dirty VALUES(new.id); END;
            CREATE TRIGGER IF NOT EXISTS knowledge_product_delete AFTER DELETE ON products
                BEGIN INSERT OR IGNORE INTO knowledge_product_dirty VALUES(old.id); END;
            CREATE TRIGGER IF NOT EXISTS knowledge_listing_insert AFTER INSERT ON product_listings
                BEGIN INSERT OR IGNORE INTO knowledge_product_dirty VALUES(new.product_id); END;
            CREATE TRIGGER IF NOT EXISTS knowledge_listing_update AFTER UPDATE ON product_listings
                BEGIN INSERT OR IGNORE INTO knowledge_product_dirty VALUES(new.product_id);
                INSERT OR IGNORE INTO knowledge_product_dirty VALUES(old.product_id); END;
            CREATE TRIGGER IF NOT EXISTS knowledge_listing_delete AFTER DELETE ON product_listings
                BEGIN INSERT OR IGNORE INTO knowledge_product_dirty VALUES(old.product_id); END;
            """)
            if not any(row["name"] == "revision" for row in conn.execute("PRAGMA table_info(knowledge_documents)")):
                conn.execute("ALTER TABLE knowledge_documents ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            if not any(row["name"] == "related_products" for row in conn.execute("PRAGMA table_info(knowledge_document_versions)")):
                conn.execute("ALTER TABLE knowledge_document_versions ADD COLUMN related_products TEXT NOT NULL DEFAULT '[]'")
            # The baseline records exactly what exists now. It does not invent
            # earlier edits or rewrite the original text and timestamps.
            for row in conn.execute("""SELECT d.* FROM knowledge_documents d WHERE NOT EXISTS
                (SELECT 1 FROM knowledge_document_versions v WHERE v.document_id=d.id AND v.revision=d.revision)""").fetchall():
                _append_version(conn, row, "baseline", None)
            # Recreate only missing derived chunks/index entries when opening a
            # partial or older knowledge backup, leaving the source rows intact.
            for row in conn.execute("""SELECT d.id,d.title,d.category,d.content FROM knowledge_documents d
                WHERE d.id NOT IN (SELECT document_id FROM knowledge_chunks)
                   OR d.id NOT IN (SELECT document_id FROM knowledge_chunk_search WHERE document_id IS NOT NULL)""").fetchall():
                _reindex_document(conn, row["id"], row["title"], row["category"], row["content"])
            if not conn.execute("SELECT 1 FROM app_meta WHERE key='knowledge:product_index_v1'").fetchone():
                conn.execute("INSERT OR IGNORE INTO knowledge_product_dirty SELECT id FROM products")
                conn.execute("INSERT INTO app_meta(key,value) VALUES('knowledge:product_index_v1','1')")
            _tracking_triggers(conn)
        _initialized = marker


def _sync_products() -> None:
    init_storage()
    # Each batch releases the writer lock. Incremental triggers keep subsequent
    # queries cheap while the first search can see every existing product.
    with backend._connection(write=True) as conn:
        _tracking_triggers(conn)
        total = conn.execute("SELECT COUNT(*) FROM knowledge_product_dirty").fetchone()[0]
    for _ in range((total + 249) // 250):
        with backend._connection(write=True) as conn:
            dirty = conn.execute("SELECT product_id FROM knowledge_product_dirty ORDER BY product_id LIMIT 250").fetchall()
            has_tracking = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_tracking'").fetchone())
            for entry in dirty:
                identity = entry["product_id"]
                conn.execute("DELETE FROM knowledge_product_search WHERE product_id=?", (identity,))
                row = conn.execute("SELECT * FROM products WHERE id=? AND deleted=0", (identity,)).fetchone()
                if row:
                    pieces = [str(row[key]) for key in ("code", "name", "category", "specs", "notes", "owner")]
                    pieces.extend(" ".join(str(item[key]) for key in ("platform", "store", "product_code", "product_number"))
                                  for item in conn.execute("SELECT platform,store,product_code,product_number FROM product_listings WHERE product_id=?", (identity,)))
                    if has_tracking:
                        pieces.extend(" ".join(str(item[key]) for key in ("region", "variant_hint")) for item in conn.execute(
                            "SELECT t.region,t.variant_hint FROM price_tracking t JOIN product_listings l ON l.id=t.listing_id WHERE l.product_id=?", (identity,)))
                    conn.execute("INSERT INTO knowledge_product_search(product_id,tokens) VALUES(?,?)", (identity, _tokens(" ".join(pieces))))
                conn.execute("DELETE FROM knowledge_product_dirty WHERE product_id=?", (identity,))


def _ids(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > MAX_REFERENCES or any(not isinstance(item, str) or not 1 <= len(item) <= 100 for item in raw):
        raise HTTPException(400, "资料引用最多6条，需提供有效编号。")
    return list(dict.fromkeys(raw))


def get_products_by_ids(ids: list[str]) -> list[dict]:
    """Trusted caller must be authenticated. Never reuse client text or URLs."""
    identities = _ids(ids)
    with backend._connection() as conn:
        result = []
        for identity in identities:
            row = conn.execute("SELECT * FROM products WHERE id=? AND deleted=0", (identity,)).fetchone()
            if row:
                result.append(backend._serialize_product(conn, row))
        return result


def retrieve_products(query: str, limit: int = MAX_REFERENCES) -> list[dict]:
    _limit(limit)
    match = _match(query)
    if not match:
        return []
    _sync_products()
    with backend._connection() as conn:
        rows = conn.execute("""SELECT s.product_id FROM knowledge_product_search s JOIN products p ON p.id=s.product_id
            WHERE knowledge_product_search MATCH ? AND p.deleted=0
            ORDER BY bm25(knowledge_product_search),p.updated_at DESC,p.id LIMIT ?""", (match, limit)).fetchall()
    return get_products_by_ids([row["product_id"] for row in rows])


def _field(value: Any, name: str, length: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > length or any(ord(c) < 32 or 127 <= ord(c) < 160 or c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for c in value):
        raise HTTPException(400, f"{name}需为不超过{length}字的普通文字。")
    value = value.strip()
    if not value and not empty:
        raise HTTPException(400, f"请填写{name}。")
    return value


def _body(raw: Any, *, partial: bool = False) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"title", "content", "category", "product_ids", "expected_revision"} or not raw:
        raise HTTPException(400, "知识条目只支持标题、正文、分类、关联商品及版本核对。")
    if not partial and not {"title", "content"}.issubset(raw):
        raise HTTPException(400, "请填写知识标题和正文。")
    result = {}
    if "product_ids" in raw:
        result["product_ids"] = _product_ids(raw["product_ids"])
    if "expected_revision" in raw:
        if not partial:
            raise HTTPException(400, "新建知识无需提交当前版本。")
        result["expected_revision"] = _revision(raw["expected_revision"])
    for key, label, size in (("title", "知识标题", 120), ("category", "分类", 60)):
        if key in raw:
            result[key] = _field(raw[key], label, size, empty=key == "category")
    if "content" in raw:
        text = raw["content"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_CONTENT or any(ord(c) < 32 and c not in "\r\n\t" for c in text):
            raise HTTPException(400, f"知识正文需为1至{MAX_CONTENT}字的文字。")
        result["content"] = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return result


async def _json(request: Request) -> Any:
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > MAX_JSON_BYTES:
            raise HTTPException(413, "知识正文请求过大，请拆分条目。")
        data.extend(part)
    try:
        return json.loads(data)
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(400, "请提交有效的知识条目。") from None


def _record(conn, identity: str, *, deleted: bool | None = False):
    condition = "" if deleted is None else " AND deleted=?"
    params = (identity,) if deleted is None else (identity, int(deleted))
    row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?" + condition, params).fetchone()
    if not row:
        raise HTTPException(404, "知识条目不存在或已移除。")
    return row


def _privileged(user: dict | None) -> bool:
    return bool(user and user.get("role") in {"owner", "admin"} and not user.get("disabled", False))


def _can_edit(row, user: dict | None) -> bool:
    return _privileged(user) and (permissions.has(user, "knowledge.edit_all") or
        (row["created_by"] == user["id"] and permissions.has(user, "knowledge.edit_own")))


def _can_archive(user: dict | None) -> bool:
    return _privileged(user) and permissions.has(user, "knowledge.archive")


def _can_create(user: dict | None) -> bool:
    return _privileged(user) and permissions.has(user, "knowledge.create")


def _edit(row, user: dict) -> None:
    if not _can_edit(row, user):
        raise HTTPException(403, "仅获授权的老板或管理员可编辑此企业知识。")


def _product_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) > MAX_PRODUCT_LINKS or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value) for value in raw) or len(set(raw)) != len(raw):
        raise HTTPException(400, f"关联商品须为不重复的有效商品编号，最多{MAX_PRODUCT_LINKS}款。")
    return list(raw)


def _revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise HTTPException(400, "当前版本须为大于0的整数。")
    return value


def _check_revision(row, fields: dict) -> None:
    if "expected_revision" in fields and _revision(fields["expected_revision"]) != row["revision"]:
        raise HTTPException(409, "知识已被其他操作更新，请重新打开最新版本再保存。")


def _linked_products(conn, identity: str, *, include_deleted: bool = False) -> list[dict]:
    fields = "p.id,p.code,p.name,p.deleted" if include_deleted else "p.id,p.code,p.name"
    condition = "" if include_deleted else " AND p.deleted=0"
    products = [dict(row) for row in conn.execute(f"""SELECT {fields} FROM knowledge_document_products k
        JOIN products p ON p.id=k.product_id WHERE k.document_id=?{condition} ORDER BY k.position,p.id""", (identity,))]
    if include_deleted:
        for product in products:
            product["deleted"] = bool(product["deleted"])
    return products


def _all_links(conn, identity: str) -> list[str]:
    return [row[0] for row in conn.execute("SELECT product_id FROM knowledge_document_products WHERE document_id=? ORDER BY position,product_id", (identity,))]


def _replace_links(conn, identity: str, product_ids: list[str]) -> None:
    retained = set(_all_links(conn, identity))
    for product_id in product_ids:
        product = conn.execute("SELECT deleted FROM products WHERE id=?", (product_id,)).fetchone()
        if not product or (product["deleted"] and product_id not in retained):
            raise HTTPException(400, "关联商品不存在或已移除，请重新选择。")
    _write_links(conn, identity, product_ids)


def _write_links(conn, identity: str, product_ids: list[str]) -> None:
    conn.execute("DELETE FROM knowledge_document_products WHERE document_id=?", (identity,))
    conn.executemany("INSERT INTO knowledge_document_products(document_id,product_id,position) VALUES(?,?,?)",
                     [(identity, product_id, position) for position, product_id in enumerate(product_ids)])


def _append_version(conn, row, action: str, user: dict | None) -> None:
    fields = ("title", "category", "content", "source_name", "truncated", "characters", "created_by", "owner", "created_at", "updated_at", "deleted")
    related = [dict(product) for product in conn.execute("""SELECT p.id,p.code,p.name FROM knowledge_document_products k
        JOIN products p ON p.id=k.product_id WHERE k.document_id=? ORDER BY k.position,p.id""", (row["id"],))]
    conn.execute("""INSERT INTO knowledge_document_versions(document_id,revision,title,category,content,source_name,truncated,
        characters,created_by,owner,created_at,updated_at,deleted,product_ids,related_products,action,changed_by,changed_by_name,recorded_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (row["id"], row["revision"], *[row[key] for key in fields],
            json.dumps(_all_links(conn, row["id"])), json.dumps(related, ensure_ascii=False), action, user["id"] if user else None,
            user["name"] if user else "系统迁移", backend.now()))


def assert_product_purge_allowed(conn, product_id: str) -> None:
    """Use the caller's transaction; never erase retained company knowledge."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_document_products'").fetchone():
        return
    if conn.execute("SELECT 1 FROM knowledge_document_products WHERE product_id=? LIMIT 1", (product_id,)).fetchone():
        raise HTTPException(409, "此商品仍有关联企业知识（含已停用资料），请先明确解除关联后再永久清理。")


def _has_link_schema(conn) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_document_products'").fetchone():
        return False
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_document_versions'").fetchone():
        raise HTTPException(409, "企业知识版本表缺失，请完成知识库升级后再合并商品。")
    return True


def remap_product_links(conn, source_ids: list[str], target_id: str, user: dict) -> dict:
    """Remap explicit links inside the product merge's existing transaction."""
    result = {"documents": []}
    if not _has_link_schema(conn) or not source_ids:
        return result
    if not isinstance(source_ids, list) or len(source_ids) > 100 or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value) for value in source_ids):
        raise HTTPException(400, "合并来源商品编号不正确。")
    if not isinstance(target_id, str) or not re.fullmatch(r"[a-f0-9]{32}", target_id) or target_id in source_ids:
        raise HTTPException(400, "合并目标商品编号不正确。")
    sources = set(source_ids)
    placeholders = ",".join("?" for _ in sources)
    rows = conn.execute(f"""SELECT DISTINCT d.* FROM knowledge_documents d
        JOIN knowledge_document_products k ON k.document_id=d.id WHERE k.product_id IN ({placeholders}) ORDER BY d.id""", sorted(sources)).fetchall()
    if not rows:
        return result
    if not conn.execute("SELECT 1 FROM products WHERE id=? AND deleted=0", (target_id,)).fetchone():
        raise HTTPException(409, "合并目标商品不存在或已移除，无法同步知识关联。")
    # Preflight the complete set so callers cannot accidentally commit a partial
    # remap after catching an authorization failure inside their transaction.
    for row in rows:
        _edit(row, user)
    planned = []
    for row in rows:
        before = _all_links(conn, row["id"])
        after = list(dict.fromkeys(target_id if identity in sources else identity for identity in before))
        planned.append((row, before, after))
    for row, before, after in planned:
        _write_links(conn, row["id"], after)
        conn.execute("UPDATE knowledge_documents SET revision=revision+1,updated_at=? WHERE id=?", (backend.now(), row["id"]))
        changed = _record(conn, row["id"], deleted=None)
        _append_version(conn, changed, "product_merge", user)
        backend._audit(conn, user, "knowledge_product_merge", "商品合并同步知识关联：" + row["title"])
        result["documents"].append({"document_id": row["id"], "before_product_ids": before,
            "after_product_ids": after, "after_revision": changed["revision"]})
    return result


def _product_link_undo_plan(conn, changes: dict, user: dict) -> list:
    if not _has_link_schema(conn):
        return []
    # Merge journals created before explicit knowledge links use '{}' as their
    # migration default and have no recorded knowledge changes to undo.
    if changes == {}:
        return []
    if not isinstance(changes, dict) or set(changes) != {"documents"} or not isinstance(changes["documents"], list) or len(changes["documents"]) > MAX_DOCUMENTS:
        raise HTTPException(409, "商品合并的知识关联留痕不完整，不能安全撤销。")
    plan, seen = [], set()
    for change in changes["documents"]:
        if not isinstance(change, dict) or set(change) != {"document_id", "before_product_ids", "after_product_ids", "after_revision"}:
            raise HTTPException(409, "商品合并的知识关联留痕不完整，不能安全撤销。")
        identity = change["document_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity) or identity in seen:
            raise HTTPException(409, "商品合并的知识条目编号无效或重复。")
        seen.add(identity)
        try:
            before = _product_ids(change["before_product_ids"])
            after = _product_ids(change["after_product_ids"])
            revision = _revision(change["after_revision"])
        except HTTPException:
            raise HTTPException(409, "商品合并的知识关联留痕不完整，不能安全撤销。") from None
        row = conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (identity,)).fetchone()
        if row is None or row["revision"] != revision or _all_links(conn, identity) != after:
            raise HTTPException(409, "合并后的企业知识或关联已被修改，请先核对，不能覆盖后续修改撤销。")
        _edit(row, user)
        for product_id in before:
            if not conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                raise HTTPException(409, "原关联商品已不存在，不能安全恢复知识关联。")
        plan.append((row, before, after))
    return plan


def assert_product_link_undo_allowed(conn, changes: dict, user: dict) -> None:
    """Pure preflight, safe to call before restoring merged product records."""
    _product_link_undo_plan(conn, changes, user)


def undo_product_link_remap(conn, changes: dict, user: dict) -> dict:
    """Append new revisions restoring former links; old history never changes."""
    plan = _product_link_undo_plan(conn, changes, user)
    result = {"documents": []}
    for row, before, after in plan:
        # Every former product was verified above. Soft-deleted sources are
        # permitted here because restoring a recorded link is not a new claim.
        _write_links(conn, row["id"], before)
        conn.execute("UPDATE knowledge_documents SET revision=revision+1,updated_at=? WHERE id=?", (backend.now(), row["id"]))
        changed = _record(conn, row["id"], deleted=None)
        _append_version(conn, changed, "product_merge_undo", user)
        backend._audit(conn, user, "knowledge_product_merge_undo", "撤销商品合并，恢复知识关联：" + row["title"])
        result["documents"].append({"document_id": row["id"], "before_product_ids": after,
            "after_product_ids": before, "after_revision": changed["revision"]})
    return result


def _view(row, user: dict | None = None, *, content: bool = False, conn=None) -> dict:
    fields = ("id", "title", "category", "source_name", "characters", "owner", "created_at", "updated_at", "revision")
    result = {key: row[key] for key in fields}
    result.update(truncated=bool(row["truncated"]), deleted=bool(row["deleted"]))
    if user:
        result["can_edit"] = _can_edit(row, user)
        result["can_archive"] = _can_archive(user)
        result["can_view_history"] = result["can_edit"] or result["can_archive"]
    include_deleted = bool(user and (_can_edit(row, user) or _can_archive(user)))
    if conn is None:
        with backend._connection() as connection:
            products = _linked_products(connection, row["id"], include_deleted=include_deleted)
            product_ids = _all_links(connection, row["id"])
    else:
        products = _linked_products(conn, row["id"], include_deleted=include_deleted)
        product_ids = _all_links(conn, row["id"])
    result["product_ids"] = product_ids
    if content:
        result["content"] = row["content"]
        result["related_products"] = products
    return result


def _reindex_document(conn, identity: str, title: str, category: str, content: str) -> None:
    conn.execute("DELETE FROM knowledge_chunk_search WHERE document_id=?", (identity,))
    conn.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (identity,))
    for position, start in enumerate(range(0, len(content), CHUNK_SIZE - CHUNK_OVERLAP)):
        chunk = content[start:start + CHUNK_SIZE]
        chunk_id = identity + ":" + str(position)
        conn.execute("INSERT INTO knowledge_chunks(id,document_id,position,content) VALUES(?,?,?,?)", (chunk_id, identity, position, chunk))
        conn.execute("INSERT INTO knowledge_chunk_search(chunk_id,document_id,tokens) VALUES(?,?,?)", (chunk_id, identity, _tokens(title + " " + category + " " + chunk)))


def _capacity(conn, additional: int = 1) -> None:
    # Deleted entries remain recoverable and count toward the bounded library.
    if conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0] + additional > MAX_DOCUMENTS:
        raise HTTPException(409, f"知识库达到{MAX_DOCUMENTS}个条目的容量上限（含停用资料）；请联系老板安排扩容，系统不会自动清理知识。")


def _insert(conn, fields: dict, user: dict, source_name: str = "", truncated: bool = False) -> dict:
    identity, stamp = uuid.uuid4().hex, backend.now()
    content = fields["content"]
    conn.execute("""INSERT INTO knowledge_documents(id,title,category,content,source_name,truncated,characters,created_by,owner,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (identity, fields["title"], fields.get("category", ""), content, source_name, int(truncated), len(content), user["id"], user["name"], stamp, stamp))
    _replace_links(conn, identity, fields.get("product_ids", []))
    _reindex_document(conn, identity, fields["title"], fields.get("category", ""), content)
    _append_version(conn, _record(conn, identity), "import" if source_name else "create", user)
    backend._audit(conn, user, "knowledge_add", "保存知识条目：" + fields["title"])
    return _view(_record(conn, identity), user, content=True, conn=conn)


@router.get("/stats")
def stats(request: Request):
    init_storage()
    with backend._connection() as conn:
        documents = conn.execute("SELECT COUNT(*) FROM knowledge_documents WHERE deleted=0").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id WHERE d.deleted=0").fetchone()[0]
        products = conn.execute("SELECT COUNT(*) FROM products WHERE deleted=0").fetchone()[0]
    user = backend._user(request)
    return {"documents": documents, "chunks": chunks, "products": products, "total_sources": documents + products,
            "can_create": _can_create(user), "can_archive": _can_archive(user)}


@router.get("/documents")
def list_documents(request: Request, query: str = Query("", max_length=200), deleted: bool = False,
                   limit: int = Query(40, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
    init_storage()
    pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    match = _match(query)
    with backend._connection() as conn:
        params = [int(deleted), pattern, pattern, pattern]
        conditions = ["title LIKE ? ESCAPE '\\'", "category LIKE ? ESCAPE '\\'", "source_name LIKE ? ESCAPE '\\'"]
        if match:
            # Reuse the chunk index instead of scanning full document bodies.
            # IN deduplicates multiple matching chunks before count/pagination.
            conditions.append("id IN (SELECT document_id FROM knowledge_chunk_search WHERE knowledge_chunk_search MATCH ?)")
            params.append(match)
        where = "deleted=? AND (" + " OR ".join(conditions) + ")"
        count = conn.execute("SELECT COUNT(*) FROM knowledge_documents WHERE " + where, params).fetchone()[0]
        rows = conn.execute("SELECT id,title,category,source_name,truncated,characters,created_by,owner,created_at,updated_at,deleted,revision FROM knowledge_documents WHERE " + where + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        return {"items": [_view(row, backend._user(request), conn=conn) for row in rows], "total": count, "limit": limit, "offset": offset}


def _create_sync(fields: dict, user: dict) -> dict:
    init_storage()
    with backend._connection(write=True) as conn:
        _capacity(conn)
        return _insert(conn, fields, user)


@router.post("/documents")
async def create_document(request: Request):
    user = backend._user(request)
    if not _can_create(user):
        raise HTTPException(403, "仅获授权的老板或管理员可新增企业知识。")
    raw = await _json(request)
    fields = _body(raw)
    return await run_in_threadpool(_create_sync, fields, user)


def _import_sync(prepared: list, user: dict) -> list[dict]:
    init_storage()
    with backend._connection(write=True) as conn:
        _capacity(conn, len(prepared))
        return [_insert(conn, fields, user, meta["name"], meta["truncated"]) for fields, meta in prepared]


@router.post("/documents/import")
async def import_documents(request: Request):
    user = backend._user(request)
    if not _can_create(user):
        raise HTTPException(403, "仅获授权的老板或管理员可导入企业知识。")
    # Bound the streamed multipart request before accepting file bytes.
    received = 0
    async def receive():
        nonlocal received
        event = await request.receive()
        if event["type"] == "http.request":
            received += len(event.get("body", b""))
            if received > 21 * 1024 * 1024:
                raise HTTPException(413, "每次导入总量不能超过20MB。")
        return event
    bounded = Request(request.scope, receive=receive)
    async with bounded.form(max_files=6, max_fields=2, max_part_size=100000) as form:
        uploads = form.getlist("files")
        if not 1 <= len(uploads) <= 6 or any(not isinstance(item, UploadFile) for item in uploads):
            raise HTTPException(400, "请选择1至6份知识文件。")
        if any(isinstance(value, UploadFile) and key != "files" for key, value in form.multi_items()):
            raise HTTPException(400, "请使用知识文件入口导入。")
        category = _field(form.get("category", ""), "分类", 60, empty=True)
        total, prepared = 0, []
        for upload in uploads:
            data = await upload.read(chat_attachments.MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > chat_attachments.MAX_FILE_BYTES or total > 20 * 1024 * 1024:
                raise HTTPException(413, "单份文件不超过10MB，每次总量不超过20MB。")
            item = await run_in_threadpool(chat_attachments.parse_document, upload.filename or "资料.txt", data)
            title = Path(item["name"]).stem.strip()[:120] or "导入资料"
            prepared.append(({"title": title, "content": item["text"], "category": category}, item))
    items = await run_in_threadpool(_import_sync, prepared, user)
    return {"items": items, "original_files_stored": False, "text_limit": chat_attachments.MAX_TEXT_CHARACTERS}


@router.get("/documents/{identity}")
def document(identity: str, request: Request):
    init_storage()
    with backend._connection() as conn:
        return _view(_record(conn, identity, deleted=None), backend._user(request), content=True, conn=conn)


def _update_sync(identity: str, fields: dict, user: dict) -> dict:
    init_storage()
    with backend._connection(write=True) as conn:
        row = _record(conn, identity)
        _edit(row, user)
        _check_revision(row, fields)
        fields = {key: value for key, value in fields.items() if key != "expected_revision"}
        link_ids = fields.pop("product_ids", None)
        changed = {**dict(row), **fields}
        if "content" in fields:
            changed["truncated"] = False
        # Save a revision only when either the current text or its explicit
        # associations actually changed, preserving immutable history forever.
        actual_change = any(changed[key] != row[key] for key in ("title", "category", "content", "truncated"))
        if link_ids is not None:
            old_links = _all_links(conn, identity)
            _replace_links(conn, identity, link_ids)
            actual_change = actual_change or old_links != link_ids
        if not actual_change:
            return _view(row, user, content=True, conn=conn)
        conn.execute("UPDATE knowledge_documents SET title=?,category=?,content=?,characters=?,truncated=?,updated_at=?,revision=revision+1 WHERE id=?",
                     (changed["title"], changed["category"], changed["content"], len(changed["content"]), int(changed["truncated"]), backend.now(), identity))
        _reindex_document(conn, identity, changed["title"], changed["category"], changed["content"])
        updated = _record(conn, identity)
        _append_version(conn, updated, "update", user)
        backend._audit(conn, user, "knowledge_update", "更新知识条目：" + changed["title"])
        return _view(updated, user, content=True, conn=conn)


@router.patch("/documents/{identity}")
async def update_document(identity: str, request: Request):
    user = backend._user(request)
    if not _privileged(user):
        raise HTTPException(403, "仅获授权的老板或管理员可编辑企业知识。")
    raw = await _json(request)
    return await run_in_threadpool(_update_sync, identity, _body(raw, partial=True), user)


@router.delete("/documents/{identity}")
def delete_document(identity: str, request: Request, payload: dict | None = Body(None)):
    user = backend._user(request)
    if not _can_archive(user):
        raise HTTPException(403, "老板尚未授予企业知识停用与恢复权限。")
    payload = {} if payload is None else payload
    if set(payload) - {"expected_revision"}:
        raise HTTPException(400, "停用知识只支持核对当前版本。")
    init_storage()
    with backend._connection(write=True) as conn:
        row = _record(conn, identity, deleted=None)
        _check_revision(row, payload)
        if not row["deleted"]:
            conn.execute("UPDATE knowledge_documents SET deleted=1,updated_at=?,revision=revision+1 WHERE id=?", (backend.now(), identity))
            _append_version(conn, _record(conn, identity, deleted=None), "archive", user)
            backend._audit(conn, user, "knowledge_delete", "停用知识条目：" + row["title"])
    return {"ok": True}


@router.post("/documents/{identity}/restore")
def restore_document(identity: str, request: Request, payload: dict | None = Body(None)):
    user = backend._user(request)
    if not _can_archive(user):
        raise HTTPException(403, "老板尚未授予企业知识停用与恢复权限。")
    payload = {} if payload is None else payload
    if set(payload) - {"expected_revision"}:
        raise HTTPException(400, "恢复知识只支持核对当前版本。")
    init_storage()
    with backend._connection(write=True) as conn:
        row = _record(conn, identity, deleted=None)
        _check_revision(row, payload)
        if row["deleted"]:
            conn.execute("UPDATE knowledge_documents SET deleted=0,updated_at=?,revision=revision+1 WHERE id=?", (backend.now(), identity))
            _append_version(conn, _record(conn, identity), "restore", user)
            backend._audit(conn, user, "knowledge_restore", "恢复知识条目：" + row["title"])
        return _view(_record(conn, identity), user, content=True, conn=conn)


def _history_access(conn, identity: str, user: dict):
    row = _record(conn, identity, deleted=None)
    if not (_can_edit(row, user) or _can_archive(user)):
        raise HTTPException(403, "仅知识维护者可查看含停用内容的历史版本。")
    return row


def _version_view(row, *, content: bool = False) -> dict:
    fields = ("document_id", "revision", "title", "category", "source_name", "characters", "owner",
              "created_at", "updated_at", "action", "changed_by", "changed_by_name", "recorded_at")
    result = {key: row[key] for key in fields}
    result.update(id=row["document_id"], deleted=bool(row["deleted"]), truncated=bool(row["truncated"]))
    result["product_ids"] = json.loads(row["product_ids"])
    result["related_products"] = json.loads(row["related_products"])
    if content:
        result["content"] = row["content"]
    return result


@router.get("/documents/{identity}/versions")
def versions(identity: str, request: Request, limit: int = Query(40, ge=1, le=100), offset: int = Query(0, ge=0, le=100000)):
    init_storage()
    with backend._connection() as conn:
        _history_access(conn, identity, backend._user(request))
        total = conn.execute("SELECT COUNT(*) FROM knowledge_document_versions WHERE document_id=?", (identity,)).fetchone()[0]
        # Do not read old full texts merely to populate the history list.
        rows = conn.execute("""SELECT document_id,revision,title,category,source_name,truncated,characters,owner,created_at,
            updated_at,deleted,product_ids,related_products,action,changed_by,changed_by_name,recorded_at
            FROM knowledge_document_versions WHERE document_id=? ORDER BY revision DESC LIMIT ? OFFSET ?""", (identity, limit, offset)).fetchall()
        return {"items": [_version_view(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/documents/{identity}/versions/{revision}")
def version(identity: str, revision: int, request: Request):
    _revision(revision)
    init_storage()
    with backend._connection() as conn:
        _history_access(conn, identity, backend._user(request))
        row = conn.execute("SELECT * FROM knowledge_document_versions WHERE document_id=? AND revision=?", (identity, revision)).fetchone()
        if row is None:
            raise HTTPException(404, "此历史版本不存在。")
        return _version_view(row, content=True)


def _reference(row, excerpt: str, conn) -> dict:
    return {"id": row["id"], "document_id": row["id"], "kind": "knowledge", "title": row["title"],
            "excerpt": excerpt[:CHUNK_SIZE], "category": row["category"], "source_name": row["source_name"],
            "truncated": bool(row["truncated"]), "updated_at": row["updated_at"], "revision": row["revision"],
            "product_ids": [product["id"] for product in _linked_products(conn, row["id"])]}


def get_documents_by_ids(ids: list[str]) -> list[dict]:
    """Re-read active references; client excerpts are never trusted."""
    identities = _ids(ids)
    init_storage()
    with backend._connection() as conn:
        items = []
        for identity in identities:
            row = conn.execute("SELECT * FROM knowledge_documents WHERE id=? AND deleted=0", (identity,)).fetchone()
            if row:
                items.append(_reference(row, row["content"][:CHUNK_SIZE], conn))
        return items


def retrieve(query: str, limit: int = MAX_REFERENCES) -> dict:
    _limit(limit)
    match = _match(query)
    init_storage()
    items = []
    if match:
        with backend._connection() as conn:
            # A very long document can contribute many top-ranked chunks. Read
            # enough bounded candidates to still return distinct documents.
            max_chunks = (MAX_CONTENT + CHUNK_SIZE - CHUNK_OVERLAP - 1) // (CHUNK_SIZE - CHUNK_OVERLAP)
            candidates = (limit - 1) * max_chunks + 1
            rows = conn.execute("""SELECT d.id,d.title,d.category,d.source_name,d.truncated,d.updated_at,d.revision,c.content AS excerpt FROM knowledge_chunk_search s
                JOIN knowledge_chunks c ON c.id=s.chunk_id JOIN knowledge_documents d ON d.id=c.document_id
                WHERE knowledge_chunk_search MATCH ? AND d.deleted=0
                ORDER BY bm25(knowledge_chunk_search),d.updated_at DESC,c.position LIMIT ?""", (match, candidates)).fetchall()
            seen = set()
            for row in rows:
                if row["id"] not in seen:
                    items.append(_reference(row, row["excerpt"], conn))
                    seen.add(row["id"])
                if len(items) == limit:
                    break
    return {"items": items, "scope": "本地文字检索片段；仅包含仍有效的知识条目，无命中不会返回无关资料。", "matched": len(items)}


def retrieve_for_products(product_ids: list[str], limit: int = MAX_REFERENCES) -> list[dict]:
    """Only explicitly linked active documents and products, without AI guesses."""
    _limit(limit)
    identities = _product_ids(product_ids)
    if not identities:
        return []
    init_storage()
    placeholders = ",".join("?" for _ in identities)
    with backend._connection() as conn:
        rows = conn.execute(f"""SELECT DISTINCT d.* FROM knowledge_documents d
            JOIN knowledge_document_products k ON k.document_id=d.id JOIN products p ON p.id=k.product_id
            WHERE d.deleted=0 AND p.deleted=0 AND p.id IN ({placeholders})
            ORDER BY d.updated_at DESC,d.id LIMIT ?""", [*identities, limit]).fetchall()
        return [_reference(row, row["content"][:CHUNK_SIZE], conn) for row in rows]
