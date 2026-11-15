"""Merging product identities preserves explicit knowledge and revision history."""
import uuid
from contextlib import nullcontext

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import backend
import knowledge_store as knowledge


PASSWORD = "knowledge-merge-links-test-only"
OWNER = "关联合并老板"
BASE = "/api/knowledge/documents"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "knowledge-merge-links")
    backend._login_attempts.clear()
    monkeypatch.setattr(knowledge, "_initialized", None)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(knowledge.router)
    with TestClient(app) as client:
        response = client.post("/api/setup", json={"name": OWNER, "password": PASSWORD})
        response.raise_for_status()
        yield client, response.json()["user"]


def product(name="关联商品"):
    identity = uuid.uuid4().hex
    with backend._connection(write=True) as conn:
        user = conn.execute("SELECT id,name FROM users ORDER BY created_at,id LIMIT 1").fetchone()
        conn.execute("""INSERT INTO products(id,code,name,owner,created_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""", (identity, "MERGE-" + identity[:10], name, user["name"], user["id"], backend.now(), backend.now()))
    return identity


def note(client, product_ids, **fields):
    response = client.post(BASE, json={"title": "企业规范", "category": "包装", "content": "正文永远保留，合并只调整商品关联。",
                                       "product_ids": product_ids, **fields})
    response.raise_for_status()
    return response.json()


def document(client, identity):
    response = client.get(BASE + "/" + identity)
    response.raise_for_status()
    return response.json()


def version(client, identity, revision):
    response = client.get(f"{BASE}/{identity}/versions/{revision}")
    response.raise_for_status()
    return response.json()


def snapshot(conn=None):
    with (nullcontext(conn) if conn is not None else backend._connection()) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'knowledge_%' OR name='products' OR name='activity')")]
        return {table: sorted([tuple(row) for row in connection.execute('SELECT * FROM "' + table + '"')], key=repr)
                for table in tables}


def remap(setup, source_ids, target_id, user=None):
    with backend._connection(write=True) as conn:
        return knowledge.remap_product_links(conn, source_ids, target_id, user or setup[1])


def undo(setup, changes, user=None):
    with backend._connection(write=True) as conn:
        return knowledge.undo_product_link_remap(conn, changes, user or setup[1])


def admin(client, *, edit_all=True):
    response = client.post("/api/team", json={"name": "关联管理员", "password": PASSWORD, "role": "admin"})
    response.raise_for_status()
    actor = response.json()
    if not edit_all:
        response = client.patch("/api/team/" + actor["id"], json={
            "expected_revision": actor["revision"], "permissions": {"knowledge.edit_all": False, "knowledge.edit_own": True},
        })
        response.raise_for_status()
        actor = response.json()
    return actor


