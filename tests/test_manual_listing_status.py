"""Manual availability uses isolated SKU records and never contacts a storefront."""
import hashlib
import io
import json
import socket
import urllib.request

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import price_storage as storage


PASSWORD = "manual-status-test-password"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "manual-status")
    storage.init_storage()
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as client:
        response = client.post("/api/setup", json={"name": "状态管理员", "password": PASSWORD})
        response.raise_for_status()
        user = response.json()["user"]
        product = create(client, listings=[listing("RED"), listing("BLUE", price="30.00")])
        yield client, user, product


def listing(sku="RED", **fields):
    return {"platform": "Temu", "store": "美国店", "product_code": sku,
            "product_number": "SKC-SHARED", "price": "20.00", "currency": "USD",
            "link": "https://shop.example.com/product/shared", "status": "pending", **fields}


def create(client, **fields):
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "#387e7c").save(image, "PNG")
    response = client.post("/api/products", data={"metadata": json.dumps({
        "name": "手动状态商品", "notes": "原始资料", **fields,
    })}, files={"images": ("sample.png", image.getvalue(), "image/png")})
    response.raise_for_status()
    product = response.json()
    product.pop("duplicate_matches", None)
    return product


def set_status(setup, status, expected="pending", index=0, product=None, **options):
    client, _, original = setup
    product = product or original
    return client.patch(f"/api/products/{product['id']}/listings/{product['listings'][index]['id']}/status",
                        json={"status": status, "expected_status": expected}, **options)


def snapshot(product):
    with backend._connection() as conn:
        tables = {
            "product": dict(conn.execute("SELECT * FROM products WHERE id=?", (product["id"],)).fetchone()),
            "listings": [dict(row) for row in conn.execute(
                "SELECT * FROM product_listings WHERE product_id=? ORDER BY position,id", (product["id"],))],
            "images": [dict(row) for row in conn.execute(
                "SELECT * FROM images WHERE product_id=? ORDER BY id", (product["id"],))],
            "tracking": [dict(row) for row in conn.execute("SELECT * FROM price_tracking ORDER BY listing_id")],
            "history": [dict(row) for row in conn.execute("SELECT * FROM price_history ORDER BY id")],
            "audit": [dict(row) for row in conn.execute("SELECT * FROM activity WHERE action='listing_status' ORDER BY id")],
        }
    tables["image_bytes"] = {
        kind + "/" + row[field]: hashlib.sha256((backend.DATA_DIR / kind / row[field]).read_bytes()).hexdigest()
        for row in tables["images"] for kind, field in (("images", "filename"), ("thumbnails", "thumb_filename"))
    }
    return tables


def test_manual_switch_changes_only_one_sku_status_and_preserves_quotes_images_and_ids(setup, monkeypatch):
    client, user, product = setup
    row_id = product["listings"][0]["id"]
    storage.save_listing_tracking(row_id, {"enabled": True, "region": "美国", "variant_hint": "红色 2 件装"}, user)
    target = storage.get_target(row_id, user)
    storage.record_attempt(row_id, {"price": "15.00", "currency": "USD", "source": "manual-test"}, expected_target=target)
    before = snapshot(product)

    def no_network(*args, **kwargs):
        pytest.fail("A manual availability change must not access a storefront")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    response = set_status(setup, "listed")
    response.raise_for_status()
    body = response.json()
    after = snapshot(product)
    assert set(body) == {"product", "listing"}
    assert body["listing"] == {**product["listings"][0], "status": "listed", "region": "美国", "variant_hint": "红色 2 件装"}
    assert body["product"]["listings"] == [body["listing"], product["listings"][1]]
    assert body["product"] == client.get("/api/products/" + product["id"]).json()
    assert after["product"]["updated_at"] != before["product"]["updated_at"]
    assert after["product"] == {**before["product"], "updated_at": after["product"]["updated_at"]}
    assert after["listings"] == [{**before["listings"][0], "status": "listed"}, before["listings"][1]]
    for key in ("images", "image_bytes", "tracking", "history"):
        assert after[key] == before[key]
    assert len(after["audit"]) == 1
    audit = after["audit"][0]
    assert audit["product_id"] == product["id"] and audit["user_id"] == user["id"]
    for text in ("Temu", "美国店", "RED", "SKC-SHARED", "准备上架", "已上架"):
        assert text in audit["description"]


def test_same_skc_skus_are_independent_and_all_archived_requires_every_record(setup):
    client, _, product = setup
    set_status(setup, "listed").raise_for_status()
    response = set_status(setup, "archived", "listed")
    response.raise_for_status()
    current = response.json()["product"]
    assert current["listings"][1]["status"] == "pending"
    assert current["workflow_states"] == ["pending"]
    assert client.get("/api/products", params={"workflow_state": "archived"}).json()["total"] == 0
    response = set_status(setup, "archived", index=1)
    response.raise_for_status()
    assert response.json()["product"]["workflow_states"] == ["archived"]
    assert client.get("/api/stats").json()["workflow_counts"]["archived"] == 1
    response = set_status(setup, "listed", "archived", index=1)
    response.raise_for_status()
    assert response.json()["product"]["workflow_states"] == ["listed"]
    assert response.json()["product"]["listings"][0]["status"] == "archived"


def test_repeated_target_is_idempotent_and_conflicting_stale_state_is_rejected(setup):
    _, _, product = setup
    set_status(setup, "listed").raise_for_status()
    saved = snapshot(product)
    set_status(setup, "listed").raise_for_status()  # Retried pending -> listed request.
    assert snapshot(product) == saved
    response = set_status(setup, "archived", expected="pending")
    assert response.status_code == 409
    assert snapshot(product) == saved
    set_status(setup, "archived", expected="listed").raise_for_status()
    assert len(snapshot(product)["audit"]) == 2


