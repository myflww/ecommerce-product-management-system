"""Import bounded public-page evidence exported by the optional browser helper.

This is a file handoff, not a browser connection. The file is never executed or
retained. Product ownership may be explicitly attested by the operator; this is
recorded as a manual confirmation, never as platform-authenticated evidence.
"""
from __future__ import annotations

import copy
import json
import time
from decimal import Decimal
from urllib.parse import urlsplit

from fastapi import HTTPException, UploadFile

import backend
import permissions
import store_import_engine as engine
import store_import_parser as parser
import store_import_storage as storage

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PAGES = 20
MAX_PRODUCTS = 20
MAX_IMAGE_BATCH_SECONDS = 90
MANUAL_STORE_WARNING = '店铺归属由操作人手动确认，未经平台接口验证。'
FILE_WARNING = '来自浏览器手动采集文件；只保留页面已暴露的信息，请核对规格与报价。'

# Decoded JSON is deliberately public-field-only. A complete hydration object
# can contain account/session data and is not an accepted capture file.
PUBLIC_KEYS = frozenset('''products goods_id goodsId sku_id skuId variant_id variantId
mall_id mallId store_id goods_name goodsName name title mallName mall_name
storeName store_name image image_url imageUrl thumb_url thumbUrl hdThumbUrl
hd_thumb_url url goodsUrl goods_url mallUrl mall_url contentUrl sku price
priceCurrency currency currencyCode currency_code @type @context skuCount
sku_count variantCount variant_count skuList sku_list hasVariant offers specs
specList spec_list skuSpecs sku_specs mall mallInfo mall_info seller store
spec_key specKey spec_value specValue value spec variant_name variantName skuName
sku_name color size pattern material lowPrice highPrice eligibleCustomerType
eligibleQuantity priceUnit price_unit amountUnit amount_unit priceType struck'''.split())
SNAPSHOT_KEYS = frozenset('''url title store_name product_title product_image seller
links scripts variants text json_ld recommendation_boundary_seen'''.split())
LINK_KEYS = frozenset('url name image mall_id context section kind related'.split())
SELLER_KEYS = frozenset('scope mall_id mallId store_id name mallName mall_name storeName store_name url mallUrl mall_url'.split())
VARIANT_KEYS = PUBLIC_KEYS | {'scope', 'related'}


def _bad(message='采集文件格式无效，请使用当前版本的浏览器采集插件重新导出。'):
    raise HTTPException(400, message)


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - allowed or not set(required) <= set(value):
        _bad()


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        _bad()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            _bad('采集文件含重复字段，请重新导出。')
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_float=Decimal,
                          parse_constant=lambda _: _bad())
    except (ValueError, UnicodeError, RecursionError):
        _bad()


def _public_tree(value, allowed=PUBLIC_KEYS):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 25000 or depth > 35:
            _bad('页面信息过大，请分批采集并重新导出。')
        if isinstance(item, dict):
            if len(item) > 150 or set(item) - allowed:
                _bad('采集文件包含不支持的字段；请勿导入完整网页或账号数据。')
            if any(isinstance(item.get(key), list) and len(item[key]) > parser.MAX_VARIANTS
                   for key in ('skuList', 'sku_list', 'hasVariant')):
                _bad('单页规格超过 200 条，请拆批核对，不能静默截断。')
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > 2000:
                _bad('页面信息过大，请分批采集并重新导出。')
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            _text(item, 4000)
        elif item is not None and not isinstance(item, (bool, int, Decimal)):
            _bad()


