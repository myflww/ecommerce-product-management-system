"""Platform records, operational categories and non-destructive upgrade coverage."""
import io
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "data")
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as instance:
        assert instance.post("/api/setup", json={"name": "平台管理员", "password": "test-password-2026"}).status_code == 200
        yield instance


def create(client, **metadata):
    content = io.BytesIO()
    Image.new("RGB", (80, 80), "green").save(content, "PNG")
    response = client.post("/api/products", data={"metadata": json.dumps({"name": "蜂蜡蜡烛", **metadata})},
                           files=[("images", ("candle.png", content.getvalue(), "image/png"))])
    assert response.status_code == 200, response.text
    product = response.json()
    product.pop("duplicate_matches", None)
    return product


def update(client, product, **fields):
    response = client.patch(f"/api/products/{product['id']}", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def listing(platform="Temu", **fields):
    return {"platform": platform, "product_code": "SELLER-2026", "product_number": "SKU-001", **fields}


def test_new_imports_remain_unconfigured_even_if_legacy_global_status_says_listed(client):
    product = create(client, status="listed")
    assert product["status"] == "listed"  # Preserve old API metadata without claiming a verified listing.
    assert product["workflow_state"] == "unconfigured"
    assert product["listings"] == []
    assert client.get("/api/products", params={"workflow_state": "listed"}).json()["total"] == 0
    stats = client.get("/api/stats").json()
    assert stats["workflow_counts"]["unconfigured"] == 1
    assert stats["pending_products"] == 1


def test_platform_prices_and_statuses_derive_separate_operational_categories(client):
    product = create(client, listings=[listing()])
    assert product["workflow_state"] == "pricing"
    product = update(client, product, listings=[listing(status="listed")])
    assert product["workflow_state"] == "listed"
    assert set(product["workflow_states"]) == {"listed", "pricing", "unconfigured"}
    product = update(client, product, listings=[listing(price="19.90")])
    assert product["workflow_state"] == "pending"
    product = update(client, product, listings=[listing(price="19.90", status="listed"), listing("Amazon", status="archived")])
    assert product["workflow_state"] == "listed"
    temu, amazon = product["listings"]
    assert temu["currency"] == "CNY"
    assert temu["price"] == "19.90"
    assert amazon["status"] == "archived"
    # Only Temu's state changes; Amazon stays archived and all ids survive.
    product = update(client, product, listings=[{**temu, "status": "archived"}, amazon])
    assert product["workflow_state"] == "archived"
    assert [row["id"] for row in product["listings"]] == [temu["id"], amazon["id"]]
    product = update(client, product, listings=[])
    assert product["workflow_state"] == "unconfigured"


def test_platform_status_filters_match_one_row_and_identifiers_are_searchable(client):
    product = create(client, listings=[listing(price="20", status="listed", store="旗舰店"), listing("Amazon", status="archived")])
    assert client.get("/api/products", params={"platform": "Amazon", "listing_status": "listed"}).json()["total"] == 0
    assert client.get("/api/products", params={"platform": "Amazon", "listing_status": "archived"}).json()["items"][0]["id"] == product["id"]
    assert client.get("/api/products", params={"platform": "temu", "listing_status": "listed"}).json()["total"] == 1
    for word in ("SELLER-2026", "SKU-001", "Amazon", "旗舰店"):
        assert client.get("/api/products", params={"q": word}).json()["total"] == 1
    assert client.get("/api/products", params={"q": "%"}).json()["total"] == 0
    stats = client.get("/api/stats").json()
    assert stats["platforms"] == ["Amazon", "Temu"]
    assert stats["workflow_counts"]["listed"] == 1
    assert sum(stats["workflow_counts"].values()) == stats["total_products"]
    for workflow in backend.WORKFLOW_STATES:
        assert client.get("/api/products", params={"workflow_state": workflow}).json()["total"] == stats["workflow_counts"][workflow]


@pytest.mark.parametrize("bad_listing", [
    {"platform": ""}, {"platform": "Temu", "price": "0"}, {"platform": "Temu", "price": "-2"},
    {"platform": "Temu", "price": "NaN"}, {"platform": "Temu", "price": True},
    {"platform": "Temu", "price": "10.12345"}, {"platform": "Temu", "currency": "人民币"},
    {"platform": "Temu", "status": "unknown"}, {"platform": "Temu", "link": "javascript:alert(1)"},
    {"platform": "Temu", "product_code": ["bad"]}, {"platform": "Temu", "id": "not-valid"},
])
def test_invalid_platform_metadata_cannot_partially_update_product(client, bad_listing):
    product = create(client, listings=[listing(price="20", status="listed")])
    response = client.patch(f"/api/products/{product['id']}", json={"name": "不得保存", "listings": [bad_listing]})
    assert response.status_code == 400, response.text
    unchanged = client.get(f"/api/products/{product['id']}").json()
    assert unchanged == product


def test_other_products_listing_ids_and_duplicate_ids_are_rejected_atomically(client):
    first = create(client, listings=[listing(price="20", status="listed")])
    second = create(client, name="另一个商品", listings=[listing("Walmart")])
    row = first["listings"][0]
    for rows in ([row], [second["listings"][0], second["listings"][0]]):
        response = client.patch(f"/api/products/{second['id']}", json={"name": "错误更新", "listings": rows})
        assert response.status_code == 400
        assert client.get(f"/api/products/{second['id']}").json() == second
    assert client.get(f"/api/products/{first['id']}").json() == first


def test_same_platform_can_have_independent_store_rows_and_soft_deletion_excludes_counts(client):
    product = create(client, listings=[listing(store="一店", status="listed", price="12.99", currency="usd"),
                                       listing(store="二店", status="archived", price="15")])
    assert product["workflow_state"] == "listed"
    assert product["listings"][0]["currency"] == "USD"
    assert client.get("/api/products", params={"platform": "Temu", "listing_status": "archived"}).json()["total"] == 1
    assert client.delete(f"/api/products/{product['id']}").status_code == 200
    stats = client.get("/api/stats").json()
    assert sum(stats["workflow_counts"].values()) == 0
    assert stats["platforms"] == []


def test_legacy_schema_upgrade_preserves_product_details_and_images(client):
    product = create(client, status="listed", notes="历史商品不能捏造平台", specs="白色/2件装")
    with backend._connection(write=True) as conn:
        original_sha = conn.execute("SELECT sha256 FROM images WHERE product_id=?", (product["id"],)).fetchone()[0]
        conn.execute("DROP TABLE product_listings")
        conn.execute("ALTER TABLE products DROP COLUMN listings_initialized")
    # Reopening a 1.1-style database performs only additive schema migration.
    backend.init_storage(backend.DATA_DIR)
    backend.init_storage(backend.DATA_DIR)
    migrated = client.get(f"/api/products/{product['id']}").json()
    assert migrated["workflow_state"] == "legacy"
    assert migrated["listings"] == []
    for field in ("id", "code", "name", "status", "notes", "specs", "images", "created_at", "updated_at"):
        assert migrated[field] == product[field]
    assert client.get("/api/products", params={"workflow_state": "legacy"}).json()["total"] == 1
    with backend._connection() as conn:
        assert conn.execute("SELECT sha256 FROM images WHERE product_id=?", (product["id"],)).fetchone()[0] == original_sha
    migrated = update(client, migrated, notes="只改备注仍未补平台")
    assert migrated["workflow_state"] == "legacy"
    migrated = update(client, migrated, listings=[listing(price="15", status="listed")])
    assert migrated["workflow_state"] == "listed"


def test_batch_import_accepts_platform_rows_and_failure_leaves_no_partial_batch(client):
    content = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(content, "PNG")
    groups = [{"name": "一号", "paths": ["one.png"], "listings": [listing(status="listed", price="10")]},
              {"name": "二号", "paths": ["two.png"]}]
    files = [("images", (path, content.getvalue(), "image/png")) for path in ("one.png", "two.png")]
    response = client.post("/api/import", data={"manifest": json.dumps({"groups": groups})}, files=files)
    assert response.status_code == 200, response.text
    assert [item["workflow_state"] for item in response.json()["items"]] == ["listed", "unconfigured"]
    groups[1]["listings"] = [listing(price="-1")]
    response = client.post("/api/import", data={"manifest": json.dumps({"groups": groups})}, files=files)
    assert response.status_code == 400
    assert client.get("/api/products").json()["total"] == 2
    assert client.get("/api/products", params={"workflow_state": "unknown"}).status_code == 400
    assert client.get("/api/products", params={"listing_status": "unknown"}).status_code == 400
