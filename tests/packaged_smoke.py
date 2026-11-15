"""Manual 1.7 executable smoke; copy this file to tests/ before running.

Only a newly named qa-data library and port 8767 are used. Cloud AI is a local
stub; web search stays disabled; FX comes from synthetic SQLite cache rows.
The packaged visual model uses bundled local weights without downloading them.
Store collection is tested only at capability/auth/session/rejection boundaries:
this does not claim a successful real storefront login or collection run.
"""
import io
import json
import os
import runpy
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import closing

import httpx


BASE_URL = "http://127.0.0.1:8767"
PASSWORD = "packaged-test-only"
EXPECTED_VERSION = "1.7.0"


def result(response):
    response.raise_for_status()
    return response.json()


def db_rows(data, table):
    # Table names are constants owned by this test, never request input.
    with closing(sqlite3.connect(data / 'library.db')) as conn:
        return conn.execute('SELECT * FROM "' + table + '" ORDER BY 1').fetchall()


def business_snapshot(data):
    return {table: db_rows(data, table) for table in (
        'products', 'images', 'product_listings', 'price_tracking', 'price_history',
        'store_import_sources', 'knowledge_documents', 'knowledge_document_versions',
        'knowledge_document_products', 'company_announcements', 'announcement_assets',
        'announcement_revisions', 'activity')}


def member_update(owner, member_id, **fields):
    current = next(item for item in result(owner.get('/api/team'))['items'] if item['id'] == member_id)
    return owner.patch('/api/team/' + member_id,
                       json={'expected_revision': current['revision'], **fields})


def login(client, name):
    return result(client.post('/api/login', json={'name': name, 'password': PASSWORD}))['user']


def verify_permissions(owner, admin, employee, peer, product_id, document_id):
    boss = result(owner.get('/api/me'))['user']
    assert boss['role'] == 'owner' and boss['is_owner']
    catalog = result(owner.get('/api/permissions'))
    assert catalog['owner_id'] == boss['id']
    assert set(boss['permissions']) == {item['key'] for item in catalog['items']}
    assert owner.post('/api/setup', json={'name': '第二老板', 'password': PASSWORD}).status_code == 409
    assert owner.post('/api/team', json={'name': '第二老板', 'password': PASSWORD, 'role': 'owner'}).status_code == 400
    assert member_update(owner, boss['id'], disabled=True).status_code == 403
    members = {}
    for name, role in (('打包管理员', 'admin'), ('打包员工甲', 'operator'), ('打包员工乙', 'operator')):
        member = result(owner.post('/api/team', json={'name': name, 'password': PASSWORD, 'role': role}))
        members[name] = member
    admin_user = login(admin, '打包管理员')
    worker = login(employee, '打包员工甲')
    login(peer, '打包员工乙')
    assert admin_user['role'] == 'admin' and not admin_user['is_owner']
    assert worker['role'] == 'operator' and not any(key.startswith('knowledge.') for key in worker['permissions'])
    team = result(owner.get('/api/team'))['items']
    assert [item['id'] for item in team if item['is_owner']] == [boss['id']]
    assert all('permission_overrides' in item for item in team)
    assert all('permission_overrides' not in item for item in result(admin.get('/api/team'))['items'])
    assert admin.patch('/api/team/' + worker['id'], json={'expected_revision': 0, 'disabled': True}).status_code == 403
    assert admin.post('/api/team', json={'name': '越权管理员', 'password': PASSWORD, 'role': 'admin'}).status_code == 403
    assert member_update(owner, worker['id'], permissions={'team.manage': True}).status_code == 400
    assert member_update(owner, worker['id'], permissions={'knowledge.create': True}).status_code == 400
    for session in (admin, employee):
        assert session.post('/api/backup').status_code == 403
        assert session.put('/api/ai/config', json={'share_library_context': False}).status_code == 403
    assert employee.post('/api/knowledge/documents', json={'title': '越权知识', 'content': '不应写入'}).status_code == 403
    assert employee.patch('/api/knowledge/documents/' + document_id,
                          json={'expected_revision': 1, 'title': '越权修改'}).status_code == 403
    assert employee.delete('/api/knowledge/documents/' + document_id).status_code == 403
    assert admin.delete('/api/knowledge/documents/' + document_id).status_code == 403
    assert employee.get('/api/knowledge/documents/' + document_id + '/versions').status_code == 403
    product_url = '/api/products/' + product_id
    current = result(owner.get(product_url))
    assert not result(employee.get(product_url))['can_edit']
    assert employee.patch(product_url, json={'notes': '越权写入'}).status_code == 403
    granted = result(member_update(owner, worker['id'], permissions={'products.edit_all': True}))
    assert granted['revision'] == worker['revision'] + 1
    assert 'products.edit_all' in result(employee.get('/api/me'))['user']['permissions']
    assert result(employee.get(product_url))['can_edit']
    assert not result(peer.get(product_url))['can_edit'], 'A grant must apply to only the selected member'
    employee.patch(product_url, json={'notes': current['notes']}).raise_for_status()
    result(member_update(owner, worker['id'], permissions={}))
    assert employee.patch(product_url, json={'notes': '撤权后不可写入'}).status_code == 403
    assert 'products.edit_all' not in result(employee.get('/api/me'))['user']['permissions']
    assert owner.patch('/api/team/' + worker['id'],
                       json={'expected_revision': worker['revision'], 'disabled': True}).status_code == 409
    # The current administrator session is also narrowed immediately.
    result(member_update(owner, admin_user['id'], permissions={'products.edit_all': False}))
    assert admin.patch(product_url, json={'notes': '管理员撤权后不可写入'}).status_code == 403
    result(member_update(owner, admin_user['id'], permissions={}))
    assert result(admin.get(product_url))['can_edit']
    result(member_update(owner, worker['id'], disabled=True))
    assert employee.get('/api/me').status_code == 401
    assert employee.get(product_url).status_code == 401
    assert employee.post('/api/login', json={'name': worker['name'], 'password': PASSWORD}).status_code == 401
    assert owner.get(product_url).status_code == 200
    result(member_update(owner, worker['id'], disabled=False))
    assert employee.get('/api/me').status_code == 401, 'Re-enabling must not resurrect revoked sessions'
    login(employee, worker['name'])
    return boss, admin_user, worker


