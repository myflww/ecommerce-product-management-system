"""No real browser, company database, network or paid API is used here."""
import copy
import io
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import backend
import browser_price_routes as routes
import price_storage
import price_tracking


@pytest.fixture(autouse=True)
def no_live_reference_rates(monkeypatch):
    # Existing price tests remain offline; FX behavior has dedicated tests.
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *args, **kwargs: None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    routes.engine.init_engine(tmp_path)
    backend._login_attempts.clear()
    price_tracking.init_storage()
    routes._consumed.clear()
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(routes.router)
    app.include_router(price_tracking.router)
    jobs = {}
    monkeypatch.setattr(routes.engine, 'available', lambda: {'available': True, 'browser': 'Edge', 'note': '独立窗口'})
    def get(owner, jobid):
        if jobid not in jobs or jobs[jobid]['owner'] != owner:
            raise HTTPException(404, '任务不存在')
        return jobs[jobid]
    def public(job):
        return {k: v for k, v in job.items() if k not in {'owner', 'target'}}
    def start(owner, target):
        jobid = 'job-' + str(len(jobs))
        jobs[jobid] = {'id': jobid, 'owner': owner, 'target': copy.deepcopy(target),
            'state': 'ready', 'listing_id': target['listing_id'], 'candidates': [], 'message': '选择规格后读取'}
        return public(jobs[jobid])
    def read(owner, jobid):
        job = get(owner, jobid)
        job.update(state='preview', candidates=[{'id': 'quote-A', 'price': '15.20', 'currency': 'USD',
            'evidence': '当前售价 US$15.20', 'title': '套装D', 'source': 'browser_confirmed'}])
        return public(job)
    def close(owner, jobid):
        job = get(owner, jobid)
        job['state'] = 'closed'
        return public(job)
    def candidate(owner, jobid, token):
        job = get(owner, jobid)
        if job['state'] != 'preview':
            raise HTTPException(409, '请先读取页面')
        found = next((c for c in job['candidates'] if c['id'] == token), None)
        if not found:
            raise HTTPException(400, '未知报价')
        return copy.deepcopy(job['target']), copy.deepcopy(found)
    for name, function in {'start': start, 'snapshot': lambda o, j: public(get(o, j)), 'read': read, 'close': close, 'candidate': candidate}.items():
        monkeypatch.setattr(routes.engine, name, function)
    with TestClient(app, client=('127.0.0.1', 30001)) as client:
        client.post('/api/setup', json={'name': '报价测试', 'password': 'browser-quote-test-only'}).raise_for_status()
        stream = io.BytesIO()
        Image.new('RGB', (40, 40), '#4545aa').save(stream, 'PNG')
        response = client.post('/api/products', data={'metadata': json.dumps({'name': '套装D', 'listings': [
            {'platform': 'Temu', 'price': '74.62', 'currency': 'CNY', 'status': 'pending'}]})}, files={'images': ('fixture.png', stream.getvalue(), 'image/png')})
        response.raise_for_status()
        product = response.json()
        listing = product['listings'][0]['id']
        client.put('/api/price-tracking/listings/' + listing, json={'enabled': True,
            'source_url': 'https://www.temu.com/us-zh-Hans/sample-g-700000000000002.html', 'region': '美国站', 'variant_hint': '套装D'}).raise_for_status()
        yield client, app, product, listing, jobs


def begin(setup, read=True):
    c, _, _, listing, _ = setup
    r = c.post('/api/price-tracking/listings/' + listing + '/browser/start')
    r.raise_for_status()
    job = r.json()
    if read:
        c.post('/api/price-tracking/browser/' + job['id'] + '/read').raise_for_status()
    return job['id']


def confirmed(client, job, **overrides):
    return client.post('/api/price-tracking/browser/' + job + '/confirm', json={
        'candidate_id': 'quote-A', 'confirmed_variant': True, 'confirmed_price_basis': True, **overrides})


def test_preview_does_not_write_and_confirm_keeps_cny_basis(setup):
    c, _, product, listing, _ = setup
    job = begin(setup)
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 0
    result = confirmed(c, job)
    assert result.status_code == 200, result.text
    assert result.json()['updated'] and '参考汇率' in result.json()['message']
    current = c.get('/api/price-tracking/products/' + product['id']).json()['listings'][0]
    assert current['current_currency'] == 'USD' and current['current_price'] == '15.20'
    assert current['original_price'] == '74.62' and current['currency'] == 'CNY'
    assert current['ratio_percent'] is None
    assert current['source'] == 'browser_confirmed'
    assert confirmed(c, job).status_code == 409
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='price_confirm'").fetchone()[0] == 1


@pytest.mark.parametrize('override', [{'confirmed_variant': False}, {'confirmed_variant': 1},
    {'confirmed_price_basis': False}, {'price': '0.01'}, {'candidate_id': ''}, {'candidate_id': ['quote-A']}])
def test_confirmation_requires_real_user_choices_and_never_accepts_price_override(setup, override):
    c = setup[0]
    job = begin(setup)
    assert confirmed(c, job, **override).status_code == 400
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 0


def test_cannot_confirm_before_reading_or_unknown_quote(setup):
    c = setup[0]
    job = begin(setup, read=False)
    assert confirmed(c, job).status_code == 409
    c.post('/api/price-tracking/browser/' + job + '/read')
    assert confirmed(c, job, candidate_id='forged').status_code == 400


