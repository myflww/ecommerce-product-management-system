"""Cross-module permissions: disposable libraries, fake credentials, no live HTTP."""
from contextlib import ExitStack
import io
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import ai_service
import backend
import exchange_rates
import price_capture
import price_storage
import price_tracking


PASSWORD = 'integration-test-password'
FAKE_KEY = 'rbac-integration-fake-key-never-valid'
ROLES = ('owner', 'admin', 'operator')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / 'library')
    ai_service.init_config(tmp_path / 'ai')
    price_tracking.init_storage()
    backend._login_attempts.clear()
    price_tracking._active.clear()
    price_tracking._batch_running = False
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)

    async def no_marketplace(*args, **kwargs):
        raise AssertionError('A price test attempted an unmocked marketplace request')

    def no_rates(*args, **kwargs):
        raise AssertionError('These same-currency tests must not request exchange rates')

    monkeypatch.setattr(price_capture, 'fetch_and_extract', no_marketplace)
    monkeypatch.setattr(exchange_rates, 'refresh_rates', no_rates)
    requests = []

    def upstream(request):
        # MockTransport is the only transport accessible to application AI HTTP.
        # Unknown destinations fail rather than ever reaching DNS or a socket.
        assert request.url.host == 'api.deepseek.com'
        assert request.headers['authorization'] == 'Bearer ' + FAKE_KEY
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path == '/v1/tools/search':
            return httpx.Response(200, json={'results': [{
                'title': '测试公开资料', 'url': 'https://www.example.org/public',
                'content': '此内容来自本地测试桩，没有发起联网请求。'}]})
        assert request.url.path == '/v1/chat/completions'
        return httpx.Response(200, json={
            'choices': [{'message': {'content': '离线测试回答'}, 'finish_reason': 'stop'}],
            'usage': {'total_tokens': 9}})

    async_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        kwargs['transport'] = httpx.MockTransport(upstream)
        return async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', offline_client)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(ai_service.router)
    app.include_router(price_tracking.router)
    with ExitStack() as stack:
        owner = stack.enter_context(TestClient(app))
        owner.post('/api/setup', json={'name': '集成老板', 'password': PASSWORD}).raise_for_status()
        clients, identities = {'owner': owner}, {}
        for role in ('admin', 'operator'):
            name = '集成' + role
            response = owner.post('/api/team', json={'name': name, 'password': PASSWORD, 'role': role})
            response.raise_for_status()
            identities[role] = response.json()['id']
            member = stack.enter_context(TestClient(app))
            member.post('/api/login', json={'name': name, 'password': PASSWORD}).raise_for_status()
            clients[role] = member
        configured = owner.put('/api/ai/config', json={
            'providers': [{'id': 'deepseek', 'enabled': True, 'api_key': FAKE_KEY,
                           'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash'}],
            'active_provider': 'deepseek', 'allow_web_search': True,
            'share_library_context': False, 'share_knowledge_context': False})
        configured.raise_for_status()
        yield {'owner': owner, 'clients': clients, 'identities': identities, 'requests': requests}


def grant(workspace, role, **overrides):
    owner = workspace['owner']
    identity = workspace['identities'][role]
    member = next(item for item in owner.get('/api/team').json()['items'] if item['id'] == identity)
    current = {**member.get('permission_overrides', {}), **overrides}
    response = owner.patch('/api/team/' + identity, json={
        'expected_revision': member['revision'], 'permissions': current})
    response.raise_for_status()
    return response.json()


def create_product(client, name='价格权限测试商品'):
    raw = io.BytesIO()
    Image.new('RGB', (64, 64), 'green').save(raw, 'PNG')
    response = client.post('/api/products', data={'metadata': json.dumps({
        'name': name, 'listings': [{
            'platform': 'Temu', 'store': '测试店', 'product_code': 'SKU-RED',
            'product_number': 'SKC-TEST', 'price': '20', 'currency': 'USD',
            'status': 'listed', 'link': 'https://shop.example.com/product'}]})},
        files={'images': ('product.png', raw.getvalue(), 'image/png')})
    response.raise_for_status()
    product = response.json()
    listing_id = product['listings'][0]['id']
    response = client.put('/api/price-tracking/listings/' + listing_id, json={
        'enabled': True, 'region': 'US', 'variant_hint': 'red two pieces'})
    response.raise_for_status()
    return product['id'], listing_id


def tracking(client, product_id):
    response = client.get('/api/price-tracking/products/' + product_id)
    response.raise_for_status()
    return response.json()


def manual(client, product_id, listing_id, amount='12'):
    row = next(item for item in tracking(client, product_id)['listings'] if item['id'] == listing_id)
    return client.post('/api/price-tracking/listings/' + listing_id + '/manual-price', json={
        'price': amount, 'currency': 'USD', 'expected_context': row['context_token']})