def verify_announcements(owner, admin, employee, original, data, admin_user):
    base = '/api/announcements'
    notice = result(owner.post(base, json={'title': '打包公司公告', 'content_html':
        '<script>fixtureScript()</script><p onclick="bad()" style="color:#ff0000;position:fixed">'
        '<strong>发布前核对规格</strong></p><a href="javascript:bad()">安全提示</a>'}))
    url = base + '/' + notice['id']
    clean = notice['content_html']
    assert '<strong>' in clean and 'color:#ff0000' in clean
    assert all(token not in clean for token in ('script', 'onclick', 'position:', 'javascript:', 'fixtureScript'))
    assert notice['status'] == 'draft' and notice['revision'] == 1
    assert employee.get(url).status_code == 404
    assert employee.get(base, params={'status': 'draft'}).status_code == 403
    assert employee.post(base, json={'title': '员工不可写', 'content_html': '<p>不可写</p>'}).status_code == 403
    assert employee.patch(url, json={'expected_revision': 1, 'title': '不可写'}).status_code == 403
    assert employee.post(url + '/publish', json={'expected_revision': 1}).status_code == 403
    assert employee.post(url + '/archive', json={'expected_revision': 1}).status_code == 403
    assert employee.get(url + '/history').status_code == 403
    # A public remote image is rejected before it could cause a network request.
    assert owner.patch(url, json={'expected_revision': 1,
        'content_html': '<img src="https://invalid.example/never-request.png">'}).status_code == 400
    assert result(owner.get(url))['revision'] == 1
    assert admin.get(url).status_code == 200
    result(member_update(owner, admin_user['id'], permissions={'announcements.edit': False, 'announcements.publish': False}))
    assert admin.patch(url, json={'expected_revision': 1, 'title': '已撤权'}).status_code == 403
    assert admin.get(url).status_code == 404
    result(member_update(owner, admin_user['id'], permissions={}))
    attachment_text = '仅用于打包公告验证，无真实资料。'.encode()
    notice = result(owner.post(url + '/attachments', data={'expected_revision': str(notice['revision'])}, files=[
        ('files', ('公告图.png', original, 'image/png')),
        ('files', ('公告附件.txt', attachment_text, 'text/plain'))]))
    photo = next(item for item in notice['attachments'] if item['kind'] == 'image')
    file = next(item for item in notice['attachments'] if item['kind'] == 'file')
    photo_bytes = owner.get(photo['url']).content
    assert photo['mime'] == 'image/jpeg' and photo_bytes.startswith(b'\xff\xd8')
    assert employee.get(photo['url']).status_code == 404
    old_revision = notice['revision']
    notice = result(owner.patch(url, json={'expected_revision': old_revision,
        'content_html': clean + '<img src="' + photo['url'] + '" onerror="bad()" width="160">'}))
    assert photo['url'] in notice['content_html'] and 'onerror' not in notice['content_html']
    assert owner.patch(url, json={'expected_revision': old_revision, 'title': '过时编辑'}).status_code == 409
    notice = result(owner.post(url + '/publish', json={'expected_revision': notice['revision']}))
    published_revision = notice['revision']
    public = result(employee.get(url))
    assert public['status'] == 'published' and not public['can_edit'] and not public['can_publish'] and not public['can_archive']
    assert public['revision'] == published_revision
    assert result(employee.get(base))['total'] == 1
    employee.get(photo['url']).raise_for_status()
    download = employee.get(file['url'])
    assert download.content == attachment_text and 'attachment;' in download.headers['content-disposition']
    assert download.headers['x-content-type-options'] == 'nosniff'
    notice = result(owner.patch(url, json={'expected_revision': notice['revision'],
        'title': '尚未公开的工作稿', 'content_html': '<p>尚未公开的正文</p><img src="' + photo['url'] + '">'}))
    draft_text = b'synthetic draft attachment only'
    notice = result(owner.post(url + '/attachments', data={'expected_revision': str(notice['revision'])},
        files={'files': ('工作稿.txt', draft_text, 'text/plain')}))
    draft_asset = next(item for item in notice['attachments'] if item['name'] == '工作稿.txt')
    public = result(employee.get(url))
    assert public['title'] == '打包公司公告' and public['revision'] == published_revision
    assert '尚未公开' not in public['content_html']
    assert result(employee.get(base))['items'][0]['title'] == '打包公司公告'
    assert employee.get(draft_asset['url']).status_code == 404
    assert any(item['id'] == notice['id'] for item in result(owner.get(base, params={'status': 'draft'}))['items'])
    notice = result(owner.post(url + '/publish', json={'expected_revision': notice['revision']}))
    assert result(employee.get(url))['title'] == '尚未公开的工作稿'
    assert employee.get(draft_asset['url']).content == draft_text
    assert employee.post(url + '/read', json={'revision': published_revision}).status_code == 409
    assert result(employee.post(url + '/read', json={'revision': notice['revision']}))['unread_count'] == 0
    notice = result(owner.post(url + '/archive', json={'expected_revision': notice['revision']}))
    assert notice['status'] == 'archived' and employee.get(url).status_code == 404
    assert employee.get(photo['url']).status_code == 404 and result(employee.get(base))['total'] == 0
    assert any(item['id'] == notice['id'] for item in result(owner.get(base, params={'status': 'archived'}))['items'])
    notice = result(owner.post(url + '/restore', json={'expected_revision': notice['revision']}))
    assert notice['status'] == 'draft' and employee.get(url).status_code == 404
    notice = result(owner.post(url + '/publish', json={'expected_revision': notice['revision']}))
    employee.get(url).raise_for_status()
    assets = {photo['id']: photo_bytes, file['id']: attachment_text, draft_asset['id']: draft_text}
    with closing(sqlite3.connect(data / 'library.db')) as conn:
        stored = dict(conn.execute('SELECT id,data FROM announcement_assets WHERE announcement_id=?', (notice['id'],)))
        assert stored == assets, 'Announcement bytes must be stored as database BLOBs'
    return notice['id'], assets


