"""The extension's public projection must never rehabilitate rejected quotes."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

import page_capture_import
import store_import_parser as parser

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
GOODS, STORE = '700000000000005', '700000000000003'
URL = f'https://www.temu.com/candle-g-{GOODS}.html'


def project(document):
    if not NODE:
        pytest.skip('Node is required to test the shipped extension projection')
    script = """
const fs=require('node:fs');
const core=require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(core.projectDocuments([JSON.stringify(input.document)],input.url)));
"""
    result = subprocess.run([NODE, '-e', script, str(ROOT / 'browser-extension/capture-core.js')],
                            input=json.dumps({'document': document, 'url': URL}),
                            capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
    return json.loads(result.stdout)


def product(variant):
    return {'goods_id': GOODS, 'mall_id': STORE, 'goods_name': 'Fixture candle',
            'image': 'https://img.kwcdn.com/candle.jpg', 'skuCount': 1, 'skuList': [variant]}


def parse(scripts):
    return parser.parse_product_snapshot({'url': URL, 'scripts': scripts}, STORE)['variants']


INVALID_VARIANTS = [
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'url': URL + '?skuId=999'},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'url': URL + '?variant_id=999'},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'url': URL + '?variantId=999'},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'url': URL + '?skuId=201&skuId=999'},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'currencyCode': 123},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'sku': {'code': 'invalid'}},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'sku': 'A  B'},
    {'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD', 'sku': 'A' * 201},
    {'sku_id': '201', 'spec': 'Red', 'offers': {'@type': ['Offer', 'AggregateOffer'],
                                              'price': '10', 'priceCurrency': 'USD'}},
    {'sku_id': '201', 'spec': 'Red', 'offers': {'skuId': '999', 'price': '10', 'priceCurrency': 'USD'}},
]


@pytest.mark.parametrize('variant', INVALID_VARIANTS)
def test_public_projection_preserves_negative_price_evidence(variant):
    document = {'product': product(copy.deepcopy(variant))}
    raw = parse([{'text': json.dumps(document)}])
    assert raw and not raw[0]['price_verified'], 'fixture must be rejected by the existing parser'
    projected = project(document)
    observed = parse(projected['scripts'])
    assert observed and not observed[0]['price_verified']
    assert observed[0]['price'] is None


def test_valid_independent_variant_still_survives_and_passes_file_schema():
    document = {'product': product({'sku_id': '201', 'spec': 'Red / 2',
                                    'price': '10.54', 'currency': 'USD'})}
    projected = project(document)
    snapshot = {'url': URL, 'scripts': projected['scripts']}
    envelope = {'format': 'midoo-page-capture', 'version': 1, 'pages': [{
        'snapshot': snapshot, 'store_url': 'https://www.temu.com/mall.html?mall_id=' + STORE,
        'confirmed_store': True}]}
    pages = page_capture_import.read_file(json.dumps(envelope).encode())
    items = page_capture_import.collect_items(pages)
    assert items[0]['variants'][0]['price'] == '10.54'
    assert items[0]['variants'][0]['currency'] == 'USD'
    assert items[0]['variants'][0]['price_verified']


@pytest.mark.parametrize('branch', ['account', 'orders', 'payment', 'address', 'cart', 'checkout',
                                   'user', 'session', 'customer', 'profile', 'login', 'auth'])
def test_private_branches_with_matching_goods_id_are_not_projected(branch):
    public = product({'sku_id': '201', 'spec': 'Red', 'price': '10', 'currency': 'USD'})
    private = {'goods_id': GOODS, 'name': 'PRIVATE RECIPIENT', 'sku': 'PRIVATE ORDER CODE',
               'image': 'https://img.kwcdn.com/private-profile.jpg'}
    projected = project({branch: {'orders': [private]}, 'product': public})
    assert 'PRIVATE' not in json.dumps(projected)
    assert 'private-profile' not in json.dumps(projected)
    assert projected['products'], 'the public product must remain available'
