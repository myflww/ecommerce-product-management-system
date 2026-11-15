"""Store capture import checks use a disposable catalog and no live requests."""
import copy
import io
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import permissions
import price_storage
import store_import_storage as storage


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    backend.init_storage(tmp_path)
    storage.init_storage()
    storage._drafts.clear()
    monkeypatch.setattr(backend, '_extract_semantic', lambda _: None)
    import exchange_rates
    monkeypatch.setattr(exchange_rates, 'refresh_rates', lambda *a, **k: None)
    users = [{'id': 'admin', 'name': '管理成员', 'role': 'admin'}, {'id': 'member', 'name': '录入成员', 'role': 'operator'},
             {'id': 'other', 'name': '另一成员', 'role': 'operator'}]
    with backend._connection(write=True) as conn:
        for u in users:
            conn.execute('INSERT INTO users VALUES(?,?,?,?,?)', (u['id'], u['name'], 'test-only-no-login', u['role'], backend.now()))
    permissions.init_storage()
    with backend._connection() as conn:
        users = [permissions.public_user(conn, conn.execute('SELECT * FROM users WHERE id=?', (u['id'],)).fetchone()) for u in users]
    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'green').save(picture, 'PNG')
    image = backend._prepare_image(picture.getvalue(), 'fixture.png')
    return users, image


def capture(pid='700000000000005', store='700000000000003', variants=None, **fields):
    return {'platform': 'Temu', 'store_id': store, 'store_name': 'Fixture shop ' + store[-3:], 'product_id': pid,
            'name': '店铺样品 ' + pid, 'url': 'https://www.temu.com/g-' + pid + '.html',
            'image': 'https://img.example.com/' + pid + '.png', 'membership_verified': True,
            'completeness': 'complete', 'warnings': [], 'variants': variants if variants is not None else [
                {'public_variant_id': 'sku-red', 'spec': '红色 2件装', 'price': '10.54', 'currency': 'USD', 'price_verified': True},
                {'public_variant_id': 'sku-blue', 'spec': '蓝色 4件装', 'price': '17.21', 'currency': 'USD', 'price_verified': True}], **fields}


def preview(catalog, items=None, user=None, with_images=True):
    users, image = catalog
    items = items or [capture()]
    user = user or users[0]
    images = {item['product_id']: image for item in items} if with_images else {}
    return storage.create_preview('owner', user, items, images)


def choose(preview, action='new', product=None):
    result = []
    for item in preview['items']:
        target = product or item['existing_product']
        value = {'item_id': item['id'], 'action': action}
        if target:
            value.update(product_id=target['id'], expected_updated_at=target['updated_at'])
        result.append(value)
    return result


def commit(catalog, staged, selections=None, user=None, **fields):
    return storage.commit_preview('owner', staged['id'], user or catalog[0][0], selections or choose(staged), **fields)


def rows(table):
    with backend._connection() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')]


def product(pid):
    with backend._connection() as conn:
        return backend._serialize_product(conn, backend._product(conn, pid))


def new_target(catalog, user=None):
    user = user or catalog[0][0]
    info = backend._metadata({'name': '已有手工商品', 'specs': '手工保留规格', 'notes': '手工保留备注', 'category': '手工分类',
        'listings': [{'platform': 'Amazon', 'store': 'Original store', 'product_code': 'MERCHANT-SKU', 'product_number': 'ASIN-1',
                      'price': '30', 'currency': 'USD', 'status': 'archived', 'link': 'https://amazon.example.com/item', 'variant_hint': '手工规格'}]}, user)
    return backend._create_product_sync(info, [catalog[1]], user)


def test_preview_keeps_business_catalog_untouched_and_serves_local_image(catalog):
    staged = preview(catalog)
    assert staged['items'][0]['can_import']
    assert staged['items'][0]['variants'][0]['price'] == '10.54'
    assert rows('products') == rows('images') == rows('product_listings') == rows('price_history') == []
    image = storage.preview_image('owner', staged['id'], staged['items'][0]['id'], catalog[0][0])
    assert image['mime'] == 'image/jpeg' and image['data'].startswith(b'\xff\xd8')
    assert 'data' not in staged['items'][0]