def verify_merge_and_graph(owner, employee, product_id, document_id, original_content, original_revision, picture, data):
    product = result(owner.get('/api/products/' + product_id))
    source = result(owner.post('/api/products', data={'metadata': json.dumps({
        'name': '合并撤销来源样品', 'category': '验收合并', 'notes': '仅合成数据',
        'listings': [{'platform': 'Temu', 'store': '合成店铺', 'product_code': 'SRC-001',
                      'product_number': '00009', 'status': 'listed', 'price': '40', 'currency': 'USD',
                      'link': 'https://shop.example.com/p/source', 'variant_hint': '合成规格'}]})},
        files={'images': ('merge-source.png', picture, 'image/png')}))
    source_prices = result(owner.get('/api/price-tracking/products/' + source['id']))['listings']
    result(owner.post('/api/price-tracking/listings/' + source_prices[0]['id'] + '/manual-price',
        json={'price': '22', 'currency': 'USD', 'expected_context': source_prices[0]['context_token']}))
    doc_url = '/api/knowledge/documents/' + document_id
    old_version = result(owner.get(doc_url + '/versions/' + str(original_revision)))
    assert old_version['content'] == original_content and old_version['product_ids'] == []
    current = result(owner.get(doc_url))
    linked = result(owner.patch(doc_url, json={'expected_revision': current['revision'],
        'product_ids': [source['id'], product_id], 'content': original_content + '\n人工关联：仅按核对结果维护。'}))
    assert linked['product_ids'] == [source['id'], product_id]
    assert {item['id'] for item in linked['related_products']} == {source['id'], product_id}
    before_stale = db_rows(data, 'knowledge_document_versions')
    assert owner.patch(doc_url, json={'expected_revision': current['revision'], 'content': '过时内容不可覆盖'}).status_code == 409
    assert db_rows(data, 'knowledge_document_versions') == before_stale
    assert result(owner.get(doc_url))['content'] == linked['content']
    graph = result(employee.get('/api/knowledge/graph'))
    stats = result(owner.get('/api/knowledge/stats'))
    assert graph['totals'] == {'products': 2, 'documents': 2, 'skus': 3, 'sources': 4}
    assert graph['totals']['sources'] == stats['total_sources']
    assert graph['shown'] == graph['matched'] and not graph['truncated']
    assert any(edge == {'source': 'product:' + source['id'], 'target': 'knowledge:' + document_id,
                        'relation': 'documented_by'} for edge in graph['edges'])
    assert graph['policy']['persistent'] and not graph['policy']['automatic_decay'] and not graph['policy']['automatic_cleanup']
    selection = {'ids': [product_id, source['id']], 'target_id': product_id}
    assert employee.post('/api/products/merge/preview', json=selection).status_code == 403
    tables = ('images', 'product_listings', 'price_tracking', 'price_history')
    before = {table: db_rows(data, table) for table in tables}
    before_prices = {identity: result(owner.get('/api/price-tracking/products/' + identity))['listings']
                     for identity in (product_id, source['id'])}
    preview = result(owner.post('/api/products/merge/preview', json=selection))
    assert preview['image_count'] == 2 and preview['listing_count'] == 3
    assert {table: db_rows(data, table) for table in tables} == before
    merged = result(owner.post('/api/products/merge', json={**selection, 'expected_token': preview['expected_token']}))
    assert merged['ok'] and merged['can_undo'] and merged['merged_ids'] == [source['id']]
    assert {item['id'] for item in merged['product']['images']} == {item['id'] for item in product['images'] + source['images']}
    assert {item['id'] for item in merged['product']['listings']} == {item['id'] for item in product['listings'] + source['listings']}
    assert owner.get('/api/products/' + source['id']).status_code == 404
    assert db_rows(data, 'price_history') == before['price_history']
    assert db_rows(data, 'price_tracking') == before['price_tracking']
    for image in product['images'] + source['images']:
        owner.get(image['url']).raise_for_status()
    merged_doc = result(owner.get(doc_url))
    assert merged_doc['product_ids'] == [product_id] and merged_doc['revision'] == linked['revision'] + 1
    assert result(owner.get(doc_url + '/versions/' + str(linked['revision'])))['product_ids'] == [source['id'], product_id]
    merged_graph = result(owner.get('/api/knowledge/graph'))
    assert merged_graph['totals'] == {'products': 1, 'documents': 2, 'skus': 3, 'sources': 3}
    journal = result(owner.get('/api/product-merges/' + merged['merge_id']))
    assert journal['can_undo'] and journal['undo_token'] == merged['undo_token']
    restored = result(owner.post('/api/product-merges/' + merged['merge_id'] + '/undo',
                                json={'expected_token': journal['undo_token']}))
    assert restored['ok'] and restored['restored_ids'] == [source['id']]
    assert {table: db_rows(data, table) for table in tables} == before, 'Undo must restore original identities, ownership, quotes and history'
    for identity in (product_id, source['id']):
        after_prices = result(owner.get('/api/price-tracking/products/' + identity))['listings']
        assert [(item['id'], item['context_token'], item['current_price']) for item in after_prices] == [
            (item['id'], item['context_token'], item['current_price']) for item in before_prices[identity]]
    after = result(owner.get(doc_url))
    assert after['product_ids'] == [source['id'], product_id] and after['revision'] == merged_doc['revision'] + 1
    actions = {item['action'] for item in result(owner.get(doc_url + '/versions'))['items']}
    assert {'create', 'update', 'archive', 'restore', 'product_merge', 'product_merge_undo'} <= actions
    assert result(owner.get(doc_url + '/versions/' + str(original_revision))) == old_version
    assert result(owner.get('/api/knowledge/graph'))['totals'] == graph['totals']
    assert not result(owner.get('/api/product-merges/' + merged['merge_id']))['can_undo']
    assert owner.post('/api/product-merges/' + merged['merge_id'] + '/undo',
                      json={'expected_token': journal['undo_token']}).status_code == 409


