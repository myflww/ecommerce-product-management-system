"""Platform/store filters must apply to the same listing, across pagination."""
import io
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend


@pytest.fixture
def stores(tmp_path,monkeypatch):
    backend.init_storage(tmp_path)
    backend._login_attempts.clear()
    monkeypatch.setattr(backend,'_extract_semantic',lambda _:None)
    app=FastAPI();app.include_router(backend.router)
    with TestClient(app) as client:
        client.post('/api/setup',json={'name':'店铺筛选测试','password':'store-test-password'}).raise_for_status()
        image=io.BytesIO();Image.new('RGB',(64,64),'green').save(image,'PNG')
        def add(name,listings):
            response=client.post('/api/products',data={'metadata':json.dumps({'name':name,'category':'柱蜡','listings':listings})},
                                 files={'images':('fixture.png',image.getvalue(),'image/png')})
            response.raise_for_status();return response.json()
        items={
            'mixed':add('跨平台同名店铺',[{'platform':'Temu','store':'A店','status':'pending'}, {'platform':'Amazon','store':'B店','status':'listed'}]),
            'temu_b':add('Temu B店产品',[{'platform':'Temu','store':'B店','status':'listed'}]),
            'amazon_a':add('Amazon A店产品',[{'platform':'Amazon','store':'A店','status':'archived'}]),
            'blank':add('未填店铺',[{'platform':'Temu','store':'','status':'pending'}]),
            'literal':add('特殊字符店铺',[{'platform':'Temu','store':'A%_店','status':'pending'}]),
            'deleted':add('已移除C店',[{'platform':'Temu','store':'C店','status':'listed'}]),
        }
        client.delete('/api/products/'+items['deleted']['id']).raise_for_status()
        yield client,items


def names(client,**filters):
    response=client.get('/api/products',params=filters);response.raise_for_status()
    return {item['name'] for item in response.json()['items']},response.json()['total']


def test_store_and_platform_and_status_match_one_listing(stores):
    client,items=stores
    assert names(client,platform='Temu',store='B店')==({'Temu B店产品'},1)
    assert names(client,platform='Temu',store='B店',listing_status='listed')==({'Temu B店产品'},1)
    assert names(client,platform='Temu',store='A店',listing_status='listed')==(set(),0)
    assert names(client,platform='Amazon',store='B店',listing_status='listed')==({'跨平台同名店铺'},1)
    assert names(client,platform='Amazon',store='A店',listing_status='archived')==({'Amazon A店产品'},1)


def test_same_store_name_without_platform_and_pagination(stores):
    client,items=stores
    first=client.get('/api/products',params={'store':'A店','limit':1}).json()
    second=client.get('/api/products',params={'store':'A店','limit':1,'offset':1}).json()
    assert first['total']==second['total']==2
    assert first['items'][0]['id']!=second['items'][0]['id']
    assert {first['items'][0]['name'],second['items'][0]['name']}=={'跨平台同名店铺','Amazon A店产品'}


def test_metadata_is_global_distinct_and_omits_blank_or_deleted(stores):
    client,items=stores
    assert client.get('/api/products',params={'limit':1}).json()['total']==5
    stats=client.get('/api/stats').json()
    assert stats['platform_stores']==[
        {'platform':'Amazon','store':'A店'}, {'platform':'Amazon','store':'B店'},
        {'platform':'Temu','store':'A%_店'}, {'platform':'Temu','store':'A店'}, {'platform':'Temu','store':'B店'}]
    assert stats['platforms']==['Amazon','Temu']


def test_omitted_or_empty_store_preserves_old_behavior(stores):
    client,items=stores
    base=client.get('/api/products',params={'platform':'Temu'}).json()
    for empty in ('','   '):
        filtered=client.get('/api/products',params={'platform':'Temu','store':empty}).json()
        assert [row['id'] for row in filtered['items']]==[row['id'] for row in base['items']]
        assert filtered['total']==base['total']==4


def test_literal_store_values_are_not_like_patterns_or_sql(stores):
    client,items=stores
    assert names(client,store='A%_店')==({'特殊字符店铺'},1)
    assert names(client,store='A%')==(set(),0)
    assert names(client,store="' OR 1=1 --")==(set(),0)
    assert names(client,store='C店')==(set(),0)
    assert client.get('/api/products',params={'store':'x'*81}).status_code==422
    assert names(client,store=' A店 ',category='柱蜡',q='Amazon A店产品')==({'Amazon A店产品'},1)


def test_listing_sources_are_optional_provenance_and_never_merchant_identifiers(stores):
    client,items=stores
    product=items['mixed']; listing=product['listings'][0]
    assert all('source' not in row for row in client.get('/api/products/'+product['id']).json()['listings'])
    with backend._connection(write=True) as conn:
        conn.execute('''CREATE TABLE store_import_sources(
            listing_id TEXT PRIMARY KEY,platform TEXT,store_id TEXT,store_name TEXT,site TEXT,
            source_product_id TEXT,source_variant_id TEXT,source_url TEXT)''')
        conn.execute('UPDATE product_listings SET product_code=?,product_number=? WHERE id=?',
                     ('merchant-SKU','merchant-SKC',listing['id']))
        conn.execute('INSERT INTO store_import_sources VALUES(?,?,?,?,?,?,?,?)',
                     (listing['id'],'Temu','public-store-7','A店','US','public-product-8','public-variant-9','https://www.temu.com/goods.html?goods_id=8'))
    expected={'platform':'Temu','store_id':'public-store-7','store_name':'A店','site':'US',
              'product_id':'public-product-8','variant_id':'public-variant-9','url':'https://www.temu.com/goods.html?goods_id=8'}
    for result in (client.get('/api/products/'+product['id']).json(),
                   next(row for row in client.get('/api/products').json()['items'] if row['id']==product['id'])):
        sourced=next(row for row in result['listings'] if row['id']==listing['id'])
        assert sourced['source']==expected
        assert sourced['product_code']=='merchant-SKU' and sourced['product_number']=='merchant-SKC'
        assert all('source' not in row for row in result['listings'] if row['id']!=listing['id'])
    assert all('source' not in row for row in client.get('/api/products/'+items['temu_b']['id']).json()['listings'])
