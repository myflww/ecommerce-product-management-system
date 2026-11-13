"""Overlapping preparation queues use temporary product records only."""
import hashlib
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend


TODO_QUEUES = {"unconfigured", "pricing", "pending"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "library")
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as instance:
        instance.post("/api/setup", json={"name": "队列管理员", "password": "queue-test-password"}).raise_for_status()
        yield instance


def product(client, *, status="pending", listings=None, legacy=False, name="待处理商品"):
    output = io.BytesIO()
    Image.new("RGB", (80, 80), "#638a7b").save(output, "PNG")
    response = client.post("/api/products", data={"metadata": json.dumps({
        "name": name, "status": status, "specs": "白色 / 2件装", "notes": "保持原始资料", "listings": listings or [],
    })}, files={"images": ("fixture.png", output.getvalue(), "image/png")})
    response.raise_for_status()
    created = response.json()
    if legacy:
        # Reproduce a migrated record, without inventing platform rows.
        with backend._connection(write=True) as conn:
            conn.execute("UPDATE products SET listings_initialized=0 WHERE id=?", (created["id"],))
        created = client.get("/api/products/" + created["id"]).json()
    return created


def listing(platform="Temu", status="pending", price=""):
    return {"platform": platform, "status": status, "price": price, "currency": "USD", "product_number": "0000123"}


def ids_in(client, queue, **filters):
    response = client.get("/api/products", params={"workflow_state": queue, **filters})
    response.raise_for_status()
    payload = response.json()
    assert payload["total"] == len(payload["items"])
    return {item["id"] for item in payload["items"]}


@pytest.mark.parametrize("status", ["pending", "listed", "archived"])
@pytest.mark.parametrize("legacy", [False, True])
def test_missing_platform_is_in_all_three_queues_regardless_old_global_status(client, status, legacy):
    created = product(client, status=status, legacy=legacy)
    assert created["status"] == status
    assert created["listings"] == []
    assert set(created["workflow_states"]) == TODO_QUEUES
    assert created["workflow_state"] == ("legacy" if legacy else "unconfigured")
    for queue in TODO_QUEUES:
        assert ids_in(client, queue) == {created["id"]}
    assert ids_in(client, "listed") == set()
    assert ids_in(client, "archived") == set()
    stats = client.get("/api/stats").json()
    assert stats["pending_products"] == 1
    assert all(stats["workflow_counts"][queue] == 1 for queue in TODO_QUEUES)


@pytest.mark.parametrize("platform_status", ["pending", "listed"])
def test_missing_price_does_not_override_manual_availability(client, platform_status):
    created = product(client, listings=[listing(status=platform_status)])
    expected = TODO_QUEUES if platform_status == "pending" else {"unconfigured", "pricing", "listed"}
    assert created["workflow_state"] == ("pricing" if platform_status == "pending" else "listed")
    assert set(created["workflow_states"]) == expected
    assert created["listings"][0]["status"] == platform_status
    assert created["listings"][0]["price"] == ""
    for queue in expected:
        assert ids_in(client, queue) == {created["id"]}
    assert ids_in(client, "pending") == ({created["id"]} if platform_status == "pending" else set())


def test_priced_manual_pending_platform_needs_only_preparation_queue(client):
    created = product(client, listings=[listing(price="19.90")])
    assert created["workflow_state"] == "pending"
    assert created["workflow_states"] == ["pending"]
    assert ids_in(client, "pending") == {created["id"]}
    assert ids_in(client, "pricing") == set()
    assert ids_in(client, "unconfigured") == set()


def test_listed_platform_can_coexist_with_unpriced_platform_and_keeps_independent_state(client):
    created = product(client, listings=[listing(status="listed", price="20"), listing("Amazon")])
    assert created["workflow_state"] == "listed"
    assert set(created["workflow_states"]) == TODO_QUEUES | {"listed"}
    for queue in TODO_QUEUES | {"listed"}:
        assert ids_in(client, queue) == {created["id"]}
    # A platform filter and its status must still match the same listing row.
    assert ids_in(client, "pricing", platform="Amazon", listing_status="pending") == {created["id"]}
    assert ids_in(client, "pricing", platform="Amazon", listing_status="listed") == set()
    saved = client.get("/api/products/" + created["id"]).json()
    assert saved["listings"] == created["listings"]


