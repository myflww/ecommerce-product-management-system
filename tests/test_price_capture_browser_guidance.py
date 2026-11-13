import asyncio
import pytest
import price_capture as capture


def meta(title='Set A candles', country=''):
    return '<html><head><title>'+title+'</title><meta property="product:price:amount" content="12.90"><meta property="product:price:currency" content="USD">'+ ('<meta property="product:country" content="'+country+'">' if country else '')+'</head><body>Product details</body></html>'


def test_meta_must_not_use_default_variant_quote():
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(meta(), 'USD', '套装D')
    assert error.value.code == 'variant_mismatch'
    assert capture.extract_price(meta('套装D candle'), 'USD', '套装D')['price'] == '12.90'


def test_meta_requires_explicit_region_when_requested():
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(meta(), 'USD', region='美国站')
    assert error.value.code == 'region_unverified'
    assert capture.extract_price(meta(country='US'), 'USD', region='美国站')['price'] == '12.90'
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(meta(country='CA'), 'USD', region='美国站')
    assert error.value.code == 'region_mismatch'


def test_script_only_response_is_explained_without_spending_ai(monkeypatch):
    monkeypatch.setattr(capture, '_fetch_page', lambda url: ('<html><script>window.product={}</script></html>', url))
    async def forbidden(*args):
        raise AssertionError('No AI call for an empty script shell')
    with pytest.raises(capture.PriceCaptureError) as error:
        asyncio.run(capture.fetch_and_extract('https://example.com/product', 'USD', ai_extractor=forbidden))
    assert error.value.code == 'dynamic_page'
    assert '浏览器辅助' in str(error.value)
