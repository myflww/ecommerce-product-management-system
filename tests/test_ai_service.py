import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ai_service
import backend


@pytest.fixture
def client(tmp_path):
    backend.init_storage(tmp_path)
    ai_service.init_config(tmp_path)
    api = FastAPI()
    api.include_router(backend.router)
    api.include_router(ai_service.router)
    with TestClient(api) as client:
        assert client.post("/api/setup", json={"name": "测试管理员", "password": "test-password-2026"}).status_code == 200
        yield client


def enable_provider(client, **extra):
    return client.put("/api/ai/config", json={"providers": [{"id": "deepseek", "enabled": True, "api_key": "test-not-a-real-api-key-123456", **extra}], "active_provider": "deepseek"})


def test_unconfigured_never_calls_cloud(client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No network request may occur without configured credentials")
    monkeypatch.setattr(httpx.AsyncClient, "post", forbidden)
    response = client.post("/api/ai/chat", json={"message": "帮我查库"})
    assert response.status_code == 409
    assert client.get("/api/ai/config").json()["requests_today"] == 0


def test_keys_encrypted_and_not_returned(client, tmp_path):
    response = enable_provider(client)
    assert response.status_code == 200
    assert "test-not-a-real-api-key" not in response.text
    assert "test-not-a-real-api-key" not in (tmp_path / "ai-config.json").read_text(encoding="utf-8")
    provider = next(p for p in response.json()["providers"] if p["id"] == "deepseek")
    assert provider["configured"]
    assert enable_provider(client, api_key="").status_code == 200
    with ai_service._lock:
        secret = ai_service._load()["providers"]["deepseek"]["secret"]
    assert ai_service._decrypt(secret) == "test-not-a-real-api-key-123456"


def test_endpoint_change_clears_old_key(client):
    assert enable_provider(client).status_code == 200
    response = client.put("/api/ai/config", json={"providers": [{"id": "deepseek", "base_url": "https://example.com/v1"}]})
    assert response.status_code == 200
    assert not next(p for p in response.json()["providers"] if p["id"] == "deepseek")["configured"]


def test_text_only_context_and_daily_limit(client, monkeypatch):
    enable_provider(client)
    client.put("/api/ai/config", json={"share_library_context": True, "daily_request_limit": 1})
    seen = []
    async def mock_post(self, url, **kwargs):
        seen.append(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "当前库尚无商品。"}}], "usage": {"total_tokens": 12}})
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    first = client.post("/api/ai/chat", json={"message": "当前有多少商品？"})
    assert first.status_code == 200
    assert first.json()["context_shared"]
    assert "image_url" not in json.dumps(seen)
    assert "company_evidence" in seen[0]["messages"][0]["content"]
    assert client.post("/api/ai/chat", json={"message": "再查一次"}).status_code == 429
    assert len(seen) == 1


def test_non_admin_cannot_change_provider(client):
    assert client.post("/api/team", json={"name": "运营", "password": "member-password", "role": "operator"}).status_code == 200
    client.post("/api/logout")
    assert client.post("/api/login", json={"name": "运营", "password": "member-password"}).status_code == 200
    assert client.put("/api/ai/config", json={"active_provider": "deepseek"}).status_code == 403


def test_http_external_endpoint_rejected(client):
    response = client.put("/api/ai/config", json={"providers": [{"id": "custom", "base_url": "http://example.com/v1"}]})
    assert response.status_code == 400


def test_anthropic_adapter(client, monkeypatch):
    client.put("/api/ai/config", json={"providers": [{"id": "anthropic", "model": "account-model-id", "api_key": "test-key-anthropic", "enabled": True}]})
    seen = []
    async def mock_post(self, url, **kwargs):
        seen.append((url, kwargs))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "已收到。"}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    response = client.post("/api/ai/chat", json={"message": "你好", "provider_id": "anthropic"})
    assert response.status_code == 200
    assert seen[0][0].endswith("/messages")
    assert "system" in seen[0][1]["json"]
    assert seen[0][1]["headers"]["x-api-key"] == "test-key-anthropic"


@pytest.mark.parametrize("payload", [[], {"choices": [None]}, {"choices": [{"message": None}]}])
def test_malformed_upstream_is_handled(client, monkeypatch, payload):
    enable_provider(client)
    async def mock_post(self, url, **kwargs):
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    assert client.post("/api/ai/chat", json={"message": "你好"}).status_code == 502


def test_upstream_usage_cannot_reflect_key(client, monkeypatch):
    enable_provider(client)
    async def mock_post(self, url, **kwargs):
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}], "usage": {"reflected_key": "test-not-a-real-api-key-123456", "total_tokens": 9}})
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    response = client.post("/api/ai/chat", json={"message": "你好"})
    assert response.status_code == 200
    assert response.json()["usage"] == {"total_tokens": 9}
    assert "test-not-a-real-api-key" not in response.text
