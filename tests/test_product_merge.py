"""Saved-product merging is tested only against temporary local catalogs."""
import hashlib
import io
import json
from pathlib import Path
import sys
import types
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import exchange_rates
import knowledge_store
import price_storage
import price_tracking
import product_management
import product_merge
import store_import_storage


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "merge-test-only")
    price_storage.init_storage()
    knowledge_store.init_storage()
    product_merge.init_storage()
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    monkeypatch.setattr(backend, "_semantic_model_status", lambda: {"loaded": False, "available": False, "model_id": "", "error": None})
    monkeypatch.setattr(backend, "start_indexing", lambda *a, **k: {})
    monkeypatch.setattr(exchange_rates, "refresh_rates", lambda *a, **k: None)
    if product_merge.permissions is None:
        def require(user, key):
            if key in user.get("denied_permissions", []):
                raise HTTPException(403, "测试权限被拒绝")
        def require_product(user, row, action="edit"):
            require(user, "products." + action)
            backend._may_edit(row, user)
        monkeypatch.setattr(product_merge, "permissions", types.SimpleNamespace(require=require, require_product=require_product))
    app = FastAPI()
    app.include_router(product_merge.router)
    app.include_router(product_management.router)
    app.include_router(price_tracking.router)
    app.include_router(knowledge_store.router)
    app.include_router(backend.router)
    with TestClient(app) as result:
        yield result


def setup(client):
    response = client.post("/api/setup", json={"name": "合并测试管理员", "password": "merge-test-only"})
    response.raise_for_status()
    return response.json()["user"]


def create(client, name="合并测试商品", **fields):
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), "orange").save(stream, "PNG")
    response = client.post("/api/products", data={"metadata": json.dumps({"name": name, **fields})},
                           files={"images": ("sample.png", stream.getvalue(), "image/png")})
    response.raise_for_status()
    return response.json()


def listing(platform="Temu", store="美国一店", code="RED", **values):
    return {"platform": platform, "store": store, "product_code": code, "product_number": "SKC-10",
            "status": "listed", "price": "20", "currency": "USD", "link": "https://shop.example.com/product/10",
            "variant_hint": "红色 2件装", "region": "美国", **values}


def preview(client, items, target=None):
    ids = [item["id"] for item in items]
    response = client.post("/api/products/merge/preview", json={"ids": ids, "target_id": target or ids[0]})
    response.raise_for_status()
    return response.json()


def merge(client, draft):
    return client.post("/api/products/merge", json={key: draft[key] for key in ("ids", "target_id", "expected_token")})


def business_snapshot(ids):
    with backend._connection() as conn:
        return product_merge._snapshot(conn, sorted(ids))


def source_table():
    # Use the application's actual schema: the central product serializer now
    # exposes provenance fields as well as the mapping's ownership identifiers.
    store_import_storage.init_storage()


