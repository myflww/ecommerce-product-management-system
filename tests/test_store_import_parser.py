"""Synthetic frontend evidence only; no live browser/store/network access."""
import copy
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("store_import_parser", Path(__file__).resolve().parents[1] / "store_import_parser.py")
parser = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parser)

STORE = "700000000000003"
GOODS = "700000000000005"
URL = "https://www.temu.com/us-en/candle-g-" + GOODS + ".html"


def product(**changes):
    return {"goodsId": GOODS, "mallId": STORE, "goodsName": "Wax candle",
            "image": "https://img.kwcdn.com/product/candle.jpg", "skuCount": 2,
            "skuList": [
                {"skuId": "1001", "specs": [{"spec_key": "Color", "spec_value": "Red"}], "price": "8.15", "currency": "USD"},
                {"skuId": "1002", "specs": [{"spec_key": "Color", "spec_value": "Blue"}], "price": "10.54", "currency": "USD"}], **changes}


def snapshot(data=None, **changes):
    return {"url": URL, "title": "Wax candle - Temu", "scripts": [{"text": json.dumps(data if data is not None else product())}], **changes}


def parse(data=None, **changes):
    return parser.parse_product_snapshot(snapshot(data, **changes), STORE, store_name="Fixture Candle Shop", expected_product_id=GOODS)


def test_precise_product_store_public_variant_identity_and_prices():
    result = parse()
    assert result["store_id"] == STORE
    assert result["store_name"] == "Fixture Candle Shop"
    assert result["product_id"] == GOODS
    assert result["merchant_skc"] == ""
    assert result["membership_verified"] is True
    assert result["importable"] is True
    assert result["completeness"] == "complete"
    assert [v["price"] for v in result["variants"]] == ["8.15", "10.54"]
    assert [v["public_variant_id"] for v in result["variants"]] == ["1001", "1002"]
    assert all(v["merchant_sku"] == "" for v in result["variants"])
    assert result["variants"][0]["url"] == URL + "?sku_id=1001"
    assert result["variants"][0]["spec"] == "Color: Red"


def test_referrer_session_data_not_retained_in_canonical_frontend_links():
    raw = "https://www.temu.com/mall.html?mall_id=" + STORE + "&no_cache_id=private&_x_sessn_id=private"
    assert parser.canonical_store_url(raw)["url"] == "https://www.temu.com/mall.html?mall_id=" + STORE
    result = parse(url=URL + "?sku_id=1001&_x_sessn_id=private&refer_page_id=private")
    assert result["url"] == URL + "?sku_id=1001"
    assert all("private" not in v["url"] for v in result["variants"])


@pytest.mark.parametrize("url", [
    "https://eviltemu.com/mall.html?mall_id=" + STORE,
    "https://temu.com.evil.com/mall.html?mall_id=" + STORE,
    "http://www.temu.com/mall.html?mall_id=" + STORE,
    "https://username:password@www.temu.com/mall.html?mall_id=" + STORE,
    "https://www.temu.com:8443/mall.html?mall_id=" + STORE,
    "https://www.temu.com/mall.html?mall_id=" + STORE + "&mall_id=1",
    "https://www.temu.com/login.html?mall_id=" + STORE,
    "https://www.temu.com/mall.html?mall_id=not-a-number",
])
def test_store_url_rejects_non_target_urls(url):
    with pytest.raises(ValueError):
        parser.canonical_store_url(url)


def test_distinct_goods_id_and_sku_ids_and_conflicting_id_rejection():
    assert parser.product_id(URL + "?sku_id=1001") == GOODS
    assert parser.product_id(URL + "?goods_id=1") == ""
    assert parser.product_id("https://www.temu.com/goods.html?goods_id=" + GOODS) == GOODS
    with pytest.raises(ValueError):
        parse(url="https://www.temu.com/wrong-g-999.html")


def test_variant_missing_price_not_filled_from_parent_starting_price():
    p = product(price="3.00", currency="USD", lowPrice="3.00", highPrice="10.54")
    del p["skuList"][0]["price"]
    result = parse(p)
    assert result["variants"][0]["price"] is None
    assert result["variants"][1]["price"] == "10.54"
    assert result["completeness"] == "partial"
    assert "variant_prices_incomplete" in result["warnings"]


