"""Announcement lifecycle/security tests use disposable libraries and fake files."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import io
import json
import sqlite3
import zipfile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

import announcements as notices
import backend


PASSWORD = 'notice-test-only'


@pytest.fixture
def client(tmp_path):
    backend.init_storage(tmp_path / 'library')
    backend._login_attempts.clear()
    notices.init_storage()
    app = FastAPI()
    app.include_router(notices.router)
    app.include_router(backend.router)
    with TestClient(app) as instance:
        instance.post('/api/setup',json={'name':'公告老板','password':PASSWORD}).raise_for_status()
        for name,role in [('公告管理员','admin'),('普通员工','operator')]:
            instance.post('/api/team',json={'name':name,'password':PASSWORD,'role':role}).raise_for_status()
        yield instance


def login(client, name='公告老板'):
    client.post('/api/logout').raise_for_status()
    client.post('/api/login',json={'name':name,'password':PASSWORD}).raise_for_status()


def create(client, **fields):
    response = client.post('/api/announcements',json={'title':'本周上新安排','content_html':'<p><strong>周五</strong>核对商品规格。</p>',**fields})
    response.raise_for_status()
    return response.json()


def change(client,item,action):
    return client.post('/api/announcements/'+item['id']+'/'+action,json={'expected_revision':item['revision']})


def publish(client,item):
    response=change(client,item,'publish');response.raise_for_status();return response.json()


def image():
    stream=io.BytesIO();Image.new('RGB',(64,42),'#eaa141').save(stream,'PNG')
    return ('公告图.png',stream.getvalue(),'image/png')


def upload(client,item,*files):
    return client.post('/api/announcements/'+item['id']+'/attachments',data={'expected_revision':str(item['revision'])},
                       files=[('files',part) for part in files])


def edit(client,item,**fields):
    return client.patch('/api/announcements/'+item['id'],json={'expected_revision':item['revision'],**fields})


def test_draft_is_private_and_employee_can_only_read_published(client):
    item=create(client)
    assert item['status']=='draft' and item['revision']==1 and item['can_edit']
    login(client,'普通员工')
    assert client.get('/api/announcements').json()['items']==[]
    assert client.get('/api/announcements/'+item['id']).status_code==404
    assert client.get('/api/announcements',params={'status':'draft'}).status_code==403
    assert client.post('/api/announcements',json={'title':'越权'}).status_code==403
    login(client)
    item=publish(client,item)
    login(client,'普通员工')
    public=client.get('/api/announcements/'+item['id']).json()
    assert public['title']=='本周上新安排' and not public['can_edit']
    assert client.get('/api/announcements/summary').json()['latest']['id']==item['id']


def test_admin_can_edit_and_publish_but_cannot_archive(client):
    login(client,'公告管理员')
    item=create(client)
    item=publish(client,item)
    assert item['can_publish'] and not item['can_archive']
    assert change(client,item,'archive').status_code==403
    response=edit(client,item,title='管理员工作稿')
    response.raise_for_status()
    assert response.json()['has_unpublished_changes']
    with backend._connection() as conn:
        descriptions = {row['action']:row['description'] for row in conn.execute(
            "SELECT action,description FROM activity WHERE action LIKE 'announcement_%'")}
    assert descriptions['announcement_create']=='创建公告：本周上新安排'
    assert descriptions['announcement_publish']=='发布公告：本周上新安排'
    assert descriptions['announcement_edit']=='编辑公告：管理员工作稿'


@pytest.mark.parametrize('action',['publish','withdraw','archive','restore'])
def test_employee_direct_state_api_cannot_bypass_buttons(client,action):
    item=publish(client,create(client))
    login(client,'普通员工')
    assert change(client,item,action).status_code==403
    assert edit(client,item,title='越权修改').status_code==403
    assert upload(client,item,image()).status_code==403


def test_published_edits_stay_draft_until_explicit_publish(client):
    item=publish(client,create(client))
    old_revision=item['revision']
    response=edit(client,item,title='尚未发布的新标题',content_html='<h2>新内容</h2>',pinned=True)
    response.raise_for_status();draft=response.json()
    assert draft['has_unpublished_changes'] and draft['published_revision']==old_revision
    public=client.get('/api/announcements/'+item['id'],params={'published':'true'}).json()
    assert public['title']=='本周上新安排' and public['revision']==old_revision and not public['pinned']
    assert client.get('/api/announcements',params={'status':'draft'}).json()['items'][0]['id']==item['id']
    login(client,'普通员工')
    assert client.get('/api/announcements/'+item['id']).json()['title']=='本周上新安排'
    login(client);item=publish(client,draft)
    login(client,'普通员工')
    public=client.get('/api/announcements/'+item['id']).json()
    assert public['title']=='尚未发布的新标题' and public['pinned'] and public['revision']==item['revision']


def test_read_receipt_is_per_user_and_republish_resets_unread(client):
    item=publish(client,create(client))
    login(client,'普通员工')
    assert client.get('/api/announcements/summary').json()['unread_count']==1
    route='/api/announcements/'+item['id']+'/read'
    assert client.post(route,json={'revision':item['revision']}).json()['unread_count']==0
    assert client.get('/api/announcements/'+item['id']).json()['is_read']
    assert client.post(route,json={'revision':1}).status_code==409
    assert client.get('/api/announcements/'+item['id']+'/readers').status_code==403
    login(client)
    readers=client.get('/api/announcements/'+item['id']+'/readers').json()
    assert readers['read_count']==1 and readers['items'][0]['name']=='普通员工'
    assert client.get('/api/announcements/summary').json()['unread_count']==1
    item=publish(client,item)
    login(client,'普通员工')
    assert client.get('/api/announcements/summary').json()['unread_count']==1


def test_archive_withdraw_restore_and_expiry(client,monkeypatch):
    future=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(timespec='microseconds')
    item=publish(client,create(client,expires_at=future))
    response=change(client,item,'withdraw');response.raise_for_status();item=response.json()
    assert item['status']=='draft'
    login(client,'普通员工');assert client.get('/api/announcements/'+item['id']).status_code==404
    login(client);item=publish(client,item)
    response=change(client,item,'archive');response.raise_for_status();item=response.json()
    assert item['status']=='archived'
    assert edit(client,item,title='不允许').status_code==409
    response=change(client,item,'restore');response.raise_for_status();item=response.json()
    assert item['status']=='draft'
    assert client.get('/api/announcements').json()['items']==[]
    item=publish(client,item)
    monkeypatch.setattr(backend,'now',lambda:(datetime.fromisoformat(future)+timedelta(seconds=1)).isoformat(timespec='microseconds'))
    assert client.get('/api/announcements/summary').json()['items']==[]
    assert client.get('/api/announcements',params={'status':'expired'}).json()['items'][0]['id']==item['id']
    login(client,'普通员工')
    assert client.get('/api/announcements/'+item['id']).status_code==404
    assert client.post('/api/announcements/'+item['id']+'/read',json={'revision':item['revision']}).status_code==404


def test_stale_editor_and_upload_do_not_overwrite(client):
    item=create(client)
    response=edit(client,item,title='编辑后的标题');response.raise_for_status()
    assert edit(client,item,title='旧草稿').status_code==409
    assert upload(client,item,image()).status_code==409
    assert change(client,item,'publish').status_code==409
    assert client.get('/api/announcements/'+item['id']).json()['title']=='编辑后的标题'


@pytest.mark.parametrize('field,value',[
    ('title',True),('title','x'*161),('title','abc\n公告'),('content_html',[]),('content_html','x'*100001),
    ('pinned','true'),('expires_at','2026-11-13'),('expires_at',True),('status','published'),('created_by','forged')])
def test_invalid_document_fields_are_rejected(client,field,value):
    response=client.post('/api/announcements',json={'title':'测试公告','content_html':'正文',field:value})
    assert response.status_code==400
    assert client.get('/api/announcements',params={'status':'draft'}).json()['total']==0


@pytest.mark.parametrize('payload',[{}, {'expected_revision':True},{'expected_revision':0},{'expected_revision':'1'}, {'expected_revision':1,'status':'published'}])
def test_strict_versions_and_state_bodies(client,payload):
    item=create(client)
    assert client.post('/api/announcements/'+item['id']+'/publish',json=payload).status_code==400


def test_publish_requires_title_and_content_or_attachment(client):
    item=create(client,title='',content_html='')
    assert change(client,item,'publish').status_code==400
    response=edit(client,item,title='仅附件公告');response.raise_for_status();item=response.json()
    assert change(client,item,'publish').status_code==400
    response=upload(client,item,('通知.pdf',b'%PDF-test-only','application/pdf'));response.raise_for_status()
    assert publish(client,response.json())['status']=='published'


def test_image_reencoding_attachment_download_and_draft_access(client):
    item=create(client)
    response=upload(client,item,image(),('通知.txt','仅测试附件'.encode(),'text/plain'));response.raise_for_status();item=response.json()
    photo,file=item['attachments']
    assert photo['mime']=='image/jpeg' and photo['name'].endswith('.jpg')
    picture=client.get(photo['url']);assert picture.content.startswith(b'\xff\xd8')
    with Image.open(io.BytesIO(picture.content)) as bitmap:
        assert bitmap.size==(64,42) and not bitmap.getexif()
    document=client.get(file['url']);assert 'attachment;' in document.headers['content-disposition']
    assert document.headers['x-content-type-options']=='nosniff'
    login(client,'普通员工');assert client.get(photo['url']).status_code==404
    login(client);item=publish(client,item)
    login(client,'普通员工');assert client.get(photo['url']).status_code==200


def test_rich_image_must_be_this_notices_attached_image(client):
    first,second=create(client),create(client,title='另一个公告')
    response=upload(client,first,image());response.raise_for_status();first=response.json()
    asset=first['attachments'][0]
    assert edit(client,second,content_html='<img src="'+asset['url']+'">').status_code==400
    assert edit(client,first,content_html='<p>实拍</p><img src="'+asset['url']+'" onerror="alert(1)">').status_code==200
    assert 'onerror' not in client.get('/api/announcements/'+first['id']).json()['content_html']
    forged='/api/announcements/'+second['id']+'/attachments/'+asset['id']
    assert client.get(forged).status_code==404


def test_working_asset_removal_preserves_publication_until_republish(client):
    item=create(client)
    response=upload(client,item,image());response.raise_for_status();item=response.json();asset=item['attachments'][0]
    response=edit(client,item,content_html='<img src="'+asset['url']+'">');response.raise_for_status();item=publish(client,response.json())
    response=client.request('DELETE',asset['url'],json={'expected_revision':item['revision']})
    response.raise_for_status();draft=response.json()
    assert draft['attachments']==[] and '<img' not in draft['content_html']
    login(client,'普通员工');assert client.get(asset['url']).status_code==200
    login(client);draft=edit(client,draft,content_html='<p>图片已移除的新公告</p>').json();publish(client,draft)
    login(client,'普通员工');assert client.get(asset['url']).status_code==404


@pytest.mark.parametrize('filename,content',[
    ('恶意.html',b'<script>1</script>'),('恶意.svg',b'<svg/>'),('工具.exe',b'MZ'),('脚本.js',b'alert(1)'),
    ('伪造.png',b'<svg onload="alert(1)"/>'),('空白.pdf',b'')])
def test_unsupported_or_malformed_uploads_are_rejected(client,filename,content):
    item=create(client)
    assert upload(client,item,(filename,content,'application/octet-stream')).status_code in {400,413}
    assert client.get('/api/announcements/'+item['id']).json()['attachments']==[]


def test_upload_limits_are_atomic(client,monkeypatch):
    item=create(client)
    assert upload(client,item,*[('x.txt',b'x','text/plain') for _ in range(7)]).status_code==400
    monkeypatch.setattr(notices,'MAX_STORAGE',3)
    assert upload(client,item,('资源.zip',b'PK00','application/zip')).status_code==413
    assert client.get('/api/announcements/'+item['id']).json()['revision']==item['revision']
    assert upload(client,item,('big.pdf',b'x'*(notices.MAX_FILE+1),'application/pdf')).status_code==413


def test_update_history_and_audit_rollback_together(client,monkeypatch):
    item=create(client)
    def fail(*args,**kwargs):
        raise RuntimeError('fixture audit failure')
    monkeypatch.setattr(backend,'_audit',fail)
    with pytest.raises(RuntimeError,match='audit failure'):
        edit(client,item,title='不应写入')
    current=client.get('/api/announcements/'+item['id']).json()
    assert current['title']==item['title'] and current['revision']==item['revision']
    assert len(client.get('/api/announcements/'+item['id']+'/history').json()['items'])==1
    with pytest.raises(RuntimeError):
        upload(client,item,image())
    assert client.get('/api/announcements/'+item['id']).json()['attachments']==[]


def test_tables_are_persistent_and_existing_backup_contains_attachment(client):
    item=create(client)
    response=upload(client,item,('资源.zip',b'PK-fixture-only','application/zip'));response.raise_for_status();item=publish(client,response.json())
    notices._initialized=None;notices.init_storage()
    assert client.get('/api/announcements/'+item['id']).json()['attachments']
    response=client.post('/api/backup');response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        db=backend.DATA_DIR/'fixture-backup-verification.db';db.write_bytes(archive.read('library.db'))
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT data FROM announcement_assets').fetchone()[0]==b'PK-fixture-only'


def test_pin_working_draft_does_not_reorder_public_cards(client):
    a=publish(client,create(client,title='先发布'))
    b=publish(client,create(client,title='后发布'))
    edit(client,a,pinned=True).raise_for_status()
    assert client.get('/api/announcements').json()['items'][0]['id']==b['id']
    assert client.get('/api/announcements/summary').json()['items'][0]['id']==b['id']


def test_owner_can_revoke_admin_announcement_rights_immediately(client):
    item=create(client)
    member=next(row for row in client.get('/api/team').json()['items'] if row['name']=='公告管理员')
    with TestClient(client.app) as admin:
        admin.post('/api/login',json={'name':'公告管理员','password':PASSWORD}).raise_for_status()
        assert admin.get('/api/announcements/'+item['id']).status_code==200
        response=client.patch('/api/team/'+member['id'],json={'expected_revision':member['revision'],
            'permissions':{'announcements.edit':False,'announcements.publish':False,'announcements.archive':False}})
        response.raise_for_status()
        assert edit(admin,item,title='撤权后不可写').status_code==403
        assert admin.get('/api/announcements/'+item['id']).status_code==404
        assert admin.get('/api/announcements/'+item['id']+'/history').status_code==403


def test_disabled_account_cannot_read_previously_visible_files(client):
    item=create(client)
    response=upload(client,item,('通知.txt',b'test-only','text/plain'));response.raise_for_status();item=publish(client,response.json())
    member=next(row for row in client.get('/api/team').json()['items'] if row['name']=='普通员工')
    with TestClient(client.app) as employee:
        employee.post('/api/login',json={'name':'普通员工','password':PASSWORD}).raise_for_status()
        assert employee.get(item['attachments'][0]['url']).status_code==200
        client.patch('/api/team/'+member['id'],json={'expected_revision':member['revision'],'disabled':True}).raise_for_status()
        assert employee.get(item['attachments'][0]['url']).status_code==401


def test_custom_granted_employee_can_publish_but_stays_non_owner(client):
    member=next(row for row in client.get('/api/team').json()['items'] if row['name']=='普通员工')
    client.patch('/api/team/'+member['id'],json={'expected_revision':member['revision'],
        'permissions':{'announcements.edit':True,'announcements.publish':True}}).raise_for_status()
    login(client,'普通员工')
    item=publish(client,create(client))
    assert item['status']=='published' and not item['can_archive']
    assert client.get('/api/me').json()['user']['role']=='operator'


def test_archive_only_never_reveals_working_draft_or_draft_attachments(client):
    private=create(client,title='从未公开的秘密草稿')
    item=create(client,title='公开标题')
    response=upload(client,item,image());response.raise_for_status();item=publish(client,response.json())
    public_asset=item['attachments'][0]
    response=edit(client,item,title='不应泄漏的工作标题',content_html='<p>尚未发布的机密正文</p>');response.raise_for_status();draft=response.json()
    response=upload(client,draft,('新草稿.txt',b'private draft','text/plain'));response.raise_for_status();draft=response.json()
    draft_asset=draft['attachments'][-1]
    member=next(row for row in client.get('/api/team').json()['items'] if row['name']=='普通员工')
    client.patch('/api/team/'+member['id'],json={'expected_revision':member['revision'],
        'permissions':{'announcements.archive':True}}).raise_for_status()
    login(client,'普通员工')
    public=client.get('/api/announcements/'+item['id'],params={'published':'true'}).json()
    assert public['title']=='公开标题' and public['control_revision']==draft['revision']
    assert '尚未发布' not in json.dumps(public,ensure_ascii=False)
    assert client.get('/api/announcements/'+private['id']).status_code==404
    assert client.get('/api/announcements',params={'status':'draft'}).status_code==403
    assert client.get('/api/announcements/'+item['id']+'/history').status_code==403
    assert client.get('/api/announcements/'+item['id']+'/readers').status_code==403
    assert client.get(public_asset['url']).status_code==200 and client.get(draft_asset['url']).status_code==404
    archived=client.post('/api/announcements/'+item['id']+'/archive',json={'expected_revision':public['control_revision']})
    archived.raise_for_status();archived=archived.json()
    assert archived['title']=='公开标题' and '不应泄漏' not in json.dumps(archived,ensure_ascii=False)
    assert client.get('/api/announcements',params={'status':'archived'}).json()['items'][0]['title']=='公开标题'
    restored=change(client,archived,'restore');restored.raise_for_status()
    assert 'content_html' not in restored.json() and restored.json()['status']=='draft'
    assert client.get('/api/announcements/'+item['id']).status_code==404


def test_archive_only_does_not_see_never_published_archived_notice(client):
    item=create(client,title='未发布的归档稿')
    change(client,item,'archive').raise_for_status()
    member=next(row for row in client.get('/api/team').json()['items'] if row['name']=='普通员工')
    client.patch('/api/team/'+member['id'],json={'expected_revision':member['revision'],
        'permissions':{'announcements.archive':True}}).raise_for_status()
    login(client,'普通员工')
    assert client.get('/api/announcements',params={'status':'archived'}).json()['total']==0
    assert client.get('/api/announcements/'+item['id']).status_code==404
    assert change(client,{**item,'revision':item['revision']+1},'restore').status_code==404


@pytest.mark.parametrize('field,value',[('title','\ud800'),('content_html','<p>\ud800</p>')])
def test_bad_unicode_is_an_actionable_400(client,field,value):
    encoded=json.dumps({'title':'公告','content_html':'正文',field:value},ensure_ascii=True).encode()
    assert client.post('/api/announcements',content=encoded,headers={'Content-Type':'application/json'}).status_code==400


@pytest.mark.parametrize('danger',[
    '<script>alert(1)</script><p onclick="alert(2)">正文</p>',
    '<svg><foreignObject><img src=x onerror=alert(1)></foreignObject></svg><p>正文</p>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=alert(1)>">',
    '<iframe src="https://example.com"></iframe><p>正文</p>',
    '<a href="javascript:alert(1)">安全文字</a>',
    '<a href="data:text/html,alert(1)" onclick="alert(2)">正文</a>',
    '<p style="background-image:url(https://evil.test);position:fixed;color:#ff0000">正文</p>',
    '<a href="https://user:password@example.com">正文</a>',
    '<span style="color:expression(alert(1));font-size:9999px">正文</span>',
])
def test_html_sanitizer_strips_executable_markup(danger):
    cleaned,_=notices.sanitize_html(danger,'a'*32)
    lowered=cleaned.lower()
    for forbidden in ('<script','<svg','<iframe','<math','onclick','onerror','javascript:','data:','expression(','url(','position:','password@'):
        assert forbidden not in lowered


@pytest.mark.parametrize('image_src',['https://example.com/x.jpg','data:image/png;base64,abc','file:///secret.png','/media/abc',
    '/api/announcements/'+'b'*32+'/attachments/'+'c'*32])
def test_external_or_unbound_rich_images_are_rejected(image_src):
    with pytest.raises(HTTPException) as error:
        notices.sanitize_html('<img src="'+image_src+'">','a'*32)
    assert error.value.status_code==400


def test_word_style_table_and_basic_formatting_survive():
    value='<h2 style="text-align:center;color:rgb(22, 33, 44)">标题</h2><p><b>粗体</b><i>斜体</i><u>下划线</u></p><ul><li>条目</li></ul><table><tr><td colspan="2">规格</td></tr></table><a href="https://example.com/a?x=1&amp;y=2">链接</a>'
    cleaned,preview=notices.sanitize_html(value,'a'*32)
    assert '<table>' in cleaned and 'colspan="2"' in cleaned and '<b>粗体</b>' in cleaned
    assert 'text-align:center' in cleaned and 'color:rgb(22, 33, 44)' in cleaned
    assert 'rel="noopener noreferrer"' in cleaned and '标题' in preview