def test_missing_price_on_archived_platform_does_not_reopen_its_work(client):
    created = product(client, listings=[listing(status="listed", price="20"), listing("Amazon", status="archived")])
    assert created["workflow_states"] == ["listed"]
    assert ids_in(client, "listed") == {created["id"]}
    for queue in TODO_QUEUES:
        assert ids_in(client, queue) == set()
    assert ids_in(client, "listed", platform="Amazon", listing_status="archived") == {created["id"]}


def test_all_platforms_manually_archived_remain_archived_even_without_prices(client):
    created = product(client, listings=[listing(status="archived"), listing("Amazon", status="archived", price="15")])
    assert created["workflow_state"] == "archived"
    assert created["workflow_states"] == ["archived"]
    for queue in TODO_QUEUES | {"listed"}:
        assert ids_in(client, queue) == set()
    assert ids_in(client, "archived") == {created["id"]}
    assert client.get("/api/stats").json()["pending_products"] == 0


def test_queue_counts_match_filters_and_total_counts_are_not_multiplied(client):
    cases = [
        product(client, name="新商品"),
        product(client, name="旧已上架", status="listed", legacy=True),
        product(client, name="旧已归档", status="archived", legacy=True),
        product(client, name="未定价", listings=[listing()]),
        product(client, name="准备上架", listings=[listing(price="20")]),
        product(client, name="在售并准备", listings=[listing(status="listed", price="20"), listing("Amazon")]),
        product(client, name="在售及已下架", listings=[listing(status="listed", price="20"), listing("Amazon", status="archived")]),
        product(client, name="全部下架", listings=[listing(status="archived")]),
    ]
    stats = client.get("/api/stats").json()
    assert stats["total_products"] == 8
    assert stats["workflow_categories_overlap"] is True
    assert {key: stats["workflow_counts"][key] for key in TODO_QUEUES} == {
        "unconfigured": 5, "pricing": 5, "pending": 6,
    }
    assert stats["workflow_counts"]["listed"] == 2
    assert stats["workflow_counts"]["archived"] == 1
    assert stats["pending_products"] == 6  # Product union, not 5+5+6.
    assert sum(stats["workflow_counts"].values()) > stats["total_products"]
    assert sum(stats["workflow_primary_counts"].values()) == stats["total_products"]
    for queue in backend.WORKFLOW_STATES:
        matching = ids_in(client, queue)
        assert len(matching) == stats["workflow_counts"][queue]
        if queue != "legacy":
            assert matching == {item["id"] for item in cases if queue in item["workflow_states"]}
    # Soft removal removes one product from every queue to which it belonged.
    removed = cases[5]
    client.delete("/api/products/" + removed["id"]).raise_for_status()
    after = client.get("/api/stats").json()
    assert after["total_products"] == 7
    assert after["pending_products"] == 5
    for queue in TODO_QUEUES | {"listed"}:
        assert after["workflow_counts"][queue] == stats["workflow_counts"][queue] - 1
        assert removed["id"] not in ids_in(client, queue)


def test_reading_new_queues_does_not_rewrite_legacy_business_records_or_images(client):
    created = product(client, status="archived", legacy=True)
    with backend._connection() as conn:
        before = dict(conn.execute("SELECT * FROM products WHERE id=?", (created["id"],)).fetchone())
        before_images = [dict(row) for row in conn.execute("SELECT * FROM images WHERE product_id=? ORDER BY id", (created["id"],))]
    image_hashes = {row["filename"]: hashlib.sha256((backend.DATA_DIR / "images" / row["filename"]).read_bytes()).hexdigest() for row in before_images}
    for queue in TODO_QUEUES:
        ids_in(client, queue)
    client.get("/api/stats").raise_for_status()
    client.get("/api/products/" + created["id"]).raise_for_status()
    with backend._connection() as conn:
        assert dict(conn.execute("SELECT * FROM products WHERE id=?", (created["id"],)).fetchone()) == before
        assert [dict(row) for row in conn.execute("SELECT * FROM images WHERE product_id=? ORDER BY id", (created["id"],))] == before_images
    assert {name: hashlib.sha256((backend.DATA_DIR / "images" / name).read_bytes()).hexdigest() for name in image_hashes} == image_hashes
