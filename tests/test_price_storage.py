"""Price tracking storage tests use an isolated database and no live requests."""
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import backend
import price_storage as storage


@pytest.fixture(autouse=True)
def no_live_reference_rates(monkeypatch):
    # Existing price tests remain offline; FX behavior has dedicated tests.
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *args, **kwargs: None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "data")
    storage.init_storage()
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as client:
        response = client.post('/api/setup', json={'name':'价格管理员','password':'test-price-password'})
        response.raise_for_status()
        user = response.json()['user']
        content = io.BytesIO()
        Image.new('RGB',(64,64),'green').save(content,'PNG')
        response = client.post('/api/products', data={'metadata':json.dumps({'name':'价格测试样品','notes':'原始备注', 'listings':[
            {'platform':'Temu','store':'美国店','product_code':'000SKU','product_number':'000001','status':'listed','price':'20.00','currency':'USD','link':'https://shop.example.com/p/1'},
            {'platform':'Amazon','status':'archived','price':'10.0001','currency':'USD'}]})}, files={'images':('sample.png',content.getvalue(),'image/png')})
        response.raise_for_status()
        yield client, user, response.json()


def enable(setup, **fields):
    client, user, product = setup
    listing_id = product['listings'][0]['id']
    storage.save_listing_tracking(listing_id, {'source_url':'https://shop.example.com/p/1','region':'美国','variant_hint':'2件装','enabled':True,**fields}, user)
    return storage.get_target(listing_id, user)


def view(setup, index=0):
    _, user, product = setup
    return storage.get_product_tracking(product['id'], user)['listings'][index]


def test_existing_platform_links_are_visible_but_never_auto_enabled(setup):
    _, user, product = setup
    data = storage.get_product_tracking(product['id'], user)
    assert data['can_edit'] is True
    first, second = data['listings']
    assert first['source_url'] == product['listings'][0]['link']
    assert first['enabled'] is False and first['configured'] is False
    assert first['original_price'] == '20.00' and first['current_price'] == ''
    assert first['product_number'] == '000001' and first['product_code'] == '000SKU'
    assert first['ratio_percent'] is None and first['status'] == 'never'
    assert second['status'] == 'unconfigured'
    assert storage.eligible_targets() == []


def test_schema_initialization_is_additive_and_preserves_existing_data(setup):
    client, user, product = setup
    storage.init_storage()
    storage.init_storage()
    current = client.get('/api/products/' + product['id']).json()
    for key in ('name','notes','specs','images','listings','code','updated_at'):
        assert current[key] == product[key]
    assert storage.get_settings() == storage.DEFAULT_SETTINGS


def test_legacy_disabled_link_accepts_manual_price_and_is_available_for_explicit_batch(setup):
    _, user, product = setup
    listing_id = product['listings'][0]['id']
    saved = storage.save_listing_tracking(listing_id, {'enabled': False}, user)
    assert saved['status'] == 'never' and saved['enabled'] is False
    target = storage.get_target(listing_id, user)
    assert [row['id'] for row in storage.eligible_targets()] == [listing_id]
    assert storage.record_attempt(listing_id, {'price': '15', 'currency': 'USD'}, expected_target=target)
    current = view(setup)
    assert current['enabled'] is False and current['status'] == 'ok'
    assert current['current_price'] == '15' and current['ratio_percent'] == '75.0'
    assert current['stale'] is False


def test_success_ratio_is_current_divided_by_manual_base_and_platforms_stay_independent(setup):
    target = enable(setup)
    client, _, product = setup
    before_check = client.get('/api/products/'+product['id']).json()
    assert target['listing_id'] == target['id']
    assert storage.record_attempt(target['listing_id'], {'price':'15.00','currency':'USD','source':'json_ld','evidence':'price 15.00 USD'}, expected_target=target)
    first = view(setup)
    assert first['current_price'] == '15.00' and first['original_price'] == '20.00'
    assert first['ratio_percent'] == '75.0' and first['status'] == 'ok' and first['stale'] is False
    assert first['last_success_at'] and first['last_checked_at']
    assert view(setup,1)['current_price'] == ''
    assert client.get('/api/products/'+product['id']).json()['listings'] == before_check['listings']
    current = client.get('/api/products/'+product['id']).json()
    assert current['updated_at'] == before_check['updated_at']


def test_ratio_rounds_half_up_and_prices_over_base_are_not_capped(setup):
    target = enable(setup,original_price='3')
    assert storage.record_attempt(target['id'],{'price':'3.9999','currency':'USD'},expected_target=target)
    assert view(setup)['ratio_percent'] == '133.3'
    assert storage.record_attempt(target['id'],{'price':'0.0001','currency':'USD'},expected_target=target)
    assert view(setup)['ratio_percent'] == '0.0'


