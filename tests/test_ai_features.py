"""AI product tools use temporary stores and mocked providers; no paid requests."""
import base64
import copy
import io
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import ai_service
import backend


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    backend._login_attempts.clear()
    ai_service.init_config(tmp_path)
    monkeypatch.setattr(backend, "_extract_semantic", lambda content: None)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(ai_service.router)
    with TestClient(app) as test_client:
        assert test_client.post("/api/setup", json={"name": "测试管理员", "password": "test-password-2026"}).status_code == 200
        yield test_client


def picture(size=(160, 240), color="#bca040"):
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def uploads(raw=None, count=1):
    return [("images", (f"01_white_bg_main_{i}.png", raw or picture(), "image/png")) for i in range(count)]


def enable(client, vision=True, context=True, provider="custom", **extra):
    response = client.put("/api/ai/config", json={
        "providers": [{"id": provider, "model": "account-model", "base_url": "https://example.test/v1", "api_key": "test-key-only", "enabled": True, "vision": vision, **extra}],
        "active_provider": provider, "share_library_context": context})
    assert response.status_code == 200, response.text
    return response.json()


def mock_reply(monkeypatch, reply):
    seen = []

    async def post(self, url, **kwargs):
        seen.append((url, kwargs))
        text = reply() if callable(reply) else reply
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        payload = {"choices": [{"message": {"content": text}}], "content": [{"type": "text", "text": text}], "usage": {"total_tokens": 10}}
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return seen


def product(client, name="蜂窝装饰蜡烛"):
    response = client.post("/api/products", data={"metadata": json.dumps({"name": name, "specs": "黄色，2件装"})}, files=uploads())
    assert response.status_code == 200, response.text
    return response.json()


def candidate(item, index=0):
    image = item["images"][0]
    return {"product": item, "matched_image_id": image["id"], "matched_image_url": "/media/" + image["id"],
            "query_index": 0, "query_fingerprint": "query-sha", "previous_decision": {"decision": "uncertain"}, "reason": "本地候选", "grade": "semantic"}


def local_results(monkeypatch, matches):
    result = {"items": matches[:5], "additional_items": matches[5:], "total_searched": len(matches), "notice": "本地结果可能漏检。", "query_fingerprints": ["query-sha"], "score_is_probability": False}
    monkeypatch.setattr(backend, "_search_sync", lambda *args, **kwargs: copy.deepcopy(result))


def test_unconfigured_tools_do_not_call_provider(client, monkeypatch):
    seen = mock_reply(monkeypatch, {"title": "不应调用"})
    assert client.post("/api/ai/title", files=uploads()).status_code == 409
    assert client.post("/api/ai/deduplicate", files=uploads()).status_code == 409
    assert not seen
    assert client.get("/api/ai/config").json()["requests_today"] == 0


def test_vision_consent_defaults_off_and_resets_with_model(client):
    initial = client.get("/api/ai/config").json()
    assert all(not p["vision"] for p in initial["providers"])
    assert initial["text_only"]
    enabled = enable(client)
    assert not enabled["text_only"]
    unchanged = client.put("/api/ai/config", json={"providers": [{"id": "custom", "enabled": True}]}).json()
    assert next(p for p in unchanged["providers"] if p["id"] == "custom")["vision"]
    changed = client.put("/api/ai/config", json={"providers": [{"id": "custom", "model": "different-model"}]}).json()
    assert not next(p for p in changed["providers"] if p["id"] == "custom")["vision"]
    assert client.put("/api/ai/config", json={"providers": [{"id": "custom", "vision": "false"}]}).status_code == 400


@pytest.mark.parametrize("name", ["01_white_bg_main", "04_dimensions", "商品 1", "1a6b1b20-af9f-48fa-bd95-d7fbc990aabc", "image_123.png"])
def test_filename_alone_cannot_supply_product_facts(client, monkeypatch, name):
    enable(client, vision=False)
    seen = mock_reply(monkeypatch, {"title": "猜测标题"})
    response = client.post("/api/ai/title", data={"metadata": json.dumps({"name": name})}, files=uploads())
    assert response.status_code == 409
    assert "资料不足" in response.json()["detail"]
    assert not seen
    assert client.get("/api/ai/config").json()["requests_today"] == 0


def test_text_title_uses_manual_facts_without_sending_images(client, monkeypatch):
    enable(client, vision=False, context=False)
    seen = mock_reply(monkeypatch, {"title": "黄色蜂窝蜡烛"})
    response = client.post("/api/ai/title", data={"metadata": json.dumps({"name": "01_white_bg_main", "specs": "黄色蜂窝蜡烛，2支装"})}, files=uploads())
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "text"
    assert response.json()["images_shared"] == 0
    body = seen[0][1]["json"]
    assert isinstance(body["messages"][-1]["content"], str)
    assert "01_white_bg_main" not in json.dumps(body)
    assert "image_url" not in json.dumps(body)
    assert "未向你发送图片" in body["messages"][-1]["content"]


