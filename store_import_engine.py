"""On-demand storefront collection in a dedicated, visible browser.

Only page-owned evidence is read. Login and platform challenges remain normal
user interactions. A collection job creates a preview, never catalog records.
"""
from __future__ import annotations

import copy
import hashlib
import json
import queue
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from fastapi import HTTPException

import backend
import browser_price_engine as browser_support
import price_capture
import store_import_parser as parser

TTL = 3600
MAX_JOBS = 4
MAX_IMAGE_BYTES = 12 * 1024 * 1024
_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_directory: Path | None = None
_dns_slots = threading.BoundedSemaphore(4)


def _resolve(host, port, timeout=3):
    # OS DNS resolution can outlive a socket timeout. Keep it off the worker,
    # bound concurrency, and fail closed if all resolver slots are occupied.
    if not _dns_slots.acquire(blocking=False):
        raise ValueError('网络地址解析繁忙，请稍后重试。')
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put((True, price_capture._resolve_public_addresses(host, port)))
        except Exception as exc:
            result.put((False, exc))
        finally:
            _dns_slots.release()
    threading.Thread(target=resolve, name='midoo-store-dns', daemon=True).start()
    try:
        ok, value = result.get(timeout=max(.01, timeout))
    except queue.Empty:
        raise ValueError('网络地址解析超时。') from None
    if not ok:
        raise value
    return value


def _guard(url):
    parts = urlsplit(price_capture.safe_validate_url(url))
    _resolve(parts.hostname, 443 if parts.scheme == 'https' else 80)


def _safe_route(route):
    try:
        _guard(route.request.url)
        route.continue_()
    except Exception:
        try:
            route.abort('blockedbyclient')
        except Exception:
            pass


def init_engine(directory: Path):
    global _directory
    _directory = Path(directory).resolve()


def _profile(user_id, store_id, channel):
    if _directory is None:
        raise HTTPException(503, '店铺采集尚未初始化。')
    key = hashlib.sha256(json.dumps([user_id, 'Temu', store_id, channel]).encode()).hexdigest()
    base = _directory / 'browser-store-profiles'
    target = base / key
    if base.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(_directory):
        raise HTTPException(503, '店铺浏览器资料目录无效。')
    target.mkdir(parents=True, exist_ok=True)
    return target


def _view(job):
    return copy.deepcopy({key: job[key] for key in (
        'id', 'state', 'message', 'store_id', 'store_name', 'store_url',
        'candidates', 'items', 'warnings', 'progress', 'draft_id')})


def _owned(owner, identity):
    job = _jobs.get(identity)
    if not job or job['_owner'] != owner:
        raise HTTPException(404, '采集任务不存在或不属于当前会话。')
    return job


def start(owner, user, url):
    try:
        store = parser.canonical_store_url(url)
    except (TypeError, ValueError):
        raise HTTPException(400, '请填写含 mall_id 的 Temu 店铺前端链接。') from None
    with _lock:
        for job in _jobs.values():
            if not job['_done'].is_set():
                raise HTTPException(409, '已有店铺采集窗口，请先完成或关闭。')
        for key in list(_jobs):
            if len(_jobs) >= MAX_JOBS and _jobs[key]['_done'].is_set():
                del _jobs[key]
        identity = secrets.token_urlsafe(24)
        job = dict(id=identity, state='opening', message='正在打开专用店铺浏览器…',
                   store_id=store['store_id'], store_url=store['url'], store_name='',
                   candidates=[], items=[], warnings=[], progress=0, draft_id='',
                   _owner=owner, _user=copy.deepcopy(user), _created=time.monotonic(),
                   _commands=queue.Queue(maxsize=1), _stop=threading.Event(), _done=threading.Event(),
                   _seen=set(), _deadline=0)
        _jobs[identity] = job
        threading.Thread(target=_worker, args=(job,), name='midoo-store-collector', daemon=True).start()
        return _view(job)


def snapshot(owner, identity):
    with _lock:
        return _view(_owned(owner, identity))


def command(owner, identity, action, ids=None):
    with _lock:
        job = _owned(owner, identity)
        if job['state'] not in {'ready', 'discovered', 'preview', 'attention'} or job['_done'].is_set():
            raise HTTPException(409, '采集窗口正在工作或已关闭，请稍后刷新。')
        if action not in {'discover', 'collect'}:
            raise HTTPException(400, '采集操作无效。')
        if action == 'collect':
            known = {item['product_id'] for item in job['candidates']}
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 20 or
                    any(not isinstance(i, str) or i not in known for i in ids) or len(set(ids)) != len(ids)):
                raise HTTPException(400, '请选择本次发现的 1–20 个商品。')
        try:
            job['_commands'].put_nowait((action, ids))
        except queue.Full:
            raise HTTPException(409, '上一次操作尚未完成。') from None
        job.update(state='discovering' if action == 'discover' else 'collecting', progress=0,
                   draft_id='', _draft_sources={},
                   message='正在读取店铺商品…' if action == 'discover' else '正在逐个读取详情和规格价格…')
        return _view(job)