def test_unverified_cents_sale_price_is_never_assumed_major_units():
    p = product()
    p["skuList"][0].pop("price")
    p["skuList"][0]["salePrice"] = 815
    assert parse(p)["variants"][0]["price"] is None


@pytest.mark.parametrize("value", ["From 3", "3-6", "8,15", "-2", "NaN", "Infinity", True, 0, {"value": 10}, [1]])
def test_ambiguous_invalid_and_range_prices_are_not_imported(value):
    p = product()
    p["skuList"][0]["price"] = value
    assert parse(p)["variants"][0]["price"] is None


def test_currency_cannot_be_inferred_from_dollar_sign_or_user_preference():
    p = product()
    p["skuList"][0]["currency"] = "$"
    assert parse(p)["variants"][0]["price"] is None
    assert parse(p)["variants"][0]["currency"] == ""


def test_has_variant_schema_keeps_child_offers_and_merchant_declared_sku():
    p = {"@type": "ProductGroup", "url": URL, "name": "Wax candle", "image": "https://img.kwcdn.com/candle.jpg",
         "seller": {"url": "https://www.temu.com/mall.html?mall_id=" + STORE},
         "hasVariant": [
             {"@type": "Product", "sku": "MERCHANT-RED", "color": "red", "offers": {"@type": "Offer", "price": "9.1", "priceCurrency": "USD"}},
             {"@type": "Product", "sku": "MERCHANT-BLUE", "color": "blue", "offers": {"@type": "AggregateOffer", "lowPrice": "2", "highPrice": "10", "priceCurrency": "USD"}}]}
    result = parse(p)
    assert result["variants"][0]["merchant_sku"] == "MERCHANT-RED"
    assert result["variants"][0]["public_variant_id"] == ""
    assert result["variants"][0]["price"] == "9.1"
    assert result["variants"][1]["price"] is None
    assert result["completeness"] == "partial"


def test_multiple_offer_amounts_for_same_sku_are_ambiguous():
    p = product()
    p["skuList"][0]["offers"] = [{"price": "5", "priceCurrency": "USD"}, {"price": "7", "priceCurrency": "USD"}]
    assert parse(p)["variants"][0]["price"] is None


def test_member_coupon_quantity_offer_excluded():
    p = product()
    p["skuList"][0].pop("price")
    p["skuList"][0]["offers"] = [{"price": "5", "priceCurrency": "USD", "eligibleQuantity": {"minValue": 10}}]
    assert parse(p)["variants"][0]["price"] is None


@pytest.mark.parametrize("identity", [
    {"skuId": "1002"}, {"sku_id": "1002"}, {"variantId": "1002"},
    {"sku": "UNKNOWN-MERCHANT-CODE"}, {"skuId": "not-a-public-id"},
    {"skuId": "1001", "sku_id": "1002"},
    {"goods_id": "999"}, {"goodsId": GOODS, "goods_id": "999"},
    {"url": "https://www.temu.com/other-g-999.html?sku_id=1001"},
    {"url": "https://www.temu.com/unidentified-offer.html"},
    {"url": URL + "?sku_id=1002"},
    {"url": URL + "?skuId=1002"},
    {"url": URL + "?sku_id=1001&sku_id=1001"},
    {"url": URL + "?sku_id="},
    {"url": URL + "?sku_id=not-a-public-id"},
    {"url": URL},  # A generic product URL does not identify this child offer.
])
def test_offer_explicit_identity_must_match_its_containing_variant(identity):
    p = product()
    p["skuList"][0].pop("price")
    p["skuList"][0]["offers"] = {"price": "25.5", "priceCurrency": "USD", **identity}
    result = parse(p)
    assert result["variants"][0]["public_variant_id"] == "1001"
    assert result["variants"][0]["price"] is None
    assert result["variants"][0]["price_verified"] is False
    assert result["variants"][1]["price"] == "10.54"
    assert result["completeness"] == "partial"