def test_arbitrary_uploaded_basename_is_not_used_as_text_evidence(client, monkeypatch):
    enable(client, vision=False)
    seen = mock_reply(monkeypatch, {"title": "猜测商品"})
    response = client.post("/api/ai/title", data={"metadata": json.dumps({"name": "BATCH_imported_unreviewed"})},
                           files=[("images", ("BATCH_imported_unreviewed.png", picture(), "image/png"))])
    assert response.status_code == 409
    assert not seen


def test_visual_title_resizes_upload_and_uses_inline_data(client, monkeypatch):
    enable(client, context=False)
    seen = mock_reply(monkeypatch, {"title": "黄色蜂窝蜡烛"})
    response = client.post("/api/ai/title", data={"metadata": json.dumps({"name": "04_dimensions"})}, files=uploads(picture((2200, 1600))))
    assert response.status_code == 200, response.text
    assert response.json()["images_shared"] == 1
    assert response.json()["source"] == "vision"
    content = seen[0][1]["json"]["messages"][-1]["content"]
    data_url = next(item["image_url"]["url"] for item in content if item["type"] == "image_url")
    assert data_url.startswith("data:image/jpeg;base64,")
    sent_image = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    assert max(sent_image.size) <= 1280
    assert not sent_image.getexif()
    assert "04_dimensions" not in json.dumps(content)


def test_anthropic_visual_content_uses_its_own_protocol(client, monkeypatch):
    enable(client, provider="anthropic")
    seen = mock_reply(monkeypatch, {"title": "装饰蜡烛"})
    response = client.post("/api/ai/title", files=uploads())
    assert response.status_code == 200, response.text
    assert seen[0][0].endswith("/messages")
    content = seen[0][1]["json"]["messages"][-1]["content"]
    image = next(item for item in content if item["type"] == "image")
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/jpeg"
    assert "image_url" not in json.dumps(content)


@pytest.mark.parametrize("reply", [{"title": ""}, {"title": "x" * 41}, {"title": []}, {"title": "名字\n另一行"}, "not-json"])
def test_bad_titles_are_rejected_without_modifying_library(client, monkeypatch, reply):
    enable(client)
    mock_reply(monkeypatch, reply)
    response = client.post("/api/ai/title", files=uploads())
    assert response.status_code == 502
    assert client.get("/api/stats").json()["total_products"] == 0


@pytest.mark.parametrize("vision,context,status", [(False, True, 409), (True, False, 403)])
def test_ai_lookup_enforces_image_and_summary_consent(client, monkeypatch, vision, context, status):
    enable(client, vision=vision, context=context)
    seen = mock_reply(monkeypatch, {})
    assert client.post("/api/ai/deduplicate", files=uploads()).status_code == status
    assert client.get("/api/ai/config").json()["requests_today"] == 0
    assert not seen


def test_no_candidates_does_not_spend_cloud_quota(client, monkeypatch):
    enable(client)
    local_results(monkeypatch, [])
    seen = mock_reply(monkeypatch, {})
    response = client.post("/api/ai/deduplicate", files=uploads())
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["reviewed_count"] == 0
    assert not seen
    assert client.get("/api/ai/config").json()["requests_today"] == 0


def test_ai_reviews_candidate_images_without_mutating_human_decisions(client, monkeypatch):
    enable(client)
    item = product(client)
    local_results(monkeypatch, [candidate(item)])
    seen = mock_reply(monkeypatch, {"summary": "外形一致，核对件数。", "reviews": [{"product_id": item["id"], "decision": "same", "reason": "蜂窝表面与轮廓一致。", "differences": ["实际尺寸待核对"]}]})
    response = client.post("/api/ai/deduplicate", files=uploads())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reviewed_count"] == 1
    assert result["images_shared"] == 2
    assert result["items"][0]["ai_review"]["decision"] == "same"
    assert result["items"][0]["previous_decision"]["decision"] == "uncertain"
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    content = seen[0][1]["json"]["messages"][-1]["content"]
    assert sum(block["type"] == "image_url" for block in content) == 2
    assert "成本" not in content[0]["text"]
    assert "最多 8" in result["notice"]