def test_import_keeps_store_variant_prices_and_base_prices_separate(catalog):
    staged = preview(catalog)
    result = commit(catalog, staged)
    assert (result['created'], result['new_listings'], result['images']) == (1, 2, 1)
    target = product(result['items'][0]['product_id'])
    assert len(target['images']) == 1
    assert target['link'] == capture()['url']
    assert {l['platform'] for l in target['listings']} == {'Temu'}
    assert {l['store'] for l in target['listings']} == {capture()['store_name']}
    assert all(l['price'] == l['product_code'] == l['product_number'] == '' and l['status'] == 'pending' for l in target['listings'])
    observed = price_storage.get_product_tracking(target['id'], catalog[0][0])['listings']
    assert {l['variant_hint']: l['current_price'] for l in observed} == {'红色 2件装': '10.54', '蓝色 4件装': '17.21'}
    assert all(l['current_currency'] == 'USD' and l['ratio_percent'] is None for l in observed)
    sources = rows('store_import_sources')
    assert {s['source_variant_id'] for s in sources} == {'sku-red', 'sku-blue'}
    assert all(s['source_product_id'] == capture()['product_id'] and s['store_id'] == capture()['store_id'] for s in sources)
    assert {h['source'] for h in rows('price_history')} == {'store_frontend'}


def test_explicit_confirm_listed_only_applies_to_new_rows(catalog):
    staged = preview(catalog)
    result = commit(catalog, staged, mark_listed=True)
    pid = result['items'][0]['product_id']
    assert {l['status'] for l in product(pid)['listings']} == {'listed'}
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE product_listings SET status='archived' WHERE product_id=?", (pid,))
    staged = preview(catalog)
    commit(catalog, staged, mark_listed=True)
    assert {l['status'] for l in product(pid)['listings']} == {'archived'}


def test_retry_same_commit_is_idempotent(catalog):
    staged = preview(catalog)
    first = commit(catalog, staged)
    before = {t: rows(t) for t in ('products', 'images', 'product_listings', 'price_tracking', 'price_history', 'activity')}
    assert commit(catalog, staged) == first
    assert before == {t: rows(t) for t in before}


def test_reimport_exact_source_preserves_manual_fields_quotes_and_status(catalog):
    first = commit(catalog, preview(catalog))
    pid = first['items'][0]['product_id']
    with backend._connection(write=True) as conn:
        conn.execute("UPDATE products SET name='人工名称',notes='人工备注',updated_at=? WHERE id=?", (backend.now(), pid))
        conn.execute("UPDATE product_listings SET price='74.62',currency='CNY',status='archived' WHERE product_id=?", (pid,))
    listing = price_storage.get_product_tracking(pid, catalog[0][0])['listings'][0]
    price_storage.save_manual_price(listing['id'], {'price': '9.99', 'currency': 'USD', 'expected_context': listing['context_token']}, catalog[0][0])
    before = {t: rows(t) for t in ('products', 'images', 'product_listings', 'price_tracking', 'price_history', 'activity')}
    changed = capture(name='采集改名', variants=[{'public_variant_id': 'sku-red', 'spec': '变更标签', 'price': '50', 'currency': 'EUR', 'price_verified': True}])
    staged = preview(catalog, [changed])
    assert staged['items'][0]['existing_product']['id'] == pid
    again = commit(catalog, staged)
    assert again['created'] == again['new_listings'] == again['images'] == 0
    assert before == {t: rows(t) for t in before}


