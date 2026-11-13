"""Chat transport contracts with mocked providers and disposable local stores."""
import io
import json
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

import ai_service
import backend
import chat_memory


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    backend._login_attempts.clear()
    ai_service.init_config(tmp_path)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(ai_service.router)
    with TestClient(app) as client:
        assert client.post('/api/setup', json={'name': '聊天测试', 'password': 'chat-testing-only'}).status_code == 200
        assert client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'model': 'deepseek-flash',
            'enabled': True, 'api_key': 'chat-test-key-only', 'vision': True}]}).status_code == 200
        yield client


@pytest.fixture
def seen(monkeypatch):
    requests = []
    async def post(self, url, **kwargs):
        requests.append(kwargs['json'])
        return httpx.Response(200, json={'choices': [{'message': {'content': '已根据附件整理。'}}]})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    return requests


def image():
    stream = io.BytesIO()
    Image.new('RGB', (32, 32), '#f2bc44').save(stream, 'PNG')
    return ('商品.png', stream.getvalue(), 'image/png')


def test_official_effort_is_forwarded_and_bad_values_do_not_spend(client, seen):
    options = client.get('/api/ai/config').json()['providers'][0]['reasoning']['options']
    assert {'default', 'none', 'low', 'high', 'max'} <= {o['value'] for o in options}
    response = client.post('/api/ai/chat', json={'message': '分析商品', 'reasoning_effort': 'max'})
    assert response.status_code == 200, response.text
    assert seen[-1]['reasoning_effort'] == 'max'
    assert seen[-1]['thinking']['type'] == 'enabled'
    before = client.get('/api/ai/config').json()['requests_today']
    for invalid in ('medium', 'invented', [], None):
        assert client.post('/api/ai/chat', json={'message': '分析', 'reasoning_effort': invalid}).status_code == 400
    assert client.get('/api/ai/config').json()['requests_today'] == before
    assert len(seen) == 1


def test_images_and_file_text_have_real_multimodal_content_and_followup(client, seen):
    response = client.post('/api/ai/chat', data={'message': '比较规格', 'reasoning_effort': 'low'}, files=[
        ('files', image()), ('files', ('规格.txt', '蜂窝蜡烛，高度20cm，2件装'.encode(), 'text/plain'))])
    assert response.status_code == 200, response.text
    content = seen[-1]['messages'][-1]['content']
    assert '蜂窝蜡烛' in content[0]['text']
    assert any(p['type'] == 'image_url' and p['image_url']['url'].startswith('data:image/jpeg;base64,') for p in content)
    assert '没有图片输入' not in seen[-1]['messages'][0]['content']
    refs = response.json()['attachments']
    assert len(refs) == 2
    assert all('id' in a and 'data' not in a and 'text' not in a for a in refs)
    follow = client.post('/api/ai/chat', json={'message': '这个高度再确认下', 'history': [
        {'role': 'user', 'content': '比较规格', 'attachments': refs}, {'role': 'assistant', 'content': response.json()['reply']} ]})
    assert follow.status_code == 200, follow.text
    history_user = seen[-1]['messages'][1]['content']
    assert '20cm' in history_user[0]['text']
    assert any(p['type'] == 'image_url' for p in history_user)
    assert client.get('/api/stats').json()['total_products'] == 0


def test_documents_do_not_require_library_context_or_vision(client, seen):
    client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'vision': False}], 'share_library_context': False})
    response = client.post('/api/ai/chat', files=[('files', ('创意.md', '# 套装\n3件装'.encode(), 'text/markdown'))])
    assert response.status_code == 200, response.text
    assert response.json()['attachments'][0]['characters'] > 0
    assert not response.json()['context_shared']
    assert '3件装' in seen[-1]['messages'][-1]['content']
    assert '没有图片输入' in seen[-1]['messages'][0]['content']


def test_image_consent_and_known_nonvisual_model_rejected_before_reservation(client, seen):
    client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'vision': False}]})
    assert client.post('/api/ai/chat', data={'message': '看图'}, files=[('files', image())]).status_code == 409
    client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'model': 'deepseek-v4-pro', 'vision': True}]})
    assert client.post('/api/ai/chat', data={'message': '看图'}, files=[('files', image())]).status_code == 400
    assert not seen
    assert client.get('/api/ai/config').json()['requests_today'] == 0


