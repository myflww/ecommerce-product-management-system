"""Browser lifecycle contracts using fake Playwright; no actual UI or network."""
import copy
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import browser_price_engine as engine
import browser_price_parser as parser
import price_capture


TARGET = {'listing_id': 'listing-one', 'source_url': 'https://www.temu.com/us/g-12345.html?secret=private-query',
          'currency': 'CNY', 'region': '美国站', 'variant_hint': '套装D', 'revision': 3,
          '_profile_user': 'stable-user-one', 'platform': 'Temu', 'store': '测试店铺'}


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url = 'about:blank'
        self.closed = False
        self.events = {}
        self.snapshot = {'url': 'https://attacker.test/fake', 'title': '商品A', 'text': 'Price US$12.90', 'blocks': []}
        self.fail_goto = False

    def check(self):
        assert threading.get_ident() == self.context.worker

    def on(self, event, callback):
        self.check(); self.events[event] = callback

    def goto(self, url, **kwargs):
        self.check(); self.url = url
        self.context.goto_options = kwargs
        if self.fail_goto:
            raise RuntimeError('secret upstream message must not escape')
        return SimpleNamespace(status=200)

    def evaluate(self, js):
        self.check()
        if getattr(self, 'block_read', None) is not None:
            self.block_read.wait(2)
        return copy.deepcopy(self.snapshot)

    def is_closed(self):
        self.check(); return self.closed

    def wait_for_timeout(self, ms):
        self.check(); time.sleep(0.002)


class FakeContext:
    def __init__(self, worker):
        self.worker = worker
        self.events = {}
        self.page = FakePage(self)
        self.closed = False
        self.pages = []
        self.browser = None

    def check(self):
        assert threading.get_ident() == self.worker

    def set_default_timeout(self, value):
        self.check(); self.timeout = value

    def route(self, pattern, callback):
        self.check(); self.router = callback

    def route_web_socket(self, pattern, callback):
        self.check(); self.ws_router = callback

    def new_page(self):
        self.check(); self.pages.append(self.page); return self.page

    def on(self, event, callback):
        self.check(); self.events[event] = callback

    def close(self):
        self.check(); self.closed = True; self.page.closed = True
        if self.browser is not None:
            self.browser.closed = True


class FakeBrowser:
    def __init__(self, worker):
        self.worker = worker
        self.context = FakeContext(worker)
        self.context.browser = self
        self.closed = False

    def new_context(self, **kwargs):
        assert threading.get_ident() == self.worker
        self.context_options = kwargs
        return self.context

    def is_connected(self):
        assert threading.get_ident() == self.worker
        return not self.closed

    def close(self):
        assert threading.get_ident() == self.worker
        self.closed = True


class FakeManager:
    def __init__(self):
        self.calls = []
        self.browser = None
        self.exited = False
        self.fail_channels = set()

    def __enter__(self):
        self.worker = threading.get_ident()
        return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=self.launch))

    def launch(self, **kwargs):
        assert threading.get_ident() == self.worker
        self.calls.append(kwargs)
        if kwargs['channel'] in self.fail_channels:
            raise RuntimeError('provider/private details must be hidden')
        self.browser = FakeBrowser(self.worker)
        self.browser.context_options = kwargs
        return self.browser.context

    def __exit__(self, *args):
        assert threading.get_ident() == self.worker
        # Context and browser must close before stopping the driver.
        if self.browser:
            assert self.browser.closed and self.browser.context.closed
        self.exited = True


def await_state(job, states, timeout=2):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        value = engine.snapshot('owner-one', job['id'])
        if value['state'] in states:
            return value
        time.sleep(0.005)
    raise AssertionError(engine.snapshot('owner-one', job['id']))


