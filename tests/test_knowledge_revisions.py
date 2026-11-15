"""Enterprise knowledge permissions, revision history and explicit product links.

All tests use disposable SQLite libraries; no external models or shops are used.
"""
import io
import json
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import backend
import knowledge_store as knowledge
import product_management


PASSWORD = "knowledge-revisions-test-only"
OWNER = "企业知识老板"
BASE = "/api/knowledge/documents"


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "knowledge-revisions")
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    monkeypatch.setattr(knowledge, "_initialized", None)
    app = FastAPI()
    app.include_router(product_management.router)
    app.include_router(backend.router)
    app.include_router(knowledge.router)
    with TestClient(app) as instance:
        response = instance.post("/api/setup", json={"name": OWNER, "password": PASSWORD})
        response.raise_for_status()
        assert response.json()["user"]["role"] == "owner"
        yield instance


def login(client, name=OWNER):
    client.post("/api/logout").raise_for_status()
    client.post("/api/login", json={"name": name, "password": PASSWORD}).raise_for_status()


def member(client, name="企业知识管理员", role="admin", overrides=None):
    response = client.post("/api/team", json={"name": name, "password": PASSWORD, "role": role})
    response.raise_for_status()
    person = response.json()
    if overrides is not None:
        response = client.patch("/api/team/" + person["id"], json={
            "expected_revision": person["revision"], "permissions": overrides,
        })
        response.raise_for_status()
        person = response.json()
    return person


def note(client, **fields):
    response = client.post(BASE, json={"title": "企业包装规范", "content": "运输前核对红色套装件数。",
                                       "category": "包装", **fields})
    response.raise_for_status()
    return response.json()


def product(client, name="红色套装蜡烛"):
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "#bd483f").save(image, "PNG")
    response = client.post("/api/products", data={"metadata": json.dumps({"name": name})},
                           files={"images": ("sample.png", image.getvalue(), "image/png")})
    response.raise_for_status()
    return response.json()


def current(client, identity):
    response = client.get(BASE + "/" + identity)
    response.raise_for_status()
    return response.json()


def versions(client, identity, **params):
    response = client.get(BASE + "/" + identity + "/versions", params=params)
    response.raise_for_status()
    return response.json()


def version(client, identity, revision):
    response = client.get(f"{BASE}/{identity}/versions/{revision}")
    response.raise_for_status()
    return response.json()


def storage_snapshot():
    with backend._connection() as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'")]
        snapshot = {name: sorted([tuple(row) for row in conn.execute('SELECT * FROM "' + name + '"')], key=repr)
                    for name in names}
        snapshot["audit"] = [tuple(row) for row in conn.execute("SELECT * FROM activity WHERE action LIKE 'knowledge_%' ORDER BY id")]
        return snapshot


def archive(client, identity, **fields):
    return client.request("DELETE", BASE + "/" + identity, json=fields) if fields else client.delete(BASE + "/" + identity)


def test_new_documents_keep_immutable_versions_and_only_live_text_is_retrieved(client):
    first = note(client, content="第一版独有金色礼盒包装。")
    assert first["revision"] == 1 and first["can_edit"] and first["can_archive"]
    second = client.patch(BASE + "/" + first["id"], json={
        "expected_revision": 1, "title": "新版运输规范", "content": "第二版改用蓝色内衬。", "category": "物流",
    })
    second.raise_for_status()
    assert second.json()["revision"] == 2
    assert version(client, first["id"], 1)["content"] == first["content"]
    historical = version(client, first["id"], 1)
    assert {key: historical[key] for key in ("title", "content", "category")} == {
        key: first[key] for key in ("title", "content", "category")}
    assert version(client, first["id"], 2)["content"] == "第二版改用蓝色内衬。"
    assert knowledge.retrieve("金色礼盒")["items"] == []
    assert knowledge.retrieve("蓝色内衬")["items"][0]["document_id"] == first["id"]
    history = versions(client, first["id"], limit=1, offset=0)
    next_page = versions(client, first["id"], limit=1, offset=1)
    assert history["total"] == next_page["total"] == 2
    assert history["limit"] == 1 and history["offset"] == 0
    assert {row["revision"] for page in (history, next_page) for row in page["items"]} == {1, 2}
    assert all("content" not in row for page in (history, next_page) for row in page["items"])
    assert client.get(f"{BASE}/{first['id']}/versions/999").status_code == 404


