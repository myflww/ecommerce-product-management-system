"""SKU price tracking uses isolated storage and never calls a marketplace."""
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import browser_price_routes
import price_capture
import price_storage as storage
import price_tracking


@pytest.fixture(autouse=True)
def no_live_reference_rates(monkeypatch):
    # Existing price tests remain offline; FX behavior has dedicated tests.
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *args, **kwargs: None)


@pytest.fixture
def variants(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "data")
    storage.init_storage()
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(price_tracking.router)
    app.include_router(browser_price_routes.router)
    browser_price_routes._consumed.clear()
    with TestClient(app, client=('127.0.0.1', 30001)) as client:
        response = client.post('/api/setup', json={'name': 'SKU 管理员', 'password': 'test-only-password'})
        response.raise_for_status()
        user = response.json()['user']
        picture = io.BytesIO()
        Image.new('RGB', (64, 64), 'green').save(picture, 'PNG')
        response = client.post('/api/products', data={'metadata': json.dumps({
            'name': '多规格商品', 'listings': [{
                'platform': 'Temu', 'store': '美国店', 'product_code': 'SKU-RED',
                'product_number': 'SKC-001', 'status': 'listed', 'price': '20.00',
                'currency': 'USD', 'link': 'https://shop.example.com/p/1'}]})},
            files={'images': ('sample.png', picture.getvalue(), 'image/png')})
        response.raise_for_status()
        product = response.json()
        listing = product['listings'][0]['id']
        storage.save_listing_tracking(listing, {
            'enabled': True, 'region': '美国', 'variant_hint': '红色 2 件装'}, user)
        yield client, user, product, listing


def add(variants, **changes):
    client, _, _, listing = variants
    return client.post('/api/price-tracking/listings/' + listing + '/variants', json={
        'product_code': 'SKU-BLUE', 'variant_hint': '蓝色 4 件装', **changes})


def rows(variants):
    client, _, product, _ = variants
    return client.get('/api/price-tracking/products/' + product['id']).json()['listings']


def test_shared_link_skus_have_independent_prices_baselines_and_history(variants):
    client, user, product, listing = variants
    parent = storage.get_target(listing, user)
    storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=parent)
    response = add(variants, original_price='40')
    response.raise_for_status()
    child = response.json()
    assert child['id'] != listing
    assert child['source_url'] == parent['source_url']
    assert child['platform'] == parent['platform'] and child['store'] == parent['store']
    assert child['product_number'] == parent['product_number'] == 'SKC-001'
    assert child['product_code'] == 'SKU-BLUE'
    assert child['variant_hint'] == '蓝色 4 件装' and child['region'] == '美国'
    assert child['enabled'] is True and child['current_price'] == ''
    assert child['last_checked_at'] == '' and child['last_success_at'] == ''
    assert storage.get_history(child['id'], user)['items'] == []
    target = storage.get_target(child['id'], user)
    assert storage.record_attempt(child['id'], {'price': '24', 'currency': 'USD'}, expected_target=target)
    first, second = rows(variants)
    assert first['current_price'] == '15' and first['original_price'] == '20.00'
    assert first['ratio_percent'] == '75.0'
    assert second['current_price'] == '24' and second['original_price'] == '40'
    assert second['ratio_percent'] == '60.0'
    assert len(storage.eligible_targets()) == 2
    assert storage.get_history(listing, user)['items'][0]['price'] == '15'
    assert storage.get_history(child['id'], user)['items'][0]['price'] == '24'
    saved = client.get('/api/products/' + product['id']).json()
    assert saved['listings'][0] == {**product['listings'][0], 'region': '美国', 'variant_hint': '红色 2 件装'}
    assert saved['listings'][1]['product_code'] == 'SKU-BLUE'
    assert saved['listings'][0]['status'] == 'listed'
    assert saved['listings'][1]['status'] == 'pending'
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='price_variant_add'").fetchone()[0] == 1


def test_new_sku_never_inherits_the_parent_baseline_and_can_set_currency(variants):
    response = add(variants, currency='cad')
    response.raise_for_status()
    assert response.json()['original_price'] == ''
    assert response.json()['currency'] == 'CAD'


