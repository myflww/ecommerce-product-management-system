"""User-entered categories remain separate from availability and preparation queues."""
import hashlib
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import product_management


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "categories")
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(product_management.router)
    app.include_router(backend.router)
    with TestClient(app) as instance:
        instance.post("/api/setup", json={"name": "分类管理员", "password": "category-test-password"}).raise_for_status()
        yield instance


def image():
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "#638a7b").save(output, "PNG")
    return output.getvalue()


def create(client, **metadata):
    response = client.post("/api/products", data={"metadata": json.dumps({"name": "分类商品", **metadata})},
                           files={"images": ("fixture.png", image(), "image/png")})
    response.raise_for_status()
    result = response.json()
    result.pop("duplicate_matches", None)
    return result


def classified(client, **params):
    response = client.get("/api/products", params=params)
    response.raise_for_status()
    return response.json()


def test_category_can_be_set_changed_or_cleared_without_reclassifying_availability(client):
    product = create(client, category="  蜂蜡蜡烛  ", listings=[{
        "platform": "Temu", "status": "listed", "product_code": "SKU-RED", "price": "20"}])
    assert product["category"] == "蜂蜡蜡烛"
    assert product["workflow_state"] == "listed"
    response = client.patch("/api/products/" + product["id"], json={"category": "  家居装饰  "})
    response.raise_for_status()
    changed = response.json()
    assert changed["category"] == "家居装饰"
    for key in ("id", "code", "name", "status", "created_at", "images", "listings", "workflow_state", "workflow_states"):
        assert changed[key] == product[key]
    # Editing another field never resets the chosen category.
    unchanged = client.patch("/api/products/" + product["id"], json={"notes": "补充备注"}).json()
    assert unchanged["category"] == "家居装饰"
    cleared = client.patch("/api/products/" + product["id"], json={"category": "   "}).json()
    assert cleared["category"] == "" and cleared["workflow_state"] == "listed"
    assert client.get("/api/categories").json() == {"items": [], "uncategorized_count": 1, "total": 1}


@pytest.mark.parametrize("invalid", [None, True, 123, [], {}, "类" * 61,
                                      "a\x00b", "a\nb", "a\rb", "a\tb", "a\x7fb", "a\x85b"])
def test_invalid_category_never_partially_updates_a_product(client, invalid):
    product = create(client, category="正确分类")
    response = client.patch("/api/products/" + product["id"], json={"category": invalid, "name": "不应保存"})
    assert response.status_code == 400
    assert client.get("/api/products/" + product["id"]).json() == product


def test_new_products_default_to_uncategorized_and_sixty_characters_are_allowed(client):
    product = create(client)
    assert product["category"] == ""
    longest = create(client, category="类" * 60)
    assert longest["category"] == "类" * 60


def test_category_filter_is_exact_and_uncategorized_does_not_confuse_a_user_category(client):
    targets = {name: create(client, category=name)["id"] for name in ("蜡烛", "香薰蜡烛", "未分类", "", "50%_优惠", "Tools", "tools", "x' OR 1=1 --")}
    for name, identity in targets.items():
        query = {"category": name} if name else {"uncategorized": True}
        result = classified(client, **query)
        assert result["total"] == 1
        assert result["items"][0]["id"] == identity
    assert classified(client, category="  蜡烛  ")["items"][0]["id"] == targets["蜡烛"]
    assert classified(client, category="不存在")["total"] == 0
    assert classified(client, category="蜡烛", uncategorized=True)["total"] == 0
    assert client.get("/api/products", params={"category": "a\nb"}).status_code == 400


def test_category_filter_combines_with_search_platform_workflow_and_pagination(client):
    matching = [create(client, name="蜂蜡套装 " + str(index), category="蜡烛", owner="同一负责人", listings=[
        {"platform": "Temu", "status": "listed", "price": "20", "product_code": "SKU-RED"},
        {"platform": "Temu", "status": "pending", "product_code": "SKU-BLUE"},
    ]) for index in range(3)]
    create(client, name="蜂蜡套装", category="家居", owner="同一负责人", listings=[{"platform": "Temu", "status": "listed"}])
    create(client, name="蜂蜡套装", category="蜡烛", owner="另一负责人", listings=[{"platform": "Amazon", "status": "listed"}])
    query = {"category": "蜡烛", "q": "蜂蜡", "owner": "同一负责人", "workflow_state": "listed", "platform": "Temu", "listing_status": "listed", "limit": 2}
    first = classified(client, **query)
    second = classified(client, **query, offset=2)
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {p["id"] for p in first["items"] + second["items"]} == {p["id"] for p in matching}
    # Global category counts count products once, despite each product's two SKUs.
    assert client.get("/api/categories").json() == {
        "items": [{"name": "家居", "count": 1}, {"name": "蜡烛", "count": 4}],
        "uncategorized_count": 0, "total": 5,
    }


