"""Company knowledge tests use disposable SQLite libraries and no cloud calls."""
from contextlib import contextmanager
from contextlib import closing
import io
import json
from pathlib import Path
import runpy
import shutil
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

import backend
import knowledge_store as knowledge


@pytest.fixture
def client(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / "knowledge")
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, "_extract_semantic", lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    app.include_router(knowledge.router)
    with TestClient(app) as instance:
        instance.post('/api/setup', json={'name':'知识管理员','password':'knowledge-test-only'}).raise_for_status()
        yield instance


def note(client, title="上品核对规范", content="上品前核对规格、材质、套装数量，交给商品负责人复核。", **changes):
    response = client.post('/api/knowledge/documents', json={'title':title,'content':content, **changes})
    response.raise_for_status()
    return response.json()


def product(client, name="圣诞树造型蜡烛", **changes):
    stream = io.BytesIO()
    Image.new('RGB',(64,64),'#a67844').save(stream,'PNG')
    response = client.post('/api/products', data={'metadata':json.dumps({'name':name,**changes})}, files={
        'images':('fixture.png',stream.getvalue(),'image/png')})
    response.raise_for_status()
    return response.json()


def login(client, name="知识管理员"):
    client.post('/api/logout').raise_for_status()
    client.post('/api/login',json={'name':name,'password':'knowledge-test-only'}).raise_for_status()


def test_notes_persist_and_chinese_natural_query_matches_relevant_snippet(client):
    first = note(client, title="圣诞树蜡烛包装", content="圣诞树造型蜡烛使用独立内托，装箱前测量最高处与底座直径。", category="包装规范")
    note(client,title="水晶珠退货",content="水晶手串需核验珠子尺寸和数量。")
    result = knowledge.retrieve("帮我找一下公司已有的圣诞树蜡烛的包装知识")
    assert result['items'][0]['id'] == first['id']
    assert result['items'][0]['document_id'] == first['id']
    assert result['items'][0]['kind'] == 'knowledge'
    assert '独立内托' in result['items'][0]['excerpt']
    knowledge._initialized = None
    knowledge.init_storage()
    assert client.get('/api/knowledge/documents/'+first['id']).json()['content'] == first['content']
    assert client.get('/api/knowledge/stats').json()['documents'] == 2


def test_chunked_retrieval_finds_tail_and_returns_bounded_source(client):
    content = "通用包装信息。" * 1000 + "柱蜡夏季运输需要隔热内衬。"
    item = note(client,content=content)
    result = knowledge.retrieve('夏季运输 隔热内衬')
    assert result['items'][0]['id'] == item['id']
    assert '隔热内衬' in result['items'][0]['excerpt']
    assert len(result['items'][0]['excerpt']) <= knowledge.CHUNK_SIZE
    assert len(result['items']) == 1
    assert client.get('/api/knowledge/stats').json()['chunks'] > 1


def test_empty_and_unmatched_queries_never_return_recent_random_documents_or_products(client):
    note(client)
    product(client)
    for query in ("帮我把它的样子发给我", "没有匹配的钻石切割设备", "", "' OR 1=1--"):
        assert knowledge.retrieve(query)['items'] == []
        assert knowledge.retrieve_products(query) == []


def test_product_catalog_has_real_images_and_prioritizes_tree_candle_over_pillar(client):
    tree = product(client, name="红白渐变圣诞树造型蜡烛", category="模具蜡", specs="高度20cm")
    pillar = product(client, name="红色蜂窝纹柱蜡烛", category="柱蜡", specs="高度15cm")
    result = knowledge.retrieve_products('能帮我找一个公司有的像圣诞树的蜡烛吗，把它样子发给我')
    assert result[0]['id'] == tree['id']
    assert result[0]['code'] == tree['code']
    assert result[0]['images'][0]['thumb_url'] == tree['images'][0]['thumb_url']
    assert result[0]['images'][0]['url'].startswith('/media/')
    assert client.get(result[0]['images'][0]['thumb_url']).status_code == 200
    assert {row['id'] for row in knowledge.retrieve_products('柱蜡')} == {pillar['id']}


def test_product_index_tracks_name_category_and_sku_changes_incrementally(client, monkeypatch):
    item = product(client,name="普通样品",category="水晶",listings=[{'platform':'Temu','store':'美国店','product_code':'UNIQUE-RED-0008','product_number':'SKC-010'}])
    assert knowledge.retrieve_products('UNIQUE-RED-0008')[0]['id'] == item['id']
    calls = []
    original = knowledge._tokens
    monkeypatch.setattr(knowledge,'_tokens',lambda text:(calls.append(text),original(text))[1])
    assert knowledge.retrieve_products('水晶')[0]['id'] == item['id']
    assert calls == []
    client.patch('/api/products/'+item['id'],json={'name':'新名字珍珠挂饰','category':'珍珠'}).raise_for_status()
    assert knowledge.retrieve_products('珍珠')[0]['id'] == item['id']
    assert len(calls) == 1
    assert knowledge.retrieve_products('水晶') == []
    with backend._connection(write=True) as conn:
        conn.execute('UPDATE product_listings SET product_code=? WHERE product_id=?',('UNIQUE-BLUE-0099',item['id']))
    assert knowledge.retrieve_products('UNIQUE-BLUE-0099')[0]['id'] == item['id']
    assert len(calls) == 2


