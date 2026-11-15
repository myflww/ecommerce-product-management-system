"""Owner-only permanent cleanup: isolated media, backups and business guards."""
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import backend
import knowledge_store
import price_storage
import product_management
import store_import_storage


PASSWORD = 'purge-test-password-only'


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / 'purge')
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)
    monkeypatch.setattr(backend, 'start_indexing', lambda *a, **k: {})
    monkeypatch.setattr(backend, '_backup_extras', {})
    monkeypatch.setattr(knowledge_store, '_initialized', None)
    price_storage.init_storage()
    knowledge_store.init_storage()
    app = FastAPI()
    app.include_router(product_management.router)
    app.include_router(backend.router)
    app.include_router(knowledge_store.router)
    with TestClient(app) as instance:
        instance.post('/api/setup', json={'name':'老板', 'password':PASSWORD}).raise_for_status()
        yield instance


def product(client, name='隔离商品'):
    stream = io.BytesIO()
    Image.new('RGB', (64,64), '#e99945').save(stream, 'PNG')
    result = client.post('/api/products', data={'metadata':json.dumps({'name':name, 'listings':[
        {'platform':'Temu', 'price':'10', 'currency':'USD'}]})},
        files={'images':('fixture.png',stream.getvalue(),'image/png')})
    result.raise_for_status()
    return result.json()


def preview(client):
    result = client.post('/api/products/trash/purge-preview')
    result.raise_for_status()
    return result.json()


def purge(client, plan):
    return client.post('/api/products/trash/purge', json={
        'confirmation':'永久清空回收站','expected_token':plan['token']})


def remove(client, item):
    client.delete('/api/products/' + item['id']).raise_for_status()


@pytest.mark.parametrize('role', ['admin','operator'])
def test_non_owner_cannot_preview_or_permanently_remove_even_own_product(client, role):
    client.post('/api/team', json={'name':'同事','password':PASSWORD,'role':role}).raise_for_status()
    client.post('/api/logout').raise_for_status()
    client.post('/api/login', json={'name':'同事','password':PASSWORD}).raise_for_status()
    own = product(client)
    remove(client, own)
    assert client.post('/api/products/trash/purge-preview').status_code == 403
    assert purge(client, {'token':'0'*64}).status_code == 403
    assert client.get('/api/products/trash').json()['total'] == 1


def test_owner_cleanup_preserves_full_backup_active_media_and_audit(client, tmp_path):
    deleted, active = product(client), product(client, '保留商品')
    with backend._connection(write=True) as conn:
        original = dict(conn.execute('SELECT * FROM images WHERE product_id=?',(deleted['id'],)).fetchone())
        conn.execute('INSERT INTO image_embeddings VALUES(?,?,?,?,?)',
                     (original['id'],'fixture','fixture','[1]',backend.now()))
        conn.execute('INSERT INTO price_tracking(listing_id,updated_at) VALUES(?,?)',
                     (deleted['listings'][0]['id'],backend.now()))
        conn.execute('INSERT INTO price_history(listing_id,checked_at,source_url) VALUES(?,?,?)',
                     (deleted['listings'][0]['id'],backend.now(),'https://www.temu.com/g-fixture.html'))
    original_path = backend.DATA_DIR / 'images' / original['filename']
    original_bytes = original_path.read_bytes()
    remove(client, deleted)
    plan = preview(client)
    assert plan['count'] == 1 and plan['image_count'] == 1 and plan['can_purge']
    result = purge(client, plan)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result['removed'] == 1 and result['backup_created']
    assert not original_path.exists()
    assert client.get(active['images'][0]['url']).status_code == 200
    assert client.get('/api/products/trash').json()['total'] == 0
    with backend._connection() as conn:
        for table in ('price_history','price_tracking','image_embeddings'):
            assert conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='purge_products'").fetchone()[0] == 1
    with zipfile.ZipFile(backend.DATA_DIR / 'backups' / result['backup_name']) as archive:
        assert archive.read('images/'+original['filename']) == original_bytes
        snapshot = tmp_path / 'restore-check.db'
        snapshot.write_bytes(archive.read('library.db'))
    with sqlite3.connect(snapshot) as saved:
        assert saved.execute('SELECT COUNT(*) FROM products').fetchone()[0] == 2
        assert saved.execute('SELECT deleted FROM products WHERE id=?',(deleted['id'],)).fetchone()[0] == 1


def test_stale_preview_and_exact_confirmation_do_not_change_data(client):
    first = product(client)
    remove(client, first)
    old = preview(client)
    second = product(client)
    remove(client, second)
    assert purge(client, old).status_code == 409
    assert client.post('/api/products/trash/purge',json={
        'confirmation':'清空','expected_token':preview(client)['token']}).status_code == 400
    assert client.get('/api/products/trash').json()['total'] == 2
    assert not list((backend.DATA_DIR / 'backups').glob('*.zip'))