def test_candidate_limit_and_missing_reviews_are_explicitly_uncertain(client, monkeypatch):
    enable(client)
    matches = [candidate(product(client, name=f"蜡烛 {i}")) for i in range(10)]
    local_results(monkeypatch, matches)
    seen = mock_reply(monkeypatch, {"summary": "逐项核对。", "reviews": []})
    response = client.post("/api/ai/deduplicate", files=uploads())
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["items"]) == 3
    assert len(result["additional_items"]) == 5
    assert result["additional_count"] == 5
    assert result["has_more"]
    assert all(item["ai_review"]["decision"] == "uncertain" for item in result["items"] + result["additional_items"])
    assert result["reviewed_count"] == 0
    assert result["images_shared"] == 9
    content = seen[0][1]["json"]["messages"][-1]["content"]
    assert sum(block["type"] == "image_url" for block in content) == 9
    assert matches[8]["product"]["id"] not in json.dumps(content)


def test_human_same_confirmation_ranks_above_ai_disagreement(client, monkeypatch):
    enable(client)
    matches = [candidate(product(client, name=f"蜡烛 {i}")) for i in range(4)]
    confirmed = matches[-1]
    confirmed["previous_decision"] = {"decision": "same", "actor": "运营成员"}
    local_results(monkeypatch, matches)
    mock_reply(monkeypatch, {"reviews": [
        {"product_id": m["product"]["id"], "decision": "different" if m is confirmed else "same", "reason": "AI 的建议仍需复核。", "differences": []}
        for m in matches]})
    response = client.post("/api/ai/deduplicate", files=uploads())
    assert response.status_code == 200, response.text
    first = response.json()["items"][0]
    assert first["product"]["id"] == confirmed["product"]["id"]
    assert first["previous_decision"]["decision"] == "same"
    assert first["ai_review"]["decision"] == "different"


@pytest.mark.parametrize("review", [
    {"product_id": "invented-id", "decision": "same", "reason": "猜测"},
    {"decision": ["same"], "reason": "格式错误"},
    {"decision": "same", "reason": ""},
    {"decision": "same", "reason": "不合法差异", "differences": "字符串"},
])
def test_untrusted_model_reviews_are_validated(client, monkeypatch, review):
    enable(client)
    item = product(client)
    local_results(monkeypatch, [candidate(item)])
    mock_reply(monkeypatch, {"reviews": [{"product_id": item["id"], **review}]})
    assert client.post("/api/ai/deduplicate", files=uploads()).status_code == 502


def test_daily_quota_is_shared_by_chat_title_and_dedup(client, monkeypatch):
    enable(client)
    client.put("/api/ai/config", json={"daily_request_limit": 1})
    item = product(client)
    local_results(monkeypatch, [candidate(item)])
    seen = mock_reply(monkeypatch, {"title": "黄色蜡烛"})
    assert client.post("/api/ai/title", files=uploads()).status_code == 200
    assert client.post("/api/ai/title", files=uploads()).status_code == 429
    assert client.post("/api/ai/deduplicate", files=uploads()).status_code == 429
    assert client.post("/api/ai/chat", json={"message": "你好"}).status_code == 429
    assert len(seen) == 1


def test_unavailable_candidate_image_is_not_claimed_as_ai_review(client, monkeypatch):
    enable(client)
    item = product(client)
    match = candidate(item)
    match["matched_image_id"] = "not-present"
    local_results(monkeypatch, [match])
    seen = mock_reply(monkeypatch, {})
    response = client.post("/api/ai/deduplicate", files=uploads())
    assert response.status_code == 200
    assert response.json()["items"][0]["ai_review"]["decision"] == "uncertain"
    assert not seen
    assert client.get("/api/ai/config").json()["requests_today"] == 0


def test_member_can_use_tools_but_cannot_enable_image_sharing(client, monkeypatch):
    enable(client)
    assert client.post("/api/team", json={"name": "运营成员", "password": "member-password", "role": "operator"}).status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"name": "运营成员", "password": "member-password"}).status_code == 200
    assert client.put("/api/ai/config", json={"providers": [{"id": "custom", "vision": True}]}).status_code == 403
    mock_reply(monkeypatch, {"title": "黄色装饰蜡烛"})
    assert client.post("/api/ai/title", files=uploads()).status_code == 200
    config = client.get("/api/ai/config").json()
    assert all("base_url" not in p for p in config["providers"])
    client.post("/api/logout")
    assert client.post("/api/ai/title", files=uploads()).status_code == 401


def test_feature_upload_and_json_boundaries(client, monkeypatch):
    enable(client)
    seen = mock_reply(monkeypatch, {})
    assert client.post("/api/ai/title", data={"metadata": "[]"}, files=uploads()).status_code == 400
    assert client.post("/api/ai/title", files=uploads(count=4)).status_code == 400
    assert client.post("/api/ai/title", files=uploads(b"not-an-image")).status_code == 400
    assert client.post("/api/ai/deduplicate", files=uploads(count=6)).status_code == 400
    assert not seen