@pytest.fixture
def fake(monkeypatch, tmp_path):
    engine.shutdown()
    engine.init_engine(tmp_path / 'data')
    with engine._lock:
        engine._jobs.clear(); engine._active_id = None
    manager = FakeManager()
    monkeypatch.setattr(engine, '_runtime_ready', lambda: True)
    monkeypatch.setattr(engine, '_browsers', lambda: [('msedge', 'Microsoft Edge'), ('chrome', 'Google Chrome')])
    monkeypatch.setattr(engine, '_playwright_factory', lambda: manager)
    monkeypatch.setattr(price_capture, '_resolve_public_addresses', lambda *args: ['8.8.8.8'])
    value = {'candidates': [{'id': 'quote-one', 'price': '12.90', 'currency': 'USD',
              'evidence': 'Price US$12.90', 'currency_inferred': False}],
             'needs_action': False, 'message': '核对后确认', 'notices': ['请确认套装D']}
    monkeypatch.setattr(parser, 'parse_snapshot', lambda snapshot, target: copy.deepcopy(value))
    manager.parsed = value
    yield manager
    engine.shutdown()
    assert not engine._active_id


def test_launch_is_isolated_visible_sandboxed_and_all_playwright_calls_stay_on_worker(fake):
    job = engine.start('owner-one', TARGET)
    ready = await_state(job, {'ready'})
    assert ready['browser'] == 'Microsoft Edge'
    assert 'secret=' not in ready['url']
    launch = fake.calls[0]
    assert launch['headless'] is False and launch['chromium_sandbox'] is True
    assert launch['user_data_dir'].startswith(str(engine._directory / 'browser-price-profiles'))
    assert 'ignore_default_args' not in launch and 'args' not in launch
    options = fake.browser.context_options
    assert options['service_workers'] == 'block'
    assert options['accept_downloads'] is False and options['ignore_https_errors'] is False
    assert options['permissions'] == []
    assert fake.worker != threading.get_ident()
    engine.close('owner-one', job['id'])
    assert engine._jobs[job['id']]['_done'].wait(2)
    assert fake.exited and fake.browser.closed and fake.browser.context.closed


def test_no_browser_or_runtime_never_claims_success(monkeypatch):
    monkeypatch.setattr(engine, '_runtime_ready', lambda: False)
    assert not engine.available()['available']
    with pytest.raises(HTTPException) as exc:
        engine.start('x', TARGET)
    assert exc.value.status_code == 503
    monkeypatch.setattr(engine, '_runtime_ready', lambda: True)
    monkeypatch.setattr(engine, '_browsers', lambda: [])
    assert not engine.available()['available']


def test_missing_bundled_node_sdk_error_returns_unavailable_and_503(monkeypatch):
    from playwright._impl import _driver
    from playwright.sync_api import Error as PlaywrightError
    def missing_node():
        raise PlaywrightError('This installation does not include Node.js')
    monkeypatch.setattr(_driver, 'compute_driver_executable', missing_node)
    monkeypatch.setattr(engine, '_browsers', lambda: [('msedge', 'Microsoft Edge')])
    assert engine.available()['available'] is False
    with pytest.raises(HTTPException) as exc:
        engine.start('owner-one', TARGET)
    assert exc.value.status_code == 503


@pytest.mark.parametrize('url', ['http://127.0.0.1/admin', 'file:///C:/Users/file.txt', 'https://user:password@example.com', 'http://localhost', 'https://example.com:8765'])
def test_invalid_targets_are_rejected_before_launch(fake, url):
    with pytest.raises(HTTPException) as exc:
        engine.start('owner-one', {**TARGET, 'source_url': url})
    assert exc.value.status_code == 400
    assert not fake.calls


def test_busy_cannot_be_taken_over_even_by_same_owner(fake):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    for owner in ('owner-one', 'owner-two'):
        with pytest.raises(HTTPException) as exc:
            engine.start(owner, TARGET)
        assert exc.value.status_code == 409
    for action in (engine.snapshot, engine.close, engine.read):
        with pytest.raises(HTTPException) as exc:
            action('owner-two', job['id'])
        assert exc.value.status_code == 404


