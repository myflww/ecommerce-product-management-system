"""Bounded, read-only Temu storefront extraction from page-owned evidence.

No requests, cookies, browser storage, script execution, or seller API calls.
Temu hydration layouts are deliberately treated as unverified adapters: a
changed layout produces incomplete evidence rather than invented SKU prices.
Public goods/variant IDs are never presented as merchant SKC/SKU codes.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

MAX_PRODUCTS = 20
MAX_VARIANTS = 200
MAX_SCRIPTS = 20
MAX_JSON_CHARS = 2_000_000
MAX_NODES = 25_000
MAX_DEPTH = 35
_ID = re.compile(r"^[0-9]{1,30}$")
_CURRENCIES = frozenset("USD CNY EUR GBP CAD AUD JPY HKD CHF NZD SGD KRW MXN BRL TWD SEK NOK DKK PLN CZK INR ZAR AED SAR TRY IDR MYR THB PHP VND ILS CLP COP PEN".split())
_RELATED = re.compile(r"recommend|related|sponsored|you may|also bought|recently viewed|猜你喜欢|推荐|相似商品", re.I)
_BLOCKED = re.compile(r"captcha|verify (?:that )?you are human|sign in to (?:continue|view)|log in to (?:continue|view)|人机验证|请先登录|完成验证码", re.I)


def _str(value, limit=300):
    if not isinstance(value, str):
        return ""
    # Public titles/specifications commonly contain layout line breaks. Keep
    # the text while normalizing it to the catalog's single-line field format.
    value = re.sub(r"\s+", " ", value).strip()
    return "".join(char for char in value if ord(char) >= 32 and ord(char) != 127)[:limit]


def _id(value):
    if isinstance(value, bool):
        return ""
    value = str(value) if isinstance(value, int) else _str(value, 60)
    return value if _ID.fullmatch(value) else ""


def _field(obj, *names):
    for name in names:
        value = obj.get(name)
        if value is not None and value != "":
            return value
    return None


def _url(value, base="", *, temu=False):
    if not isinstance(value, str) or not value or len(value) > 4000 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return ""
    try:
        p = urlsplit(urljoin(base, value))
        host = (p.hostname or "").rstrip(".").lower()
        if p.scheme != "https" or not host or p.username is not None or p.password is not None or p.port not in {None, 443}:
            return ""
        if temu and not (host == "temu.com" or host.endswith(".temu.com")):
            return ""
        # Image fetchers must additionally resolve/pin public DNS themselves.
        if not temu and (host == "localhost" or "." not in host or host.endswith((".local", ".internal", ".lan"))):
            return ""
        return urlunsplit(("https", host, p.path or "/", p.query, ""))
    except (ValueError, UnicodeError):
        return ""


def product_id(url):
    url = _url(url, temu=True)
    if not url:
        return ""
    p = urlsplit(url)
    match = re.search(r"-g-(\d+)\.html(?:$|/)", p.path)
    ids = parse_qs(p.query).get("goods_id", [])
    if len(set(ids)) > 1 or (match and ids and ids[0] != match.group(1)):
        return ""
    return _id(match.group(1) if match else ids[0] if ids else "")


def canonical_product_url(url):
    """Keep site/path and product/variant identity, discard referrer/session data."""
    url = _url(url, temu=True)
    if not product_id(url):
        return ""
    p = urlsplit(url)
    query = parse_qs(p.query)
    keep = [(key, query[key][0]) for key in ("goods_id", "sku_id") if len(query.get(key, [])) == 1 and _id(query[key][0])]
    return urlunsplit(("https", p.hostname, p.path, urlencode(keep), ""))


def canonical_store_url(url):
    url = _url(url, temu=True)
    if not url:
        raise ValueError("请填写 Temu 店铺的 HTTPS 前端链接。")
    p = urlsplit(url)
    values = parse_qs(p.query).get("mall_id", [])
    if not p.path.rstrip("/").endswith("/mall.html") or len(values) != 1 or not _id(values[0]):
        raise ValueError("店铺链接缺少有效的 mall_id，不能用商品或登录页面代替。")
    return {"platform": "Temu", "store_id": values[0], "url": "https://www.temu.com/mall.html?" + urlencode({"mall_id": values[0]})}


def _walk(value):
    stack = [(value, 0)]
    count = 0
    while stack and count < MAX_NODES:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH:
            continue
        if isinstance(node, dict):
            yield node
            stack.extend((v, depth + 1) for v in reversed(list(node.values())[:1500]) if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in reversed(node[:2000]) if isinstance(v, (dict, list)))


def _documents(snapshot):
    """Parse JSON only. A rawData assignment may contain JSON, never JS code."""
    total = 0
    entries = list(snapshot.get("json_ld", []))[:12] if isinstance(snapshot.get("json_ld"), list) else []
    scripts = snapshot.get("scripts", [])
    if isinstance(scripts, list):
        entries.extend(item.get("text", "") if isinstance(item, dict) else item for item in scripts[:MAX_SCRIPTS])
    decoder = json.JSONDecoder(parse_float=Decimal)
    for raw in entries[:MAX_SCRIPTS]:
        if not isinstance(raw, str) or len(raw) > MAX_JSON_CHARS or total + len(raw) > MAX_JSON_CHARS:
            continue
        total += len(raw)
        raw = raw.strip()
        assignment = re.match(r"^(?:window\.)?(?:rawData|__INITIAL_STATE__|__PRELOADED_STATE__)\s*=\s*", raw)
        if assignment:
            raw = raw[assignment.end():]
        try:
            data, end = decoder.raw_decode(raw)
            if raw[end:].strip() not in {"", ";"}:
                continue
            if isinstance(data, (dict, list)):
                yield data
        except (ValueError, RecursionError, InvalidOperation):
            continue


def _goods_id(obj):
    return _id(_field(obj, "goods_id", "goodsId")) or product_id(_str(_field(obj, "url", "goodsUrl", "goods_url"), 4000))


def _mall_id(obj):
    direct = _id(_field(obj, "mall_id", "mallId", "store_id"))
    if direct:
        return direct
    for key in ("mall", "mallInfo", "mall_info", "seller", "store"):
        owner = obj.get(key)
        if isinstance(owner, dict):
            direct = _id(_field(owner, "mall_id", "mallId", "store_id"))
            if direct:
                return direct
            seller_url = _str(_field(owner, "url", "mallUrl", "mall_url"), 4000)
            try:
                return canonical_store_url(seller_url)["store_id"]
            except ValueError:
                pass
    return ""


def _mall_name(obj):
    direct = _str(_field(obj, "mallName", "mall_name", "storeName", "store_name"), 160)
    if direct:
        return direct
    for key in ("mall", "mallInfo", "mall_info", "seller", "store"):
        owner = obj.get(key)
        if isinstance(owner, dict):
            direct = _str(_field(owner, "mallName", "mall_name", "storeName", "store_name", "name"), 160)
            if direct:
                return direct
    return ""


def _name(obj):
    return _str(_field(obj, "goods_name", "goodsName", "name", "title"), 500)


def _image(obj, base):
    value = _field(obj, "image", "image_url", "imageUrl", "thumb_url", "thumbUrl", "hdThumbUrl", "hd_thumb_url")
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = _field(value, "url", "contentUrl")
    return _url(value, base)


def _price(value, currency):
    """Accept explicit decimal major units only. No unknown cents conversion."""
    currency = _str(currency, 8).upper()
    if currency not in _CURRENCIES or isinstance(value, bool):
        return None, ""
    if isinstance(value, (int, Decimal)):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,9}(?:\.\d{1,4})?", value.strip()):
        return None, ""
    try:
        amount = Decimal(value.strip())
        if amount <= 0 or not amount.is_finite():
            return None, ""
        return format(amount.normalize(), "f"), currency
    except InvalidOperation:
        return None, ""


def _spec(obj):
    pieces = []
    values = _field(obj, "specs", "specList", "spec_list", "skuSpecs", "sku_specs")
    if isinstance(values, list):
        for item in values[:20]:
            if isinstance(item, dict):
                label = _str(_field(item, "spec_key", "specKey", "name"), 60)
                value = _str(_field(item, "spec_value", "specValue", "value"), 120)
                if value:
                    pieces.append((label + ": " if label else "") + value)
            elif isinstance(item, str):
                pieces.append(_str(item, 180))
    for key in ("color", "size", "pattern", "material"):
        value = _str(obj.get(key), 120)
        if value:
            pieces.append(key + ": " + value)
    return " / ".join(dict.fromkeys(pieces))[:500] or _str(_field(obj, "spec", "variant_name", "variantName", "skuName", "sku_name", "name"), 500)


def _identity_matches(value, product_url, public_id, merchant_sku, *, offer=False):
    """Explicit identifiers must corroborate the containing variant.

    Missing identifiers on a nested Offer are allowed, but an explicit unknown
    or conflicting identifier/URL cannot be authenticated by its parent SKU.
    Check every alias before canonicalization can discard a conflicting query.
    """
    gid = product_id(product_url)
    variant_reference = False
    for key in ("sku_id", "skuId", "variant_id", "variantId"):
        if key in value and value[key] not in (None, ""):
            if not public_id or _id(value[key]) != public_id:
                return False
            variant_reference = True
    if "sku" in value and value["sku"] not in (None, ""):
        sku = value["sku"]
        if not isinstance(sku, str) or len(sku) > 200 or not merchant_sku or sku.strip() != merchant_sku:
            return False
        variant_reference = True
    for key in ("goods_id", "goodsId"):
        if key in value and value[key] not in (None, ""):
            if not gid or _id(value[key]) != gid:
                return False
    has_url = False
    for key in ("url", "goodsUrl", "goods_url"):
        if key not in value or value[key] in (None, ""):
            continue
        has_url = True
        url = _url(value[key], product_url, temu=True)
        if not url or not gid or product_id(url) != gid:
            return False
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        for name in ("sku_id", "skuId", "variant_id", "variantId"):
            if name in query:
                ids = query[name]
                if len(ids) != 1 or not public_id or _id(ids[0]) != public_id:
                    return False
                variant_reference = True
    return not (offer and has_url and not variant_reference)


def _currency_consistent(value, currency):
    # Currency aliases on one amount are corroborating evidence, not fallbacks
    # that may silently override each other. Never infer cents/minor units.
    return all(_str(value[key], 8).upper() == currency and currency in _CURRENCIES
               for key in ("priceCurrency", "currency", "currencyCode", "currency_code")
               if key in value and value[key] not in (None, ""))


def _variant(obj, product_url, *, source="page_json"):
    public_id = _id(_field(obj, "sku_id", "skuId", "variant_id", "variantId"))
    merchant_sku = _str(obj.get("sku"), 200)  # Schema.org's declared SKU, not goods_id.
    spec = _spec(obj)
    currency = _field(obj, "priceCurrency", "currency", "currencyCode", "currency_code")
    # salePrice/retailPrice integers are intentionally unsupported: unit and
    # eligibility semantics have not been verified against this store's page.
    price, currency = _price(obj.get("price"), currency)
    if (not _currency_consistent(obj, currency) or obj.get("struck") or obj.get("priceType") or
            any(key in obj for key in ("lowPrice", "highPrice", "eligibleCustomerType", "eligibleQuantity", "priceUnit", "price_unit", "amountUnit", "amount_unit"))):
        price, currency = None, ""
    offers = obj.get("offers")
    offers = [offers] if isinstance(offers, dict) else offers[:12] if isinstance(offers, list) else []
    valid = set()
    for offer in offers:
        if not isinstance(offer, dict) or "AggregateOffer" in str(offer.get("@type", "")):
            continue
        if not _identity_matches(offer, product_url, public_id, merchant_sku, offer=True):
            continue
        if offer.get("struck") or offer.get("priceType") or any(key in offer for key in ("lowPrice", "highPrice", "eligibleCustomerType", "eligibleQuantity", "priceUnit", "price_unit", "amountUnit", "amount_unit")):
            continue
        candidate = _price(offer.get("price"), offer.get("priceCurrency"))
        if candidate[0] and _currency_consistent(offer, candidate[1]):
            valid.add(candidate)
    if valid:
        if len(valid) == 1 and (price is None or (price, currency) in valid):
            price, currency = next(iter(valid))
        else:
            price, currency = None, ""
    if not _identity_matches(obj, product_url, public_id, merchant_sku):
        price, currency = None, ""
    url = canonical_product_url(_str(obj.get("url"), 4000)) or product_url
    if product_id(url) != product_id(product_url):
        # An offer for another product cannot become a child of this product.
        price, currency = None, ""
        url = product_url
    if public_id:
        p = urlsplit(url)
        q = parse_qs(p.query)
        q["sku_id"] = [public_id]
        url = urlunsplit((p.scheme, p.netloc, p.path, urlencode([(k, v[0]) for k, v in q.items()]), ""))
    return {"public_variant_id": public_id, "merchant_sku": merchant_sku, "spec": spec,
            "price": price, "currency": currency, "url": url, "source": source,
            "price_verified": bool(price), "warnings": [] if price else ["variant_price_missing"]}


def parse_store_snapshot(snapshot, store_id, *, seen_ids=(), limit=MAX_PRODUCTS):
    """Discover candidates; detail pages must still establish seller membership."""
    store_id = _id(store_id)
    if not store_id or not isinstance(snapshot, dict):
        raise ValueError("店铺快照或店铺编号无效。")
    expected = canonical_store_url(_str(snapshot.get("url"), 4000))
    if expected["store_id"] != store_id:
        raise ValueError("当前页面不属于目标店铺。")
    limit = max(1, min(MAX_PRODUCTS, int(limit)))
    seen = set(str(value) for value in list(seen_ids)[:10000])
    items = []
    by_id = {}
    generated_urls = set()
    warnings = []

    def add(value, *, related=False, source="page_json"):
        if related:
            return
        gid = _goods_id(value)
        owner = _mall_id(value)
        if not gid or gid in seen or (owner and owner != store_id):
            return
        supplied_url = canonical_product_url(_str(_field(value, "url", "goodsUrl", "goods_url"), 4000))
        url = supplied_url or "https://www.temu.com/goods.html?goods_id=" + gid
        if product_id(url) != gid:
            return
        if gid in by_id:
            old = by_id[gid]
            if supplied_url and gid in generated_urls:
                old["url"] = supplied_url
                generated_urls.discard(gid)
            old["membership_verified"] = old["membership_verified"] or owner == store_id
            old["name"] = old["name"] or _name(value)
            old["image"] = old["image"] or _image(value, url)
            return
        if len(items) >= limit:
            return
        item = {"platform": "Temu", "store_id": store_id, "product_id": gid,
                      "url": url, "name": _name(value), "image": _image(value, url),
                      "membership_verified": owner == store_id, "source": source}
        by_id[gid] = item
        if not supplied_url:
            generated_urls.add(gid)
        items.append(item)

    # Structured products require explicit owner evidence, so a recommendation
    # list in hydration data never establishes shop membership accidentally.
    for doc in _documents(snapshot):
        for node in _walk(doc):
            if _mall_id(node) == store_id:
                add(node)
    links = snapshot.get("links", [])
    for link in links[:1000] if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        context = " ".join(_str(link.get(k), 500) for k in ("context", "section", "kind"))
        add(link, related=bool(link.get("related")) or bool(_RELATED.search(context)), source="visible_store_link")
    if any(not item["membership_verified"] for item in items):
        warnings.append("store_membership_needs_detail_verification")
    if not items:
        warnings.append("no_owned_products_found")
    name = _str(snapshot.get("store_name"), 160)
    if not name:
        title = _str(snapshot.get("title"), 300)
        match = re.match(r"^Temu\s*[|｜]\s*(.+)$", title, re.I)
        name = _str(match.group(1), 160) if match else ""
    return {"platform": "Temu", "store_id": store_id, "store_name": name, "store_url": expected["url"],
            "items": items, "warnings": warnings, "limit": limit,
            "has_more": len(items) == limit, "discovery_complete": False}


def parse_product_snapshot(snapshot, store_id, *, store_name="", expected_product_id=""):
    if not isinstance(snapshot, dict) or not _id(store_id):
        raise ValueError("商品快照或店铺编号无效。")
    url = canonical_product_url(_str(snapshot.get("url"), 4000))
    gid = product_id(url)
    if not gid or (expected_product_id and gid != str(expected_product_id)):
        raise ValueError("当前页面不是目标商品，不能导入。")
    if _BLOCKED.search(_str(snapshot.get("title"), 500) + " " + _str(snapshot.get("text"), 2000)):
        raise ValueError("前端页面要求登录或验证，请在专用浏览器正常完成后重新读取。")
    warnings, roots = [], []
    for doc in _documents(snapshot):
        for node in _walk(doc):
            if _goods_id(node) == gid and (_name(node) or _field(node, "skuList", "sku_list", "hasVariant")):
                roots.append(node)
    wrong_owner = any(_mall_id(node) and _mall_id(node) != str(store_id) for node in roots)
    if wrong_owner:
        raise ValueError("商品页面的店铺编号与目标店铺不符，已排除其他店铺商品。")
    owned = [node for node in roots if _mall_id(node) == str(store_id)]
    root = max(owned or roots, key=lambda obj: bool(_field(obj, "skuList", "sku_list", "hasVariant"))) if roots else {}
    membership = bool(owned)
    # The visible seller link is valid membership evidence only when anchored
    # in the product's seller area by SNAPSHOT_JS, not a random page hyperlink.
    seller = snapshot.get("seller")
    if isinstance(seller, dict) and seller.get("scope") == "product_seller":
        seller_id = _mall_id(seller)
        if seller_id and seller_id != str(store_id):
            raise ValueError("可见商品店铺与目标店铺不符。")
        membership = membership or seller_id == str(store_id)
    if not membership:
        warnings.append("store_membership_unverified")
    name = _name(root) or _str(snapshot.get("product_title"), 500)
    image = _image(root, url) or _url(_str(snapshot.get("product_image"), 4000), url)
    if not name:
        warnings.append("product_name_missing")
    if not image:
        warnings.append("main_image_missing")
    raw_variants = _field(root, "skuList", "sku_list", "hasVariant")
    variants = []
    if isinstance(raw_variants, list):
        for raw in raw_variants[:MAX_VARIANTS]:
            if isinstance(raw, dict):
                variants.append(_variant(raw, url))
        if len(raw_variants) > MAX_VARIANTS:
            warnings.append("variant_limit_reached")
    # Explicit rendered rows (not a single selected page price) may fill
    # missing prices, but never invent a Cartesian product of option buttons.
    dom_variants = snapshot.get("variants", [])
    for raw in dom_variants[:MAX_VARIANTS] if isinstance(dom_variants, list) else []:
        if isinstance(raw, dict) and raw.get("scope") == "variant_row" and not raw.get("struck") and not raw.get("related"):
            variants.append(_variant(raw, url, source="visible_variant_row"))
    unique = {}
    for variant in variants:
        key = variant["public_variant_id"] or variant["merchant_sku"] or variant["spec"]
        if not key:
            warnings.append("variant_identity_missing")
            continue
        if key in unique:
            old = unique[key]
            if old["price"] and variant["price"] and (old["price"], old["currency"]) != (variant["price"], variant["currency"]):
                old["price"], old["currency"], old["price_verified"] = None, "", False
                old["warnings"] = ["variant_price_conflict"]
                warnings.append("variant_price_conflict")
            elif not old["price"] and variant["price"] and "variant_price_conflict" not in old["warnings"]:
                unique[key] = variant
        elif len(unique) < MAX_VARIANTS:
            unique[key] = variant
    variants = list(unique.values())
    declared = _field(root, "skuCount", "sku_count", "variantCount", "variant_count")
    declared = int(declared) if not isinstance(declared, bool) and isinstance(declared, (int, str)) and str(declared).isdigit() and 0 < int(declared) <= 100000 else None
    if not variants:
        warnings.append("variants_missing")
    if any(not item["price_verified"] for item in variants):
        warnings.append("variant_prices_incomplete")
    complete = bool(variants and declared == len(variants) and all(item["price_verified"] and item["spec"] for item in variants))
    if not complete:
        warnings.append("variant_list_unverified")
    return {"platform": "Temu", "store_id": str(store_id), "store_name": _mall_name(root) or _str(store_name, 160),
            "product_id": gid, "merchant_skc": "", "url": url,
            "name": name, "image": image, "variants": variants,
            "membership_verified": membership, "completeness": "complete" if complete else "partial",
            "declared_variant_count": declared, "warnings": list(dict.fromkeys(warnings)),
            "importable": bool(membership and name and image and variants)}


SNAPSHOT_JS = r'''() => {
  const root=document.querySelector('main,[role="main"]')||document.body;
  const related=/recommend|related|sponsored|you.may|also.bought|猜你喜欢|推荐|相似商品/i;
  const boundary=/^(?:为您精选|为你精选|为你推荐|猜你喜欢|推荐商品|相似商品|selected for you|picked for you|recommended for you|you may also like|you might also like)\s*[:：]?$/i;
  const plugin=/temu[-_ ]?(?:helper|assistant)|selection[-_ ]?(?:helper|assistant)|mango|芒果|选品助手|供货助手|chrome-extension|extension[-_ ]?(?:root|panel)/i;
  const visible=e=>{if(!e||e.closest('[hidden],[aria-hidden="true"],nav,header,footer,form,input,textarea,select,[contenteditable]:not([contenteditable="false"])')||!e.getClientRects().length)return false;for(let n=e,d=0;n&&d++<20;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;if(n===root)break;}return true;};
  const context=e=>{const out=[];for(let n=e,d=0;n&&d++<6;n=n.parentElement){out.push(String(n.id||'').slice(0,150),String(n.className||'').slice(0,200),n.getAttribute('aria-label')||'');if(n===root)break;}return out.join(' ').slice(0,800);};
  const links=[];let visited=0,recommendation_boundary_seen=false;const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);let a;
  while((a=walker.nextNode())){if(++visited>25000||links.length>=400)break;if(!visible(a))continue;const ctx=context(a);if(plugin.test(ctx))continue;const text=(a.innerText||'').trim();if((a.childElementCount<2||a.matches('h2,h3,[role="heading"]'))&&text.length<100&&boundary.test(text)){recommendation_boundary_seen=true;break;}if(!a.matches('a[href]'))continue;const href=a.href||'';if(!/-g-\d+\.html|[?&]goods_id=\d+/.test(href)||related.test(ctx))continue;const img=a.querySelector('img');let owner='';for(let n=a,d=0;n&&d++<5;n=n.parentElement){owner=n.getAttribute('data-mall-id')||n.getAttribute('data-mall_id')||'';if(owner)break;}links.push({url:href.slice(0,4000),name:(img?.alt||a.getAttribute('aria-label')||a.getAttribute('title')||a.innerText||'').trim().slice(0,500),image:img?.currentSrc||img?.src||'',mall_id:owner,context:ctx});}
  const scripts=[];let chars=0;
  for(const s of document.scripts){if(scripts.length>=20)break;const t=s.textContent||'';if(s.src||plugin.test(s.id+' '+s.className)||!t||t.length>2000000||chars+t.length>2000000)continue;if(s.type==='application/ld+json'||s.type==='application/json'||s.id==='__NEXT_DATA__'||/^\s*(?:window\.)?(?:rawData|__INITIAL_STATE__|__PRELOADED_STATE__)\s*=/.test(t)){scripts.push({text:t,type:s.type,id:s.id});chars+=t.length;}}
  const heading=[...root.querySelectorAll('h1,[itemprop="name"]')].find(visible);
  const storeHeading=[...root.querySelectorAll('h1,h2,[role="heading"]')].find(e=>visible(e)&&!plugin.test(context(e))&&!related.test(context(e))&&((e.innerText||'').trim().length<160));
  const storeTitle=(document.title||'').match(/^Temu\s*[|｜]\s*(.+)$/i);
  const store_name=/\/mall\.html(?:\?|$)/.test(location.href)?((storeTitle?.[1]||storeHeading?.innerText||'').trim().slice(0,160)):'';
  const image=document.querySelector('meta[property="og:image"]')?.content||'';
  let text='',node,textNodes=0;const textWalker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);while((node=textWalker.nextNode())&&textNodes++<9000&&text.length<2000){const e=node.parentElement;if(!visible(e)||e.closest('script,style,noscript,template')||plugin.test(context(e)))continue;const t=(node.textContent||'').trim();if(boundary.test(t))break;if(t)text+=t+'\n';}
  return {url:location.href.slice(0,4000),title:document.title.slice(0,500),store_name,text:text.slice(0,2000),product_title:(heading?.innerText||'').slice(0,500),product_image:image,links,scripts,variants:[],recommendation_boundary_seen};
}'''


class _HTMLSnapshot(HTMLParser):
    """Fixture/offline adapter. Visibility cannot be established from HTML."""
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.snapshot = {"url": url, "scripts": [], "links": [], "variants": []}
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and len(self.snapshot["scripts"]) < MAX_SCRIPTS:
            self.script = {"type": attrs.get("type", ""), "id": attrs.get("id", ""), "text": ""}

    def handle_data(self, text):
        if self.script is not None and len(self.script["text"]) + len(text) <= MAX_JSON_CHARS:
            self.script["text"] += text

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.snapshot["scripts"].append(self.script)
            self.script = None


def snapshot_from_html(html, url):
    if not isinstance(html, str) or len(html) > MAX_JSON_CHARS:
        raise ValueError("网页内容超过解析上限。")
    parser = _HTMLSnapshot(url)
    parser.feed(html)
    return parser.snapshot
