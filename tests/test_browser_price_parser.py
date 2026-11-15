"""Human-confirmed browser price previews use fabricated DOM data only."""
import shutil
import subprocess
import json

import pytest

import browser_price_parser as parser

URL='https://www.temu.com/us-zh-Hans/product-g-700000000000002.html'


def target(**changes):
    return {'source_url':URL,'currency':'USD','variant_hint':'Set D','region':'美国站',**changes}


def snapshot(text='Now US$12.90', *, blocks=None, **changes):
    return {'url':URL,'title':'Beeswax candle set','text':text,
            'blocks':blocks if blocks is not None else [{'text':text,'context':text,'kind':'price'}],
            'json_ld':[],'meta':{},**changes}


def prices(result):
    return [(v['price'],v['currency']) for v in result['candidates']]


def test_visible_rendered_price_is_only_a_preview():
    result=parser.parse_snapshot(snapshot(),target())
    assert prices(result)==[('12.9','USD')]
    assert not result['needs_action']
    candidate=result['candidates'][0]
    assert candidate['source']=='browser_confirmed'
    assert candidate['evidence']=='Now US$12.90'
    assert candidate['require_variant_confirmation']
    assert '尚未写入' in result['message']
    assert any('未能确认' in notice and 'Set D' in notice for notice in result['notices'])


def test_exact_variant_words_still_require_selected_variant_confirmation():
    result=parser.parse_snapshot(snapshot('Set D\nPrice USD 12.90'),target())
    assert prices(result)==[('12.9','USD')]
    assert any('不代表已选中' in notice for notice in result['notices'])


def test_missing_variant_is_not_treated_as_verified():
    result=parser.parse_snapshot(snapshot(),target(variant_hint=''))
    assert any('未填写具体规格' in notice for notice in result['notices'])


def test_us_path_supports_dollar_currency_without_using_expected_cny():
    result=parser.parse_snapshot(snapshot('$12.90'),target(currency='CNY'))
    assert prices(result)==[('12.9','USD')]
    assert result['candidates'][0]['currency_inferred']
    assert result['candidates'][0]['require_currency_confirmation']
    assert any('不自动换汇' in notice for notice in result['notices'])


def test_unknown_dollar_is_never_assumed_to_equal_expected_currency():
    url='https://shop.example/product'
    result=parser.parse_snapshot(snapshot('$12.90',url=url),target(source_url=url,currency='USD'))
    assert result['candidates']==[]
    assert result['needs_action']
    assert any('币种' in notice for notice in result['notices'])


@pytest.mark.parametrize('shown,currency', [('US$12.90','USD'),('CA$ 12.90','CAD'),('AU$12.90','AUD'),('HK$12.90','HKD'),('USD 12.90','USD'),('12.90 EUR','EUR'),('RMB 12.90','CNY'),('€12,90','EUR'),('£12.90','GBP')])
def test_explicit_currency_tokens(shown,currency):
    assert prices(parser.parse_snapshot(snapshot(shown),target()))==[('12.9',currency)]


@pytest.mark.parametrize('shown,amount', [('US$1,299.50','1299.5'),('EUR 1.299,50','1299.5'),('EUR 1 299,50','1299.5'),('US$ 12.99 2件装','12.99')])
def test_number_notation_never_concatenates_the_next_product_quantity(shown,amount):
    assert prices(parser.parse_snapshot(snapshot(shown),target()))[0][0]==amount


@pytest.mark.parametrize('shown', ['USD 0','USD -12.90','-USD 12.90','-US$12.90','USD 1000000000','USD 0.00001','USD 12.90 - USD 15.90','US$12.90–15.90','From USD 12.90','低至 USD 12.90','USD 12.90 至 USD 15.90'])
def test_invalid_or_range_prices_are_not_current_unit_price(shown):
    assert not parser.parse_snapshot(snapshot(shown),target())['candidates']


@pytest.mark.parametrize('shown', ['Was USD 19.90','Original price USD 19.90','Shipping USD 3.90','Coupon USD 5.00','Save USD 3.00','Member price USD 7.90','New customer USD 1.00','原价USD 19.90','运费USD 5.00','分期USD 2.00','推荐商品USD 3.00'])
def test_non_current_amounts_are_excluded(shown):
    assert not parser.parse_snapshot(snapshot(shown),target())['candidates']


