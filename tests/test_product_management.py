"""Recoverable management tests use generated pictures and temporary libraries."""
import hashlib
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import backend
import product_management


def picture():
    image = Image.new("RGB", (150, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((25, 30, 125, 135), radius=8, fill="#497e83")
    draw.rectangle((40, 55, 110, 90), fill="#ede6cb")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "management")
    backend._login_attempts.clear()
    # CRUD permissions/transactions do not depend on loading a visual model.
    monkeypatch.setattr(backend, "_semantic_model_status", lambda: {"loaded": False, "available": False, "model_id": "", "error": None})
    monkeypatch.setattr(backend, "start_indexing", lambda *args, **kwargs: {"state": "unavailable"})
    app = FastAPI()
    app.include_router(product_management.router)
    app.include_router(backend.router)
    with TestClient(app) as test_client:
        yield test_client


def setup(client):
    result = client.post("/api/setup", json={"name": "管理员", "password": "management-test-only"})
    assert result.status_code == 200, result.text
    return result.json()["user"]


def create(client, name="管理测试商品", **fields):
    response = client.post("/api/products", data={"metadata": json.dumps({"name": name, **fields})},
                           files={"images": ("management.png", picture(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()


def operator(client):
    assert client.post("/api/team", json={"name": "运营", "password": "operator-management", "role": "operator"}).status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"name": "运营", "password": "operator-management"}).status_code == 200


def db_product(product_id):
    with backend._connection() as connection:
        return dict(connection.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())


def test_management_requires_authentication_and_same_origin(client):
    assert client.get("/api/products/trash").status_code == 401
    assert client.post("/api/products/bulk-delete", json={"ids": ["unknown"]}).status_code == 401
    assert client.post("/api/products/unknown/restore").status_code == 401
    setup(client)
    item = create(client)
    assert client.post("/api/products/bulk-delete", json={"ids": [item["id"]]}, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert not db_product(item["id"])["deleted"]
    assert client.delete(f"/api/products/{item['id']}").status_code == 200
    assert client.post(f"/api/products/{item['id']}/restore", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert db_product(item["id"])["deleted"]


@pytest.mark.parametrize("ids", [None, [], "not-a-list", [1], [None], [""], ["  "], ["x" * 65], ["x"] * 101])
def test_bulk_selection_validation(client, ids):
    setup(client)
    assert client.post("/api/products/bulk-delete", json={"ids": ids}).status_code == 400


def test_bulk_delete_is_atomic_for_permission_and_missing_records(client):
    setup(client)
    admin_item = create(client, "管理员商品")
    operator(client)
    own_item = create(client, "运营商品")
    response = client.post("/api/products/bulk-delete", json={"ids": [own_item["id"], admin_item["id"]]})
    assert response.status_code == 403
    assert not db_product(own_item["id"])["deleted"]
    response = client.post("/api/products/bulk-delete", json={"ids": [own_item["id"], "does-not-exist"]})
    assert response.status_code == 404
    assert not db_product(own_item["id"])["deleted"]
    assert client.get("/api/activity").json()["items"][0]["action"] != "delete"


def test_bulk_soft_delete_preserves_files_and_restore_preserves_identity_and_platforms(client):
    setup(client)
    item = create(client, "多平台商品", specs="陶瓷 / 2件", notes="原始业务备注", listings=[
        {"platform": "Temu", "product_code": "T-001", "product_number": "PN-1", "status": "listed", "price": "19.95", "currency": "USD"},
        {"platform": "Amazon", "product_code": "A-002", "status": "archived", "price": "29", "currency": "USD"},
    ])
    original = db_product(item["id"])
    with backend._connection() as connection:
        image_row = dict(connection.execute("SELECT * FROM images WHERE product_id=?", (item["id"],)).fetchone())
    original_file = backend.DATA_DIR / "images" / image_row["filename"]
    digest = hashlib.sha256(original_file.read_bytes()).hexdigest()
    response = client.post("/api/products/bulk-delete", json={"ids": [item["id"], item["id"]]})
    assert response.status_code == 200
    assert response.json()["removed"] == 1
    assert response.json()["ids"] == [item["id"]]
    assert original_file.exists()
    assert client.get("/api/products").json()["total"] == 0
    assert client.post("/api/search", files={"images": ("query.png", picture(), "image/png")}).json()["items"] == []
    trash = client.get("/api/products/trash").json()
    assert trash["total"] == 1 and trash["items"][0]["listings"] == item["listings"]
    assert client.get(f"/api/products/trash/{item['id']}").json()["deleted"]
    assert client.get(trash["items"][0]["images"][0]["thumb_url"]).headers["content-type"] == "image/jpeg"
    assert client.get(trash["items"][0]["images"][0]["url"]).content == picture()
    assert client.get(item["images"][0]["url"]).status_code == 404
    restored = client.post(f"/api/products/{item['id']}/restore")
    assert restored.status_code == 200, restored.text
    restored = restored.json()
    assert restored["restored"]
    assert restored["product"]["id"] == item["id"]
    assert restored["product"]["code"] == item["code"]
    assert restored["product"]["images"] == item["images"]
    assert restored["product"]["listings"] == item["listings"]
    assert restored["product"]["workflow_state"] == item["workflow_state"]
    current = db_product(item["id"])
    for field, value in original.items():
        if field not in {"updated_at", "deleted"}:
            assert current[field] == value
    with backend._connection() as connection:
        assert dict(connection.execute("SELECT * FROM images WHERE id=?", (image_row["id"],)).fetchone()) == image_row
    assert hashlib.sha256(original_file.read_bytes()).hexdigest() == digest
    assert client.post(f"/api/products/{item['id']}/restore").json()["restored"] is False
    assert client.get("/api/products/trash").json()["total"] == 0
    assert client.get(f"/api/products/trash/{item['id']}").status_code == 404
    assert client.get(item["images"][0]["url"]).status_code == 200
    assert client.post("/api/search", files={"images": ("query.png", picture(), "image/png")}).json()["items"][0]["product"]["id"] == item["id"]


def test_trash_visibility_restore_and_images_are_permission_scoped(client):
    setup(client)
    private = create(client, "其他成员商品")
    assert client.delete(f"/api/products/{private['id']}").status_code == 200
    private_trash = client.get("/api/products/trash").json()["items"][0]
    assigned = create(client, "指派给运营的商品", owner="运营")
    assert client.delete(f"/api/products/{assigned['id']}").status_code == 200
    operator(client)
    own = create(client, "自己录入的商品", owner="管理员")
    assert client.delete(f"/api/products/{own['id']}").status_code == 200
    assert {row["id"] for row in client.get("/api/products/trash").json()["items"]} == {own["id"], assigned["id"]}
    assert client.get(f"/api/products/trash/{private['id']}").status_code == 403
    assert client.get(private_trash["images"][0]["thumb_url"]).status_code == 403
    assert client.post(f"/api/products/{private['id']}/restore").status_code == 403
    assert client.post(f"/api/products/{own['id']}/restore").status_code == 200
    assert client.post(f"/api/products/{assigned['id']}/restore").status_code == 200


def test_trash_search_pagination_and_literal_wildcards(client):
    setup(client)
    first = create(client, "100% 棉花收纳")
    second = create(client, "普通收纳盒")
    third = create(client, "另一条商品")
    client.post("/api/products/bulk-delete", json={"ids": [first["id"], second["id"], third["id"]]})
    assert client.get("/api/products/trash", params={"q": "100%"}).json()["total"] == 1
    assert client.get("/api/products/trash", params={"q": "收纳"}).json()["total"] == 2
    page1 = client.get("/api/products/trash", params={"limit": 1, "offset": 0}).json()
    page2 = client.get("/api/products/trash", params={"limit": 1, "offset": 1}).json()
    assert page1["total"] == 3 and len(page1["items"]) == 1
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    assert client.get("/api/products/trash", params={"q": "x" * 161}).status_code == 422
    assert client.get("/api/products/trash", params={"limit": 101}).status_code == 422


def test_restore_reports_missing_original_without_mutating_record(client):
    setup(client)
    item = create(client)
    client.delete(f"/api/products/{item['id']}")
    with backend._connection() as connection:
        filename = connection.execute("SELECT filename FROM images WHERE product_id=?", (item["id"],)).fetchone()[0]
    # Only the temporary test fixture is deliberately removed.
    (backend.DATA_DIR / "images" / filename).unlink()
    original = db_product(item["id"])
    response = client.post(f"/api/products/{item['id']}/restore")
    assert response.status_code == 409
    assert db_product(item["id"]) == original


def test_index_update_failure_does_not_undo_committed_restore(client, monkeypatch):
    setup(client)
    item = create(client)
    client.delete(f"/api/products/{item['id']}")
    def failing_index():
        raise RuntimeError("fixture failure")
    monkeypatch.setattr(backend, "start_indexing", failing_index)
    response = client.post(f"/api/products/{item['id']}/restore")
    assert response.status_code == 200 and response.json()["restored"]
    assert client.get(f"/api/products/{item['id']}").status_code == 200
