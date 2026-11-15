"""Isolated collector contracts. Fake Playwright only; no real pages/profiles."""
import copy
import importlib.util
import json
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import store_import_engine as engine
IMAGE_FETCH = engine._image

STORE = "700000000000003"
GOODS = "700000000000005"
URL = "https://www.temu.com/us-zh-Hans/candle-g-" + GOODS + ".html"
SHOP = "https://www.temu.com/mall.html?mall_id=" + STORE


def data(*, variants=True, store=STORE):
    result = {"goodsId": GOODS, "mallId": store, "goodsName": "Wax candle", "image": "https://img.kwcdn.com/candle.jpg"}
    if variants:
        result.update(skuCount=1, skuList=[{"skuId": "1001", "spec": "Red, 2 pieces", "price": "9.5", "currency": "USD"}])
    return result


def snapshot(*, url=URL, variants=True, store=STORE):
    return {"url": url, "title": "Wax candle", "text": "", "scripts": [{"text": json.dumps(data(variants=variants, store=store))}]}


def job():
    return {"id": "job-one", "state": "discovered", "message": "", "store_id": STORE,
            "store_name": "Fixture Candle Shop", "store_url": SHOP,
            "candidates": [{"product_id": GOODS, "url": URL, "name": "Wax candle", "image": "https://img.kwcdn.com/candle.jpg"}],
            "items": [], "warnings": [], "progress": 0, "draft_id": "",
            "_owner": "owner", "_user": {"id": "user", "name": "Tester", "role": "admin"},
            "_created": time.monotonic(), "_commands": queue.Queue(maxsize=1),
            "_stop": threading.Event(), "_done": threading.Event(), "_seen": set(), "_deadline": 0}


class FakePage:
    def __init__(self, capture=None):
        self.capture = capture or snapshot()
        self.url = self.capture["url"]
        self.closed = False
        self.navigations = []
        self.reads = []
        self.events = {}
        self.mouse = SimpleNamespace(wheel=lambda *args: None)

    def goto(self, url, **kwargs):
        self.navigations.append(url)
        self.url = self.capture["url"]

    def locator(self, selector):
        assert selector == "body"
        return self

    def evaluate(self, js, **kwargs):
        self.reads.append((js, kwargs))
        return copy.deepcopy(self.capture)

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed

    def on(self, event, callback):
        self.events[event] = callback

    def wait_for_timeout(self, ms):
        time.sleep(.001)


class FakeContext:
    def __init__(self, captures):
        self.captures = list(captures)
        self.pages = []

    def new_page(self):
        page = FakePage(self.captures.pop(0))
        self.pages.append(page)
        return page


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    engine._jobs.clear()
    engine.init_engine(tmp_path)
    monkeypatch.setattr(engine.browser_support, "_network_guard", lambda url: None)
    if hasattr(engine, '_guard'):
        monkeypatch.setattr(engine, '_guard', lambda url: None)
    image_calls, previews = [], []
    monkeypatch.setattr(engine, "_image", lambda url: image_calls.append(url) or {"data": b"verified image"})

    def preview(owner, user, items, prepared):
        previews.append({"owner": owner, "user": user, "items": copy.deepcopy(items), "prepared": prepared})
        return {"id": "preview-one", "items": [{"id": "normalized-" + item['product_id'], "source_id": item['product_id']} for item in items]}

    monkeypatch.setitem(sys.modules, "store_import_storage", SimpleNamespace(create_preview=preview))
    yield SimpleNamespace(images=image_calls, previews=previews)
    for value in engine._jobs.values():
        value["_stop"].set()
        value["_done"].set()
    engine._jobs.clear()


def test_read_uses_exported_snapshot_and_bounded_body_acquisition():
    page = FakePage()
    actual = engine._read(page)
    assert actual["url"] == URL
    assert engine.parser.SNAPSHOT_JS in page.reads[0][0]
    assert page.reads[0][1]["timeout"] == 12000


def test_owned_job_cannot_be_read_or_controlled_by_different_session():
    value = job()
    engine._jobs[value["id"]] = value
    for action in (lambda: engine.snapshot("another-owner", value["id"]),
                   lambda: engine.command("another-owner", value["id"], "discover"),
                   lambda: engine.close("another-owner", value["id"])):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 404