def price_state():
    # Include audit and batch timestamps: a forbidden action must be side-effect free.
    with backend._connection() as conn:
        return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('products', 'product_listings', 'price_tracking', 'price_history', 'activity', 'app_meta')}


@pytest.mark.parametrize('role,can_ai,can_price', [
    ('owner', True, True), ('admin', False, True), ('operator', False, False)])
def test_default_settings_capabilities_match_server_and_controls(workspace, role, can_ai, can_price):
    client = workspace['clients'][role]
    config = client.get('/api/ai/config').json()
    assert config['can_manage'] is can_ai
    assert all(('base_url' in provider) is can_ai for provider in config['providers'])
    assert FAKE_KEY not in json.dumps(config)
    assert client.put('/api/ai/config', json={'daily_request_limit': 91}).status_code == (200 if can_ai else 403)
    settings = client.get('/api/price-tracking/settings').json()
    assert settings['can_edit'] is can_price
    assert settings['manual_only'] and settings['next_run_at'] is None
    assert client.put('/api/price-tracking/settings', json={'allow_ai': False}).status_code == (200 if can_price else 403)
    assert not workspace['requests']


@pytest.mark.parametrize('role', ['admin', 'operator'])
def test_individual_settings_grants_and_revocations_apply_to_existing_login(workspace, role):
    client = workspace['clients'][role]
    grant(workspace, role, **{'settings.ai': True, 'settings.price': True})
    assert client.get('/api/ai/config').json()['can_manage']
    assert client.put('/api/ai/config', json={'daily_request_limit': 73}).status_code == 200
    assert client.put('/api/price-tracking/settings', json={'allow_ai': True}).status_code == 200
    grant(workspace, role, **{'settings.ai': False, 'settings.price': False})
    assert not client.get('/api/ai/config').json()['can_manage']
    assert not client.get('/api/price-tracking/settings').json()['can_edit']
    assert client.put('/api/ai/config', json={'daily_request_limit': 74}).status_code == 403
    assert client.post('/api/ai/test', json={}).status_code == 403
    assert client.put('/api/price-tracking/settings', json={'allow_ai': False}).status_code == 403
    assert workspace['owner'].get('/api/ai/config').json()['daily_request_limit'] == 73
    assert workspace['owner'].get('/api/price-tracking/settings').json()['allow_ai']
    assert not workspace['requests']


def test_all_default_roles_can_chat_without_configuring_model(workspace):
    for role in ROLES:
        response = workspace['clients'][role].post('/api/ai/chat', json={'message': '普通问题', 'mode': 'general'})
        response.raise_for_status()
        assert response.json()['reply'] == '离线测试回答'
    assert len(workspace['requests']) == 3
    assert workspace['owner'].get('/api/ai/config').json()['requests_today'] == 3


@pytest.mark.parametrize('role', ['admin', 'operator'])
def test_revoked_ai_use_denies_every_generation_route_without_charging(workspace, role):
    client = workspace['clients'][role]
    # Configuration permission must not implicitly restore ordinary AI use.
    grant(workspace, role, **{'ai.use': False, 'settings.ai': True})
    assert client.post('/api/ai/chat', json={'message': '问题'}).status_code == 403
    assert client.post('/api/ai/chat', json={
        'message': '问题', 'search_enabled': True, 'web_query': '公开蜡烛资料'}).status_code == 403
    assert client.post('/api/ai/title', data={'metadata': json.dumps({'name': '红色柱蜡'})}).status_code == 403
    image = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(image, 'PNG')
    assert client.post('/api/ai/deduplicate', files={
        'images': ('query.png', image.getvalue(), 'image/png')}).status_code == 403
    assert not workspace['requests']
    assert workspace['owner'].get('/api/ai/config').json()['requests_today'] == 0


def test_web_revocation_blocks_search_only_then_explicit_grant_restores_it(workspace):
    client = workspace['clients']['operator']
    grant(workspace, 'operator', **{'ai.web': False})
    assert client.post('/api/ai/chat', json={'message': '普通问题', 'mode': 'general'}).status_code == 200
    before = len(workspace['requests'])
    denied = client.post('/api/ai/chat', json={
        'message': '请搜索', 'mode': 'general', 'search_enabled': True, 'web_query': '公开蜡烛资料'})
    assert denied.status_code == 403
    assert len(workspace['requests']) == before
    assert workspace['owner'].get('/api/ai/config').json()['requests_today'] == 1
    grant(workspace, 'operator', **{'ai.web': True})
    response = client.post('/api/ai/chat', json={
        'message': '内部文字不应发往搜索', 'mode': 'general',
        'search_enabled': True, 'web_query': '公开蜡烛资料'})
    response.raise_for_status()
    assert response.json()['web_search']['status'] == 'ok'
    searches = [body for path, body in workspace['requests'] if path.endswith('/tools/search')]
    assert searches == [{'query': '公开蜡烛资料', 'count': 6, 'need_content': True}]
    assert workspace['owner'].get('/api/ai/config').json()['requests_today'] == 3


