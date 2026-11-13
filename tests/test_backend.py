"""Integration tests exercise real image extraction, SQLite and HTTP sessions."""
import io
import json
import sqlite3
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import backend


def image_bytes(shape="box", size=240):
    image = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(image)
    if shape == "box":
        draw.rounded_rectangle((40, 35, 200, 210), radius=12, fill="#397b6b", outline="#153c37", width=4)
        draw.rectangle((65, 70, 175, 125), fill="#edf0d2")
        draw.text((78, 86), "Midoo 26", fill="black")
        for x in range(65, 180, 20):
            draw.line((x, 145, x, 185), fill="#d3eddf", width=4)
    else:
        draw.ellipse((35, 50, 205, 190), fill="#d28c3f", outline="#402c1a", width=5)
        draw.line((50, 170, 190, 70), fill="#55341d", width=15)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.fixture
def client(tmp_path):
    backend.init_storage(tmp_path / "data")
    backend._backup_extras.clear()
    backend._login_attempts.clear()
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as test_client:
        yield test_client


def setup(client, name="管理员"):
    response = client.post("/api/setup", json={"name": name, "password": "a-strong-password"})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def create(client, name="测试收纳盒", shape="box", **metadata):
    response = client.post("/api/products", data={"metadata": json.dumps({"name": name, **metadata})},
                           files=[("images", ("商品.png", image_bytes(shape), "image/png"))])
    assert response.status_code == 200, response.text
    return response.json()


def import_batch(client, groups=None):
    groups = groups or [{"name": "收纳盒", "paths": ["收纳盒/主图.png", "收纳盒/侧面.png"]},
                        {"name": "装饰碗", "paths": ["装饰碗/图片.png"]}]
    return client.post("/api/import", data={"manifest": json.dumps({"groups": groups})}, files=[
        ("images", ("收纳盒/主图.png", image_bytes(), "image/png")),
        ("images", ("收纳盒/侧面.png", image_bytes(), "image/png")),
        ("images", ("装饰碗/图片.png", image_bytes("bowl"), "image/png")),
    ])


def test_first_admin_is_atomic_and_library_is_private(client):
    assert client.get("/api/bootstrap").json() == {"needs_setup": True}
    assert client.get("/api/products").status_code == 401
    response = client.post("/api/setup", json={"name": "管理员", "password": "short"})
    assert response.status_code == 400
    user = setup(client)
    assert user["role"] == "admin"
    assert client.get("/api/bootstrap").json() == {"needs_setup": False}
    assert client.post("/api/setup", json={"name": "第二位", "password": "another-password"}).status_code == 409
    assert "httponly" in client.cookies.__repr__().lower() or backend.COOKIE_NAME in client.cookies
    assert client.get("/api/stats").json()["total_products"] == 0
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_authentication_login_and_password_rotation(client):
    setup(client)
    client.post("/api/logout")
    assert client.post("/api/login", json={"name": "管理员", "password": "incorrect"}).status_code == 401
    assert client.post("/api/login", json={"name": "管理员", "password": "a-strong-password"}).status_code == 200
    assert client.put("/api/me/password", json={"current_password": "wrong", "new_password": "new-long-password"}).status_code == 400
    assert client.put("/api/me/password", json={"current_password": "a-strong-password", "new_password": "new-long-password"}).status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"name": "管理员", "password": "a-strong-password"}).status_code == 401
    assert client.post("/api/login", json={"name": "管理员", "password": "new-long-password"}).status_code == 200