@pytest.mark.parametrize("identity", [
    {"skuId": "1001", "sku_id": "1001", "goodsId": GOODS},
    {"url": URL + "?sku_id=1001"},
    {"skuId": "1001", "url": URL},
    {"url": "?sku_id=1001"},
])
def test_matching_offer_identity_preserves_exact_variant_price(identity):
    p = product()
    p["skuList"][0].pop("price")
    p["skuList"][0]["offers"] = {"price": "25.5", "priceCurrency": "USD", **identity}
    row = parse(p)["variants"][0]
    assert row["price"] == "25.5"
    assert row["currency"] == "USD"
    assert row["url"] == URL + "?sku_id=1001"


def test_rejected_offer_does_not_erase_an_independent_direct_child_price():
    p = product()
    p["skuList"][0]["offers"] = {"skuId": "1002", "price": "25.5", "priceCurrency": "USD"}
    assert parse(p)["variants"][0]["price"] == "8.15"


@pytest.mark.parametrize("identity", [
    {"sku_id": "1002"}, {"goodsId": "999"},
    {"url": URL + "?sku_id=1002"},
    {"url": URL + "?sku_id=1001&sku_id=1001"},
])
def test_child_own_identity_conflict_is_not_hidden_by_rewriting_its_url(identity):
    p = product()
    p["skuList"][0].update(identity)
    row = parser._variant(p["skuList"][0], URL)
    assert row["price"] is None
    assert row["price_verified"] is False


def test_offer_declared_merchant_sku_is_compared_exactly():
    p = product()
    p["skuList"][0].update(sku="MERCHANT-RED", offers={"sku": "MERCHANT-BLUE", "price": "25.5", "priceCurrency": "USD"})
    p["skuList"][0].pop("price")
    assert parse(p)["variants"][0]["price"] is None
    p["skuList"][0]["offers"]["sku"] = "MERCHANT-RED"
    assert parse(p)["variants"][0]["price"] == "25.5"


@pytest.mark.parametrize("offer_fields", [
    {"currency": "EUR"}, {"currencyCode": "EUR"},
    {"priceUnit": "cents"}, {"price_unit": "minor"},
    {"salePrice": 2550, "price": None},
])
def test_offer_currency_and_unverified_price_units_are_not_guessed(offer_fields):
    p = product()
    p["skuList"][0].pop("price")
    p["skuList"][0]["offers"] = {"skuId": "1001", "price": "25.5", "priceCurrency": "USD", **offer_fields}
    assert parse(p)["variants"][0]["price"] is None


def test_duplicate_conflicting_variants_flag_no_arbitrary_lowest_price():
    p = product()
    p["skuList"].append({**p["skuList"][0], "price": "999"})
    result = parse(p)
    assert len(result["variants"]) == 2
    assert result["variants"][0]["price"] is None
    assert "variant_price_conflict" in result["warnings"]


def test_rendered_variant_rows_can_supply_missing_price_but_not_unscoped_page_price():
    p = product()
    p["skuList"][0].pop("price")
    result = parse(p, variants=[
        {"scope": "page_price", "skuId": "1001", "price": "1", "currency": "USD"},
        {"scope": "variant_row", "skuId": "1001", "spec": "Color: Red", "price": "8.15", "currency": "USD"},
        {"scope": "variant_row", "skuId": "1002", "price": "1", "currency": "USD", "struck": True}])
    assert [v["price"] for v in result["variants"]] == ["8.15", "10.54"]


def test_declared_count_missing_or_mismatch_never_claims_all_skus_collected():
    p = product()
    p.pop("skuCount")
    assert parse(p)["completeness"] == "partial"
    assert parse(product(skuCount=3))["completeness"] == "partial"


def test_no_merchant_codes_inferred_from_public_ids():
    p = product()
    p["skuList"][0]["sku_id"] = "7000000000000000001"
    p["skuList"][0].pop("skuId")
    result = parse(p)
    assert result["variants"][0]["public_variant_id"] == "7000000000000000001"
    assert result["variants"][0]["merchant_sku"] == ""
    assert result["merchant_skc"] == ""