def test_deleted_products_and_stale_client_references_are_not_returned(client):
    item = product(client)
    assert knowledge.retrieve_products('圣诞树')[0]['id'] == item['id']
    assert knowledge.get_products_by_ids([item['id']])[0]['images']
    client.delete('/api/products/'+item['id']).raise_for_status()
    assert knowledge.retrieve_products('圣诞树') == []
    assert knowledge.get_products_by_ids([item['id']]) == []


def test_first_search_indexes_beyond_the_first_batch_without_touching_products(client):
    with backend._connection(write=True) as conn:
        user = conn.execute('SELECT * FROM users LIMIT 1').fetchone()
        rows = [(f'id-{i:04}',f'MD-{i:06}',f'样品{i}',user['name'],user['id'],backend.now(),backend.now()) for i in range(302)]
        conn.executemany('INSERT INTO products(id,code,name,owner,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',rows)
        conn.execute('UPDATE products SET name=? WHERE id=?',('罕见树脂珊瑚底座','id-0301'))
        before = [tuple(row) for row in conn.execute('SELECT * FROM products ORDER BY id')]
    assert knowledge.retrieve_products('珊瑚底座')[0]['id'] == 'id-0301'
    with backend._connection() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM products ORDER BY id')] == before
        assert conn.execute('SELECT COUNT(*) FROM knowledge_product_dirty').fetchone()[0] == 0


def test_document_edit_reindexes_and_deleted_notes_restore_without_losing_text(client):
    item = note(client,content="上架核对需要复核产品尺寸。")
    client.patch('/api/knowledge/documents/'+item['id'],json={'title':'退货流程','category':'售后','content':'退货先核验物流单号。'}).raise_for_status()
    assert knowledge.retrieve('产品尺寸')['items'] == []
    assert knowledge.retrieve('物流单号')['items'][0]['id'] == item['id']
    client.delete('/api/knowledge/documents/'+item['id']).raise_for_status()
    assert knowledge.retrieve('物流单号')['items'] == []
    assert knowledge.get_documents_by_ids([item['id']]) == []
    assert client.get('/api/knowledge/documents',params={'deleted':True}).json()['total'] == 1
    restored = client.post('/api/knowledge/documents/'+item['id']+'/restore')
    restored.raise_for_status()
    assert restored.json()['content'] == '退货先核验物流单号。'
    assert knowledge.get_documents_by_ids([item['id']])[0]['excerpt'] == '退货先核验物流单号。'


def test_operators_can_read_but_cannot_write_company_knowledge(client):
    admin_note = note(client)
    client.post('/api/team',json={'name':'运营甲','password':'knowledge-test-only','role':'operator'}).raise_for_status()
    client.post('/api/team',json={'name':'运营乙','password':'knowledge-test-only','role':'operator'}).raise_for_status()
    login(client,'运营甲')
    assert client.get('/api/knowledge/documents/'+admin_note['id']).status_code == 200
    assert client.get('/api/knowledge/documents').json()['items'][0]['can_edit'] is False
    for method, path, kwargs in [('patch','',{'json':{'title':'不允许'}}),('delete','',{}),('post','/restore',{})]:
        assert getattr(client,method)('/api/knowledge/documents/'+admin_note['id']+path,**kwargs).status_code == 403
    assert client.post('/api/knowledge/documents',json={'title':'甲的文档','content':'普通运营不可新增企业知识。'}).status_code == 403
    login(client,'运营乙')
    assert client.patch('/api/knowledge/documents/'+admin_note['id'],json={'content':'越权'}).status_code == 403
    login(client)
    assert client.patch('/api/knowledge/documents/'+admin_note['id'],json={'content':'老板复核'}).status_code == 200
    client.post('/api/logout')
    assert client.get('/api/knowledge/stats').status_code == 401
    assert client.post('/api/knowledge/documents',json={'title':'x','content':'x'}).status_code == 401