def test_preview_has_evidence_but_no_internal_data_and_is_not_mutable(fake):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id'])
    preview = await_state(job, {'preview'})
    quote = preview['candidates'][0]
    assert quote['price'] == '12.90' and quote['currency'] == 'USD'
    assert quote['source'] == 'browser_confirmed' and quote['evidence']
    assert 'url' not in quote and 'private-query' not in str(preview)
    assert all(not key.startswith('_') for key in preview)
    quote['price'] = '100000'
    assert engine.snapshot('owner-one', job['id'])['candidates'][0]['price'] == '12.90'
    old, actual = engine.candidate('owner-one', job['id'], 'quote-one')
    assert old == TARGET and actual['price'] == '12.90'
    old['currency'] = 'ZZZ'
    assert engine._jobs[job['id']]['_target']['currency'] == 'CNY'


def test_changed_live_price_is_rejected_before_confirmation(fake):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id']); await_state(job, {'preview'})
    fake.parsed['candidates'][0].update(price='15.90', evidence='Price US$15.90')
    with pytest.raises(HTTPException) as exc:
        engine.candidate('owner-one', job['id'], 'quote-one')
    assert exc.value.status_code == 409
    assert '变化' in exc.value.detail
    assert engine.snapshot('owner-one', job['id'])['candidates'][0]['price'] == '15.90'


def test_expired_preview_and_closed_jobs_cannot_confirm(fake, monkeypatch):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id']); await_state(job, {'preview'})
    monkeypatch.setattr(engine, 'PREVIEW_TTL', -1)
    with pytest.raises(HTTPException) as exc:
        engine.candidate('owner-one', job['id'], 'quote-one')
    assert exc.value.status_code == 409
    assert engine.snapshot('owner-one', job['id'])['state'] == 'ready'
    engine.close('owner-one', job['id'])
    with pytest.raises(HTTPException):
        engine.read('owner-one', job['id'])
    with pytest.raises(HTTPException):
        engine.candidate('owner-one', job['id'], 'quote-one')


def test_unknown_quote_rejected(fake):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id']); await_state(job, {'preview'})
    with pytest.raises(HTTPException) as exc:
        engine.candidate('owner-one', job['id'], 'untrusted-invented-id')
    assert exc.value.status_code == 409


def test_startup_failure_is_sanitized_and_worker_releases_slot(fake):
    fake.fail_channels = {'msedge', 'chrome'}
    job = engine.start('owner-one', TARGET)
    result = await_state(job, {'error'})
    assert 'private details' not in str(result)
    assert engine._jobs[job['id']]['_done'].wait(2)
    assert engine._active_id is None


def test_edge_failure_uses_installed_chrome_without_unsafe_flags(fake):
    fake.fail_channels = {'msedge'}
    job = engine.start('owner-one', TARGET)
    result = await_state(job, {'ready'})
    assert result['browser'] == 'Google Chrome'
    assert [call['channel'] for call in fake.calls] == ['msedge', 'chrome']
    assert all(call['chromium_sandbox'] for call in fake.calls)


def test_empty_or_challenge_page_shows_needs_action(fake):
    fake.parsed.update(candidates=[], needs_action=True, message='请手动完成验证')
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id'])
    result = await_state(job, {'needs_action'})
    assert result['candidates'] == [] and '验证' in result['message']


def test_navigation_discards_old_preview(fake):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id']); await_state(job, {'preview'})
    fake.browser.context.page.url = 'https://www.temu.com/us/g-other.html?token=secret'
    result = await_state(job, {'ready'})
    assert not result['candidates'] and 'token=' not in result['url']


def test_ttl_closes_resources_and_history_stays_bounded(fake, monkeypatch):
    monkeypatch.setattr(engine, 'JOB_TTL', 0.04)
    monkeypatch.setattr(engine, 'MAX_HISTORY', 2)
    for _ in range(4):
        job = engine.start('owner-one', TARGET)
        assert engine._jobs[job['id']]['_done'].wait(2)
        assert engine.snapshot('owner-one', job['id'])['state'] == 'closed'
    assert len(engine._jobs) <= 2