def test_cross_site_writes_are_refused_even_on_setup(client):
    payload = {"name": "管理员", "password": "a-strong-password"}
    assert client.post("/api/setup", json=payload, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post("/api/setup", json=payload, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/setup", json=payload, headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/team", json={"name": "运营一", "password": "operator-password"}, headers={"Origin": "http://evil.invalid"}).status_code == 403


def test_real_product_import_grouping_search_and_decision(client):
    setup(client)
    response = import_batch(client)
    assert response.status_code == 200, response.text
    imported = response.json()
    assert imported["created"] == 2
    # Exact duplicate images within one product are represented once.
    assert imported["images"] == 2
    products = client.get("/api/products").json()
    assert products["total"] == 2
    box = next(p for p in products["items"] if p["name"] == "收纳盒")
    assert box["owner"] == "管理员"
    assert box["status"] == "pending"
    assert client.get(box["images"][0]["url"]).headers["content-type"] == "image/png"
    assert client.get(box["images"][0]["thumb_url"]).headers["content-type"] == "image/jpeg"
    response = client.post("/api/search", files=[("images", ("核价图.png", image_bytes(), "image/png"))])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total_searched"] == 2
    hit = result["items"][0]
    assert hit["product"]["id"] == box["id"]
    assert hit["exact"] is True
    assert hit["grade"] == "exact"
    assert hit["query_index"] == 0
    assert client.post("/api/decisions", json={"product_id": box["id"], "decision": "same", "query_fingerprint": hit["query_fingerprint"], "note": "规格已核对"}).status_code == 200
    repeated = client.post("/api/search", files=[("images", ("核价图.png", image_bytes(), "image/png"))]).json()
    assert repeated["items"][0]["previous_decision"]["decision"] == "same"
    activity = client.get("/api/activity").json()["items"]
    assert any(a["action"] == "import" and a["batch_id"] == imported["batch_id"] for a in activity)


def test_import_validation_is_all_or_nothing(client):
    setup(client)
    response = import_batch(client, groups=[{"name": "丢失图片", "paths": ["不存在.png"]}])
    assert response.status_code == 400
    assert client.get("/api/products").json()["total"] == 0
    assert not list((backend.DATA_DIR / "images").iterdir())
    response = client.post("/api/products", data={"metadata": "{}"}, files=[("images", ("fake.jpg", b"not an image", "image/jpeg"))])
    assert response.status_code == 400
    assert client.get("/api/products").json()["total"] == 0


def test_batch_undo_requires_unchanged_products(client):
    setup(client)
    imported = import_batch(client).json()
    assert client.post(f"/api/import/{imported['batch_id']}/undo").json()["removed"] == 2
    assert client.get("/api/products").json()["total"] == 0
    assert client.get(imported["items"][0]["images"][0]["url"]).status_code == 404
    assert client.post(f"/api/import/{imported['batch_id']}/undo").status_code == 404
    imported = import_batch(client).json()
    product = imported["items"][0]
    assert client.patch(f"/api/products/{product['id']}", json={"notes": "已确认真实规格"}).status_code == 200
    assert client.post(f"/api/import/{imported['batch_id']}/undo").status_code == 409
    assert client.get("/api/products").json()["total"] == 2


def test_duplicate_images_warn_without_merging_products(client):
    setup(client)
    imported1 = import_batch(client).json()
    imported2 = import_batch(client).json()
    assert imported1["duplicates"] == 0
    assert imported2["duplicates"] >= 2
    assert imported2["duplicate_matches"]
    assert client.get("/api/products").json()["total"] == 4


def test_operator_cannot_modify_other_operators_product_or_backup(client):
    setup(client)
    private = create(client)
    response = client.post("/api/team", json={"name": "运营小陈", "password": "operator-password", "role": "operator"})
    assert response.status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"name": "运营小陈", "password": "operator-password"}).status_code == 200
    assert client.patch(f"/api/products/{private['id']}", json={"name": "越权修改"}).status_code == 403
    assert client.delete(f"/api/products/{private['id']}").status_code == 403
    assert client.post("/api/backup").status_code == 403
    assert client.post("/api/team", json={"name": "越权成员", "password": "operator-password"}).status_code == 403
    own = create(client, "自己的商品")
    assert client.patch(f"/api/products/{own['id']}", json={"status": "listed"}).status_code == 200
    assert client.delete(f"/api/products/{own['id']}").status_code == 200
    assert client.get("/api/products").json()["total"] == 1


def test_backup_is_a_restoreable_snapshot_with_media(client, tmp_path):
    setup(client)
    product = create(client)
    extra = backend.DATA_DIR / "ai-settings.json"
    extra.write_text('{"provider":"deepseek"}', encoding="utf-8")
    backend.register_backup_file(extra, "ai-settings.json")
    response = client.post("/api/backup")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "library.db" in archive.namelist()
        assert "ai-settings.json" in archive.namelist()
        assert len([n for n in archive.namelist() if n.startswith("images/")]) == 1
        restore = tmp_path / "restored"
        archive.extractall(restore)
    connection = sqlite3.connect(restore / "library.db")
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT name FROM products WHERE id=?", (product["id"],)).fetchone()[0] == product["name"]
    finally:
        connection.close()
    assert client.get("/api/system").json()["last_backup"]


def test_scheduler_can_backup_before_first_setup(client):
    path = backend.create_backup()
    assert path.exists()
    with zipfile.ZipFile(path) as archive:
        assert "library.db" in archive.namelist()


def test_text_filters_and_ai_context_do_not_expose_full_links(client):
    setup(client)
    product = create(client, name="100% 棉花收纳盒", specs="绿色 2件装", link="https://example.com/private?token=hidden")
    create(client, "另一个装饰碗", shape="bowl")
    assert client.get("/api/products", params={"q": "100%"}).json()["total"] == 1
    assert client.get("/api/products", params={"q": "不存在"}).json()["total"] == 0
    context = backend.ai_context("绿色")
    assert context["products"][0]["id"] == product["id"]
    assert "link" not in context["products"][0]
    assert "hidden" not in json.dumps(context)
    assert context["stats"]["total_products"] == 2