def test_manual_base_price_updates_only_base_and_audits_once(setup):
    client, user, product = setup
    target = enable(setup)
    listing = product['listings'][0]
    saved = storage.save_listing_tracking(listing['id'],{'original_price':'10.0001'},user)
    current = client.get('/api/products/'+product['id']).json()
    assert saved['original_price'] == '10.0001'
    assert current['updated_at'] != product['updated_at']
    assert current['name'] == product['name'] and current['notes'] == product['notes']
    assert current['listings'][1] == product['listings'][1]
    assert current['listings'][0] == {**listing,'price':'10.0001','region':'美国','variant_hint':'2件装'}
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='price_tracking_update'").fetchone()[0] == 2
    storage.record_attempt(target['id'],{'price':'9','currency':'USD'},expected_target=target)
    assert view(setup)['ratio_percent'] == '90.0'


def test_no_base_or_different_currency_has_no_percentage(setup):
    _, user, _ = setup
    target = enable(setup,original_price='')
    storage.record_attempt(target['id'],{'price':'10','currency':'USD'},expected_target=target)
    assert view(setup)['ratio_percent'] is None
    storage.save_listing_tracking(target['id'], {'original_price':'20'}, user)
    storage.record_attempt(target['id'],{'price':'10','currency':'EUR'},expected_target=target)
    assert view(setup)['current_currency'] == 'EUR'
    assert view(setup)['ratio_percent'] is None and view(setup)['status'] == 'currency_mismatch'


def test_failure_keeps_last_success_but_marks_it_stale(setup):
    target = enable(setup)
    storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target)
    success = view(setup)
    storage.record_attempt(target['id'],error='页面要求登录，无法确认价格',expected_target=target)
    after = view(setup)
    assert after['current_price'] == success['current_price']
    assert after['last_success_at'] == success['last_success_at']
    assert after['last_checked_at'] > success['last_checked_at']
    assert after['stale'] is True and after['status'] == 'stale' and after['error']
    assert after['ratio_percent'] == '75.0'  # Explicitly a stale, last-known comparison.
    assert storage.get_history(target['id'],setup[1])['items'][0]['price'] == ''


@pytest.mark.parametrize('changed', [{'source_url':'https://shop.example.com/p/2'}, {'region':'加拿大'}, {'variant_hint':'3件装'}])
def test_target_changes_clear_old_value_and_late_results_are_discarded(setup,changed):
    target = enable(setup)
    storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target)
    storage.save_listing_tracking(target['id'],changed,setup[1])
    assert view(setup)['current_price'] == '' and view(setup)['ratio_percent'] is None
    assert storage.record_attempt(target['id'],{'price':'5','currency':'USD'},expected_target=target) is False
    assert view(setup)['current_price'] == ''
    assert len(storage.get_history(target['id'],setup[1])['items']) == 1


def test_previous_revision_result_cannot_apply_after_configuration_change_or_delete(setup):
    client,user,product = setup
    target = enable(setup)
    storage.save_listing_tracking(target['id'],{'enabled':False},user)
    assert storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target) is False
    target = enable(setup)
    client.delete('/api/products/'+product['id']).raise_for_status()
    assert storage.eligible_targets() == []
    assert storage.record_attempt(target['id'],error='late failure',expected_target=target) is False


def test_existing_backend_edit_can_replace_listing_rows_without_losing_price_tracking(setup):
    client,user,product = setup
    target = enable(setup)
    storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target)
    response = client.patch('/api/products/'+product['id'],json={'name':'新名称','listings':product['listings']})
    response.raise_for_status()
    assert view(setup)['current_price'] == '15' and view(setup)['ratio_percent'] == '75.0'
    assert len(storage.eligible_targets()) == 1
    changed = [{**product['listings'][0],'currency':'CAD'}, product['listings'][1]]
    client.patch('/api/products/'+product['id'],json={'listings':changed}).raise_for_status()
    assert view(setup)['current_price'] == '' and view(setup)['ratio_percent'] is None
    assert storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target) is False


def test_deleted_listing_does_not_schedule_and_record_cannot_resurrect_it(setup):
    client,_,product = setup
    target = enable(setup)
    client.patch('/api/products/'+product['id'],json={'listings':[]}).raise_for_status()
    assert storage.eligible_targets() == []
    assert storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target) is False