@pytest.mark.parametrize('url', ['http://127.0.0.1/private', 'http://[::1]/', 'file:///C:/secrets', 'http://localhost/', 'ws://example.com/', 'https://name:secret@example.com'])
def test_route_blocks_nonpublic_and_non_http(url, monkeypatch):
    actions = []
    route = SimpleNamespace(request=SimpleNamespace(url=url), continue_=lambda: actions.append('continue'), abort=lambda reason: actions.append(reason))
    engine._safe_route(route)
    assert actions == ['blockedbyclient']


def test_route_validates_dns_for_each_subresource_and_redirect(monkeypatch):
    calls = []
    def dns(host, port):
        calls.append((host, port))
        if host == 'private.example':
            raise price_capture.PriceCaptureError('unsafe_url', 'private')
        return ['8.8.8.8']
    monkeypatch.setattr(price_capture, '_resolve_public_addresses', dns)
    for url, expected in [('https://public.example/image.jpg', 'continue'), ('https://private.example/private', 'blockedbyclient')]:
        actions = []
        route = SimpleNamespace(request=SimpleNamespace(url=url), continue_=lambda: actions.append('continue'), abort=lambda reason: actions.append(reason))
        engine._safe_route(route)
        assert actions == [expected]
    assert calls == [('public.example', 443), ('private.example', 443)]


def test_status_url_never_echoes_userinfo_query_or_fragment():
    assert engine._public_url('https://name:password@example.com/goods?token=secret#private') == 'https://example.com/goods'
    assert engine._public_url('file:///C:/Users/secret') == ''


def test_capture_uses_actual_browser_url_instead_of_snapshot_claim(fake, monkeypatch):
    seen = []
    def parse(snapshot, target):
        seen.append(snapshot['url'])
        return copy.deepcopy(fake.parsed)
    monkeypatch.setattr(parser, 'parse_snapshot', parse)
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    engine.read('owner-one', job['id']); await_state(job, {'preview'})
    assert seen == [TARGET['source_url']]


def test_unresponsive_renderer_is_cancelled_and_resources_close(fake, monkeypatch):
    monkeypatch.setattr(engine, 'READ_TIMEOUT', 0.02)
    monkeypatch.setattr(engine, 'STOP_GRACE', 0.01)
    unblocked = threading.Event()
    stops = []
    def stop(manager):
        stops.append(manager)
        unblocked.set()
    monkeypatch.setattr(engine, '_request_driver_stop', stop)
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    fake.browser.context.page.block_read = unblocked
    engine.read('owner-one', job['id'])
    assert engine._jobs[job['id']]['_done'].wait(2)
    assert stops == [fake]
    assert fake.browser.closed and fake.browser.context.closed and fake.exited
    assert engine.snapshot('owner-one', job['id'])['state'] == 'closed'


def test_driver_shutdown_is_scheduled_on_owning_loop():
    callbacks, actions = [], []
    callback = lambda: actions.append('stop-owned-driver')
    manager = SimpleNamespace(_loop=SimpleNamespace(call_soon_threadsafe=callbacks.append),
                              _client=SimpleNamespace(_transport=SimpleNamespace(request_stop=callback)))
    engine._request_driver_stop(manager)
    assert callbacks == [callback] and not actions
    callbacks[0]()
    assert actions == ['stop-owned-driver']


def test_profile_is_stable_for_user_shop_and_host_not_cookie_session_or_sku(fake):
    first = engine._profile_path(TARGET, 'msedge')
    another_sku = {**TARGET, 'listing_id': 'other-sku', 'source_url': 'https://www.temu.com/us/g-999.html?other=query'}
    assert engine._profile_path(another_sku, 'msedge') == first
    assert first.is_dir() and first.parent.name == 'browser-price-profiles'
    assert 'stable-user-one' not in str(first) and '测试店铺' not in str(first)
    for field, value in [('_profile_user', 'another-user'), ('platform', 'Other'), ('store', '另一店铺'),
                         ('source_url', 'https://www.amazon.com/dp/sample')]:
        assert engine._profile_path({**TARGET, field: value}, 'msedge') != first
    assert engine._profile_path(TARGET, 'chrome') != first