def test_changed_source_and_deleted_products_cannot_apply_stale_quotes(setup):
    c, _, product, listing, _ = setup
    job = begin(setup)
    c.put('/api/price-tracking/listings/' + listing, json={'source_url': 'https://www.temu.com/changed-g-999.html'}).raise_for_status()
    assert confirmed(c, job).status_code == 409
    second = begin(setup)
    c.delete('/api/products/' + product['id']).raise_for_status()
    assert confirmed(c, second).status_code == 404
    assert c.post('/api/price-tracking/browser/' + second + '/close').status_code == 200
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 0


def test_another_login_session_cannot_read_or_control_a_job(setup):
    c = setup[0]
    job = begin(setup)
    c.post('/api/logout')
    c.post('/api/login', json={'name': '报价测试', 'password': 'browser-quote-test-only'}).raise_for_status()
    for suffix, method in [('', 'GET'), ('/read', 'POST'), ('/close', 'POST')]:
        assert c.request(method, '/api/price-tracking/browser/' + job + suffix).status_code == 404
    assert confirmed(c, job).status_code == 404


def test_profile_user_is_assigned_from_auth_and_survives_session_rotation(setup):
    c, _, _, _, jobs = setup
    first = begin(setup, read=False)
    user = jobs[first]['target']['_profile_user']
    assert user
    old_owner = jobs[first]['owner']
    c.post('/api/logout').raise_for_status()
    c.post('/api/login', json={'name': '报价测试', 'password': 'browser-quote-test-only'}).raise_for_status()
    second = begin(setup, read=False)
    assert jobs[second]['target']['_profile_user'] == user
    assert jobs[second]['owner'] != old_owner
    assert c.get('/api/price-tracking/browser/' + first).status_code == 404
    assert '_profile_user' not in c.get('/api/price-tracking/browser/' + second).json()


def test_lan_cannot_open_or_control_host_browser_even_with_forwarded_header(setup):
    c, app, _, listing, _ = setup
    job = begin(setup)
    with TestClient(app, client=('192.168.2.33', 30002)) as lan:
        lan.cookies.update(c.cookies)
        assert not lan.get('/api/price-tracking/browser/capabilities').json()['can_use']
        assert lan.post('/api/price-tracking/listings/' + listing + '/browser/start', headers={'X-Forwarded-For': '127.0.0.1'}).status_code == 403
        assert lan.post('/api/price-tracking/listings/' + listing + '/browser/profile/clear').status_code == 403
        assert lan.get('/api/price-tracking/browser/' + job).status_code == 403


def test_product_edit_rights_rechecked_on_confirm(setup):
    owner, app, product, listing, jobs = setup
    response = owner.post('/api/team', json={
        'name': '采价管理员', 'password': 'browser-admin-test-only', 'role': 'admin'})
    response.raise_for_status()
    identity = response.json()['id']
    with TestClient(app, client=('127.0.0.1', 30003)) as admin:
        admin.post('/api/login', json={
            'name': '采价管理员', 'password': 'browser-admin-test-only'}).raise_for_status()
        job = begin((admin, app, product, listing, jobs))
        member = next(item for item in owner.get('/api/team').json()['items'] if item['id'] == identity)
        owner.patch('/api/team/' + identity, json={
            'expected_revision': member['revision'],
            'permissions': {'products.edit_own': False, 'products.edit_all': False}}).raise_for_status()
        assert confirmed(admin, job).status_code == 403
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 0


def test_nonowner_cannot_open_product_browser(setup):
    c, _, _, listing, _ = setup
    c.post('/api/team', json={'name': '另一运营', 'password': 'other-member-password', 'role': 'operator'}).raise_for_status()
    c.post('/api/logout')
    c.post('/api/login', json={'name': '另一运营', 'password': 'other-member-password'}).raise_for_status()
    assert c.post('/api/price-tracking/listings/' + listing + '/browser/start').status_code == 403
    assert c.post('/api/price-tracking/listings/' + listing + '/browser/profile/clear').status_code == 403


def test_clear_login_uses_authenticated_profile_scope_and_exposes_no_paths(setup):
    c, _, _, listing, jobs = setup
    job = begin(setup, read=False)
    target = jobs[job]['target']
    profile = routes.engine._profile_path(target, 'msedge')
    profile.joinpath('Cookies').write_text('synthetic login', encoding='utf-8')
    unrelated = routes.engine._profile_path({**target, '_profile_user':'unrelated-user'}, 'msedge')
    unrelated.joinpath('Cookies').write_text('synthetic other login', encoding='utf-8')
    result = c.post('/api/price-tracking/listings/' + listing + '/browser/profile/clear')
    assert result.status_code == 200 and result.json()['ok']
    assert not profile.exists() and unrelated.joinpath('Cookies').exists()
    assert str(backend.DATA_DIR) not in result.text


def test_browser_close_problem_does_not_report_record_as_failed(setup, monkeypatch):
    c = setup[0]
    job = begin(setup)
    def fail(*args):
        raise HTTPException(409, '窗口暂不可关闭')
    monkeypatch.setattr(routes.engine, 'close', fail)
    response = confirmed(c, job)
    assert response.status_code == 200 and response.json()['updated']
    assert '手动关闭' in response.json()['message']


def test_audit_failure_after_commit_does_not_claim_quote_failed(setup, monkeypatch):
    import sqlite3
    c = setup[0]
    job = begin(setup)
    def fail(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(backend, '_audit', fail)
    response = confirmed(c, job)
    assert response.status_code == 200 and response.json()['updated']
    assert '价格已保存' in response.json()['message']
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM price_history').fetchone()[0] == 1
