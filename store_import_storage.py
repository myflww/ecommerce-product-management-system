"""Server-owned store capture drafts and atomic, idempotent catalog import.

The collector passes parsed observations and already downloaded/decoded images.
HTTP routes must never expose ``create_preview`` as a client JSON upload endpoint.
Observed storefront prices are separate from the operator's original/base price.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

import backend
import price_storage

MAX_PRODUCTS = 20
MAX_VARIANTS = 100
DRAFT_TTL = 30 * 60
MAX_DRAFTS = 8
MAX_DRAFT_BYTES = 192 * 1024 * 1024
_drafts: dict[str, dict] = {}
_lock = threading.RLock()


def _require_permission(user: dict, action: str) -> None:
    # The 1.7 permission service is supplied by the parallel permission change.
    # Fail closed if this module is accidentally registered without that service.
    try:
        import permissions
    except ImportError:
        raise HTTPException(503, '导入权限组件尚未安装完整，请更新软件。') from None
    permissions.require(user, action)


def init_storage() -> None:
    price_storage.init_storage()
    with backend._connection(write=True) as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS store_import_sources (
            source_key TEXT PRIMARY KEY, product_key TEXT NOT NULL,
            platform TEXT NOT NULL, store_id TEXT NOT NULL, store_name TEXT NOT NULL,
            site TEXT NOT NULL, source_product_id TEXT NOT NULL, source_variant_id TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(id),
            listing_id TEXT NOT NULL, source_url TEXT NOT NULL, image_url TEXT NOT NULL,
            captured_title TEXT NOT NULL, captured_spec TEXT NOT NULL,
            captured_price TEXT NOT NULL DEFAULT '', captured_currency TEXT NOT NULL DEFAULT '',
            warnings TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS store_import_product_key_idx ON store_import_sources(product_key);
        CREATE INDEX IF NOT EXISTS store_import_product_idx ON store_import_sources(product_id);
        ''')


def _text(value, maximum: int, label: str, *, required=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(400, label + '格式不正确或过长。')
    value = value.strip()
    if required and not value:
        raise HTTPException(400, '采集结果缺少' + label + '。')
    return value


def _digest(values: list) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _normalize(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise HTTPException(400, '采集商品格式不正确。')
    platform = _text(raw.get('platform', ''), 60, '平台', required=True)
    store_id = _text(raw.get('store_id', ''), 160, '店铺编号', required=True)
    name = _text(raw.get('store_name', '') or store_id, 160, '店铺名称', required=True)
    external_id = _text(raw.get('source_id', raw.get('product_id', raw.get('id', ''))), 160, '公开商品编号', required=True)
    url = price_storage._validate_url(raw.get('url', raw.get('product_url', '')))
    if not url:
        raise HTTPException(400, '采集结果缺少商品前端链接。')
    site = urlsplit(url).hostname.lower().rstrip('.')
    title = _text(raw.get('title', raw.get('name', '')), 500, '商品名称', required=True)
    image_url = price_storage._validate_url(raw.get('image_url', raw.get('image', '')))
    region = _text(raw.get('region', ''), 80, '地区')
    warnings = raw.get('warnings', [])
    if not isinstance(warnings, list) or len(warnings) > 30:
        raise HTTPException(400, '采集提示格式不正确。')
    warnings = [_text(w, 500, '采集提示') for w in warnings]
    variants = raw.get('variants', [])
    if not isinstance(variants, list) or len(variants) > 200:
        raise HTTPException(400, '单个商品采集结果过大，请拆批核对。')
    limit_exceeded = len(variants) > MAX_VARIANTS
    if limit_exceeded:
        warnings.append('规格超过单个商品 100 条的平台记录上限，暂只预览，请拆分整理后再入库。')
    if len(name) > 80:
        warnings.append('店铺名称超过平台记录显示上限，完整店名仍保留在来源信息中。')
    children, seen = [], set()
    for raw_variant in variants:
        if not isinstance(raw_variant, dict):
            raise HTTPException(400, '子产品格式不正确。')
        vid = _text(raw_variant.get('public_variant_id', raw_variant.get('variant_id', raw_variant.get('id', ''))), 160, '公开规格编号')
        full_spec = _text(raw_variant.get('spec', raw_variant.get('specification', raw_variant.get('name', ''))), 500, '规格')
        spec = full_spec[:200]
        if len(full_spec) > 200:
            warnings.append('有规格超过平台记录显示上限，完整规格仍保留在来源信息中。')
        # A label alone is unstable: changing translations/order must not create
        # duplicate SKUs or silently assign a price to a different combination.
        if not vid:
            warnings.append('有规格缺少稳定的公开编号，暂不创建该规格或保存其价格。')
            continue
        if vid in seen:
            raise HTTPException(400, '采集结果包含重复的公开规格编号，请重新读取。')
        seen.add(vid)
        variant_url = price_storage._validate_url(raw_variant.get('url', ''))
        if variant_url and urlsplit(variant_url).hostname.lower().rstrip('.') != site:
            raise HTTPException(400, '子产品前端链接不属于目标商品站点。')
        if not variant_url:
            parts = urlsplit(url)
            # A selected SKU in the parent URL must not accidentally become
            # every child's browser target when no child-specific URL exists.
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'sku_id']
            variant_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
        amount = raw_variant.get('price', '')
        currency = _text(raw_variant.get('currency', ''), 3, '币种').upper()
        amount = price_storage._price(amount, allow_empty=True)
        if amount and raw_variant.get('price_verified') is not True:
            amount = ''
            warnings.append('有子产品价格未通过规格与币种核对，暂不保存该报价。')
        if amount and not re.fullmatch('[A-Z]{3}', currency):
            raise HTTPException(400, '子产品报价缺少币种。')
        if currency and not re.fullmatch('[A-Z]{3}', currency):
            raise HTTPException(400, '子产品报价币种不正确。')
        children.append({'id': vid, 'spec': spec, 'full_spec': full_spec, 'price': amount, 'currency': currency, 'url': variant_url})
    if not children:
        children = [{'id': '', 'spec': '规格尚未完整采集', 'full_spec': '', 'price': '', 'currency': '', 'url': url}]
        warnings.append('尚未取得可绑定具体子产品的完整规格，保留商品档案供补全。')
    product_key = _digest([platform.casefold(), store_id, site, external_id])
    for child in children:
        child['source_key'] = _digest([product_key, child['id']])
    return {'id': product_key, 'product_key': product_key, 'platform': platform, 'store_id': store_id,
            'store_name': name, 'site': site, 'source_id': external_id, 'url': url, 'title': title,
            'image_url': image_url, 'region': region, 'variants': children,
            'membership_verified': raw.get('membership_verified') is True,
            'limit_exceeded': limit_exceeded,
            'completeness': 'complete' if raw.get('completeness') == 'complete' else 'partial',
            'warnings': list(dict.fromkeys(warnings))[:30]}


def _prune() -> None:
    stamp = time.monotonic()
    for did in list(_drafts):
        if stamp - _drafts[did]['created'] >= DRAFT_TTL:
            del _drafts[did]


def _owned(owner: str, draft_id: str, user: dict) -> dict:
    _prune()
    draft = _drafts.get(draft_id) if isinstance(draft_id, str) else None
    if not draft or draft['owner'] != owner or draft['user_id'] != user['id'] or draft['db'] != str(backend.DB_PATH):
        raise HTTPException(404, '导入预览不存在、已过期或不属于当前会话，请重新采集。')
    return draft


def _mapped_product(conn, item: dict):
    matches = conn.execute('SELECT DISTINCT product_id FROM store_import_sources WHERE product_key=?', (item['product_key'],)).fetchall()
    if len(matches) > 1:
        raise HTTPException(409, '同一来源商品已关联多个商品，请先人工核对，不能自动合并。')
    if not matches:
        return None
    product = backend._product(conn, matches[0]['product_id'], include_deleted=True)
    if product['deleted']:
        raise HTTPException(409, '这个来源商品已入库后移入回收站，请先恢复或核对已有档案。')
    return product


def _view(draft: dict, user: dict) -> dict:
    result = {'id': draft['id'], 'expires_in': max(0, int(DRAFT_TTL - time.monotonic() + draft['created'])),
              'committed': draft['result'] is not None, 'items': []}
    with backend._connection() as conn:
        for item in draft['items']:
            value = copy.deepcopy(item)
            existing = _mapped_product(conn, item)
            value['existing_product'] = ({k: existing[k] for k in ('id', 'code', 'name', 'updated_at')} if existing else None)
            value['has_image'] = item['id'] in draft['images']
            value['can_import'] = value['has_image'] and item['membership_verified'] and not item['limit_exceeded']
            if not item['membership_verified']:
                value['warnings'].append('尚未确认商品属于目标店铺，不能入库。')
            if existing:
                try:
                    backend._may_edit(existing, user)
                except HTTPException as exc:
                    if exc.status_code != 403:
                        raise
                    value['can_import'] = False
                    value['warnings'].append('已有来源档案不在你的可编辑范围内。')
            result['items'].append(value)
    if draft['result'] is not None:
        result['result'] = copy.deepcopy(draft['result'])
    return result


def create_preview(owner: str, user: dict, items: list[dict], prepared_images: dict[str, dict] | None = None) -> dict:
    """Stage trusted collector output; no image/network operations occur here.

    ``prepared_images`` keys may be normalized product_key, source_id, or source
    URL. Values must be the collector's backend._prepare_image result.
    """
    _require_permission(user, 'products.import')
    if not isinstance(owner, str) or not owner or len(owner) > 256:
        raise HTTPException(400, '当前会话无法建立导入预览，请重新登录。')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_PRODUCTS:
        raise HTTPException(400, '每次请预览 1–20 个商品。')
    normalized = [_normalize(item) for item in items]
    if len({item['id'] for item in normalized}) != len(normalized):
        raise HTTPException(400, '本次采集包含重复商品，请先去重。')
    supplied, images, total = prepared_images or {}, {}, 0
    for item in normalized:
        image = next((supplied[key] for key in (item['id'], item['source_id'], item['url']) if key in supplied), None)
        if image is None:
            item['warnings'].append('主图尚未成功下载，不能入库，请重新采集。')
            continue
        if not isinstance(image, dict) or any(key not in image for key in ('data', 'thumb', 'features', 'sha256', 'name', 'mime')):
            raise HTTPException(400, '采集主图尚未经过图片验证。')
        if not isinstance(image['data'], bytes) or not isinstance(image['thumb'], bytes):
            raise HTTPException(400, '采集主图尚未经过图片验证。')
        if not image['data'] or len(image['data']) > backend.MAX_IMAGE_SIZE:
            raise HTTPException(400, '采集主图大小不正确。')
        images[item['id']] = copy.deepcopy(image)
        total += len(image['data']) + len(image['thumb'])
    with _lock:
        _prune()
        if len(_drafts) >= MAX_DRAFTS or total + sum(d['bytes'] for d in _drafts.values()) > MAX_DRAFT_BYTES:
            raise HTTPException(429, '待处理导入预览过多，请先完成或关闭已有预览。')
        did = uuid.uuid4().hex
        draft = {'id': did, 'owner': owner, 'user_id': user['id'], 'db': str(backend.DB_PATH),
                 'created': time.monotonic(), 'items': normalized, 'images': images, 'bytes': total, 'result': None}
        # Validate mapping conflicts before retaining memory.
        result = _view(draft, user)
        _drafts[did] = draft
        return result


def get_preview(owner: str, draft_id: str, user: dict) -> dict:
    _require_permission(user, 'products.import')
    with _lock:
        return _view(_owned(owner, draft_id, user), user)


def preview_image(owner: str, draft_id: str, item_id: str, user: dict) -> dict:
    _require_permission(user, 'products.import')
    with _lock:
        image = _owned(owner, draft_id, user)['images'].get(item_id)
        if image is None:
            raise HTTPException(404, '这张预览图不存在或尚未采集成功。')
        return {'data': image['thumb'], 'mime': 'image/jpeg'}


def close_preview(owner: str, draft_id: str, user: dict) -> dict:
    with _lock:
        draft = _owned(owner, draft_id, user)
        result = {'ok': True, 'committed': draft['result'] is not None}
        if draft['result'] is not None:
            result['result'] = copy.deepcopy(draft['result'])
        del _drafts[draft['id']]
        return result


def _append_listing(conn, product_id: str, item: dict, variant: dict, mark_listed: bool) -> str:
    lid = uuid.uuid4().hex
    currency = variant['currency'] or 'CNY'
    # These are public frontend IDs, not merchant SKU/SKC numbers. They remain
    # in source metadata until an operator supplies the actual merchant codes.
    listing = {'id': lid, 'platform': item['platform'], 'store': item['store_name'][:80],
               'product_code': '', 'product_number': '', 'status': 'listed' if mark_listed else 'pending',
               'price': '', 'currency': currency, 'link': variant['url'],
               'region': item['region'], 'variant_hint': variant['spec']}
    position = conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM product_listings WHERE product_id=?', (product_id,)).fetchone()[0]
    conn.execute('''INSERT INTO product_listings(id,product_id,platform,store,product_code,product_number,status,price,currency,link,position)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (lid, product_id, listing['platform'], listing['store'], '', '', listing['status'], '', currency, variant['url'], position))
    price_storage.sync_product_listing(conn, listing)
    if variant['price']:
        stamp = backend.now()
        target = price_storage._target(conn.execute('SELECT * FROM product_listings WHERE id=?', (lid,)).fetchone(), price_storage._tracking(conn, lid))
        evidence = '店铺前端采集；公开商品编号 ' + item['source_id'] + '；公开规格编号 ' + variant['id']
        conn.execute('''UPDATE price_tracking SET current_price=?,current_currency=?,last_success_at=?,last_checked_at=?,
            last_success_source_url=?,last_success_context=?,source='store_frontend',evidence=?,title=?,error='',updated_at=? WHERE listing_id=?''',
            (variant['price'], variant['currency'], stamp, stamp, variant['url'], price_storage._context(target), evidence, item['title'], stamp, lid))
        conn.execute('''INSERT INTO price_history(listing_id,checked_at,source_url,region,variant_hint,price,currency,source,evidence,error)
            VALUES(?,?,?,?,?,?,?,'store_frontend',?,'')''',
            (lid, stamp, variant['url'], item['region'], variant['spec'], variant['price'], variant['currency'], evidence))
    return lid


def _resolve_selection(conn, item, selection, user):
    existing = _mapped_product(conn, item)
    if existing:
        # An explicit conflicting destination cannot silently move an established
        # source record. Use the separate reviewed product merge action instead.
        if selection['action'] == 'merge' and selection.get('product_id') != existing['id']:
            raise HTTPException(409, '这个来源已有商品档案，请先使用人工合并功能，不能重新关联到另一商品。')
        backend._may_edit(existing, user)
        expected = selection.get('expected_updated_at')
        if not isinstance(expected, str) or expected != existing['updated_at']:
            raise HTTPException(409, '已有来源商品已变化，请刷新预览后再导入。')
        return existing
    if selection['action'] == 'merge':
        target = backend._product(conn, selection.get('product_id', ''))
        backend._may_edit(target, user)
        if selection.get('expected_updated_at') != target['updated_at']:
            raise HTTPException(409, '要合并的商品已变化，请刷新并重新选择。')
        return target
    return None


def commit_preview(owner: str, draft_id: str, user: dict, selections: list[dict], *, mark_listed: bool = False) -> dict:
    _require_permission(user, 'products.import')
    if type(mark_listed) is not bool:
        raise HTTPException(400, '人工确认已上架须为 true 或 false。')
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_PRODUCTS:
        raise HTTPException(400, '请选择这次要导入的商品。')
    if any(not isinstance(s, dict) or set(s) - {'item_id', 'action', 'product_id', 'expected_updated_at'} or s.get('action') not in {'new', 'merge', 'skip'} or not isinstance(s.get('item_id'), str) for s in selections):
        raise HTTPException(400, '导入选择格式不正确。')
    if len({s['item_id'] for s in selections}) != len(selections):
        raise HTTPException(400, '导入选择含重复商品。')
    written = []
    with _lock:
        draft = _owned(owner, draft_id, user)
        if draft['result'] is not None:
            return copy.deepcopy(draft['result'])
        lookup = {item['id']: item for item in draft['items']}
        if any(s['item_id'] not in lookup for s in selections):
            raise HTTPException(400, '所选商品不属于当前采集预览。')
        chosen = [(lookup[s['item_id']], s) for s in selections if s['action'] != 'skip']
        if not chosen:
            raise HTTPException(400, '请至少选择一个商品入库。')
        if any(item['id'] not in draft['images'] for item, _ in chosen):
            raise HTTPException(400, '有商品主图未采集成功，请取消选择或重新采集。')
        if any(not item['membership_verified'] for item, _ in chosen):
            raise HTTPException(400, '有商品尚未确认属于目标店铺，不能入库。')
        if any(item['limit_exceeded'] for item, _ in chosen):
            raise HTTPException(400, '所选商品的规格超过平台记录上限，请取消选择并拆分整理。')
        try:
            with backend._connection(write=True) as conn:
                # Validate every permission, source mapping and optimistic lock
                # before writing any business row or image.
                plans = [(item, selection, _resolve_selection(conn, item, selection, user)) for item, selection in chosen]
                if any(target is None for _, _, target in plans):
                    _require_permission(user, 'products.create')
                additions = {}
                for item, _, target in plans:
                    missing = 0
                    for variant in item['variants']:
                        mapping = conn.execute('SELECT * FROM store_import_sources WHERE source_key=?', (variant['source_key'],)).fetchone()
                        if mapping:
                            listing = conn.execute('SELECT product_id FROM product_listings WHERE id=?', (mapping['listing_id'],)).fetchone()
                            if not listing or listing['product_id'] != mapping['product_id']:
                                raise HTTPException(409, '已导入的规格已被删除或移动，请先核对已有商品。')
                        else:
                            missing += 1
                    if target:
                        key = target['id']
                        additions[key] = additions.get(key, 0) + missing
                for pid, amount in additions.items():
                    count = conn.execute('SELECT COUNT(*) FROM product_listings WHERE product_id=?', (pid,)).fetchone()[0]
                    if count + amount > MAX_VARIANTS:
                        raise HTTPException(400, '合并后单个商品平台规格将超过 100 条，请分开整理。')
                result = {'created': 0, 'merged': 0, 'new_listings': 0, 'existing_listings': 0, 'images': 0, 'items': []}
                touched = set()
                for item, _, target in plans:
                    image = draft['images'][item['id']]
                    if target is None:
                        info = backend._metadata({'name': item['title'][:160], 'owner': user['name'], 'status': 'pending', 'link': item['url'], 'listings': []}, user)
                        pid = backend._insert_product(conn, info, [image], user, None, written)
                        result['created'] += 1
                        result['images'] += 1
                    else:
                        pid = target['id']
                        result['merged'] += 1
                        duplicate = conn.execute('SELECT 1 FROM images WHERE product_id=? AND sha256=?', (pid, image['sha256'])).fetchone()
                        if not duplicate:
                            count = conn.execute('SELECT COUNT(*) FROM images WHERE product_id=?', (pid,)).fetchone()[0]
                            if count >= 30:
                                raise HTTPException(400, '合并后的商品图片将超过 30 张，请先整理图片。')
                            backend._save_image(conn, pid, image, written)
                            result['images'] += 1
                    changed = target is None or not duplicate
                    for variant in item['variants']:
                        if conn.execute('SELECT 1 FROM store_import_sources WHERE source_key=?', (variant['source_key'],)).fetchone():
                            result['existing_listings'] += 1
                            continue
                        lid = _append_listing(conn, pid, item, variant, mark_listed)
                        conn.execute('''INSERT INTO store_import_sources(source_key,product_key,platform,store_id,store_name,site,
                            source_product_id,source_variant_id,product_id,listing_id,source_url,image_url,captured_title,captured_spec,
                            captured_price,captured_currency,warnings,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (variant['source_key'], item['product_key'], item['platform'], item['store_id'], item['store_name'], item['site'],
                             item['source_id'], variant['id'], pid, lid, variant['url'], item['image_url'], item['title'], variant['full_spec'],
                             variant['price'], variant['currency'], json.dumps(item['warnings'], ensure_ascii=False), backend.now()))
                        result['new_listings'] += 1
                        changed = True
                    if changed:
                        conn.execute('UPDATE products SET listings_initialized=1,updated_at=? WHERE id=?', (backend.now(), pid))
                        touched.add(pid)
                    result['items'].append({'item_id': item['id'], 'product_id': pid})
                if touched:
                    backend._audit(conn, user, 'store_import', f"店铺采集入库：新增 {result['created']} 款，关联 {result['merged']} 款，新增 {result['new_listings']} 条规格")
                draft['result'] = result
            return copy.deepcopy(result)
        except Exception:
            draft['result'] = None
            backend._cleanup(written)
            raise