def test_knowledge_association_blocks_all_cleanup_until_explicit_unlink(client):
    linked, other = product(client), product(client, '另一个商品')
    document = client.post('/api/knowledge/documents',json={
        'title':'关联规范','content':'需要保留产品资料。','product_ids':[linked['id']]}).json()
    remove(client, linked)
    remove(client, other)
    plan = preview(client)
    assert plan['count'] == 2 and not plan['can_purge']
    assert [item['id'] for item in plan['blocked']] == [linked['id']]
    assert purge(client, plan).status_code == 409
    assert client.get('/api/products/trash').json()['total'] == 2
    client.patch('/api/knowledge/documents/'+document['id'],json={
        'product_ids':[],'expected_revision':document['revision']}).raise_for_status()
    assert purge(client, preview(client)).status_code == 200
    # Historical source labels remain in immutable knowledge versions.
    assert client.get('/api/knowledge/documents/'+document['id']+'/versions').status_code == 200


def test_new_business_fk_is_not_silently_deleted(client):
    item = product(client)
    remove(client, item)
    with backend._connection(write=True) as conn:
        conn.execute('CREATE TABLE future_reference(product_id TEXT REFERENCES products(id))')
        conn.execute('INSERT INTO future_reference VALUES(?)',(item['id'],))
    plan = preview(client)
    assert not plan['can_purge'] and plan['blocked'][0]['id'] == item['id']
    assert purge(client, plan).status_code == 409


def test_backup_failure_prevents_database_and_media_changes(client, monkeypatch):
    item = product(client)
    remove(client, item)
    plan = preview(client)
    def fail(_):
        raise HTTPException(409,'测试备份失败')
    monkeypatch.setattr(backend,'_make_backup',fail)
    assert purge(client, plan).status_code == 409
    assert client.get('/api/products/trash').json()['total'] == 1
    assert client.get('/api/products/trash/'+item['id']+'/images/'+item['images'][0]['id']).status_code == 200


def test_cleanup_transaction_failure_retains_rows_and_originals(client, monkeypatch):
    item = product(client)
    remove(client, item)
    plan = preview(client)
    original_audit = backend._audit
    def fail(conn,user,action,*args,**kwargs):
        if action == 'purge_products':
            raise HTTPException(409,'测试事务失败')
        return original_audit(conn,user,action,*args,**kwargs)
    monkeypatch.setattr(backend,'_audit',fail)
    assert purge(client, plan).status_code == 409
    assert client.get('/api/products/trash').json()['total'] == 1
    assert client.get('/api/products/trash/'+item['id']+'/images/'+item['images'][0]['id']).status_code == 200
    assert len(list((backend.DATA_DIR / 'backups').glob('*.zip'))) == 1


def test_empty_cleanup_does_not_make_redundant_backup(client):
    plan = preview(client)
    assert plan['count'] == 0 and not plan['can_purge']
    assert purge(client, plan).json() == {'ok':True,'removed':0,'backup_created':False}


def test_store_import_provenance_is_backed_up_and_cleaned_only_for_purged_products(client, tmp_path):
    store_import_storage.init_storage()
    removed,active=product(client),product(client,'保留来源商品')
    with backend._connection(write=True) as conn:
        for index,item in enumerate((removed,active)):
            conn.execute('''INSERT INTO store_import_sources(
                source_key,product_key,platform,store_id,store_name,site,source_product_id,
                source_variant_id,product_id,listing_id,source_url,image_url,captured_title,captured_spec,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'source-{index}',f'product-{index}','Temu','fixture-shop','虚构测试店','www.temu.com',
                 f'public-{index}','variant-fixture',item['id'],item['listings'][0]['id'],
                 'https://www.temu.com/g-fixture.html','https://img.kwcdn.com/fixture.png',
                 '合成采样商品','测试规格',backend.now()))
    remove(client,removed)
    plan=preview(client)
    assert plan['can_purge'] and not plan['blocked']
    result=purge(client,plan)
    assert result.status_code==200,result.text
    with backend._connection() as conn:
        assert [row[0] for row in conn.execute('SELECT product_id FROM store_import_sources')]==[active['id']]
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    with zipfile.ZipFile(backend.DATA_DIR/'backups'/result.json()['backup_name']) as archive:
        snapshot=tmp_path/'import-restore-check.db'
        snapshot.write_bytes(archive.read('library.db'))
    with sqlite3.connect(snapshot) as saved:
        assert saved.execute('SELECT COUNT(*) FROM store_import_sources').fetchone()[0]==2