def test_strikethrough_and_recommendation_blocks_are_excluded():
    result=parser.parse_snapshot(snapshot('',blocks=[
        {'text':'USD 19.90','kind':'price','struck':True},
        {'text':'USD 1.00','kind':'price','blocked_context':True},
        {'text':'USD 12.90','kind':'price','context':'Current product price'},
    ]),target())
    assert prices(result)==[('12.9','USD')]


def test_body_text_cannot_resurrect_explicitly_excluded_amounts():
    result=parser.parse_snapshot(snapshot('USD 49.99\nUSD 1.99',blocks=[
        {'text':'USD 49.99','struck':True},
        {'text':'USD 1.99','blocked_context':True},
    ]),target())
    assert not result['candidates']


def test_same_amount_is_allowed_only_if_an_explicit_visible_block_supports_it():
    result=parser.parse_snapshot(snapshot('USD 49.99',blocks=[
        {'text':'USD 49.99','struck':True},
        {'text':'USD 49.99','context':'Current product price'},
    ]),target())
    assert prices(result)==[('49.99','USD')]


def test_partial_body_integer_does_not_drop_split_cents():
    assert not parser.parse_snapshot(snapshot('USD 12\n.99',blocks=[]),target())['candidates']
    assert prices(parser.parse_snapshot(snapshot('US$ 12 .99'),target()))==[('12.99','USD')]


def test_body_fallback_stops_before_recommended_products():
    result=parser.parse_snapshot(snapshot('Price US$12.90\nRecommended for you\nUS$1.99',blocks=[]),target())
    assert prices(result)==[('12.9','USD')]


def test_multiple_prices_are_distinct_candidates_not_a_lowest_price_decision():
    result=parser.parse_snapshot(snapshot('',blocks=[{'text':'Price USD 12.90'},{'text':'USD 9.90'},{'text':'USD 12.90'}]),target())
    assert prices(result)==[('12.9','USD'),('9.9','USD')]
    assert any('不会自动采用最低价' in notice for notice in result['notices'])


def test_snapshot_budgets_and_candidate_ids():
    data=snapshot('',blocks=[{'text':f'USD {i+1}.90','context':'price'} for i in range(500)])
    result=parser.parse_snapshot(data,target())
    assert len(result['candidates'])==12
    assert len({c['id'] for c in result['candidates']})==12
    assert result==parser.parse_snapshot(data,target())
    assert all(len(c['evidence'])<=500 for c in result['candidates'])


def test_temu_same_product_allows_query_variant_changes_and_mobile_host():
    other='https://m.temu.com/another-title-g-700000000000002.html?sku_id=different'
    assert parser.parse_snapshot(snapshot(url=other),target())['candidates']


@pytest.mark.parametrize('url', ['https://www.temu.com/us-en/product-g-700000000001.html','https://temu.com.evil.example/us-en/product-g-700000000000002.html','https://evil.example/us-en/product-g-700000000000002.html','https://www.temu.com/login.html','file:///product-g-700000000000002.html','https://name:secret@www.temu.com/us/product-g-700000000000002.html'])
def test_wrong_product_or_host_is_never_read_as_target(url):
    result=parser.parse_snapshot(snapshot(url=url),target())
    assert result['code']=='identity_mismatch'
    assert result['needs_action'] and not result['candidates']


def test_temu_goods_id_query_identity():
    t=target(source_url='https://www.temu.com/goods.html?goods_id=700000000000002&sku_id=A')
    assert parser.parse_snapshot(snapshot(url='https://www.temu.com/goods.html?goods_id=700000000000002&sku_id=D'),t)['candidates']
    assert not parser.parse_snapshot(snapshot(url='https://www.temu.com/goods.html?goods_id=99'),t)['candidates']
    assert not parser.parse_snapshot(snapshot(url=URL+'?goods_id=999'),target())['candidates']
    assert not parser.parse_snapshot(snapshot(url='https://www.temu.com/goods.html?goods_id=700000000000002&goods_id=99'),t)['candidates']


