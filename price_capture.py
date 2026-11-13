"""Read public product prices with evidence; never browse authenticated pages.

The network socket connects only to a validated numeric address. HTTPS still
validates the certificate for the original hostname and uses its SNI. This
avoids the validate-DNS/resolve-again gap without modifying global DNS behavior.
No JavaScript, subresources, proxy, cookies or model-side URL browsing is used.
"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_AI_TEXT = 12000
MAX_REDIRECTS = 3
NETWORK_TIMEOUT = 10
TOTAL_TIMEOUT = 35
USER_AGENT = "MidooPriceMonitor/1.0 (public product price verification)"


class PriceCaptureError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _public_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise PriceCaptureError("unsafe_url", "商品链接解析到了无效的网络地址。") from None
    mapped = getattr(address, "ipv4_mapped", None)
    check = mapped or address
    transitional = isinstance(address, ipaddress.IPv6Address) and any(address in network for network in (
        ipaddress.IPv6Network("64:ff9b::/96"), ipaddress.IPv6Network("64:ff9b:1::/48"),
        ipaddress.IPv6Network("2002::/16"), ipaddress.IPv6Network("2001::/32"),
    ))
    if (not check.is_global or check.is_multicast or check.is_reserved
            or check.is_unspecified or check.is_loopback or check.is_link_local or transitional):
        raise PriceCaptureError("unsafe_url", "价格追踪只支持公开商品页面，不能访问本机、内网或保留地址。")
    return str(address)


def safe_validate_url(url: str) -> str:
    """Validate URL syntax/static IPs without a network call; return normalized URL."""
    if not isinstance(url, str) or not 1 <= len(url) <= 2000 or re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise PriceCaptureError("invalid_url", "请填写有效的公开商品前端链接。")
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        if (scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or "%" in host
                or (port is not None and port != (443 if scheme == "https" else 80))):
            raise ValueError
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if (not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan"))
                or not re.fullmatch(r"[a-z0-9.:-]+", host)):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Numeric legacy spellings (2130706433, 127.1, 0x7f000001) are
            # resolved and checked again before any socket is created.
            if "." not in host:
                raise ValueError
        else:
            _public_ip(host)
        authority = "[" + host + "]" if ":" in host else host
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        return urlunsplit((scheme, authority, path, query, ""))
    except (ValueError, UnicodeError):
        raise PriceCaptureError("invalid_url", "商品链接须为公开 HTTP/HTTPS 页面，不能含登录凭据或自定义端口。") from None


def _resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise PriceCaptureError("network_error", "无法解析商品站点地址，请稍后重试。") from None
    # Reject mixed public/private answers instead of opportunistically picking
    # a public record while another retry could reach an internal address.
    addresses = list(dict.fromkeys(_public_ip(record[4][0]) for record in records))
    if not addresses:
        raise PriceCaptureError("network_error", "商品站点未返回可用的公开地址。")
    return sorted(addresses, key=lambda address: ":" in address)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int):
        super().__init__(host, port, timeout=NETWORK_TIMEOUT)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int):
        context = ssl.create_default_context()
        context.set_alpn_protocols(["http/1.1"])
        super().__init__(host, port, timeout=NETWORK_TIMEOUT, context=context)
        self._address = address

    def connect(self):
        raw_socket = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _request_once(url: str, deadline: float) -> tuple[int, dict[str, str], bytes]:
    """One direct GET. Redirects are handled only by the validating caller."""
    parsed = urlsplit(url)
    port = 443 if parsed.scheme == "https" else 80
    addresses = _resolve_public_addresses(parsed.hostname, port)
    cls = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = cls(parsed.hostname, addresses[0], port)
    try:
        if time.monotonic() > deadline:
            raise PriceCaptureError("timeout", "商品页面读取超时，请稍后重试。")
        connection.request("GET", urlunsplit(("", "", parsed.path or "/", parsed.query, "")), headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity", "Connection": "close",
        })
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status in {301, 302, 303, 307, 308}:
            return response.status, headers, b""
        if response.status != 200:
            return response.status, headers, b""
        media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type not in {"text/html", "application/xhtml+xml"}:
            raise PriceCaptureError("unsupported_content", "此链接没有返回商品网页，请填写浏览器中的商品前端链接。")
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_PAGE_BYTES:
            raise PriceCaptureError("page_too_large", "商品网页过大，本次未更新价格。")
        chunks, size = [], 0
        while True:
            if time.monotonic() > deadline:
                raise PriceCaptureError("timeout", "商品页面读取超时，请稍后重试。")
            chunk = response.read(min(65536, MAX_PAGE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                raise PriceCaptureError("page_too_large", "商品网页过大，本次未更新价格。")
            chunks.append(chunk)
        raw = b"".join(chunks)
        encoding = headers.get("content-encoding", "identity").lower().strip()
        if encoding == "gzip":
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = decoder.decompress(raw, MAX_PAGE_BYTES + 1)
            if len(raw) > MAX_PAGE_BYTES or decoder.unconsumed_tail or not decoder.eof:
                raise PriceCaptureError("page_too_large", "商品网页解压后过大或不完整，本次未更新价格。")
        elif encoding != "identity":
            raise PriceCaptureError("unsupported_content", "此商品网页的压缩格式暂不支持，请稍后重试。")
        return response.status, headers, raw
    except (OSError, http.client.HTTPException, zlib.error):
        raise PriceCaptureError("network_error", "未能安全读取商品页面，可能是网络、证书或站点访问限制。") from None
    finally:
        connection.close()


def _fetch_page(url: str) -> tuple[str, str]:
    deadline = time.monotonic() + TOTAL_TIMEOUT - 2
    current = safe_validate_url(url)
    for redirects in range(MAX_REDIRECTS + 1):
        status, headers, body = _request_once(current, deadline)
        if status in {301, 302, 303, 307, 308}:
            if redirects == MAX_REDIRECTS or not headers.get("location"):
                raise PriceCaptureError("redirect_limit", "商品页面重定向过多，请填写最终商品链接。")
            current = safe_validate_url(urljoin(current, headers["location"]))
            continue
        if status in {401, 403, 407, 429}:
            raise PriceCaptureError("access_restricted", "商品站点要求登录、验证或限制了自动访问，本次未更新价格。")
        if status != 200:
            raise PriceCaptureError("http_error", "商品页面暂不可访问（HTTP " + str(status) + "），本次未更新价格。")
        match = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", headers.get("content-type", ""))
        charset = match.group(1) if match else "utf-8"
        try:
            html = body.decode(charset, errors="replace")
        except LookupError:
            html = body.decode("utf-8", errors="replace")
        return html, current
    raise PriceCaptureError("redirect_limit", "商品页面重定向过多。")


class _ProductPage(HTMLParser):
    _excluded = {"script", "style", "form", "input", "button", "select", "textarea", "noscript", "head", "del", "s", "strike", "template"}
    _block = {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "section", "article", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.json_blocks: list[str] = []
        self.metadata: dict[str, list[str]] = {}
        self.title = ""
        self._stack: list[tuple[str, bool]] = []
        self._json_depth = 0
        self._json_parts: list[str] = []
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key and attrs.get("content"):
                self.metadata.setdefault(key.lower(), []).append(attrs["content"].strip())
        if tag == "title":
            self._in_title = True
        if tag == "script" and attrs.get("type", "").lower().split(";")[0].strip() == "application/ld+json":
            self._json_depth += 1
            self._json_parts = []
        hidden = (tag in self._excluded or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                  or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style", ""), re.I)))
        # HTML void elements must not leave an open exclusion frame.
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append((tag, hidden))
        if tag in self._block:
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self._json_depth:
            self.json_blocks.append("".join(self._json_parts))
            self._json_depth = 0
        if tag == "title":
            self._in_title = False
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                self._stack = self._stack[:index]
                break
        if tag in self._block:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._json_depth:
            self._json_parts.append(data)
        if self._in_title:
            self.title += data
        if not any(hidden for _, hidden in self._stack):
            self._parts.append(data)

    @property
    def text(self):
        return "\n".join(line for part in self._parts for line in [re.sub(r"\s+", " ", part).strip()] if line)


def _page(html: str) -> _ProductPage:
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_PAGE_BYTES:
        raise PriceCaptureError("page_too_large", "商品网页过大，本次未更新价格。")
    page = _ProductPage()
    try:
        page.feed(html)
        page.close()
    except (ValueError, RecursionError):
        raise PriceCaptureError("unsupported_content", "商品网页格式无法读取。") from None
    challenge = re.search(r"verify (?:that )?you are human|robot check|captcha verification|sign in to (?:continue|view)|请(?:输入|完成)验证码|验证您是真人|访问被拒绝", page.text[:6000], re.I)
    challenge_title = re.search(r"just a moment|access denied|captcha|robot check|^\s*(?:sign in|log in|login|登录|安全验证)\b", page.title, re.I)
    if challenge or challenge_title:
        raise PriceCaptureError("access_restricted", "商品页面要求登录或验证码，本次未更新价格。")
    return page


def _price(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PriceCaptureError("invalid_price", "页面价格格式无法核实。")
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,9}(?:\.\d{1,4})?", text):
        raise PriceCaptureError("invalid_price", "页面价格不是一个明确的商品售价。")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        raise PriceCaptureError("invalid_price", "页面价格必须是大于 0 的明确金额。") from None
    return format(amount, "f")


def _currency(value: Any, expected: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value.strip()):
        raise PriceCaptureError("currency_missing", "页面没有提供明确币种，不能只根据货币符号猜测。")
    currency = value.strip().upper()
    if currency != expected:
        raise PriceCaptureError("currency_mismatch", "页面币种与所填原定价币种不一致，本次未更新。")
    return currency


def _types(value: dict) -> set[str]:
    raw = value.get("@type", [])
    return {str(item).rsplit("/", 1)[-1] for item in (raw if isinstance(raw, list) else [raw])}


def _nodes(value: Any, depth: int = 0):
    if depth > 35:
        raise PriceCaptureError("unsupported_content", "商品结构化信息层级过多，无法可靠读取。")
    if isinstance(value, dict):
        yield value
        for item in value.values():
            if isinstance(item, (dict, list)):
                yield from _nodes(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _nodes(item, depth + 1)


def _hint_matches(value: dict, hint: str) -> bool:
    haystack = " ".join(str(value.get(field, "")) for field in ("name", "sku", "productID", "mpn", "color", "size"))
    # Identifiers need token boundaries: SKU-A must never match SKU-AA.
    pattern = r'(?<![A-Za-z0-9_-])' + re.escape(hint.strip().casefold()) + r'(?![A-Za-z0-9_-])'
    return bool(re.search(pattern, haystack.casefold()))


def _sku_matches(value: dict, hint: str) -> bool:
    sku = value.get('sku')
    return isinstance(sku, (str, int)) and not isinstance(sku, bool) and str(sku).strip().casefold() == hint.strip().casefold()


def _sku_missing(value: dict) -> bool:
    sku = value.get('sku')
    return sku is None or (isinstance(sku, str) and not sku.strip())


def _country(value: str) -> str:
    if not isinstance(value, str):
        return ""
    aliases = {"美国": "US", "美国站": "US", "us": "US", "usa": "US", "united states": "US", "英国": "GB", "英国站": "GB", "uk": "GB", "united kingdom": "GB", "加拿大": "CA", "加拿大站": "CA", "中国": "CN", "中国站": "CN", "澳大利亚": "AU", "澳洲": "AU", "德国": "DE", "日本": "JP", "法国": "FR"}
    text = value.strip().casefold()
    return aliases.get(text, value.strip().upper() if re.fullmatch(r"[a-zA-Z]{2}", value.strip()) else "")


def _check_region(offer: dict, region: str):
    expected = _country(region)
    if not expected:
        return
    served = offer.get("eligibleRegion", offer.get("areaServed"))
    values = served if isinstance(served, list) else [served]
    countries = {_country(value.get("identifier", value.get("name", "")) if isinstance(value, dict) else str(value or "")) for value in values}
    countries.discard("")
    if countries and expected not in countries:
        raise PriceCaptureError("region_mismatch", "页面明确适用的地区与所填站点地区不一致，本次未更新。")


def _extract(page: _ProductPage, expected_currency: str, variant_hint: str, region: str, expected_sku: str = '') -> dict:
    expected = _currency(expected_currency, str(expected_currency).upper())
    nodes = []
    for block in page.json_blocks[:40]:
        try:
            nodes.extend(_nodes(json.loads(block)))
        except (ValueError, TypeError, RecursionError):
            continue
    by_id = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str) and len(node) > 1}
    products = [node for node in nodes if _types(node) & {"Product", "IndividualProduct", "ProductModel"} and node.get("offers")]
    # A complete @id node can be repeated as part of a graph; it is still one product.
    unique = {json.dumps(node, sort_keys=True, ensure_ascii=False): node for node in products}
    products = list(unique.values())
    if expected_sku:
        matches = []
        for item in products:
            offers = item['offers'] if isinstance(item['offers'], list) else [item['offers']]
            offers = [by_id.get(offer.get('@id'), offer) if isinstance(offer, dict) else offer for offer in offers]
            if _sku_matches(item, expected_sku) or any(isinstance(offer, dict) and _sku_matches(offer, expected_sku) for offer in offers):
                matches.append(item)
        if not matches:
            raise PriceCaptureError('sku_unverified', '页面没有与所填 SKU 完整匹配的报价标识，请用浏览器辅助选好该 SKU 后确认价格。')
        products = matches
    if variant_hint:
        matching = [node for node in products if _hint_matches(node, variant_hint)]
        if len(products) > 1 and len(matching) != 1:
            raise PriceCaptureError("ambiguous_price", "页面存在多个商品或规格，请提供指向具体规格的链接与规格标识。")
        if matching:
            products = matching
    if len(products) > 1:
        raise PriceCaptureError("ambiguous_price", "页面包含多个商品或规格，不能确定要追踪的商品售价。")
    if products:
        product = products[0]
        offers = product["offers"]
        offers = offers if isinstance(offers, list) else [offers]
        expanded = [by_id.get(item.get("@id"), item) if isinstance(item, dict) else item for item in offers]
        if expected_sku:
            expanded = [offer for offer in expanded if isinstance(offer, dict) and
                        (_sku_matches(offer, expected_sku) or (_sku_missing(offer) and _sku_matches(product, expected_sku)))]
            if not expanded:
                raise PriceCaptureError('sku_mismatch', '当前报价属于其他 SKU，本次未更新。请在浏览器辅助中选择目标规格。')
        if variant_hint:
            matching = [offer for offer in expanded if isinstance(offer, dict) and _hint_matches(offer, variant_hint)]
            if matching:
                expanded = matching
            elif (not _hint_matches(product, variant_hint) or any(isinstance(offer, dict) and
                    any(offer.get(field) for field in ('sku', 'name', 'productID', 'mpn', 'color', 'size')) for offer in expanded)):
                raise PriceCaptureError("variant_mismatch", "页面商品与报价中没有匹配所填规格标识，本次未更新价格。")
        found = []
        for offer in expanded:
            if not isinstance(offer, dict):
                continue
            if "AggregateOffer" in _types(offer) or "lowPrice" in offer or "highPrice" in offer:
                raise PriceCaptureError("ambiguous_price", "页面给出的是价格区间或多卖家报价，请提供具体规格的商品链接。")
            if "price" not in offer:
                continue
            if re.search(r"OutOfStock|Discontinued|SoldOut", str(offer.get("availability", "")), re.I):
                raise PriceCaptureError("unavailable", "页面标记商品已售罄或停售，本次不更新价格。")
            valid_until = str(offer.get("priceValidUntil", ""))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valid_until) and valid_until < datetime.now(timezone.utc).date().isoformat():
                raise PriceCaptureError("expired_price", "页面价格的有效期已过，本次不更新价格。")
            _check_region(offer, region)
            amount = _price(offer["price"])
            currency = _currency(offer.get("priceCurrency"), expected)
            evidence = "Offer.price=" + str(offer["price"]) + "; Offer.priceCurrency=" + currency
            if expected_sku:
                evidence += '; matched_sku=' + expected_sku
            found.append({"price": amount, "currency": currency, "title": str(product.get("name") or page.title)[:300], "evidence": evidence, "source": "json_ld_offer"})
        if len({(item["price"], item["currency"]) for item in found}) > 1:
            raise PriceCaptureError("ambiguous_price", "页面存在多个不同售价，请提供具体规格的链接与规格标识。")
        if found:
            return found[0]
    # Product-specific metadata only; generic price/og:price may describe a
    # subscription, cart total or unrelated advertised item.
    prices = page.metadata.get("product:price:amount", [])
    currencies = page.metadata.get("product:price:currency", [])
    if prices or currencies:
        if len(set(prices)) != 1 or len(set(currencies)) != 1:
            raise PriceCaptureError("ambiguous_price", "页面商品价格标记不唯一或缺少币种，本次未更新。")
        identity = {'name': page.title + ' ' + ' '.join(page.metadata.get('og:title', [])),
                    'sku': ' '.join(page.metadata.get('product:retailer_item_id', []))}
        if variant_hint and not _hint_matches(identity, variant_hint):
            raise PriceCaptureError('variant_mismatch', '网页价格标记不能确认所填规格，请用浏览器辅助选择规格后读取。')
        if region:
            countries = page.metadata.get('product:country', []) + page.metadata.get('product:availability:country', [])
            expected_country = _country(region)
            actual_countries = {_country(value) for value in countries}
            actual_countries.discard('')
            if not expected_country or not actual_countries:
                raise PriceCaptureError('region_unverified', '网页价格标记不能确认目标地区，请用浏览器辅助核对地区后读取。')
            _check_region({'eligibleRegion': list(actual_countries)}, region)
        amount, currency = _price(prices[0]), _currency(currencies[0], expected)
        return {"price": amount, "currency": currency, "title": page.title[:300], "evidence": "product:price:amount=" + prices[0] + "; product:price:currency=" + currency, "source": "product_meta"}
    raise PriceCaptureError("price_missing", "普通采集未取得可核验的商品售价。Temu 等动态页面请使用浏览器辅助，加载并选择规格后读取。")


def extract_price(html: str, expected_currency: str, variant_hint: str = "", region: str = "", *, expected_sku: str = '') -> dict:
    """Extract only explicit, unambiguous product price facts from an HTML page."""
    return _extract(_page(html), expected_currency, variant_hint, region, expected_sku)


_MONEY = re.compile(r"(?:\b(?:USD|CNY|EUR|GBP|CAD|AUD|JPY|HKD|CHF)\s*|US\$\s*|CA\$\s*|AU\$\s*|HK\$\s*|[$€£¥￥]\s*)(\d{1,9}(?:\.\d{1,4})?)(?![\d.,])|(?<![\d.,])(\d{1,9}(?:\.\d{1,4})?)(?![\d.,])\s*(?:USD|CNY|EUR|GBP|CAD|AUD|JPY|HKD|CHF)\b", re.I)
_SYMBOLS = {"USD": "US$", "CAD": "CA$", "AUD": "AU$", "HKD": "HK$", "EUR": "€", "GBP": "£"}


def _validate_ai_result(value: Any, text: str, expected: str, title: str) -> dict:
    if not isinstance(value, dict):
        raise PriceCaptureError("unverified_ai_price", "AI 没有返回可核验的价格，本次未更新。")
    amount = _price(value.get("price"))
    currency = _currency(value.get("currency"), expected)
    evidence = value.get("evidence")
    normalized = re.sub(r"\s+", " ", text).strip()
    if not isinstance(evidence, str) or not 4 <= len(evidence.strip()) <= 600:
        raise PriceCaptureError("unverified_ai_price", "AI 未提供可核验的页面原文证据，本次未更新。")
    evidence = re.sub(r"\s+", " ", evidence).strip()
    numbers = {Decimal(match.group(1) or match.group(2)) for match in _MONEY.finditer(evidence) if "," not in (match.group(1) or match.group(2))}
    currency_proof = re.search(r"(?<![A-Z])" + re.escape(currency) + r"(?![A-Z])", evidence, re.I) or (_SYMBOLS.get(currency) and _SYMBOLS[currency] in evidence)
    non_current = re.search(r"original|list price|was\b|rrp\b|shipping|delivery|coupon|save\b|discount amount|划线|原价|运费|配送费|优惠券|节省", evidence, re.I)
    price_label = re.search(r"price|now\b|售价|现价|价格", evidence, re.I)
    if (evidence not in normalized or Decimal(amount) not in numbers or not currency_proof or non_current or not price_label):
        raise PriceCaptureError("unverified_ai_price", "AI 提供的金额、币种或售价证据无法在本页原文核实，本次未更新。")
    return {"price": amount, "currency": currency, "title": title[:300], "evidence": evidence, "source": "ai_page_text"}


async def fetch_and_extract(url: str, expected_currency: str, variant_hint: str = "",
                            ai_extractor: Callable[..., Awaitable[dict]] | None = None, region: str = "", *, expected_sku: str = '') -> dict:
    """Fetch a public page, prefer explicit facts, optionally verify AI extraction.

    The optional callback receives only sanitized visible page text, expected
    currency and variant hint. It never receives raw HTML, hidden forms, cookies,
    URL parameters or an instruction to browse a URL.
    """
    normalized_url = safe_validate_url(url)
    expected = _currency(expected_currency, str(expected_currency).upper())
    if not isinstance(variant_hint, str) or len(variant_hint) > 200 or not isinstance(region, str) or len(region) > 80:
        raise PriceCaptureError("invalid_input", "商品规格标识或地区信息过长。")
    if not isinstance(expected_sku, str) or len(expected_sku) > 200:
        raise PriceCaptureError('invalid_input', 'SKU 标识格式不正确或过长。')
    try:
        html, final_url = await asyncio.wait_for(asyncio.to_thread(_fetch_page, normalized_url), timeout=TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        raise PriceCaptureError("timeout", "商品页面读取超时，本次未更新价格。") from None
    page = _page(html)
    try:
        result = _extract(page, expected, variant_hint, region, expected_sku)
    except PriceCaptureError as error:
        if error.code in {'price_missing', 'sku_unverified'} and not page.text.strip() and re.search(r'<script\b', html, re.I):
            raise PriceCaptureError('dynamic_page', '站点只返回了脚本加载页，没有商品正文或售价。请点击浏览器辅助，完成页面加载或验证后再读取。') from None
        if error.code != "price_missing" or ai_extractor is None:
            raise
        text = page.text[:MAX_AI_TEXT]
        money = {(match.group(1) or match.group(2)).replace(",", ".") for match in _MONEY.finditer(text)}
        if len({Decimal(value) for value in money}) > 1:
            raise PriceCaptureError("ambiguous_price", "页面正文存在多个金额，无法可靠区分规格价、优惠价或配送费。")
        if not money:
            raise PriceCaptureError("price_missing", "公开页面正文没有可核验的价格，本次不会请求 AI 猜测。")
        if variant_hint and variant_hint.casefold() not in text.casefold():
            raise PriceCaptureError("variant_mismatch", "公开页面没有找到所填规格标识，本次未更新价格。")
        value = await ai_extractor(text, expected, variant_hint)
        result = _validate_ai_result(value, text, expected, page.title)
    result.update(url=final_url, region_hint=region, variant_hint=variant_hint)
    return result