def test_wrong_store_detail_is_rejected():
    with pytest.raises(ValueError, match="店铺编号"):
        parse(product(mallId="999"))


def test_absent_seller_membership_not_guessed_from_selected_shop():
    p = product()
    p.pop("mallId")
    result = parse(p)
    assert result["membership_verified"] is False
    assert result["importable"] is False
    assert "store_membership_unverified" in result["warnings"]
    assert parse(p, seller={"scope": "product_seller", "mall_id": STORE})["membership_verified"] is True
    assert parse(p, seller={"scope": "random_link", "mall_id": STORE})["membership_verified"] is False


def test_other_product_recommendation_data_does_not_pollute_detail():
    recommendation = product(goodsId="999", mallId="999", goodsName="Another seller product")
    result = parse({"page": {"goods": product()}, "recommendations": [recommendation]})
    assert result["name"] == "Wax candle"
    assert len(result["variants"]) == 2


@pytest.mark.parametrize("text", ["Verify you are human", "Please sign in to continue", "请先登录后查看", "请完成人机验证"])
def test_challenge_or_login_is_explicit_and_not_zero_products_success(text):
    with pytest.raises(ValueError, match="登录或验证"):
        parse(text=text)


def test_store_discovery_ignores_recommendations_foreign_sellers_and_seen_ids():
    s = snapshot({"storeProducts": [product(), product(goodsId="777", mallId="999")],
                  "recommendations": [product(goodsId="888", mallId="999")]},
                 url="https://www.temu.com/mall.html?mall_id=" + STORE,
                 links=[
                     {"url": "https://www.temu.com/x-g-111.html", "context": "recommendations"},
                     {"url": "https://www.temu.com/x-g-222.html", "mall_id": "999"},
                     {"url": "https://www.temu.com/x-g-333.html", "context": "store products", "name": "Needs seller verification"},
                     {"url": URL, "mall_id": STORE}])
    result = parser.parse_store_snapshot(s, STORE)
    assert [x["product_id"] for x in result["items"]] == [GOODS, "333"]
    assert result["items"][0]["membership_verified"] is True
    assert result["items"][1]["membership_verified"] is False
    again = parser.parse_store_snapshot(s, STORE, seen_ids=[GOODS, "333"])
    assert again["items"] == []


def test_discovery_limit_is_always_twenty_and_server_seen_ids_deduplicate():
    s = snapshot([], url="https://www.temu.com/mall.html?mall_id=" + STORE,
                 links=[{"url": f"https://www.temu.com/x-g-{i}.html"} for i in range(1, 100)])
    result = parser.parse_store_snapshot(s, STORE, limit=999)
    assert len(result["items"]) == 20
    assert result["has_more"] is True
    assert result["discovery_complete"] is False


def test_untrusted_assignments_are_json_only_never_evaluated():
    source = "window.rawData=" + json.dumps(product()) + ";"
    assert parse(scripts=[{"text": source}])["name"] == "Wax candle"
    for script in [source + " stealCookies();", "window.rawData=JSON.parse('" + json.dumps(product()) + "');", "window.rawData=(function(){return {};})();"]:
        result = parse(scripts=[{"text": script}])
        assert result["variants"] == []


def test_offline_html_adapter_only_extracts_data_not_hidden_card_or_form_text():
    html = '<input value="private"><script type="application/json">' + json.dumps(product()) + '</script><div hidden>USD 1</div>'
    s = parser.snapshot_from_html(html, URL)
    assert parser.parse_product_snapshot(s, STORE)["name"] == "Wax candle"
    assert "private" not in json.dumps(s)


def test_oversized_scripts_and_extreme_nested_json_are_bounded():
    result = parse(scripts=[{"text": " " * (parser.MAX_JSON_CHARS + 1)}])
    assert not result["variants"]
    nested = product()
    for _ in range(50):
        nested = {"data": nested}
    assert not parse(nested)["variants"]
    with pytest.raises(ValueError, match="上限"):
        parser.snapshot_from_html(" " * (parser.MAX_JSON_CHARS + 1), URL)