@pytest.mark.parametrize('source,enabled', [('https://shop.example.com/p/1', False), ('', False)])
def test_sku_preserves_legacy_enabled_flag_without_using_it_as_price_status(variants, source, enabled):
    _, user, _, listing = variants
    storage.save_listing_tracking(listing, {'source_url': source, 'enabled': enabled}, user)
    response = add(variants)
    response.raise_for_status()
    assert response.json()['enabled'] is False
    assert response.json()['source_url'] == source
    assert response.json()['status'] == ('never' if source else 'unconfigured')


def test_new_sku_with_legacy_disabled_flag_still_accepts_manual_price(variants):
    _, user, _, listing = variants
    storage.save_listing_tracking(listing, {'enabled': False}, user)
    child_response = add(variants, original_price='40')
    child_response.raise_for_status()
    child = child_response.json()
    assert child['enabled'] is False and child['status'] == 'never'
    target = storage.get_target(child['id'], user)
    assert storage.record_attempt(child['id'], {'price': '24', 'currency': 'USD'}, expected_target=target)
    parent, saved = rows(variants)
    assert saved['current_price'] == '24' and saved['ratio_percent'] == '60.0'
    assert saved['status'] == 'ok' and saved['enabled'] is False
    assert parent['current_price'] == ''


def test_duplicate_sku_is_rejected_atomically_but_skc_can_distinguish_it(variants):
    client, _, product, listing = variants
    response = add(variants, product_code='SKU-RED')
    assert response.status_code == 409
    assert len(rows(variants)) == 1
    different = [{**product['listings'][0], 'product_number': 'SKC-002'}]
    client.patch('/api/products/' + product['id'], json={'listings': different}).raise_for_status()
    # An existing SKU in another SKC does not block changing a distinct SKU's SKC.
    child = add(variants).json()
    response = client.put('/api/price-tracking/listings/' + child['id'], json={
        'product_code': 'SKU-RED', 'product_number': 'SKC-001'})
    response.raise_for_status()
    assert response.json()['product_number'] == 'SKC-001'
    response = client.put('/api/price-tracking/listings/' + listing, json={'product_number': 'SKC-001'})
    assert response.status_code == 409
    assert rows(variants)[0]['product_number'] == 'SKC-002'


@pytest.mark.parametrize('bad', [
    {'product_code': ''}, {'product_code': '   '}, {'product_code': 12},
    {'product_code': 'x' * 121}, {'variant_hint': ''}, {'variant_hint': None},
    {'variant_hint': 'x' * 201}, {'original_price': '0'}, {'original_price': True},
    {'currency': 'US'}, {'currency': None}, {'unknown': True},
])
def test_invalid_variant_never_creates_a_partial_row(variants, bad):
    before = rows(variants)
    assert add(variants, **bad).status_code == 400
    assert rows(variants) == before
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_tracking').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='price_variant_add'").fetchone()[0] == 0


def test_variant_count_limit_leaves_the_existing_hundred_records_unchanged(variants):
    client, _, product, _ = variants
    listings = [product['listings'][0]] + [{**product['listings'][0], 'id': None,
        'product_code': 'SKU-' + str(index)} for index in range(99)]
    client.patch('/api/products/' + product['id'], json={'listings': listings}).raise_for_status()
    assert add(variants).status_code == 400
    assert len(rows(variants)) == 100


def test_only_an_authorized_editor_can_create_skus_or_change_their_identity(variants):
    client, _, _, listing = variants
    client.post('/api/team', json={'name': '其他成员', 'password': 'test-only-password', 'role': 'operator'}).raise_for_status()
    client.post('/api/logout').raise_for_status()
    client.post('/api/login', json={'name': '其他成员', 'password': 'test-only-password'}).raise_for_status()
    assert add(variants).status_code == 403
    assert client.put('/api/price-tracking/listings/' + listing, json={'product_code': 'SKU-OTHER'}).status_code == 403
    assert len(rows(variants)) == 1
    client.post('/api/logout').raise_for_status()
    assert add(variants).status_code == 401