def test_new_variant_reuses_external_product_without_replacing_existing_skus(catalog):
    first = commit(catalog, preview(catalog))
    pid = first['items'][0]['product_id']
    added = capture(variants=capture()['variants'] + [{'public_variant_id': 'sku-green', 'spec': '绿色 1件', 'price': '8.99', 'currency': 'USD', 'price_verified': True}])
    staged = preview(catalog, [added])
    result = commit(catalog, staged)
    assert result['created'] == 0 and result['new_listings'] == 1 and result['existing_listings'] == 2
    assert len(rows('products')) == 1 and len(product(pid)['listings']) == 3


def test_same_goods_id_in_different_stores_is_not_automatically_merged(catalog):
    staged = preview(catalog, [capture(), capture(store='700000000000004')])
    result = commit(catalog, staged)
    assert result['created'] == 2 and len(rows('products')) == 2
    assert len({s['product_key'] for s in rows('store_import_sources')}) == 2


def test_selected_merge_appends_platforms_without_touching_target_metadata_or_quotes(catalog):
    target = new_target(catalog)
    original_listing = target['listings'][0]
    staged = preview(catalog)
    result = commit(catalog, staged, choose(staged, 'merge', target))
    assert result['created'] == 0 and result['merged'] == 1 and result['new_listings'] == 2
    updated = product(target['id'])
    for key in ('name', 'specs', 'notes', 'category', 'owner', 'link'):
        assert updated[key] == target[key]
    assert len(updated['images']) == 1
    assert next(l for l in updated['listings'] if l['id'] == original_listing['id']) == original_listing
    assert {l['platform'] for l in updated['listings']} == {'Amazon', 'Temu'}


def test_cannot_retarget_known_source_to_another_product(catalog):
    commit(catalog, preview(catalog))
    other = new_target(catalog)
    staged = preview(catalog)
    with pytest.raises(HTTPException) as exc:
        commit(catalog, staged, choose(staged, 'merge', other))
    assert exc.value.status_code == 409
    assert len(rows('products')) == 2 and len(rows('store_import_sources')) == 2


def test_permission_failure_rolls_back_whole_batch(catalog):
    target = new_target(catalog, catalog[0][2])
    staged = preview(catalog, [capture(), capture(pid='700000000000006')], catalog[0][1])
    selections = choose(staged)
    selections[1].update(action='merge', product_id=target['id'], expected_updated_at=target['updated_at'])
    before = {t: rows(t) for t in ('products', 'images', 'product_listings', 'activity', 'counters')}
    with pytest.raises(HTTPException) as exc:
        commit(catalog, staged, selections, user=catalog[0][1])
    assert exc.value.status_code == 403
    assert before == {t: rows(t) for t in before}
    assert not rows('store_import_sources')


def test_stale_merge_target_rolls_back_batch(catalog):
    target = new_target(catalog)
    staged = preview(catalog)
    with backend._connection(write=True) as conn:
        conn.execute('UPDATE products SET updated_at=? WHERE id=?', (backend.now(), target['id']))
    with pytest.raises(HTTPException) as exc:
        commit(catalog, staged, choose(staged, 'merge', target))
    assert exc.value.status_code == 409 and not rows('store_import_sources')


def test_later_file_failure_cleans_written_files_and_rolls_back_database(catalog, monkeypatch):
    staged = preview(catalog, [capture(), capture(pid='700000000000006')])
    original = backend._save_image
    calls = []
    def fail_second(conn, product_id, image, written):
        calls.append(product_id)
        original(conn, product_id, image, written)
        if len(calls) == 2:
            raise OSError('fixture disk failure')
    monkeypatch.setattr(backend, '_save_image', fail_second)
    with pytest.raises(OSError):
        commit(catalog, staged)
    assert not rows('products') and not rows('store_import_sources') and not rows('price_history')
    assert list((backend.DATA_DIR / 'images').iterdir()) == list((backend.DATA_DIR / 'thumbnails').iterdir()) == []
    assert not storage.get_preview('owner', staged['id'], catalog[0][0])['committed']