def test_concurrent_product_submissions_receive_unique_codes(client):
    user = setup(client)
    prepared = backend._prepare_image(image_bytes(), "same.png")
    info = backend._metadata({"name": "同时登记商品"}, user)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: backend._create_product_sync(info, [prepared], user), range(4)))
    assert len({p["code"] for p in records}) == 4
    assert sum(bool(p["duplicate_matches"]) for p in records) == 3
    assert client.get("/api/products").json()["total"] == 4


class StubVisualModel:
    """Index lifecycle tests use deterministic vectors, not accuracy claims."""
    def __init__(self, model_id="index-contract-v1"):
        self.model_id = model_id
        self.calls = 0

    def availability(self):
        return {"available": True, "loaded": True, "model_id": self.model_id, "model": "Index contract test model"}

    def extract_embedding(self, content):
        self.calls += 1
        # The embedding is deliberately irrelevant to these lifecycle checks.
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def wait_for_index():
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        status = backend.search_index_status()
        if not status["indexing"]:
            return status
        time.sleep(.01)
    raise AssertionError("Index worker did not finish")


def test_semantic_index_upgrade_is_incremental_and_preserves_business_rows(client, monkeypatch):
    monkeypatch.setattr(backend, "semantic_engine", None)
    setup(client)
    import_batch(client)
    with backend._connection() as conn:
        original_products = [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")]
        original_images = [tuple(row) for row in conn.execute("SELECT * FROM images ORDER BY id")]
    model = StubVisualModel()
    monkeypatch.setattr(backend, "semantic_engine", model)
    backend.start_indexing()
    status = wait_for_index()
    assert status["state"] == "ready"
    assert status["indexed_images"] == status["total_images"] == 2
    assert model.calls == 2
    backend.start_indexing()
    assert wait_for_index()["state"] == "ready"
    assert model.calls == 2  # Already indexed images do not run inference again.
    model.model_id = "index-contract-v2"
    assert backend.search_index_status()["indexed_images"] == 0
    backend.start_indexing()
    assert wait_for_index()["indexed_images"] == 2
    assert model.calls == 4
    with backend._connection() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")] == original_products
        assert [tuple(row) for row in conn.execute("SELECT * FROM images ORDER BY id")] == original_images
        assert conn.execute("SELECT COUNT(*) FROM image_embeddings").fetchone()[0] == 4


def test_missing_model_does_not_present_handcrafted_appearance_as_semantic_match(client, monkeypatch):
    monkeypatch.setattr(backend, "semantic_engine", None)
    setup(client)
    create(client)
    original = Image.open(io.BytesIO(image_bytes()))
    output = io.BytesIO()
    original.save(output, "JPEG", quality=75)
    response = client.post("/api/search", files={"images": ("compressed.jpg", output.getvalue(), "image/jpeg")})
    assert response.status_code == 200
    result = response.json()
    assert result["items"] == result["additional_items"] == []
    assert result["engine_status"]["state"] == "unavailable"
    assert result["hidden_count"] == 1
    assert "原图" in result["notice"]


def test_feedback_for_every_query_image_affects_results_and_is_reversible(client, monkeypatch):
    monkeypatch.setattr(backend, "semantic_engine", None)
    setup(client)
    box = create(client)
    bowl = create(client, "另一款", shape="bowl")
    import hashlib
    second_hash = hashlib.sha256(image_bytes("bowl")).hexdigest()
    # The judgement is attached to the second query, while the box's exact image
    # is query zero. It must still apply to this multi-image lookup.
    payload = {"product_id": box["id"], "query_fingerprint": second_hash, "decision": "different"}
    assert client.post("/api/decisions", json=payload).status_code == 200
    files = [("images", ("first.png", image_bytes(), "image/png")), ("images", ("second.png", image_bytes("bowl"), "image/png"))]
    result = client.post("/api/search", files=files).json()
    assert box["id"] not in {item["product"]["id"] for item in result["items"] + result["additional_items"]}
    assert result["excluded_count"] == 1
    payload["decision"] = "same"
    assert client.post("/api/decisions", json=payload).status_code == 200
    repeated = client.post("/api/search", files=files).json()
    assert repeated["items"][0]["product"]["id"] == box["id"]
    assert repeated["items"][0]["match_type"] == "feedback"
    assert repeated["items"][0]["query_fingerprint"] == second_hash
    assert repeated["excluded_count"] == 0