@pytest.mark.parametrize('remove', ['product', 'listing'])
def test_removed_listing_or_soft_deleted_product_cannot_gain_skus(variants, remove):
    client, _, product, listing = variants
    if remove == 'product':
        client.delete('/api/products/' + product['id']).raise_for_status()
    else:
        client.patch('/api/products/' + product['id'], json={'listings': []}).raise_for_status()
    assert add(variants).status_code == 404
    assert storage.eligible_targets() == []
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM product_listings').fetchone()[0] == (1 if remove == 'product' else 0)


@pytest.mark.parametrize('identity', [
    {'product_code': 'SKU-GREEN'}, {'product_number': 'SKC-NEW'}, {'currency': 'CAD'},
])
def test_identity_edit_clears_only_this_sku_quote_and_rejects_inflight_result(variants, identity):
    client, user, _, listing = variants
    child = add(variants, original_price='40').json()
    old = storage.get_target(listing, user)
    child_target = storage.get_target(child['id'], user)
    storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=old)
    storage.record_attempt(child['id'], {'price': '30', 'currency': 'USD'}, expected_target=child_target)
    response = client.put('/api/price-tracking/listings/' + listing, json=identity)
    response.raise_for_status()
    current = response.json()
    assert current['current_price'] == '' and current['ratio_percent'] is None
    assert current['last_success_at'] == '' and current['error'] == ''
    assert current['revision'] == old['revision'] + 1
    assert storage.record_attempt(listing, {'price': '10', 'currency': 'USD'}, expected_target=old) is False
    assert storage.record_attempt(listing, error='outdated failure', expected_target=old) is False
    assert rows(variants)[1]['current_price'] == '30'
    assert storage.get_history(listing, user)['items'][0]['price'] == '15'
    assert len(storage.get_history(listing, user)['items']) == 1


def test_non_identity_edit_keeps_saved_quote_compatible_with_existing_hashes(variants):
    client, user, _, listing = variants
    old = storage.get_target(listing, user)
    storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=old)
    response = client.put('/api/price-tracking/listings/' + listing, json={
        'product_code': 'SKU-RED', 'product_number': 'SKC-001', 'currency': 'usd',
        'original_price': '30'})
    response.raise_for_status()
    saved = response.json()
    assert saved['current_price'] == '15' and saved['ratio_percent'] == '50.0'
    assert saved['revision'] == old['revision'] and saved['status'] == 'ok'


def test_direct_product_editor_identity_change_rejects_an_inflight_old_quote(variants):
    client, user, product, listing = variants
    old = storage.get_target(listing, user)
    updated = [{**product['listings'][0], 'product_code': 'SKU-CHANGED'}]
    client.patch('/api/products/' + product['id'], json={'listings': updated}).raise_for_status()
    assert storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=old) is False


@pytest.mark.parametrize('identity', [
    {'product_code': 'SKU-GREEN'}, {'product_number': 'SKC-NEW'}, {'currency': 'CAD'},
    {'platform': 'Walmart'}, {'store': '加拿大店'},
])
def test_product_editor_identity_change_clears_only_affected_quote_and_rejects_browser_target(variants, monkeypatch, identity):
    client, user, product, listing = variants
    child = add(variants, original_price='40').json()
    target = storage.get_target(listing, user)
    storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=target)
    child_target = storage.get_target(child['id'], user)
    storage.record_attempt(child['id'], {'price': '30', 'currency': 'USD'}, expected_target=child_target)
    listings = client.get('/api/products/' + product['id']).json()['listings']
    listings[0].update(identity)
    client.patch('/api/products/' + product['id'], json={'listings': listings}).raise_for_status()
    current, unaffected = rows(variants)
    assert current['current_price'] == '' and current['ratio_percent'] is None
    assert current['last_success_at'] == '' and current['last_checked_at'] == ''
    assert current['revision'] == target['revision'] + 1
    assert unaffected['current_price'] == '30' and unaffected['revision'] == child_target['revision']
    assert len(storage.get_history(listing, user)['items']) == 1
    assert storage.record_attempt(listing, {'price': '8', 'currency': 'USD'}, expected_target=target) is False
    assert storage.record_attempt(listing, error='late error', expected_target=target) is False
    monkeypatch.setattr(browser_price_routes.engine, 'candidate', lambda *_: (target, {
        'price': '9', 'currency': 'USD', 'source': 'browser_confirmed'}))
    monkeypatch.setattr(browser_price_routes.engine, 'close', lambda *_: {'state': 'closed'})
    response = client.post('/api/price-tracking/browser/old-sku/confirm', json={
        'candidate_id': 'quote-A', 'confirmed_variant': True, 'confirmed_price_basis': True})
    assert response.status_code == 409
    assert rows(variants)[0]['current_price'] == ''
    assert len(storage.get_history(listing, user)['items']) == 1


