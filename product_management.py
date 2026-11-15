"""Recoverable product management using the shared library's existing permissions.

Register this router before ``backend.router`` so the literal ``products/trash``
route is not interpreted as a product ID. Permanent cleanup is owner-only and
creates a complete backup before removing unreferenced originals.
"""
from __future__ import annotations

import logging
import hashlib
import importlib
import json
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

import backend
import permissions

router = APIRouter(dependencies=[Depends(backend.require_user)])
MAX_BULK_PRODUCTS = 100


def _may_view_trash(product, user):
    if not (permissions.can_product(user, product, 'restore') or permissions.can_product(user, product, 'delete')):
        raise HTTPException(403, '没有查看此回收站商品的权限。')


def _optional_guard(module_name, function_name, connection, product_id):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            return
        raise
    guard = getattr(module, function_name, None)
    if callable(guard):
        guard(connection, product_id)


def _purge_plan(connection):
    rows = connection.execute('SELECT id,code,name,updated_at FROM products WHERE deleted=1 ORDER BY id').fetchall()
    blocked = []
    # Unknown, newly added business references must never be silently erased.
    foreign_references = []
    for table in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        name = table['name']
        if name in {'images', 'product_listings', 'decisions', 'store_import_sources'} or name.startswith('sqlite_'):
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        for key in connection.execute('PRAGMA foreign_key_list(' + quoted + ')').fetchall():
            if key['table'] == 'products':
                column = '"' + key['from'].replace('"', '""') + '"'
                foreign_references.append((quoted, column))
    for row in rows:
        try:
            _optional_guard('product_merge', 'assert_purge_allowed', connection, row['id'])
            _optional_guard('knowledge_store', 'assert_product_purge_allowed', connection, row['id'])
            if any(connection.execute('SELECT 1 FROM ' + table + ' WHERE ' + column + '=? LIMIT 1', (row['id'],)).fetchone()
                   for table, column in foreign_references):
                raise HTTPException(409, '该商品仍被其他公司资料引用，请先处理关联后再永久清理。')
        except HTTPException as error:
            blocked.append({**{name: row[name] for name in ('id','code','name')}, 'reason':str(error.detail)})
    token = hashlib.sha256(json.dumps([(row['id'], row['updated_at']) for row in rows], ensure_ascii=False).encode()).hexdigest()
    count = connection.execute('SELECT COUNT(*) FROM images i JOIN products p ON p.id=i.product_id WHERE p.deleted=1').fetchone()[0]
    return {'token':token, 'count':len(rows), 'image_count':count,
            'items':[{name:row[name] for name in ('id','code','name')} for row in rows], 'blocked':blocked,
            'can_purge':bool(rows) and not blocked, 'backup_required':True}


def _media_candidate(folder, name):
    root = Path(backend.DATA_DIR).resolve()
    directory = (root / folder).resolve()
    path = (directory / name).resolve()
    if not directory.is_relative_to(root) or path.parent != directory:
        raise HTTPException(409, '图片资料路径不完整，请先检查备份，未执行永久清理。')
    return path


@router.post('/api/products/trash/purge-preview')
def purge_preview(request: Request):
    permissions.require(backend._user(request), 'products.purge')
    with backend._connection() as connection:
        return _purge_plan(connection)