@pytest.mark.parametrize("payload", [
    {}, {"status": "listed"}, {"expected_status": "pending"},
    {"status": "pending", "expected_status": "pending"},
    {"status": "unknown", "expected_status": "pending"},
    {"status": "listed", "expected_status": "unknown"},
    {"status": True, "expected_status": "pending"},
    {"status": ["listed"], "expected_status": "pending"},
    {"status": "listed", "expected_status": {"status": "pending"}},
    {"status": None, "expected_status": "pending"},
    {"status": "listed", "expected_status": None},
    {"status": "listed", "expected_status": "pending", "price": "999"},
])
def test_invalid_status_payload_cannot_partially_modify_a_product(setup, payload):
    client, _, product = setup
    before = snapshot(product)
    response = client.patch(f"/api/products/{product['id']}/listings/{product['listings'][0]['id']}/status", json=payload)
    assert response.status_code == 400
    assert snapshot(product) == before


@pytest.mark.parametrize("role", ["admin", "creator", "owner", "unrelated"])
def test_switch_enforces_existing_admin_creator_and_owner_permissions(setup, role):
    client, _, product = setup
    if role != "admin":
        client.post("/api/team", json={"name": "状态运营", "password": PASSWORD, "role": "operator"}).raise_for_status()
        if role == "owner":
            client.patch("/api/products/" + product["id"], json={"owner": "状态运营"}).raise_for_status()
        client.post("/api/logout").raise_for_status()
        client.post("/api/login", json={"name": "状态运营", "password": PASSWORD}).raise_for_status()
        if role == "creator":
            product = create(client, owner="其他负责人", listings=[listing()])
    before = snapshot(product)
    response = set_status(setup, "listed", product=product)
    assert response.status_code == (403 if role == "unrelated" else 200)
    if role == "unrelated":
        assert snapshot(product) == before
    else:
        assert response.json()["listing"]["status"] == "listed"


def test_unauthenticated_and_cross_origin_switches_are_rejected(setup):
    client, _, product = setup
    before = snapshot(product)
    assert set_status(setup, "listed", headers={"origin": "https://foreign.example.com"}).status_code == 403
    assert set_status(setup, "listed", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert snapshot(product) == before
    client.post("/api/logout").raise_for_status()
    assert set_status(setup, "listed").status_code == 401
    assert snapshot(product) == before


def test_listing_must_belong_to_the_requested_live_product(setup):
    client, _, product = setup
    other = create(client, name="另一个商品", listings=[listing("OTHER")])
    before, other_before = snapshot(product), snapshot(other)
    response = client.patch(f"/api/products/{product['id']}/listings/{other['listings'][0]['id']}/status",
                            json={"status": "listed", "expected_status": "pending"})
    assert response.status_code == 404
    response = client.patch(f"/api/products/{product['id']}/listings/{'0' * 32}/status",
                            json={"status": "listed", "expected_status": "pending"})
    assert response.status_code == 404
    assert snapshot(product) == before and snapshot(other) == other_before
    client.delete("/api/products/" + product["id"]).raise_for_status()
    removed = snapshot(product)
    assert set_status(setup, "listed").status_code == 404
    assert snapshot(product) == removed


def test_status_and_timestamp_roll_back_if_audit_fails(setup, monkeypatch):
    _, _, product = setup
    before = snapshot(product)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("test-only audit failure")

    monkeypatch.setattr(backend, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="test-only audit failure"):
        set_status(setup, "listed")
    assert snapshot(product) == before


@pytest.mark.parametrize("rows,expected", [
    ([listing(price="", link="", status="listed")], {"listed", "unconfigured", "pricing"}),
    ([listing(price="", status="archived")], {"archived"}),
    ([listing(status="pending")], {"pending"}),
    ([listing("RED", status="listed"), listing("BLUE", status="archived")], {"listed"}),
    ([listing("RED", status="archived"), listing("BLUE", status="pending")], {"pending"}),
    ([], {"unconfigured", "pricing", "pending"}),
])
def test_availability_filters_and_counts_use_manual_status_independently_from_links_prices(setup, rows, expected):
    client, _, original = setup
    client.delete("/api/products/" + original["id"]).raise_for_status()
    product = create(client, status="archived", listings=rows)
    assert set(product["workflow_states"]) == expected
    stats = client.get("/api/stats").json()
    assert stats["total_products"] == 1
    for workflow in set(backend.WORKFLOW_STATES) - {"legacy"}:
        response = client.get("/api/products", params={"workflow_state": workflow})
        response.raise_for_status()
        count = int(workflow in expected)
        assert response.json()["total"] == stats["workflow_counts"][workflow] == count
        assert {row["id"] for row in response.json()["items"]} == ({product["id"]} if count else set())


def test_one_platform_down_does_not_hide_another_platform_in_sale(setup):
    client, _, original = setup
    client.delete("/api/products/" + original["id"]).raise_for_status()
    product = create(client, listings=[listing(status="archived"), listing("AMZ", platform="Amazon", status="listed", price="")])
    assert product["workflow_state"] == "listed"
    for params, expected in [
        ({"platform": "Temu", "listing_status": "listed"}, 0),
        ({"platform": "Temu", "listing_status": "archived"}, 1),
        ({"platform": "Amazon", "listing_status": "listed"}, 1),
        ({"workflow_state": "archived"}, 0),
    ]:
        assert client.get("/api/products", params=params).json()["total"] == expected