@pytest.mark.parametrize('fields', [{}, {'price': '30'}])
def test_product_editor_keeps_quote_when_identity_is_unchanged_or_only_base_changes(variants, fields):
    client, user, product, listing = variants
    target = storage.get_target(listing, user)
    storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=target)
    updated = [{**product['listings'][0], **fields}]
    client.patch('/api/products/' + product['id'], json={'listings': updated}).raise_for_status()
    saved = rows(variants)[0]
    assert saved['current_price'] == '15' and saved['status'] == 'ok'
    assert saved['ratio_percent'] == ('50.0' if fields else '75.0')
    assert saved['revision'] == target['revision']


def test_product_editor_works_when_optional_price_tracking_is_not_installed(variants):
    client, _, product, _ = variants
    with backend._connection(write=True) as conn:
        conn.execute('DROP TABLE price_tracking')
    updated = [{**product['listings'][0], 'product_code': 'SKU-RENAMED'}]
    response = client.patch('/api/products/' + product['id'], json={'listings': updated})
    response.raise_for_status()
    assert response.json()['listings'][0]['product_code'] == 'SKU-RENAMED'


def test_manual_refresh_passes_each_independent_sku_to_the_capture_layer(variants, monkeypatch):
    client, _, _, listing = variants
    child = add(variants, original_price='40').json()
    calls = []
    async def fetch(url, currency, **options):
        calls.append((url, currency, options))
        return {'price': {'SKU-RED': '15', 'SKU-BLUE': '24'}[options['expected_sku']],
                'currency': currency, 'source': 'json_ld_offer'}
    monkeypatch.setattr(price_capture, 'fetch_and_extract', fetch)
    for identity in (listing, child['id']):
        response = client.post('/api/price-tracking/listings/' + identity + '/refresh')
        response.raise_for_status()
        assert response.json()['updated'] is True
    assert [call[2]['expected_sku'] for call in calls] == ['SKU-RED', 'SKU-BLUE']
    assert [call[2]['variant_hint'] for call in calls] == ['红色 2 件装', '蓝色 4 件装']
    assert calls[0][0] == calls[1][0] == child['source_url']
    assert [(row['current_price'], row['original_price']) for row in rows(variants)] == [('15', '20.00'), ('24', '40')]


def _structured_sku_page(shape):
    choices = [('SKU-RED', '红色 2 件装', '11.25'), ('SKU-BLUE', '蓝色 4 件装', '23.75')]
    def offer(price):
        return {'@type': 'Offer', 'price': price, 'priceCurrency': 'USD', 'eligibleRegion': 'US'}
    if shape == 'products':
        data = [{'@type': 'Product', 'sku': sku, 'name': name, 'offers': offer(price)}
                for sku, name, price in choices]
    else:
        data = {'@type': 'Product', 'name': '多规格商品', 'offers': [
            {**offer(price), 'sku': sku, 'name': name} for sku, name, price in choices]}
    return '<html><head><script type="application/ld+json">' + json.dumps(data) + '</script></head><body>多规格商品</body></html>'