@router.post('/api/products/trash/purge')
def purge_trash(request: Request, payload: dict = Body(...)):
    user = backend._user(request)
    permissions.require(user, 'products.purge')
    if (set(payload) != {'confirmation','expected_token'} or payload.get('confirmation') != '永久清空回收站'
            or not isinstance(payload.get('expected_token'), str) or len(payload['expected_token']) != 64):
        raise HTTPException(400, '请先预览回收站，并准确输入“永久清空回收站”。')
    with backend._connection() as connection:
        plan = _purge_plan(connection)
        if plan['token'] != payload['expected_token']:
            raise HTTPException(409, '回收站已经变化，请重新预览后再确认。')
        if plan['blocked']:
            raise HTTPException(409, '部分商品仍关联企业知识或合并记录，请先处理关联，未清理任何商品。')
        if not plan['count']:
            return {'ok':True,'removed':0,'backup_created':False}
    # Complete a consistent backup before any irreversible database/file change.
    backup = backend._make_backup(user)
    files = []
    with backend._connection(write=True) as connection:
        current = _purge_plan(connection)
        if current['token'] != payload['expected_token'] or current['blocked']:
            raise HTTPException(409, '回收站或关联资料已经变化。已保留备份，请重新预览后确认。')
        identities = [row['id'] for row in current['items']]
        for start in range(0, len(identities), 200):
            batch = identities[start:start+200]
            marks = ','.join('?' for _ in batch)
            images = connection.execute(f'SELECT filename,thumb_filename FROM images WHERE product_id IN ({marks})', batch).fetchall()
            for image in images:
                files.extend((('images', image['filename'], _media_candidate('images', image['filename'])),
                              ('thumbnails', image['thumb_filename'], _media_candidate('thumbnails', image['thumb_filename']))))
            for table in ('price_history','price_tracking'):
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    connection.execute(f'DELETE FROM {table} WHERE listing_id IN (SELECT id FROM product_listings WHERE product_id IN ({marks}))', batch)
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='store_import_sources'").fetchone():
                connection.execute(f'DELETE FROM store_import_sources WHERE product_id IN ({marks})', batch)
            connection.execute(f'DELETE FROM product_listings WHERE product_id IN ({marks})', batch)
            connection.execute(f'DELETE FROM image_embeddings WHERE image_id IN (SELECT id FROM images WHERE product_id IN ({marks}))', batch)
            connection.execute(f'DELETE FROM images WHERE product_id IN ({marks})', batch)
            connection.execute(f'DELETE FROM decisions WHERE product_id IN ({marks})', batch)
            connection.execute(f'DELETE FROM products WHERE deleted=1 AND id IN ({marks})', batch)
            for table in ('knowledge_product_search','knowledge_product_dirty'):
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    connection.execute(f'DELETE FROM {table} WHERE product_id IN ({marks})', batch)
        backend._audit(connection, user, 'purge_products', f"永久清空回收站：{len(identities)}款商品；已先创建完整备份")
    failed = 0
    # Recheck shared paths after the commit; only unreferenced files are removed.
    with backend._connection() as connection:
        for folder, name, path in files:
            column = 'filename' if folder == 'images' else 'thumb_filename'
            if connection.execute('SELECT 1 FROM images WHERE ' + column + '=? LIMIT 1', (name,)).fetchone():
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failed += 1
    result = {'ok':True, 'removed':len(identities), 'backup_created':True, 'backup_name':backup.name}
    if failed:
        result['cleanup_warning'] = '回收站记录已移除，部分无引用图片暂被占用；完整主机备份已保存。'
    return result


def _validated_ids(payload: dict) -> list[str]:
    values = payload.get("ids")
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_BULK_PRODUCTS:
        raise HTTPException(400, "请选择 1 至 100 个需要移除的商品。")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 64 for value in values):
        raise HTTPException(400, "商品编号格式不正确，请刷新商品库后重试。")
    return list(dict.fromkeys(value.strip() for value in values))


@router.post("/api/products/bulk-delete")
def bulk_delete(request: Request, payload: dict = Body(...)):
    """Authorize the complete selection before making any recoverable changes."""
    user = backend._user(request)
    ids = _validated_ids(payload)
    with backend._connection(write=True) as connection:
        products = [backend._product(connection, product_id) for product_id in ids]
        for product in products:
            permissions.require_product(user, product, 'delete')
        stamp = backend.now()
        for product in products:
            connection.execute("UPDATE products SET deleted=1,updated_at=? WHERE id=?", (stamp, product["id"]))
            backend._audit(connection, user, "delete", "批量移除商品：" + product["name"], product["id"])
    return {"ok": True, "removed": len(ids), "ids": ids}


def _trash_product(connection, row, user=None) -> dict:
    # Reuse the central serializer so platform listings and future fields survive.
    item = backend._serialize_product(connection, row, user)
    item.update(deleted=True, removed_at=row["updated_at"])
    for image in item.get("images", []):
        path = f"/api/products/trash/{row['id']}/images/{image['id']}"
        image["url"], image["thumb_url"] = path, path + "?thumb=1"
    return item