def test_archive_restore_create_versions_and_repeated_noops_do_not(client):
    first = note(client)
    response = client.patch(BASE + "/" + first["id"], json={
        "expected_revision": 1, "title": first["title"], "content": first["content"], "category": first["category"],
    })
    response.raise_for_status()
    assert response.json()["revision"] == 1
    assert versions(client, first["id"])["total"] == 1
    archive(client, first["id"], expected_revision=1).raise_for_status()
    stopped = current(client, first["id"])
    assert stopped["deleted"] is True and stopped["revision"] == 2
    archive(client, first["id"], expected_revision=2).raise_for_status()
    assert versions(client, first["id"])["total"] == 2
    assert knowledge.retrieve("红色套装")["items"] == []
    assert version(client, first["id"], 2)["deleted"] is True
    response = client.post(BASE + "/" + first["id"] + "/restore", json={"expected_revision": 2})
    response.raise_for_status()
    assert response.json()["revision"] == 3 and response.json()["deleted"] is False
    client.post(BASE + "/" + first["id"] + "/restore", json={"expected_revision": 3}).raise_for_status()
    assert versions(client, first["id"])["total"] == 3
    assert knowledge.retrieve("红色套装")["items"][0]["document_id"] == first["id"]


@pytest.mark.parametrize("bad", [True, False, 0, -1, "1", 1.0, None, [], {}])
def test_revision_must_be_a_positive_actual_integer_and_invalid_update_is_atomic(client, bad):
    first = note(client)
    before = storage_snapshot()
    response = client.patch(BASE + "/" + first["id"], json={"content": "不应保存", "expected_revision": bad})
    assert response.status_code == 400
    assert storage_snapshot() == before


@pytest.mark.parametrize("action", ["edit", "archive", "restore"])
def test_stale_revision_cannot_overwrite_or_change_live_state(client, action):
    first = note(client)
    client.patch(BASE + "/" + first["id"], json={"content": "当前新版本。", "expected_revision": 1}).raise_for_status()
    if action == "restore":
        archive(client, first["id"], expected_revision=2).raise_for_status()
    before = storage_snapshot()
    if action == "edit":
        response = client.patch(BASE + "/" + first["id"], json={"content": "旧用户写入", "expected_revision": 1})
    elif action == "archive":
        response = archive(client, first["id"], expected_revision=1)
    else:
        response = client.post(BASE + "/" + first["id"] + "/restore", json={"expected_revision": 1})
    assert response.status_code == 409
    assert storage_snapshot() == before


def test_history_and_live_index_roll_back_together_if_audit_fails(client, monkeypatch):
    first = note(client)
    before = storage_snapshot()

    def fail(*args, **kwargs):
        raise RuntimeError("test-only revision audit failure")

    monkeypatch.setattr(backend, "_audit", fail)
    with pytest.raises(RuntimeError, match="revision audit failure"):
        client.patch(BASE + "/" + first["id"], json={"expected_revision": 1, "content": "不应保留新段落。"})
    assert storage_snapshot() == before
    assert current(client, first["id"])["revision"] == 1
    assert knowledge.retrieve("不应保留新段落")["items"] == []