def test_aggregate_or_unverified_price_is_not_saved_to_variant(catalog):
    staged = preview(catalog, [capture(variants=[{'public_variant_id': 'sku', 'spec': '红色', 'price': '10.00', 'currency': 'USD', 'price_verified': False}])])
    assert staged['items'][0]['variants'][0]['price'] == ''
    result = commit(catalog, staged)
    assert len(product(result['items'][0]['product_id'])['listings']) == 1
    assert rows('price_history') == [] and rows('price_tracking')[0]['current_price'] == ''


def test_specs_without_public_ids_keep_incomplete_product_without_fake_sku_price(catalog):
    staged = preview(catalog, [capture(variants=[{'spec': '未绑定规格', 'price': '10', 'currency': 'USD', 'price_verified': True}])])
    assert staged['items'][0]['warnings']
    result = commit(catalog, staged)
    assert len(product(result['items'][0]['product_id'])['listings']) == 1
    assert rows('store_import_sources')[0]['source_variant_id'] == ''
    assert rows('price_history') == []


def test_unverified_store_membership_and_missing_images_are_preview_only(catalog):
    staged = preview(catalog, [capture(membership_verified=False)])
    assert not staged['items'][0]['can_import']
    with pytest.raises(HTTPException):
        commit(catalog, staged)
    staged = preview(catalog, with_images=False)
    assert not staged['items'][0]['can_import']
    with pytest.raises(HTTPException):
        commit(catalog, staged)
    assert not rows('products')


def test_draft_ownership_expiry_and_close(catalog, monkeypatch):
    staged = preview(catalog)
    for owner, user in [('different-session', catalog[0][0]), ('owner', catalog[0][1])]:
        with pytest.raises(HTTPException) as exc:
            storage.get_preview(owner, staged['id'], user)
        assert exc.value.status_code == 404
    assert storage.close_preview('owner', staged['id'], catalog[0][0]) == {'ok': True, 'committed': False}
    staged = preview(catalog)
    monkeypatch.setattr(storage.time, 'monotonic', lambda: storage._drafts[staged['id']]['created'] + storage.DRAFT_TTL + 1)
    with pytest.raises(HTTPException) as exc:
        storage.get_preview('owner', staged['id'], catalog[0][0])
    assert exc.value.status_code == 404


def test_known_source_deleted_or_listing_removed_requires_manual_resolution(catalog):
    result = commit(catalog, preview(catalog))
    staged = preview(catalog)
    with backend._connection(write=True) as conn:
        conn.execute('DELETE FROM product_listings WHERE product_id=?', (result['items'][0]['product_id'],))
    with pytest.raises(HTTPException) as exc:
        commit(catalog, staged)
    assert exc.value.status_code == 409
    with backend._connection(write=True) as conn:
        conn.execute('UPDATE products SET deleted=1 WHERE id=?', (result['items'][0]['product_id'],))
    with pytest.raises(HTTPException) as exc:
        preview(catalog)
    assert exc.value.status_code == 409


@pytest.mark.parametrize('fields', [{'product_id': 700000000000005}, {'store_id': 1234}, {'url': 'http://127.0.0.1/p'},
    {'variants': [{'public_variant_id': 'one', 'spec': 'spec', 'price': '1', 'currency': '', 'price_verified': True}]},
    {'variants': [{'public_variant_id': 'same'}, {'public_variant_id': 'same'}]}])
def test_invalid_capture_identifiers_and_prices_are_rejected(catalog, fields):
    with pytest.raises(HTTPException):
        preview(catalog, [capture(**fields)])
    assert not rows('products')


def test_multiple_sources_can_explicitly_merge_into_one_target_atomically(catalog):
    target = new_target(catalog)
    staged = preview(catalog, [capture(), capture(store='700000000000004')])
    result = commit(catalog, staged, choose(staged, 'merge', target))
    assert result['created'] == 0 and len(product(target['id'])['listings']) == 5
    assert len({s['product_key'] for s in rows('store_import_sources')}) == 2


def test_knowledge_tracking_triggers_accept_atomic_store_import(catalog):
    import knowledge_store
    knowledge_store.init_storage()
    result = commit(catalog, preview(catalog))
    pid = result['items'][0]['product_id']
    assert knowledge_store.retrieve_products('店铺样品')[0]['id'] == pid
    assert len(rows('price_history')) == 2


