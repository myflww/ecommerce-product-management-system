"""Explicit, reversible merging of already saved company products.

Keep the chosen company's product identity and metadata, while moving every
image, platform/SKU row, quote history reference and source mapping intact.
No picture is deleted or deduplicated and no storefront status is inferred.
Register this router before backend.router and call init_storage at startup.
product_management.restore must call assert_restore_allowed in its transaction.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request

import backend
import knowledge_store
try:
    import permissions
except ImportError:
    # Fail closed while an older installation lacks the new permission service.
    # The isolated development tests inject an adapter, never production roles.
    permissions = None

router = APIRouter(dependencies=[Depends(backend.require_user)])
MAX_PRODUCTS = 50
MAX_IMAGES = 30
MAX_LISTINGS = 100


def init_storage() -> None:
    with backend._connection(write=True) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS product_merge_operations (
            id TEXT PRIMARY KEY, target_id TEXT NOT NULL,
            source_ids TEXT NOT NULL, before_snapshot TEXT NOT NULL,
            after_token TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL, undone_at TEXT NOT NULL DEFAULT '',
            knowledge_changes TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS product_merge_sources (
            source_id TEXT PRIMARY KEY REFERENCES products(id),
            target_id TEXT NOT NULL REFERENCES products(id),
            merge_id TEXT NOT NULL REFERENCES product_merge_operations(id)
        );
        CREATE INDEX IF NOT EXISTS product_merge_target_idx
            ON product_merge_operations(target_id,created_at DESC);
        """)
        if not any(row["name"] == "knowledge_changes" for row in conn.execute("PRAGMA table_info(product_merge_operations)")):
            conn.execute("ALTER TABLE product_merge_operations ADD COLUMN knowledge_changes TEXT NOT NULL DEFAULT '{}'")


def _has_table(conn, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def merge_info(conn, product_id: str) -> dict | None:
    """Resolve any chain of merges; no login or permission checks are implied."""
    if not _has_table(conn, "product_merge_sources"):
        return None
    current, first, visited = product_id, None, set()
    while current not in visited:
        visited.add(current)
        row = conn.execute("SELECT * FROM product_merge_sources WHERE source_id=?", (current,)).fetchone()
        if row is None:
            return {**first, "merged_into": current} if first else None
        if first is None:
            first = dict(row)
        current = row["target_id"]
    raise HTTPException(409, "商品合并关系异常，请联系管理员检查数据备份。")


def assert_restore_allowed(conn, product_id: str) -> None:
    info = merge_info(conn, product_id)
    if info:
        target = conn.execute("SELECT code FROM products WHERE id=?", (info["merged_into"],)).fetchone()
        label = target["code"] if target else info["merged_into"]
        raise HTTPException(409, f"此商品已合并至 {label}，请在合并记录中撤销合并，不能单独恢复。")


def assert_purge_allowed(conn, product_id: str) -> None:
    """A purge cannot destroy the live main or source of an unfinished merge.

    Undone journals deliberately have no FK to their former main product, so a
    later authorized purge can keep the immutable historical snapshot intact.
    Call inside the purge preview and again inside the purge write transaction.
    """
    if not _has_table(conn, "product_merge_operations"):
        return
    active = conn.execute("""SELECT id FROM product_merge_operations
        WHERE undone_at='' AND (target_id=? OR id IN (
            SELECT merge_id FROM product_merge_sources WHERE source_id=?)) LIMIT 1""", (product_id, product_id)).fetchone()
    if active:
        raise HTTPException(409, "此商品涉及尚未撤销的合并，不能永久清理；请先撤销或处理合并记录。")


def _selection(payload: dict, *, commit: bool = False) -> tuple[list[str], str]:
    allowed = {"ids", "target_id", "expected_token"} if commit else {"ids", "target_id"}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise HTTPException(400, "请提交所选商品、保留的主商品和当前预览版本。")
    ids, target = payload.get("ids"), payload.get("target_id")
    if not isinstance(ids, list) or not 2 <= len(ids) <= MAX_PRODUCTS:
        raise HTTPException(400, f"请选择 2 至 {MAX_PRODUCTS} 个商品合并。")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 64 for item in ids):
        raise HTTPException(400, "商品编号格式不正确。")
    ids = [item.strip() for item in ids]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "所选商品有重复，请重新选择。")
    if not isinstance(target, str) or target not in ids:
        raise HTTPException(400, "请选择其中一个商品作为保留的主商品。")
    return sorted(ids), target


