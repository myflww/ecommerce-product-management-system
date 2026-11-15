"""Current-page file imports exercise a disposable catalog, never live stores."""
import copy
import hashlib
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import page_capture_import as capture
import permissions
import store_import_routes as routes
import store_import_storage as storage

STORE = '700000000000003'
GOODS = '700000000000005'
SHOP = f'https://www.temu.com/mall.html?mall_id={STORE}'
URL = f'https://www.temu.com/candle-g-{GOODS}.html'


def page(gid=GOODS, store=STORE, *, variants=True, owner=True, **fields):
    product = {'goods_id': gid, 'goods_name': 'Fixture candle',
               'image': 'https://img.kwcdn.com/fixture.png'}
    if owner:
        product.update(mall_id=store, mall_name='Fixture shop')
    if variants:
        product.update(skuCount=2, skuList=[
            {'sku_id': '301', 'spec': '红色 / 2件', 'price': '10.54', 'currency': 'USD'},
            {'sku_id': '302', 'spec': '蓝色 / 4件', 'price': '14.21', 'currency': 'USD'}])
    snapshot = {'url': f'https://www.temu.com/candle-g-{gid}.html', 'title': 'Fixture candle | Temu',
                'scripts': [{'text': json.dumps({'products': [product]}, ensure_ascii=False)}]}
    return {'snapshot': snapshot, 'store_url': f'https://www.temu.com/mall.html?mall_id={store}',
            'store_name': 'Fixture shop', 'confirmed_store': True,
            'captured_at': '2026-11-15T03:00:00Z', **fields}


def storefront(count=2):
    links = [{'url': f'https://www.temu.com/item-g-{int(GOODS) - 1 + i}.html',
              'name': 'Fixture ' + str(i), 'image': 'https://img.kwcdn.com/fixture.png',
              'context': 'store-products'} for i in range(count)]
    return {'snapshot': {'url': SHOP, 'title': 'Temu | Fixture shop', 'links': links},
            'store_url': SHOP, 'confirmed_store': True}


def file_bytes(pages=None, **fields):
    return json.dumps({'format': 'midoo-page-capture', 'version': 1,
                       'pages': pages if pages is not None else [page()], **fields},
                      ensure_ascii=False).encode('utf-8')


def error(raw, status=400):
    with pytest.raises(HTTPException) as exc:
        capture.read_file(raw)
    assert exc.value.status_code == status


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    storage.init_storage()
    storage._drafts.clear()
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *a, **k: None)
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',
                     ('owner', 'Fixture owner', 'unused-test-hash', 'admin', backend.now()))
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',
                     ('member', 'Fixture member', 'unused-test-hash', 'operator', backend.now()))
    permissions.init_storage()
    with backend._connection() as conn:
        user = permissions.public_user(conn, conn.execute('SELECT * FROM users WHERE id=?', ('owner',)).fetchone())
    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'green').save(picture, 'PNG')
    image = backend._prepare_image(picture.getvalue(), 'fixture.png')
    calls = []
    monkeypatch.setattr(capture.engine, '_image', lambda url: calls.append(url) or image)
    yield user, image, calls
    storage._drafts.clear()


def preview(catalog, pages=None):
    return capture.create_preview('owner-session', catalog[0], file_bytes(pages))


@pytest.fixture
def client(catalog):
    application = FastAPI()
    application.include_router(routes.router)
    from app import security_headers
    application.middleware('http')(security_headers)
    token = 'fixture-page-capture-session'
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO sessions VALUES(?,?,?)',
                     (hashlib.sha256(token.encode()).hexdigest(), 'owner', expires))
    with TestClient(application, client=('127.0.0.1', 32001)) as value:
        value.cookies.set(backend.COOKIE_NAME, token)
        yield value


def post(client, raw=None, **kwargs):
    return client.post('/api/store-import/page-file',
                       files={'file': ('capture.json', raw if raw is not None else file_bytes(), 'application/json')}, **kwargs)


def count(table):
    with backend._connection() as conn:
        return conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


def test_valid_file_can_begin_with_utf8_bom():
    assert len(capture.read_file(b'\xef\xbb\xbf' + file_bytes())) == 1


