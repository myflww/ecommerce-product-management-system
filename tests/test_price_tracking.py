"""Price route/scheduler regression tests: no real network or company database."""
import asyncio
import io
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import price_capture
import price_storage
import price_tracking as tracking


@pytest.fixture(autouse=True)
def no_live_reference_rates(monkeypatch):
    # Existing price tests remain offline; FX behavior has dedicated tests.
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *args, **kwargs: None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    tracking.init_storage()
    backend._login_attempts.clear()
    tracking._active.clear()
    tracking._batch_running = False
    monkeypatch.setattr(backend, '_extract_semantic', lambda _:None)
    application=FastAPI()
    application.include_router(backend.router)
    application.include_router(tracking.router)
    with TestClient(application) as c:
        c.post('/api/setup', json={'name':'测试管理员','password':'test-only-password'}).raise_for_status()
        picture=Image.new('RGB',(120,120),'blue')
        raw=io.BytesIO();picture.save(raw,'PNG')
        p=c.post('/api/products',data={'metadata':json.dumps({'name':'测试商品','listings':[{'platform':'Amazon','price':'20','currency':'USD','status':'listed'}]})},files={'images':('sample.png',raw.getvalue(),'image/png')})
        p.raise_for_status()
        c.product=p.json()
        c.listing=c.product['listings'][0]['id']
        yield c


def configured(client):
    r=client.put('/api/price-tracking/listings/'+client.listing,json={'enabled':True,'source_url':'https://shop.example.com/item','region':'US','variant_hint':'blue'})
    assert r.status_code==200,r.text


def fetched(monkeypatch):
    calls=[]
    async def fetch(url,currency,**kwargs):
        calls.append((url,currency,kwargs))
        return {'price':'15','currency':'USD','source':'jsonld','evidence':'USD 15','title':'商品'}
    monkeypatch.setattr(price_capture,'fetch_and_extract',fetch)
    return calls


def test_opt_in_and_ratio_preserve_baseline(client,monkeypatch):
    calls=fetched(monkeypatch)
    assert client.post('/api/price-tracking/listings/'+client.listing+'/refresh').status_code==409
    assert not calls
    configured(client)
    assert client.post('/api/price-tracking/listings/'+client.listing+'/refresh').json()['updated']
    view=client.get('/api/price-tracking/products/'+client.product['id']).json()
    assert float(view['listings'][0]['ratio_percent'])==75
    assert client.get('/api/products/'+client.product['id']).json()['listings'][0]['price']=='20'
    assert calls[0][2]['ai_extractor'] is None


def test_failure_retains_old_price_and_marks_failure(client,monkeypatch):
    configured(client);fetched(monkeypatch)
    client.post('/api/price-tracking/listings/'+client.listing+'/refresh').raise_for_status()
    async def fail(*a,**kw):
        raise price_capture.PriceCaptureError('blocked','页面需要验证码，无法更新')
    monkeypatch.setattr(price_capture,'fetch_and_extract',fail)
    result=client.post('/api/price-tracking/listings/'+client.listing+'/refresh').json()
    assert result['updated'] is False
    row=client.get('/api/price-tracking/products/'+client.product['id']).json()['listings'][0]
    assert row['current_price']=='15'
    assert row['status']=='stale'
    assert '验证码' in row['error']


def test_permission_and_settings(client,monkeypatch):
    assert client.get('/api/price-tracking/settings').json()['can_edit']
    assert client.put('/api/price-tracking/settings',json={'enabled':True,'daily_time':'09:00'}).status_code==400
    assert client.put('/api/price-tracking/settings',json={'allow_ai':True}).status_code==200
    client.post('/api/team',json={'name':'其他成员','password':'test-only-password','role':'operator'}).raise_for_status()
    client.post('/api/logout');client.post('/api/login',json={'name':'其他成员','password':'test-only-password'}).raise_for_status()
    assert client.get('/api/price-tracking/settings').json()['can_edit'] is False
    assert client.get('/api/price-tracking/products/'+client.product['id']).json()['can_edit'] is False
    assert client.put('/api/price-tracking/settings',json={'enabled':True}).status_code==403
    assert client.put('/api/price-tracking/listings/'+client.listing,json={'enabled':True}).status_code==403
    assert client.post('/api/price-tracking/listings/'+client.listing+'/refresh').status_code==403
    assert client.post('/api/price-tracking/refresh').status_code==403
    client.post('/api/logout')
    assert client.get('/api/price-tracking/settings').status_code==401


