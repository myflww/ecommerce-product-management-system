"""Grouped SKU editor and inline quotes use an isolated library, never a shop."""
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import price_storage as storage
import price_tracking
import knowledge_store


@pytest.fixture(autouse=True)
def no_live_reference_rates(monkeypatch):
    # Existing price tests remain offline; FX behavior has dedicated tests.
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *args, **kwargs: None)


@pytest.fixture
def editor(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    storage.init_storage()
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)
    app = FastAPI()
    app.include_router(price_tracking.router)
    app.include_router(backend.router)
    with TestClient(app) as client:
        user = client.post('/api/setup', json={'name': 'SKU 编辑管理员', 'password': 'test-only-password'}).json()['user']
        picture = io.BytesIO()
        Image.new('RGB', (64, 64), 'green').save(picture, 'PNG')
        response = client.post('/api/products', data={'metadata': json.dumps({'name': '同 SKC 编辑样品', 'listings': [
            {'platform': 'Temu', 'store': '美国店', 'product_number': 'SKC-1', 'product_code': 'RED',
             'status': 'listed', 'price': '20', 'currency': 'USD', 'link': 'https://shop.example.com/p/1', 'variant_hint': '红色 2件装', 'region': '美国'},
            {'platform': 'Temu', 'store': '美国店', 'product_number': 'SKC-1', 'product_code': 'BLUE',
             'status': 'pending', 'price': '40', 'currency': 'USD', 'link': 'https://shop.example.com/p/1', 'variant_hint': '蓝色 4件装', 'region': '美国'},
        ]})}, files={'images': ('sample.png', picture.getvalue(), 'image/png')})
        response.raise_for_status()
        yield client, user, response.json()


def product(editor):
    client, _, item = editor
    return client.get('/api/products/' + item['id']).json()


def prices(editor):
    client, _, item = editor
    return client.get('/api/price-tracking/products/' + item['id']).json()['listings']


def quote(editor, index=0, **fields):
    client, _, _ = editor
    row = prices(editor)[index]
    return client.post('/api/price-tracking/listings/' + row['id'] + '/manual-price', json={
        'price': '15', 'currency': 'USD', 'expected_context': row['context_token'], **fields})


def test_flat_editor_saves_same_skc_independent_specs_and_manual_states(editor):
    first, second = product(editor)['listings']
    assert first['product_number'] == second['product_number'] == 'SKC-1'
    assert first['link'] == second['link']
    assert first['region'] == second['region'] == '美国'
    assert first['variant_hint'] == '红色 2件装' and second['variant_hint'] == '蓝色 4件装'
    assert first['status'] == 'listed' and second['status'] == 'pending'
    assert [row['variant_hint'] for row in prices(editor)] == ['红色 2件装', '蓝色 4件装']


def test_sku_replacement_with_dirty_knowledge_index_preserves_quotes(editor):
    client, _, item = editor
    knowledge_store.init_storage()
    quote(editor).raise_for_status()
    original = prices(editor)[0]
    with backend._connection() as conn:
        history = [tuple(row) for row in conn.execute('SELECT * FROM price_history ORDER BY id')]
    rows = product(editor)['listings']
    response = client.patch('/api/products/' + item['id'], json={'listings': rows[:1]})
    response.raise_for_status()
    current = prices(editor)
    assert len(current) == 1 and current[0]['current_price'] == '15'
    assert current[0]['context_token'] == original['context_token']
    with backend._connection() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM price_history ORDER BY id')] == history
    assert knowledge_store.retrieve_products('同 SKC 编辑样品')[0]['id'] == item['id']


