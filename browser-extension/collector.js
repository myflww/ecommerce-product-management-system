/* Invoked only after the user clicks the extension. No requests or page writes. */
(function (scope) {
  'use strict';
  function capture() {
    const c=scope.MidooCaptureCore;
    const url=c.productURL(location.href)||c.storeURL(location.href);
    if(!url)throw Error('请先打开 Temu 商品详情页或店铺页，再点击采集。搜索页、登录页不能导入。');
    const root=document.querySelector('main,[role="main"]')||document.body;
    if(!root)throw Error('页面还未加载，请稍后重试。');
    const plugin=/temu[-_ ]?(?:helper|assistant)|selection[-_ ]?(?:helper|assistant)|mango|芒果|选品助手|供货助手|extension[-_ ]?(?:root|panel)/i;
    const related=/recommend|related|sponsored|you.may|also.bought|猜你喜欢|推荐|相似商品/i;
    const privateArea=/account|checkout|address|payment|profile|login|password|wallet|user[-_ ]?info|order[-_ ]?(?:history|detail|summary)|账号|账户|收货地址|支付|登录/i;
    const boundary=/^(?:为您精选|为你精选|为你推荐|猜你喜欢|推荐商品|相似商品|selected for you|picked for you|recommended for you|you may also like|you might also like)\s*[:：]?$/i;
    const context=e=>{const v=[];for(let n=e,d=0;n&&d++<6;n=n.parentElement){v.push(String(n.id||'').slice(0,100),String(n.className||'').slice(0,150),n.getAttribute('aria-label')||'');if(n===root)break;}return v.join(' ').slice(0,800);};
    const visible=e=>{if(!e||e.closest('[hidden],[aria-hidden="true"],nav,header,footer,form,input,textarea,select,[contenteditable]:not([contenteditable="false"])')||!e.getClientRects().length)return false;for(let n=e,d=0;n&&d++<20;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;if(n===root)break;}return true;};
    const raw=[];let chars=0;
    for(const s of document.scripts){const t=s.textContent||'';if(raw.length>=c.LIMITS.scripts)break;if(s.src||plugin.test(String(s.id)+' '+String(s.className))||!t||t.length>c.LIMITS.scriptChars||chars+t.length>c.LIMITS.scriptChars)continue;if(s.type==='application/ld+json'||s.type==='application/json'||s.id==='__NEXT_DATA__'||/^\s*(?:window\.)?(?:rawData|__INITIAL_STATE__|__PRELOADED_STATE__)\s*=/.test(t)){raw.push(t);chars+=t.length;}}
    const projected=c.projectDocuments(raw,url);
    const gid=c.productID(url), products=projected.products;
    const product=products.find(p=>Array.isArray(p.skuList)||Array.isArray(p.sku_list)||Array.isArray(p.hasVariant))||products[0]||{};
    const heading=[...root.querySelectorAll('h1,[itemprop="name"]')].find(e=>visible(e)&&!plugin.test(context(e))&&!related.test(context(e)));
    const cleanTitle=c.text(document.title,500);
    const snapshot={url,title:cleanTitle,store_name:'',product_title:c.text(heading?.innerText,500),product_image:c.imageURL(document.querySelector('meta[property="og:image"]')?.content||'',url),seller:{},links:[],scripts:projected.scripts,variants:[],text:'',recommendation_boundary_seen:false};
    // Only an explicit product seller area establishes seller-link evidence.
    const sellers=[];let nodes=0;const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT);let node;
    while((node=walker.nextNode())&&nodes++<c.LIMITS.nodes){
      if(!visible(node))continue;const ctx=context(node);if(plugin.test(ctx)||privateArea.test(ctx))continue;
      const headingNode=node.childElementCount<2||node.matches('h2,h3,[role="heading"]');
      if(headingNode&&boundary.test(c.text(node.innerText,100))){snapshot.recommendation_boundary_seen=true;break;}
      if(!node.matches('a[href]'))continue;
      const shop=c.storeURL(node.href,url);
      if(shop&&gid&&!related.test(ctx)&&/seller|merchant|mall[-_ ]?info|store[-_ ]?info|店铺|商家/i.test(ctx))sellers.push({url:shop,name:c.text(node.innerText,160)});
      const productLink=c.productURL(node.href,url);
      if(!gid&&productLink&&!related.test(ctx)&&snapshot.links.length<c.LIMITS.links){const img=node.querySelector('img');let owner='';for(let p=node,d=0;p&&d++<5;p=p.parentElement){owner=c.id(p.getAttribute('data-mall-id')||p.getAttribute('data-mall_id'));if(owner)break;}snapshot.links.push({url:productLink,name:c.text(img?.alt||node.getAttribute('aria-label')||node.getAttribute('title')||node.innerText,500),image:c.imageURL(img?.currentSrc||img?.src||'',url),mall_id:owner,context:c.text(ctx,800)});}
    }
    const foundLinks=snapshot.links.length;
    if(!gid){const seen=new Set();snapshot.links=snapshot.links.filter(link=>{const id=c.productID(link.url);if(seen.has(id))return false;seen.add(id);return true;});snapshot.scripts=[];}
    let detectedStore=c.storeURL(url), detectedName='';
    if(gid){
      const owner=product.mall||product.mallInfo||product.mall_info||product.seller||product.store||{};
      const mall=c.id(product.mall_id||product.mallId||product.store_id||owner.mall_id||owner.mallId||owner.store_id);
      detectedStore=mall?'https://www.temu.com/mall.html?mall_id='+mall:c.storeURL(owner.url||owner.mallUrl||owner.mall_url||'');
      detectedName=c.text(product.mallName||product.mall_name||product.storeName||product.store_name||owner.mallName||owner.mall_name||owner.storeName||owner.store_name||owner.name,160);
      const unique=[...new Map(sellers.map(s=>[s.url,s])).values()];
      if(unique.length===1){snapshot.seller={scope:'product_seller',mall_id:new URL(unique[0].url).searchParams.get('mall_id'),name:unique[0].name,url:unique[0].url};detectedStore=detectedStore||unique[0].url;detectedName=detectedName||unique[0].name;}
      snapshot.product_title=snapshot.product_title||c.text(product.goods_name||product.goodsName||product.name||product.title,500);
      if(!snapshot.product_title)snapshot.product_title=c.text(document.querySelector('meta[property="og:title"]')?.content,500);
    } else {
      const match=cleanTitle.match(/^Temu\s*[|｜]\s*(.+)$/i);
      detectedName=c.text(match?.[1]||heading?.innerText,160);snapshot.store_name=detectedName;
    }
    // Retain only a fixed diagnostic marker, never body/account/payment text.
    if(/captcha|verify (?:that )?you are human|sign in to (?:continue|view)|log in to (?:continue|view)|人机验证|请先登录|完成验证码/i.test(cleanTitle))snapshot.text='请先登录或完成验证码';
    return {snapshot,detected_store_url:detectedStore,detected_store_name:detectedName,type:gid?'product':'store',public_product_count:gid?1:snapshot.links.length,store_link_count:foundLinks,bounded:projected.bounded||nodes>=c.LIMITS.nodes||foundLinks>=c.LIMITS.links};
  }
  scope.MidooCaptureCollector={capture};
})(globalThis);