def test_legacy_enabled_daily_schedule_is_ignored_and_only_manual_requests_run(client,monkeypatch):
    configured(client);calls=fetched(monkeypatch)
    with backend._connection() as conn:
        user=dict(conn.execute('SELECT id,name,role FROM users LIMIT 1').fetchone())
    price_storage.save_settings({'enabled':True,'daily_time':'09:00'}, user)
    for moment in [datetime(2026,11,13,8,59,tzinfo=tracking.CHINA),
                   datetime(2026,11,13,15,0,tzinfo=tracking.CHINA),
                   datetime(2026,11,14,10,0,tzinfo=tracking.CHINA)]:
        monkeypatch.setattr(tracking,'_clock',lambda:moment)
        assert asyncio.run(tracking.run_due()) is None
        assert not tracking._claim_day(moment)
    assert asyncio.run(tracking.scheduler_loop()) is None
    assert not asyncio.run(tracking.refresh_all(scheduled=True))['ok']
    assert not calls
    status=client.get('/api/price-tracking/settings').json()
    assert status['manual_only'] and not status['enabled'] and status['next_run_at'] is None
    assert client.post('/api/price-tracking/listings/'+client.listing+'/refresh').json()['updated']
    assert len(calls)==1


def test_old_disabled_tracking_record_can_be_explicitly_refreshed(client,monkeypatch):
    configured(client);calls=fetched(monkeypatch)
    client.put('/api/price-tracking/listings/'+client.listing,json={'enabled':False}).raise_for_status()
    response=client.post('/api/price-tracking/listings/'+client.listing+'/refresh')
    assert response.status_code==200 and response.json()['updated']
    assert len(calls)==1


def test_explicit_manual_batch_keeps_current_user_permissions(client,monkeypatch):
    configured(client);calls=fetched(monkeypatch)
    client.put('/api/price-tracking/listings/'+client.listing,json={'enabled':False}).raise_for_status()
    response=client.post('/api/price-tracking/refresh')
    assert response.status_code==200 and response.json()['updated']==1
    assert len(calls)==1


def test_existing_platform_link_initializes_manual_quote_without_hidden_opt_in(client,monkeypatch):
    calls=fetched(monkeypatch)
    platform=client.product['listings'][0]
    platform['link']='https://shop.example.com/linked-only'
    client.patch('/api/products/'+client.product['id'],json={'listings':[platform]}).raise_for_status()
    assert not price_storage.get_target(client.listing)['configured']
    result=client.post('/api/price-tracking/listings/'+client.listing+'/refresh')
    assert result.status_code==200 and result.json()['updated']
    assert price_storage.get_target(client.listing)['configured']
    assert len(calls)==1


def test_disabled_daily_no_calls_and_manual_optional_ai(client,monkeypatch):
    configured(client);calls=fetched(monkeypatch)
    assert asyncio.run(tracking.run_due()) is None
    assert not calls
    client.put('/api/price-tracking/settings',json={'allow_ai':True}).raise_for_status()
    client.post('/api/price-tracking/listings/'+client.listing+'/refresh').raise_for_status()
    assert calls[0][2]['ai_extractor'] is tracking._ai_extract


def test_changed_link_during_fetch_does_not_apply_old_price(client,monkeypatch):
    configured(client)
    with backend._connection() as conn:
        admin=dict(conn.execute('SELECT id,name,role FROM users LIMIT 1').fetchone())
    async def change(*a,**kw):
        price_storage.save_listing_tracking(client.listing,{'source_url':'https://other.example.com/other','enabled':True},admin)
        return {'price':'15','currency':'USD','source':'jsonld','evidence':'USD 15'}
    monkeypatch.setattr(price_capture,'fetch_and_extract',change)
    assert client.post('/api/price-tracking/listings/'+client.listing+'/refresh').json()['updated'] is False
    row=client.get('/api/price-tracking/products/'+client.product['id']).json()['listings'][0]
    assert not row['current_price']


def test_duplicate_refresh_is_rejected_without_second_request(client,monkeypatch):
    configured(client)
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event()
        async def slow(*a,**kw):
            entered.set();await release.wait()
            return {'price':'15','currency':'USD','source':'jsonld','evidence':'USD 15'}
        monkeypatch.setattr(price_capture,'fetch_and_extract',slow)
        first=asyncio.create_task(tracking.refresh_one(client.listing))
        await entered.wait()
        try:
            with pytest.raises(Exception) as exc:
                await tracking.refresh_one(client.listing)
            assert exc.value.status_code==409
        finally:
            release.set()
        assert (await first)['updated']
    asyncio.run(scenario())