@pytest.mark.parametrize("ids", [None, [], ["unknown"], [GOODS, GOODS], [GOODS] * 21, [1]])
def test_collect_only_server_discovered_bounded_unique_product_ids(ids):
    value = job()
    engine._jobs[value["id"]] = value
    with pytest.raises(HTTPException) as exc:
        engine.command("owner", value["id"], "collect", ids)
    assert exc.value.status_code == 400
    assert value["_commands"].empty()


def test_busy_job_rejects_concurrent_repeated_commands():
    value = job()
    engine._jobs[value["id"]] = value
    engine.command("owner", value["id"], "collect", [GOODS])
    with pytest.raises(HTTPException) as exc:
        engine.command("owner", value["id"], "collect", [GOODS])
    assert exc.value.status_code == 409
    assert value["_commands"].qsize() == 1


def test_collect_stages_verified_product_price_without_catalog_write(isolated):
    value = job()
    context = FakeContext([snapshot()])
    engine._collect(context, value, [GOODS])
    assert value["draft_id"] == "preview-one"
    assert value["state"] == "preview"
    assert len(isolated.previews) == 1
    item = isolated.previews[0]["items"][0]
    assert item["membership_verified"] is True
    assert item["variants"][0]["price"] == "9.5"
    assert item["variants"][0]["price_verified"] is True
    assert context.pages[0].closed is True


def test_foreign_shop_detail_does_not_fetch_image_or_create_preview(isolated):
    value = job()
    engine._collect(FakeContext([snapshot(store="999")]), value, [GOODS])
    assert not isolated.images and not isolated.previews
    assert value["items"][0]["importable"] is False


def test_watchdog_only_requests_this_jobs_driver_stop(monkeypatch):
    value = job()
    value["_deadline"] = time.monotonic() - 1
    manager, calls = object(), []
    monkeypatch.setattr(engine.browser_support, "_request_driver_stop", lambda target: calls.append(target))
    worker = threading.Thread(target=engine._watch, args=(value, manager), daemon=True)
    worker.start()
    worker.join(1.5)
    value["_done"].set()
    assert not worker.is_alive()
    assert calls == [manager]
    assert value["_stop"].is_set()


def test_profile_uses_app_user_store_channel_not_owner_cookie(tmp_path):
    one = engine._profile("user-a", STORE, "msedge")
    assert one == engine._profile("user-a", STORE, "msedge")
    assert one != engine._profile("user-b", STORE, "msedge")
    assert one != engine._profile("user-a", "99", "msedge")
    assert one.is_relative_to(tmp_path)
    assert STORE not in str(one) and "user-a" not in str(one)


def test_image_failure_is_partial_and_not_retained_as_importable(monkeypatch, isolated):
    value = job()
    monkeypatch.setattr(engine, "_image", lambda url: (_ for _ in ()).throw(ValueError("decode failed")))
    engine._collect(FakeContext([snapshot()]), value, [GOODS])
    assert value["items"][0]["importable"] is False
    assert "main_image_download_failed" in value["items"][0]["warnings"]
    assert not isolated.previews


def test_close_remains_available_for_own_job_without_product_write_permission():
    value = job()
    engine._jobs[value["id"]] = value
    result = engine.close("owner", value["id"])
    assert value["_stop"].is_set()
    assert result["state"] == "closing"


def test_login_wall_must_keep_page_open_for_user_to_complete_verification():
    value = job()
    context = FakeContext([snapshot(url="https://www.temu.com/login.html")])
    engine._collect(context, value, [GOODS])
    assert value["state"] == "attention"
    assert context.pages[0].closed is False


def test_valid_shop_product_without_variants_can_preview_missing_spec_draft(isolated):
    value = job()
    engine._collect(FakeContext([snapshot(variants=False)]), value, [GOODS])
    assert len(isolated.previews) == 1
    assert isolated.previews[0]["items"][0]["variants"] == []


