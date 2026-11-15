/* Offline public-capture projection, privacy and bounded export regressions. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const core=require('../browser-extension/capture-core.js');
const BASE='https://www.temu.com/wax-g-700000000000005.html';
const STORE='https://www.temu.com/mall.html?mall_id=700000000000003';
const product=(extra={})=>({goods_id:'700000000000005',mall_id:'700000000000003',goods_name:'Fixture candle',image:'https://img.kwcdn.com/candle.jpg',skuList:[{sku_id:'900000000000201',spec:'红色 / 2件',price:'10.54',currency:'USD'}],...extra});
const page=(url=BASE)=>({snapshot:{url,product_title:'Fixture candle'},store_url:STORE,store_name:'Fixture shop',confirmed_store:true,captured_at:'2026-11-15T00:00:00.000Z'});

test('manifest limits privileges to explicit current-tab capture and local queue',()=>{
  const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../browser-extension/manifest.json'),'utf8'));
  assert.equal(manifest.manifest_version,3);
  assert.deepEqual(manifest.permissions,['activeTab','scripting','storage']);
  assert.equal(manifest.host_permissions,undefined);assert.equal(manifest.content_scripts,undefined);assert.equal(manifest.background,undefined);
  assert.equal(manifest.action.default_icon,undefined);
});
test('canonical links keep identity and remove sessions, referrer and credentials',()=>{
  assert.equal(core.productURL(BASE+'?sku_id=200&_x_sessn_id=secret&refer_page_id=private#account'),BASE+'?sku_id=200');
  assert.equal(core.storeURL(STORE+'&_x_sessn_id=private&no_cache_id=x'),STORE);
  for(const url of ['http://www.temu.com/x-g-1.html','https://eviltemu.com/x-g-1.html','https://u:p@www.temu.com/x-g-1.html','https://www.temu.com:444/x-g-1.html','https://www.temu.com/x-g-1.html?goods_id=2','https://www.temu.com/x-g-1.html?sku_id=3&sku_id=4'])assert.equal(core.productURL(url),'');
  assert.equal(core.imageURL('https://img.kwcdn.com/a.jpg?session=secret&width=800#x'),'https://img.kwcdn.com/a.jpg?width=800');
});
test('only matching product subtrees reach export; private hydration stays out',()=>{
  const raw={account:{email:'fixture-private@example.invalid',token:'secret-session',addresses:['private-address']},page:{goods:product({password:'private-password',recommendations:[product({goods_name:'Recommendation private'})],analytics:{session:'private-analytics'}})},related:[product({goods_name:'Related private'})]};
  const result=core.projectDocuments([JSON.stringify(raw)],BASE);
  assert.equal(result.products.length,1);assert.equal(result.products[0].goods_name,'Fixture candle');
  const encoded=JSON.stringify(result.scripts);
  for(const forbidden of ['private@example','secret-session','private-address','private-password','private-analytics','Recommendation private','Related private','account','analytics'])assert.ok(!encoded.includes(forbidden),forbidden);
  assert.deepEqual(JSON.parse(result.scripts[0].text).products[0].skuList[0],{sku_id:'900000000000201',spec:'红色 / 2件',price:'10.54',currency:'USD'});
});
test('matching goods inside sensitive ancestor branches are excluded before projection',()=>{
  for(const key of ['account','orders','payment','address','userInfo','session','customer','checkout','profile','login','auth','token','orderHistory']){
    const raw={[key]:{goods:[product({goods_name:'PRIVATE RECIPIENT',sku:'PRIVATE ORDER CODE',image:'https://img.kwcdn.com/private-profile.jpg'})]},publicProduct:product()};
    const result=core.projectDocuments([JSON.stringify(raw)],BASE);assert.equal(result.products.length,1,key);assert.ok(!JSON.stringify(result).includes('PRIVATE'),key);assert.ok(!JSON.stringify(result).includes('private-profile'),key);
  }
});
test('scripts are parsed as JSON and arbitrary JavaScript never runs',()=>{
  assert.equal(core.parseScript('window.rawData = '+JSON.stringify(product())+';').goods_id,'700000000000005');
  for(const raw of ['window.rawData = {goods_id:123};','window.rawData = JSON.parse("{}");','{"goods_id":"1"};globalThis.attack=true','(()=>({goods_id:1}))()'])assert.equal(core.parseScript(raw),null);
  const raw='{"goods_id":"700000000000005","__proto__":{"polluted":true},"constructor":{"secret":1},"goods_name":"Fixture"}';
  assert.ok(!JSON.stringify(core.projectDocuments([raw],BASE)).includes('polluted'));assert.equal({}.polluted,undefined);
  assert.equal(core.parseScript('{"price":10.0000000000000001}').price,'10.0000000000000001');
  assert.equal(core.parseScript('{"price":10.54}').price,'10.54');
  assert.equal(core.project({price:10},BASE).price,'','unproven numeric values cannot become price evidence');
});
test('numeric identity fields retain exact canonical integer lexemes and reject rounded fractions',()=>{
  const names=['goods_id','goodsId','sku_id','skuId','variant_id','variantId','mall_id','mallId','store_id'];
  for(const key of names){
    assert.equal(core.parseScript('{"'+key+'":201}')[key],'201',key);
    assert.equal(core.parseScript('{"'+key+'":9007199254740993}')[key],'9007199254740993',key);
    for(const literal of ['201.0000000000000001','201.0','201e0','2.01e2','-201','1234567890123456789012345678901'])assert.equal(core.parseScript('{"'+key+'":'+literal+'}')[key],'__invalid__',key+' '+literal);
  }
  const raw='{"goods_id":700000000000005,"mall_id":700000000000003,"goods_name":"Fixture","skuList":[{"sku_id":201.0000000000000001,"spec":"red","price":"10","currency":"USD"}]}';
  const result=core.projectDocuments([raw],BASE);assert.equal(result.products[0].skuList[0].sku_id,'__invalid__');
});
test('negative quote identity, unit and currency evidence is retained for server validation',()=>{
  const p=product({skuList:[{sku_id:'900000000000201',spec:'red',price:'10.54',currency:'USD',currencyCode:'CNY',priceUnit:'cents',offers:{'@type':'Offer',sku_id:'900000000000999',url:'https://other.example/offer',price:'5',priceCurrency:'USD'}}]});
  const v=core.projectDocuments([JSON.stringify(p)],BASE).products[0].skuList[0];
  assert.equal(v.currencyCode,'CNY');assert.equal(v.priceUnit,'present');assert.equal(v.offers.sku_id,'900000000000999');assert.equal(v.offers.url,'__invalid__');
});
test('all supported URL identity aliases and malformed quote aliases remain rejecting evidence',()=>{
  for(const key of ['skuId','variant_id','variantId']){
    const url=BASE+'?'+key+'=999&session=private';
    assert.equal(core.productURL(url),BASE+'?'+key+'=999');
    const p=core.project(product({skuList:[{sku_id:'201',price:'10',currency:'USD',url}]}),BASE);
    assert.equal(new URL(p.skuList[0].url).searchParams.get(key),'999');
    assert.equal(core.pageIdentity({url}),BASE);
  }
  const malformed=core.project(product({skuList:[{sku_id:'201',price:'10',currency:'USD',currencyCode:123,sku:{secret:'do-not-copy'}},{sku_id:'202',price:'12',currency:'USD',sku:'a'.repeat(201)},{sku_id:'203',price:'10'+' '.repeat(30)+'garbage',currency:'USD'}]}),BASE).skuList;
  assert.equal(malformed[0].currencyCode,'INVALID');assert.equal(malformed[0].sku,false);assert.equal(malformed[1].sku,false);assert.equal(malformed[2].price,'');assert.ok(!JSON.stringify(malformed).includes('do-not-copy'));
  const aggregate=core.project(product({skuList:[{sku_id:'201',offers:{'@type':['Offer','AggregateOffer'],price:'10',priceCurrency:'USD'}}]}),BASE);
  assert.equal(aggregate.skuList[0].offers['@type'],'AggregateOffer');
});
test('store projection includes explicit owned product nodes and no other shops',()=>{
  const result=core.projectDocuments([JSON.stringify({owned:[product()],foreign:[product({goods_id:'900000000000199',mall_id:'999'})]})],STORE);
  assert.equal(result.products.length,1);assert.equal(result.products[0].mall_id,'700000000000003');
});
test('bounded projection does not preserve arbitrary recursive payloads',()=>{
  const p=product({skuList:Array.from({length:1000},(_,i)=>({sku_id:String(300+i),name:'x'.repeat(10000),unknown:'secret'}))});
  const projected=core.project(p,BASE);
  assert.equal(projected.skuList.length,200);assert.equal(projected.skuList[0].name.length,500);assert.equal(projected.skuList[0].unknown,undefined);
  assert.equal(core.parseScript(' '.repeat(core.LIMITS.scriptChars+1)),null);
  const deep={};let n=deep;for(let i=0;i<50;i++){n.child={};n=n.child;}n.product=product();assert.equal(core.projectDocuments([JSON.stringify(deep)],BASE).products.length,0);
});
test('queue requires explicit store confirmation and replaces same-product SKU URLs',()=>{
  assert.throws(()=>core.queuePage([], {...page(),confirmed_store:false}),/确认/);
  assert.throws(()=>core.queuePage([], {...page(),store_url:'https://evil.example/mall.html?mall_id=1'}),/确认/);
  const first=page(BASE+'?sku_id=201'),second={...page(BASE+'?sku_id=202'),store_name:'Updated name'};
  const result=core.queuePage(core.queuePage([],first),second);assert.equal(result.length,1);assert.equal(result[0].store_name,'Updated name');assert.equal(result[0].snapshot.url,BASE+'?sku_id=202');
});
test('queue is bounded to 20 pages and 8MiB without changing prior queue on failure',()=>{
  const pages=Array.from({length:20},(_,i)=>page('https://www.temu.com/x-g-'+(i+1)+'.html'));
  assert.throws(()=>core.queuePage(pages,page()),/20/);assert.equal(pages.length,20);
  const huge=page();huge.snapshot.scripts=[{text:'x'.repeat(core.LIMITS.bytes)}];assert.throws(()=>core.queuePage([],huge),/8 MB/);
  assert.deepEqual(Object.keys(core.envelope([page()])),['format','version','pages']);
});
test('detail capture replaces store card without duplicating product; combined goods limit applies',()=>{
  const shop=page(STORE);shop.snapshot.links=Array.from({length:20},(_,i)=>({url:'https://www.temu.com/x-g-'+(i+1)+'.html',name:'Card '+i}));
  const detail=page('https://www.temu.com/x-g-1.html');
  const result=core.queuePage([shop],detail);assert.equal(result.length,2);assert.equal(result[0].snapshot.links.length,19);assert.equal(shop.snapshot.links.length,20);
  assert.throws(()=>core.queuePage([shop],page()),/20 款/);
  const noGoods=page(STORE);noGoods.snapshot.links=[];assert.throws(()=>core.queuePage([],noGoods),/未读到商品/);
});
test('collector reads public evidence without cookie, storage, requests or page mutations',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../browser-extension/collector.js'),'utf8');
  const root={querySelectorAll:()=>[],getClientRects:()=>[{}]},scripts=[{type:'application/json',id:'app',className:'',textContent:JSON.stringify({account:{token:'private-token'},data:product()})}];
  const document={querySelector:selector=>selector==='main,[role="main"]'?root:selector==='meta[property="og:image"]'?{content:'https://img.kwcdn.com/candle.jpg'}:null,body:root,title:'Fixture candle - Temu',scripts,createTreeWalker:()=>({nextNode:()=>null})};
  Object.defineProperty(document,'cookie',{get(){throw Error('Cookie read forbidden');}});
  const context=vm.createContext({MidooCaptureCore:core,document,location:{href:BASE+'?session=private'},NodeFilter:{SHOW_ELEMENT:1},URL,Map,getComputedStyle(){throw Error('No elements');}});
  vm.runInContext(source,context);const result=context.MidooCaptureCollector.capture();
  assert.equal(result.snapshot.url,BASE);assert.equal(result.detected_store_url,STORE);assert.equal(result.type,'product');
  assert.equal(result.snapshot.text,'');assert.ok(!JSON.stringify(result).includes('private-token'));
  assert.equal(result.snapshot.variants.length,0,'never synthesize selected-price Cartesian variants');
  assert.throws(()=>{context.location.href='https://www.temu.com/search_result.html?search_key=candle';context.MidooCaptureCollector.capture();},/搜索页/);
  for(const forbidden of ['document.cookie','localStorage','sessionStorage','fetch(','XMLHttpRequest','webRequest','eval(','innerHTML'])assert.ok(!source.includes(forbidden),forbidden);
});
test('all shipped scripts pass parser checks and popup DOM insertion is text only',()=>{
  for(const file of ['capture-core.js','collector.js','popup.js'])new vm.Script(fs.readFileSync(path.join(__dirname,'../browser-extension',file),'utf8'),{filename:file});
  const popup=fs.readFileSync(path.join(__dirname,'../browser-extension/popup.js'),'utf8');
  for(const forbidden of ['innerHTML','outerHTML','document.write','fetch(','document.cookie'])assert.ok(!popup.includes(forbidden),forbidden);
  assert.ok(popup.includes('chrome.scripting.executeScript'));assert.ok(popup.includes('URL.createObjectURL'));
});
test('store collector stops at recommendations and selection can reach later visible cards',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../browser-extension/collector.js'),'utf8');
  const root={querySelectorAll:()=>[],getClientRects:()=>[{}]};
  function card(i){return {id:'',className:'product-card',parentElement:root,childElementCount:2,innerText:'Candle '+i,href:'https://www.temu.com/candle-g-'+i+'.html?session=private',closest:()=>null,getClientRects:()=>[{}],getAttribute:()=>null,matches:s=>s==='a[href]',querySelector:()=>({alt:'Candle '+i,currentSrc:'https://img.kwcdn.com/'+i+'.jpg'})};}
  const boundary={...card(999),childElementCount:0,innerText:'猜你喜欢',matches:s=>s==='h2,h3,[role="heading"]'};
  const sequence=[...Array.from({length:25},(_,i)=>card(i+1)),boundary,card(999)];let cursor=0;
  const document={querySelector:s=>s==='main,[role="main"]'?root:null,body:root,title:'Temu | Fixture shop',scripts:[],createTreeWalker:()=>({nextNode:()=>sequence[cursor++]||null})};
  root.getAttribute=()=>null;
  const context=vm.createContext({MidooCaptureCore:core,document,location:{href:STORE},NodeFilter:{SHOW_ELEMENT:1},URL,Map,Set,getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})});
  vm.runInContext(source,context);const result=context.MidooCaptureCollector.capture();
  assert.equal(result.store_link_count,25);assert.equal(result.snapshot.links.length,25);assert.equal(result.snapshot.recommendation_boundary_seen,true);
  assert.equal(result.snapshot.scripts.length,0);assert.equal(result.snapshot.links[0].url,'https://www.temu.com/candle-g-1.html');assert.ok(!JSON.stringify(result).includes('private'));
  assert.ok(!result.snapshot.links.some(l=>l.url.includes('999')));
  const first=core.selectStoreCards(result.snapshot,0),second=core.selectStoreCards(result.snapshot,20);
  assert.equal(first.links.length,20);assert.equal(second.links.length,5);assert.equal(second.links[0].url,'https://www.temu.com/candle-g-21.html');
  assert.equal(core.selectStoreCards(result.snapshot,20,new Set(['22'])).links.length,1);
  assert.throws(()=>core.selectStoreCards(result.snapshot,20,new Set()),/至少选择/);
});

function popupHarness({readError=false,storePage=false}={}){
  const source=fs.readFileSync(path.join(__dirname,'../browser-extension/popup.js'),'utf8');
  const elements=new Map(),listeners=new Map(),store={},downloads=[];let injections=0;
  const element=()=>({textContent:'',value:'',checked:false,hidden:false,disabled:false,children:[],classList:{toggle(){}},replaceChildren(){this.children=[];},append(...children){this.children.push(...children);},setAttribute(){},addEventListener(type,fn){this.listeners??={};this.listeners[type]=fn;},click(){if(this.download)downloads.push({name:this.download,url:this.href});}});
  const document={getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:()=>element()};
  const chrome={tabs:{query:async()=>[{id:1,url:storePage?STORE:BASE}]},scripting:{executeScript:async args=>{injections++;if(readError)throw Error('页面不可访问');return args.files?[]:[{frameId:0,result:{snapshot:{url:storePage?STORE:BASE,title:'<script>not HTML</script>',product_title:'Fixture candle',scripts:[],links:storePage?Array.from({length:45},(_,i)=>({url:'https://www.temu.com/x-g-'+(i+1)+'.html',name:'Card '+i})):[],variants:[]},detected_store_url:STORE,detected_store_name:'Fixture shop',type:storePage?'store':'product',public_product_count:storePage?45:1}}];}},storage:{local:{get:async key=>({[key]:store[key]}),set:async obj=>Object.assign(store,obj),remove:async key=>{delete store[key];}}}};
  const URLFake=class extends URL{};URLFake.createObjectURL=()=> 'blob:fixture';URLFake.revokeObjectURL=()=>{};
  vm.runInNewContext(source,{MidooCaptureCore:core,document,chrome,TextEncoder,Blob,URL:URLFake,Date,Set,Map,setTimeout:()=>0});
  const tick=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
  return {elements,store,downloads,tick,injections:()=>injections,async click(id){elements.get(id).listeners.click();await tick();}};
}
test('popup requires explicit confirmation, persists only accepted capture and exports locally',async()=>{
  const h=popupHarness();await h.tick();assert.equal(h.elements.get('export').disabled,true);
  await h.click('read');assert.equal(h.injections(),2);assert.equal(h.elements.get('store-url').value,STORE);assert.equal(h.elements.get('capture').hidden,false);
  await h.click('add');assert.match(h.elements.get('status').textContent,/确认/);assert.equal(Object.keys(h.store).length,0);
  h.elements.get('confirmed').checked=true;await h.click('add');assert.equal(h.elements.get('count').textContent,'1');assert.equal(h.elements.get('export').disabled,false);
  assert.equal(h.store.midoo_public_capture_v1[0].confirmed_store,true);
  await h.click('export');assert.equal(h.downloads.length,1);assert.match(h.downloads[0].name,/^midoo-page-capture-.*\.json$/);assert.equal(h.store.midoo_public_capture_v1.length,1,'export does not discard capture before import');
  await h.click('clear');assert.equal(h.elements.get('count').textContent,'0');assert.equal(h.elements.get('export').disabled,true);assert.equal(h.store.midoo_public_capture_v1,undefined);
});
test('popup injection failure keeps queue empty and offers a visible error',async()=>{
  const h=popupHarness({readError:true});await h.tick();await h.click('read');
  assert.match(h.elements.get('status').textContent,/页面不可访问/);assert.equal(h.elements.get('capture').hidden,true);assert.equal(h.elements.get('read').disabled,false);assert.equal(Object.keys(h.store).length,0);
});
test('popup store selector reaches range 21–40 and persists only individually checked cards',async()=>{
  const h=popupHarness({storePage:true});await h.tick();await h.click('read');
  assert.equal(h.elements.get('store-selection').hidden,false);assert.equal(h.elements.get('store-batch').children.length,3);assert.equal(h.elements.get('store-cards').children.length,20);
  h.elements.get('store-batch').value='20';h.elements.get('store-batch').listeners.change();
  const first=h.elements.get('store-cards').children[0];assert.equal(first.children[1].textContent,'Card 20');
  first.children[0].checked=false;first.children[0].listeners.change();assert.match(h.elements.get('selected-count').textContent,/19/);
  h.elements.get('confirmed').checked=true;await h.click('add');
  const captured=h.store.midoo_public_capture_v1[0].snapshot;
  assert.equal(captured.links.length,19);assert.equal(captured.links[0].url,'https://www.temu.com/x-g-22.html');assert.equal(captured.links.at(-1).url,'https://www.temu.com/x-g-40.html');assert.equal(captured.scripts.length,0);
});
