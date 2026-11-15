"""Local knowledge graph describes real active sources without changing them."""
import json
import socket
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend
import knowledge_graph
import knowledge_store
import product_management


PASSWORD = "knowledge-graph-test-only"
OWNER = "知识图谱老板"
GRAPH = "/api/knowledge/graph"
DOCUMENTS = "/api/knowledge/documents"


def test_search_prioritizes_requested_sku_even_when_product_has_many_variants(client):
    item = product()
    for index in range(70):
        sku(item["id"], code=f"OTHER-{index:03d}", position=index)
    target = sku(item["id"], code="NEEDLE-LAST-071", position=71)
    result = graph(client, q="NEEDLE-LAST-071", limit=30)
    assert target["id"] in source_ids(result, "sku")
    assert result["truncated"] is True


def test_exact_sku_parent_is_not_hidden_by_other_products_with_common_sku_tokens(client):
    for index in range(35):
        other = product(name=f"其他蜡烛{index}", code=f"A-{index:03d}")
        sku(other["id"], code=f"SKU-RED-{index:03d}")
    target = product(name="目标蜡烛", code="Z-TARGET")
    target_sku = sku(target["id"], code="SKU-RED-099")
    result = graph(client, q="SKU-RED-099", limit=30)
    assert target["id"] in source_ids(result, "product")
    assert target_sku["id"] in source_ids(result, "sku")


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "graph-library")
    backend._login_attempts.clear()
    monkeypatch.setattr(knowledge_store, "_initialized", None)
    def forbid_network(*args, **kwargs):
        pytest.fail("The local graph must not access external sources")
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    app = FastAPI()
    app.include_router(product_management.router)
    app.include_router(backend.router)
    app.include_router(knowledge_store.router)
    app.include_router(knowledge_graph.router)
    with TestClient(app) as instance:
        response = instance.post("/api/setup", json={"name": OWNER, "password": PASSWORD})
        response.raise_for_status()
        assert response.json()["user"]["role"] == "owner"
        yield instance