def _validate_snapshot(snapshot):
    _object(snapshot, SNAPSHOT_KEYS, {'url'})
    for name, limit in {'url': 4000, 'title': 500, 'store_name': 160,
                        'product_title': 500, 'product_image': 4000, 'text': 2000}.items():
        if name in snapshot:
            _text(snapshot[name], limit)
    if 'recommendation_boundary_seen' in snapshot and type(snapshot['recommendation_boundary_seen']) is not bool:
        _bad()
    if 'seller' in snapshot:
        _object(snapshot['seller'], SELLER_KEYS)
        _public_tree(snapshot['seller'], SELLER_KEYS)
    links = snapshot.get('links', [])
    if not isinstance(links, list) or len(links) > 400:
        _bad('单页商品链接过多，请缩小采集范围。')
    for link in links:
        _object(link, LINK_KEYS)
        _public_tree(link, LINK_KEYS)
    variants = snapshot.get('variants', [])
    if not isinstance(variants, list) or len(variants) > parser.MAX_VARIANTS:
        _bad('单页规格过多，请拆批核对。')
    for variant in variants:
        _object(variant, VARIANT_KEYS)
        _public_tree(variant, VARIANT_KEYS)
    scripts, documents = snapshot.get('scripts', []), snapshot.get('json_ld', [])
    if not isinstance(scripts, list) or len(scripts) > parser.MAX_SCRIPTS or not isinstance(documents, list) or len(documents) > 12:
        _bad()
    raw_documents = []
    for script in scripts:
        _object(script, {'text', 'type', 'id'}, {'text'})
        for key in ('type', 'id'):
            if key in script:
                _text(script[key], 200)
        raw_documents.append(script['text'])
    raw_documents.extend(documents)
    total = 0
    for raw in raw_documents:
        _text(raw, parser.MAX_JSON_CHARS)
        total += len(raw)
        if total > parser.MAX_JSON_CHARS:
            _bad('单页结构信息过大，请重新采集。')
        _public_tree(_json(raw))


