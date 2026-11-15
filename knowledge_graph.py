"""A bounded, local map of real company sources and recorded relationships.

This is a navigational view, never an LLM-generated graph or a memory system.
It neither copies source content nor assigns age, decay, expiry or importance.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Query

import backend
import knowledge_store

router = APIRouter(prefix="/api/knowledge", dependencies=[Depends(backend.require_user)])


def _category_id(kind: str, category: str) -> str:
    return "category:" + kind + ":" + hashlib.sha256(category.encode("utf-8")).hexdigest()[:20]


def _search_where(kind: str, query: str, match: str) -> tuple[str, list]:
    alias = "p" if kind == "product" else "d"
    conditions, arguments = [f"{alias}.deleted=0"], []
    if query:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        fields = ("name", "code", "category") if kind == "product" else ("title", "category", "source_name")
        found = [f"{alias}.{field} LIKE ? ESCAPE '\\'" for field in fields]
        arguments.extend([pattern] * len(fields))
        if match:
            index = "knowledge_product_search" if kind == "product" else "knowledge_chunk_search"
            key = "product_id" if kind == "product" else "document_id"
            found.append(f"{alias}.id IN (SELECT {key} FROM {index} WHERE {index} MATCH ?)")
            arguments.append(match)
        conditions.append("(" + " OR ".join(found) + ")")
    return " AND ".join(conditions), arguments


@router.get("/graph")
def graph(q: str = Query("", max_length=200), kind: str = Query("all", pattern="^(all|product|knowledge)$"),
          limit: int = Query(240, ge=30, le=600)):
    """All signed-in members can browse company-shared, currently active sources.

    Editing capabilities are checked by the source APIs, independently of this
    read-only map. Disabled documents and recycled products never enter it.
    Every node cap is reported explicitly; source records remain untouched.
    """
    query = q.strip()
    knowledge_store.init_storage()
    if query:
        knowledge_store._sync_products()
    match = knowledge_store._match(query)
    nodes = [{"id": "company", "kind": "root", "label": "企业知识库"}]
    edges = []
    source_budget = max(1, (limit - 3) * 2 // 3)
    products, documents = [], []
    with backend._connection() as conn:
        totals = {
            "products": conn.execute("SELECT COUNT(*) FROM products WHERE deleted=0").fetchone()[0],
            "documents": conn.execute("SELECT COUNT(*) FROM knowledge_documents WHERE deleted=0").fetchone()[0],
            "skus": conn.execute("SELECT COUNT(*) FROM product_listings l JOIN products p ON p.id=l.product_id WHERE p.deleted=0").fetchone()[0],
        }
        totals["sources"] = totals["products"] + totals["documents"]
        matched = {"products": 0, "documents": 0, "skus": 0}
        for source_kind, table, alias, order in (("product", "products", "p", "p.code,p.id"),
                                                  ("knowledge", "knowledge_documents", "d", "d.title,d.id")):
            if kind not in {"all", source_kind}:
                continue
            group_id = "group:" + source_kind
            nodes.append({"id": group_id, "kind": "group", "label": "公司商品" if source_kind == "product" else "知识文档"})
            edges.append({"source": "company", "target": group_id, "relation": "contains"})
            where, args = _search_where(source_kind, query, match)
            key = "products" if source_kind == "product" else "documents"
            matched[key] = conn.execute(f"SELECT COUNT(*) FROM {table} {alias} WHERE {where}", args).fetchone()[0]
            fields = "p.id,p.name,p.code,p.category,p.updated_at" if source_kind == "product" else "d.id,d.title,d.category,d.updated_at,d.revision"
            priority_args = []
            if query:
                pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                if source_kind == "product":
                    # FTS includes common SKU tokens. Exact identities must win
                    # before applying the source cap, not just within its page.
                    order = f"""CASE WHEN p.code=? COLLATE NOCASE OR EXISTS(
                        SELECT 1 FROM product_listings exact WHERE exact.product_id=p.id
                        AND (exact.product_code=? COLLATE NOCASE OR exact.product_number=? COLLATE NOCASE)) THEN 0
                        WHEN p.name=? COLLATE NOCASE THEN 1 WHEN p.name LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END,{order}"""
                    priority_args = [query, query, query, query, pattern]
                else:
                    order = f"CASE WHEN d.title=? COLLATE NOCASE THEN 0 WHEN d.title LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,{order}"
                    priority_args = [query, pattern]
            rows = conn.execute(f"SELECT {fields} FROM {table} {alias} WHERE {where} ORDER BY {order} LIMIT ?", [*args, *priority_args, source_budget]).fetchall()
            (products if source_kind == "product" else documents).extend(dict(row) for row in rows)
            if source_kind == "product":
                matched["skus"] = conn.execute(f"SELECT COUNT(*) FROM product_listings l JOIN products p ON p.id=l.product_id WHERE {where}", args).fetchone()[0]

        # Interleave both source types, so a large catalog cannot hide documents.
        selected = []
        for index in range(max(len(products), len(documents))):
            for source_kind, rows in (("product", products), ("knowledge", documents)):
                if index < len(rows) and len(selected) < source_budget:
                    selected.append((source_kind, rows[index]))
        category_slots = min(40, max(0, (limit - len(nodes) - len(selected)) // 2))
        categories = set()
        selected_products, selected_documents = [], []
        for source_kind, row in selected:
            identity = source_kind + ":" + row["id"]
            node = {"id": identity, "kind": source_kind, "label": row.get("name", row.get("title", "")),
                    "source_id": row["id"], "category": row["category"], "updated_at": row["updated_at"]}
            if source_kind == "product":
                node["code"] = row["code"]
                selected_products.append(row["id"])
            else:
                node["revision"] = row["revision"]
                selected_documents.append(row["id"])
            nodes.append(node)
            category_id = _category_id(source_kind, row["category"])
            if category_id not in categories and len(categories) < category_slots:
                categories.add(category_id)
                nodes.append({"id": category_id, "kind": "category", "label": row["category"] or "未分类",
                              "source_kind": source_kind, "category": row["category"]})
                edges.append({"source": "group:" + source_kind, "target": category_id, "relation": "contains"})
            if category_id in categories:
                edges.append({"source": identity, "target": category_id, "relation": "classified_as"})
            else:
                edges.append({"source": "group:" + source_kind, "target": identity, "relation": "contains"})

        sku_count = 0
        if selected_products:
            marks = ",".join("?" * len(selected_products))
            tracking = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_tracking'").fetchone()
            tracking_fields = ",COALESCE(t.variant_hint,'') AS variant_hint,COALESCE(t.region,'') AS region" if tracking else ""
            tracking_join = " LEFT JOIN price_tracking t ON t.listing_id=l.id" if tracking else ""
            room = max(0, limit - len(nodes))
            sku_order, priority_args = "l.product_id,l.position,l.id", []
            if query:
                # A 100-SKU product can exceed the picture's node budget. Keep
                # the requested SKU visible, with other variants as context.
                pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                sku_order = f"""CASE WHEN l.product_code=? COLLATE NOCASE THEN 0
                    WHEN l.product_number=? COLLATE NOCASE THEN 1
                    WHEN l.product_code LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END,{sku_order}"""
                priority_args = [query, query, pattern]
            rows = conn.execute(f"""SELECT l.id,l.product_id,l.platform,l.store,l.product_code,l.product_number,l.status{tracking_fields}
                FROM product_listings l{tracking_join} WHERE l.product_id IN ({marks})
                ORDER BY {sku_order} LIMIT ?""", [*selected_products, *priority_args, room]).fetchall()
            for row in rows:
                entry = dict(row)
                identity = "sku:" + entry.pop("id")
                nodes.append({"id": identity, "source_id": identity[4:], "kind": "sku",
                              "label": entry["product_code"] or entry.get("variant_hint") or "未填写 SKU",
                              "code": entry["product_code"], **entry})
                edges.append({"source": identity, "target": "product:" + row["product_id"], "relation": "variant_of"})
                sku_count += 1
        if selected_products and selected_documents:
            product_marks = ",".join("?" * len(selected_products))
            document_marks = ",".join("?" * len(selected_documents))
            for row in conn.execute(f"SELECT document_id,product_id FROM knowledge_document_products WHERE product_id IN ({product_marks}) AND document_id IN ({document_marks}) ORDER BY document_id,product_id", [*selected_products, *selected_documents]):
                edges.append({"source": "product:" + row["product_id"], "target": "knowledge:" + row["document_id"], "relation": "documented_by"})
    shown = {"products": len(selected_products), "documents": len(selected_documents), "skus": sku_count}
    return {"nodes": nodes, "edges": edges, "totals": totals, "matched": matched, "shown": shown,
            "truncated": any(shown[key] < matched[key] for key in shown), "query": query,
            "policy": {"persistent": True, "automatic_decay": False, "automatic_cleanup": False,
                       "relationships": "商品归属、分类、SKU 与人工关联；不表示模型推理或推测的语义关系。"}}