def test_preview_then_commit_cursor_advances_only_confirmed_rows(isolated):
    value = job()
    engine._jobs[value['id']] = value
    engine._collect(FakeContext([snapshot()]), value, [GOODS])
    assert not value['_seen']
    engine.committed('another-owner', 'preview-one', {'items': [{'item_id': 'normalized-' + GOODS}]})
    assert not value['_seen']
    engine.committed('owner', 'preview-one', {'items': [{'item_id': 'unknown'}]})
    assert not value['_seen']
    engine.committed('owner', 'preview-one', {'items': [{'item_id': 'normalized-' + GOODS}]})
    assert value['_seen'] == {GOODS}


def test_login_completion_retries_same_retained_product_tab(isolated):
    value = job()
    context = FakeContext([snapshot(url='https://www.temu.com/login.html')])
    engine._collect(context, value, [GOODS])
    retained = context.pages[0]
    retained.capture = snapshot()
    retained.url = URL
    engine._collect(context, value, [GOODS])
    assert len(context.pages) == 1
    assert len(retained.navigations) == 1
    assert retained.closed
    assert value['draft_id'] == 'preview-one'
    assert '_attention_page' not in value


def image_fixture(monkeypatch, responses):
    monkeypatch.setattr(engine, '_image', IMAGE_FETCH)
    calls, closed, resolution = [], [], []
    def resolve(host, port):
        resolution.append((host, port))
        return ['1.1.1.1']
    monkeypatch.setattr(engine.price_capture, '_resolve_public_addresses', resolve)
    class Connection:
        def __init__(self, host, address, port):
            self.host, self.address, self.port = host, address, port
            self.response = responses.pop(0)
        def request(self, method, path, headers):
            calls.append((self.host, self.address, self.port, method, path, headers))
        def getresponse(self):
            return self.response
        def close(self):
            closed.append(self.host)
    monkeypatch.setattr(engine.price_capture, '_PinnedHTTPSConnection', Connection)
    return SimpleNamespace(calls=calls, closed=closed, resolution=resolution)


def response(status=200, headers=None, chunks=None):
    chunks = list(chunks or [])
    return SimpleNamespace(status=status, getheaders=lambda: list((headers or {'Content-Type': 'image/png'}).items()), read=lambda n: chunks.pop(0) if chunks else b'')


def test_image_redirect_resolves_and_pins_each_destination_and_omits_cookies(monkeypatch):
    net = image_fixture(monkeypatch, [response(302, {'Location': 'https://cdn.example.com/main.png'}), response(chunks=[b'verified-image'])])
    monkeypatch.setattr(engine.backend, '_prepare_image', lambda content, name: {'data': content})
    assert engine._image('https://img.example.com/a.png') == {'data': b'verified-image'}
    assert net.resolution == [('img.example.com', 443), ('cdn.example.com', 443)]
    assert [call[1] for call in net.calls] == ['1.1.1.1', '1.1.1.1']
    assert net.closed == ['img.example.com', 'cdn.example.com']
    assert all(not any(k.lower() in {'cookie', 'authorization'} for k in call[-1]) for call in net.calls)


def test_image_redirect_to_private_address_is_blocked_before_second_connection(monkeypatch):
    net = image_fixture(monkeypatch, [response(302, {'Location': 'https://127.0.0.1/private.png'})])
    with pytest.raises(engine.price_capture.PriceCaptureError):
        engine._image('https://img.example.com/a.png')
    assert len(net.calls) == 1
    assert net.closed == ['img.example.com']


@pytest.mark.parametrize('headers,chunks', [
    ({'Content-Type': 'text/html'}, [b'not-image']),
    ({'Content-Type': 'image/png', 'Content-Encoding': 'gzip'}, [b'compressed']),
    ({'Content-Type': 'image/png', 'Content-Length': str(engine.MAX_IMAGE_BYTES + 1)}, []),
    ({'Content-Type': 'image/png'}, [b'x' * (engine.MAX_IMAGE_BYTES + 1)]),
])
def test_image_rejects_wrong_type_encoding_or_oversized_data(monkeypatch, headers, chunks):
    net = image_fixture(monkeypatch, [response(headers=headers, chunks=chunks)])
    with pytest.raises(ValueError):
        engine._image('https://img.example.com/a.png')
    assert len(net.closed) == 1


