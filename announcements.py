"""Authenticated company notices with reviewed rich text and local attachments.

All state, including attachment bytes, stays in the library SQLite database so
the existing consistent backup contains notices without a second file tree.
"""
from __future__ import annotations

import html
import io
import json
import re
import threading
import uuid
import warnings
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

import backend
import permissions

router = APIRouter(prefix='/api/announcements', dependencies=[Depends(backend.require_user)])
MAX_HTML = 100000
MAX_FILE = 10 * 1024 * 1024
MAX_IMAGE = 5 * 1024 * 1024
MAX_NOTICE_BYTES = 20 * 1024 * 1024
MAX_STORAGE = 500 * 1024 * 1024
MAX_ASSETS = 6
MAX_NOTICES = 2000
_init_lock = threading.RLock()
_initialized = None
_ASSET = re.compile(r'/api/announcements/([a-f0-9]{32})/attachments/([a-f0-9]{32})')
_FILES = {'.pdf':'application/pdf', '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
          '.txt':'text/plain', '.md':'text/plain', '.csv':'text/csv', '.json':'application/json', '.zip':'application/zip'}
_IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def _can(user, key):
    return permissions.has(user, 'announcements.' + key)


def _require(user, key):
    permissions.require(user, 'announcements.' + key)


def _manager(user):
    return permissions.is_owner(user) or any(_can(user, key) for key in ('edit', 'publish'))


