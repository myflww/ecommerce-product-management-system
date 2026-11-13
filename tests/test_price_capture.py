"""Public-page price extraction uses fabricated HTML and mocked sockets only."""
import asyncio
import gzip
import json
import socket
from decimal import Decimal

import pytest

import price_capture as capture


def html(product=None, body="", extra=""):
    data = '' if product is None else '<script type="application/ld+json">' + json.dumps(product) + '</script>'
    return '<html><head><title>Test product</title>' + data + extra + '</head><body>' + body + '</body></html>'


def product(price="12.90", currency="USD", **offer):
    return {"@type":"Product", "name":"Blue candle 2 pieces", "sku":"CND-001", "offers":{
        "@type":"Offer", "price":price,"priceCurrency":currency, **offer}}


def run(url="https://shop.example/product", **kwargs):
    return asyncio.run(capture.fetch_and_extract(url, "USD", **kwargs))


def test_explicit_product_offer_has_verified_currency_and_evidence():
    result = capture.extract_price(html(product()), "USD")
    assert result['price'] == '12.90'
    assert result['currency'] == 'USD'
    assert result['source'] == 'json_ld_offer'
    assert 'Offer.price=12.90' in result['evidence']


def test_jsonld_graph_resolves_offer_reference():
    graph = {"@context":"https://schema.org", "@graph":[
        {"@type":"Product", "name":"Candle", "offers":{"@id":"#offer"}},
        {"@id":"#offer","@type":"Offer", "price":"8.6", "priceCurrency":"USD"}]}
    assert capture.extract_price(html(graph), 'USD')['price'] == '8.6'


def test_unrelated_original_and_shipping_prices_are_not_used():
    p = product()
    p['offers']['priceSpecification'] = {'price':'99.90','priceType':'https://schema.org/StrikethroughPrice'}
    p['offers']['shippingDetails'] = {'shippingRate':{'value':'3.50','currency':'USD'}}
    assert capture.extract_price(html(p, '<del>USD 99.90</del><p>Shipping USD 3.50</p>'), 'USD')['price'] == '12.90'


@pytest.mark.parametrize('price', [True, None, '0', '-1', '9-12', '12,90', 'NaN', '1e5', '0.00001', '1000000000'])
def test_ambiguous_or_invalid_amount_is_refused(price):
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(html(product(price)), 'USD')
    assert error.value.code == 'invalid_price'


@pytest.mark.parametrize('currency,code', [('CAD','currency_mismatch'), ('$','currency_missing'), (None,'currency_missing')])
def test_currency_is_not_inferred_from_expected_value(currency, code):
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(html(product(currency=currency)), 'USD')
    assert error.value.code == code


def test_aggregate_and_multiple_sku_prices_are_refused():
    p = product()
    p['offers'] = {'@type':'AggregateOffer','lowPrice':'5','highPrice':'15','priceCurrency':'USD'}
    with pytest.raises(capture.PriceCaptureError, match='区间'):
        capture.extract_price(html(p), 'USD')
    p['offers'] = [product('5')['offers'], product('15')['offers']]
    with pytest.raises(capture.PriceCaptureError, match='多个不同售价'):
        capture.extract_price(html(p), 'USD')


def test_multiple_product_variants_need_unique_matching_hint():
    blue = product('5')
    red = {**product('15'), 'name':'Red candle', 'sku':'RED-002'}
    with pytest.raises(capture.PriceCaptureError, match='多个商品'):
        capture.extract_price(html([blue, red]), 'USD')
    assert capture.extract_price(html([blue, red]), 'USD', 'RED-002')['price'] == '15'
    with pytest.raises(capture.PriceCaptureError):
        capture.extract_price(html([blue, red]), 'USD', 'no-such-variant')


def test_offer_variant_hint_does_not_select_arbitrary_lowest_price():
    p = product()
    p['offers'] = [product('5', sku='SMALL')['offers'], product('15', sku='LARGE')['offers']]
    assert capture.extract_price(html(p), 'USD', 'LARGE')['price'] == '15'


def test_even_a_single_product_must_match_an_explicit_variant_hint(monkeypatch):
    for p in [product(), {**product(), 'offers':[product('5',sku='SMALL')['offers'],product('5',sku='LARGE')['offers']]}]:
        monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(p),url))
        async def ai(*args):pytest.fail('A missing explicit variant must never fall back to AI')
        with pytest.raises(capture.PriceCaptureError) as error:
            run(variant_hint='WRONG-SKU',ai_extractor=ai)
        assert error.value.code == 'variant_mismatch'


def test_product_specific_metadata_fallback():
    markup = '<meta property="product:price:amount" content="12.90"><meta property="product:price:currency" content="USD">'
    assert capture.extract_price(html(extra=markup), 'USD')['source'] == 'product_meta'
    bad = markup + '<meta property="product:price:amount" content="5">'
    with pytest.raises(capture.PriceCaptureError):
        capture.extract_price(html(extra=bad), 'USD')


def test_generic_price_meta_is_not_used_for_product_price():
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(html(extra='<meta name="price" content="1.99">'), 'USD')
    assert error.value.code == 'price_missing'