def test_history_is_bounded_and_preserves_observation_context(setup):
    target = enable(setup)
    for i in range(95):
        storage.record_attempt(target['id'],{'price':str(i+1),'currency':'USD','evidence':str(i)},expected_target=target)
    history = storage.get_history(target['id'],setup[1],limit=90)['items']
    assert len(history) == 90
    assert history[0]['price'] == '95' and history[-1]['price'] == '6'
    assert history[0]['source_url'] == target['source_url'] and history[0]['region'] == '美国'
    assert history[0]['variant_hint'] == '2件装'


def test_older_success_shows_stale_status_without_fabricated_new_timestamp(setup):
    target = enable(setup)
    storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target)
    past = (datetime.now(timezone.utc)-timedelta(hours=27)).isoformat()
    with backend._connection(write=True) as conn:
        conn.execute('UPDATE price_tracking SET last_success_at=? WHERE listing_id=?',(past,target['id']))
    assert view(setup)['status'] == 'stale' and view(setup)['stale'] is True
    assert view(setup)['last_success_at'] == past


def test_members_can_view_but_only_owner_creator_or_admin_can_edit(setup):
    _,user,product = setup
    other = {'id':'another-user','name':'另一个成员','role':'operator'}
    assert storage.get_product_tracking(product['id'],other)['can_edit'] is False
    with pytest.raises(HTTPException) as error:
        storage.save_listing_tracking(product['listings'][0]['id'],{'original_price':'10'},other)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        storage.get_target(product['listings'][0]['id'],other)
    assert error.value.status_code == 403
    with backend._connection(write=True) as conn:
        conn.execute("INSERT INTO users(id,name,password_hash,role,created_at) VALUES(?,?,?,?,?)", ('owner-user','新负责人','test-only-hash','operator',backend.now()))
        conn.execute('UPDATE products SET owner=? WHERE id=?', ('新负责人',product['id']))
    owner = {'id':'owner-user','name':'新负责人','role':'operator'}
    assert storage.get_product_tracking(product['id'],owner)['can_edit'] is True
    storage.save_listing_tracking(product['listings'][0]['id'],{'original_price':'10'},owner)
    assert view(setup)['original_price'] == '10'


def test_global_settings_are_validated_and_require_admin(setup):
    user=setup[1]
    result=storage.save_settings({'enabled':True,'allow_ai':True,'daily_time':'23:59'},user)
    assert result == {'enabled':True,'allow_ai':True,'daily_time':'23:59','timezone':'Asia/Shanghai'}
    with pytest.raises(HTTPException) as error:
        storage.save_settings({'enabled':False},{'id':'member','name':'成员','role':'operator'})
    assert error.value.status_code == 403
    assert storage.get_settings() == result


@pytest.mark.parametrize('bad',[
    {'enabled':1},{'allow_ai':'yes'},{'daily_time':'24:00'},{'daily_time':'9:00'},
    {'timezone':'America/New_York'},{'unknown':True},
])
def test_invalid_settings_do_not_partially_save(setup,bad):
    before=storage.get_settings()
    with pytest.raises(HTTPException):
        storage.save_settings(bad,setup[1])
    assert storage.get_settings() == before


@pytest.mark.parametrize('bad',[
    {'source_url':'http://127.0.0.1/a'}, {'source_url':'http://[::1]/a'},
    {'source_url':'http://169.254.169.254/metadata'}, {'source_url':'http://10.0.0.1/'},
    {'source_url':'https://user:password@shop.example.com/'}, {'source_url':'http://host.local/a'},
    {'source_url':'file:///C:/data'}, {'source_url':'https://shop.example.com:8765/p'},
    {'source_url':'https://shop.example.com/#private'}, {'enabled':1}, {'source_url':'','enabled':True},
    {'original_price':'0'}, {'original_price':'NaN'}, {'original_price':True},
    {'original_price':'12.12345'}, {'region':['美国']}, {'variant_hint':None}, {'extra':'not allowed'},
])
def test_invalid_configuration_is_atomic_and_never_changes_original_price(setup,bad):
    target=enable(setup)
    before=view(setup)
    with pytest.raises(HTTPException):
        storage.save_listing_tracking(target['id'],bad,setup[1])
    assert view(setup) == before


@pytest.mark.parametrize('bad',[
    {'price':'0','currency':'USD'}, {'price':'NaN','currency':'USD'},
    {'price':True,'currency':'USD'}, {'price':'10','currency':'$'},
    {'price':'10','currency':'USD','evidence':['bad']},
])
def test_invalid_results_never_overwrite_success(setup,bad):
    target=enable(setup)
    storage.record_attempt(target['id'],{'price':'15','currency':'USD'},expected_target=target)
    before=view(setup)
    with pytest.raises(HTTPException):
        storage.record_attempt(target['id'],bad,expected_target=target)
    assert view(setup) == before