def close(owner, identity):
    with _lock:
        job = _owned(owner, identity)
        job['_stop'].set()
        job.update(state='closing', message='正在关闭本次采集窗口；已生成的预览仍可核对。')
        return _view(job)


def committed(owner, draft_id, result):
    """Advance a live shop cursor only for rows actually committed."""
    with _lock:
        for job in _jobs.values():
            if job['_owner'] == owner and job.get('draft_id') == draft_id:
                sources = job.get('_draft_sources', {})
                for row in result.get('items', []):
                    if row.get('item_id') in sources:
                        job['_seen'].add(sources[row['item_id']])


def release_preview(owner, draft_id):
    with _lock:
        for job in _jobs.values():
            if job['_owner'] == owner and job.get('draft_id') == draft_id:
                job.update(draft_id='', _draft_sources={})


def _read(page):
    # Locator.evaluate has a bounded element acquisition. The browser driver
    # watchdog below also closes only the context belonging to this job.
    return page.locator('body').evaluate('(body) => (' + parser.SNAPSHOT_JS + ')()', timeout=12000)


def _watch(job, manager):
    while not job['_done'].wait(.5):
        expired = time.monotonic() - job['_created'] >= TTL
        stalled = job['_deadline'] and time.monotonic() > job['_deadline']
        if job['_stop'].is_set() or expired or stalled:
            job['_stop'].set()
            browser_support._request_driver_stop(manager)
            return