def test_image_redirect_chain_cannot_start_another_request_after_total_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(engine, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    first = response(302, {'Location': 'https://cdn.example.com/main.png'})
    old_headers = first.getheaders
    def headers():
        clock[0] = 30.0
        return old_headers()
    first.getheaders = headers
    net = image_fixture(monkeypatch, [first, response(chunks=[b'data'])])
    with pytest.raises(ValueError):
        engine._image('https://img.example.com/a.png')
    assert len(net.calls) == 1


@pytest.fixture
def route_setup(monkeypatch, tmp_path):
    """Use the real permission service and disposable DB; browser stays fake."""
    import backend
    import permissions
    from fastapi import FastAPI
    backend.init_storage(tmp_path)
    permissions.init_storage()
    user = {'id': 'operator', 'name': 'Operator', 'role': 'operator',
            'permissions': ['products.import', 'products.create']}
    monkeypatch.setattr(backend, '_user', lambda request: user)
    spec = importlib.util.spec_from_file_location('isolated_store_import_routes', SOURCE / 'store_import_routes.py')
    routes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routes)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[backend.require_user] = lambda: user
    return app, routes, user


def test_start_invalid_frontend_url_returns_400_not_500(route_setup):
    from fastapi.testclient import TestClient
    app, routes, user = route_setup
    with TestClient(app, client=('127.0.0.1', 32001), raise_server_exceptions=False) as client:
        result = client.post('/api/store-import/browser/start', json={'url': 'https://evil.example.com/mall.html?mall_id=' + STORE})
    assert result.status_code == 400


@pytest.mark.parametrize('runtime_available', [True, False])
def test_runtime_readiness_does_not_claim_verified_temu_capture(route_setup, monkeypatch, runtime_available):
    from fastapi.testclient import TestClient
    app, routes, user = route_setup
    monkeypatch.setattr(routes.browser_price_engine, 'available', lambda: {
        'available': runtime_available, 'browser': 'Fixture Browser', 'note': 'fixture runtime only'})
    with TestClient(app, client=('127.0.0.1', 32001)) as client:
        result = client.get('/api/store-import/capabilities')
    assert result.status_code == 200
    capability = result.json()
    assert capability['available'] is runtime_available
    assert capability['can_use'] is True
    assert capability['experimental'] is True
    assert capability['live_capture_verified'] is False
    assert 'temu_login_redirect_unresolved' in capability['limitations']
    assert 'live_variant_completeness_unverified' in capability['limitations']
    assert '登录' in capability['note'] and '尚未验收通过' in capability['note']
    assert capability['runtime_note'] == 'fixture runtime only'


def test_lan_user_cannot_start_or_control_host_browser(route_setup, monkeypatch):
    from fastapi.testclient import TestClient
    app, routes, user = route_setup
    calls = []
    monkeypatch.setattr(routes.engine, 'start', lambda *args: calls.append(args))
    with TestClient(app, client=('192.168.1.50', 32001)) as client:
        results = [client.post('/api/store-import/browser/start', json={'url': SHOP}),
                   client.get('/api/store-import/browser/job-one'),
                   client.post('/api/store-import/browser/job-one/discover'),
                   client.post('/api/store-import/browser/job-one/collect', json={'ids': [GOODS]}),
                   client.post('/api/store-import/browser/job-one/close')]
    assert [r.status_code for r in results] == [403] * 5
    assert not calls


def test_live_permission_revocation_blocks_collect_but_allows_own_window_close(route_setup):
    from fastapi.testclient import TestClient
    import hashlib
    app, routes, user = route_setup
    value = job()
    value['_owner'] = hashlib.sha256(b'').hexdigest()
    engine._jobs[value['id']] = value
    user['permissions'] = []
    with TestClient(app, client=('127.0.0.1', 32001)) as client:
        blocked = client.post('/api/store-import/browser/job-one/collect', json={'ids': [GOODS]})
        closed = client.post('/api/store-import/browser/job-one/close')
    assert blocked.status_code == 403
    assert closed.status_code == 200
    assert value['_stop'].is_set()
    assert value['_commands'].empty()


