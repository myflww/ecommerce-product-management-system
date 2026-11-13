/* Manual listing UI contracts. Uses only synthetic records and local VM stubs. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
const listeners = new Map();
const events = [];
function makeElement(dataset = {}) {
  const attributes = new Map();
  return {
    innerHTML:'', textContent:'', style:{}, dataset, disabled:false,
    isConnected:true, scrollTop:0,
    classList:{add(){},remove(){},toggle(){}},
    setAttribute(name,value){attributes.set(name,String(value));},
    getAttribute(name){return attributes.get(name) ?? null;},
    removeAttribute(name){attributes.delete(name);},
    append(){},remove(){},focus(){},click(){},
    querySelector(){return null;},querySelectorAll(){return [];},
  };
}
function element(selector) {
  if(!elements.has(selector))elements.set(selector,makeElement());
  return elements.get(selector);
}
const overlay = element('#overlay-root');
const drawerBody = makeElement();
const drawer = makeElement();
overlay.querySelector = selector => {
  if(!overlay.innerHTML.includes('product-drawer'))return null;
  return selector==='.drawer-body' ? drawerBody : selector==='.product-drawer' ? drawer : null;
};
class TestCustomEvent {
  constructor(type,options={}){this.type=type;this.detail=options.detail;}
}
const context = vm.createContext({
  console, URL, URLSearchParams, structuredClone, Blob,
  CustomEvent:TestCustomEvent,
  document:{
    querySelector:element,querySelectorAll(){return [];},
    addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);},
    dispatchEvent(event){events.push(event);return true;},
    body:{style:{}},createElement:makeElement,
  },
  window:{addEventListener(){},scrollTo(){}},
  location:{hash:'#library'},history:{replaceState(){}},navigator:{},
  setTimeout(){return 1;},clearTimeout(){},
});
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/,'');
vm.runInContext(source,context);
const run = code => vm.runInContext(code,context);
const plain = value => JSON.parse(JSON.stringify(value));
const rows = [
  {id:'red-sku',platform:'Temu',store:'美国店',product_code:'001-RED',product_number:'SKC-001',status:'pending',price:'20',currency:'USD',link:'https://shop.example.com/shared'},
  {id:'blue-sku',platform:'Temu',store:'美国店',product_code:'001-BLUE',product_number:'SKC-001',status:'listed',price:'30',currency:'USD',link:'https://shop.example.com/shared'},
  {id:'green-sku',platform:'Temu',store:'美国店',product_code:'001-GREEN',product_number:'SKC-001',status:'archived',price:'40',currency:'USD',link:'https://shop.example.com/shared'},
];
const product = {
  id:'manual-product',code:'MD-QA',name:'独立 SKU 测试商品',owner:'测试运营',created_by:'qa-user',
  status:'pending',workflow_state:'listed',workflow_states:['pending','listed'],
  specs:'测试规格',notes:'保留原备注',images:[],listings:rows,
  created_at:'2026-11-13T01:00:00Z',updated_at:'2026-11-13T01:00:00Z',
};
function buttonFor(row=rows[0],nextStatus=row.status==='listed'?'archived':'listed') {
  return makeElement({productId:product.id,listingId:row.id,status:row.status,nextStatus});
}
function section(html,id) {
  const start=html.indexOf(`data-listing-detail="${id}"`);
  assert(start>=0,`SKU ${id} is visible`);
  const end=html.indexOf('data-listing-detail="',start+22);
  return html.slice(start,end<0?undefined:end);
}
function reset() {
  events.length=0;
  context.calls=[];context.messages=[];context.fixture=structuredClone(product);
  run(`state.user={id:'qa-user',name:'测试运营',role:'operator'};
    state.route='library';state.trashMode=false;state.libraryOffset=48;
    state.detail=structuredClone(fixture);state.detailEditing=false;
    state.listingStatusBusy.clear();state.products=[];state.stats={};
    toast=(message,error=false)=>messages.push({message,error});`);
  drawerBody.scrollTop=370;
  run('renderDetail()');
}

async function main(){
  reset();context.listingRows=rows;
  const editable=run(`listingDetails(listingRows,true,'manual-product')`);
  assert.equal((editable.match(/role="switch"/g)||[]).length,3,'Each sibling SKU has its own switch');
  for(const row of rows){
    const markup=section(editable,row.id);
    assert(markup.includes(`aria-checked="${row.status==='listed'}"`));
    assert(markup.includes(`data-listing-id="${row.id}"`)&&markup.includes(row.product_code));
    assert(markup.includes(`data-next-status="${row.status==='listed'?'archived':'listed'}"`));
  }
  assert(section(editable,'red-sku').includes('标记为已下架'),'Pending SKU can be explicitly archived without first listing it');
  assert(!section(editable,'blue-sku').includes('标记为已下架'),'Listed SKU uses its switch to archive');
  assert(!run('listingDetails(listingRows)').includes('role="switch"'),'Read-only view has no status controls');
  assert(!run(`listingDetails(listingRows,true,'')`).includes('role="switch"'),'Missing product identity cannot expose a writable control');
  assert(!run(`listingDetails([{...listingRows[0],id:''}],true,'manual-product')`).includes('role="switch"'));
  assert(run('listingDetails([])').includes('尚未登记平台'));
  context.unsafeRows=[{...rows[0],id:'bad" onclick="evil()',platform:'<script>evil()</script>',
    store:'<img src=x onerror=evil()>',product_code:'SKU" onclick="evil()',link:'javascript:evil()'}];
  const safe=run(`listingDetails(unsafeRows,true,'product" onclick="evil()')`);
  assert(!safe.includes('<script>')&&!safe.includes('<img')&&!safe.includes(' onclick="')&&!safe.includes('href="javascript:'));
  assert(safe.includes('&lt;script&gt;')&&safe.includes('&quot;'),'Labels and data attributes are escaped');

  // Delay the server acknowledgement: the switch must not turn green early.
  context.testButton=buttonFor();
  run(`api=(url,options={})=>{
    calls.push({url,options});
    if(options.method==='PATCH')return new Promise(resolve=>globalThis.resolvePatch=resolve);
    if(url==='/api/stats')return Promise.resolve({total_products:1,workflow_counts:{listed:1,pending:0}});
    if(url.startsWith('/api/products?'))return Promise.resolve({items:[state.detail],total:1});
    throw new Error('Unexpected request: '+url);
  };`);
  const request=run('toggleListingStatus(testButton)');
  assert.equal(run(`state.listingStatusBusy.has('red-sku')`),true);
  assert.equal(context.testButton.disabled,true);
  assert.equal(context.testButton.getAttribute('aria-busy'),'true');
  assert.equal(run('state.detail.listings[0].status'),'pending','No optimistic in-sale state before save succeeds');
  const busyMarkup=run(`listingDetails(state.detail.listings,true,state.detail.id)`);
  assert.match(section(busyMarkup,'red-sku'),/role="switch"[^>]*aria-checked="false"[^>]*disabled aria-busy="true"/);
  assert(!section(busyMarkup,'blue-sku').includes('disabled'),'Another SKU remains independently editable');
  await run('toggleListingStatus(testButton)');
  context.duplicateButton=buttonFor();
  await run('toggleListingStatus(duplicateButton)');
  assert.equal(context.calls.length,1,'Busy SKU rejects repeat clicks even from a second rendered control');
  context.saved=structuredClone(product);context.saved.listings[0].status='listed';
  run('resolvePatch({product:saved,listing:saved.listings[0]})');
  await request;
  assert.equal(context.calls[0].url,'/api/products/manual-product/listings/red-sku/status');
  assert.equal(context.calls[0].options.method,'PATCH');
  assert.deepEqual(plain(context.calls[0].options.body),{status:'listed',expected_status:'pending'},'Patch updates one SKU with its prior manual state');
  assert.equal(context.calls.filter(call=>call.options.method==='PATCH').length,1);
  assert(context.calls.some(call=>call.url==='/api/stats'),'Category counts refresh immediately');
  const libraryCall=context.calls.find(call=>call.url.startsWith('/api/products?'));
  assert(libraryCall,'Product list refreshes after the saved status');
  assert.equal(new URL(libraryCall.url,'http://localhost').searchParams.get('offset'),'0');
  assert.deepEqual(plain(run('state.detail.listings.map(row=>row.status)')),['listed','listed','archived'],'Shared SKC and link do not couple sibling states');
  assert.equal(run('state.products[0].listings[0].status'),'listed');
  assert.equal(run('state.listingStatusBusy.size'),0);
  assert.equal(drawerBody.scrollTop,370,'Updating the drawer preserves its scroll position');
  assert.match(section(overlay.innerHTML,'red-sku'),/role="switch"[^>]*aria-checked="true"/);
  assert(!section(overlay.innerHTML,'red-sku').includes('aria-busy="true"'),'Saved control is usable again');
  assert.equal(events.length,1);
  assert.equal(events[0].type,'midoo:listing-status-saved');
  assert.deepEqual(plain(events[0].detail),{productId:product.id,listingId:'red-sku'});
  assert(context.calls.every(call=>call.url.startsWith('/api/')),'Manual switch never requests the front-end shop URL');

  reset();context.testButton=buttonFor();
  run(`api=async(url,options={})=>{
    calls.push({url,options});
    if(options.method==='PATCH')throw new Error('保存失败，请重试');
    if(url==='/api/products/manual-product')return structuredClone(fixture);
    throw new Error('Unexpected request: '+url);
  };`);
  await run('toggleListingStatus(testButton)');
  assert.equal(run('state.detail.listings[0].status'),'pending','Failure keeps the prior manual state');
  assert.equal(run('state.listingStatusBusy.size'),0);
  assert.match(section(overlay.innerHTML,'red-sku'),/role="switch"[^>]*aria-checked="false"/);
  assert(!section(overlay.innerHTML,'red-sku').includes('disabled'),'Failed save restores a usable off switch');
  assert(context.messages.some(message=>message.error&&message.message.includes('保存失败')));
  assert.equal(events.length,0,'Failed save emits no successful status event');
  assert.deepEqual(context.calls.map(call=>call.url),['/api/products/manual-product/listings/red-sku/status','/api/products/manual-product'],'Failure only reloads the local product');

  for(const guard of [
    `state.user={id:'other',name:'其他运营',role:'operator'}`,
    'state.detail.deleted=true',
    'state.detailEditing=true',
    `state.detail.id='different-product'`,
    'state.detail=null',
  ]){
    reset();context.testButton=buttonFor();
    run(`api=async(url,options={})=>{calls.push({url,options});throw new Error('Must not call API');};${guard};`);
    await run('toggleListingStatus(testButton)');
    assert.equal(context.calls.length,0,`No writes when ${guard}`);
  }

  reset();context.testButton=buttonFor(rows[1]);
  run(`api=(url,options={})=>{
    calls.push({url,options});
    if(options.method==='PATCH')return new Promise(resolve=>globalThis.resolvePatch=resolve);
    if(url==='/api/stats')return Promise.resolve({total_products:1});
    if(url.startsWith('/api/products?'))return Promise.resolve({items:[],total:0});
    throw new Error('Unexpected request: '+url);
  };`);
  const late=run('toggleListingStatus(testButton)');
  run(`state.detail={...fixture,id:'now-open-product',name:'随后打开的商品'};`);
  context.saved=structuredClone(product);context.saved.listings[1].status='archived';
  run('resolvePatch({product:saved,listing:saved.listings[1]})');
  await late;
  assert.equal(run('state.detail.id'),'now-open-product','Late response cannot replace another open product');
  assert.equal(context.testButton.disabled,false);
  assert.equal(context.testButton.getAttribute('aria-busy'),null);
  assert.deepEqual(plain(context.calls[0].options.body),{status:'archived',expected_status:'listed'},'Turning off explicitly archives the selected SKU');

  console.log('MANUAL_LISTING_UI_SMOKE_OK: per-SKU accessible switches, manual archive, read-only access, escaping, single-row save, acknowledgement, busy deduplication, immediate refresh, failure recovery and late-response isolation');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