def _image(url):
    """Public image fetch with DNS-pinned sockets and bounded decoded payload."""
    deadline = time.monotonic() + 25
    current = price_capture.safe_validate_url(url)
    for redirect in range(4):
        if time.monotonic() >= deadline:
            raise ValueError('主图读取超时。')
        parts = urlsplit(current)
        port = 443 if parts.scheme == 'https' else 80
        addresses = _resolve(parts.hostname, port, min(3, max(.01, deadline-time.monotonic())))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('主图读取超时。')
        cls = price_capture._PinnedHTTPSConnection if parts.scheme == 'https' else price_capture._PinnedHTTPConnection
        connection = cls(parts.hostname, addresses[0], port)
        connection.timeout = min(price_capture.NETWORK_TIMEOUT, remaining)
        try:
            connection.request('GET', urlunsplit(('', '', parts.path or '/', parts.query, '')), headers={
                'User-Agent': price_capture.USER_AGENT, 'Accept': 'image/*',
                'Accept-Encoding': 'identity', 'Connection': 'close'})
            response = connection.getresponse()
            if time.monotonic() >= deadline:
                raise ValueError('主图读取超时。')
            headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status in {301, 302, 303, 307, 308}:
                if redirect >= 3 or not headers.get('location'):
                    raise ValueError('主图重定向过多。')
                current = price_capture.safe_validate_url(urljoin(current, headers['location']))
                continue
            if response.status != 200 or not headers.get('content-type', '').lower().startswith('image/'):
                raise ValueError('主图未返回有效图片。')
            if headers.get('content-encoding', 'identity').lower() != 'identity':
                raise ValueError('主图压缩传输格式不支持。')
            declared = headers.get('content-length', '')
            if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                raise ValueError('主图超过 12 MB。')
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise ValueError('主图读取超时。')
                chunk = response.read(min(65536, MAX_IMAGE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise ValueError('主图超过 12 MB。')
                chunks.append(chunk)
            return backend._prepare_image(b''.join(chunks), '店铺主图')
        finally:
            connection.close()
    raise ValueError('主图无法读取。')


def _discover(page, job):
    all_items, warnings = {}, []
    for _ in range(5):
        if job['_stop'].is_set():
            break
        captured = _read(page)
        found = parser.parse_store_snapshot(captured, job['store_id'], seen_ids=job['_seen'], limit=20)
        for item in found['items']:
            all_items.setdefault(item['product_id'], item)
        warnings.extend(found.get('warnings', []))
        if found.get('store_name'):
            job['store_name'] = found['store_name'][:160]
        if len(all_items) >= 20:
            break
        page.mouse.wheel(0, 700)
        page.wait_for_timeout(500)
    with _lock:
        job.update(candidates=list(all_items.values())[:20], warnings=list(dict.fromkeys(warnings)),
                   state='discovered', message=f'本次发现 {min(len(all_items), 20)} 个候选商品，请选择后读取详情。')


def _collect(context, job, ids):
    import store_import_storage as storage
    selected = [item for item in job['candidates'] if item['product_id'] in ids]
    items, prepared = [], {}
    for index, candidate in enumerate(selected):
        if job['_stop'].is_set():
            break
        page = job.get('_attention_page') if job.get('_attention_id') == candidate['product_id'] else None
        reused = page is not None and not page.is_closed()
        page = page if reused else context.new_page()
        keep_open = False
        try:
            job['_deadline'] = time.monotonic() + 70
            _guard(candidate['url'])
            if not reused:
                page.goto(candidate['url'], wait_until='domcontentloaded', timeout=25000)
            item = None
            for attempt in range(3):
                captured = _read(page)
                blocked = bool(re.search(r'/(?:login|captcha|verify)[./?]|captcha|verify (?:that )?you are human|请先登录|人机验证|完成验证码',
                              str(captured.get('url', '')) + ' ' + str(captured.get('text', '')), re.I))
                if blocked:
                    keep_open = True
                    with _lock:
                        job.update(state='attention', message='详情页要求登录或验证，采集已暂停。请在保留的商品窗口正常完成，再点“继续读取详情”。',
                                   _attention_page=page, _attention_id=candidate['product_id'], items=items)
                    return
                try:
                    item = parser.parse_product_snapshot(captured, job['store_id'],
                            store_name=job['store_name'], expected_product_id=candidate['product_id'])
                except ValueError:
                    if attempt == 2:
                        raise
                if item and item.get('importable'):
                    break
                if attempt < 2:
                    page.wait_for_timeout(800)
            if item is None:
                raise ValueError('详情尚未加载。')
            item['id'] = item['product_id']
            # Parent evidence can be staged with an explicit missing-variants
            # warning. The storage layer keeps an unpriced, pending source
            # archive; it never invents a merchant SKU or variant quote.
            item['importable'] = bool(item['membership_verified'] and item['image'] and item['name'])
            if item['membership_verified'] and item['image'] and item['name']:
                try:
                    prepared[item['id']] = _image(item['image'])
                except Exception:
                    item['warnings'].append('main_image_download_failed')
                    item['importable'] = False
            items.append(item)
        except Exception:
            items.append({**candidate, 'id': candidate['product_id'], 'variants': [], 'importable': False,
                          'completeness': 'partial', 'warnings': ['detail_read_failed_or_login_required']})
        finally:
            if not keep_open:
                page.close()
                if job.get('_attention_page') is page:
                    job.pop('_attention_page', None)
                    job.pop('_attention_id', None)
        with _lock:
            job.update(progress=index + 1, items=copy.deepcopy(items))
    valid = [item for item in items if item.get('importable') and item['id'] in prepared]
    draft = storage.create_preview(job['_owner'], job['_user'], valid, prepared) if valid and not job['_stop'].is_set() else None
    with _lock:
        job.update(state='preview', items=items, draft_id=draft['id'] if draft else '',
                   _draft_sources={item['id']: item['source_id'] for item in draft['items']} if draft else {},
                   message=f'已读取 {len(items)} 个商品，{len(valid)} 个可进入导入预览。缺项已标明，请核对。')


def _worker(job):
    context = manager = None
    try:
        manager = browser_support._playwright_factory()
        playwright = manager.__enter__()
        threading.Thread(target=_watch, args=(job, manager), name='midoo-store-watch', daemon=True).start()
        job['_deadline'] = time.monotonic() + 70
        for channel, _ in browser_support._browsers():
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(_profile(job['_user']['id'], job['store_id'], channel)),
                    channel=channel, headless=False, chromium_sandbox=True, timeout=15000,
                    handle_sigint=False, handle_sigterm=False, handle_sighup=False,
                    accept_downloads=False, service_workers='block', permissions=[],
                    ignore_https_errors=False, no_viewport=True)
                break
            except Exception:
                continue
        if context is None:
            raise ValueError('无法打开专用店铺浏览器，请检查本机 Edge 或 Chrome。')
        context.route('**/*', _safe_route)
        context.route_web_socket('**/*', lambda socket: socket.close())
        context.set_default_timeout(8000)
        def secure_page(page):
            if len(context.pages) > 4:
                page.close()
                return
            page.on('download', lambda download: download.cancel())
            page.on('dialog', lambda dialog: dialog.dismiss())
        previous = list(context.pages)
        page = context.new_page()
        secure_page(page)
        context.on('page', secure_page)
        for old in previous:
            old.close()
        try:
            _guard(job['store_url'])
            page.goto(job['store_url'], wait_until='domcontentloaded', timeout=25000)
        except Exception:
            pass
        with _lock:
            job.update(state='ready', message='请在专用窗口打开店铺；如需登录或验证码，请正常完成，再点“读取店铺”。登录仅保留在本机。')
            job['_deadline'] = 0
        while not job['_stop'].is_set() and time.monotonic() - job['_created'] < TTL and not page.is_closed():
            try:
                action, ids = job['_commands'].get_nowait()
            except queue.Empty:
                page.wait_for_timeout(150)
                continue
            try:
                job['_deadline'] = time.monotonic() + 40
                if action == 'discover':
                    _discover(page, job)
                else:
                    _collect(context, job, ids)
            except Exception:
                with _lock:
                    job.update(state='attention', message='当前页面无法完整读取。请检查是否已进入目标店铺、完成登录或平台验证，再重试。')
            finally:
                job['_deadline'] = 0
    except Exception:
        with _lock:
            job.update(state='error', message='无法启动或保持店铺采集窗口，请检查本机浏览器后重试。')
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if manager:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass
        with _lock:
            job['_done'].set()
            if job['state'] != 'error':
                job.update(state='closed', message='采集窗口已关闭；已生成的导入预览可继续核对。')


def shutdown():
    with _lock:
        for job in _jobs.values():
            job['_stop'].set()