def init_storage():
    global _initialized
    if backend.DB_PATH is None:
        raise RuntimeError('请先初始化公司商品库。')
    marker = (str(backend.DB_PATH.resolve()), backend.DB_PATH.stat().st_ino)
    with _init_lock:
        if marker == _initialized:
            with backend._connection() as conn:
                if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('company_announcements','announcement_assets','announcement_revisions','announcement_reads')").fetchone()[0] == 4:
                    return
        with backend._connection(write=True) as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS company_announcements (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, content_html TEXT NOT NULL DEFAULT '',
                preview TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
                pinned INTEGER NOT NULL DEFAULT 0, expires_at TEXT,
                created_by TEXT NOT NULL REFERENCES users(id), author TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT,
                published_revision INTEGER, published_expires_at TEXT, published_snapshot TEXT
            );
            CREATE INDEX IF NOT EXISTS company_announcements_status ON company_announcements(status,pinned,published_at);
            CREATE TABLE IF NOT EXISTS announcement_assets (
                id TEXT PRIMARY KEY, announcement_id TEXT NOT NULL REFERENCES company_announcements(id),
                name TEXT NOT NULL, kind TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                data BLOB NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS announcement_assets_notice ON announcement_assets(announcement_id,deleted);
            CREATE TABLE IF NOT EXISTS announcement_revisions (
                announcement_id TEXT NOT NULL REFERENCES company_announcements(id), revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL, action TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
                actor TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(announcement_id,revision)
            );
            CREATE TABLE IF NOT EXISTS announcement_reads (
                announcement_id TEXT NOT NULL REFERENCES company_announcements(id), user_id TEXT NOT NULL REFERENCES users(id),
                revision INTEGER NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY(announcement_id,user_id)
            );
            ''')
        _initialized = marker


def _escape(value):
    return html.escape(str(value), quote=True)


def _style(raw):
    accepted = []
    for part in raw.split(';')[:24]:
        key, separator, value = part.partition(':')
        key, value = key.strip().lower(), value.strip().lower()
        if not separator or len(value) > 100:
            continue
        valid = (key == 'text-align' and value in {'left','center','right','justify'} or
                 key == 'font-weight' and value in {'normal','bold','400','500','600','700'} or
                 key == 'font-style' and value in {'normal','italic'} or
                 key == 'text-decoration' and value in {'none','underline','line-through'} or
                 key == 'font-size' and bool(re.fullmatch(r'(?:[89]|[1-4]\d)(?:px|pt)', value)))
        if key in {'color','background-color'}:
            valid = value in {'black','white','red','blue','green','gray','grey','orange','purple','yellow','transparent'} or bool(re.fullmatch(r'#(?:[a-f0-9]{3}|[a-f0-9]{6}|[a-f0-9]{8})', value))
            match = re.fullmatch(r'rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)', value)
            valid = valid or bool(match and all(int(n) <= 255 for n in match.groups()))
        if valid:
            accepted.append(key + ':' + value)
    return ';'.join(accepted)


def _link(value):
    if len(value) > 2000 or re.search(r'[\x00-\x20\x7f-\x9f]', value):
        return None
    try:
        parts = urlsplit(value)
        return value if parts.scheme.lower() in {'http','https'} and parts.hostname and parts.username is None and parts.password is None else None
    except ValueError:
        return None


class _RichText(HTMLParser):
    tags = {'p','div','span','h1','h2','h3','h4','strong','b','em','i','u','s','ul','ol','li','blockquote','pre','code',
            'br','hr','table','thead','tbody','tfoot','tr','td','th','a','img','sub','sup'}
    void = {'br','hr','img'}
    blocked = {'script','style','iframe','frame','frameset','object','embed','svg','math','template','noscript'}

    def __init__(self, announcement_id, assets):
        super().__init__(convert_charrefs=True)
        self.announcement_id, self.assets = announcement_id, assets
        self.parts, self.text, self.stack, self.suppressed = [], [], [], []
        self.nodes = 0

    def handle_starttag(self, tag, attributes):
        self.nodes += 1
        if self.nodes > 5000 or len(self.stack) > 64 or len(self.suppressed) > 64:
            raise HTTPException(400, '公告排版过于复杂，请简化后保存。')
        if self.suppressed or tag in self.blocked:
            self.suppressed.append(tag)
            return
        if tag not in self.tags:
            return
        values = dict(attributes)
        attrs = []
        style = _style(values.get('style') or '')
        if style:
            attrs.append(('style', style))
        align = values.get('align') or ''
        if not style and align in {'left','center','right','justify'}:
            attrs.append(('style', 'text-align:' + align))
        if tag == 'a':
            href = _link(values.get('href') or '')
            if href:
                attrs += [('href', href), ('target','_blank'), ('rel','noopener noreferrer')]
        if tag == 'img':
            src = values.get('src') or ''
            matched = _ASSET.fullmatch(src)
            if not matched or matched[1] != self.announcement_id or self.assets.get(matched[2]) != 'image':
                raise HTTPException(400, '公告图片只能使用本篇公告已经上传的图片，请重新插入。')
            attrs += [('src',src), ('alt',(values.get('alt') or '公告图片')[:300])]
            for key in ('width','height'):
                value = values.get(key) or ''
                if value.isdigit() and 1 <= int(value) <= 1600:
                    attrs.append((key,str(int(value))))
        if tag in {'td','th'}:
            for key in ('colspan','rowspan'):
                value = values.get(key) or ''
                if value.isdigit() and 1 <= int(value) <= 20:
                    attrs.append((key,str(int(value))))
        self.parts.append('<' + tag + ''.join(' ' + key + '="' + _escape(value) + '"' for key,value in attrs) + '>')
        if tag not in self.void:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag in self.suppressed:
                at = len(self.suppressed) - 1 - self.suppressed[::-1].index(tag)
                self.suppressed = self.suppressed[:at]
            return
        if tag in self.stack:
            while self.stack:
                item = self.stack.pop()
                self.parts.append('</' + item + '>')
                if item == tag:
                    break
            if tag in {'p','div','li','tr','h1','h2','h3','h4'}:
                self.text.append(' ')

    def handle_data(self, data):
        if not self.suppressed:
            data = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', data)
            self.parts.append(html.escape(data))
            self.text.append(data)

    def result(self):
        while self.stack:
            self.parts.append('</' + self.stack.pop() + '>')
        return ''.join(self.parts), re.sub(r'\s+', ' ', ''.join(self.text)).strip()[:500]


def sanitize_html(value, announcement_id, assets=None):
    if not isinstance(value, str) or len(value) > MAX_HTML:
        raise HTTPException(400, '公告正文最多100000字，请拆分内容。')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise HTTPException(400, '公告正文包含无效字符，请重新粘贴。') from None
    parser = _RichText(announcement_id, assets or {})
    try:
        parser.feed(value)
        parser.close()
    except (RecursionError, ValueError):
        raise HTTPException(400, '公告排版无法安全读取，请粘贴为普通文字后重新排版。') from None
    output, preview = parser.result()
    if len(output) > MAX_HTML:
        raise HTTPException(400, '公告排版数据过大，请简化后保存。')
    return output, preview


async def _json(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 500000:
            raise HTTPException(413, '公告请求过大，请减少正文或改用附件。')
        data.extend(chunk)
    try:
        result = json.loads(data)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(400, '请提交有效的公告信息。') from None


def _title(value):
    if not isinstance(value, str) or len(value) > 160 or re.search(r'[\x00-\x1f\x7f-\x9f\ud800-\udfff\u202a-\u202e\u2066-\u2069]', value):
        raise HTTPException(400, '公告标题最多160字，不能包含控制字符。')
    return value.strip()


def _expiry(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise HTTPException(400, '公告失效时间格式不正确。')
    try:
        stamp = datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except ValueError:
        raise HTTPException(400, '公告失效时间须包含时区。') from None


def _row(conn, identity):
    row = conn.execute('SELECT * FROM company_announcements WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise HTTPException(404, '公告不存在或不可查看。')
    return row


def _assets(conn, identity):
    rows = conn.execute('SELECT id,name,kind,mime,size FROM announcement_assets WHERE announcement_id=? AND deleted=0 ORDER BY created_at,id', (identity,)).fetchall()
    return [{**dict(row), 'url':f'/api/announcements/{identity}/attachments/{row["id"]}'} for row in rows]


def _snapshot(conn, row):
    keys = ('id','title','content_html','preview','status','revision','pinned','expires_at','author','created_by','created_at','updated_at','published_at','published_revision')
    result = {key:row[key] for key in keys}
    result['pinned'] = bool(result['pinned'])
    result['attachments'] = _assets(conn, row['id'])
    return result


def _live(row):
    return row['status'] == 'published' and row['published_snapshot'] is not None and (row['published_expires_at'] is None or row['published_expires_at'] > backend.now())


def _view(conn, row, user, *, published=False):
    if published or not _manager(user):
        archive_view = not published and _can(user,'archive') and row['published_snapshot'] is not None and (row['status']=='archived' or row['status']=='published')
        if not _live(row) and not archive_view:
            raise HTTPException(404, '公告不存在或不可查看。')
        result = json.loads(row['published_snapshot'])
        if archive_view:
            # An archiver may restore the old public version, but never inspect
            # a working draft or newly uploaded draft attachments.
            result.update(status=row['status'],revision=row['revision'])
    else:
        result = _snapshot(conn, row)
    seen = conn.execute('SELECT revision FROM announcement_reads WHERE announcement_id=? AND user_id=?', (row['id'],user['id'])).fetchone()
    result.update(can_edit=_can(user,'edit'), can_publish=_can(user,'publish'), can_archive=_can(user,'archive'),
                  published_revision=row['published_revision'],
                  control_revision=row['revision'] if _manager(user) or _can(user,'archive') else None,
                  has_unpublished_changes=bool(row['status']=='published' and row['revision'] != row['published_revision']),
                  expired=bool(row['status']=='published' and row['published_expires_at'] and row['published_expires_at'] <= backend.now()),
                  is_read=bool(seen and seen['revision'] == result['revision']))
    return result


def _expected(payload, row):
    revision = payload.get('expected_revision')
    if type(revision) is not int or revision < 1:
        raise HTTPException(400, '请提供公告的当前版本。')
    if revision != row['revision']:
        raise HTTPException(409, '公告已被其他人修改，请刷新后重新编辑。')


def _history(conn, row, user, action):
    snapshot = _snapshot(conn,row)
    conn.execute('INSERT INTO announcement_revisions VALUES(?,?,?,?,?,?,?)',
                 (row['id'],row['revision'],json.dumps(snapshot,ensure_ascii=False),action,user['id'],user['name'],backend.now()))
    labels = {'create':'创建公告', 'edit':'编辑公告', 'publish':'发布公告',
              'withdraw':'撤回公告', 'archive':'归档公告', 'restore':'将公告恢复为草稿',
              'attachment':'上传公告附件', 'attachment_remove':'移除公告附件'}
    backend._audit(conn,user,'announcement_' + action,
                   labels.get(action, '更新公告') + '：' + (row['title'] or '未命名公告'))


def _unread(conn, user):
    return conn.execute('''SELECT COUNT(*) FROM company_announcements a
        LEFT JOIN announcement_reads r ON r.announcement_id=a.id AND r.user_id=?
        WHERE a.status='published' AND a.published_snapshot IS NOT NULL
        AND (a.published_expires_at IS NULL OR a.published_expires_at>?)
        AND (r.revision IS NULL OR r.revision<>a.published_revision)''', (user['id'],backend.now())).fetchone()[0]


def _envelope(conn, rows, user, *, total=None, published=False):
    items = [_view(conn,row,user,published=published) for row in rows]
    return {'items':items,'total':len(items) if total is None else total,'unread_count':_unread(conn,user),
            'can_edit':_can(user,'edit'),'can_publish':_can(user,'publish'),'can_archive':_can(user,'archive')}


@router.get('')
def list_announcements(request: Request, status: str = Query('published'), limit: int = Query(40,ge=1,le=100), offset: int = Query(0,ge=0,le=10000)):
    init_storage()
    user = backend._user(request)
    if status not in {'published','draft','archived','expired'}:
        raise HTTPException(400, '公告状态不正确。')
    if status == 'draft' and not _manager(user) or status in {'archived','expired'} and not (_manager(user) or _can(user,'archive')):
        raise HTTPException(403, '没有查看公告管理列表的权限。')
    condition, params = "status='published' AND published_snapshot IS NOT NULL AND (published_expires_at IS NULL OR published_expires_at>?)", [backend.now()]
    if status == 'draft':
        condition, params = "(status='draft' OR (status='published' AND revision<>published_revision))", []
    elif status == 'archived':
        condition, params = "status='archived'", []
    elif status == 'expired':
        condition, params = "status='published' AND published_expires_at IS NOT NULL AND published_expires_at<=?", [backend.now()]
    if status in {'archived','expired'} and not _manager(user):
        condition += ' AND published_snapshot IS NOT NULL'
    with backend._connection() as conn:
        count = conn.execute('SELECT COUNT(*) FROM company_announcements WHERE '+condition,params).fetchone()[0]
        ordering = "json_extract(published_snapshot,'$.pinned')" if status=='published' else 'pinned'
        rows = conn.execute('SELECT * FROM company_announcements WHERE '+condition+' ORDER BY '+ordering+' DESC,COALESCE(published_at,updated_at) DESC,id LIMIT ? OFFSET ?', [*params,limit,offset]).fetchall()
        return _envelope(conn,rows,user,total=count,published=status=='published')


@router.get('/summary')
def summary(request: Request):
    init_storage()
    user = backend._user(request)
    with backend._connection() as conn:
        rows = conn.execute("SELECT * FROM company_announcements WHERE status='published' AND published_snapshot IS NOT NULL AND (published_expires_at IS NULL OR published_expires_at>?) ORDER BY json_extract(published_snapshot,'$.pinned') DESC,published_at DESC,id LIMIT 3",(backend.now(),)).fetchall()
        result = _envelope(conn,rows,user,published=True)
        result['latest'] = result['items'][0] if result['items'] else None
        return result


@router.post('')
async def create(request: Request):
    user = backend._user(request)
    _require(user,'edit')
    body = await _json(request)
    if set(body) - {'title','content_html','pinned','expires_at'}:
        raise HTTPException(400, '公告字段不正确。')
    identity = uuid.uuid4().hex
    title = _title(body.get('title','未命名公告'))
    content, preview = sanitize_html(body.get('content_html',''),identity)
    pinned = body.get('pinned',False)
    if type(pinned) is not bool:
        raise HTTPException(400, '置顶选项须为布尔值。')
    expires = _expiry(body.get('expires_at'))
    init_storage()
    with backend._connection(write=True) as conn:
        if conn.execute('SELECT COUNT(*) FROM company_announcements').fetchone()[0] >= MAX_NOTICES:
            raise HTTPException(409, '公告数量已达到上限，请联系老板整理。')
        stamp = backend.now()
        conn.execute('''INSERT INTO company_announcements(id,title,content_html,preview,pinned,expires_at,created_by,author,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',(identity,title,content,preview,int(pinned),expires,user['id'],user['name'],stamp,stamp))
        row = _row(conn,identity)
        _history(conn,row,user,'create')
        return _view(conn,row,user)


@router.get('/{identity}')
def detail(identity: str, request: Request, published: bool = False):
    init_storage()
    with backend._connection() as conn:
        return _view(conn,_row(conn,identity),backend._user(request),published=published)


@router.patch('/{identity}')
async def edit(identity: str, request: Request):
    user = backend._user(request)
    _require(user,'edit')
    body = await _json(request)
    if set(body)-{'title','content_html','pinned','expires_at','expected_revision'}:
        raise HTTPException(400, '只能修改公告标题、正文、置顶和失效时间。')
    init_storage()
    with backend._connection(write=True) as conn:
        row = _row(conn,identity)
        _expected(body,row)
        if row['status']=='archived':
            raise HTTPException(409, '请先将公告恢复为草稿再编辑。')
        title = _title(body.get('title',row['title']))
        content, preview = sanitize_html(body.get('content_html',row['content_html']), identity, {a['id']:a['kind'] for a in _assets(conn,identity)})
        pinned = body.get('pinned',bool(row['pinned']))
        if type(pinned) is not bool:
            raise HTTPException(400, '置顶选项须为布尔值。')
        expires = _expiry(body.get('expires_at',row['expires_at']))
        conn.execute('UPDATE company_announcements SET title=?,content_html=?,preview=?,pinned=?,expires_at=?,revision=revision+1,updated_at=? WHERE id=?',
                     (title,content,preview,int(pinned),expires,backend.now(),identity))
        row = _row(conn,identity)
        _history(conn,row,user,'edit')
        return _view(conn,row,user)


def _transition(identity, request, payload, action):
    user = backend._user(request)
    _require(user,'archive' if action in {'archive','restore'} else 'publish')
    if not isinstance(payload,dict) or set(payload) != {'expected_revision'}:
        raise HTTPException(400, '请提供公告当前版本。')
    init_storage()
    with backend._connection(write=True) as conn:
        row = _row(conn,identity)
        _expected(payload,row)
        if not _manager(user) and (row['published_snapshot'] is None or row['status']=='draft'):
            raise HTTPException(404,'公告不存在或不可管理。')
        if action=='publish':
            if row['status']=='archived':
                raise HTTPException(409,'请先恢复公告再发布。')
            if not row['title'].strip() or not (row['preview'].strip() or _assets(conn,identity)):
                raise HTTPException(400,'发布前请填写标题，并添加正文或附件。')
            if row['expires_at'] and row['expires_at'] <= backend.now():
                raise HTTPException(400,'失效时间须晚于发布时间。')
            stamp = backend.now()
            conn.execute("UPDATE company_announcements SET status='published',revision=revision+1,published_at=?,updated_at=?,published_revision=revision+1,published_expires_at=expires_at WHERE id=?",(stamp,stamp,identity))
            row = _row(conn,identity)
            conn.execute('UPDATE company_announcements SET published_snapshot=? WHERE id=?',(json.dumps(_snapshot(conn,row),ensure_ascii=False),identity))
        else:
            expected_status = 'archived' if action=='restore' else 'published' if action=='withdraw' else None
            if expected_status and row['status'] != expected_status:
                raise HTTPException(409,'公告状态已变化，请刷新后重试。')
            conn.execute('UPDATE company_announcements SET status=?,revision=revision+1,updated_at=? WHERE id=?',
                         ('archived' if action=='archive' else 'draft',backend.now(),identity))
        row = _row(conn,identity)
        _history(conn,row,user,action)
        if not _manager(user) and row['status']=='draft':
            return {'id':row['id'],'status':'draft','revision':row['revision'],'control_revision':row['revision'],
                    'can_edit':False,'can_publish':False,'can_archive':True}
        return _view(conn,row,user)


@router.post('/{identity}/publish')
def publish(identity: str, request: Request, payload: dict = Body(...)):
    return _transition(identity,request,payload,'publish')


@router.post('/{identity}/withdraw')
def withdraw(identity: str, request: Request, payload: dict = Body(...)):
    return _transition(identity,request,payload,'withdraw')


@router.post('/{identity}/archive')
def archive(identity: str, request: Request, payload: dict = Body(...)):
    return _transition(identity,request,payload,'archive')


@router.post('/{identity}/restore')
def restore(identity: str, request: Request, payload: dict = Body(...)):
    return _transition(identity,request,payload,'restore')


@router.post('/{identity}/read')
def mark_read(identity: str, request: Request, payload: dict = Body(...)):
    if not isinstance(payload,dict) or set(payload) != {'revision'} or type(payload['revision']) is not int:
        raise HTTPException(400,'请提供当前公开公告的版本。')
    init_storage()
    user=backend._user(request)
    with backend._connection(write=True) as conn:
        row=_row(conn,identity)
        if not _live(row):
            raise HTTPException(404,'公告不存在或不可查看。')
        if payload['revision'] != row['published_revision']:
            raise HTTPException(409,'公告已重新发布，请刷新后阅读。')
        conn.execute('''INSERT INTO announcement_reads VALUES(?,?,?,?) ON CONFLICT(announcement_id,user_id)
            DO UPDATE SET revision=excluded.revision,read_at=excluded.read_at''',(identity,user['id'],row['published_revision'],backend.now()))
        return {'ok':True,'unread_count':_unread(conn,user)}


@router.get('/{identity}/history')
def history(identity: str, request: Request):
    user=backend._user(request)
    if not _manager(user):
        raise HTTPException(403,'没有查看公告版本记录的权限。')
    init_storage()
    with backend._connection() as conn:
        _row(conn,identity)
        rows=conn.execute('SELECT revision,snapshot,action,actor,created_at FROM announcement_revisions WHERE announcement_id=? ORDER BY revision DESC LIMIT 50',(identity,)).fetchall()
        return {'items':[{**dict(row),'snapshot':json.loads(row['snapshot'])} for row in rows]}


@router.get('/{identity}/readers')
def readers(identity: str, request: Request):
    user=backend._user(request)
    if not _manager(user):
        raise HTTPException(403,'没有查看公告阅读统计的权限。')
    init_storage()
    with backend._connection() as conn:
        row=_row(conn,identity)
        rows=conn.execute('''SELECT u.id,u.name,r.read_at FROM announcement_reads r JOIN users u ON u.id=r.user_id
            WHERE r.announcement_id=? AND r.revision=? ORDER BY r.read_at DESC''',(identity,row['published_revision'])).fetchall()
        return {'revision':row['published_revision'],'items':[dict(item) for item in rows], 'read_count':len(rows), 'member_count':conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]}


def _prepare_file(filename, content):
    name=(filename or '附件').replace('\\','/').rsplit('/',1)[-1]
    name=re.sub(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]','',name).strip()[:180] or '附件'
    suffix=Path(name).suffix.lower()
    if not content or len(content)>MAX_FILE:
        raise HTTPException(413,'单个附件须有内容且不能超过10MB。')
    if suffix in _IMAGES:
        if len(content)>MAX_IMAGE:
            raise HTTPException(413,'单张公告图片不能超过5MB。')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.width*image.height>16000000 or image.width<1 or image.height<1:
                        raise ValueError
                    image.seek(0)
                    image=ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((2000,2000))
                    result=io.BytesIO();image.save(result,'JPEG',quality=88)
                    encoded=result.getvalue()
            return {'name':Path(name).stem+'.jpg','kind':'image','mime':'image/jpeg','size':len(encoded),'data':encoded}
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):
            raise HTTPException(400,'图片无法安全读取，请使用普通JPG、PNG、WebP或GIF。') from None
    if suffix not in _FILES:
        raise HTTPException(400,'此附件类型不支持。请使用图片、PDF、Office文档、文字或ZIP；不接受网页和可执行文件。')
    # Office/ZIP bytes are download-only; no archive entries are extracted.
    return {'name':name,'kind':'file','mime':_FILES[suffix],'size':len(content),'data':content}