def test_admin_default_can_edit_other_products_but_operator_only_own(workspace):
    owner, admin, operator = (workspace['clients'][role] for role in ROLES)
    foreign_product, foreign_listing = create_product(owner)
    own_product, own_listing = create_product(operator, '员工自己的商品')
    assert tracking(admin, foreign_product)['can_edit']
    assert manual(admin, foreign_product, foreign_listing).status_code == 200
    assert not tracking(operator, foreign_product)['can_edit']
    assert manual(operator, foreign_product, foreign_listing).status_code == 403
    assert tracking(operator, own_product)['can_edit']
    assert manual(operator, own_product, own_listing).status_code == 200


def test_revoked_admin_edit_all_cannot_bypass_through_price_routes(workspace):
    owner, admin = workspace['owner'], workspace['clients']['admin']
    product_id, listing_id = create_product(owner)
    grant(workspace, 'admin', **{'products.edit_all': False})
    assert not tracking(admin, product_id)['can_edit']
    before = price_state()
    base = '/api/price-tracking/listings/' + listing_id
    assert admin.put(base, json={'original_price': '1', 'variant_hint': 'tampered'}).status_code == 403
    assert admin.post(base + '/variants', json={
        'product_code': 'SKU-BLUE', 'variant_hint': 'blue'}).status_code == 403
    assert manual(admin, product_id, listing_id).status_code == 403
    assert admin.post(base + '/refresh').status_code == 403
    assert admin.post('/api/price-tracking/refresh').status_code == 403
    assert price_state() == before
    fresh = admin.get('/api/me').json()['user']
    with pytest.raises(HTTPException) as denied:
        price_storage.save_listing_tracking(listing_id, {'original_price': '1'}, fresh)
    assert denied.value.status_code == 403
    own_product, own_listing = create_product(admin, '管理员自己的商品')
    assert manual(admin, own_product, own_listing).status_code == 200


def test_price_settings_grant_does_not_authorize_foreign_products(workspace):
    operator = workspace['clients']['operator']
    product_id, listing_id = create_product(workspace['owner'])
    grant(workspace, 'operator', **{'settings.price': True})
    assert operator.put('/api/price-tracking/settings', json={'allow_ai': True}).status_code == 200
    assert not tracking(operator, product_id)['can_edit']
    assert manual(operator, product_id, listing_id).status_code == 403
    assert operator.post('/api/price-tracking/refresh').status_code == 403
    grant(workspace, 'operator', **{'products.edit_all': True})
    assert tracking(operator, product_id)['can_edit']
    assert manual(operator, product_id, listing_id).status_code == 200
    grant(workspace, 'operator', **{'products.edit_all': False})
    assert manual(operator, product_id, listing_id).status_code == 403


def test_bulk_price_refresh_requires_global_product_edit_not_admin_role(workspace, monkeypatch):
    create_product(workspace['owner'])
    observed = []

    async def offline_capture(url, currency, **kwargs):
        observed.append(url)
        return {'price': '15', 'currency': currency, 'source': 'offline-fixture'}

    monkeypatch.setattr(price_capture, 'fetch_and_extract', offline_capture)
    operator = workspace['clients']['operator']
    assert operator.post('/api/price-tracking/refresh').status_code == 403
    assert not observed
    grant(workspace, 'operator', **{'products.edit_all': True})
    response = operator.post('/api/price-tracking/refresh')
    response.raise_for_status()
    assert response.json()['updated'] == 1
    assert len(observed) == 1
    grant(workspace, 'operator', **{'products.edit_all': False})
    assert operator.post('/api/price-tracking/refresh').status_code == 403
    assert len(observed) == 1


@pytest.mark.parametrize('ai_allowed', [True, False])
def test_manual_capture_inherits_ai_use_for_optional_paid_extractor(workspace, monkeypatch, ai_allowed):
    operator = workspace['clients']['operator']
    _, listing_id = create_product(operator)
    workspace['owner'].put('/api/price-tracking/settings', json={'allow_ai': True}).raise_for_status()
    if not ai_allowed:
        grant(workspace, 'operator', **{'ai.use': False})
    extractors = []

    async def offline_capture(url, currency, **kwargs):
        extractors.append(kwargs['ai_extractor'])
        return {'price': '13', 'currency': currency, 'source': 'offline-fixture'}

    monkeypatch.setattr(price_capture, 'fetch_and_extract', offline_capture)
    response = operator.post('/api/price-tracking/listings/' + listing_id + '/refresh')
    response.raise_for_status()
    assert response.json()['updated']
    assert len(extractors) == 1
    assert callable(extractors[0]) is ai_allowed
    assert not workspace['requests']
    assert workspace['owner'].get('/api/ai/config').json()['requests_today'] == 0