def verify_store_boundaries(owner, employee, worker, data):
    # Never call an authorized browser/start or manufacture a collector preview.
    # Successful Temu login/collection is explicitly outside this smoke's claim.
    base = '/api/store-import'
    unknown = '0' * 32
    selection = {'selections': [{'item_id': 'synthetic-uncollected-item', 'action': 'new'}], 'mark_listed': True}
    before = business_snapshot(data)
    capabilities = result(owner.get(base + '/capabilities'))
    assert capabilities['available'] and capabilities['can_use'] and capabilities['persistent_login']
    assert capabilities['platforms'] == ['Temu'] and capabilities['batch_limit'] == 20
    assert result(employee.get(base + '/capabilities'))['can_use']
    assert owner.get(base + '/browser/' + unknown).status_code == 404
    assert owner.get(base + '/previews/' + unknown).status_code == 404
    assert owner.post(base + '/previews/' + unknown + '/commit', json=selection).status_code == 404
    assert owner.post(base + '/previews', json={'items': [{'title': '客户端不可伪造草稿'}]}).status_code in {404, 405}
    with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=20) as anonymous:
        assert anonymous.get(base + '/capabilities').status_code == 401
        assert anonymous.get(base + '/browser/' + unknown).status_code == 401
        assert anonymous.post(base + '/previews/' + unknown + '/commit', json=selection).status_code == 401
        # A separate logged-in session cannot find or commit a nonexistent draft.
        login(anonymous, worker['name'])
        assert anonymous.get(base + '/browser/' + unknown).status_code == 404
        assert anonymous.post(base + '/previews/' + unknown + '/commit', json=selection).status_code == 404
    assert business_snapshot(data) == before, 'Capabilities/session checks and missing drafts must not write any business data'
    result(member_update(owner, worker['id'], permissions={'products.import': False}))
    before_denied = business_snapshot(data)
    assert employee.get(base + '/capabilities').status_code == 403
    assert employee.post(base + '/browser/start', json={'url': 'https://www.temu.com/mall.html?mall_id=12345'}).status_code == 403
    assert employee.post(base + '/previews/' + unknown + '/commit', json=selection).status_code == 403
    assert business_snapshot(data) == before_denied, 'Revoked import must fail before jobs, drafts or catalog writes'
    result(member_update(owner, worker['id'], permissions={}))
    assert result(employee.get(base + '/capabilities'))['can_use']