def test_existing_tracking_dirty_triggers_upgrade_without_changing_quotes(editor):
    client, _, item = editor
    knowledge_store.init_storage()
    quote(editor).raise_for_status()
    with backend._connection(write=True) as conn:
        conn.execute('DROP TRIGGER knowledge_tracking_update')
        conn.execute('''CREATE TRIGGER knowledge_tracking_update AFTER UPDATE ON price_tracking
            BEGIN INSERT OR IGNORE INTO knowledge_product_dirty(product_id)
            SELECT product_id FROM product_listings WHERE id=new.listing_id; END''')
        before = {table:[tuple(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY 1')]
                  for table in ('product_listings','price_tracking','price_history')}
    knowledge_store._initialized = None
    knowledge_store.init_storage()
    with backend._connection() as conn:
        after = {table:[tuple(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY 1')]
                 for table in before}
        assert 'ON CONFLICT(product_id) DO NOTHING' in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='knowledge_tracking_update'").fetchone()[0]
    assert before == after
    rows = product(editor)['listings']
    client.patch('/api/products/' + item['id'], json={'listings': rows}).raise_for_status()
    assert prices(editor)[0]['current_price'] == '15'


def test_legacy_editor_omitted_spec_fields_preserves_context_and_price(editor):
    client, _, item = editor
    quote(editor).raise_for_status()
    before = prices(editor)[0]
    rows = product(editor)['listings']
    for row in rows:
        row.pop('variant_hint')
        row.pop('region')
    rows[0]['status'] = 'archived'
    rows[0]['price'] = '30'
    client.patch('/api/products/' + item['id'], json={'listings': rows}).raise_for_status()
    after = prices(editor)[0]
    assert after['variant_hint'] == before['variant_hint'] and after['region'] == before['region']
    assert after['current_price'] == '15' and after['ratio_percent'] == '50.0'
    assert after['revision'] == before['revision']


@pytest.mark.parametrize('change', [
    {'link': 'https://shop.example.com/p/new'}, {'variant_hint': '红色 6件装'}, {'region': '加拿大'},
    {'product_code': 'RED-NEW'}, {'product_number': 'SKC-NEW'}, {'currency': 'CAD'},
])
def test_editor_context_change_clears_only_changed_sku_and_rejects_older_quote(editor, change):
    client, user, item = editor
    quote(editor).raise_for_status()
    quote(editor, 1, price='24').raise_for_status()
    old, sibling = prices(editor)
    target = storage.get_target(old['id'], user)
    rows = product(editor)['listings']
    rows[0].update(change)
    response = client.patch('/api/products/' + item['id'], json={'listings': rows})
    response.raise_for_status()
    current, unaffected = prices(editor)
    assert current['current_price'] == '' and current['revision'] == old['revision'] + 1
    assert unaffected['current_price'] == '24' and unaffected['revision'] == sibling['revision']
    assert len(storage.get_history(old['id'], user)['items']) == 1
    assert storage.record_attempt(old['id'], {'price': '9', 'currency': 'USD'}, expected_target=target) is False
    assert quote(editor, expected_context=old['context_token']).status_code == 409


def test_shared_link_and_explicit_empty_editor_context_are_synchronized(editor):
    client, _, item = editor
    rows = product(editor)['listings']
    for row in rows:
        row.update(link='https://shop.example.com/shared-new', region='', variant_hint='')
    response = client.patch('/api/products/' + item['id'], json={'listings': rows})
    response.raise_for_status()
    for row in prices(editor):
        assert row['source_url'] == 'https://shop.example.com/shared-new'
        assert row['region'] == '' and row['variant_hint'] == ''


@pytest.mark.parametrize('bad', [{'variant_hint': 'x' * 201}, {'region': 'x' * 81}, {'variant_hint': None}, {'region': 'US\nCA'}])
def test_invalid_editor_spec_rolls_back_all_rows(editor, bad):
    client, _, item = editor
    before = product(editor)
    rows = before['listings']
    rows[1].update(bad)
    assert client.patch('/api/products/' + item['id'], json={'listings': rows}).status_code == 400
    assert product(editor)['listings'][0]['variant_hint'] == '红色 2件装'
    assert len(product(editor)['listings']) == 2


def test_manual_quote_keeps_original_and_sibling_with_traceable_history(editor):
    _, user, _ = editor
    response = quote(editor)
    response.raise_for_status()
    current, sibling = prices(editor)
    assert current['source'] == 'manual_entry' and current['status'] == 'ok'
    assert current['original_price'] == '20' and current['current_price'] == '15'
    assert current['ratio_percent'] == '75.0' and sibling['current_price'] == ''
    assert product(editor)['listings'][0]['price'] == '20'
    history = storage.get_history(current['id'], user)['items']
    assert len(history) == 1 and history[0]['source'] == 'manual_entry'
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='price_manual_entry'").fetchone()[0] == 1


def test_manual_quote_accepts_different_currency_but_has_no_discount_ratio(editor):
    response = quote(editor, price='99.5', currency='cny')
    response.raise_for_status()
    current = response.json()
    assert current['current_price'] == '99.5' and current['current_currency'] == 'CNY'
    assert current['original_price'] == '20' and current['currency'] == 'USD'
    assert current['ratio_percent'] is None and current['status'] == 'currency_mismatch'


def test_manual_quote_needs_no_source_link_or_hidden_tracking_opt_in(editor):
    client, user, item = editor
    rows = product(editor)['listings']
    rows[0]['link'] = ''
    client.patch('/api/products/' + item['id'], json={'listings': rows}).raise_for_status()
    with backend._connection(write=True) as conn:
        conn.execute('DELETE FROM price_tracking WHERE listing_id=?', (rows[0]['id'],))
    before = prices(editor)[0]
    assert before['configured'] is False and before['revision'] == 0
    response = quote(editor)
    response.raise_for_status()
    current = response.json()
    assert current['source_url'] == '' and current['current_price'] == '15' and current['ratio_percent'] == '75.0'
    assert current['status'] == 'ok' and current['enabled'] is False and current['revision'] == 1
    assert storage.get_history(current['id'], user)['items'][0]['source_url'] == ''


def test_manual_quote_rejects_replay_and_invalidates_old_inflight_capture(editor):
    _, user, _ = editor
    before = prices(editor)[0]
    target = storage.get_target(before['id'], user)
    response = quote(editor)
    response.raise_for_status()
    assert response.json()['revision'] == before['revision'] + 1
    assert quote(editor, expected_context=before['context_token']).status_code == 409
    assert storage.record_attempt(before['id'], {'price': '8', 'currency': 'USD'}, expected_target=target) is False
    assert prices(editor)[0]['current_price'] == '15'


@pytest.mark.parametrize('bad', [{'price': True}, {'price': '-1'}, {'price': '0'}, {'price': 'NaN'}, {'currency': 'US'}, {'expected_context': ''}, {'extra': 'x'}])
def test_manual_quote_invalid_inputs_do_not_change_current_value(editor, bad):
    assert quote(editor, **bad).status_code == 400
    assert prices(editor)[0]['current_price'] == ''


def test_removed_sku_cannot_be_quoted_but_other_sku_and_history_remain(editor):
    client, user, item = editor
    old = prices(editor)[0]
    quote(editor).raise_for_status()
    rows = product(editor)['listings']
    client.patch('/api/products/' + item['id'], json={'listings': rows[1:]}).raise_for_status()
    response = client.post('/api/price-tracking/listings/' + old['id'] + '/manual-price', json={
        'price': '10', 'currency': 'USD', 'expected_context': old['context_token']})
    assert response.status_code == 404
    assert len(prices(editor)) == 1 and prices(editor)[0]['product_code'] == 'BLUE'
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history WHERE listing_id=?', (old['id'],)).fetchone()[0] == 1


def test_manual_quote_requires_current_edit_rights_and_existing_product(editor):
    client, _, item = editor
    row = prices(editor)[0]
    payload = {'price': '10', 'currency': 'USD', 'expected_context': row['context_token']}
    endpoint = '/api/price-tracking/listings/' + row['id'] + '/manual-price'
    client.post('/api/team', json={'name': '其他运营', 'password': 'test-only-password', 'role': 'operator'}).raise_for_status()
    client.post('/api/logout').raise_for_status()
    assert client.post(endpoint, json=payload).status_code == 401
    client.post('/api/login', json={'name': '其他运营', 'password': 'test-only-password'}).raise_for_status()
    assert client.post(endpoint, json=payload).status_code == 403
    client.post('/api/logout').raise_for_status()
    client.post('/api/login', json={'name': 'SKU 编辑管理员', 'password': 'test-only-password'}).raise_for_status()
    client.delete('/api/products/' + item['id']).raise_for_status()
    assert client.post(endpoint, json=payload).status_code == 404


def test_manual_quote_and_history_are_rolled_back_if_audit_fails(editor, monkeypatch):
    def fail(*_):
        raise RuntimeError('simulated audit failure')
    monkeypatch.setattr(backend, '_audit', fail)
    with pytest.raises(RuntimeError, match='audit failure'):
        quote(editor)
    row = prices(editor)[0]
    assert row['current_price'] == ''
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 0