@pytest.mark.parametrize('raw', [b'', b'not json', b'\xff', b'[]', b'null', b'{"version":1,"version":1}',
                                  b'{"format":"midoo-page-capture","version":NaN,"pages":[]}'])
def test_invalid_transport_is_rejected(raw):
    error(raw)


def test_oversized_transport_is_rejected_before_json():
    error(b'x' * (capture.MAX_FILE_BYTES + 1), 413)


@pytest.mark.parametrize('fields', [{'version': True}, {'version': 2}, {'format': 'raw-html'},
                                    {'permissions': ['products.import']}, {'membership_verified': True}])
def test_envelope_cannot_smuggle_trust_or_permissions(fields):
    error(file_bytes(**fields))


def test_empty_or_too_many_pages_rejected():
    error(file_bytes([]))
    error(file_bytes([page()] * 21))


@pytest.mark.parametrize('field', ['cookie', 'localStorage', 'accessToken', 'importable', 'price_verified', 'membership_verified'])
def test_private_or_server_owned_fields_rejected_at_page_level(field):
    error(file_bytes([page(**{field: True})]))


@pytest.mark.parametrize('field', ['cookie', 'sessionId', 'password', 'user', 'account', 'token'])
def test_raw_hydration_with_private_fields_is_rejected(field):
    value = page()
    value['snapshot']['scripts'][0]['text'] = json.dumps({'products': [], field: 'private fixture'})
    error(file_bytes([value]))


def test_scripts_must_be_json_not_javascript_assignments():
    value = page()
    value['snapshot']['scripts'][0]['text'] = 'window.rawData = {"products":[]};'
    error(file_bytes([value]))


def test_nested_duplicate_public_fields_rejected():
    value = page()
    value['snapshot']['scripts'][0]['text'] = '{"goods_id":"1","goods_id":"2"}'
    error(file_bytes([value]))


def test_structured_evidence_limits_are_not_silently_truncated():
    value = page()
    value['snapshot']['links'] = [{}] * 401
    error(file_bytes([value]))
    value = page()
    value['snapshot']['variants'] = [{}] * 201
    error(file_bytes([value]))
    value = page()
    value['snapshot']['scripts'] = [{'text': '{}'}] * 21
    error(file_bytes([value]))
    value = page()
    child = {}
    for _ in range(40):
        child = {'offers': child}
    value['snapshot']['scripts'][0]['text'] = json.dumps(child)
    error(file_bytes([value]))


@pytest.mark.parametrize('ack', [False, None, 'true', 1])
def test_operator_must_confirm_store(ack):
    error(file_bytes([page(confirmed_store=ack)]))


def test_preview_preserves_independent_sku_prices_without_writing_catalog(catalog):
    result = preview(catalog)
    item = result['items'][0]
    assert item['can_import'] and item['has_image']
    assert [(v['id'], v['price'], v['currency']) for v in item['variants']] == [
        ('301', '10.54', 'USD'), ('302', '14.21', 'USD')]
    assert result['capture_summary'] == {'pages': 1, 'products': 1, 'partial': 0, 'image_failures': 0, 'source': 'browser_file'}
    assert count('products') == count('product_listings') == count('price_history') == 0
    assert len(catalog[2]) == 1


def test_missing_store_evidence_is_recorded_as_manual_confirmation(catalog):
    result = preview(catalog, [page(owner=False)])
    assert result['items'][0]['can_import']
    assert capture.MANUAL_STORE_WARNING in result['items'][0]['warnings']


def test_missing_skus_keeps_partial_archive_without_fabricating_ids_or_price(catalog):
    result = preview(catalog, [page(variants=False)])
    item = result['items'][0]
    assert item['can_import'] and item['completeness'] == 'partial'
    assert item['variants'][0]['id'] == item['variants'][0]['price'] == ''
    assert len(item['variants']) == 1


def test_product_dom_fallback_needs_title_and_image_but_not_skus(catalog):
    value = page(owner=False, variants=False)
    value['snapshot'].update(scripts=[], product_title='Rendered candle',
                             product_image='https://img.kwcdn.com/rendered.png')
    item = preview(catalog, [value])['items'][0]
    assert item['title'] == 'Rendered candle' and item['can_import']