def source_row(item, key="source-key"):
    row = item["listings"][0]
    with backend._connection(write=True) as conn:
        conn.execute("""INSERT INTO store_import_sources(
            source_key,product_key,platform,store_id,store_name,site,source_product_id,source_variant_id,
            product_id,listing_id,source_url,image_url,captured_title,captured_spec,captured_price,captured_currency,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (key, "product-" + key, row["platform"], "70000000000000001", row["store"], "www.temu.com",
                      "700000000000001", "public-red", item["id"], row["id"],
                      "https://www.temu.com/g-700000000000001.html", "https://images.example.com/head.jpg",
                      item["name"], row.get("variant_hint", "红色 2件装"), "12.50", "USD", backend.now()))


def quote(item, user, value="12.50"):
    identity = item["listings"][0]["id"]
    target = price_storage.get_target(identity, user)
    price_storage.save_manual_price(identity, {"price": value, "currency": "USD", "expected_context": target["context_token"]}, user)


def test_merge_keeps_all_platform_shops_skus_images_prices_and_source_ids(client):
    user = setup(client)
    a = create(client, "主商品", specs="主规格", category="蜡烛", notes="主备注", listings=[listing()])
    b = create(client, "同品第二店", specs="源规格", notes="保留此原备注", listings=[listing(store="美国二店", price="30", status="archived")])
    c = create(client, "其他平台", listings=[listing(platform="Amazon", store="品牌美国店", code="AMZ", price="40", status="pending")])
    source_table()
    source_row(b)
    quote(a, user)
    quote(b, user, "25")
    with backend._connection(write=True) as conn:
        image = conn.execute("SELECT id,sha256 FROM images WHERE product_id=?", (b["id"],)).fetchone()
        conn.execute("INSERT INTO image_embeddings VALUES(?,?,?,?,?)", (image["id"], "test-model", image["sha256"], "[0,1]", backend.now()))
        conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)", ("test-decision", b["id"], "same", "历史同款确认", "fingerprint", user["id"], backend.now()))
    before = business_snapshot([a["id"], b["id"], c["id"]])
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (backend.DATA_DIR / "images").iterdir()}
    tokens = {row["id"]: price_storage.get_target(row["id"], user)["context_token"] for item in [a, b, c] for row in item["listings"]}
    draft = preview(client, [a, b, c])
    assert draft["image_count"] == 3 and draft["listing_count"] == 3
    response = merge(client, draft)
    assert response.status_code == 200, response.text
    result = response.json()
    product = result["product"]
    for key in ("id", "code", "name", "owner", "specs", "category", "notes", "link", "created_at", "created_by"):
        assert product[key] == a[key]
    assert {row["id"] for row in product["images"]} == {row["id"] for item in [a, b, c] for row in item["images"]}
    assert {(row["platform"], row["store"], row["product_code"], row["status"], row["price"]) for row in product["listings"]} == {
        ("Temu", "美国一店", "RED", "listed", "20"), ("Temu", "美国二店", "RED", "archived", "30"), ("Amazon", "品牌美国店", "AMZ", "pending", "40")}
    assert {row["id"]: price_storage.get_target(row["id"], user)["context_token"] for row in product["listings"]} == tokens
    after = business_snapshot(draft["ids"])
    assert before["price_tracking"] == after["price_tracking"] and before["price_history"] == after["price_history"]
    assert after["decisions"][0]["id"] == "test-decision" and after["decisions"][0]["product_id"] == a["id"]
    assert after["external_sources"][0] == {**before["external_sources"][0], "product_id": a["id"]}
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (backend.DATA_DIR / "images").iterdir()} == files
    assert client.get("/api/products").json()["total"] == 1
    assert client.get("/api/products/" + b["id"]).status_code == 404
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM image_embeddings WHERE image_id=?", (image["id"],)).fetchone()[0] == 1
        assert product_merge.merge_info(conn, b["id"])["merged_into"] == a["id"]
        with pytest.raises(HTTPException) as error:
            product_merge.assert_restore_allowed(conn, b["id"])
        assert error.value.status_code == 409
    history = client.get("/api/product-merges/" + result["merge_id"]).json()
    assert history["can_undo"] and len(history["original_products"]) == 3
    assert next(row for row in history["original_products"] if row["id"] == b["id"])["notes"] == "保留此原备注"
    assert client.get(f"/api/products/{a['id']}/merge-history").json()["items"][0]["id"] == result["merge_id"]
    source_history = client.get(f"/api/products/{b['id']}/merge-history").json()
    assert source_history["merged_into"] == a["id"] and source_history["items"][0]["id"] == result["merge_id"]
    # Dirty-product triggers include old and new owners; company text search
    # finds the moved store/SKU only on the active main company product.
    found = knowledge_store.retrieve_products("美国二店")
    assert [row["id"] for row in found] == [a["id"]]


def test_undo_restores_exact_business_snapshot_and_keeps_permanent_journal(client):
    user = setup(client)
    a = create(client, "主商品", listings=[listing()])
    b = create(client, "源商品", listings=[listing(store="第二店")])
    source_table()
    source_row(a, "source-a")
    source_row(b, "source-b")
    quote(b, user)
    original = business_snapshot([a["id"], b["id"]])
    result = merge(client, preview(client, [a, b])).json()
    response = client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]})
    assert response.status_code == 200, response.text
    assert business_snapshot([a["id"], b["id"]]) == original
    assert client.get("/api/products").json()["total"] == 2
    with backend._connection() as conn:
        assert product_merge.merge_info(conn, b["id"]) is None
        product_merge.assert_restore_allowed(conn, b["id"])
        assert conn.execute("SELECT COUNT(*) FROM product_merge_operations").fetchone()[0] == 1
        actions = [row[0] for row in conn.execute("SELECT action FROM activity ORDER BY id DESC LIMIT 2")]
        assert actions == ["undo_product_merge", "merge_products"]
    history = client.get(f"/api/product-merges/{result['merge_id']}").json()
    assert history["undone_at"] and not history["can_undo"]
    assert client.get(f"/api/products/{a['id']}/merge-history").json()["items"][0]["undone_at"]
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 409


@pytest.mark.parametrize("change", ["target", "source", "listing", "quote", "source_map", "decision"])
def test_stale_preview_refuses_merge_without_partial_changes(client, change):
    user = setup(client)
    a = create(client, "主商品", listings=[listing()])
    b = create(client, "源商品", listings=[listing(store="另一店")])
    source_table()
    source_row(b)
    draft = preview(client, [a, b])
    mutate(client, change, a, b, user)
    original = business_snapshot(draft["ids"])
    response = merge(client, draft)
    assert response.status_code == 409
    assert business_snapshot(draft["ids"]) == original
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_merge_operations").fetchone()[0] == 0


def mutate(client, change, a, b, user):
    if change in {"target", "source"}:
        response = client.patch("/api/products/" + (a if change == "target" else b)["id"], json={"notes": "后续编辑"})
        response.raise_for_status()
    elif change == "listing":
        response = client.patch(f"/api/products/{b['id']}/listings/{b['listings'][0]['id']}/status", json={"status": "archived", "expected_status": "listed"})
        response.raise_for_status()
    elif change == "quote":
        quote(b, user, "14")
    elif change == "source_map":
        with backend._connection(write=True) as conn:
            conn.execute("UPDATE store_import_sources SET captured_price='18'")
    else:
        with backend._connection(write=True) as conn:
            conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, b["id"], "same", "后续同款确认", "fingerprint", user["id"], backend.now()))


@pytest.mark.parametrize("change", ["target", "listing", "quote", "source_map", "decision"])
def test_later_edits_block_undo_without_losing_new_work(client, change):
    user = setup(client)
    a = create(client, "主商品", listings=[listing()])
    b = create(client, "源商品", listings=[listing(store="另一店")])
    source_table()
    source_row(b)
    result = merge(client, preview(client, [a, b])).json()
    # Moved rows now belong to the main product.
    moved = {**b, "id": a["id"]}
    mutate(client, change, a, moved, user)
    current = business_snapshot([a["id"], b["id"]])
    response = client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]})
    assert response.status_code == 409
    assert business_snapshot([a["id"], b["id"]]) == current
    assert not client.get(f"/api/product-merges/{result['merge_id']}").json()["can_undo"]


@pytest.mark.parametrize("values", [None, [], ["one"], ["same", "same"], ["x", 1], ["x", None], ["x", ""], ["x", " "], ["x", "z" * 65], ["z"] * 51])
def test_invalid_selection(client, values):
    setup(client)
    response = client.post("/api/products/merge/preview", json={"ids": values, "target_id": "x"})
    assert response.status_code == 400


def test_selection_needs_member_main_and_no_unknown_fields_or_missing_token(client):
    setup(client)
    a, b = create(client), create(client)
    payload = {"ids": [a["id"], b["id"]], "target_id": "another"}
    assert client.post("/api/products/merge/preview", json=payload).status_code == 400
    payload["target_id"] = a["id"]
    assert client.post("/api/products/merge/preview", json={**payload, "extra": True}).status_code == 400
    assert client.post("/api/products/merge", json=payload).status_code == 400
    assert client.post("/api/products/merge", json={**payload, "expected_token": "bad"}).status_code == 400
    assert client.post("/api/products/merge/preview", json={"ids": [a["id"], "missing"], "target_id": a["id"]}).status_code == 404


def test_authentication_origin_and_all_product_permissions(client):
    assert client.post("/api/products/merge/preview", json={"ids": ["a", "b"], "target_id": "a"}).status_code == 401
    setup(client)
    a, b = create(client, "管理员商品"), create(client, "另一个管理员商品")
    draft = preview(client, [a, b])
    payload = {key: draft[key] for key in ("ids", "target_id", "expected_token")}
    assert client.post("/api/products/merge", json=payload, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post("/api/products/merge", json=payload, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/team", json={"name": "普通运营", "password": "operator-merge-test", "role": "operator"}).status_code == 200
    client.post("/api/logout")
    client.post("/api/login", json={"name": "普通运营", "password": "operator-merge-test"}).raise_for_status()
    own = create(client, "自己录入的商品")
    response = client.post("/api/products/merge/preview", json={"ids": [own["id"], a["id"]], "target_id": own["id"]})
    assert response.status_code == 403
    assert merge(client, draft).status_code == 403
    assert client.get("/api/products").json()["total"] == 3


def test_operator_can_merge_all_owned_records_and_cannot_read_other_merge_history(client):
    setup(client)
    a, b = create(client), create(client)
    result = merge(client, preview(client, [a, b])).json()
    member = client.post("/api/team", json={"name": "普通运营", "password": "operator-merge-test", "role": "operator"})
    member.raise_for_status()
    if product_merge.permissions and hasattr(product_merge.permissions, "DEFAULTS"):
        client.patch("/api/team/" + member.json()["id"], json={"expected_revision": member.json()["revision"], "permissions": {"products.merge": True}}).raise_for_status()
    client.post("/api/logout")
    client.post("/api/login", json={"name": "普通运营", "password": "operator-merge-test"}).raise_for_status()
    assert client.get(f"/api/product-merges/{result['merge_id']}").status_code == 403
    assert client.get(f"/api/products/{a['id']}/merge-history").status_code == 403
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 403
    c, d = create(client, "自己甲"), create(client, "自己乙")
    assert merge(client, preview(client, [c, d])).status_code == 200


def test_preview_is_independent_of_selection_order_and_no_sku_deduplication(client):
    setup(client)
    a, b = create(client, listings=[listing()]), create(client, listings=[listing()])
    first, second = preview(client, [a, b], a["id"]), preview(client, [b, a], a["id"])
    assert first["expected_token"] == second["expected_token"]
    result = merge(client, second)
    assert result.status_code == 200
    assert len(result.json()["product"]["listings"]) == 2
    assert merge(client, first).status_code == 404


def test_merge_and_undo_roll_back_every_row_if_audit_fails(client, monkeypatch):
    setup(client)
    a, b = create(client, listings=[listing()]), create(client, listings=[listing()])
    draft = preview(client, [a, b])
    before = business_snapshot(draft["ids"])
    original_audit = backend._audit
    def fail(*args, **kwargs):
        raise RuntimeError("isolated audit failure")
    monkeypatch.setattr(backend, "_audit", fail)
    with pytest.raises(RuntimeError, match="isolated audit failure"):
        merge(client, draft)
    assert business_snapshot(draft["ids"]) == before
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_merge_sources").fetchone()[0] == 0
    monkeypatch.setattr(backend, "_audit", original_audit)
    result = merge(client, draft).json()
    merged = business_snapshot(draft["ids"])
    monkeypatch.setattr(backend, "_audit", fail)
    with pytest.raises(RuntimeError, match="isolated audit failure"):
        client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]})
    assert business_snapshot(draft["ids"]) == merged
    with backend._connection() as conn:
        assert not conn.execute("SELECT undone_at FROM product_merge_operations").fetchone()[0]


def test_chained_merges_resolve_main_and_only_undo_in_safe_order(client):
    setup(client)
    a, b, c = create(client, "A"), create(client, "B"), create(client, "C")
    first = merge(client, preview(client, [a, b])).json()
    second = merge(client, preview(client, [a, c], c["id"])).json()
    with backend._connection() as conn:
        assert product_merge.merge_info(conn, b["id"])["merged_into"] == c["id"]
    assert client.post(f"/api/product-merges/{first['merge_id']}/undo", json={"expected_token": first["undo_token"]}).status_code == 409
    assert client.post(f"/api/product-merges/{second['merge_id']}/undo", json={"expected_token": second["undo_token"]}).status_code == 200
    assert client.post(f"/api/product-merges/{first['merge_id']}/undo", json={"expected_token": first["undo_token"]}).status_code == 200
    assert client.get("/api/products").json()["total"] == 3


@pytest.mark.parametrize("table,total", [("images", 31), ("product_listings", 101)])
def test_existing_editor_limits_refuse_without_dropping_media_or_skus(client, table, total):
    setup(client)
    a, b = create(client, listings=[listing()]), create(client, listings=[listing()])
    with backend._connection(write=True) as conn:
        row = dict(conn.execute(f"SELECT * FROM {table} LIMIT 1").fetchone())
        columns = list(row)
        for _ in range(total - 2):
            row["id"] = uuid.uuid4().hex
            conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", [row[key] for key in columns])
    response = client.post("/api/products/merge/preview", json={"ids": [a["id"], b["id"]], "target_id": a["id"]})
    assert response.status_code == 400
    assert len(business_snapshot([a["id"], b["id"]])["images" if table == "images" else "listings"]) == total


def test_init_is_repeatable_and_history_not_found_is_bounded(client):
    setup(client)
    product_merge.init_storage()
    assert client.get("/api/product-merges/not-real").status_code == 404
    assert client.get("/api/product-merges/" + "f" * 32).status_code == 404
    assert client.post("/api/product-merges/" + "f" * 32 + "/undo", json={"expected_token": "0" * 64}).status_code == 404


@pytest.mark.parametrize("denied", ["products.merge", "products.delete", "products.edit"])
def test_merge_checks_global_and_individual_removal_permissions(client, monkeypatch, denied):
    setup(client)
    a, b = create(client), create(client)
    draft = preview(client, [a, b])
    original = business_snapshot(draft["ids"])
    previous = product_merge.permissions.require
    def require(user, key):
        if key == denied:
            raise HTTPException(403, "本次测试拒绝此操作")
        return previous(user, key)
    original_product = product_merge.permissions.require_product
    def require_product(user, product, action="edit"):
        require(user, "products." + action)
        return original_product(user, product, action)
    monkeypatch.setattr(product_merge.permissions, "require", require)
    monkeypatch.setattr(product_merge.permissions, "require_product", require_product)
    assert merge(client, draft).status_code == 403
    assert business_snapshot(draft["ids"]) == original


def test_undo_requires_restore_for_sources_and_incomplete_permission_service_denies(client, monkeypatch):
    setup(client)
    a, b = create(client), create(client)
    draft = preview(client, [a, b])
    result = merge(client, draft).json()
    original = business_snapshot(draft["ids"])
    previous = product_merge.permissions.require_product
    def require_product(user, product, action="edit"):
        if action == "restore":
            raise HTTPException(403, "不允许恢复")
        return previous(user, product, action)
    monkeypatch.setattr(product_merge.permissions, "require_product", require_product)
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 403
    assert business_snapshot(draft["ids"]) == original
    monkeypatch.setattr(product_merge, "permissions", None)
    assert client.get(f"/api/product-merges/{result['merge_id']}").status_code == 503


def test_real_permissions_prevent_edit_privilege_bypassing_delete_or_restore(client):
    setup(client)
    if not hasattr(product_merge.permissions, "DEFAULTS"):
        pytest.skip("Permission implementation not installed in isolated development checkout")
    member = client.post("/api/team", json={"name": "第二管理员", "password": "second-admin-merge", "role": "admin"}).json()
    client.post("/api/logout")
    client.post("/api/login", json={"name": "第二管理员", "password": "second-admin-merge"}).raise_for_status()
    a, b = create(client, "管理员商品A"), create(client, "管理员商品B")
    draft = preview(client, [a, b])
    def deny(keys):
        with backend._connection(write=True) as conn:
            conn.execute("DELETE FROM permission_overrides WHERE user_id=?", (member["id"],))
            for key in keys:
                conn.execute("INSERT INTO permission_overrides VALUES(?,?,0)", (member["id"], key))
    deny(["products.delete_all", "products.delete_own"])
    assert merge(client, draft).status_code == 403
    assert client.get("/api/me").json()["user"]["permissions"]
    deny([])
    result = merge(client, preview(client, [a, b])).json()
    deny(["products.restore_all", "products.restore_own"])
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 403
    assert not client.get(f"/api/product-merges/{result['merge_id']}").json()["can_undo"]
    deny(["products.merge"])
    assert client.get("/api/product-merges").status_code == 403


def test_purge_guard_protects_sources_and_main_until_undo(client):
    setup(client)
    a, b, c = create(client), create(client), create(client)
    result = merge(client, preview(client, [a, b])).json()
    with backend._connection() as conn:
        for item in [a, b]:
            with pytest.raises(HTTPException) as error:
                product_merge.assert_purge_allowed(conn, item["id"])
            assert error.value.status_code == 409
        product_merge.assert_purge_allowed(conn, c["id"])
    client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).raise_for_status()
    with backend._connection() as conn:
        for item in [a, b, c]:
            product_merge.assert_purge_allowed(conn, item["id"])
        assert not any(row["from"] == "target_id" for row in conn.execute("PRAGMA foreign_key_list(product_merge_operations)"))
    # Simulate the final DB step of an authorized, already validated purge of
    # an undone product, retaining all immutable journal snapshots.
    with backend._connection(write=True) as conn:
        conn.execute("DELETE FROM images WHERE product_id=?", (a["id"],))
        conn.execute("DELETE FROM products WHERE id=?", (a["id"],))
    assert client.get("/api/product-merges").status_code == 200
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM product_merge_operations").fetchone()[0] == 1


def knowledge(client, products, *, title="关联商品知识"):
    response = client.post("/api/knowledge/documents", json={"title": title, "content": "保留原始知识正文", "product_ids": [product["id"] for product in products]})
    response.raise_for_status()
    return response.json()


def test_knowledge_link_or_content_edit_invalidates_merge_preview(client):
    setup(client)
    a, b, c = create(client, "主"), create(client, "源"), create(client, "另一商品")
    doc = knowledge(client, [b, c])
    draft = preview(client, [a, b])
    response = client.patch("/api/knowledge/documents/" + doc["id"], json={"content": "后续编辑", "expected_revision": doc["revision"]})
    response.raise_for_status()
    assert merge(client, draft).status_code == 409
    draft = preview(client, [a, b])
    response = client.patch("/api/knowledge/documents/" + doc["id"], json={"product_ids": [a["id"], c["id"]], "expected_revision": response.json()["revision"]})
    response.raise_for_status()
    assert merge(client, draft).status_code == 409


@pytest.mark.parametrize("nullable_legacy_column", [False, True])
def test_legacy_journal_additive_migration_preserves_old_token_shape(client, nullable_legacy_column):
    setup(client)
    a, b = create(client), create(client)
    result = merge(client, preview(client, [a, b])).json()
    with backend._connection(write=True) as conn:
        operation = conn.execute("SELECT * FROM product_merge_operations WHERE id=?", (result["merge_id"],)).fetchone()
        old_snapshot = json.loads(operation["before_snapshot"])
        old_snapshot.pop("knowledge_links", None)
        old_token = product_merge._token(product_merge._snapshot(conn, sorted([a["id"], b["id"]]), include_knowledge=False))
        conn.execute("UPDATE product_merge_operations SET before_snapshot=?,after_token=? WHERE id=?", (json.dumps(old_snapshot), old_token, result["merge_id"]))
        conn.execute("ALTER TABLE product_merge_operations DROP COLUMN knowledge_changes")
        if nullable_legacy_column:
            conn.execute("ALTER TABLE product_merge_operations ADD COLUMN knowledge_changes TEXT")
    product_merge.init_storage()
    with backend._connection() as conn:
        operation = conn.execute("SELECT * FROM product_merge_operations WHERE id=?", (result["merge_id"],)).fetchone()
        assert operation["knowledge_changes"] == (None if nullable_legacy_column else "{}")
    assert client.get(f"/api/product-merges/{result['merge_id']}").json()["can_undo"]
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": old_token}).status_code == 200


def knowledge_versions(identity):
    with backend._connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM knowledge_document_versions WHERE document_id=? ORDER BY revision", (identity,))]


def test_merge_remaps_and_deduplicates_knowledge_links_with_append_only_versions(client):
    setup(client)
    a, b, c, unrelated = create(client, "主商品"), create(client, "来源B"), create(client, "来源C"), create(client, "关联的其他商品")
    doc = knowledge(client, [b, unrelated, a, c])
    original_versions = knowledge_versions(doc["id"])
    before = business_snapshot([a["id"], b["id"], c["id"]])
    result = merge(client, preview(client, [a, b, c])).json()
    current = client.get("/api/knowledge/documents/" + doc["id"]).json()
    assert current["product_ids"] == [a["id"], unrelated["id"]]
    assert current["revision"] == doc["revision"] + 1
    assert current["content"] == doc["content"]
    versions = knowledge_versions(doc["id"])
    assert versions[:-1] == original_versions
    assert versions[-1]["action"] == "product_merge"
    assert json.loads(versions[-1]["product_ids"]) == [a["id"], unrelated["id"]]
    assert json.loads(versions[-1]["related_products"]) == [{"id": a["id"], "code": a["code"], "name": a["name"]}, {"id": unrelated["id"], "code": unrelated["code"], "name": unrelated["name"]}]
    with backend._connection() as conn:
        changes = json.loads(conn.execute("SELECT knowledge_changes FROM product_merge_operations WHERE id=?", (result["merge_id"],)).fetchone()[0])
        assert changes["documents"] == [{"document_id": doc["id"], "before_product_ids": [b["id"], unrelated["id"], a["id"], c["id"]], "after_product_ids": [a["id"], unrelated["id"]], "after_revision": current["revision"]}]
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 200
    restored = client.get("/api/knowledge/documents/" + doc["id"]).json()
    assert restored["product_ids"] == doc["product_ids"]
    assert restored["revision"] == current["revision"] + 1
    assert knowledge_versions(doc["id"])[:-1] == versions
    assert knowledge_versions(doc["id"])[-1]["action"] == "product_merge_undo"
    after = business_snapshot([a["id"], b["id"], c["id"]])
    assert after["knowledge_links"][0]["links"] == before["knowledge_links"][0]["links"]
    before.pop("knowledge_links"); after.pop("knowledge_links")
    assert before == after


@pytest.mark.parametrize("change", ["content", "links", "archive"])
def test_knowledge_followup_changes_block_undo_without_overwrite(client, change):
    setup(client)
    a, b, c = create(client), create(client), create(client)
    doc = knowledge(client, [b])
    result = merge(client, preview(client, [a, b])).json()
    updated = client.get("/api/knowledge/documents/" + doc["id"]).json()
    if change == "archive":
        response = client.request("DELETE", "/api/knowledge/documents/" + doc["id"], json={"expected_revision": updated["revision"]})
    else:
        field = {"content": "新的知识正文"} if change == "content" else {"product_ids": [c["id"]]}
        response = client.patch("/api/knowledge/documents/" + doc["id"], json={**field, "expected_revision": updated["revision"]})
    response.raise_for_status()
    versions = knowledge_versions(doc["id"])
    before = business_snapshot([a["id"], b["id"]])
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 409
    assert not client.get(f"/api/product-merges/{result['merge_id']}").json()["can_undo"]
    assert business_snapshot([a["id"], b["id"]]) == before
    assert knowledge_versions(doc["id"]) == versions


def test_archived_knowledge_links_remap_and_undo_without_reactivation(client):
    setup(client)
    a, b = create(client), create(client)
    doc = knowledge(client, [b])
    client.request("DELETE", "/api/knowledge/documents/" + doc["id"], json={"expected_revision": doc["revision"]}).raise_for_status()
    result = merge(client, preview(client, [a, b])).json()
    current = client.get("/api/knowledge/documents/" + doc["id"]).json()
    assert current["deleted"] and current["product_ids"] == [a["id"]]
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 200
    restored = client.get("/api/knowledge/documents/" + doc["id"]).json()
    assert restored["deleted"] and restored["product_ids"] == [b["id"]]


def test_knowledge_edit_permissions_gate_merge_and_undo_atomically(client):
    setup(client)
    member = client.post("/api/team", json={"name": "关联测试管理员", "password": "knowledge-merge-admin", "role": "admin"}).json()
    a, b = create(client), create(client)
    doc = knowledge(client, [b])
    draft = preview(client, [a, b])
    client.post("/api/logout")
    client.post("/api/login", json={"name": "关联测试管理员", "password": "knowledge-merge-admin"}).raise_for_status()
    def deny_edit(deny):
        with backend._connection(write=True) as conn:
            conn.execute("DELETE FROM permission_overrides WHERE user_id=?", (member["id"],))
            if deny:
                conn.executemany("INSERT INTO permission_overrides VALUES(?,?,0)", [(member["id"], "knowledge.edit_all"), (member["id"], "knowledge.edit_own")])
    deny_edit(True)
    before, versions = business_snapshot(draft["ids"]), knowledge_versions(doc["id"])
    assert merge(client, draft).status_code == 403
    assert business_snapshot(draft["ids"]) == before and knowledge_versions(doc["id"]) == versions
    deny_edit(False)
    result = merge(client, preview(client, [a, b])).json()
    deny_edit(True)
    before, versions = business_snapshot(draft["ids"]), knowledge_versions(doc["id"])
    assert client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]}).status_code == 403
    assert not client.get(f"/api/product-merges/{result['merge_id']}").json()["can_undo"]
    assert business_snapshot(draft["ids"]) == before and knowledge_versions(doc["id"]) == versions


@pytest.mark.parametrize("when", ["merge", "undo"])
def test_knowledge_and_product_changes_roll_back_together_on_audit_failure(client, monkeypatch, when):
    setup(client)
    a, b = create(client), create(client)
    doc = knowledge(client, [b])
    draft = preview(client, [a, b])
    result = merge(client, draft).json() if when == "undo" else None
    before, versions = business_snapshot(draft["ids"]), knowledge_versions(doc["id"])
    previous = backend._audit
    def fail(conn, user, action, *args, **kwargs):
        if action == ("undo_product_merge" if when == "undo" else "merge_products"):
            raise RuntimeError("final audit failure after knowledge mutation")
        return previous(conn, user, action, *args, **kwargs)
    monkeypatch.setattr(backend, "_audit", fail)
    with pytest.raises(RuntimeError, match="final audit failure"):
        if when == "undo":
            client.post(f"/api/product-merges/{result['merge_id']}/undo", json={"expected_token": result["undo_token"]})
        else:
            merge(client, draft)
    assert business_snapshot(draft["ids"]) == before and knowledge_versions(doc["id"]) == versions