def test_revoked_import_permission_blocks_commit_and_image_access(catalog):
    staged = preview(catalog, user=catalog[0][1])
    user = {**catalog[0][1], 'permissions': [p for p in catalog[0][1]['permissions'] if p != 'products.import']}
    for call in (lambda: commit(catalog, staged, user=user),
                 lambda: storage.get_preview('owner', staged['id'], user),
                 lambda: storage.preview_image('owner', staged['id'], staged['items'][0]['id'], user)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 403
    assert not rows('products')


def test_create_permission_not_needed_for_authorized_merge_but_new_product_is_blocked(catalog):
    user = {**catalog[0][1], 'permissions': [p for p in catalog[0][1]['permissions'] if p != 'products.create']}
    target = new_target(catalog, user)
    staged = preview(catalog, user=user)
    with pytest.raises(HTTPException) as exc:
        commit(catalog, staged, user=user)
    assert exc.value.status_code == 403 and not rows('store_import_sources')
    result = commit(catalog, staged, choose(staged, 'merge', target), user=user)
    assert result['created'] == 0 and result['merged'] == 1


def test_long_source_labels_keep_full_metadata_but_fit_existing_editor(catalog):
    name, spec = '店铺' * 70, '完整规格' * 100
    staged = preview(catalog, [capture(store_name=name, variants=[{'public_variant_id': 'long', 'spec': spec,
        'price': '1', 'currency': 'USD', 'price_verified': True}])])
    assert staged['items'][0]['can_import']
    result = commit(catalog, staged)
    listing = product(result['items'][0]['product_id'])['listings'][0]
    assert listing['store'] == name[:80] and listing['variant_hint'] == spec[:200]
    source = rows('store_import_sources')[0]
    assert source['store_name'] == name and source['captured_spec'] == spec


def test_over_editor_variant_limit_is_preview_only_without_blocking_other_items(catalog):
    too_many = capture(variants=[{'public_variant_id': str(i), 'spec': str(i)} for i in range(101)])
    staged = preview(catalog, [too_many, capture(pid='700000000000006')])
    assert not staged['items'][0]['can_import'] and staged['items'][1]['can_import']
    with pytest.raises(HTTPException):
        commit(catalog, staged)
    selections = choose(staged)
    selections[0]['action'] = 'skip'
    result = commit(catalog, staged, selections)
    assert result['created'] == 1


def test_variant_specific_frontend_links_survive_import_and_quote_context(catalog):
    item = capture()
    for variant in item['variants']:
        variant['url'] = item['url'] + '?sku_id=' + variant['public_variant_id']
    result = commit(catalog, preview(catalog, [item]))
    pid = result['items'][0]['product_id']
    assert {l['link'] for l in product(pid)['listings']} == {v['url'] for v in item['variants']}
    assert {r['source_url'] for r in rows('price_history')} == {v['url'] for v in item['variants']}
    assert {r['source_url'] for r in rows('store_import_sources')} == {v['url'] for v in item['variants']}


def test_parent_selected_sku_is_not_copied_as_other_variant_browser_target(catalog):
    item = capture()
    item['url'] += '?sku_id=unrelated-selected-sku'
    result = commit(catalog, preview(catalog, [item]))
    pid = result['items'][0]['product_id']
    assert product(pid)['link'] == item['url']
    assert all('sku_id' not in l['link'] for l in product(pid)['listings'])


def test_close_preview_reports_already_committed_result_and_releases_all_image_memory(catalog):
    staged = preview(catalog)
    result = commit(catalog, staged)
    released = storage.close_preview('owner', staged['id'], catalog[0][0])
    assert released == {'ok': True, 'committed': True, 'result': result}
    assert staged['id'] not in storage._drafts
    assert len(rows('products')) == 1 and len(rows('price_history')) == 2
