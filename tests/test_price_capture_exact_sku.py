"""Exact-SKU price selection uses fabricated pages and mocked fetches only."""
from __future__ import annotations

import asyncio
import json

import pytest

import price_capture as capture

URL='https://shop.example/product'
ABSENT=object()


def offer(price='12.90',sku=ABSENT,**changes):
    value={'@type':'Offer','price':price,'priceCurrency':'USD',**changes}
    if sku is not ABSENT:
        value['sku']=sku
    return value


def product(sku=ABSENT,offers=ABSENT,**changes):
    value={'@type':'Product','name':'Candle collection','offers':offer() if offers is ABSENT else offers,**changes}
    if sku is not ABSENT:
        value['sku']=sku
    return value


def html(data=None,body='Current product price USD 12.90',meta=''):
    ld='' if data is None else '<script type="application/ld+json">'+json.dumps(data)+'</script>'
    return '<html><head><title>Candle product</title>'+meta+ld+'</head><body>'+body+'</body></html>'


def run(markup,monkeypatch,*,expected_sku='SKU-A',**kwargs):
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(markup,url))
    return asyncio.run(capture.fetch_and_extract(URL,'USD',expected_sku=expected_sku,**kwargs))


def rejected(markup,sku='SKU-A',**kwargs):
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(markup,'USD',expected_sku=sku,**kwargs)
    return error.value.code


def test_parent_product_exact_sku_can_authorize_an_offer_without_its_own_sku():
    result=capture.extract_price(html(product('SKU-A')),'USD',expected_sku='SKU-A')
    assert result['price']=='12.90'
    assert result['source']=='json_ld_offer'


def test_offer_sku_selects_the_right_variant_even_with_a_parent_family_identifier():
    data=product('FAMILY',offers=[offer('5.90','SKU-A'),offer('19.90','SKU-B')])
    assert capture.extract_price(html(data),'USD',expected_sku='SKU-A')['price']=='5.90'
    assert capture.extract_price(html(data),'USD',expected_sku='SKU-B')['price']=='19.90'


@pytest.mark.parametrize('field', ['product','offer'])
@pytest.mark.parametrize('wrong', ['SKU-AA','PRE-SKU-A','SKU-A-2','SKU_A','SKU-AX'])
def test_sku_matching_uses_a_complete_identifier_not_a_substring(field,wrong):
    data=product(wrong) if field=='product' else product(offers=offer(sku=wrong))
    assert rejected(html(data))=='sku_unverified'


def test_whitespace_and_case_normalization_do_not_change_identifier_boundaries():
    assert capture.extract_price(html(product(' sku-a ')),'USD',expected_sku='SKU-A')['price']=='12.90'
    assert rejected(html(product(' SKU-AA ')))=='sku_unverified'


def test_numeric_sku_is_exact_and_does_not_drop_textual_leading_zeroes():
    assert capture.extract_price(html(product(123)),'USD',expected_sku='123')['price']=='12.90'
    assert rejected(html(product(123)),sku='00123')=='sku_unverified'
    assert capture.extract_price(html(product('00123')),'USD',expected_sku='00123')['price']=='12.90'


def test_parent_product_name_cannot_override_an_offer_explicitly_for_another_sku():
    data=product(offers=offer(sku='SMALL'),name='Large candle set')
    assert rejected(html(data),sku='LARGE')=='sku_unverified'
    # The human-readable variant path must have the same protection, even
    # when exact-SKU filtering is not requested by an old client.
    assert rejected(html(data),sku='',variant_hint='Large')=='variant_mismatch'


def test_correct_parent_sku_does_not_override_an_explicit_offer_sku_conflict():
    data=product('SKU-A',offers=offer(sku='SKU-B'))
    assert rejected(html(data))=='sku_mismatch'


@pytest.mark.parametrize('sku', [0,False,[],{}])
def test_falsy_or_malformed_offer_sku_is_not_treated_as_an_absent_identifier(sku):
    data=product('SKU-A',offers=offer(sku=sku))
    assert rejected(html(data))=='sku_mismatch'


@pytest.mark.parametrize('sku', [ABSENT,None,'','   '])
def test_really_absent_offer_sku_can_use_the_exact_parent_sku(sku):
    result=capture.extract_price(html(product('SKU-A',offers=offer(sku=sku))),'USD',expected_sku='SKU-A')
    assert result['price']=='12.90'


def test_explicit_zero_offer_sku_is_a_valid_exact_identifier():
    data=product('FAMILY',offers=[offer('3.90',0),offer('7.90','SKU-A')])
    assert capture.extract_price(html(data),'USD',expected_sku='0')['price']=='3.90'


def test_parent_sku_substring_cannot_authorize_an_unidentified_offer():
    assert rejected(html(product('SKU-AA',name='SKU-A candle family')))=='sku_unverified'