def test_wrong_root_store_cannot_be_overridden_by_manual_ack(catalog):
    value = page(store='999')
    value['store_url'] = SHOP
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [value])
    assert exc.value.status_code == 400 and '店铺' in exc.value.detail
    assert not catalog[2]


def test_wrong_visible_seller_cannot_be_overridden(catalog):
    value = page(owner=False)
    value['snapshot']['seller'] = {'scope': 'product_seller', 'mall_id': '999'}
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [value])
    assert exc.value.status_code == 400 and not catalog[2]


@pytest.mark.parametrize('url', ['https://www.temu.com/login.html',
                                'https://www.temu.com/search_result.html?search_key=candle',
                                'http://www.temu.com/candle-g-700000000000005.html',
                                'https://evil.example/candle-g-700000000000005.html'])
def test_unsupported_page_is_not_treated_as_store_or_product(catalog, url):
    value = page()
    value['snapshot']['url'] = url
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [value])
    assert exc.value.status_code == 400 and not catalog[2]


def test_login_wall_blocks_even_if_product_evidence_remains(catalog):
    value = page()
    value['snapshot']['text'] = 'Please sign in to continue'
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [value])
    assert exc.value.status_code == 400 and '登录' in exc.value.detail and not catalog[2]


def test_all_pages_validated_before_any_image_network_work(catalog):
    invalid = page()
    invalid['snapshot']['cookie'] = 'should not exist'
    with pytest.raises(HTTPException):
        preview(catalog, [page(), invalid])
    assert not catalog[2] and not storage._drafts


def test_store_cards_import_basic_archives_and_exclude_recommendations(catalog):
    value = storefront()
    value['snapshot']['links'].append({'url': 'https://www.temu.com/item-g-999.html',
                                       'name': 'Recommendation', 'image': 'https://img.kwcdn.com/unrelated.png',
                                       'context': 'recommended for you'})
    result = preview(catalog, [value])
    assert len(result['items']) == 2
    assert all(i['can_import'] and i['completeness'] == 'partial' for i in result['items'])
    assert all(i['variants'][0]['id'] == '' and i['variants'][0]['price'] == '' for i in result['items'])
    assert all(capture.MANUAL_STORE_WARNING in i['warnings'] for i in result['items'])


def test_store_scripts_do_not_invent_cards(catalog):
    value = storefront(0)
    value['snapshot']['scripts'] = page()['snapshot']['scripts']
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [value])
    assert exc.value.status_code == 400 and not catalog[2]


def test_too_many_store_products_rejected_instead_of_truncated(catalog):
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [storefront(21)])
    assert exc.value.status_code == 400 and '20' in exc.value.detail and not catalog[2]


def test_store_page_must_match_manually_confirmed_store(catalog):
    value = storefront()
    value['snapshot']['url'] = 'https://www.temu.com/mall.html?mall_id=999'
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [value])
    assert exc.value.status_code == 400 and not catalog[2]


def test_duplicate_referrer_and_session_urls_do_not_duplicate_products(catalog):
    first, second = page(), page()
    second['snapshot']['url'] += '?_x_sessn_id=private-fixture&refer_page_id=private-fixture'
    result = preview(catalog, [first, second])
    assert len(result['items']) == 1 and len(catalog[2]) == 1
    assert 'private-fixture' not in json.dumps(result)


def test_detail_overrides_store_card_and_keeps_real_skus(catalog):
    value = storefront(2)
    result = preview(catalog, [page(), value])
    detail = next(i for i in result['items'] if i['source_id'] == GOODS)
    assert detail['title'] == 'Fixture candle'
    assert {v['id'] for v in detail['variants']} == {'301', '302'}