@pytest.mark.parametrize('body,title', [('Verify you are human','Test product'), ('Sign in to view this product','Test product'), ('','Just a moment...'), ('请输入验证码','Test product')])
def test_challenge_pages_refused_even_if_they_embed_a_price(body, title):
    page = html(product(), body).replace('Test product', title)
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(page, 'USD')
    assert error.value.code == 'access_restricted'


def test_explicit_region_conflicts_and_expired_prices_are_refused():
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(html(product(eligibleRegion='CA')), 'USD', region='美国站')
    assert error.value.code == 'region_mismatch'
    assert capture.extract_price(html(product(eligibleRegion=['CA','US'])), 'USD', region='美国站')['price'] == '12.90'
    with pytest.raises(capture.PriceCaptureError) as error:
        capture.extract_price(html(product(priceValidUntil='2020-01-01')), 'USD')
    assert error.value.code == 'expired_price'


@pytest.mark.parametrize('url', [
    'file:///C:/Windows/test', 'ftp://shop.example/product', 'https://user:secret@shop.example/product',
    'http://127.0.0.1/a', 'http://10.0.0.1/a', 'http://169.254.169.254/latest/meta-data',
    'http://[::1]/a', 'http://[::ffff:127.0.0.1]/a', 'http://localhost/a',
    'http://[64:ff9b::7f00:1]/a', 'http://[2002:7f00:1::]/a',
    'http://shop.local/a', 'https://shop.internal/a', 'http://224.0.0.1/a',
    'http://shop.example:8080/a', 'https://shop.example:80/a', 'https://shop.example/\r\nX:1',
    'https://shop.example\\@127.0.0.1/', 'https://%31%32%37.0.0.1/',
])
def test_non_public_or_unsafe_url_is_refused_without_network(url, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: pytest.fail('Must not resolve a rejected URL'))
    with pytest.raises(capture.PriceCaptureError):
        capture.safe_validate_url(url)


def test_validated_url_normalizes_idna_and_drops_fragment():
    assert capture.safe_validate_url('https://SHOP.example:443/item?q=1#price') == 'https://shop.example/item?q=1'
    assert capture.safe_validate_url('https://shop.example/蜡烛') == 'https://shop.example/%E8%9C%A1%E7%83%9B'


def test_dns_rejects_private_and_mixed_answer_sets(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo', lambda *a, **kw:[
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443)),
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(capture.PriceCaptureError) as error:
        capture._resolve_public_addresses('shop.example',443)
    assert error.value.code == 'unsafe_url'


def test_dns_is_resolved_once_and_socket_uses_the_pinned_ip(monkeypatch):
    calls=[]
    monkeypatch.setattr(socket, 'create_connection', lambda target, timeout: calls.append((target,timeout)) or object())
    conn=capture._PinnedHTTPConnection('shop.example','93.184.216.34',80)
    conn.connect()
    assert calls == [(('93.184.216.34',80),capture.NETWORK_TIMEOUT)]
    assert conn.host == 'shop.example'


def test_https_pin_preserves_hostname_for_tls_verification(monkeypatch):
    calls=[]
    class Context:
        def set_alpn_protocols(self, protocols): assert protocols == ['http/1.1']
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(('tls',server_hostname));return sock
    monkeypatch.setattr(capture.ssl,'create_default_context',lambda:Context())
    monkeypatch.setattr(socket,'create_connection',lambda target, timeout:calls.append(('socket',target)) or object())
    # HTTPSConnection expects context attributes in addition to wrap_socket.
    context=Context();context.check_hostname=True;context.verify_mode=capture.ssl.CERT_REQUIRED
    monkeypatch.setattr(capture.ssl,'create_default_context',lambda:context)
    conn=capture._PinnedHTTPSConnection('shop.example','93.184.216.34',443)
    conn.connect()
    assert calls == [('socket',('93.184.216.34',443)),('tls','shop.example')]


def test_redirects_are_revalidated_before_request_and_are_bounded(monkeypatch):
    visited=[]
    def response(url, deadline):
        visited.append(url)
        return 302,{'location':'http://127.0.0.1/private'},b''
    monkeypatch.setattr(capture,'_request_once',response)
    with pytest.raises(capture.PriceCaptureError):run()
    assert visited == ['https://shop.example/product']
    visited.clear()
    monkeypatch.setattr(capture,'_request_once',lambda url, deadline:(visited.append(url) or 302, {'location':'/loop'}, b''))
    with pytest.raises(capture.PriceCaptureError) as error:run()
    assert error.value.code == 'redirect_limit'
    assert len(visited) == capture.MAX_REDIRECTS + 1


@pytest.mark.parametrize('status', [401,403,407,429,500])
def test_failed_http_status_never_calls_ai(status, monkeypatch):
    monkeypatch.setattr(capture,'_request_once',lambda *a:(status,{},b''))
    async def ai(*args):pytest.fail('Blocked page must not be given to AI')
    with pytest.raises(capture.PriceCaptureError):run(ai_extractor=ai)


def test_structured_facts_do_not_call_ai(monkeypatch):
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(product()),url))
    async def ai(*args):pytest.fail('Directly verified facts should not consume an AI call')
    result=run(ai_extractor=ai)
    assert result['price'] == '12.90' and result['url'] == 'https://shop.example/product'