def test_generic_product_path_and_query_product_id_are_protected():
    t=target(source_url='https://shop.example/product?product_id=123&sku=A')
    assert parser.parse_snapshot(snapshot(url='https://shop.example/product?product_id=123&sku=D'),t)['candidates']
    assert not parser.parse_snapshot(snapshot(url='https://shop.example/product?product_id=999'),t)['candidates']
    assert not parser.parse_snapshot(snapshot(url='https://shop.example/other?product_id=123'),t)['candidates']


@pytest.mark.parametrize('title,body',[('Just a moment…','Price USD 1.00'),('Sign in','Price USD 1.00'),('商品','请完成验证码后继续\nUSD 12.90'),('Verification','Verify you are human\nUSD 12.90')])
def test_login_and_challenge_wall_do_not_expose_prices(title,body):
    result=parser.parse_snapshot(snapshot(body,title=title),target())
    assert result['code']=='verification_required'
    assert result['needs_action'] and not result['candidates']


def test_regular_account_sign_in_link_is_not_mistaken_for_login_wall():
    assert parser.parse_snapshot(snapshot('Sign in\nCurrent price USD 12.90'),target())['candidates']


def test_hidden_jsonld_price_is_not_a_visible_price():
    data=snapshot('',blocks=[],json_ld=['{"@type":"Product","offers":{"price":"12.90","priceCurrency":"USD"}}'])
    assert not parser.parse_snapshot(data,target())['candidates']


def test_numeric_price_element_needs_an_explicit_declared_currency():
    data=snapshot('',blocks=[{'text':'12.90','kind':'product_price'}],meta={'product:price:currency':['USD']})
    assert prices(parser.parse_snapshot(data,target()))==[('12.9','USD')]
    data['meta']={}
    assert not parser.parse_snapshot(data,target())['candidates']


def test_conflicting_site_path_and_metadata_do_not_resolve_an_ambiguous_dollar():
    assert not parser.parse_snapshot(snapshot('$12.90',meta={'product:price:currency':['CAD']}),target())['candidates']


@pytest.mark.parametrize('snapshot_value', [None,[],{}, {'url':URL,'title':{},'text':[],'blocks':[None,[],{}, {'text':{},'context':12}],'meta':{'product:price:currency':{}}}])
def test_malformed_snapshots_return_actionable_results(snapshot_value):
    result=parser.parse_snapshot(snapshot_value,target())
    assert not result['candidates']
    assert isinstance(result['message'],str)


def test_snapshot_javascript_compiles_and_has_no_secret_storage_access():
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    completed=subprocess.run(['node','-e','new Function("return (" + '+json.dumps(parser.SNAPSHOT_JS)+ ' + ")");'],capture_output=True,text=True,timeout=10)
    assert completed.returncode==0,completed.stderr
    assert not any(secret in parser.SNAPSHOT_JS for secret in ('document.cookie','localStorage','sessionStorage','.value','storageState'))


