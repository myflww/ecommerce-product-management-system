"""Read-only, bounded DOM snapshots and price previews for a human to confirm.

These are candidates, never an automatic pricing decision. The browser engine
owns navigation and overwrites ``url`` with its actual page URL before parsing.
No cookies, form values, browser storage or network requests are read here.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, unquote, urlsplit

MAX_TEXT = 60000
MAX_BLOCKS = 80
MAX_CANDIDATES = 12

# The tree walkers cap work as well as output. Reading a form or a screenshot is
# unnecessary: only rendered product text and explicit product metadata leave
# the browser. All query results are plain JSON data, never executable HTML.
SNAPSHOT_JS = r"""() => {
  const MAX_TEXT=60000, MAX_BLOCKS=80, MAX_NODES=9000;
  const omit='script,style,noscript,template,form,input,textarea,select,button,[contenteditable]:not([contenteditable="false"]),header,nav,footer,[hidden],[aria-hidden="true"]';
  const irrelevant=/recommend|related[-_ ]?product|you[-_ ]?may[-_ ]?(?:also[-_ ]?)?like|also[-_ ]?bought|recently[-_ ]?viewed|sponsored|recommendation|推荐|猜你喜欢|相似商品/i;
  const relatedHeading=/^(?:you may (?:also )?like|recommended(?: for you)?|related products|people also (?:bought|viewed)|猜你喜欢|为你推荐|相似商品|推荐商品)\s*[:：]?$/i;
  const noncurrent=/original[-_ ]?price|old[-_ ]?price|previous[-_ ]?price|regular[-_ ]?price|compare[-_ ]?(?:at|price)|list[-_ ]?price|strike|was[-_ ]?price|shipping[-_ ]?(?:cost|price|fee)|coupon|installment|member[-_ ]?price|原价|划线|运费|优惠券|会员价/i;
  const money=/(?:\b(?:USD|CNY|RMB|EUR|GBP|CAD|AUD|JPY|HKD|CHF|NZD|SGD|KRW|MXN|BRL|TWD|SEK|NOK|DKK|PLN|CZK|INR|ZAR|AED|SAR)\b\s*\d|(?:US|CA|AU|HK|NZ|SG)?[$€£¥￥]\s*\d|\d[\d., ]*\s*\b(?:USD|CNY|EUR|GBP|CAD|AUD|JPY|HKD|CHF)\b)/i;
  const root=document.querySelector('main,[role="main"]')||document.body;
  const visibility=new WeakMap();
  function visible(el) {
    if (!el||el.nodeType!==1) return false;
    if (visibility.has(el)) return visibility.get(el);
    let yes=!el.closest(omit);
    if (yes) {const s=getComputedStyle(el);yes=s.display!=='none'&&s.visibility!=='hidden'&&s.visibility!=='collapse'&&Number(s.opacity)!==0&&el.getClientRects().length>0;}
    if(yes){for(let p=el.parentElement,depth=0;p&&depth<20;p=p.parentElement,depth++){if(Number(getComputedStyle(p).opacity)===0){yes=false;break;}if(p===root)break;}}
    visibility.set(el,yes);return yes;
  }
  function contextFlags(el) {
    let blocked=false,struck=false;
    for(let p=el,depth=0;p&&depth<7;p=p.parentElement,depth++){
      const labels=[p.id,p.className,p.getAttribute('data-testid'),p.getAttribute('data-test'),p.getAttribute('aria-label')].filter(x=>typeof x==='string').join(' ').slice(0,1400);
      if(irrelevant.test(labels)||noncurrent.test(labels))blocked=true;
      if(p!==root){const h=p.querySelector(':scope > h2,:scope > h3,:scope > [role="heading"]');if(h&&relatedHeading.test((h.textContent||'').trim().slice(0,150)))blocked=true;}
      if(/^(DEL|S|STRIKE)$/.test(p.tagName)||getComputedStyle(p).textDecorationLine.includes('line-through'))struck=true;
      if(p===root)break;
    }
    return {blocked,struck};
  }
  function textOf(el,limit=300) {
    const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let text='',node,visited=0;
    while((node=walker.nextNode())&&visited++<160&&text.length<limit){
      const p=node.parentElement;if(!visible(p))continue;const f=contextFlags(p);if(f.blocked||f.struck)continue;
      text+=node.textContent||'';
    }
    return text.replace(/\s+/g,' ').trim().slice(0,limit);
  }
  const blocks=[],seen=new Set();
  function add(el,kind){
    if(blocks.length>=MAX_BLOCKS||!visible(el))return;
    const flags=contextFlags(el);if(flags.blocked||flags.struck)return;
    const text=textOf(el,260);if(!text||(!money.test(text)&&kind!=='product_price'))return;
    const contextParent=el.parentElement&&el.parentElement!==root?el.parentElement:el;
    let context=textOf(contextParent,360);const key=kind+'\n'+text+'\n'+context;
    if(seen.has(key))return;seen.add(key);
    blocks.push({text,context,struck:false,kind,blocked_context:false});
  }
  if(root){
    const elements=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);let el,visited=0;
    while((el=elements.nextNode())&&visited++<MAX_NODES&&blocks.length<MAX_BLOCKS){
      if(visible(el)&&el.childElementCount<2&&relatedHeading.test((el.textContent||'').trim().slice(0,160)))break;
      if(el.matches('[itemprop="price"],[data-product-price]'))add(el,'product_price');
      else if(/price/i.test([el.className,el.getAttribute('data-testid'),el.getAttribute('data-test'),el.getAttribute('aria-label')].filter(x=>typeof x==='string').join(' ')))add(el,'price');
    }
  }
  let text='',node,visited=0;
  if(root){
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    while((node=walker.nextNode())&&visited++<MAX_NODES&&text.length<MAX_TEXT){
      const p=node.parentElement;if(!visible(p))continue;const flags=contextFlags(p);if(flags.blocked||flags.struck)continue;
      const value=(node.textContent||'').replace(/\s+/g,' ').trim();if(!value)continue;
      if(relatedHeading.test(value))break;
      text+=value+'\n';
      if((money.test(value)||/^(?:(?:USD|CNY|RMB|EUR|GBP|CAD|AUD|JPY|HKD|CHF|NZD|SGD)|(?:US|CA|AU|HK|NZ|SG)?[$€£¥￥]|\d[\d., ]*|[.,]\d{1,4})$/i.test(value))&&blocks.length<MAX_BLOCKS){
        let candidate=p;
        // Retailers often split the currency, integer and cents into spans.
        for(let level=0;level<4&&candidate;level++,candidate=candidate.parentElement){
          const combined=textOf(candidate,260);
          if(combined.length>180)break;
          if(money.test(combined)){add(candidate,'visible_text');break;}
        }
      }
    }
  }
  const meta={};let metaCount=0;
  for(const el of document.querySelectorAll('meta[property],meta[name]')){
    if(metaCount++>=100)break;const key=(el.getAttribute('property')||el.getAttribute('name')||'').toLowerCase();
    if(['product:price:amount','product:price:currency','og:title'].includes(key)){
      const value=(el.getAttribute('content')||'').slice(0,300);if(value&&(meta[key]||[]).length<6)(meta[key]??=[]).push(value);
    }
  }
  let totalJSON=0;const json_ld=[];
  for(const el of document.querySelectorAll('script[type="application/ld+json"]')){
    if(json_ld.length>=12)break;const value=el.textContent||'';
    if(value.length>16000||totalJSON+value.length>24000)continue;json_ld.push(value);totalJSON+=value.length;
  }
  return {url:location.href.slice(0,4000),title:(document.title||'').slice(0,300),text:text.slice(0,MAX_TEXT),blocks,json_ld,meta};
}"""

_CURRENCIES = frozenset('USD CNY EUR GBP CAD AUD JPY HKD CHF NZD SGD KRW MXN BRL TWD SEK NOK DKK PLN CZK INR ZAR AED SAR TRY IDR MYR THB PHP VND ILS CLP COP PEN'.split())
_CODES = '|'.join(sorted(_CURRENCIES | {'RMB'}, key=len, reverse=True))
_NUMBER = r'(?:\d{1,3}(?:[ \u00a0]\d{3})+(?:\s*[.,]\s*\d{1,12})?|\d{1,12}(?:\s*[.,]\s*\d{1,12}){0,3})'
_MONEY = re.compile(rf'(?<![A-Za-z])(?:(?P<prefix>{_CODES}|(?:US|CA|AU|HK|NZ|SG)?\$|€|£|¥|￥)\s*(?P<n1>{_NUMBER})|(?P<n2>{_NUMBER})\s*(?P<suffix>{_CODES})(?![A-Za-z]))', re.I)
_BARE = re.compile(r'^\d[\d.,\u00a0 ]{0,23}$')
_NOT_CURRENT = re.compile(
    r'\b(?:original(?: price)?|list price|rrp|msrp|was|shipping|delivery (?:fee|cost)|coupon|save|saving|installments?|per month|monthly|membership|member[- ]only|member price|new (?:user|customer)|first order|subscribe|subscription|tax|subtotal|total|recommended|related products|also bought|sponsored|from)\b'
    r'|划线|原价|运费|配送费|优惠券|节省|会员|新人|新客|首单|分期|每月|推荐|猜你喜欢|相似商品|起价|起售|最低|低至|税费|合计', re.I)
_RANGE = re.compile(r'\d[\d.,]*\s*(?:[-–—~～至]|to\b)\s*(?:(?:[A-Z]{3}|US\$|CA\$|AU\$|HK\$|[$€£¥￥])\s*)?\d', re.I)
_NEGATIVE_MONEY = re.compile(rf'(?:[-−]\s*(?:{_CODES}|(?:US|CA|AU|HK|NZ|SG)?\$|€|£|¥|￥)|(?:{_CODES}|(?:US|CA|AU|HK|NZ|SG)?\$|€|£|¥|￥)\s*[-−])\s*\d',re.I)
_CHALLENGE = re.compile(r'verify (?:that )?you are human|(?:complete|solve|enter) (?:the )?(?:captcha|verification)|robot check|sign in to (?:continue|view)|log in to (?:continue|view)|请(?:输入|完成)验证码|验证您是真人|人机验证|访问被拒绝|请先登录后(?:查看|继续)', re.I)
_CHALLENGE_TITLE = re.compile(r'just a moment|access denied|captcha|robot check|^\s*(?:sign in|log in|login|登录|安全验证)(?:\b|\s|$)', re.I)
_RELATED_HEADING = re.compile(r'^(?:you may (?:also )?like|recommended(?: for you)?|related products|people also (?:bought|viewed)|猜你喜欢|为你推荐|相似商品|推荐商品)\s*[:：]?$', re.I)


def _text(value, limit=MAX_TEXT):
    return value[:limit] if isinstance(value, str) else ''


def _host(parts):
    return (parts.hostname or '').lower().rstrip('.').removeprefix('www.')


def _temu_id(parts):
    match = re.search(r'-g-(\d+)\.html(?:$|/)', parts.path)
    values = parse_qs(parts.query).get('goods_id', [])
    if len(set(values))>1 or (match and values and values[0]!=match.group(1)):
        return ''
    return match.group(1) if match else values[0] if values else ''


def _same_product(current: str, expected: str) -> bool:
    try:
        actual, original = urlsplit(current), urlsplit(expected)
        if any(p.scheme not in {'http','https'} or not p.hostname or p.username is not None or p.password is not None or p.port not in {None,443 if p.scheme=='https' else 80} for p in (actual, original)):
            return False
        ah, oh = _host(actual), _host(original)
        temu = lambda h: h == 'temu.com' or h.endswith('.temu.com')
        if temu(oh):
            original_id = _temu_id(original)
            return bool(temu(ah) and original_id and _temu_id(actual) == original_id)
        if ah != oh or unquote(actual.path).rstrip('/') != unquote(original.path).rstrip('/'):
            return False
        aq, oq = parse_qs(actual.query), parse_qs(original.query)
        return all(aq.get(key) == oq[key] for key in ('goods_id','product_id','item_id','pid') if key in oq)
    except (TypeError, ValueError, UnicodeError):
        return False


def _url_currency(url: str) -> str:
    try:
        parts=urlsplit(url)
        # Region is inferred only from a known retail storefront's URL, never
        # from the user's expected currency or a vague currency symbol.
        if _host(parts) == 'temu.com' or _host(parts).endswith('.temu.com'):
            country=parts.path.strip('/').split('/',1)[0].split('-',1)[0].lower()
            return {'us':'USD','ca':'CAD','au':'AUD','uk':'GBP','gb':'GBP','nz':'NZD','jp':'JPY','hk':'HKD','sg':'SGD','de':'EUR','fr':'EUR','es':'EUR','it':'EUR','nl':'EUR'}.get(country,'')
    except (TypeError,ValueError):
        pass
    return ''


def _amount(raw: str) -> str | None:
    raw=re.sub(r'[\s\u00a0]','',raw)
    # Multiple decimal marks and undecidable thousands/decimal notation are
    # refused. Common comma-thousands and decimal-comma amounts are explicit.
    if re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,4})?',raw):
        raw=raw.replace(',','')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+,\d{1,4}',raw):
        raw=raw.replace('.','').replace(',','.')
    elif ',' in raw and '.' not in raw and re.fullmatch(r'\d+,\d{1,2}',raw):
        raw=raw.replace(',','.')
    if not re.fullmatch(r'\d{1,9}(?:\.\d{1,4})?',raw):
        return None
    try:
        value=Decimal(raw)
        if not value.is_finite() or value<=0:
            return None
        return format(value.normalize(),'f')
    except InvalidOperation:
        return None


def _currency(token: str, inferred: str, declared: str) -> tuple[str,bool]:
    token=token.upper().replace(' ','')
    explicit={'US$':'USD','CA$':'CAD','AU$':'AUD','HK$':'HKD','NZ$':'NZD','SG$':'SGD','€':'EUR','£':'GBP','RMB':'CNY'}
    if token in explicit:
        return explicit[token],False
    if token in _CURRENCIES:
        return token,False
    if token=='$':
        choices={'USD','CAD','AUD','HKD','NZD','SGD','MXN','TWD','CLP','COP'}
        if inferred and declared and inferred != declared:
            return '',False
        value=inferred or declared
        return (value,True) if value in choices else ('',False)
    if token in {'¥','￥'}:
        value=inferred or declared
        return (value,True) if value in {'CNY','JPY'} else ('',False)
    return '',False


def _declared_currency(snapshot: dict) -> str:
    meta=snapshot.get('meta')
    values=meta.get('product:price:currency',[]) if isinstance(meta,dict) else []
    if isinstance(values,str):
        values=[values]
    if not isinstance(values,list):
        return ''
    unique={value.strip().upper() for value in values[:12] if isinstance(value,str) and value.strip().upper() in _CURRENCIES}
    return next(iter(unique)) if len(unique)==1 else ''


def _body_blocks(text: str) -> list[dict]:
    result=[]
    lines=text.splitlines()[:1200]
    for index,line in enumerate(lines):
        line=line.strip()
        if _RELATED_HEADING.fullmatch(line):
            break
        if not line or len(line)>300 or not _MONEY.search(line):
            continue
        # A partially split price must not turn USD 12 + .99 into USD 12.
        if index+1<len(lines) and re.fullmatch(r'\s*[.,]\d{1,4}\s*',lines[index+1]):
            continue
        result.append({'text':line,'context':line,'kind':'visible_text'})
        if len(result)>=MAX_BLOCKS:
            break
    return result


def _result(code: str, message: str, *, candidates=None, notices=None):
    candidates=candidates or []
    return {'code':code,'candidates':candidates,'message':message,'needs_action':not bool(candidates),'notices':notices or []}


def parse_snapshot(snapshot: dict, target: dict) -> dict:
    """Produce bounded, visible-price previews, never write a price or call AI."""
    if not isinstance(snapshot,dict) or not isinstance(target,dict):
        return _result('invalid_snapshot','页面内容无法读取，请重新打开浏览器辅助采集。')
    current=_text(snapshot.get('url'),4000)
    source=_text(target.get('source_url'),4000)
    if not _same_product(current,source):
        return _result('identity_mismatch','当前浏览器页面不是这条记录对应的商品，请返回原商品页面再读取。')
    title=re.sub(r'\s+',' ',_text(snapshot.get('title'),300)).strip()
    body=_text(snapshot.get('text'))
    if _CHALLENGE_TITLE.search(title) or _CHALLENGE.search(body[:6000]):
        return _result('verification_required','商品页面需要登录或验证，请在采集浏览器中自行完成后重新读取。')
    inferred=_url_currency(current)
    declared=_declared_currency(snapshot)
    expected=_text(target.get('currency'),3).upper()
    hint=_text(target.get('variant_hint'),200).strip()
    region=_text(target.get('region'),80).strip()
    notice=[]
    raw_blocks=snapshot.get('blocks',[])
    if not isinstance(raw_blocks,list):
        raw_blocks=[]
    blocks=[b for b in raw_blocks[:MAX_BLOCKS] if isinstance(b,dict)]
    # If explicit blocks exist, their per-node exclusions are authoritative.
    # Re-parsing global body text could resurrect a rejected strike/recommend
    # amount or lose cents split across separate text nodes.
    if not raw_blocks:
        blocks = _body_blocks(body)
    candidates=[]
    seen=set()
    unknown_currency=False
    excluded=False
    for block in blocks[:MAX_BLOCKS*2]:
        if block.get('struck') or block.get('blocked_context'):
            continue
        text=re.sub(r'\s+',' ',_text(block.get('text'),300)).strip()
        context=re.sub(r'\s+',' ',_text(block.get('context'),500)).strip()
        if not text:
            continue
        if _NOT_CURRENT.search(text) or _NOT_CURRENT.search(context) or _RANGE.search(text) or _RANGE.search(context) or _NEGATIVE_MONEY.search(text):
            excluded=True
            continue
        matches=list(_MONEY.finditer(text))
        if not matches and block.get('kind')=='product_price' and _BARE.fullmatch(text) and declared:
            amount=_amount(text)
            pairs=[(amount,declared,True)] if amount else []
        else:
            pairs=[]
            for match in matches[:12]:
                amount=_amount((match.group('n1') or match.group('n2')).strip())
                currency,was_inferred=_currency(match.group('prefix') or match.group('suffix'),inferred,declared)
                if not currency:
                    unknown_currency=True
                if amount and currency:
                    pairs.append((amount,currency,was_inferred))
        for amount,currency,was_inferred in pairs:
            if (amount,currency) in seen:
                continue
            seen.add((amount,currency))
            mismatch=bool(expected and currency!=expected)
            evidence=text[:300]
            if was_inferred:
                evidence += '；币种依据：'+('Temu站点路径' if inferred else '页面商品币种标记')+' '+currency
            token=hashlib.sha256((current.split('?')[0]+'\0'+amount+'\0'+currency+'\0'+evidence).encode()).hexdigest()[:20]
            candidates.append({'id':token,'price':amount,'currency':currency,'evidence':evidence[:500],
                'title':title,'source':'browser_confirmed','currency_inferred':was_inferred,
                'label':'页面可见价格（需核对规格）','require_variant_confirmation':True,
                'require_currency_confirmation':mismatch or was_inferred})
            if len(candidates)>=MAX_CANDIDATES:
                break
        if len(candidates)>=MAX_CANDIDATES:
            break
    if not candidates:
        if unknown_currency:
            notice.append('页面只显示了不能唯一确定币种的货币符号，请切换为明确币种显示后再读取。')
        if excluded:
            notice.append('已排除原价、优惠券、运费、会员价、价格区间或推荐商品金额。')
        return _result('price_missing','尚未读取到可确认的当前商品售价。请等待页面加载，选择具体规格，再重新读取。',notices=notice)
    if hint:
        if hint.casefold() not in (body+' '+title+' '+' '.join(_text(b.get('text'),300) for b in blocks[:MAX_BLOCKS])).casefold():
            notice.append('页面未能确认所填规格“'+hint+'”。请在浏览器选中正确规格，并人工核对后再确认价格。')
        else:
            notice.append('请核对浏览器当前选中规格确为“'+hint+'”；仅在页面出现规格文字不代表已选中。')
    else:
        notice.append('未填写具体规格。请先在浏览器选好颜色、件数或套装，再确认对应价格。')
    if region:
        notice.append('目标地区为“'+region+'”，请核对浏览器实际送达地区；此字段不会自动切换地区。')
    if any(c['currency']!=expected for c in candidates) and expected:
        notice.append('候选币种与原定价币种可能不同；只显示原币种报价，不自动换汇或计算跨币种比例。')
    if any(c['currency_inferred'] for c in candidates):
        notice.append('部分币种依据站点路径或页面标记判断，请核对实际显示币种。')
    if len(candidates)>1:
        notice.append('页面存在多个金额，请选择当前规格的实际售价；不会自动采用最低价。')
    notice.append('请比较相同规格及同一价格口径，供货价与前台零售价不应直接当作降价比例。')
    return _result('preview','已读取到页面价格候选，请核对规格、地区和币种后确认；尚未写入价格记录。',candidates=candidates,notices=notice)
