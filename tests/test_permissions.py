"""Authorization tests use disposable users, media and SQLite libraries only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend
import permissions


PASSWORD = "isolated-testing-password"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    backend.init_storage(tmp_path / 'library')
    backend._login_attempts.clear()
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)
    app = FastAPI()
    app.include_router(backend.router)
    with TestClient(app) as owner:
        response = owner.post('/api/setup', json={'name':'首位老板','password':PASSWORD})
        response.raise_for_status()
        boss = response.json()['user']
        members = {}
        for name, role in [('日常管理员','admin'),('甲员工','operator'),('乙员工','operator')]:
            response = owner.post('/api/team', json={'name':name,'role':role,'password':PASSWORD})
            response.raise_for_status()
            members[role if role=='admin' else name] = response.json()
        yield app, owner, boss, members


def login(app, name):
    client = TestClient(app)
    response = client.post('/api/login', json={'name':name,'password':PASSWORD})
    response.raise_for_status()
    return client


def member(owner, identity):
    return next(item for item in owner.get('/api/team').json()['items'] if item['id']==identity)


def update(owner, identity, **values):
    values.setdefault('expected_revision', member(owner, identity)['revision'])
    return owner.patch('/api/team/'+identity, json=values)


def create(client, name='权限测试商品'):
    image = io.BytesIO()
    Image.new('RGB',(64,64),'green').save(image,'PNG')
    response = client.post('/api/products',data={'metadata':json.dumps({'name':name})},
                           files={'images':('sample.png',image.getvalue(),'image/png')})
    response.raise_for_status()
    return response.json()


def test_first_user_is_only_owner_without_rewriting_original_columns(workspace):
    app, owner, boss, members = workspace
    assert boss['role']=='owner' and boss['is_owner']
    assert set(boss['permissions'])==permissions.KEYS
    with backend._connection() as conn:
        rows = conn.execute('SELECT id,name,password_hash,role,created_at FROM users ORDER BY created_at,id').fetchall()
        before = [tuple(row) for row in rows]
        assert rows[0]['role']=='admin'  # Physical legacy data stays unchanged.
    backend.init_storage(backend.DATA_DIR)
    with backend._connection() as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM users ORDER BY created_at,id')]==before
        assert permissions.owner_id(conn)==boss['id']
    assert [item['id'] for item in owner.get('/api/team').json()['items'] if item['role']=='owner']==[boss['id']]
    assert owner.post('/api/setup',json={'name':'第二老板','password':PASSWORD}).status_code==409
    assert owner.post('/api/team',json={'name':'第二老板','password':PASSWORD,'role':'owner'}).status_code==400


def test_legacy_first_user_is_migrated_once_not_every_admin(tmp_path):
    data=tmp_path/'legacy'; backend.init_storage(data)
    with backend._connection(write=True) as conn:
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',('first','最早账号','hash-only','operator','2020-01-01'))
        conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',('second','后来管理员','hash-only','admin','2021-01-01'))
    backend.init_storage(data)
    with backend._connection() as conn:
        assert permissions.owner_id(conn)=='first'
        assert permissions.public_user(conn,conn.execute("SELECT * FROM users WHERE id='first'").fetchone())['role']=='owner'
        assert permissions.public_user(conn,conn.execute("SELECT * FROM users WHERE id='second'").fetchone())['role']=='admin'
    with backend._connection(write=True) as conn:
        conn.execute("INSERT INTO users VALUES('even-earlier','外部旧记录','hash-only','admin','1900-01-01')")
    backend.init_storage(data)
    assert permissions.owner_id()=='first'


def test_parallel_initial_setup_is_atomic(tmp_path):
    backend.init_storage(tmp_path/'race')
    backend._login_attempts.clear()
    app=FastAPI(); app.include_router(backend.router)
    def signup(number):
        with TestClient(app) as client:
            return client.post('/api/setup',json={'name':f'并发首位{number}','password':PASSWORD}).status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(signup,[1,2]))==[200,409]
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1
        assert conn.execute('SELECT id FROM users').fetchone()[0]==permissions.owner_id(conn)


def test_role_defaults_and_public_overrides_visibility(workspace):
    app, owner, boss, members=workspace
    admin=login(app,'日常管理员'); employee=login(app,'甲员工')
    manager=admin.get('/api/me').json()['user']
    assert {'team.create','knowledge.edit_all','products.merge','announcements.publish'}<=set(manager['permissions'])
    assert not {'team.manage','products.purge','knowledge.archive','data.backup','settings.ai'}&set(manager['permissions'])
    worker=employee.get('/api/me').json()['user']
    assert {'products.create','products.import','products.edit_own','products.delete_own'}<=set(worker['permissions'])
    assert not any(key.startswith('knowledge.') for key in worker['permissions'])
    assert all('permission_overrides' not in item for item in admin.get('/api/team').json()['items'])
    assert all('permission_overrides' in item for item in owner.get('/api/team').json()['items'])
    assert len(owner.get('/api/permissions').json()['items'])==len(permissions.KEYS)


def test_admin_may_add_employee_but_not_manager_or_grants(workspace):
    app, owner, boss, members=workspace
    admin=login(app,'日常管理员')
    assert admin.post('/api/team',json={'name':'新员工','password':PASSWORD}).status_code==200
    assert admin.post('/api/team',json={'name':'新管理员','password':PASSWORD,'role':'admin'}).status_code==403
    assert admin.post('/api/team',json={'name':'暗加权限','password':PASSWORD,'permissions':{'team.manage':True}}).status_code==400
    identity=members['甲员工']['id']
    assert admin.patch('/api/team/'+identity,json={'expected_revision':0,'disabled':True}).status_code==403
    assert admin.patch('/api/team/'+identity,json={'expected_revision':0,'role':'admin'}).status_code==403
    assert admin.delete('/api/team/'+identity).status_code in {404,405}


@pytest.mark.parametrize('payload',[{'disabled':True},{'disabled':False},{'role':'operator'},{'permissions':{}}, {'permissions':{'products.create':False}}])
def test_owner_account_cannot_be_modified_even_by_owner(workspace,payload):
    app,owner,boss,members=workspace
    assert update(owner,boss['id'],**payload).status_code==403
    assert owner.get('/api/me').json()['user']['role']=='owner'


def test_disable_revokes_live_sessions_but_keeps_business_and_account(workspace):
    app,owner,boss,members=workspace
    employee=login(app,'甲员工'); item=create(employee)
    identity=members['甲员工']['id']
    result=update(owner,identity,disabled=True)
    result.raise_for_status()
    assert result.json()['disabled'] and result.json()['revision']==1
    assert employee.get('/api/me').status_code==401
    assert employee.get('/api/products').status_code==401
    assert employee.post('/api/login',json={'name':'甲员工','password':PASSWORD}).status_code==401
    assert owner.get('/api/products/'+item['id']).status_code==200
    assert update(owner,identity,disabled=False).status_code==200
    assert employee.post('/api/login',json={'name':'甲员工','password':PASSWORD}).status_code==200
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users WHERE id=?',(identity,)).fetchone()[0]==1


def test_overrides_apply_to_current_session_and_reset_to_defaults(workspace):
    app,owner,boss,members=workspace
    employee=login(app,'甲员工'); identity=members['甲员工']['id']
    mine=create(employee); other=create(owner,'老板商品')
    assert employee.patch('/api/products/'+other['id'],json={'name':'越权修改'}).status_code==403
    assert update(owner,identity,permissions={'products.edit_all':True,'products.delete_own':False}).status_code==200
    assert employee.patch('/api/products/'+other['id'],json={'name':'获授权修改'}).status_code==200
    assert employee.delete('/api/products/'+mine['id']).status_code==403
    assert update(owner,identity,permissions={}).status_code==200
    assert employee.patch('/api/products/'+other['id'],json={'name':'收回后修改'}).status_code==403
    assert employee.delete('/api/products/'+mine['id']).status_code==200


def test_admin_can_be_restricted_to_own_products(workspace):
    app,owner,boss,members=workspace
    admin=login(app,'日常管理员'); item=create(owner)
    identity=members['admin']['id']
    update(owner,identity,permissions={'products.edit_all':False,'products.delete_all':False,'team.create':False}).raise_for_status()
    assert admin.patch('/api/products/'+item['id'],json={'name':'不能编辑'}).status_code==403
    assert admin.delete('/api/products/'+item['id']).status_code==403
    assert admin.post('/api/team',json={'name':'不能再添加','password':PASSWORD}).status_code==403
    own=create(admin,'经理本人产品')
    assert admin.patch('/api/products/'+own['id'],json={'name':'本人仍可编辑'}).status_code==200


@pytest.mark.parametrize('bad',[None,[],{'unknown.key':True},{'ai.use':'false'},{'ai.use':1},{'team.manage':True},{'products.purge':False},{'knowledge.purge':True}])
def test_bad_permission_payload_is_atomic(workspace,bad):
    app,owner,boss,members=workspace
    identity=members['甲员工']['id']; before=member(owner,identity)
    response=update(owner,identity,permissions=bad)
    assert response.status_code==400
    assert member(owner,identity)==before


@pytest.mark.parametrize('key',sorted(permissions.KNOWLEDGE_KEYS-{'knowledge.purge'}))
def test_employee_cannot_receive_knowledge_write_even_from_owner(workspace,key):
    app,owner,boss,members=workspace
    response=update(owner,members['甲员工']['id'],permissions={key:True})
    assert response.status_code==400
    assert '企业知识' in response.json()['detail']


def test_role_downgrade_resets_incompatible_overrides(workspace):
    app,owner,boss,members=workspace; identity=members['admin']['id']
    update(owner,identity,permissions={'knowledge.archive':True,'data.backup':True}).raise_for_status()
    response=update(owner,identity,role='operator')
    response.raise_for_status()
    assert response.json()['permission_overrides']=={}
    assert not any(key.startswith('knowledge.') for key in response.json()['permissions'])


def test_compare_and_swap_and_bad_inputs_do_not_change_account(workspace):
    app,owner,boss,members=workspace; identity=members['甲员工']['id']
    update(owner,identity,permissions={'ai.web':False}).raise_for_status()
    assert owner.patch('/api/team/'+identity,json={'expected_revision':0,'disabled':True}).status_code==409
    for body in ({'disabled':True},{'expected_revision':True,'disabled':True},{'expected_revision':1,'disabled':'yes'},
                 {'expected_revision':1,'role':'owner'},{'expected_revision':1,'name':'恶意重命名'}, {'expected_revision':1}):
        assert owner.patch('/api/team/'+identity,json=body).status_code==400
    assert not member(owner,identity)['disabled']


def test_permission_and_session_changes_rollback_when_audit_fails(workspace,monkeypatch):
    app,owner,boss,members=workspace; identity=members['甲员工']['id']
    employee=login(app,'甲员工'); before=member(owner,identity)
    def fail(*args,**kwargs): raise RuntimeError('audit fixture failure')
    monkeypatch.setattr(backend,'_audit',fail)
    with pytest.raises(RuntimeError,match='audit fixture'):
        update(owner,identity,disabled=True,role='admin',permissions={'data.backup':True})
    assert member(owner,identity)==before
    assert employee.get('/api/me').status_code==200


def test_backup_and_activity_scope_require_explicit_permissions(workspace):
    app,owner,boss,members=workspace
    admin=login(app,'日常管理员'); employee=login(app,'甲员工')
    secret=create(owner,'只有老板的操作日志标题')
    mine=create(employee,'员工操作')
    assert admin.post('/api/backup').status_code==403
    assert employee.post('/api/backup').status_code==403
    history=employee.get('/api/activity').json()
    assert history['items'] and all(item['user_id']==members['甲员工']['id'] for item in history['items'])
    assert history['actors']==[{'id':members['甲员工']['id'],'name':'甲员工'}]
    assert employee.get('/api/activity',params={'actor_id':boss['id']}).json()['total']==0
    update(owner,members['admin']['id'],permissions={'data.backup':True}).raise_for_status()
    assert admin.post('/api/backup').status_code==200


def test_revoked_create_and_import_denied_before_writes(workspace):
    app,owner,boss,members=workspace
    employee=login(app,'甲员工'); identity=members['甲员工']['id']
    update(owner,identity,permissions={'products.create':False,'products.import':False}).raise_for_status()
    image=io.BytesIO();Image.new('RGB',(64,64),'green').save(image,'PNG')
    assert employee.post('/api/products',data={'metadata':'{}'},files={'images':('x.png',image.getvalue(),'image/png')}).status_code==403
    assert employee.post('/api/import',data={'manifest':'{}'},files={'images':('x.png',image.getvalue(),'image/png')}).status_code==403
    assert owner.get('/api/stats').json()['total_products']==0


def test_product_capabilities_match_api_and_decision_needs_edit(workspace):
    app,owner,boss,members=workspace
    employee=login(app,'甲员工'); other=login(app,'乙员工')
    item=create(employee)
    visible=other.get('/api/products/'+item['id']).json()
    assert not visible['can_edit'] and not visible['can_delete']
    assert other.post('/api/decisions',json={'product_id':item['id'],'decision':'same'}).status_code==403
    assert employee.get('/api/products/'+item['id']).json()['can_edit']
    assert owner.get('/api/products/'+item['id']).json()['can_edit']


def test_permission_catalog_owner_only_cannot_be_forged(workspace):
    app,owner,boss,members=workspace
    impostor={'id':members['甲员工']['id'],'name':'伪造','role':'owner','is_owner':True,'permissions':list(permissions.KEYS)}
    assert not permissions.is_owner(impostor)
    assert not any(permissions.has(impostor,key) for key in permissions.KEYS)


def test_import_undo_uses_delete_scope_and_reports_matching_capability(workspace):
    app,owner,boss,members=workspace
    employee=login(app,'甲员工'); admin=login(app,'日常管理员')
    image=io.BytesIO();Image.new('RGB',(64,64),'green').save(image,'PNG')
    response=employee.post('/api/import', data={'manifest':json.dumps({'groups':[{'name':'员工导入','paths':['fixture.png']}]})},
                           files={'images':('fixture.png',image.getvalue(),'image/png')})
    response.raise_for_status();batch=response.json()['batch_id']
    assert next(item for item in employee.get('/api/activity').json()['items'] if item['batch_id']==batch)['can_undo']
    update(owner,members['甲员工']['id'],permissions={'products.delete_own':False}).raise_for_status()
    assert not next(item for item in employee.get('/api/activity').json()['items'] if item['batch_id']==batch)['can_undo']
    assert employee.post('/api/import/'+batch+'/undo').status_code==403
    update(owner,members['admin']['id'],permissions={'products.delete_all':False}).raise_for_status()
    assert admin.post('/api/import/'+batch+'/undo').status_code==403
    assert owner.get('/api/stats').json()['total_products']==1
    assert owner.post('/api/import/'+batch+'/undo').status_code==200
    assert owner.get('/api/stats').json()['total_products']==0


def test_missing_owner_record_does_not_elevate_another_account(tmp_path):
    data=tmp_path/'owner-corruption';backend.init_storage(data)
    with backend._connection(write=True) as conn:
        conn.execute("INSERT INTO users VALUES('original','原老板','fake-hash','admin','2020-01-01')")
    backend.init_storage(data)
    with backend._connection(write=True) as conn:
        conn.execute("DELETE FROM users WHERE id='original'")
    backend.init_storage(data)
    assert permissions.owner_id()=='original'
    app=FastAPI();app.include_router(backend.router)
    with TestClient(app) as client:
        assert client.post('/api/setup',json={'name':'不能接管','password':PASSWORD}).status_code==409
    with backend._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0


def test_concurrent_member_updates_only_one_current_revision_can_commit(workspace):
    app,owner,boss,members=workspace
    identity=members['甲员工']['id']
    cookie=owner.cookies.get(backend.COOKIE_NAME)
    def change(disabled):
        with TestClient(app) as client:
            client.cookies.set(backend.COOKIE_NAME,cookie)
            return client.patch('/api/team/'+identity,json={'expected_revision':0,'disabled':disabled}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change,[True,False]))==[200,409]
    assert member(owner,identity)['revision']==1
    with backend._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM activity WHERE action='team_permissions'").fetchone()[0]==1