def test_unknown_sku_cannot_use_a_default_metadata_price():
    meta='<meta property="product:price:amount" content="12.90"><meta property="product:price:currency" content="USD">'
    assert rejected(html(meta=meta))=='sku_unverified'
    assert rejected(html(product('SKU-B'),meta=meta))=='sku_unverified'


@pytest.mark.parametrize('kind', ['text','meta','other_product'])
def test_unknown_sku_never_falls_back_to_a_paid_ai_default_price(kind,monkeypatch):
    data=product('SKU-B') if kind=='other_product' else None
    meta='<meta property="product:price:amount" content="12.90"><meta property="product:price:currency" content="USD">' if kind=='meta' else ''
    markup=html(data,body='SKU-A appears in a recommendation. Current price USD 12.90',meta=meta)
    async def ai(*args):
        pytest.fail('An unverified SKU must never invoke a cloud price extractor')
    with pytest.raises(capture.PriceCaptureError) as error:
        run(markup,monkeypatch,ai_extractor=ai)
    assert error.value.code=='sku_unverified'


def test_same_product_url_can_return_different_unit_prices_for_different_exact_skus(monkeypatch):
    markup=html(product('FAMILY',offers=[offer('5.90','SKU-A'),offer('15.90','SKU-B')]))
    one=run(markup,monkeypatch,expected_sku='SKU-A')
    two=run(markup,monkeypatch,expected_sku='SKU-B')
    assert one['url']==two['url']==URL
    assert (one['price'],two['price'])==('5.90','15.90')


def test_same_sku_with_multiple_different_prices_is_not_resolved_as_the_lowest():
    data=product('FAMILY',offers=[offer('5.90','SKU-A'),offer('15.90','SKU-A')])
    assert rejected(html(data))=='ambiguous_price'


def test_parent_exact_sku_does_not_make_multiple_unidentified_offer_prices_unambiguous():
    data=product('SKU-A',offers=[offer('5.90'),offer('15.90')])
    assert rejected(html(data))=='ambiguous_price'


def test_same_sku_repeated_with_identical_price_is_still_one_price():
    data=product('FAMILY',offers=[offer('5.90','SKU-A'),offer('5.90','SKU-A')])
    assert capture.extract_price(html(data),'USD',expected_sku='SKU-A')['price']=='5.90'


def test_exact_sku_resolves_a_referenced_offer_in_a_jsonld_graph():
    markup=html({'@graph':[
        {'@type':'Product','name':'Candle family','offers':[{'@id':'#a'},{'@id':'#b'}]},
        {'@id':'#a',**offer('5.90','SKU-A')},{'@id':'#b',**offer('15.90','SKU-B')},
    ]})
    assert capture.extract_price(markup,'USD',expected_sku='SKU-B')['price']=='15.90'


@pytest.mark.parametrize('extra,code', [
    ({'priceValidUntil':'2020-01-01'},'expired_price'),
    ({'availability':'https://schema.org/OutOfStock'},'unavailable'),
    ({'priceCurrency':'CAD'},'currency_mismatch'),
    ({'eligibleRegion':'CA'},'region_mismatch'),
])
def test_exact_sku_does_not_bypass_expiry_stock_currency_or_region_checks(extra,code):
    data=product('FAMILY',offers=[offer('5.90','SKU-A',**extra),offer('15.90','SKU-B')])
    assert rejected(html(data),region='美国站')==code


def test_expired_target_sku_cannot_fall_back_to_a_fresh_other_sku_or_ai(monkeypatch):
    data=product('FAMILY',offers=[offer('5.90','SKU-A',priceValidUntil='2020-01-01'),offer('15.90','SKU-B')])
    async def ai(*args):
        pytest.fail('An expired target SKU must not invoke AI or another variant')
    with pytest.raises(capture.PriceCaptureError) as error:
        run(html(data),monkeypatch,ai_extractor=ai)
    assert error.value.code=='expired_price'


def test_exact_sku_does_not_accept_an_aggregate_price_range():
    data=product('SKU-A',offers={'@type':'AggregateOffer','sku':'SKU-A','lowPrice':'5','highPrice':'15','priceCurrency':'USD'})
    assert rejected(html(data))=='ambiguous_price'


def test_default_empty_exact_sku_preserves_single_offer_clients():
    assert capture.extract_price(html(product()),'USD',expected_sku='')['price']=='12.90'


@pytest.mark.parametrize('bad', [None,False,123,[],{},'x'*201])
def test_bad_exact_sku_is_rejected_before_any_fetch(bad,monkeypatch):
    monkeypatch.setattr(capture,'_fetch_page',lambda *args:pytest.fail('Invalid SKU must not start a request'))
    with pytest.raises(capture.PriceCaptureError) as error:
        asyncio.run(capture.fetch_and_extract(URL,'USD',expected_sku=bad))
    assert error.value.code=='invalid_input'