def test_commit_advances_cursor_only_after_storage_success(route_setup, monkeypatch):
    from fastapi.testclient import TestClient
    app, routes, user = route_setup
    calls = []
    def reject(*args, **kwargs):
        raise HTTPException(409, 'product changed')
    monkeypatch.setattr(routes.storage, 'commit_preview', reject, raising=False)
    monkeypatch.setattr(routes.engine, 'committed', lambda *args: calls.append(args))
    with TestClient(app, client=('127.0.0.1', 32001)) as client:
        failed = client.post('/api/store-import/previews/draft-one/commit', json={'selections': []})
        assert failed.status_code == 409
        assert not calls
        monkeypatch.setattr(routes.storage, 'commit_preview', lambda *a, **kw: {'items': [{'item_id': 'one'}]})
        success = client.post('/api/store-import/previews/draft-one/commit', json={'selections': []})
    assert success.status_code == 200
    assert len(calls) == 1 and calls[0][1] == 'draft-one'


def test_mutation_payload_cannot_supply_store_identity_prices_or_permissions(route_setup):
    from fastapi.testclient import TestClient
    app, routes, user = route_setup
    with TestClient(app, client=('127.0.0.1', 32001)) as client:
        requests = [
            client.post('/api/store-import/browser/start', json={'url': SHOP, 'permissions': ['products.import']}),
            client.post('/api/store-import/browser/job-one/collect', json={'ids': [GOODS], 'store_id': '999'}),
            client.post('/api/store-import/previews/draft-one/commit', json={'selections': [], 'items': [{'price': '1'}]}),
            client.post('/api/store-import/previews/draft-one/commit', json={'selections': [], 'mark_listed': 'true'}),
        ]
    assert [r.status_code for r in requests] == [400] * 4


def test_actual_shared_security_middleware_rejects_crosssite_store_import_writes(route_setup):
    from fastapi.testclient import TestClient
    from app import security_headers
    app, routes, user = route_setup
    app.middleware('http')(security_headers)
    with TestClient(app, client=('127.0.0.1', 32001)) as client:
        results = [client.post('/api/store-import/browser/start', json={'url': SHOP}, headers={'Sec-Fetch-Site': 'cross-site'}),
                   client.post('/api/store-import/previews/draft-one/commit', json={'selections': []}, headers={'Sec-Fetch-Site': 'cross-site'})]
    assert [r.status_code for r in results] == [403, 403]


def test_parser_to_storage_twenty_product_contract_preserves_text_and_omits_invalid_prices():
    spec = importlib.util.spec_from_file_location('isolated_actual_store_storage', SOURCE / 'store_import_storage.py')
    storage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(storage)
    normalized = []
    for index in range(20):
        raw = data()
        raw['goodsId'] = str(index + 1)
        raw['goodsName'] = ('A\nlong title ' * 60)[:550]
        raw['mallName'] = 'Store\n' + 'S' * 150
        raw['skuList'][0]['spec'] = 'Spec\n' + 'X' * 495
        if index == 0:
            raw['skuList'][0]['price'] = 0
        captured = {'url': f'https://www.temu.com/x-g-{index + 1}.html', 'scripts': [{'text': json.dumps(raw)}]}
        parsed = engine.parser.parse_product_snapshot(captured, STORE)
        normalized.append(storage._normalize(parsed))
    assert len(normalized) == 20
    assert all(len(item['title']) <= 500 for item in normalized)
    assert all(len(item['store_name']) <= 160 for item in normalized)
    assert all(len(item['variants'][0]['spec']) <= 200 for item in normalized)
    assert normalized[0]['variants'][0]['price'] == ''
    assert normalized[1]['variants'][0]['price'] == '9.5'
    assert normalized[1]['variants'][0]['full_spec'].startswith('Spec ')


def test_stopped_collection_does_not_create_preview(isolated):
    value = job()
    value['_stop'].set()
    engine._collect(FakeContext([snapshot()]), value, [GOODS])
    assert not isolated.previews