def test_distinct_sku_observations_merge_and_conflicting_prices_clear(catalog):
    first, second = page(), page()
    data = json.loads(second['snapshot']['scripts'][0]['text'])
    data['products'][0]['skuList'][0]['price'] = '99.12'
    data['products'][0]['skuList'].append({'sku_id': '303', 'spec': '黄色', 'price': '6.01', 'currency': 'USD'})
    second['snapshot']['scripts'][0]['text'] = json.dumps(data)
    third = page()  # Must not resurrect the conflicted price on a later copy.
    item = preview(catalog, [first, second, third])['items'][0]
    variants = {v['id']: v for v in item['variants']}
    assert set(variants) == {'301', '302', '303'}
    assert variants['301']['price'] == '' and variants['301']['currency'] == ''
    assert variants['302']['price'] == '14.21'
    assert 'variant_price_conflict' in item['warnings'] and item['completeness'] == 'partial'


@pytest.mark.parametrize('conflict_position', [0, 1, 2])
def test_parser_conflict_evidence_remains_sticky_across_observation_orders(catalog, conflict_position):
    conflicting = page()
    document = json.loads(conflicting['snapshot']['scripts'][0]['text'])
    document['products'][0]['skuList'].append(
        {'sku_id': '301', 'spec': '红色 / 2件', 'price': '12', 'currency': 'USD'})
    conflicting['snapshot']['scripts'][0]['text'] = json.dumps(document)
    pages = [page(), page()]
    pages.insert(conflict_position, conflicting)
    parsed = capture.collect_items(capture.read_file(file_bytes(pages)))[0]
    variant = next(v for v in parsed['variants'] if v['public_variant_id'] == '301')
    assert variant['price'] is None and variant['currency'] == ''
    assert variant['price_verified'] is False
    assert 'variant_price_conflict' in variant['warnings']
    item = preview(catalog, pages)['items'][0]
    normalized = next(v for v in item['variants'] if v['id'] == '301')
    assert normalized['price'] == normalized['currency'] == ''
    assert 'variant_price_conflict' in item['warnings']


def test_same_goods_cannot_be_mapped_to_two_shops_in_one_file(catalog):
    with pytest.raises(HTTPException) as exc:
        preview(catalog, [page(), page(store='999')])
    assert exc.value.status_code == 400 and '不同店铺' in exc.value.detail and not catalog[2]


def test_image_download_failure_is_not_reported_as_importable(catalog, monkeypatch):
    monkeypatch.setattr(capture.engine, '_image', lambda _: (_ for _ in ()).throw(ValueError('fixture failure')))
    result = preview(catalog)
    assert not result['items'][0]['can_import']
    assert 'main_image_download_failed' in result['items'][0]['warnings']
    assert result['capture_summary']['image_failures'] == 1


def test_image_batch_budget_stops_starting_downloads_and_keeps_partial_preview(catalog, monkeypatch):
    clock, calls = [0], []
    monkeypatch.setattr(capture.time, 'monotonic', lambda: clock[0])
    def image(url):
        calls.append(url)
        clock[0] += 46
        return catalog[1]
    monkeypatch.setattr(capture.engine, '_image', image)
    result = preview(catalog, [page(gid=str(int(GOODS) + i)) for i in range(3)])
    assert len(calls) == 2
    assert result['capture_summary']['image_failures'] == 1
    assert [i['can_import'] for i in result['items']] == [True, True, False]
    assert any('时间已用尽' in w for w in result['items'][-1]['warnings'])


def test_uploaded_stream_read_is_bounded_and_closed(catalog):
    class Probe(io.BytesIO):
        seen = []
        def read(self, limit=-1):
            self.seen.append(limit)
            return super().read(limit)
    stream = Probe(file_bytes())
    file = UploadFile(stream, filename='capture.json')
    result = capture.upload_preview('owner-session', catalog[0], file)
    assert result['id'] and stream.closed
    assert stream.seen == [capture.MAX_FILE_BYTES + 1]


def test_file_route_uses_real_session_and_existing_commit_flow(client, catalog):
    response = post(client)
    assert response.status_code == 200
    draft = response.json()
    assert draft['items'][0]['can_import'] and count('products') == 0
    choice = [{'item_id': draft['items'][0]['id'], 'action': 'new'}]
    result = client.post(f'/api/store-import/previews/{draft["id"]}/commit',
                         json={'selections': choice, 'mark_listed': False})
    assert result.status_code == 200 and result.json()['created'] == 1
    assert count('products') == 1 and count('product_listings') == 2 and count('price_history') == 2
    repeated = client.post(f'/api/store-import/previews/{draft["id"]}/commit',
                           json={'selections': choice, 'mark_listed': False})
    assert repeated.json() == result.json() and count('products') == 1