def test_attachment_clear_expiry_and_other_session_cannot_reuse(client, seen):
    first = client.post('/api/ai/chat', files=[('files', ('规格.txt', b'size: 20cm', 'text/plain'))]).json()
    body = {'message': '继续', 'history': [{'role': 'user', 'content': '附件', 'attachments': first['attachments']}]}
    original_cookie = client.cookies.get(backend.COOKIE_NAME)
    client.post('/api/logout')
    client.post('/api/login', json={'name': '聊天测试', 'password': 'chat-testing-only'})
    assert client.cookies.get(backend.COOKIE_NAME) != original_cookie
    assert client.post('/api/ai/chat', json=body).status_code == 409
    second = client.post('/api/ai/chat', files=[('files', ('新规格.txt', b'new size', 'text/plain'))]).json()
    assert client.post('/api/ai/chat/attachments/clear').status_code == 200
    assert client.post('/api/ai/chat', json={'message': '继续', 'history': [
        {'role': 'user', 'content': '文件', 'attachments': second['attachments']}]}).status_code == 409
    assert len(seen) == 2


def test_unsupported_and_large_files_never_spend_and_failures_do_not_cache(client, seen, monkeypatch):
    for upload in [('恶意.exe', b'MZxxx', 'application/octet-stream'), ('空白.txt', b'', 'text/plain')]:
        assert client.post('/api/ai/chat', files=[('files', upload)]).status_code == 400
    assert client.post('/api/ai/chat', files=[('files', ('big.txt', b'a' * (10 * 1024 * 1024 + 1), 'text/plain'))]).status_code == 413
    assert client.get('/api/ai/config').json()['requests_today'] == 0
    async def fail(self, *args, **kwargs):
        return httpx.Response(401)
    monkeypatch.setattr(httpx.AsyncClient, 'post', fail)
    assert client.post('/api/ai/chat', files=[('files', ('规格.txt', b'secret specs', 'text/plain'))]).status_code == 502
    assert not chat_memory._entries


def test_changed_provider_scope_and_revoked_image_consent_are_respected(client, seen):
    first = client.post('/api/ai/chat', files=[('files', image())]).json()
    body = {'message': '再看图', 'history': [{'role': 'user', 'content': '图', 'attachments': first['attachments']}]}
    client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'vision': False}]})
    assert client.post('/api/ai/chat', json=body).status_code == 409
    client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'model': 'deepseek-v4-pro'}]})
    assert client.post('/api/ai/chat', json=body).status_code == 409
    assert len(seen) == 1


def test_expired_attachment_returns_explicit_error(client, seen, monkeypatch):
    first = client.post('/api/ai/chat', files=[('files', ('spec.txt', b'20cm', 'text/plain'))]).json()
    monkeypatch.setattr(chat_memory, 'TTL_SECONDS', -1)
    response = client.post('/api/ai/chat', json={'message': '继续', 'history': [
        {'role': 'user', 'content': '文件', 'attachments': first['attachments']}]})
    assert response.status_code == 409
    assert '过期' in response.json()['detail']


def test_chunked_upload_is_limited_during_streaming():
    """No Content-Length: stop before an unbounded file can spool to disk."""
    received = 0
    header = b'--testboundary\r\nContent-Disposition: form-data; name="files"; filename="huge.txt"\r\nContent-Type: text/plain\r\n\r\n'
    async def receive():
        nonlocal received
        received += 1
        return {'type': 'http.request', 'body': header if received == 1 else b'a' * (1024 * 1024), 'more_body': True}
    request = Request({'type': 'http', 'headers': [(b'content-type', b'multipart/form-data; boundary=testboundary')]}, receive)
    with pytest.raises(Exception) as exc:
        asyncio.run(ai_service._chat_body(request))
    assert exc.value.status_code == 413
    assert received <= 23


@pytest.mark.parametrize('role', [[], {}, None, 12])
def test_malformed_history_role_is_a_validation_error(client, seen, role):
    result = client.post('/api/ai/chat', json={'message': '继续', 'history': [{'role': role, 'content': '资料'}]})
    assert result.status_code == 400
    assert not seen
    assert client.get('/api/ai/config').json()['requests_today'] == 0


@pytest.mark.parametrize('provider', ['deepseek', 'anthropic'])
def test_partial_answers_show_generation_limit_notice(client, monkeypatch, provider):
    if provider == 'anthropic':
        client.put('/api/ai/config', json={'providers': [{'id': 'anthropic', 'model': 'account-model', 'enabled': True, 'api_key': 'stub-only'}], 'active_provider': 'anthropic'})
    async def partial(self, url, **kwargs):
        return httpx.Response(200, json={'choices': [{'message': {'content': '未完的内容'}, 'finish_reason': 'length'}],
            'content': [{'type': 'text', 'text': '未完的内容'}], 'stop_reason': 'max_tokens'})
    monkeypatch.setattr(httpx.AsyncClient, 'post', partial)
    result = client.post('/api/ai/chat', json={'message': '详细分析'})
    assert result.status_code == 200
    assert result.json()['truncated']
    assert '达到生成上限' in result.json()['reply']