def test_soft_delete_and_restore_preserve_category_and_media_and_update_counts(client):
    product = create(client, category="蜂蜡")
    create(client)
    with backend._connection() as conn:
        name = conn.execute("SELECT filename FROM images WHERE product_id=?", (product["id"],)).fetchone()[0]
    original = hashlib.sha256((backend.DATA_DIR / "images" / name).read_bytes()).hexdigest()
    client.delete("/api/products/" + product["id"]).raise_for_status()
    assert client.get("/api/categories").json() == {"items": [], "uncategorized_count": 1, "total": 1}
    assert classified(client, category="蜂蜡")["total"] == 0
    assert client.get("/api/products/trash/" + product["id"]).json()["category"] == "蜂蜡"
    response = client.post("/api/products/" + product["id"] + "/restore")
    response.raise_for_status()
    restored = response.json()["product"]
    assert restored["category"] == "蜂蜡" and restored["images"] == product["images"]
    assert hashlib.sha256((backend.DATA_DIR / "images" / name).read_bytes()).hexdigest() == original
    assert client.get("/api/categories").json() == {"items": [{"name": "蜂蜡", "count": 1}], "uncategorized_count": 1, "total": 2}


def test_import_saves_each_groups_category_and_invalid_group_is_atomic(client):
    groups = [{"name": "蜡烛", "category": "  装饰  ", "paths": ["one.png"]}, {"name": "其他", "paths": ["two.png"]}]
    files = [("images", (name, image(), "image/png")) for name in ("one.png", "two.png")]
    response = client.post("/api/import", data={"manifest": json.dumps({"groups": groups})}, files=files)
    response.raise_for_status()
    assert [p["category"] for p in response.json()["items"]] == ["装饰", ""]
    with backend._connection() as conn:
        batch_count = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
        image_count = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    groups[1]["category"] = "禁止\n换行"
    assert client.post("/api/import", data={"manifest": json.dumps({"groups": groups})}, files=files).status_code == 400
    assert classified(client)["total"] == 2
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == batch_count
        assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == image_count


def test_additive_migration_leaves_old_product_values_and_images_unchanged(client):
    product = create(client, name="旧商品", category="", specs="历史规格", status="listed", notes="历史备注")
    with backend._connection(write=True) as conn:
        conn.execute("ALTER TABLE products DROP COLUMN category")
        old_product = dict(conn.execute("SELECT * FROM products WHERE id=?", (product["id"],)).fetchone())
        old_images = [dict(row) for row in conn.execute("SELECT * FROM images WHERE product_id=?", (product["id"],))]
    backend.init_storage(backend.DATA_DIR)
    backend.init_storage(backend.DATA_DIR)
    with backend._connection() as conn:
        migrated = dict(conn.execute("SELECT * FROM products WHERE id=?", (product["id"],)).fetchone())
        assert migrated.pop("category") == ""
        assert migrated == old_product
        assert [dict(row) for row in conn.execute("SELECT * FROM images WHERE product_id=?", (product["id"],))] == old_images
    assert client.get("/api/products/" + product["id"]).json() == product
    assert client.get("/api/categories").json() == {"items": [], "uncategorized_count": 1, "total": 1}


def test_categories_are_readable_by_operators_but_edit_permissions_and_auth_remain(client):
    product = create(client, category="共享分类")
    client.post("/api/team", json={"name": "普通运营", "password": "category-test-password", "role": "operator"}).raise_for_status()
    client.post("/api/logout").raise_for_status()
    client.post("/api/login", json={"name": "普通运营", "password": "category-test-password"}).raise_for_status()
    assert client.get("/api/categories").json() == {"items": [{"name": "共享分类", "count": 1}], "uncategorized_count": 0, "total": 1}
    assert classified(client, category="共享分类")["total"] == 1
    assert client.patch("/api/products/" + product["id"], json={"category": "不可修改"}).status_code == 403
    own = create(client, category="运营自定义")
    assert own["category"] == "运营自定义"
    client.post("/api/logout").raise_for_status()
    assert client.get("/api/categories").status_code == 401