@pytest.mark.parametrize('shape', ['products', 'offers'])
def test_manual_refresh_extracts_two_real_html_sku_prices_without_cross_writing(variants, monkeypatch, shape):
    client, user, _, listing = variants
    child = add(variants, original_price='40').json()
    calls = []
    def fetched(url):
        calls.append(url)
        return _structured_sku_page(shape), url
    monkeypatch.setattr(price_capture, '_fetch_page', fetched)
    first = client.post('/api/price-tracking/listings/' + listing + '/refresh')
    first.raise_for_status()
    assert first.json()['updated'] is True, first.json()
    assert [row['current_price'] for row in rows(variants)] == ['11.25', '']
    second = client.post('/api/price-tracking/listings/' + child['id'] + '/refresh')
    second.raise_for_status()
    assert second.json()['updated'] is True, second.json()
    assert [row['current_price'] for row in rows(variants)] == ['11.25', '23.75']
    assert len(calls) == 2 and calls[0] == calls[1] == child['source_url']
    for identity, sku, price in [(listing, 'SKU-RED', '11.25'), (child['id'], 'SKU-BLUE', '23.75')]:
        history = storage.get_history(identity, user)['items']
        assert len(history) == 1 and history[0]['price'] == price
        assert 'matched_sku=' + sku in history[0]['evidence']


@pytest.mark.parametrize('page_kind', ['public_default', 'prefix_sku'])
def test_manual_refresh_refuses_unverified_sku_and_keeps_its_last_valid_price(variants, monkeypatch, page_kind):
    client, user, _, listing = variants
    child = add(variants, original_price='40').json()
    for identity, amount in [(listing, '15'), (child['id'], '24')]:
        target = storage.get_target(identity, user)
        storage.record_attempt(identity, {'price': amount, 'currency': 'USD'}, expected_target=target)
    before = rows(variants)
    product = {'@type': 'Product', 'name': '蓝色 4 件装', 'offers': {
        '@type': 'Offer', 'price': '2', 'priceCurrency': 'USD', 'eligibleRegion': 'US'}}
    if page_kind == 'prefix_sku':
        product['sku'] = 'SKU-BLUE-OTHER'
    markup = '<html><head><script type="application/ld+json">' + json.dumps(product) + '</script>'
    markup += '<meta property="product:price:amount" content="2"><meta property="product:price:currency" content="USD"></head><body>蓝色 4 件装 Price USD 2</body></html>'
    monkeypatch.setattr(price_capture, '_fetch_page', lambda url: (markup, url))
    async def no_ai(*args):
        pytest.fail('An unverified SKU must not be inferred by AI')
    monkeypatch.setattr(price_tracking, '_ai_extract', no_ai)
    storage.save_settings({'allow_ai': True}, user)
    response = client.post('/api/price-tracking/listings/' + child['id'] + '/refresh')
    response.raise_for_status()
    assert response.json()['updated'] is False
    assert 'SKU' in response.json()['message']
    parent, saved = rows(variants)
    assert parent == before[0]
    assert saved['current_price'] == '24' and saved['stale'] is True
    assert saved['last_success_at'] == before[1]['last_success_at']
    history = storage.get_history(child['id'], user)['items']
    assert len(history) == 2 and history[0]['price'] == '' and history[0]['error']
    assert history[1]['price'] == '24'


@pytest.mark.parametrize('shared_identity', ['source_url', 'product_number'])
def test_manual_refresh_requires_sku_for_a_shared_link_or_skc_before_fetching(variants, monkeypatch, shared_identity):
    client, user, _, listing = variants
    child = add(variants).json()
    changes = ({'product_number': 'SKC-OTHER'} if shared_identity == 'source_url'
               else {'source_url': 'https://shop.example.com/p/another'})
    storage.save_listing_tracking(child['id'], changes, user)
    storage.save_listing_tracking(listing, {'product_code': ''}, user)
    target = storage.get_target(listing, user)
    storage.record_attempt(listing, {'price': '15', 'currency': 'USD'}, expected_target=target)
    async def no_fetch(*args, **options):
        pytest.fail('A shared listing without SKU must be rejected before fetching')
    monkeypatch.setattr(price_capture, 'fetch_and_extract', no_fetch)
    response = client.post('/api/price-tracking/listings/' + listing + '/refresh')
    response.raise_for_status()
    assert response.json()['updated'] is False and 'SKU' in response.json()['message']
    current = rows(variants)[0]
    assert current['current_price'] == '15' and current['stale'] is True
    assert current['last_success_at']