def test_new_web_login_session_reuses_profile_but_old_job_stays_session_owned(fake):
    first = engine.start('owner-one', TARGET)
    await_state(first, {'ready'})
    profile = fake.calls[-1]['user_data_dir']
    engine.close('owner-one', first['id'])
    assert engine._jobs[first['id']]['_done'].wait(2)
    second = engine.start('owner-two', TARGET)
    started = time.monotonic()
    while engine.snapshot('owner-two', second['id'])['state'] != 'ready':
        assert time.monotonic() - started < 2
        time.sleep(.005)
    assert fake.calls[-1]['user_data_dir'] == profile
    with pytest.raises(HTTPException) as exc:
        engine.snapshot('owner-two', first['id'])
    assert exc.value.status_code == 404


def test_login_popups_are_guarded_but_never_supply_quote(fake):
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    context = fake.browser.context
    popup = FakePage(context)
    context.pages.append(popup)
    # The event is normally emitted on the worker. Schedule this exact callback
    # through capture to keep Playwright's thread affinity assertion meaningful.
    original = context.page.evaluate
    def evaluate(script):
        context.events['page'](popup)
        return original(script)
    context.page.evaluate = evaluate
    engine.read('owner-one', job['id'])
    preview = await_state(job, {'preview'})
    assert not popup.closed
    assert set(popup.events) == {'download', 'dialog'}
    assert context.router is engine._safe_route and context.ws_router
    assert preview['candidates'][0]['price'] == '12.90'
    assert engine._jobs[job['id']]['_target']['source_url'] == TARGET['source_url']


def test_profile_is_never_exposed_in_job_status(fake):
    job = engine.start('owner-one', TARGET)
    ready = await_state(job, {'ready'})
    assert '_profile_user' not in ready and '_target' not in ready
    assert 'stable-user-one' not in str(ready)
    assert str(engine._directory) not in str(ready)


def test_missing_stable_user_does_not_open_a_shared_profile(fake):
    with pytest.raises(HTTPException) as exc:
        engine.start('session-only', {k: v for k, v in TARGET.items() if k != '_profile_user'})
    assert exc.value.status_code == 400 and not fake.calls


def test_clear_profile_only_removes_current_user_shop_and_refuses_active_job(fake):
    own = engine._profile_path(TARGET, 'msedge')
    own.joinpath('fake-cookie').write_text('synthetic session', encoding='utf-8')
    other = engine._profile_path({**TARGET, '_profile_user': 'other-user'}, 'msedge')
    other.joinpath('fake-cookie').write_text('another synthetic session', encoding='utf-8')
    job = engine.start('owner-one', TARGET)
    await_state(job, {'ready'})
    with pytest.raises(HTTPException) as exc:
        engine.clear_profile(TARGET)
    assert exc.value.status_code == 409 and own.exists()
    engine.close('owner-one', job['id'])
    assert engine._jobs[job['id']]['_done'].wait(2)
    result = engine.clear_profile(TARGET)
    assert result['ok'] and not own.exists() and other.joinpath('fake-cookie').exists()
    assert str(engine._directory) not in str(result)


def test_profile_data_is_excluded_from_application_backup(fake, tmp_path):
    import zipfile
    import backend
    backend.init_storage(tmp_path / 'backup-data')
    engine.init_engine(backend.DATA_DIR)
    profile = engine._profile_path(TARGET, 'msedge')
    profile.joinpath('Cookies').write_text('synthetic-only', encoding='utf-8')
    backup = backend.create_backup()
    with zipfile.ZipFile(backup) as archive:
        assert 'library.db' in archive.namelist()
        assert not any('browser-price-profiles' in item or 'Cookies' in item for item in archive.namelist())