@pytest.mark.parametrize('bad',[
    {}, [], {'title':'','content':'正文'}, {'title':'a'*121,'content':'正文'},
    {'title':'正常','content':''}, {'title':'正常','content':True}, {'title':'正常','content':'x'*100001},
    {'title':'正常','content':'正文','created_by':'override'}, {'title':'a\n','content':'正文'},
    {'title':'正常','content':'正文','category':'x'*61}, {'title':'正常','content':'a\x00b'},
])
def test_invalid_notes_never_partially_write(client,bad):
    assert client.post('/api/knowledge/documents',json=bad).status_code == 400
    assert client.get('/api/knowledge/stats').json() == {'documents':0,'chunks':0,'products':0,'total_sources':0,'can_create':True,'can_archive':True}


def test_import_is_bounded_parse_outside_transactions_and_keeps_only_text(client,monkeypatch):
    active = {'writes':0}
    original_connection = backend._connection
    @contextmanager
    def tracked_connection(write=False):
        with original_connection(write=write) as conn:
            active['writes'] += int(write)
            try:
                yield conn
            finally:
                active['writes'] -= int(write)
    original_parse = knowledge.chat_attachments.parse_document
    def parsed(*args):
        assert active['writes'] == 0
        return original_parse(*args)
    monkeypatch.setattr(backend,'_connection',tracked_connection)
    monkeypatch.setattr(knowledge.chat_attachments,'parse_document',parsed)
    response = client.post('/api/knowledge/documents/import',data={'category':'物流'},files=[
        ('files',('../退款流程.md','核对物流单号后安排退款。'.encode(),'text/markdown')),
        ('files',('资料.txt',('运输'*10000).encode(),'text/plain')),
    ])
    response.raise_for_status()
    result = response.json()
    assert result['original_files_stored'] is False and result['text_limit'] == 16000
    assert result['items'][0]['source_name'] == '退款流程.md'
    assert result['items'][1]['characters'] == 16000 and result['items'][1]['truncated'] is True
    assert knowledge.retrieve('物流单号')['items'][0]['source_name'] == '退款流程.md'
    assert not list(backend.DATA_DIR.glob('*.md')) and not list(backend.DATA_DIR.glob('*.txt'))


def test_bad_file_rolls_back_whole_import_and_never_stores_executable(client):
    response = client.post('/api/knowledge/documents/import',files=[
        ('files',('正常.txt','正常业务知识'.encode(),'text/plain')),
        ('files',('恶意.exe',b'MZ','application/octet-stream'))])
    assert response.status_code == 400
    assert client.get('/api/knowledge/stats').json()['documents'] == 0
    assert client.post('/api/knowledge/documents/import',files=[('files',('大.txt',b'a'*(10*1024*1024+1),'text/plain'))]).status_code == 413
    assert client.post('/api/knowledge/documents/import',files=[('files',(f'{i}.txt',b'x','text/plain')) for i in range(7)]).status_code == 400