def test_merge_deduplicates_multiple_source_links_in_order_and_preserves_every_document(setup):
    client, _ = setup
    source_a, source_b, target, unrelated = [product(name) for name in ("旧商品甲", "旧商品乙", "保留商品", "其他商品")]
    affected = note(client, [source_a, unrelated, target, source_b], title="在用规范")
    inactive = note(client, [unrelated, source_b, source_a], title="停用规范")
    unchanged = note(client, [target, unrelated], title="无关规范")
    client.delete(BASE + "/" + inactive["id"]).raise_for_status()
    before = {row["id"]: document(client, row["id"]) for row in (affected, inactive, unchanged)}
    with backend._connection() as conn:
        product_rows = [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")]
    result = remap(setup, [source_a, source_b], target)
    changed = {entry["document_id"]: entry for entry in result["documents"]}
    assert set(changed) == {affected["id"], inactive["id"]}
    assert changed[affected["id"]] == {"document_id": affected["id"], "before_product_ids": [source_a, unrelated, target, source_b],
                                       "after_product_ids": [target, unrelated], "after_revision": 2}
    assert changed[inactive["id"]] == {"document_id": inactive["id"], "before_product_ids": [unrelated, source_b, source_a],
                                       "after_product_ids": [unrelated, target], "after_revision": 3}
    for identity, old in before.items():
        now = document(client, identity)
        for key in ("title", "content", "category", "source_name", "truncated", "created_at", "owner", "deleted"):
            assert now[key] == old[key]
        if identity in changed:
            assert now["revision"] == changed[identity]["after_revision"]
            assert now["product_ids"] == changed[identity]["after_product_ids"]
            historical = version(client, identity, old["revision"])
            assert historical["product_ids"] == old["product_ids"]
            assert historical["content"] == old["content"]
            assert version(client, identity, now["revision"])["action"] == "product_merge"
        else:
            assert now == old
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0] == 3
        assert [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")] == product_rows
        assert conn.execute("SELECT COUNT(*) FROM knowledge_document_versions WHERE action='product_merge'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action LIKE 'knowledge_%'").fetchone()[0] >= 6


def test_admin_with_edit_all_can_merge_and_undo_active_and_archived_document_links(setup):
    client, _ = setup
    source, target = product(), product()
    first = note(client, [source])
    inactive = note(client, [source])
    client.delete(BASE + "/" + inactive["id"]).raise_for_status()
    actor = admin(client)
    changes = remap(setup, [source], target, actor)
    assert len(changes["documents"]) == 2
    undone = undo(setup, changes, actor)
    assert {entry["document_id"] for entry in undone["documents"]} == {first["id"], inactive["id"]}
    assert document(client, first["id"])["product_ids"] == [source]
    assert document(client, inactive["id"])["product_ids"] == [source]
    assert document(client, inactive["id"])["deleted"] is True


def test_merge_validates_permission_for_the_entire_batch_before_any_link_or_revision_change(setup):
    client, _ = setup
    source, target = product(), product()
    documents = [note(client, [source], title="规范甲"), note(client, [source], title="规范乙")]
    actor = admin(client, edit_all=False)
    allowed = min(documents, key=lambda row: row["id"])
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE knowledge_documents SET created_by=?,owner=? WHERE id=?", (actor["id"], actor["name"], allowed["id"]))
        before = snapshot(conn)
        with pytest.raises(HTTPException) as forbidden:
            knowledge.remap_product_links(conn, [source], target, actor)
        assert forbidden.value.status_code == 403
        # Catch inside the caller transaction: an eventual outer rollback must
        # not conceal an implementation that changed an earlier permitted row.
        assert snapshot(conn) == before


def test_undo_validates_permission_for_the_entire_batch_before_changes(setup):
    client, _ = setup
    source, target = product(), product()
    documents = [note(client, [source]), note(client, [source])]
    actor = admin(client, edit_all=False)
    allowed = min(documents, key=lambda row: row["id"])
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE knowledge_documents SET created_by=?,owner=? WHERE id=?", (actor["id"], actor["name"], allowed["id"]))
    changes = remap(setup, [source], target)
    with backend._connection(write=True) as conn:
        before = snapshot(conn)
        with pytest.raises(HTTPException) as forbidden:
            knowledge.undo_product_link_remap(conn, changes, actor)
        assert forbidden.value.status_code == 403
        assert snapshot(conn) == before


def test_undo_restores_soft_deleted_source_ids_and_appends_history_instead_of_removing_it(setup):
    client, _ = setup
    source_a, source_b, target, other = [product() for _ in range(4)]
    first = note(client, [source_b, other, source_a])
    changes = remap(setup, [source_a, source_b], target)
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE products SET deleted=1 WHERE id IN (?,?)", (source_a, source_b))
    result = undo(setup, changes)
    assert len(result["documents"]) == 1
    now = document(client, first["id"])
    assert now["product_ids"] == [source_b, other, source_a] and now["revision"] == 3
    assert now["content"] == first["content"]
    for revision, links in ((1, [source_b, other, source_a]), (2, [target, other]), (3, [source_b, other, source_a])):
        historical = version(client, first["id"], revision)
        assert historical["product_ids"] == links
        assert historical["content"] == first["content"]
    assert version(client, first["id"], 3)["action"] == "product_merge_undo"


@pytest.mark.parametrize("conflict", ["revision", "links"])
def test_undo_detects_all_document_drift_before_restoring_any_other_document(setup, conflict):
    client, _ = setup
    source, target, other = product(), product(), product()
    docs = [note(client, [source]), note(client, [source])]
    changes = remap(setup, [source], target)
    drifted = max(docs, key=lambda row: row["id"])
    with backend._connection(write=True) as conn:
        if conflict == "revision":
            conn.execute("UPDATE knowledge_documents SET revision=revision+1 WHERE id=?", (drifted["id"],))
        else:
            conn.execute("INSERT INTO knowledge_document_products(document_id,product_id,position) VALUES(?,?,1)", (drifted["id"], other))
        before = snapshot(conn)
        with pytest.raises(HTTPException) as changed:
            knowledge.undo_product_link_remap(conn, changes, setup[1])
        assert changed.value.status_code == 409
        assert snapshot(conn) == before


def test_undo_preflight_is_read_only_for_valid_and_conflicting_changes(setup):
    client, _ = setup
    source, target = product(), product()
    first = note(client, [source])
    changes = remap(setup, [source], target)
    with backend._connection(write=True) as conn:
        before = snapshot(conn)
        assert knowledge.assert_product_link_undo_allowed(conn, changes, setup[1]) is None
        assert snapshot(conn) == before
        conn.execute("UPDATE knowledge_documents SET revision=revision+1 WHERE id=?", (first["id"],))
        drifted = snapshot(conn)
        with pytest.raises(HTTPException) as changed:
            knowledge.assert_product_link_undo_allowed(conn, changes, setup[1])
        assert changed.value.status_code == 409
        assert snapshot(conn) == drifted


@pytest.mark.parametrize("corruption", ["duplicate", "missing_revision", "boolean_revision"])
def test_undo_rejects_damaged_or_duplicate_change_records_without_modifying_storage(setup, corruption):
    client, _ = setup
    source, target = product(), product()
    note(client, [source])
    changes = remap(setup, [source], target)
    entry = changes["documents"][0]
    if corruption == "duplicate":
        damaged = {"documents": [entry, dict(entry)]}
    elif corruption == "missing_revision":
        damaged = {"documents": [{key: value for key, value in entry.items() if key != "after_revision"}]}
    else:
        damaged = {"documents": [{**entry, "after_revision": True}]}
    with backend._connection(write=True) as conn:
        before = snapshot(conn)
        with pytest.raises(HTTPException) as invalid:
            knowledge.undo_product_link_remap(conn, damaged, setup[1])
        assert invalid.value.status_code == 409
        assert snapshot(conn) == before


@pytest.mark.parametrize("operation", ["merge", "undo"])
def test_audit_failure_rolls_back_the_callers_entire_transaction(setup, monkeypatch, operation):
    client, _ = setup
    source, target = product(), product()
    note(client, [source])
    note(client, [source])
    changes = remap(setup, [source], target) if operation == "undo" else None
    before = snapshot()
    original_audit = backend._audit
    calls = []

    def fail_second_audit(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("test-only second link audit failure")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(backend, "_audit", fail_second_audit)
    with pytest.raises(RuntimeError, match="second link audit failure"):
        if operation == "merge":
            remap(setup, [source], target)
        else:
            undo(setup, changes)
    assert len(calls) == 2
    assert snapshot() == before


def test_merge_with_no_affected_links_does_not_write_or_add_versions(setup):
    client, _ = setup
    source, target = product(), product()
    note(client, [target])
    before = snapshot()
    assert remap(setup, [source], target) == {"documents": []}
    assert undo(setup, {"documents": []}) == {"documents": []}
    assert undo(setup, {}) == {"documents": []}  # Older merge journals default to {}.
    with backend._connection(write=True) as conn:
        assert knowledge.assert_product_link_undo_allowed(conn, {}, setup[1]) is None
    assert snapshot() == before


def test_helpers_are_compatible_with_pre_knowledge_databases_without_creating_tables(setup):
    source, target = product(), product()
    before = snapshot()
    assert not any(name.startswith("knowledge_") for name in before)
    assert remap(setup, [source], target) == {"documents": []}
    assert undo(setup, {"documents": []}) == {"documents": []}
    assert snapshot() == before