@router.get("/api/products/trash")
def trash(request: Request, q: str = Query("", max_length=160),
          limit: int = Query(60, ge=1, le=100), offset: int = Query(0, ge=0)):
    user = backend._user(request)
    where, params = ["p.deleted=1"], []
    if not (permissions.has(user, 'products.restore_all') or permissions.has(user, 'products.delete_all')):
        if not (permissions.has(user, 'products.restore_own') or permissions.has(user, 'products.delete_own')):
            return {'items': [], 'total': 0}
        where.append("(p.created_by=? OR p.owner=?)")
        params.extend([user["id"], user["name"]])
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(p.name LIKE ? ESCAPE '\\' OR p.code LIKE ? ESCAPE '\\' OR p.specs LIKE ? ESCAPE '\\' OR p.owner LIKE ? ESCAPE '\\')")
        params.extend([pattern] * 4)
    condition = " AND ".join(where)
    with backend._connection() as connection:
        total = connection.execute("SELECT COUNT(*) FROM products p WHERE " + condition, params).fetchone()[0]
        rows = connection.execute("SELECT p.* FROM products p WHERE " + condition + " ORDER BY p.updated_at DESC,p.id DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        items = [_trash_product(connection, row, user) for row in rows]
    return {"items": items, "total": total}


def _image_path(folder: str, name: str) -> Path:
    directory = (Path(backend.DATA_DIR) / folder).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(404, "图片文件不存在。")
    return path


@router.get("/api/products/trash/{product_id}")
def trash_detail(product_id: str, request: Request):
    user = backend._user(request)
    with backend._connection() as connection:
        product = backend._product(connection, product_id, include_deleted=True)
        _may_view_trash(product, user)
        if not product["deleted"]:
            raise HTTPException(404, "此商品不在回收站中。")
        return _trash_product(connection, product, user)


@router.get("/api/products/trash/{product_id}/images/{image_id}")
def trash_image(product_id: str, image_id: str, request: Request, thumb: bool = False):
    """Show removed product pictures only to someone allowed to restore them."""
    user = backend._user(request)
    with backend._connection() as connection:
        product = backend._product(connection, product_id, include_deleted=True)
        _may_view_trash(product, user)
        if not product["deleted"]:
            raise HTTPException(404, "此商品不在回收站中。")
        image = connection.execute("SELECT * FROM images WHERE id=? AND product_id=?", (image_id, product_id)).fetchone()
        if not image:
            raise HTTPException(404, "图片不存在。")
    folder, filename = ("thumbnails", image["thumb_filename"]) if thumb else ("images", image["filename"])
    return FileResponse(_image_path(folder, filename), media_type="image/jpeg" if thumb else image["media_type"], headers={"Cache-Control": "no-store"})


@router.post("/api/products/{product_id}/restore")
def restore(product_id: str, request: Request):
    user = backend._user(request)
    with backend._connection(write=True) as connection:
        product = backend._product(connection, product_id, include_deleted=True)
        permissions.require_product(user, product, 'restore')
        _optional_guard('product_merge', 'assert_restore_allowed', connection, product_id)
        if not product["deleted"]:
            return {"ok": True, "restored": False, "product": backend._serialize_product(connection, product, user)}
        images = connection.execute("SELECT filename FROM images WHERE product_id=?", (product_id,)).fetchall()
        for image in images:
            try:
                _image_path("images", image["filename"])
            except HTTPException:
                raise HTTPException(409, "原始图片文件缺失，请先从完整备份恢复文件，再恢复这条商品。") from None
        connection.execute("UPDATE products SET deleted=0,updated_at=? WHERE id=?", (backend.now(), product_id))
        backend._audit(connection, user, "restore", "恢复商品：" + product["name"], product_id)
        result = backend._serialize_product(connection, backend._product(connection, product_id), user)
    try:
        backend.start_indexing()
    except Exception:
        # A derived-index problem must not falsely report a committed restore as
        # failed. The search page separately reports index readiness.
        logging.getLogger("midoo").warning("Product restored; visual index update deferred.")
    return {"ok": True, "restored": True, "product": result}