def test_ambiguous_structured_prices_never_fall_back_to_ai(monkeypatch):
    p=product();p['offers']=[product('5')['offers'],product('15')['offers']]
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(p,'Current price USD 12.90'),url))
    async def ai(*args):pytest.fail('AI must not bypass an ambiguity rejection')
    with pytest.raises(capture.PriceCaptureError):run(ai_extractor=ai)


def test_ai_receives_only_sanitized_visible_text_and_must_quote_the_price(monkeypatch):
    markup=html(body='<div>Current price USD 12.90</div><form>secret-token<input value="key"></form><script>hidden-secret</script><p style="display:none">another-secret</p><del>Original USD 99.90</del>')
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(markup,url))
    seen=[]
    async def ai(text,currency,variant):
        seen.append(text)
        assert currency=='USD' and variant==''
        return {'price':'12.90','currency':'USD','evidence':'Current price USD 12.90'}
    result=run(ai_extractor=ai)
    assert result['source']=='ai_page_text'
    assert len(seen)==1 and 'secret' not in seen[0] and '99.90' not in seen[0]


@pytest.mark.parametrize('value', [
    {'price':'1.90','currency':'USD','evidence':'Current price USD 12.90'},
    {'price':'12.90','currency':'USD','evidence':'Current price USD 12.90 invented'},
    {'price':'12.90','currency':'USD','evidence':'Shipping price USD 12.90'},
    {'price':'12.90','currency':'CAD','evidence':'Current price USD 12.90'},
    {'price':'12.90','currency':'USD','evidence':'Current price $12.90'},
])
def test_ai_cannot_invent_numbers_currency_or_source_text(value,monkeypatch):
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(body='Current price USD 12.90'),url))
    async def ai(*args):return value
    with pytest.raises(capture.PriceCaptureError):run(ai_extractor=ai)


def test_multi_amount_or_no_price_text_does_not_consume_ai_call(monkeypatch):
    async def ai(*args):pytest.fail('Missing or ambiguous amount must not call AI')
    for body in ['Price USD 12.90 Shipping USD 2.99','Please enable JavaScript to view this product']:
        monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(body=body),url))
        with pytest.raises(capture.PriceCaptureError):run(ai_extractor=ai)


@pytest.mark.parametrize('amount',['1,234.56','12,90','1234.56789'])
def test_ai_does_not_parse_just_a_prefix_of_an_ambiguous_number(amount,monkeypatch):
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(body='Current price USD '+amount),url))
    async def ai(*args):pytest.fail('Unrecognized amount formatting must not be guessed')
    with pytest.raises(capture.PriceCaptureError):run(ai_extractor=ai)


def test_ai_requires_matching_variant_and_bounded_body(monkeypatch):
    async def ai(*args):pytest.fail('Mismatched SKU must not call AI')
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(body='Price USD 12.90 SKU RED'),url))
    with pytest.raises(capture.PriceCaptureError) as error:run(ai_extractor=ai,variant_hint='BLUE')
    assert error.value.code == 'variant_mismatch'
    text='Price USD 12.90 '+('x'*13000)
    monkeypatch.setattr(capture,'_fetch_page',lambda url:(html(body=text),url))
    async def bounded_ai(text,*args):
        assert len(text)<=12000
        return {'price':'12.90','currency':'USD','evidence':'Price USD 12.90'}
    assert run(ai_extractor=bounded_ai)['price']=='12.90'


def test_request_bounds_content_type_and_decompressed_body(monkeypatch):
    class Response:
        status=200
        def __init__(self,headers,body):self.headers=headers;self.body=body
        def getheaders(self):return list(self.headers.items())
        def read(self,n):chunk=self.body[:n];self.body=self.body[n:];return chunk
    class Connection:
        closed=False
        def __init__(self,*args):pass
        def request(self,*args,**kwargs):
            assert kwargs['headers']['Accept-Encoding']=='identity'
            assert 'Cookie' not in kwargs['headers']
        def getresponse(self):return response
        def close(self):self.closed=True
    monkeypatch.setattr(capture,'_resolve_public_addresses',lambda *a:['93.184.216.34'])
    monkeypatch.setattr(capture,'_PinnedHTTPSConnection',Connection)
    for headers,body,code in [
        ({'content-type':'application/json'},b'{}','unsupported_content'),
        ({'content-type':'text/html','content-length':str(capture.MAX_PAGE_BYTES+1)},b'','page_too_large'),
        ({'content-type':'text/html','content-encoding':'gzip'},gzip.compress(b'x'*(capture.MAX_PAGE_BYTES+1)),'page_too_large'),
    ]:
        response=Response(headers,body)
        with pytest.raises(capture.PriceCaptureError) as error:
            capture._request_once('https://shop.example/item',float('inf'))
        assert error.value.code==code
    response=Response({'content-type':'text/html','content-encoding':'gzip'},gzip.compress(b'<html>ok</html>'))
    assert capture._request_once('https://shop.example/item',float('inf'))[2] == b'<html>ok</html>'