def test_file_route_rejects_unauthenticated_and_crosssite(client, catalog):
    assert post(client, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    client.cookies.clear()
    assert post(client).status_code == 401
    assert not catalog[2]


def test_file_route_rejects_wrong_origin_even_without_fetch_site(client, catalog):
    assert post(client, headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert not catalog[2]


def test_another_session_cannot_read_file_preview(client, catalog):
    result = post(client).json()
    token = 'other-fixture-session'
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),
                     'owner', (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    client.cookies.set(backend.COOKIE_NAME, token)
    assert client.get(f'/api/store-import/previews/{result["id"]}').status_code == 404


def test_lan_request_cannot_create_host_owned_file_preview(catalog):
    application = FastAPI()
    application.include_router(routes.router)
    application.dependency_overrides[backend.require_user] = lambda: catalog[0]
    # Keep the test focused on local-only owner binding, not auth fixtures.
    original = backend._user
    backend._user = lambda _: catalog[0]
    try:
        with TestClient(application, client=('192.168.1.50', 32001)) as value:
            assert post(value).status_code == 403
        assert not catalog[2]
    finally:
        backend._user = original


def test_revoked_import_permission_blocks_before_image_work(catalog):
    restricted = {**catalog[0], 'id': 'member', 'role': 'operator', 'permissions': []}
    with pytest.raises(HTTPException) as exc:
        capture.create_preview('owner-session', restricted, file_bytes())
    assert exc.value.status_code == 403 and not catalog[2]


def test_download_route_is_authenticated_and_uses_only_reviewed_zip(client, monkeypatch):
    calls = []
    from fastapi import Response
    monkeypatch.setattr(routes.page_capture_package, 'extension_response',
                        lambda: calls.append(True) or Response(b'PK-fixture', media_type='application/zip'))
    result = client.get('/api/store-import/page-extension.zip')
    assert result.status_code == 200 and result.content == b'PK-fixture'
    assert result.headers['content-type'] == 'application/zip' and len(calls) == 1
    client.cookies.clear()
    assert client.get('/api/store-import/page-extension.zip').status_code == 401
    assert len(calls) == 1


def test_download_route_rejects_lan_even_if_user_authorized(catalog, monkeypatch):
    application = FastAPI()
    application.include_router(routes.router)
    application.dependency_overrides[backend.require_user] = lambda: catalog[0]
    monkeypatch.setattr(backend, '_user', lambda _: catalog[0])
    calls = []
    monkeypatch.setattr(routes.page_capture_package, 'extension_response', lambda: calls.append(True))
    with TestClient(application, client=('192.168.1.50', 32001)) as value:
        assert value.get('/api/store-import/page-extension.zip').status_code == 403
    assert not calls


def test_revoked_member_permission_blocks_upload_download_and_existing_preview(client, catalog, monkeypatch):
    token = 'member-fixture-session'
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(),
                     'member', (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    client.cookies.set(backend.COOKIE_NAME, token)
    draft = post(client).json()
    assert draft['id']
    before = len(catalog[2])
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO permission_overrides VALUES(?,?,?)', ('member', 'products.import', 0))
    assert post(client).status_code == 403
    assert client.get('/api/store-import/page-extension.zip').status_code == 403
    assert client.get(f'/api/store-import/previews/{draft["id"]}').status_code == 403
    result = client.post(f'/api/store-import/previews/{draft["id"]}/commit',
                         json={'selections': [{'item_id': draft['items'][0]['id'], 'action': 'new'}]})
    assert result.status_code == 403 and count('products') == 0
    assert len(catalog[2]) == before


def test_disabled_account_session_cannot_use_file_import_or_preview(client, catalog):
    draft = post(client).json()
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO account_access(user_id,disabled) VALUES(?,?)', ('owner', 1))
    assert post(client).status_code == 401
    assert client.get(f'/api/store-import/previews/{draft["id"]}').status_code == 401
    assert count('products') == 0