def read_file(raw: bytes):
    """Validate the transport and evidence before any image request or draft."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413 if raw else 400, '采集文件须为 1 字节至 8 MB，请分批导出。')
    try:
        data = _json(raw.decode('utf-8-sig'))
    except UnicodeError:
        _bad('请导入插件导出的 UTF-8 JSON 文件。')
    _object(data, {'format', 'version', 'pages'}, {'format', 'version', 'pages'})
    if data['format'] != 'midoo-page-capture' or type(data['version']) is not int or data['version'] != 1:
        _bad('不支持这个采集文件版本，请更新插件后重新导出。')
    pages = data['pages']
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        _bad('每次请导入 1–20 个页面的采集文件。')
    for page in pages:
        _object(page, {'snapshot', 'store_url', 'store_name', 'confirmed_store', 'captured_at'},
                {'snapshot', 'store_url', 'confirmed_store'})
        _text(page['store_url'], 4000)
        for name, limit in (('store_name', 160), ('captured_at', 80)):
            if name in page:
                _text(page[name], limit)
        if page['confirmed_store'] is not True:
            _bad('请先在插件里人工确认商品所属店铺，再导出采集文件。')
        _validate_snapshot(page['snapshot'])
    return pages


def _product(snapshot, store, name):
    item = parser.parse_product_snapshot(snapshot, store['store_id'], store_name=name)
    # Inspect explicit ownership even on incomplete product roots that the
    # detail parser cannot use for a title or SKU list.
    for doc in parser._documents(snapshot):
        for node in parser._walk(doc):
            if parser._goods_id(node) == item['product_id']:
                owner = parser._mall_id(node)
                if owner and owner != store['store_id']:
                    raise ValueError('商品页面的店铺编号与人工确认的店铺不符。')
    if not item['name'] or not item['image']:
        raise ValueError('未读到商品名称或主图，请等待详情页加载完成后重新采集。')
    if not item['membership_verified']:
        item['warnings'].append(MANUAL_STORE_WARNING)
    item['membership_verified'] = True
    item['warnings'].append(FILE_WARNING)
    item['importable'] = True
    return item


def _store(snapshot, store, name):
    # Only rendered store cards are accepted for a store-page file. JSON may
    # hold unrelated recommendations; it is not used for this basic import.
    visible = {**snapshot, 'scripts': [], 'json_ld': []}
    found = parser.parse_store_snapshot(visible, store['store_id'])
    if len(found['items']) == MAX_PRODUCTS:
        extra = parser.parse_store_snapshot(visible, store['store_id'],
                                            seen_ids=[i['product_id'] for i in found['items']], limit=1)
        if extra['items']:
            _bad('本页超过 20 款商品，请在插件中分批选择，不能静默截断导入。')
    items = []
    for candidate in found['items']:
        if candidate['source'] != 'visible_store_link' or not candidate['name'] or not candidate['image']:
            continue
        items.append({'platform': 'Temu', 'store_id': store['store_id'],
                      'store_name': name or found['store_name'], 'product_id': candidate['product_id'],
                      'url': candidate['url'], 'name': candidate['name'], 'image': candidate['image'],
                      'variants': [], 'membership_verified': True, 'completeness': 'partial',
                      'warnings': [MANUAL_STORE_WARNING, FILE_WARNING,
                                   '店铺页仅建立商品档案；请另采集详情页补充各 SKU 与价格。']})
    if not items:
        raise ValueError('店铺页尚未读到有名称与主图的商品卡片，请先滚动加载或打开商品详情页。')
    return items


def _merge(old, new):
    # A detail page supersedes a card; distinct stable SKU observations merge.
    base = copy.deepcopy(new if new['variants'] or not old['variants'] else old)
    base['warnings'] = list(dict.fromkeys(old['warnings'] + new['warnings']))
    merged = {}
    for variant in old['variants'] + new['variants']:
        key = variant.get('public_variant_id')
        if not key:
            continue
        if key not in merged:
            merged[key] = copy.deepcopy(variant)
            continue
        previous = merged[key]
        conflict = ('variant_price_conflict' in previous.get('warnings', []) or
                    'variant_price_conflict' in variant.get('warnings', []) or
                    (previous.get('price') and variant.get('price') and
                     (previous['price'], previous['currency']) != (variant['price'], variant['currency'])))
        if conflict:
            previous.update(price=None, currency='', price_verified=False)
            previous['warnings'] = ['variant_price_conflict']
            base['warnings'].append('variant_price_conflict')
        elif not previous.get('price') and variant.get('price'):
            merged[key] = copy.deepcopy(variant)
    base['variants'] = list(merged.values())
    if len(base['variants']) > parser.MAX_VARIANTS:
        _bad('同一商品的合并规格超过 200 条，请拆批核对。')
    # Multiple observations do not prove that every SKU was exposed.
    base['completeness'] = 'partial'
    base['warnings'] = list(dict.fromkeys(base['warnings'] + ['variant_list_unverified']))
    return base


def collect_items(pages):
    products, identities = {}, {}
    try:
        for page in pages:
            store = parser.canonical_store_url(page['store_url'])
            snapshot = page['snapshot']
            if parser._BLOCKED.search(snapshot.get('title', '') + ' ' + snapshot.get('text', '')):
                raise ValueError('页面仍要求登录或验证，请在平时的浏览器里完成后重新采集。')
            name = parser._str(page.get('store_name', ''), 160)
            if parser.canonical_product_url(snapshot['url']):
                items = [_product(snapshot, store, name)]
            else:
                items = _store(snapshot, store, name)
            for item in items:
                # One file cannot silently assign the same public product to
                # two different sellers, even with a manual checkbox.
                pid = item['product_id']
                if pid in identities and identities[pid] != store['store_id']:
                    raise ValueError('同一商品被标记为不同店铺，请先核对采集文件。')
                identities[pid] = store['store_id']
                key = (store['store_id'], urlsplit(item['url']).hostname, pid)
                products[key] = _merge(products[key], item) if key in products else item
                if len(products) > MAX_PRODUCTS:
                    _bad('这批超过 20 款商品，请在插件中拆成多批导出。')
    except ValueError as exc:
        # Parser errors contain only static explanatory messages, no webpage.
        _bad(str(exc).replace('专用浏览器', '平时的浏览器'))
    return list(products.values())


def create_preview(owner: str, user: dict, raw: bytes):
    permissions.require(user, 'products.import')
    pages = read_file(raw)
    items = collect_items(pages)
    images, failures = {}, 0
    # Each downloader has its own 25-second network deadline. Stop starting
    # new downloads after 90 seconds; a final in-flight download can extend the
    # batch by at most that per-image deadline. Nothing runs in the background.
    image_deadline = time.monotonic() + MAX_IMAGE_BATCH_SECONDS
    for item in items:
        if time.monotonic() >= image_deadline:
            failures += 1
            item['warnings'].extend(['main_image_download_failed',
                                     '本批主图读取时间已用尽，请分小批重新导入未完成的商品。'])
            continue
        try:
            images[item['url']] = engine._image(item['image'])
        except (ValueError, OSError, HTTPException):
            failures += 1
            item['warnings'].append('main_image_download_failed')
    result = storage.create_preview(owner, user, items, images)
    result['capture_summary'] = {'pages': len(pages), 'products': len(items),
                                 'partial': sum(i['completeness'] != 'complete' for i in items),
                                 'image_failures': failures, 'source': 'browser_file'}
    return result


def upload_preview(owner: str, user: dict, file: UploadFile):
    # Read one extra byte, never read an unbounded file into memory.
    try:
        raw = file.file.read(MAX_FILE_BYTES + 1)
        return create_preview(owner, user, raw)
    finally:
        file.file.close()