def test_legacy_documents_gain_first_snapshot_without_changing_original_text_or_dates(client):
    identity = uuid.uuid4().hex
    content = "历史知识原文\n第二行中文与 001-SKU 保持原样。"
    with backend._connection(write=True) as conn:
        user = conn.execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()
        conn.execute("""CREATE TABLE knowledge_documents (
            id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',content TEXT NOT NULL,
            source_name TEXT NOT NULL DEFAULT '',truncated INTEGER NOT NULL DEFAULT 0,characters INTEGER NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(id),owner TEXT NOT NULL,created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""INSERT INTO knowledge_documents(id,title,category,content,source_name,truncated,characters,created_by,owner,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (identity, "历史规范", "采购", content, "历史文件.txt", 1,
            len(content), user["id"], user["name"], "2020-01-02T03:04:05+00:00", "2021-02-03T04:05:06+00:00"))
        before = dict(conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (identity,)).fetchone())
    knowledge.init_storage()
    knowledge._initialized = None
    knowledge.init_storage()
    with backend._connection() as conn:
        after = dict(conn.execute("SELECT * FROM knowledge_documents WHERE id=?", (identity,)).fetchone())
    assert {key: after[key] for key in before} == before
    assert current(client, identity)["revision"] == 1
    assert version(client, identity, 1)["content"] == content
    assert versions(client, identity)["total"] == 1
    assert knowledge.retrieve("历史知识原文")["items"][0]["document_id"] == identity


def test_default_admin_can_edit_but_archive_is_a_separate_owner_grant(client):
    first = note(client)
    admin = member(client)
    login(client, admin["name"])
    read = current(client, first["id"])
    assert read["can_edit"] and read["can_archive"] is False
    stats = client.get("/api/knowledge/stats").json()
    assert stats["can_create"] is True and stats["can_archive"] is False
    assert note(client, title="管理员的新知识")["revision"] == 1
    client.patch(BASE + "/" + first["id"], json={"content": "管理员复核后的内容。"}).raise_for_status()
    assert archive(client, first["id"]).status_code == 403
    assert client.post(BASE + "/" + first["id"] + "/restore").status_code == 403
    assert versions(client, first["id"])["total"] == 2


def test_archive_permission_can_be_granted_without_edit_or_create(client):
    first = note(client)
    admin = member(client, overrides={"knowledge.create": False, "knowledge.edit_own": False,
                                      "knowledge.edit_all": False, "knowledge.archive": True})
    login(client, admin["name"])
    read = current(client, first["id"])
    assert read["can_edit"] is False and read["can_archive"] is True
    assert client.post(BASE, json={"title": "无权新增", "content": "内容"}).status_code == 403
    assert client.patch(BASE + "/" + first["id"], json={"content": "无权编辑"}).status_code == 403
    archive(client, first["id"]).raise_for_status()
    assert versions(client, first["id"])["total"] == 2
    assert version(client, first["id"], 1)["content"] == first["content"]
    client.post(BASE + "/" + first["id"] + "/restore").raise_for_status()


def test_admin_edit_own_never_grants_edit_of_another_authors_document(client):
    other = note(client)
    admin = member(client, overrides={"knowledge.edit_all": False, "knowledge.edit_own": True})
    login(client, admin["name"])
    mine = note(client)
    assert mine["can_edit"] and current(client, other["id"])["can_edit"] is False
    client.patch(BASE + "/" + mine["id"], json={"content": "本人更新"}).raise_for_status()
    assert client.patch(BASE + "/" + other["id"], json={"content": "他人更新"}).status_code == 403
    assert client.get(BASE + "/" + other["id"] + "/versions").status_code == 403


def test_operator_remains_read_only_despite_all_knowledge_write_overrides_and_authorship(client):
    first = note(client)
    operator = member(client, name="知识员工", role="operator")
    # A formerly authored entry must not bypass the current enterprise role gate.
    with backend._connection(write=True) as conn:
        # Simulate legacy or erroneous overrides that the current team API also
        # refuses to grant. The knowledge API still needs its own role boundary.
        conn.executemany("INSERT INTO permission_overrides(user_id,permission_key,allowed) VALUES(?,?,1)", [
            (operator["id"], key) for key in ("knowledge.create", "knowledge.edit_own", "knowledge.edit_all", "knowledge.archive")])
        conn.execute("UPDATE knowledge_documents SET created_by=?,owner=? WHERE id=?", (operator["id"], operator["name"], first["id"]))
    login(client, operator["name"])
    read = current(client, first["id"])
    assert read["can_edit"] is False and read["can_archive"] is False
    assert read["content"] == first["content"]
    stats = client.get("/api/knowledge/stats").json()
    assert stats["can_create"] is False and stats["can_archive"] is False
    before = storage_snapshot()
    assert client.post(BASE, json={"title": "员工无权", "content": "新增"}).status_code == 403
    assert client.post(BASE + "/import", files={"files": ("员工.txt", "无权导入".encode(), "text/plain")}).status_code == 403
    assert client.patch(BASE + "/" + first["id"], json={"content": "无权编辑"}).status_code == 403
    assert archive(client, first["id"]).status_code == 403
    assert client.post(BASE + "/" + first["id"] + "/restore").status_code == 403
    assert client.get(BASE + "/" + first["id"] + "/versions").status_code == 403
    assert client.get(BASE + "/" + first["id"] + "/versions/1").status_code == 403
    assert storage_snapshot() == before


def test_document_import_requires_create_capability_even_for_admin(client):
    admin = member(client, overrides={"knowledge.create": False})
    login(client, admin["name"])
    assert client.post(BASE + "/import", files={"files": ("规范.txt", "包装知识".encode(), "text/plain")}).status_code == 403
    assert client.post(BASE, json={"title": "规范", "content": "包装知识"}).status_code == 403


def test_explicit_links_are_deduplicated_in_retrieval_and_versioned_on_change(client):
    red, blue = product(client), product(client, "蓝色套装蜡烛")
    first = note(client, product_ids=[red["id"], blue["id"]])
    assert set(first["product_ids"]) == {red["id"], blue["id"]}
    assert {row["id"] for row in first["related_products"]} == {red["id"], blue["id"]}
    linked = knowledge.retrieve_for_products([red["id"], blue["id"]])
    assert len(linked) == 1 and linked[0]["document_id"] == first["id"]
    assert set(linked[0]["product_ids"]) == {red["id"], blue["id"]}
    assert set(knowledge.get_documents_by_ids([first["id"]])[0]["product_ids"]) == {red["id"], blue["id"]}
    assert set(knowledge.retrieve("红色套装")["items"][0]["product_ids"]) == {red["id"], blue["id"]}
    client.patch(BASE + "/" + first["id"], json={"title": "标题更新但保留关联", "expected_revision": 1}).raise_for_status()
    assert set(current(client, first["id"])["product_ids"]) == {red["id"], blue["id"]}
    changed = client.patch(BASE + "/" + first["id"], json={"product_ids": [blue["id"]], "expected_revision": 2})
    changed.raise_for_status()
    assert changed.json()["revision"] == 3
    assert changed.json()["product_ids"] == [blue["id"]]
    assert set(version(client, first["id"], 1)["product_ids"]) == {red["id"], blue["id"]}
    assert knowledge.retrieve_for_products([red["id"]]) == []
    assert knowledge.retrieve_for_products([blue["id"]])[0]["document_id"] == first["id"]
    response = client.patch(BASE + "/" + first["id"], json={"product_ids": [], "expected_revision": 3})
    response.raise_for_status()
    assert response.json()["product_ids"] == [] and response.json()["related_products"] == []
    assert knowledge.retrieve_for_products([blue["id"]]) == []


def test_soft_deleted_products_hide_related_cards_and_retrieval_then_restore_links(client):
    red = product(client)
    first = note(client, product_ids=[red["id"]])
    client.delete("/api/products/" + red["id"]).raise_for_status()
    read = current(client, first["id"])
    assert read["product_ids"] == [red["id"]]
    assert read["related_products"][0]["id"] == red["id"]
    assert read["related_products"][0]["deleted"] is True
    assert knowledge.retrieve_for_products([red["id"]]) == []
    client.patch(BASE + "/" + first["id"], json={
        "content": "普通编辑仍保留原来的回收站关联。", "product_ids": read["product_ids"],
    }).raise_for_status()
    assert current(client, first["id"])["product_ids"] == [red["id"]]
    assert client.post(BASE, json={"title": "不可新关联回收站商品", "content": "新知识", "product_ids": [red["id"]]}).status_code == 400
    operator = member(client, name="关联只读员工", role="operator")
    login(client, operator["name"])
    assert current(client, first["id"])["related_products"] == []
    assert current(client, first["id"])["product_ids"] == [red["id"]]
    login(client)
    # Restoring the same product identity brings back the preserved relation.
    client.post("/api/products/" + red["id"] + "/restore").raise_for_status()
    assert current(client, first["id"])["related_products"][0]["id"] == red["id"]
    assert knowledge.retrieve_for_products([red["id"]])[0]["document_id"] == first["id"]
    archive(client, first["id"]).raise_for_status()
    assert knowledge.retrieve_for_products([red["id"]]) == []
    assert knowledge.get_documents_by_ids([first["id"]]) == []


def test_product_purge_is_blocked_by_active_or_archived_links_but_history_keeps_unlinked_identity(client):
    red = product(client)
    first = note(client, product_ids=[red["id"]])
    with backend._connection() as conn:
        with pytest.raises(HTTPException) as blocked:
            knowledge.assert_product_purge_allowed(conn, red["id"])
        assert blocked.value.status_code == 409
    archive(client, first["id"]).raise_for_status()
    with backend._connection() as conn:
        with pytest.raises(HTTPException) as blocked:
            knowledge.assert_product_purge_allowed(conn, red["id"])
        assert blocked.value.status_code == 409
    client.post(BASE + "/" + first["id"] + "/restore").raise_for_status()
    client.patch(BASE + "/" + first["id"], json={"product_ids": []}).raise_for_status()
    with backend._connection() as conn:
        knowledge.assert_product_purge_allowed(conn, red["id"])
    historical = version(client, first["id"], 1)
    assert historical["product_ids"] == [red["id"]]
    assert historical["related_products"] == [{"id": red["id"], "code": red["code"], "name": red["name"]}]


@pytest.mark.parametrize("bad", [None, "0" * 32, ["not-a-uuid"], [12], ["0" * 32], ["0" * 32] * 31])
def test_invalid_or_missing_product_relations_are_atomic(client, bad):
    first = note(client)
    before = storage_snapshot()
    response = client.patch(BASE + "/" + first["id"], json={"product_ids": bad, "content": "不应保存", "expected_revision": 1})
    assert response.status_code == 400
    assert storage_snapshot() == before
    assert client.post(BASE, json={"title": "无效关联", "content": "不应新增", "product_ids": bad}).status_code == 400
    assert storage_snapshot() == before


def test_duplicate_and_soft_deleted_product_relations_are_rejected(client):
    red = product(client)
    first = note(client)
    before = storage_snapshot()
    response = client.patch(BASE + "/" + first["id"], json={"product_ids": [red["id"], red["id"]]})
    assert response.status_code == 400
    assert storage_snapshot() == before
    client.delete("/api/products/" + red["id"]).raise_for_status()
    response = client.patch(BASE + "/" + first["id"], json={"product_ids": [red["id"]]})
    assert response.status_code == 400


def test_stats_counts_active_sources_and_old_company_knowledge_never_expires_automatically(client):
    red = product(client)
    first = note(client, product_ids=[red["id"]], content="长期适用的企业包装制度。")
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE knowledge_documents SET created_at='2000-01-01',updated_at='2000-01-01' WHERE id=?", (first["id"],))
    stats = client.get("/api/knowledge/stats").json()
    assert stats["documents"] == 1 and stats["chunks"] >= 1
    assert stats["products"] == 1 and stats["total_sources"] == 2
    assert stats["can_create"] is True and stats["can_archive"] is True
    assert knowledge.retrieve("企业包装制度")["items"][0]["document_id"] == first["id"]
    assert current(client, first["id"])["content"] == first["content"]
    archive(client, first["id"]).raise_for_status()
    stats = client.get("/api/knowledge/stats").json()
    assert stats["documents"] == stats["chunks"] == 0
    assert stats["products"] == stats["total_sources"] == 1