def _token(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_token(raw) -> str:
    if not isinstance(raw, str) or not re.fullmatch(r"[a-f0-9]{64}", raw):
        raise HTTPException(400, "缺少有效的预览版本，请重新预览。")
    return raw


def _require_merge(user: dict) -> None:
    if permissions is None:
        raise HTTPException(503, "商品合并权限组件不完整，请更新软件后重试。")
    permissions.require(user, "products.merge")


def _authorized(conn, ids: list[str], user: dict, *, deleted=False) -> list:
    _require_merge(user)
    rows = [backend._product(conn, identity, include_deleted=deleted) for identity in ids]
    for row in rows:
        permissions.require_product(user, row, "edit")
    return rows


def _source_key_columns(conn) -> list[str]:
    # Source identity must stay stable when a company product is merged. This
    # supports a single key or a platform/store/product/variant composite key.
    columns = conn.execute("PRAGMA table_info(store_import_sources)").fetchall()
    keys = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
    if "product_id" not in {row["name"] for row in columns} or not keys or "product_id" in keys:
        raise HTTPException(503, "店铺导入来源表尚不支持合并，请更新软件后重试。")
    return keys


def _knowledge_snapshot(conn, ids: list[str]) -> list[dict]:
    if not _has_table(conn, "knowledge_document_products"):
        return []
    marks = ",".join("?" for _ in ids)
    result = []
    for row in conn.execute(f"""SELECT id,revision FROM knowledge_documents
        WHERE id IN (SELECT document_id FROM knowledge_document_products WHERE product_id IN ({marks}))
        ORDER BY id""", ids):
        result.append({"id": row["id"], "revision": row["revision"], "links": [dict(link) for link in conn.execute(
            "SELECT product_id,position FROM knowledge_document_products WHERE document_id=? ORDER BY position,product_id", (row["id"],))]})
    return result


def _snapshot(conn, ids: list[str], *, include_knowledge: bool = True) -> dict:
    marks = ",".join("?" for _ in ids)
    result = {name: [dict(row) for row in conn.execute(sql, ids)] for name, sql in (
        ("products", f"SELECT * FROM products WHERE id IN ({marks}) ORDER BY id"),
        ("images", f"SELECT * FROM images WHERE product_id IN ({marks}) ORDER BY id"),
        ("listings", f"SELECT * FROM product_listings WHERE product_id IN ({marks}) ORDER BY id"),
        ("decisions", f"SELECT * FROM decisions WHERE product_id IN ({marks}) ORDER BY id"),
    )}
    for table in ("price_tracking", "price_history"):
        if _has_table(conn, table):
            order = "listing_id" if table == "price_tracking" else "id"
            result[table] = [dict(row) for row in conn.execute(
                f"SELECT t.* FROM {table} t JOIN product_listings l ON l.id=t.listing_id "
                f"WHERE l.product_id IN ({marks}) ORDER BY t.{order}", ids)]
        else:
            result[table] = []
    result["external_sources"] = []
    if _has_table(conn, "store_import_sources"):
        _source_key_columns(conn)
        result["external_sources"] = sorted((dict(row) for row in conn.execute(
            f"SELECT * FROM store_import_sources WHERE product_id IN ({marks})", ids)),
            key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
    if include_knowledge:
        result["knowledge_links"] = _knowledge_snapshot(conn, ids)
    return result


def _remap_knowledge(conn, source_ids: list[str], target_id: str, user: dict) -> dict:
    helper = getattr(knowledge_store, "remap_product_links", None)
    if callable(helper):
        return helper(conn, source_ids, target_id, user)
    if _knowledge_snapshot(conn, source_ids):
        raise HTTPException(503, "知识关联迁移组件不完整，请更新软件后合并。")
    return {"documents": []}


def _knowledge_changes(operation) -> dict:
    raw = operation["knowledge_changes"] if "knowledge_changes" in operation.keys() else None
    try:
        changes = json.loads(raw) if raw else {}
        if changes is None or changes == {}:
            return {"documents": []}
        if not isinstance(changes, dict) or not isinstance(changes.get("documents"), list):
            raise ValueError
        return changes
    except (TypeError, ValueError):
        raise HTTPException(409, "合并记录中的知识关联版本无法读取，请先检查数据备份。") from None


def _check_knowledge_undo(conn, changes: dict, user: dict) -> None:
    if not changes["documents"]:
        return
    if not all(_has_table(conn, name) for name in ("knowledge_documents", "knowledge_document_products", "knowledge_document_versions")):
        raise HTTPException(409, "合并后的知识关联或版本资料缺失，不能安全撤销。")
    helper = getattr(knowledge_store, "assert_product_link_undo_allowed", None)
    if not callable(helper):
        raise HTTPException(503, "知识关联撤销组件不完整，请更新软件后重试。")
    helper(conn, changes, user)


def _undo_knowledge(conn, changes: dict, user: dict) -> None:
    if not changes["documents"]:
        return
    helper = getattr(knowledge_store, "undo_product_link_remap", None)
    if not callable(helper):
        raise HTTPException(503, "知识关联撤销组件不完整，请更新软件后重试。")
    helper(conn, changes, user)


def _check_size(snapshot: dict) -> None:
    if len(snapshot["images"]) > MAX_IMAGES:
        raise HTTPException(400, f"合并后超过 {MAX_IMAGES} 张图片，请减少所选商品后重试；原图片未删除。")
    if len(snapshot["listings"]) > MAX_LISTINGS:
        raise HTTPException(400, f"合并后超过 {MAX_LISTINGS} 条平台 SKU，请分开管理。")


def _preview(conn, ids: list[str], target_id: str, user: dict) -> tuple[dict, dict]:
    rows = _authorized(conn, ids, user)
    for row in rows:
        if row["id"] != target_id:
            permissions.require_product(user, row, "delete")
    snapshot = _snapshot(conn, ids)
    _check_size(snapshot)
    products = [backend._serialize_product(conn, row, user) for row in rows]
    preview = {
        "target_id": target_id, "ids": ids, "products": products,
        "image_count": len(snapshot["images"]), "listing_count": len(snapshot["listings"]),
        "expected_token": _token({"target_id": target_id, "snapshot": snapshot}),
        "metadata_policy": "保留主商品的名称、负责人、分类、规格与备注；其他商品原资料保留在合并记录。",
        "listing_policy": "保留所有平台、店铺、SKU、上下架状态、链接和报价历史，不自动合并 SKU。",
    }
    return preview, snapshot


@router.post("/api/products/merge/preview")
def preview_merge(request: Request, payload: dict = Body(...)):
    ids, target_id = _selection(payload)
    with backend._connection() as conn:
        # A single SQLite read transaction makes metadata and child rows one
        # snapshot even if another operator edits the catalog while previewing.
        conn.execute("BEGIN")
        preview, _ = _preview(conn, ids, target_id, backend._user(request))
        return preview


@router.post("/api/products/merge")
def merge_products(request: Request, payload: dict = Body(...)):
    ids, target_id = _selection(payload, commit=True)
    expected, user = _validate_token(payload["expected_token"]), backend._user(request)
    with backend._connection(write=True) as conn:
        preview, before = _preview(conn, ids, target_id, user)
        if not hmac.compare_digest(expected, preview["expected_token"]):
            raise HTTPException(409, "商品资料、SKU 或报价已更新，请重新预览后合并。")
        source_ids = [identity for identity in ids if identity != target_id]
        marks = ",".join("?" for _ in source_ids)
        stamp, merge_id = backend.now(), uuid.uuid4().hex
        # UPDATE ownership rather than recreate rows: all existing media URLs,
        # embeddings, price tracking context tokens and history IDs survive.
        conn.execute(f"UPDATE images SET product_id=? WHERE product_id IN ({marks})", [target_id, *source_ids])
        position = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM product_listings WHERE product_id=?", (target_id,)).fetchone()[0]
        for source_id in source_ids:
            for listing in conn.execute("SELECT id FROM product_listings WHERE product_id=? ORDER BY position,id", (source_id,)).fetchall():
                conn.execute("UPDATE product_listings SET product_id=?,position=? WHERE id=?", (target_id, position, listing["id"]))
                position += 1
        conn.execute(f"UPDATE decisions SET product_id=? WHERE product_id IN ({marks})", [target_id, *source_ids])
        if _has_table(conn, "store_import_sources"):
            conn.execute(f"UPDATE store_import_sources SET product_id=? WHERE product_id IN ({marks})", [target_id, *source_ids])
        knowledge_changes = _remap_knowledge(conn, source_ids, target_id, user)
        conn.execute(f"UPDATE products SET deleted=1,updated_at=? WHERE id IN ({marks})", [stamp, *source_ids])
        # The main product's metadata and historical global status stay exact.
        # Listing initialization is true if any selected product supplied it.
        initialized = max(row["listings_initialized"] for row in before["products"])
        conn.execute("UPDATE products SET updated_at=?,listings_initialized=? WHERE id=?", (stamp, initialized, target_id))
        after_token = _token(_snapshot(conn, ids))
        conn.execute("INSERT INTO product_merge_operations(id,target_id,source_ids,before_snapshot,after_token,created_by,created_at,knowledge_changes) VALUES(?,?,?,?,?,?,?,?)",
                     (merge_id, target_id, json.dumps(source_ids), json.dumps(before, ensure_ascii=False, separators=(",", ":")), after_token, user["id"], stamp, json.dumps(knowledge_changes, ensure_ascii=False, separators=(",", ":"))))
        for source_id in source_ids:
            conn.execute("INSERT INTO product_merge_sources VALUES(?,?,?)", (source_id, target_id, merge_id))
        identities = "、".join(row["code"] for row in before["products"])
        main = backend._product(conn, target_id)
        backend._audit(conn, user, "merge_products", f"人工合并 {identities}，保留 {main['code']}：{main['name']}", target_id)
        return {"ok": True, "merge_id": merge_id, "merged_ids": source_ids,
                "product": backend._serialize_product(conn, main, user), "undo_token": after_token,
                "can_undo": True}


def _operation(conn, merge_id: str, user: dict):
    if not isinstance(merge_id, str) or not re.fullmatch(r"[a-f0-9]{32}", merge_id):
        raise HTTPException(404, "合并记录不存在。")
    operation = conn.execute("SELECT * FROM product_merge_operations WHERE id=?", (merge_id,)).fetchone()
    if operation is None:
        raise HTTPException(404, "合并记录不存在。")
    ids = sorted([operation["target_id"], *json.loads(operation["source_ids"])])
    _authorized(conn, ids, user, deleted=True)
    return operation, ids


def _operation_view(conn, operation, ids: list[str], user: dict) -> dict:
    before = json.loads(operation["before_snapshot"])
    current = _token(_snapshot(conn, ids, include_knowledge="knowledge_links" in before))
    may_restore = True
    for source_id in json.loads(operation["source_ids"]):
        try:
            permissions.require_product(user, backend._product(conn, source_id, include_deleted=True), "restore")
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            may_restore = False
    if not operation["undone_at"]:
        try:
            _check_knowledge_undo(conn, _knowledge_changes(operation), user)
        except HTTPException as exc:
            if exc.status_code not in {403, 409}:
                raise
            may_restore = False
    return {"id": operation["id"], "target_id": operation["target_id"],
            "source_ids": json.loads(operation["source_ids"]), "created_at": operation["created_at"],
            "undone_at": operation["undone_at"], "original_products": before["products"],
            "can_undo": may_restore and not operation["undone_at"] and hmac.compare_digest(current, operation["after_token"]),
            "undo_token": operation["after_token"]}


@router.get("/api/product-merges")
def recent_merges(request: Request):
    """Return up to twenty recent journals this operator may inspect and undo."""
    user = backend._user(request)
    _require_merge(user)
    items = []
    with backend._connection() as conn:
        conn.execute("BEGIN")
        for operation in conn.execute("SELECT * FROM product_merge_operations ORDER BY created_at DESC,id DESC LIMIT 100").fetchall():
            ids = sorted([operation["target_id"], *json.loads(operation["source_ids"])])
            try:
                _authorized(conn, ids, user, deleted=True)
            except HTTPException as exc:
                if exc.status_code == 403 or (exc.status_code == 404 and operation["undone_at"]):
                    continue
                raise
            items.append(_operation_view(conn, operation, ids, user))
            if len(items) == 20:
                break
    return {"items": items}


@router.get("/api/products/{product_id}/merge-history")
def product_merge_history(product_id: str, request: Request):
    """The journal remains discoverable after the original browser tab closes."""
    user = backend._user(request)
    with backend._connection() as conn:
        conn.execute("BEGIN")
        product = backend._product(conn, product_id, include_deleted=True)
        _require_merge(user)
        permissions.require_product(user, product, "edit")
        info = merge_info(conn, product_id)
        operations = conn.execute("""SELECT * FROM product_merge_operations
            WHERE target_id=? OR id IN (SELECT merge_id FROM product_merge_sources WHERE source_id=?)
            ORDER BY created_at DESC,id DESC LIMIT 20""", (product_id, product_id)).fetchall()
        items = []
        for operation in operations:
            ids = sorted([operation["target_id"], *json.loads(operation["source_ids"])])
            try:
                _authorized(conn, ids, user, deleted=True)
            except HTTPException as exc:
                if exc.status_code == 403:
                    continue
                raise
            items.append(_operation_view(conn, operation, ids, user))
        return {"product_id": product_id, "merged_into": info["merged_into"] if info else None, "items": items}


@router.get("/api/product-merges/{merge_id}")
def get_merge(merge_id: str, request: Request):
    with backend._connection() as conn:
        conn.execute("BEGIN")
        user = backend._user(request)
        operation, ids = _operation(conn, merge_id, user)
        return _operation_view(conn, operation, ids, user)


def _quoted(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def _restore_rows(conn, table: str, rows: list[dict], fields: tuple[str, ...]) -> None:
    assignments = ",".join(f"{_quoted(field)}=?" for field in fields)
    for row in rows:
        conn.execute(f"UPDATE {table} SET {assignments} WHERE id=?", [*[row[field] for field in fields], row["id"]])


@router.post("/api/product-merges/{merge_id}/undo")
def undo_merge(merge_id: str, request: Request, payload: dict = Body(...)):
    if not isinstance(payload, dict) or set(payload) != {"expected_token"}:
        raise HTTPException(400, "请提供合并记录的当前版本。")
    expected, user = _validate_token(payload["expected_token"]), backend._user(request)
    with backend._connection(write=True) as conn:
        operation, ids = _operation(conn, merge_id, user)
        if operation["undone_at"]:
            raise HTTPException(409, "此合并已撤销，请刷新商品库。")
        for source_id in json.loads(operation["source_ids"]):
            permissions.require_product(user, backend._product(conn, source_id, include_deleted=True), "restore")
        before = json.loads(operation["before_snapshot"])
        if not hmac.compare_digest(expected, operation["after_token"]) or not hmac.compare_digest(_token(_snapshot(conn, ids, include_knowledge="knowledge_links" in before)), operation["after_token"]):
            raise HTTPException(409, "合并后的商品已有后续修改，不能直接撤销；请先核对当前资料。")
        knowledge_changes = _knowledge_changes(operation)
        _check_knowledge_undo(conn, knowledge_changes, user)
        _restore_rows(conn, "images", before["images"], ("product_id",))
        _restore_rows(conn, "product_listings", before["listings"], ("product_id", "position"))
        _restore_rows(conn, "decisions", before["decisions"], ("product_id",))
        if before["external_sources"]:
            keys = _source_key_columns(conn)
            where = " AND ".join(f"{_quoted(key)}=?" for key in keys)
            for row in before["external_sources"]:
                conn.execute(f"UPDATE store_import_sources SET product_id=? WHERE {where}", [row["product_id"], *[row[key] for key in keys]])
        _restore_rows(conn, "products", before["products"], ("updated_at", "deleted", "listings_initialized"))
        _undo_knowledge(conn, knowledge_changes, user)
        conn.execute("DELETE FROM product_merge_sources WHERE merge_id=?", (merge_id,))
        conn.execute("UPDATE product_merge_operations SET undone_at=? WHERE id=?", (backend.now(), merge_id))
        target = backend._product(conn, operation["target_id"])
        backend._audit(conn, user, "undo_product_merge", f"撤销商品合并：{target['code']} · {target['name']}", operation["target_id"])
        return {"ok": True, "restored_ids": json.loads(operation["source_ids"]),
                "product": backend._serialize_product(conn, target, user)}
