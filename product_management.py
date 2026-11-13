"""Recoverable product management using the shared library's existing permissions.

Register this router before ``backend.router`` so the literal ``products/trash``
route is not interpreted as a product ID. No original image is erased here.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

import backend

router = APIRouter(dependencies=[Depends(backend.require_user)])
MAX_BULK_PRODUCTS = 100


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
            backend._may_edit(product, user)
        stamp = backend.now()
        for product in products:
            connection.execute("UPDATE products SET deleted=1,updated_at=? WHERE id=?", (stamp, product["id"]))
            backend._audit(connection, user, "delete", "批量移除商品：" + product["name"], product["id"])
    return {"ok": True, "removed": len(ids), "ids": ids}


def _trash_product(connection, row) -> dict:
    # Reuse the central serializer so platform listings and future fields survive.
    item = backend._serialize_product(connection, row)
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
    if user["role"] != "admin":
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
        items = [_trash_product(connection, row) for row in rows]
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
        backend._may_edit(product, user)
        if not product["deleted"]:
            raise HTTPException(404, "此商品不在回收站中。")
        return _trash_product(connection, product)


@router.get("/api/products/trash/{product_id}/images/{image_id}")
def trash_image(product_id: str, image_id: str, request: Request, thumb: bool = False):
    """Show removed product pictures only to someone allowed to restore them."""
    user = backend._user(request)
    with backend._connection() as connection:
        product = backend._product(connection, product_id, include_deleted=True)
        backend._may_edit(product, user)
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
        backend._may_edit(product, user)
        if not product["deleted"]:
            return {"ok": True, "restored": False, "product": backend._serialize_product(connection, product)}
        images = connection.execute("SELECT filename FROM images WHERE product_id=?", (product_id,)).fetchall()
        for image in images:
            try:
                _image_path("images", image["filename"])
            except HTTPException:
                raise HTTPException(409, "原始图片文件缺失，请先从完整备份恢复文件，再恢复这条商品。") from None
        connection.execute("UPDATE products SET deleted=0,updated_at=? WHERE id=?", (backend.now(), product_id))
        backend._audit(connection, user, "restore", "恢复商品：" + product["name"], product_id)
        result = backend._serialize_product(connection, backend._product(connection, product_id))
    try:
        backend.start_indexing()
    except Exception:
        # A derived-index problem must not falsely report a committed restore as
        # failed. The search page separately reports index readiness.
        logging.getLogger("midoo").warning("Product restored; visual index update deferred.")
    return {"ok": True, "restored": True, "product": result}
