"""Manual executable smoke test. Creates only a named, disposable QA library."""
import io
import json
import os
import runpy
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


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    executable = Path(os.environ.get("MIDOO_SMOKE_EXE", str(root / "dist" / "windows" / "Midoo.exe")))
    data = root / "qa-data" / ("packaged-" + str(time.time_ns()))
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
    runtime_report = root / 'qa-data' / ('browser-runtime-' + str(time.time_ns()) + '.json')
    runtime_check = subprocess.run([str(executable), '--check-browser-runtime', str(runtime_report)], timeout=40)
    assert runtime_check.returncode == 0
    driver = json.loads(runtime_report.read_text(encoding='utf-8'))
    assert driver.get('ok') and driver.get('windowed'), driver
    env = dict(os.environ, MIDOO_DATA_DIR=str(data))
    process = subprocess.Popen([str(executable), "--serve", "--host", "127.0.0.1", "--port", "8767"], env=env)
    seen = []
    class StubProvider(BaseHTTPRequestHandler):
        def do_POST(self):
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
        with httpx.Client(base_url="http://127.0.0.1:8767", trust_env=False, timeout=20) as client:
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
            assert client.get("/api/health").json()["version"] == "1.6.0"
            assert client.get("/static/app.js").status_code == 200
            assert client.get("/api/bootstrap").json()["needs_setup"]
            client.post("/api/setup", json={"name": "打包测试", "password": "packaged-test-only"}).raise_for_status()
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
            client.delete('/api/knowledge/documents/' + document_id).raise_for_status()
            inactive = client.get('/api/knowledge/documents/' + document_id)
            inactive.raise_for_status()
            assert inactive.json()['deleted'] is True  # recovery view retains the original document
            assert document_id not in {item['id'] for item in client.get('/api/knowledge/documents').json()['items']}
            assert document_id in {item['id'] for item in client.get('/api/knowledge/documents',params={'deleted':True}).json()['items']}
            client.post('/api/knowledge/documents/' + document_id + '/restore').raise_for_status()
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
            activity = client.get('/api/activity', params={'limit': 1, 'start_date': '2026-01-01', 'end_date': '2026-12-31'})
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
            private_fixture = data / 'browser-price-profiles' / 'packaged-sentinel' / 'login-marker.txt'
            private_fixture.parent.mkdir(parents=True, exist_ok=True)
            private_fixture.write_text('Synthetic fixture, no real cookies', encoding='utf-8')
            backup = client.post("/api/backup")
            backup.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
                assert "library.db" in archive.namelist()
                assert any(name.startswith("images/") for name in archive.namelist())
                assert not any('browser-price-profiles' in name or 'login-marker' in name for name in archive.namelist())
                backup_database = data / 'backup-verification.db'
                backup_database.write_bytes(archive.read('library.db'))
                with closing(sqlite3.connect(backup_database)) as connection:
                    assert connection.execute('SELECT COUNT(*) FROM knowledge_documents WHERE deleted=0').fetchone()[0] == 2
            print("PACKAGED_SMOKE_OK: 1.6 startup/FTS knowledge CRUD/import/restore/backup, real product cards, grounded company evidence, general mode isolation and search permissions, official effort/attachments/follow-up; cached FX, quote/history/token preservation, local visual search, categories/recovery/SKU/manual-only prices, ZIP excludes profiles; no real search, FX, cloud or marketplace requests")
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
