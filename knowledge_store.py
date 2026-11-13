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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

import backend
import chat_attachments

router = APIRouter(prefix="/api/knowledge", dependencies=[Depends(backend.require_user)])
MAX_DOCUMENTS = 2000
MAX_CONTENT = 100000
MAX_JSON_BYTES = 1024 * 1024
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 120
MAX_REFERENCES = 6
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
                present = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('knowledge_documents','knowledge_product_dirty','knowledge_chunk_search','knowledge_product_search')").fetchone()[0]
            if present == 4:
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
    if not isinstance(raw, dict) or set(raw) - {"title", "content", "category"} or not raw:
        raise HTTPException(400, "知识条目只支持标题、正文和分类。")
    if not partial and not {"title", "content"}.issubset(raw):
        raise HTTPException(400, "请填写知识标题和正文。")
    result = {}
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


def _edit(row, user: dict) -> None:
    if user["role"] != "admin" and row["created_by"] != user["id"]:
        raise HTTPException(403, "只有管理员或条目录入人可以修改此知识。")


def _view(row, user: dict | None = None, *, content: bool = False) -> dict:
    fields = ("id", "title", "category", "source_name", "characters", "owner", "created_at", "updated_at")
    result = {key: row[key] for key in fields}
    result.update(truncated=bool(row["truncated"]), deleted=bool(row["deleted"]))
    if user:
        result["can_edit"] = user["role"] == "admin" or row["created_by"] == user["id"]
    if content:
        result["content"] = row["content"]
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
        raise HTTPException(409, f"知识库最多保存{MAX_DOCUMENTS}个条目，请先整理现有资料。")


def _insert(conn, fields: dict, user: dict, source_name: str = "", truncated: bool = False) -> dict:
    identity, stamp = uuid.uuid4().hex, backend.now()
    content = fields["content"]
    conn.execute("""INSERT INTO knowledge_documents(id,title,category,content,source_name,truncated,characters,created_by,owner,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (identity, fields["title"], fields.get("category", ""), content, source_name, int(truncated), len(content), user["id"], user["name"], stamp, stamp))
    _reindex_document(conn, identity, fields["title"], fields.get("category", ""), content)
    backend._audit(conn, user, "knowledge_add", "保存知识条目：" + fields["title"])
    return _view(_record(conn, identity), user, content=True)


@router.get("/stats")
def stats():
    init_storage()
    with backend._connection() as conn:
        documents = conn.execute("SELECT COUNT(*) FROM knowledge_documents WHERE deleted=0").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id WHERE d.deleted=0").fetchone()[0]
    return {"documents": documents, "chunks": chunks}


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
        rows = conn.execute("SELECT id,title,category,source_name,truncated,characters,created_by,owner,created_at,updated_at,deleted FROM knowledge_documents WHERE " + where + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    return {"items": [_view(row, backend._user(request)) for row in rows], "total": count, "limit": limit, "offset": offset}


@router.post("/documents")
async def create_document(request: Request):
    raw = await _json(request)
    fields = _body(raw)
    init_storage()
    with backend._connection(write=True) as conn:
        _capacity(conn)
        return _insert(conn, fields, backend._user(request))


@router.post("/documents/import")
async def import_documents(request: Request):
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
    init_storage()
    with backend._connection(write=True) as conn:
        _capacity(conn, len(prepared))
        items = [_insert(conn, fields, backend._user(request), meta["name"], meta["truncated"]) for fields, meta in prepared]
    return {"items": items, "original_files_stored": False, "text_limit": chat_attachments.MAX_TEXT_CHARACTERS}


@router.get("/documents/{identity}")
def document(identity: str, request: Request):
    init_storage()
    with backend._connection() as conn:
        return _view(_record(conn, identity, deleted=None), backend._user(request), content=True)


@router.patch("/documents/{identity}")
async def update_document(identity: str, request: Request):
    raw = await _json(request)
    fields = _body(raw, partial=True)
    init_storage()
    with backend._connection(write=True) as conn:
        row = _record(conn, identity)
        _edit(row, backend._user(request))
        changed = {**dict(row), **fields}
        if "content" in fields:
            changed["truncated"] = False
        conn.execute("UPDATE knowledge_documents SET title=?,category=?,content=?,characters=?,truncated=?,updated_at=? WHERE id=?",
                     (changed["title"], changed["category"], changed["content"], len(changed["content"]), int(changed["truncated"]), backend.now(), identity))
        _reindex_document(conn, identity, changed["title"], changed["category"], changed["content"])
        backend._audit(conn, backend._user(request), "knowledge_update", "更新知识条目：" + changed["title"])
        return _view(_record(conn, identity), backend._user(request), content=True)


@router.delete("/documents/{identity}")
def delete_document(identity: str, request: Request):
    init_storage()
    with backend._connection(write=True) as conn:
        row = _record(conn, identity, deleted=None)
        _edit(row, backend._user(request))
        if not row["deleted"]:
            conn.execute("UPDATE knowledge_documents SET deleted=1,updated_at=? WHERE id=?", (backend.now(), identity))
            backend._audit(conn, backend._user(request), "knowledge_delete", "移除知识条目：" + row["title"])
    return {"ok": True}


@router.post("/documents/{identity}/restore")
def restore_document(identity: str, request: Request):
    init_storage()
    with backend._connection(write=True) as conn:
        row = _record(conn, identity, deleted=None)
        _edit(row, backend._user(request))
        if row["deleted"]:
            conn.execute("UPDATE knowledge_documents SET deleted=0,updated_at=? WHERE id=?", (backend.now(), identity))
            backend._audit(conn, backend._user(request), "knowledge_restore", "恢复知识条目：" + row["title"])
        return _view(_record(conn, identity), backend._user(request), content=True)


def _reference(row, excerpt: str) -> dict:
    return {"id": row["id"], "document_id": row["id"], "kind": "knowledge", "title": row["title"],
            "excerpt": excerpt[:CHUNK_SIZE], "category": row["category"], "source_name": row["source_name"],
            "truncated": bool(row["truncated"]), "updated_at": row["updated_at"]}


def get_documents_by_ids(ids: list[str]) -> list[dict]:
    """Re-read active references; client excerpts are never trusted."""
    identities = _ids(ids)
    init_storage()
    with backend._connection() as conn:
        items = []
        for identity in identities:
            row = conn.execute("SELECT * FROM knowledge_documents WHERE id=? AND deleted=0", (identity,)).fetchone()
            if row:
                items.append(_reference(row, row["content"][:CHUNK_SIZE]))
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
            rows = conn.execute("""SELECT d.id,d.title,d.category,d.source_name,d.truncated,d.updated_at,c.content AS excerpt FROM knowledge_chunk_search s
                JOIN knowledge_chunks c ON c.id=s.chunk_id JOIN knowledge_documents d ON d.id=c.document_id
                WHERE knowledge_chunk_search MATCH ? AND d.deleted=0
                ORDER BY bm25(knowledge_chunk_search),d.updated_at DESC,c.position LIMIT ?""", (match, candidates)).fetchall()
            seen = set()
            for row in rows:
                if row["id"] not in seen:
                    items.append(_reference(row, row["excerpt"]))
                    seen.add(row["id"])
                if len(items) == limit:
                    break
    return {"items": items, "scope": "本地文字检索片段；仅包含仍有效的知识条目，无命中不会返回无关资料。", "matched": len(items)}
