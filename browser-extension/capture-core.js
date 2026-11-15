/* Public product projection shared by the popup and isolated page collector. */
(function (scope) {
  'use strict';
  const LIMITS = Object.freeze({pages:20, bytes:8*1024*1024, scripts:20, scriptChars:2000000, nodes:25000, depth:35, variants:200, links:400});
  const text = (value, limit=500) => typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g,' ').replace(/\s+/g,' ').trim().slice(0,limit) : '';
  const id = value => (typeof value==='string' || Number.isSafeInteger(value)) && /^\d{1,30}$/.test(String(value)) ? String(value) : '';
  function temuURL(value, base) {
    if(typeof value!=='string'||!value||value.length>4000||/[\s\\\x00-\x1f\x7f]/.test(value))return null;
    try {const u=new URL(value,base);return u.protocol==='https:'&&!u.username&&!u.password&&(!u.port||u.port==='443')&&/(^|\.)temu\.com$/.test(u.hostname)?u:null;}catch{return null;}
  }
  function productID(value, base) {
    const u=temuURL(value,base);if(!u)return '';
    const path=u.pathname.match(/-g-(\d+)\.html(?:$|\/)/)?.[1], values=u.searchParams.getAll('goods_id');
    if(values.length>1||values.length&&(!id(values[0])||path&&path!==values[0]))return '';
    return id(path||values[0]);
  }
  function productURL(value, base) {
    const u=temuURL(value,base);if(!u||!productID(u.href))return '';
    const q=new URLSearchParams();for(const key of ['goods_id','sku_id','skuId','variant_id','variantId']){const vs=u.searchParams.getAll(key);if(vs.length===1&&id(vs[0]))q.set(key,vs[0]);else if(vs.length)return '';}
    return u.origin+u.pathname+(q.size?'?'+q:'');
  }
  function storeURL(value, base) {
    const u=temuURL(value,base);if(!u||!u.pathname.endsWith('/mall.html')||u.searchParams.getAll('mall_id').length!==1||!id(u.searchParams.get('mall_id')))return '';
    return 'https://www.temu.com/mall.html?mall_id='+u.searchParams.get('mall_id');
  }
  function imageURL(value, base) {
    if(typeof value!=='string'||value.length>4000||/[\s\\\x00-\x1f\x7f]/.test(value))return '';
    try {const u=new URL(value,base);if(u.protocol!=='https:'||u.username||u.password||u.port||!u.hostname.includes('.')||u.hostname==='localhost'||/\.(?:local|internal|lan)$/.test(u.hostname))return '';u.hash='';const keep=new URLSearchParams();for(const k of ['w','h','width','height','quality','format','imageMogr2','imageView2'])for(const v of u.searchParams.getAll(k))if(v.length<150)keep.append(k,v);u.search=keep.toString();return u.href;}catch{return '';}
  }
  const identity=new Set(['goods_id','goodsId','sku_id','skuId','variant_id','variantId','mall_id','mallId','store_id']);
  const strings=new Set(['goods_name','goodsName','name','title','mallName','mall_name','storeName','store_name','sku','@type','spec_key','specKey','spec_value','specValue','value','spec','variant_name','variantName','skuName','sku_name','color','size','pattern','material']);
  const currencies=new Set(['priceCurrency','currency','currencyCode','currency_code']);
  const urls=new Set(['url','goodsUrl','goods_url','mallUrl','mall_url']);
  const images=new Set(['image','image_url','imageUrl','thumb_url','thumbUrl','hdThumbUrl','hd_thumb_url']);
  const counts=new Set(['skuCount','sku_count','variantCount','variant_count']);
  const disqualifiers=new Set(['lowPrice','highPrice','eligibleCustomerType','eligibleQuantity','priceUnit','price_unit','amountUnit','amount_unit']);
  const childLists=new Set(['skuList','sku_list','hasVariant','offers','specs','specList','spec_list','skuSpecs','sku_specs']);
  const sellers=new Set(['mall','mallInfo','mall_info','seller','store']);
  function publicImage(value, base) {
    if(Array.isArray(value))return value.slice(0,1).map(v=>publicImage(v,base));
    if(value&&typeof value==='object'){const out={};for(const k of ['url','contentUrl'])if(typeof value[k]==='string')out[k]=imageURL(value[k],base);return out;}
    return imageURL(value,base);
  }
  function project(value, base, depth=0, budget={count:0}) {
    if(!value||typeof value!=='object'||Array.isArray(value)||depth>8||++budget.count>4000)return {};
    const out={};
    for(const key of Object.keys(value).slice(0,500)) {
      const v=value[key];
      if(identity.has(key))out[key]=id(v)||'__invalid__';
      else if(key==='sku')out[key]=typeof v==='string'&&v.length<=200&&!v.includes('\0')?v:(v==null||v===''?'':false);
      else if(key==='@type')out[key]=typeof v==='string'&&v.length<=500?v:'AggregateOffer';
      else if(strings.has(key))out[key]=text(v,500);
      else if(currencies.has(key))out[key]=typeof v==='string'&&v.length<=8?text(v,8):(v==null||v===''?'':'INVALID');
      else if(key==='price'){const amount=typeof v==='string'?v:'';out[key]=amount.length<=24?amount:'';}
      else if(urls.has(key))out[key]=productURL(v,base)||storeURL(v,base)||(v==null||v===''?'':'__invalid__');
      else if(images.has(key))out[key]=publicImage(v,base);
      else if(counts.has(key)&&/^\d{1,6}$/.test(String(v)))out[key]=Number(v);
      else if(disqualifiers.has(key))out[key]='present';
      else if(key==='priceType'&&v)out[key]='present';
      else if(key==='struck')out[key]=Boolean(v);
      else if(childLists.has(key)) {
        if(Array.isArray(v))out[key]=v.slice(0,key==='offers'?12:LIMITS.variants).map(e=>typeof e==='string'&&key!=='offers'?text(e,180):project(e,base,depth+1,budget));
        else if(key==='offers')out[key]=project(v,base,depth+1,budget);
      } else if(sellers.has(key))out[key]=project(v,base,depth+1,budget);
    }
    return out;
  }
  function parseScript(raw) {
    if(typeof raw!=='string'||raw.length>LIMITS.scriptChars)return null;
    raw=raw.trim().replace(/^(?:window\.)?(?:rawData|__INITIAL_STATE__|__PRELOADED_STATE__)\s*=\s*/,'').replace(/;\s*$/,'');
    try {
      // Preserve the numeric lexeme rather than rounding a long fractional
      // amount into an apparently valid major-unit price. Older browsers that
      // lack JSON reviver source evidence conservatively omit numeric prices.
      const v=JSON.parse(raw,(key,value,context)=>{
        if(typeof value!=='number')return value;
        if(identity.has(key))return typeof context?.source==='string'&&/^\d{1,30}$/.test(context.source)?context.source:'__invalid__';
        return key==='price'?(typeof context?.source==='string'?context.source:'UNVERIFIED'):value;
      });
      return v&&typeof v==='object'?v:null;
    }catch{return null;}
  }
  const related=/recommend|related|sponsored|recently.?viewed|also.?bought/i;
  // Public-looking goods fields inside an order/account tree are still private.
  // Prune those branches before looking for product IDs, never just at export.
  const privateBranch=/account|order|address|payment|cart|user|session|customer|checkout|profile|login|auth|token|email|phone|contact|receipt|recipient|buyer|billing|shipping|passport|wallet|credential|password|device|tracking|analytics|experiment|abtest|impression|notification|message|history|wishlist|favorite/i;
  function projectDocuments(rawScripts, pageURL) {
    const gid=productID(pageURL), shop=storeURL(pageURL), mall=shop?new URL(shop).searchParams.get('mall_id'):'';
    const products=[], seen=new Set();let visited=0,total=0;
    for(const raw of rawScripts.slice(0,LIMITS.scripts)) {
      if(typeof raw!=='string'||(total+=raw.length)>LIMITS.scriptChars)continue;
      const doc=parseScript(raw);if(!doc)continue;
      const stack=[[doc,0]];
      while(stack.length&&visited<LIMITS.nodes) {
        const [node,depth]=stack.pop();visited++;if(!node||typeof node!=='object'||depth>LIMITS.depth)continue;
        if(!Array.isArray(node)) {
          const current=id(node.goods_id)||id(node.goodsId)||productID(node.url||node.goodsUrl||node.goods_url||'',pageURL);
          const currentMall=id(node.mall_id)||id(node.mallId)||id(node.store_id);
          if(current&&((gid&&current===gid)||(!gid&&mall&&currentMall===mall))) {
            const p=project(node,pageURL), encoded=JSON.stringify(p);
            if(!seen.has(encoded)&&products.length<40){products.push(p);seen.add(encoded);}
          }
        }
        const entries=Array.isArray(node)?node.slice(0,2000).map((v,i)=>[String(i),v]):Object.entries(node).slice(0,1500);
        for(let i=entries.length-1;i>=0;i--){const [key,v]=entries[i];if(!related.test(key)&&!privateBranch.test(key)&&v&&typeof v==='object')stack.push([v,depth+1]);}
      }
    }
    const encoded=JSON.stringify({products});return {products,scripts:products.length&&encoded.length<=LIMITS.scriptChars?[{type:'application/json',id:'midoo-public-products',text:encoded}]:[],bounded:visited>=LIMITS.nodes};
  }
  function pageIdentity(snapshot) {const product=productURL(snapshot?.url);if(product){const u=new URL(product);for(const k of ['sku_id','skuId','variant_id','variantId'])u.searchParams.delete(k);return u.href;}return storeURL(snapshot?.url);}
  function selectStoreCards(snapshot,start=0,selected=null) {
    if(!storeURL(snapshot?.url)||!Number.isInteger(start)||start<0)throw Error('店铺商品选择无效，请重新读取。');
    const list=(snapshot.links||[]).slice(start,start+20), chosen=selected===null?list:list.filter(link=>selected.has(productID(link.url)));
    if(!chosen.length)throw Error('请至少选择一款店铺商品。');
    return {...snapshot,links:chosen,scripts:[]};
  }
  function queuePage(pages, page) {
    if(!page||!page.confirmed_store||!storeURL(page.store_url)||!pageIdentity(page.snapshot))throw Error('请确认店铺链接与当前商品归属。');
    const key=pageIdentity(page.snapshot), copy=(Array.isArray(pages)?pages:[]).filter(p=>pageIdentity(p.snapshot)!==key);
    copy.push(page);if(copy.length>LIMITS.pages)throw Error('一批最多 20 个页面，请先导出本批。');
    // A detail page replaces the same product's store card; this never mixes
    // different SKU prices or manufactures a detail snapshot from a card.
    const details=new Set(copy.map(p=>productID(p.snapshot.url)).filter(Boolean)), seen=new Set();
    const deduped=copy.map(p=>{
      if(productID(p.snapshot.url))return p;
      const links=(p.snapshot.links||[]).filter(link=>{const gid=productID(link.url);if(!gid||details.has(gid)||seen.has(gid))return false;seen.add(gid);return true;});
      return {...p,snapshot:{...p.snapshot,links,scripts:[]}};
    }).filter(p=>productID(p.snapshot.url)||(p.snapshot.links||[]).length);
    for(const gid of details)seen.add(gid);
    if(seen.size>20)throw Error('本批超过 20 款商品，请先导出已有商品，清空后再采集下一批。');
    if(!deduped.length)throw Error('当前店铺页未读到商品卡片，请先滚动加载商品或打开商品详情页。');
    if(new TextEncoder().encode(JSON.stringify(envelope(deduped))).length>LIMITS.bytes)throw Error('本批文件超过 8 MB，请拆批导出。');
    return deduped;
  }
  function envelope(pages) {return {format:'midoo-page-capture',version:1,pages};}
  scope.MidooCaptureCore={LIMITS,text,id,productID,productURL,storeURL,imageURL,project,projectDocuments,parseScript,pageIdentity,selectStoreCards,queuePage,envelope};
  if(typeof module!=='undefined'&&module.exports)module.exports=scope.MidooCaptureCore;
})(globalThis);