def test_variants_are_bounded():
    p = product(skuCount=300)
    p["skuList"] = [{"skuId": str(i + 1), "spec": f"Option {i + 1}", "price": "5", "currency": "USD"} for i in range(300)]
    result = parse(p)
    assert len(result["variants"]) == 200
    assert "variant_limit_reached" in result["warnings"]
    assert result["completeness"] == "partial"


def dom_snapshot(tree):
    """Run the exported snapshot JS against ordered DOM fixtures, offline."""
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    source = r'''
const tree=JSON.parse(process.argv[1]);
const descendants=e=>e.children.flatMap(c=>[c,...(c.nodeType===1?descendants(c):[])]);
function match(e,s){
 if(s==='[contenteditable]:not([contenteditable="false"])')return Object.hasOwn(e.attrs,'contenteditable')&&e.attrs.contenteditable!=='false';
 const a=s.match(/^([a-z]+)?\[([^=\]]+)(?:="([^"]*)")?\]$/i);if(a)return (!a[1]||e.tagName===a[1].toUpperCase())&&Object.hasOwn(e.attrs,a[2])&&(a[3]===undefined||e.attrs[a[2]]===a[3]);
 return e.tagName===s.toUpperCase();
}
class E{
 constructor(d,p=null){this.nodeType=1;this.tagName=(d.tag||'div').toUpperCase();this.attrs=d.attrs||{};this.style=d.style||{};this.parentElement=p;this.children=(d.children||[]).map(c=>typeof c==='string'?{nodeType:3,textContent:c,parentElement:this}:new E(c,this));}
 get textContent(){return this.children.map(c=>c.textContent).join('');}get innerText(){return this.textContent;}
 get id(){return this.attrs.id||'';}get className(){return this.attrs.class||'';}get type(){return this.attrs.type||'';}
 get src(){return this.attrs.src||'';}get currentSrc(){return this.attrs.src||'';}get href(){return this.attrs.href||'';}get alt(){return this.attrs.alt||'';}
 get childElementCount(){return this.children.filter(c=>c.nodeType===1).length;}
 getAttribute(n){return this.attrs[n]??null;}
 matches(s){return s.split(',').some(s=>match(this,s.trim()));}
 closest(s){for(let e=this;e;e=e.parentElement)if(e.matches(s))return e;return null;}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 querySelectorAll(s){return descendants(this).filter(c=>c.nodeType===1&&c.matches(s));}
 getClientRects(){for(let p=this;p;p=p.parentElement)if(p.style.display==='none')return [];return [{}];}
}
const body=new E(tree);
globalThis.getComputedStyle=e=>({display:e.style.display||'block',visibility:e.style.visibility||'visible',opacity:e.style.opacity??'1'});
globalThis.NodeFilter={SHOW_ELEMENT:1,SHOW_TEXT:4};
globalThis.location={href:process.argv[2]};
globalThis.document={body,title:'Fixture Candle Shop',scripts:body.querySelectorAll('script'),querySelector:s=>body.matches(s)?body:body.querySelector(s),createTreeWalker:(e,f)=>{const nodes=descendants(e).filter(n=>n.nodeType===(f===4?3:1));let i=0;return {nextNode:()=>nodes[i++]||null};}};
const run=eval('('+process.argv[3]+')');process.stdout.write(JSON.stringify(run()));
'''
    completed = subprocess.run(["node", "-e", source, json.dumps(tree), "https://www.temu.com/mall.html?mall_id=" + STORE, parser.SNAPSHOT_JS], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def link(gid, **extra):
    return {"tag": "a", "attrs": {"href": f"https://www.temu.com/us-zh-Hans/candle-g-{gid}.html", **extra},
            "children": [{"tag": "img", "attrs": {"alt": "Wax candle", "src": "https://img.kwcdn.com/candle.jpg"}}]}


def test_actual_js_stops_links_at_for_you_boundary_after_store_products():
    s = dom_snapshot({"tag": "body", "children": [
        {"tag": "h1", "children": ["Fixture Candle Shop"]},
        link(GOODS), {"tag": "button", "children": ["查看更多"]},
        {"tag": "div", "children": [{"tag": "span", "children": ["为您精选"]}]},
        link("999"), link("888")]})
    assert len(s["links"]) == 1
    assert s["recommendation_boundary_seen"] is True
    assert parser.product_id(s["links"][0]["url"]) == GOODS
    assert [item["product_id"] for item in parser.parse_store_snapshot(s, STORE)["items"]] == [GOODS]


def test_actual_js_excludes_extension_hidden_forms_and_sponsored_products():
    s = dom_snapshot({"tag": "body", "children": [
        {"tag": "div", "attrs": {"class": "temu-selection-helper"}, "children": ["选品助手供货价USD1.00", link("11")]},
        {"tag": "div", "attrs": {"id": "mango-panel"}, "children": ["芒果销售额", link("22")]},
        {"tag": "form", "children": ["Private account field", link("33")]},
        {"tag": "div", "style": {"opacity": "0"}, "children": [link("44")]},
        {"tag": "div", "attrs": {"class": "sponsored"}, "children": [link("55")]},
        link(GOODS)]})
    assert len(s["links"]) == 1
    assert parser.product_id(s["links"][0]["url"]) == GOODS
    assert not any(secret in json.dumps(s) for secret in ("Private account", "USD1.00", "芒果销售额"))


def test_actual_js_does_not_use_injected_supplier_price_as_title():
    a = link(GOODS)
    a["children"].append({"tag": "span", "attrs": {"class": "mango-price"}, "children": ["供货价 29.99"]})
    s = dom_snapshot({"tag": "body", "children": [a]})
    assert s["links"][0]["name"] == "Wax candle"
    assert s["variants"] == []


def test_actual_js_reads_page_json_but_excludes_extension_script():
    s = dom_snapshot({"tag": "body", "children": [
        {"tag": "script", "attrs": {"type": "application/json", "id": "product-data"}, "children": [json.dumps(product())]},
        {"tag": "script", "attrs": {"type": "application/json", "id": "mango"}, "children": [json.dumps(product(goodsName="Injected data"))]}]})
    assert len(s["scripts"]) == 1
    s["url"] = URL
    assert parser.parse_product_snapshot(s, STORE)["name"] == "Wax candle"
    assert not any(secret in parser.SNAPSHOT_JS for secret in ("document.cookie", "localStorage", "sessionStorage", "storageState", ".value"))


def test_store_display_name_is_a_label_not_seller_identity():
    s = snapshot([], url="https://www.temu.com/mall.html?mall_id=" + STORE, title="Temu | Fixture Candle Shop")
    assert parser.parse_store_snapshot(s, STORE)["store_name"] == "Fixture Candle Shop"
    p = product(mallName="A changed display name")
    assert parse(p)["store_name"] == "A changed display name"
    p.pop("mallId")
    assert parse(p)["membership_verified"] is False
    s = dom_snapshot({"tag": "body", "children": [{"tag": "h1", "children": ["Fixture Candle Shop"]}, link(GOODS)]})
    assert s["store_name"] == "Fixture Candle Shop"


def test_discovery_prefers_actual_regional_frontend_url_over_generated_goods_link():
    s = snapshot(product(), url="https://www.temu.com/mall.html?mall_id=" + STORE,
                 links=[{"url": URL + "?_x_sessn_id=private", "name": "Wax candle"}])
    result = parser.parse_store_snapshot(s, STORE)
    assert len(result["items"]) == 1
    assert result["items"][0]["url"] == URL
    assert result["items"][0]["membership_verified"] is True


def test_layout_whitespace_in_title_spec_and_store_name_is_normalized():
    p = product(goodsName="Wax\n candle\t two pieces", mallName="Fixture\r\nCandle Shop")
    p["skuList"][0]["specs"][0]["spec_value"] = "Red\n\t2 pieces\x00"
    result = parse(p)
    assert result["name"] == "Wax candle two pieces"
    assert result["store_name"] == "Fixture Candle Shop"
    assert result["variants"][0]["spec"] == "Color: Red 2 pieces"