def test_existing_safe_docx_pdf_xlsx_extractors_work_as_knowledge_sources(client):
    fixtures = runpy.run_path(str(Path(__file__).with_name('test_chat_attachments.py')))
    response = client.post('/api/knowledge/documents/import',files=[
        ('files',('规格.pdf',fixtures['pdf'](),'application/pdf')),
        ('files',('规格.docx',fixtures['docx'](),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
        ('files',('规格.xlsx',fixtures['xlsx'](),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
    ])
    response.raise_for_status()
    assert len(response.json()['items']) == 3
    assert all(item['characters']>0 for item in response.json()['items'])


def test_quote_references_validate_ids_and_reread_live_text(client):
    item = note(client,content='旧制度')
    client.patch('/api/knowledge/documents/'+item['id'],json={'content':'最新批准的制度'}).raise_for_status()
    assert knowledge.get_documents_by_ids([item['id']])[0]['excerpt'] == '最新批准的制度'
    assert knowledge.get_documents_by_ids(["' OR 1=1--"]) == []
    for ids in ([{}], ['x']*7, 'bad', ['x'*101]):
        with pytest.raises(Exception) as error:
            knowledge.get_documents_by_ids(ids)
        assert error.value.status_code == 400


def test_knowledge_write_and_index_are_rolled_back_together_if_audit_fails(client,monkeypatch):
    knowledge.init_storage()
    def fail(*args,**kwargs):
        raise RuntimeError('audit failure')
    monkeypatch.setattr(backend,'_audit',fail)
    with pytest.raises(RuntimeError,match='audit failure'):
        note(client)
    assert client.get('/api/knowledge/stats').json() == {'documents':0,'chunks':0,'products':0,'total_sources':0,'can_create':True,'can_archive':True}
    assert knowledge.retrieve('规格')['items'] == []


def test_note_search_pagination_filters_deleted_and_escapes_wildcards(client):
    first=note(client,title='100%确认',category='供应商')
    note(client,title='普通')
    result=client.get('/api/knowledge/documents',params={'query':'%'}).json()
    assert result['total']==1 and result['items'][0]['id']==first['id']
    assert client.get('/api/knowledge/documents',params={'limit':1,'offset':1}).json()['total']==2
    assert len(client.get('/api/knowledge/documents',params={'limit':1,'offset':1}).json()['items'])==1
    assert client.get('/api/knowledge/documents',params={'limit':0}).status_code==422


def test_note_json_body_is_stream_bounded_before_parsing(client):
    response=client.post('/api/knowledge/documents',content=b' '*(knowledge.MAX_JSON_BYTES+1),headers={'Content-Type':'application/json'})
    assert response.status_code==413
    assert client.post('/api/knowledge/documents',content=b'{broken',headers={'Content-Type':'application/json'}).status_code==400
    assert client.get('/api/knowledge/stats').json()['documents']==0


def test_price_tracking_specs_added_after_initialization_are_indexed(client):
    item=product(client,name='普通测试品')
    knowledge.init_storage()
    assert knowledge.retrieve_products('晚霞色')==[]
    import price_storage
    price_storage.init_storage()
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO product_listings(id,product_id,platform,product_code) VALUES(?,?,?,?)',('track-a',item['id'],'Temu','A-1000'))
        conn.execute('INSERT INTO price_tracking(listing_id,variant_hint,region,updated_at) VALUES(?,?,?,?)',('track-a','晚霞色四件装','北美',backend.now()))
    assert knowledge.retrieve_products('晚霞色')[0]['id']==item['id']
    with backend._connection(write=True) as conn:
        conn.execute('UPDATE price_tracking SET variant_hint=? WHERE listing_id=?',('海蓝色四件装','track-a'))
    assert knowledge.retrieve_products('海蓝色')[0]['id']==item['id']
    assert knowledge.retrieve_products('晚霞色')==[]


def test_restoring_old_database_over_same_path_recreates_missing_knowledge_schema(client,tmp_path):
    old = tmp_path/'pre-knowledge.db'
    with closing(sqlite3.connect(backend.DB_PATH)) as source, closing(sqlite3.connect(old)) as target:
        source.backup(target)
    knowledge.init_storage()
    previous_marker=knowledge._initialized
    # Copying over an existing file preserves its inode, just like an in-place
    # restore can. All connections are closed before replacing the QA database.
    shutil.copyfile(old,backend.DB_PATH)
    assert knowledge._initialized==previous_marker
    assert client.get('/api/knowledge/stats').json()=={'documents':0,'chunks':0,'products':0,'total_sources':0,'can_create':True,'can_archive':True}
    assert note(client)['content']


def test_many_top_chunks_from_one_long_document_do_not_hide_other_sources(client):
    docs=[note(client,title='物流单号核验'+str(i),content=('物流单号要复核。'*(12000 if i==0 else 160))) for i in range(6)]
    matches=knowledge.retrieve('物流单号')
    assert len(matches['items'])==6
    assert {item['id'] for item in matches['items']}=={item['id'] for item in docs}


def test_document_list_searches_unique_chinese_body_terms_and_deduplicates_pages(client):
    first=note(client,title='夏季须知',content='运输前核实隔热内衬。'*400,category='物流')
    second=note(client,title='存放须知',content='仓储也要检查隔热内衬。',category='仓储')
    note(client,title='日常安排',content='每周整理一次纸箱库存。')
    result=client.get('/api/knowledge/documents',params={'query':'隔热内衬','limit':1}).json()
    assert result['total']==2 and len(result['items'])==1
    next_page=client.get('/api/knowledge/documents',params={'query':'隔热内衬','limit':1,'offset':1}).json()
    assert next_page['total']==2 and len(next_page['items'])==1
    assert {result['items'][0]['id'],next_page['items'][0]['id']}=={first['id'],second['id']}
    assert result['items'][0]['can_edit'] is True
    assert client.get('/api/knowledge/documents',params={'query':'翡翠雕刻专机'}).json()['total']==0


def test_document_body_list_search_respects_soft_deleted_view(client):
    item=note(client,title='采购事项',content='供应商提供海蓝玛瑙配件。')
    client.delete('/api/knowledge/documents/'+item['id']).raise_for_status()
    active=client.get('/api/knowledge/documents',params={'query':'海蓝玛瑙'}).json()
    assert active['total']==0 and active['items']==[]
    deleted=client.get('/api/knowledge/documents',params={'query':'海蓝玛瑙','deleted':True}).json()
    assert deleted['total']==1 and deleted['items'][0]['id']==item['id']
    assert deleted['items'][0]['deleted'] is True
    client.post('/api/knowledge/documents/'+item['id']+'/restore').raise_for_status()
    assert client.get('/api/knowledge/documents',params={'query':'海蓝玛瑙'}).json()['total']==1
    assert client.get('/api/knowledge/documents',params={'query':'海蓝玛瑙','deleted':True}).json()['total']==0