def dom_snapshot(tree):
    """Execute the actual snapshot JS against a small DOM API mock in Node.

    The mock retains text-node boundaries, ancestors, styles, and element order;
    these regression cases therefore test the JS extraction, not an already
    flattened Python sample. It has no browser, network or filesystem access.
    """
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    source=r'''
const tree=JSON.parse(process.argv[1]);
function descendants(el){return el.children.flatMap(c=>[c,...(c.nodeType===1?descendants(c):[])]);}
function oneMatch(el,s){
  if(s==='[contenteditable]:not([contenteditable="false"])')return Object.hasOwn(el.attrs,'contenteditable')&&el.attrs.contenteditable!=='false';
  if(/^[a-z]+$/.test(s))return el.tagName===s.toUpperCase();
  const m=s.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);return !!m&&Object.hasOwn(el.attrs,m[1])&&(m[2]===undefined||el.attrs[m[1]]===m[2]);
}
class E{
 constructor(data,parent=null){this.nodeType=1;this.tagName=(data.tag||'div').toUpperCase();this.attrs=data.attrs||{};this.style=data.style||{};this.parentElement=parent;this.children=(data.children||[]).map(c=>typeof c==='string'?{nodeType:3,textContent:c,parentElement:this}:new E(c,this));}
 get textContent(){return this.children.map(c=>c.textContent).join('');}
 get id(){return this.attrs.id||'';}get className(){return this.attrs.class||'';}
 get childElementCount(){return this.children.filter(c=>c.nodeType===1).length;}
 getAttribute(name){return this.attrs[name]??null;}
 matches(selector){return selector.split(',').some(s=>oneMatch(this,s.trim()));}
 closest(selector){for(let el=this;el;el=el.parentElement)if(el.matches(selector))return el;return null;}
 querySelector(selector){if(selector.startsWith(':scope >'))return this.children.find(c=>c.nodeType===1&&(c.tagName==='H2'||c.tagName==='H3'||c.attrs.role==='heading'))||null;return descendants(this).find(c=>c.nodeType===1&&c.matches(selector))||null;}
 getClientRects(){for(let p=this;p;p=p.parentElement)if(p.style.display==='none')return [];return [{}];}
}
const body=new E(tree);
globalThis.NodeFilter={SHOW_TEXT:4,SHOW_ELEMENT:1};
globalThis.getComputedStyle=el=>({display:el.style.display||'block',visibility:el.style.visibility||(el.parentElement?getComputedStyle(el.parentElement).visibility:'visible'),opacity:el.style.opacity??'1',textDecorationLine:el.style.textDecorationLine||'none'});
globalThis.document={body,title:'Visible candle product',querySelector:selector=>(body.matches(selector)?body:body.querySelector(selector)),querySelectorAll:()=>[],createTreeWalker:(el,filter)=>{const values=descendants(el).filter(c=>c.nodeType===(filter===4?3:1));let index=0;return {nextNode:()=>values[index++]||null};}};
globalThis.location={href:process.argv[2]};
const capture=eval('('+process.argv[3]+')');process.stdout.write(JSON.stringify(capture()));
'''
    completed=subprocess.run(['node','-e',source,json.dumps(tree),URL,parser.SNAPSHOT_JS],capture_output=True,text=True,timeout=10)
    assert completed.returncode==0,completed.stderr
    return json.loads(completed.stdout)


def test_actual_snapshot_reassembles_currency_integer_and_cents_spans():
    data=dom_snapshot({'tag':'body','children':[{'tag':'div','children':[
        {'tag':'span','children':['$']},{'tag':'span','children':['12']},{'tag':'span','children':['.99']}]}]})
    assert prices(parser.parse_snapshot(data,target()))==[('12.99','USD')]
    assert any('$12.99'==b['text'] for b in data['blocks'])


def test_actual_snapshot_drops_unlabelled_strike_and_recommendation_prices():
    data=dom_snapshot({'tag':'body','children':[
        {'tag':'del','children':['$49.99']},
        {'tag':'span','style':{'textDecorationLine':'line-through'},'children':['$59.99']},
        {'tag':'p','children':['Current price US$12.99']},
        {'tag':'h2','children':['Recommended for you']},
        {'tag':'div','children':[{'tag':'span','children':['US$1.99']}]},
    ]})
    assert prices(parser.parse_snapshot(data,target()))==[('12.99','USD')]
    assert '49.99' not in data['text'] and '59.99' not in data['text'] and '1.99' not in data['text']


def test_actual_snapshot_excludes_hidden_opacity_ancestor_and_form_values():
    data=dom_snapshot({'tag':'body','children':[
        {'tag':'div','style':{'display':'none'},'children':['US$1.99']},
        {'tag':'div','style':{'opacity':'0'},'children':[{'tag':'span','children':['US$2.99']}]},
        {'tag':'form','children':[{'tag':'input','attrs':{'value':'private password'}},{'tag':'div','children':['Private account US$3.99']}]},
        {'tag':'div','attrs':{'contenteditable':'plaintext-only'},'children':['Secret draft US$4.99']},
        {'tag':'p','children':['US$12.99']},
    ]})
    assert prices(parser.parse_snapshot(data,target()))==[('12.99','USD')]
    assert not any(secret in json.dumps(data) for secret in ('private password','Private account','Secret draft'))


def test_actual_snapshot_can_show_current_price_equal_to_old_price():
    data=dom_snapshot({'tag':'body','children':[
        {'tag':'del','children':['US$49.99']},
        {'tag':'p','children':['Price US$49.99']},
    ]})
    assert prices(parser.parse_snapshot(data,target()))==[('49.99','USD')]