def product(name="红色圣诞树蜡烛", category="模具蜡", **fields):
    identity = uuid.uuid4().hex
    with backend._connection(write=True) as conn:
        user = conn.execute("SELECT id,name FROM users ORDER BY created_at,id LIMIT 1").fetchone()
        row = {"id": identity, "code": "GRAPH-" + identity[:10], "name": name, "category": category,
               "owner": user["name"], "created_by": user["id"], "created_at": backend.now(),
               "updated_at": backend.now(), **fields}
        conn.execute("INSERT INTO products(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    return row


def sku(product_id, code="SKU-RED-002", **fields):
    identity = uuid.uuid4().hex
    row = {"id": identity, "product_id": product_id, "platform": "Temu", "store": "美国店",
           "product_code": code, "product_number": "SKC-SHARED", "status": "pending", **fields}
    with backend._connection(write=True) as conn:
        conn.execute("INSERT INTO product_listings(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    return row


def note(client, title="蜡烛包装规范", **fields):
    response = client.post(DOCUMENTS, json={"title": title, "content": "运输前测量底座与核对套装件数。",
                                           "category": "包装规范", **fields})
    response.raise_for_status()
    return response.json()


def graph(client, **params):
    response = client.get(GRAPH, params=params)
    response.raise_for_status()
    return response.json()


def source_ids(result, kind):
    return {node["source_id"] for node in result["nodes"] if node["kind"] == kind}


def assert_valid_shape(result, limit=240):
    identities = [node["id"] for node in result["nodes"]]
    assert len(identities) == len(set(identities)) and len(identities) <= limit
    assert {node["kind"] for node in result["nodes"]} <= {"root", "group", "category", "product", "knowledge", "sku"}
    assert len([node for node in result["nodes"] if node["kind"] == "root"]) == 1
    assert all(edge["source"] in identities and edge["target"] in identities for edge in result["edges"])
    assert {edge["relation"] for edge in result["edges"]} <= {"contains", "classified_as", "variant_of", "documented_by"}
    for kind, key in (("product", "products"), ("knowledge", "documents"), ("sku", "skus")):
        assert result["shown"][key] == len(source_ids(result, kind))
        assert 0 <= result["shown"][key] <= result["matched"][key] <= result["totals"][key]
    assert result["truncated"] is any(result["shown"][key] < result["matched"][key] for key in result["shown"])
    assert result["policy"]["persistent"] is True
    assert result["policy"]["automatic_decay"] is False
    assert result["policy"]["automatic_cleanup"] is False


def test_empty_library_returns_a_valid_root_and_honest_zero_counts(client):
    result = graph(client)
    assert_valid_shape(result)
    assert result["totals"] == {"products": 0, "documents": 0, "skus": 0, "sources": 0}
    assert result["shown"] == result["matched"] == {"products": 0, "documents": 0, "skus": 0}
    assert result["truncated"] is False


def test_real_sources_categories_variants_and_only_explicit_document_relations(client):
    candle = product()
    red = sku(candle["id"])
    linked = note(client, product_ids=[candle["id"]])
    unrelated = note(client, title="圣诞树蜡烛包装建议")  # Similar wording alone is not a relationship.
    result = graph(client)
    assert_valid_shape(result)
    assert source_ids(result, "product") == {candle["id"]}
    assert source_ids(result, "knowledge") == {linked["id"], unrelated["id"]}
    assert source_ids(result, "sku") == {red["id"]}
    assert result["totals"] == {"products": 1, "documents": 2, "skus": 1, "sources": 3}
    assert result["shown"] == result["matched"] == {"products": 1, "documents": 2, "skus": 1}
    assert {edge["target"] for edge in result["edges"] if edge["relation"] == "documented_by"} == {"knowledge:" + linked["id"]}
    assert {edge["source"] for edge in result["edges"] if edge["relation"] == "documented_by"} == {"product:" + candle["id"]}
    assert {node["label"] for node in result["nodes"] if node["kind"] == "category"} == {"模具蜡", "包装规范"}
    assert any(edge == {"source": "sku:" + red["id"], "target": "product:" + candle["id"], "relation": "variant_of"}
               for edge in result["edges"])


@pytest.mark.parametrize("kind", ["product", "knowledge"])
def test_kind_filter_keeps_global_totals_but_counts_only_requested_source_matches(client, kind):
    candle = product()
    sku(candle["id"])
    document = note(client, product_ids=[candle["id"]])
    result = graph(client, kind=kind)
    assert_valid_shape(result)
    assert result["totals"]["sources"] == 2
    if kind == "product":
        assert source_ids(result, "product") == {candle["id"]}
        assert source_ids(result, "knowledge") == set()
        assert result["matched"]["documents"] == 0
    else:
        assert source_ids(result, "knowledge") == {document["id"]}
        assert source_ids(result, "product") == source_ids(result, "sku") == set()
        assert result["matched"]["products"] == result["matched"]["skus"] == 0
    assert not any(edge["relation"] == "documented_by" for edge in result["edges"])


def test_search_uses_chinese_document_body_product_specs_and_sku_identifiers(client):
    candle = product(specs="紫色玛瑙底座")
    sku(candle["id"], "EXACT-SKU-BLUE-0099")
    other = product(name="普通纸箱", category="包装")
    document = note(client, content="特殊流程：先核实海蓝内衬的批次。")
    assert source_ids(graph(client, q="紫色玛瑙"), "product") == {candle["id"]}
    assert source_ids(graph(client, q="EXACT-SKU-BLUE-0099"), "product") == {candle["id"]}
    assert source_ids(graph(client, q="海蓝内衬"), "knowledge") == {document["id"]}
    assert other["id"] not in source_ids(graph(client, q="海蓝内衬"), "product")
    unmatched = graph(client, q="量子钻石切割机")
    assert_valid_shape(unmatched)
    assert unmatched["matched"] == {"products": 0, "documents": 0, "skus": 0}
    assert unmatched["totals"]["products"] == 2 and unmatched["totals"]["documents"] == 1


def test_search_treats_like_symbols_and_sql_text_as_data(client):
    percent = product(name="100%纯蜂蜡")
    underline = product(name="带_符号的样品")
    slash = product(name="带\\符号的样品")
    product(name="普通蜂蜡")
    for query, identity in (("%", percent["id"]), ("_", underline["id"]), ("\\", slash["id"])):
        result = graph(client, q=query)
        assert source_ids(result, "product") == {identity}
        assert result["matched"]["products"] == 1
    malicious = graph(client, q="' OR 1=1--")
    assert_valid_shape(malicious)
    assert source_ids(malicious, "product") == set()
    assert malicious["totals"]["products"] == 4
    assert graph(client)["totals"]["products"] == 4


def test_node_cap_counts_structural_nodes_and_reports_unshown_sources_and_skus(client):
    products = [product(name="图谱样品" + str(index), category="分类" + str(index)) for index in range(50)]
    for item in products:
        for index in range(3):
            sku(item["id"], "SKU-" + str(index))
    for index in range(6):
        note(client, title="企业知识" + str(index), category="知识分类" + str(index))
    for limit in (30, 60, 240, 600):
        result = graph(client, limit=limit)
        assert_valid_shape(result, limit)
        assert result["totals"] == {"products": 50, "documents": 6, "skus": 150, "sources": 56}
        assert result["matched"] == {"products": 50, "documents": 6, "skus": 150}
        assert result["shown"]["documents"] > 0  # Large product lists cannot hide every document.
        if limit == 30:
            assert result["truncated"] is True
        if limit == 600:
            assert result["shown"] == result["matched"] and result["truncated"] is False


def test_source_changes_soft_removal_and_restoration_are_visible_without_inferred_edges(client):
    candle = product()
    red = sku(candle["id"])
    document = note(client, product_ids=[candle["id"]])
    client.patch("/api/products/" + candle["id"], json={"name": "更新后的绿树蜡烛", "category": "节庆用品"}).raise_for_status()
    client.patch(DOCUMENTS + "/" + document["id"], json={"title": "更新的物流规范", "product_ids": []}).raise_for_status()
    result = graph(client)
    node = next(node for node in result["nodes"] if node["id"] == "product:" + candle["id"])
    assert node["label"] == "更新后的绿树蜡烛" and node["category"] == "节庆用品"
    doc_node = next(node for node in result["nodes"] if node["id"] == "knowledge:" + document["id"])
    assert doc_node["label"] == "更新的物流规范" and doc_node["revision"] == 2
    assert not any(edge["relation"] == "documented_by" for edge in result["edges"])
    client.delete("/api/products/" + candle["id"]).raise_for_status()
    result = graph(client)
    assert source_ids(result, "product") == source_ids(result, "sku") == set()
    assert result["totals"]["products"] == result["totals"]["skus"] == 0
    client.post("/api/products/" + candle["id"] + "/restore").raise_for_status()
    assert source_ids(graph(client), "sku") == {red["id"]}
    client.delete(DOCUMENTS + "/" + document["id"]).raise_for_status()
    assert source_ids(graph(client), "knowledge") == set()
    client.post(DOCUMENTS + "/" + document["id"] + "/restore").raise_for_status()
    assert source_ids(graph(client), "knowledge") == {document["id"]}


def test_graph_is_shared_read_only_and_excludes_content_prices_links_and_private_fields(client):
    candle = product(notes="PRODUCT-NOTES-PRIVATE-SENTINEL")
    sku(candle["id"], price="9876.5432", currency="USD", link="https://private.example.com/SECRET-LINK-SENTINEL")
    note(client, content="DOCUMENT-CONTENT-PRIVATE-SENTINEL")
    client.post("/api/team", json={"name": "图谱员工", "password": PASSWORD, "role": "operator"}).raise_for_status()
    client.post("/api/logout").raise_for_status()
    assert client.get(GRAPH).status_code == 401
    client.post("/api/login", json={"name": "图谱员工", "password": PASSWORD}).raise_for_status()
    result = graph(client)
    assert_valid_shape(result)
    text = json.dumps(result, ensure_ascii=False)
    for forbidden in ("PRODUCT-NOTES-PRIVATE-SENTINEL", "DOCUMENT-CONTENT-PRIVATE-SENTINEL", "9876.5432", "SECRET-LINK-SENTINEL"):
        assert forbidden not in text
    for node in result["nodes"]:
        assert not {"content", "notes", "price", "current_price", "original_price", "source_url", "link", "password_hash", "api_key"} & set(node)
    assert client.post(GRAPH, json={"nodes": []}).status_code == 405


def test_very_old_sources_remain_and_graph_reads_do_not_decay_or_rewrite_source_records(client):
    candle = product(created_at="2000-01-01", updated_at="2000-01-01")
    document = note(client)
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE knowledge_documents SET created_at='2000-01-01',updated_at='2000-01-01' WHERE id=?", (document["id"],))
        before_products = [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")]
        before_documents = [tuple(row) for row in conn.execute("SELECT * FROM knowledge_documents ORDER BY id")]
    for _ in range(3):
        result = graph(client, q="蜡烛")
        assert source_ids(result, "product") == {candle["id"]}
        assert source_ids(result, "knowledge") == {document["id"]}
    with backend._connection() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM products ORDER BY id")] == before_products
        assert [tuple(row) for row in conn.execute("SELECT * FROM knowledge_documents ORDER BY id")] == before_documents


@pytest.mark.parametrize("params", [{"limit": 29}, {"limit": 601}, {"limit": "many"}, {"kind": "inferred"}, {"q": "x" * 201}])
def test_graph_rejects_unbounded_or_invalid_request_parameters(client, params):
    assert client.get(GRAPH, params=params).status_code == 422