def main():
    root = Path(__file__).resolve().parents[1]
    if not (root / 'tests' / 'test_image_engine.py').is_file():
        raise RuntimeError('Copy the reviewed release smoke to tests/packaged_smoke.py before running it.')
    sys.path.insert(0, str(root))
    executable = Path(os.environ.get("MIDOO_SMOKE_EXE", str(root / "dist" / "windows" / "Midoo.exe"))).resolve(strict=True)
    data = root / "qa-data" / ("packaged-" + str(time.time_ns()))
    data.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, MIDOO_DATA_DIR=str(data), MIDOO_HOST='127.0.0.1', MIDOO_PORT='8767',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    # Fail before launching or making HTTP requests if the dedicated QA port is busy.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 8767))
    def seed_reference_rates():
        # Synthetic cache only: the frozen executable must not fetch real FX.
        current = datetime.now(timezone.utc)
        cache = {'version':1, 'base':'EUR', 'as_of':current.date().isoformat(),
                 'fetched_at':current.isoformat(), 'rates':{'EUR':'1','USD':'1.25','CNY':'9'}}
        with closing(sqlite3.connect(data / 'library.db')) as conn:
            conn.execute('INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         ('exchange_rates:ecb', json.dumps(cache)))
            conn.commit()
        return cache['as_of']
    def quote_snapshot():
        with closing(sqlite3.connect(data / 'library.db')) as conn:
            return {name:conn.execute('SELECT * FROM '+name+' ORDER BY 1').fetchall()
                    for name in ('product_listings','price_tracking','price_history')}
    runtime_report = data / 'browser-runtime.json'
    runtime_check = subprocess.run([str(executable), '--check-browser-runtime', str(runtime_report)], env=env, timeout=40)
    assert runtime_check.returncode == 0
    driver = json.loads(runtime_report.read_text(encoding='utf-8'))
    assert driver.get('ok') and driver.get('windowed'), driver
    process = subprocess.Popen([str(executable), "--serve", "--host", "127.0.0.1", "--port", "8767"], env=env)
    seen = []
    class StubProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            raw = json.dumps({'choices': [{'message': {'content': '本地打包联调成功'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    stub = ThreadingHTTPServer(('127.0.0.1', 0), StubProvider)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=BASE_URL, trust_env=False, timeout=20) as client:
            for _ in range(120):
                try:
                    if client.get("/api/health").json().get("application") == "midoo":
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                if process.poll() is not None:
                    raise RuntimeError("Packaged process exited before readiness")
                time.sleep(0.25)
            else:
                raise RuntimeError("Packaged server did not become ready")
            assert client.get("/").status_code == 200
            assert client.get("/api/health").json()["version"] == EXPECTED_VERSION
            assert client.get("/static/app.js").status_code == 200
            for asset in ('announcements.js', 'announcements.css', 'knowledge-nebula.js',
                          'knowledge-nebula.css', 'product-merge.js', 'product-merge.css',
                          'store-import.js', 'store-import.css'):
                client.get('/static/' + asset).raise_for_status()
            assert client.get("/api/bootstrap").json()["needs_setup"]
            client.post("/api/setup", json={"name": "打包测试", "password": PASSWORD}).raise_for_status()
            assert Path(result(client.get('/api/system'))['data_path']).resolve() == data.resolve()
            fx_day = seed_reference_rates()
            fixtures = runpy.run_path(str(root / "tests" / "test_image_engine.py"))
            picture = fixtures["product_fixture"]()
            original = fixtures["image_bytes"](picture)
            response = client.post("/api/products", data={"metadata": json.dumps({"name": "打包验证样品", "category": "柱蜡", "status": "pending"})}, files={"images": ("fixture.png", original, "image/png")})
            response.raise_for_status()
            product = response.json()
            assert product['category'] == '柱蜡'
            assert client.get('/api/categories').json()['items'] == [{'name':'柱蜡','count':1}]
            assert client.get('/api/products',params={'category':'柱蜡'}).json()['total'] == 1
            assert client.get('/api/products',params={'category':'水晶'}).json()['total'] == 0
            assert product["workflow_state"] == "unconfigured"
            queues = client.get('/api/stats').json()['workflow_counts']
            assert all(queues[name] == 1 for name in ('unconfigured','pricing','pending'))
            assert set(product['workflow_states']) >= {'unconfigured','pricing','pending'}
            updated = client.patch('/api/products/' + product['id'], json={'listings':[
                {'platform':'Temu','product_code':'TM-001','product_number':'00008','status':'listed','price':'20','currency':'USD',
                 'store':'打包店铺','link':'https://shop.example.com/p/smoke','region':'美国','variant_hint':'红色2件装'},
                {'platform':'Amazon','status':'archived','price':'19.90','currency':'USD'}]})
            updated.raise_for_status()
            assert updated.json()['workflow_state'] == 'listed'
            assert client.get('/api/products', params={'platform':'Amazon','listing_status':'archived'}).json()['total'] == 1
            first = updated.json()['listings'][0]
            prices_url = '/api/price-tracking/products/' + product['id']
            child = client.post('/api/price-tracking/listings/' + first['id'] + '/variants', json={
                'product_code':'TM-002','original_price':'40','variant_hint':'蓝色4件装'})
            child.raise_for_status()
            saved = client.get('/api/products/' + product['id']).json()['listings']
            assert saved[-1]['status'] == 'pending' and saved[-1]['product_number'] == '00008'
            rows = client.get(prices_url).json()['listings']
            row = next(item for item in rows if item['id'] == first['id'])
            quote_url = '/api/price-tracking/listings/' + first['id'] + '/manual-price'
            payload = {'price':'12.5','currency':'USD','expected_context':row['context_token']}
            quote = client.post(quote_url, json=payload)
            quote.raise_for_status()
            assert quote.json()['ratio_percent'] == '62.5' and quote.json()['source'] == 'manual_entry'
            assert client.post(quote_url, json=payload).status_code == 409
            rows = client.get(prices_url).json()['listings']
            assert next(item for item in rows if item['id'] == child.json()['id'])['current_price'] == ''
            client.patch('/api/products/' + product['id'], json={'listings':saved[:-1]}).raise_for_status()
            rows = client.get(prices_url).json()['listings']
            assert len(rows) == 2 and next(item for item in rows if item['id'] == first['id'])['current_price'] == '12.5'
            clear = client.post('/api/price-tracking/listings/' + first['id'] + '/browser/profile/clear')
            clear.raise_for_status()
            assert clear.json()['ok']
            editor_rows = client.get('/api/products/' + product['id']).json()['listings']
            editor_rows[0].update(price='74.62', currency='CNY')
            client.patch('/api/products/' + product['id'], json={'listings':editor_rows}).raise_for_status()
            before_fx = client.get(prices_url).json()['listings'][0]
            fx_quote = client.post(quote_url, json={'price':'10.54','currency':'USD','expected_context':before_fx['context_token']})
            fx_quote.raise_for_status()
            converted = fx_quote.json()
            assert converted['original_price'] == '74.62' and converted['currency'] == 'CNY'
            assert converted['current_price'] == '10.54' and converted['current_currency'] == 'USD'
            assert converted['ratio_percent'] == '101.7' and converted['fx']['rate'] == '7.2'
            assert converted['fx']['converted_amount'] == '75.888' and converted['fx']['available']
            assert converted['fx']['as_of'] == fx_day
            before_read = quote_snapshot()
            reread = client.get(prices_url).json()['listings'][0]
            assert reread['context_token'] == converted['context_token'] and reread['ratio_percent'] == '101.7'
            assert quote_snapshot() == before_read, 'FX-derived view must not change raw quotes, SKU identity or history'
            # A stale cache plus a recent failed attempt must show unavailable,
            # keep the raw price, and avoid any real network request.
            with closing(sqlite3.connect(data / 'library.db')) as conn:
                raw = json.loads(conn.execute('SELECT value FROM app_meta WHERE key=?', ('exchange_rates:ecb',)).fetchone()[0])
                raw['as_of'] = (datetime.now(timezone.utc).date() - timedelta(days=8)).isoformat()
                conn.execute('UPDATE app_meta SET value=? WHERE key=?', (json.dumps(raw), 'exchange_rates:ecb'))
                conn.execute('INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                             ('exchange_rates:ecb:last_attempt',json.dumps({'at':datetime.now(timezone.utc).isoformat(),'failed':True})))
                conn.commit()
            unavailable = client.get(prices_url).json()['listings'][0]
            assert unavailable['ratio_percent'] is None and unavailable['fx']['available'] is False
            assert unavailable['current_price'] == '10.54' and unavailable['context_token'] == converted['context_token']
            assert quote_snapshot() == before_read
            seed_reference_rates()
            assert client.get(prices_url).json()['listings'][0]['ratio_percent'] == '101.7'
            client.post('/api/products/bulk-delete',json={'ids':[product['id']]}).raise_for_status()
            trash = client.get('/api/products/trash').json()
            assert trash['total'] == 1
            client.get(trash['items'][0]['images'][0]['thumb_url']).raise_for_status()
            client.post('/api/products/'+product['id']+'/restore').raise_for_status()
            assert client.get('/api/products/'+product['id']).json()['listings'][0]['product_number'] == '00008'
            client.get(product["images"][0]["thumb_url"]).raise_for_status()
            query = fixtures["image_bytes"](picture.crop((125, 135, 540, 535)), "JPEG", quality=80)
            search = client.post("/api/search", files={"images": ("crop.jpg", query, "image/jpeg")})
            search.raise_for_status()
            assert search.json()["items"][0]["product"]["id"] == product["id"]
            assert search.json()["items"][0]["score"] > 70
            assert search.json()["engine_status"]["ready"]
            assert "preprocess-v2" in search.json()["engine_status"]["model_id"]
            assert not client.get("/api/ai/config").json()["providers"][0]["configured"]
            assert client.post("/api/ai/chat", json={"message": "测试"}).status_code == 409
            config = client.put('/api/ai/config', json={'providers': [{'id': 'deepseek', 'model': 'deepseek-flash',
                'enabled': True, 'api_key': 'packaged-stub-only', 'vision': True,
                'base_url': f'http://127.0.0.1:{stub.server_port}/v1'}]})
            config.raise_for_status()
            assert 'max' in [v['value'] for v in config.json()['providers'][0]['reasoning']['options']]
            assert config.json()['share_knowledge_context'] is False
            assert config.json()['web_search']['available'] is False
            document = client.post('/api/knowledge/documents', json={'title':'打包知识-上新查重流程',
                'category':'验收', 'content':'上新查重流程：先查公司商品图库，由原负责人核对规格，再登记产品。'})
            document.raise_for_status()
            document_id = document.json()['id']
            document_initial_revision = document.json()['revision']
            document_initial_content = document.json()['content']
            linked_document = result(client.patch('/api/knowledge/documents/' + document_id,
                json={'expected_revision': document_initial_revision, 'product_ids': [product['id']]}))
            assert linked_document['revision'] == document_initial_revision + 1
            assert linked_document['product_ids'] == [product['id']]
            assert linked_document['related_products'][0]['id'] == product['id']
            assert client.get('/api/knowledge/documents/' + document_id).json()['content'].startswith('上新查重流程')
            imported = client.post('/api/knowledge/documents/import', files={'files':('流程.txt','包装知识：先核对件数再装箱。'.encode(),'text/plain')})
            imported.raise_for_status()
            assert imported.json()['items'] and imported.json()['original_files_stored'] is False
            client.put('/api/ai/config',json={'share_library_context':True,'share_knowledge_context':True}).raise_for_status()
            product_answer = client.post('/api/ai/chat',json={'message':'找到打包验证样品并展示图片','mode':'company'})
            product_answer.raise_for_status()
            assert product_answer.json()['product_cards'][0]['id'] == product['id']
            assert product_answer.json()['product_cards'][0]['images'][0]['thumb_url'].startswith('/media/')
            company_answer = client.post('/api/ai/chat',json={'message':'上新查重流程是什么','mode':'company'})
            company_answer.raise_for_status()
            assert any(s.get('document_id')==document_id for s in company_answer.json()['sources'])
            assert '上新查重流程' in seen[-1]['messages'][0]['content']
            general = client.post('/api/ai/chat',json={'message':'请说明一个数学例子','mode':'general'})
            general.raise_for_status()
            assert not general.json()['context_shared'] and general.json()['product_cards'] == []
            assert 'company_evidence' not in seen[-1]['messages'][0]['content']
            budget_before = client.get('/api/ai/config').json()['requests_today']
            blocked_search = client.post('/api/ai/chat',json={'message':'搜索测试','search_enabled':True,'web_query':'公开测试词'})
            assert blocked_search.status_code == 403
            assert client.get('/api/ai/config').json()['requests_today'] == budget_before
            active_document = result(client.get('/api/knowledge/documents/' + document_id))
            client.request('DELETE', '/api/knowledge/documents/' + document_id,
                           json={'expected_revision': active_document['revision']}).raise_for_status()
            inactive = client.get('/api/knowledge/documents/' + document_id)
            inactive.raise_for_status()
            assert inactive.json()['deleted'] is True  # recovery view retains the original document
            assert document_id not in {item['id'] for item in client.get('/api/knowledge/documents').json()['items']}
            assert document_id in {item['id'] for item in client.get('/api/knowledge/documents',params={'deleted':True}).json()['items']}
            client.post('/api/knowledge/documents/' + document_id + '/restore',
                        json={'expected_revision': inactive.json()['revision']}).raise_for_status()
            assert client.get('/api/knowledge/stats').json()['documents'] == 2
            docs = runpy.run_path(str(root/'tests'/'test_chat_attachments.py'))
            chat = client.post('/api/ai/chat', data={'message': '整理附件', 'reasoning_effort': 'high'}, files=[
                ('files', ('photo.png', original, 'image/png')),
                ('files', ('spec.pdf', docs['pdf'](), 'application/pdf')),
                ('files', ('spec.docx', docs['docx'](), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
                ('files', ('spec.xlsx', docs['xlsx'](), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))])
            chat.raise_for_status()
            assert len(chat.json()['attachments']) == 4
            assert seen[-1]['reasoning_effort'] == 'high'
            sent = seen[-1]['messages'][-1]['content']
            assert '00123' in sent[0]['text'] and '6件套' in sent[0]['text']
            assert any(p['type'] == 'image_url' for p in sent)
            followup = client.post('/api/ai/chat', json={'message': '继续核对', 'history': [
                {'role':'user', 'content':'整理附件', 'attachments': chat.json()['attachments']}]})
            followup.raise_for_status()
            assert '00123' in seen[-1]['messages'][1]['content'][0]['text']
            year = datetime.now().year
            activity = client.get('/api/activity', params={'limit': 1, 'start_date': f'{year}-01-01', 'end_date': f'{year}-12-31'})
            activity.raise_for_status()
            assert activity.json()['total'] >= 1 and activity.json()['actors']
            client.get('/static/price-tracking.js').raise_for_status()
            price_settings = client.get('/api/price-tracking/settings')
            price_settings.raise_for_status()
            assert not price_settings.json()['enabled'] and not price_settings.json()['allow_ai']
            assert price_settings.json()['manual_only'] and price_settings.json()['next_run_at'] is None
            browser_capabilities = client.get('/api/price-tracking/browser/capabilities')
            browser_capabilities.raise_for_status()
            assert browser_capabilities.json()['available'], browser_capabilities.text
            assert browser_capabilities.json()['can_use'] and browser_capabilities.json()['local_only']
            assert browser_capabilities.json()['persistent_login'] is True
            with (httpx.Client(base_url=BASE_URL, trust_env=False, timeout=20) as admin,
                  httpx.Client(base_url=BASE_URL, trust_env=False, timeout=20) as employee,
                  httpx.Client(base_url=BASE_URL, trust_env=False, timeout=20) as peer):
                boss, admin_user, worker = verify_permissions(client, admin, employee, peer, product['id'], document_id)
                notice_id, notice_assets = verify_announcements(client, admin, employee, original, data, admin_user)
                verify_merge_and_graph(client, employee, product['id'], document_id,
                    document_initial_content, document_initial_revision,
                    fixtures['image_bytes'](fixtures['product_fixture'](alternate=True)), data)
                verify_store_boundaries(client, employee, worker, data)
                assert result(client.get('/api/me'))['user']['id'] == boss['id']
            for private_directory in ('browser-price-profiles', 'browser-store-profiles', 'desktop-webview'):
                private_fixture = data / private_directory / 'packaged-sentinel' / 'login-marker.txt'
                private_fixture.parent.mkdir(parents=True, exist_ok=True)
                private_fixture.write_text('Synthetic fixture, no real cookies', encoding='utf-8')
            expected_backup_tables = {table: db_rows(data, table) for table in (
                'knowledge_documents', 'knowledge_document_versions', 'knowledge_document_products',
                'company_announcements', 'announcement_assets', 'announcement_revisions')}
            backup = client.post("/api/backup")
            backup.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
                assert "library.db" in archive.namelist()
                assert any(name.startswith("images/") for name in archive.namelist())
                assert json.loads(archive.read('backup.json'))['version'] == EXPECTED_VERSION
                assert not any(any(private in name for private in (
                    'browser-price-profiles', 'browser-store-profiles', 'desktop-webview', 'login-marker'))
                    for name in archive.namelist())
                backup_database = data / 'backup-verification.db'
                backup_database.write_bytes(archive.read('library.db'))
                with closing(sqlite3.connect(backup_database)) as connection:
                    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
                    assert connection.execute('SELECT COUNT(*) FROM knowledge_documents WHERE deleted=0').fetchone()[0] == 2
                    for table, expected in expected_backup_tables.items():
                        assert connection.execute('SELECT * FROM "' + table + '" ORDER BY 1').fetchall() == expected, table
                    assert dict(connection.execute('SELECT id,data FROM announcement_assets WHERE announcement_id=?', (notice_id,))) == notice_assets
                    assert connection.execute('SELECT COUNT(*) FROM knowledge_document_products WHERE document_id=?', (document_id,)).fetchone()[0] == 2
                    assert connection.execute('SELECT content FROM knowledge_document_versions WHERE document_id=? AND revision=?',
                        (document_id, document_initial_revision)).fetchone()[0] == document_initial_content
                    assert {row[0] for row in connection.execute('SELECT action FROM knowledge_document_versions WHERE document_id=?', (document_id,))} >= {'product_merge', 'product_merge_undo'}
            print("PACKAGED_SMOKE_OK: 1.7 isolated startup/driver; sole owner, per-member capabilities, administrator/employee denials and live revocation; sanitized announcements, public/draft separation, image/file SQLite BLOBs and archive/restore; FTS knowledge CRUD/import/revisions/manual links/graph/backup; merge+undo preserves media/SKU/history IDs, quotes, tokens and knowledge links; local AI stub, company evidence/cards, general isolation, disabled web search, effort/attachments/follow-up; cached FX and local visual model; categories/recovery/SKU/manual-only prices; store capability/auth/session/missing-draft rejection leaves catalog unchanged; ZIP excludes price/store/webview profiles. No real search, FX, cloud or marketplace requests; real store login/collection remains experimental and is not validated by this smoke.")
    finally:
        stub.shutdown()
        stub.server_close()
        # Only this test's explicitly launched executable tree is stopped.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