@router.post('/{identity}/attachments')
async def upload(identity: str, request: Request):
    user=backend._user(request)
    _require(user,'edit')
    init_storage()
    with backend._connection() as conn:
        current=_row(conn,identity)
        if current['status']=='archived':
            raise HTTPException(409,'请先恢复公告再添加附件。')
    received=0
    async def receive():
        nonlocal received
        event=await request.receive()
        if event['type']=='http.request':
            received+=len(event.get('body',b''))
            if received>21*1024*1024:
                raise HTTPException(413,'每次上传总量不能超过20MB。')
        return event
    bounded=Request(request.scope,receive=receive)
    async with bounded.form(max_files=MAX_ASSETS,max_fields=2,max_part_size=100000) as form:
        files=form.getlist('files')
        if not 1<=len(files)<=MAX_ASSETS or any(not isinstance(file,UploadFile) for file in files):
            raise HTTPException(400,'请选择1至6个公告附件。')
        if any(isinstance(value,UploadFile) and key!='files' for key,value in form.multi_items()):
            raise HTTPException(400,'请使用公告附件入口上传。')
        revision=form.get('expected_revision')
        if not isinstance(revision,str) or not re.fullmatch(r'[1-9]\d{0,9}',revision):
            raise HTTPException(400,'请提供公告当前版本。')
        total,prepared=0,[]
        for file in files:
            data=await file.read(MAX_FILE+1)
            total+=len(data)
            if len(data)>MAX_FILE or total>MAX_NOTICE_BYTES:
                raise HTTPException(413,'单个附件不超过10MB，每篇公告总量不超过20MB。')
            prepared.append(await run_in_threadpool(_prepare_file,file.filename,data))
    with backend._connection(write=True) as conn:
        row=_row(conn,identity);_expected({'expected_revision':int(revision)},row)
        if row['status']=='archived':
            raise HTTPException(409,'公告已归档，请刷新。')
        existing=conn.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM announcement_assets WHERE announcement_id=? AND deleted=0',(identity,)).fetchone()
        size=sum(item['size'] for item in prepared)
        if existing[0]+len(prepared)>MAX_ASSETS or existing[1]+size>MAX_NOTICE_BYTES:
            raise HTTPException(413,'每篇公告最多6个附件、合计20MB。')
        if conn.execute('SELECT COALESCE(SUM(size),0) FROM announcement_assets').fetchone()[0]+size>MAX_STORAGE:
            raise HTTPException(413,'公告附件总存储达到500MB，请联系老板整理。')
        for item in prepared:
            conn.execute('INSERT INTO announcement_assets(id,announcement_id,name,kind,mime,size,data,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (uuid.uuid4().hex,identity,item['name'],item['kind'],item['mime'],item['size'],item['data'],user['id'],backend.now()))
        conn.execute('UPDATE company_announcements SET revision=revision+1,updated_at=? WHERE id=?',(backend.now(),identity))
        row=_row(conn,identity);_history(conn,row,user,'attachment')
        return _view(conn,row,user)


@router.get('/{identity}/attachments/{asset_id}')
def download(identity: str, asset_id: str, request: Request):
    init_storage()
    user=backend._user(request)
    with backend._connection() as conn:
        row=_row(conn,identity)
        if not _manager(user):
            public_allowed = _live(row) or (_can(user,'archive') and row['published_snapshot'] is not None and row['status'] in {'archived','published'})
            if not public_allowed or asset_id not in {item['id'] for item in json.loads(row['published_snapshot'])['attachments']}:
                raise HTTPException(404,'附件不存在或不可查看。')
        asset=conn.execute('SELECT * FROM announcement_assets WHERE id=? AND announcement_id=?',(asset_id,identity)).fetchone()
        if asset is None:
            raise HTTPException(404,'附件不存在或不可查看。')
        # Download-only documents are never parsed as HTML, including polyglots.
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"}
        if asset['kind']!='image':
            from urllib.parse import quote
            headers['Content-Disposition']="attachment; filename*=UTF-8''"+quote(asset['name'],safe='')
        return Response(bytes(asset['data']),media_type=asset['mime'],headers=headers)


@router.delete('/{identity}/attachments/{asset_id}')
def remove_attachment(identity: str, asset_id: str, request: Request, payload: dict = Body(...)):
    user=backend._user(request);_require(user,'edit')
    if not isinstance(payload,dict) or set(payload)!={'expected_revision'}:
        raise HTTPException(400,'请提供公告当前版本。')
    init_storage()
    with backend._connection(write=True) as conn:
        row=_row(conn,identity);_expected(payload,row)
        if row['status']=='archived':
            raise HTTPException(409,'请先恢复公告再编辑。')
        asset=conn.execute('SELECT id FROM announcement_assets WHERE id=? AND announcement_id=? AND deleted=0',(asset_id,identity)).fetchone()
        if asset is None:
            raise HTTPException(404,'附件不存在。')
        src=f'/api/announcements/{identity}/attachments/{asset_id}'
        content=re.sub(r'<img\b[^>]*\bsrc="'+re.escape(src)+r'"[^>]*>','',row['content_html'])
        conn.execute('UPDATE announcement_assets SET deleted=1 WHERE id=?',(asset_id,))
        content,preview=sanitize_html(content,identity,{a['id']:a['kind'] for a in _assets(conn,identity)})
        conn.execute('UPDATE company_announcements SET content_html=?,preview=?,revision=revision+1,updated_at=? WHERE id=?',(content,preview,backend.now(),identity))
        row=_row(conn,identity);_history(conn,row,user,'attachment_remove')
        return _view(conn,row,user)
