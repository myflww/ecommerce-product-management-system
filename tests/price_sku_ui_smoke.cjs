/* Price/SKU UI behavior checks. Uses fabricated records and an in-memory DOM. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(className = '') {
    this.className = className;
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.children = [];
    this.isConnected = true;
    this.disabled = false;
    this.textContent = '';
    this.scrollHeight = 240;
    this.records = [];
    this.forms = [];
    this.classList = {contains:name=>this.className.split(/\s+/).includes(name),toggle(){}};
  }
  set innerHTML(value) {
    this.html = value;
    this.records = [...value.matchAll(/<details class="mpt-record" data-price-record="([^"]+)"/g)].map(match => {
      const record = new Element('mpt-record');
      record.dataset.priceRecord = match[1];
      record.open = false;
      return record;
    });
  }
  get innerHTML() {return this.html || '';}
  setAttribute(key,value) {this.attributes.set(key,String(value));}
  getAttribute(key) {return this.attributes.get(key) ?? null;}
  append(child) {this.children.push(child);child.parent=this;child.isConnected=true;}
  insertBefore(child,before) {this.children.splice(this.children.indexOf(before),0,child);child.parent=this;}
  remove() {this.isConnected=false;}
  focus() {this.focused=true;}
  getBoundingClientRect() {return {top:180,bottom:210,left:280,right:580};}
  closest(selector) {return selector==='.product-card' ? this.card || null : null;}
  querySelectorAll(selector) {
    if(selector==='.mpt-record')return this.records;
    if(selector==='.mpt-record[open]')return this.records.filter(row=>row.open);
    if(selector==='.mpt-record-form')return this.forms;
    return [];
  }
  querySelector(selector) {
    if(selector==='.mpt-record')return this.records[0] || null;
    return this.children.find(child => selector.startsWith('.') ? child.className.split(/\s+/).includes(selector.slice(1)) : selector==='[data-action="edit-card"]' ? child.dataset.action==='edit-card' : false) || null;
  }
}

class Form extends Element {
  constructor(kind,id,values) {
    super(kind==='listing' ? 'mpt-record-form' : '');
    this.dataset={priceForm:kind,listingId:id};
    this.values={...values};
    this.error=new Element();
    this.submit=new Element();
    this.controls=Object.keys(values).map(()=>new Element());
    this.controls.push(this.submit);
  }
  querySelector(selector) {
    if(selector==='.mpt-form-error')return this.error;
    if(selector==='[type=submit]')return this.submit;
    if(selector==='[name=source_url]')return {value:this.values.source_url || ''};
    return null;
  }
  querySelectorAll(selector) {return selector==='input,textarea,button' ? this.controls : [];}
  closest(selector) {
    if(selector==='.mpt-record')return this.record || null;
    if(selector==='form[data-price-form]')return this;
    if(selector==='.mpt-settings')return this.settingsHost || null;
    return null;
  }
}

class FormDataMock {
  constructor(form) {this.values=Object.entries(form.values).filter(([,value])=>value!==false&&value!=null);}
  get(key) {return this.values.find(([name])=>name===key)?.[1] ?? null;}
  has(key) {return this.values.some(([name])=>name===key);}
}

const listeners = new Map();
const cards = [];
const events = [];
const calls = [];
const body = new Element();
const document = {
  readyState:'loading',body,
  createElement:()=>new Element(),
  querySelector:selector=>selector==='#content' ? body : null,
  querySelectorAll(selector) {
    if(selector==='.product-card[data-product-card]')return cards;
    if(selector==='.mpt-toggle'||selector==='.mpt-manage')return cards.flatMap(card=>[...card.children,...(card.actions?.children||[])]).filter(child=>child.classList.contains(selector.slice(1)));
    return [];
  },
  addEventListener(name,listener) {if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(listener);},
  dispatchEvent(event) {events.push(event);},
};
let respond = async () => {throw new Error('Unexpected HTTP request');};
const context = vm.createContext({
  console,URL,Intl,AbortController,FormData:FormDataMock,document,
  window:{innerWidth:1200,innerHeight:800,addEventListener(){}},
  CustomEvent:class {constructor(type,options){this.type=type;this.detail=options?.detail;}},
  MutationObserver:class {observe(){}},
  requestAnimationFrame:()=>1,setTimeout:()=>1,clearTimeout(){},
  fetch:async (url,options={})=>{
    calls.push({url,method:options.method||'GET',body:options.body ? JSON.parse(options.body) : null});
    const response=await respond(calls.at(-1));
    return {ok:response.status==null||response.status<400,status:response.status||200,json:async()=>response.value};
  },
});
const source=fs.readFileSync(path.join(__dirname,'../static/price-tracking.js'),'utf8').replace(/^export /gm,'');
vm.runInContext(source,context);
const run=code=>vm.runInContext(code,context);
const plain=value=>JSON.parse(JSON.stringify(value));
const row={id:'listing-A',platform:'Temu',store:'Main store',product_number:'000-SKC',product_code:'001-RED',
  original_price:'20.00',current_price:'15',currency:'USD',current_currency:'USD',ratio_percent:'75.0',
  source_url:'https://shop.example.com/product/123',region:'US',variant_hint:'Red / 2 pieces',enabled:false,
  revision:2,status:'ok',stale:false};
const second={...row,id:'listing-B',product_code:'002-BLUE',variant_hint:'Blue / 4 pieces',original_price:'40',current_price:'10',ratio_percent:'25.0'};
context.row=row;context.second=second;

function card(editable=true,trash=false) {
  const item=new Element('product-card');
  item.dataset.productCard='product-'+cards.length;
  item.actions=new Element('product-actions');
  if(editable){const edit=new Element();edit.dataset.action='edit-card';item.actions.append(edit);}
  const query=item.querySelector.bind(item);
  item.querySelector=selector=>selector==='.product-actions' ? item.actions : selector==='[data-action="restore-product"]' ? trash ? new Element() : null : query(selector);
  const append=item.append.bind(item);
  item.append=child=>{child.card=item;append(child);};
  const insert=item.actions.insertBefore.bind(item.actions);
  item.actions.insertBefore=(child,before)=>{child.card=item;insert(child,before);};
  cards.push(item);
  return item;
}

function listingValues(value) {
  return Object.fromEntries(['product_code','product_number','currency','original_price','source_url','region','variant_hint'].map(key=>[key,value[key]]));
}

async function main() {
  row.context_token='ctx-red';second.context_token='ctx-blue';
  let html=run('renderPriceSummary({can_edit:true,listings:[row,second]})');
  assert.equal((html.match(/<th scope="col"/g)||[]).length,5);
  assert(html.includes('001-RED')&&html.includes('002-BLUE')&&html.includes('75%')&&html.includes('25%'));
  assert(html.includes('前端 ↗')&&html.includes('手动输入')&&html.includes('保存手填前端价'));
  assert(!html.includes('mpt-manager')&&!html.includes('追踪设置')&&!html.includes('name="enabled"'));
  assert(!run('renderPriceSummary({can_edit:false,listings:[row]})').includes('<form'));
  const escaped=run('renderPriceRecord({...row,platform:"<script>bad</script>",product_code:"<img src=x>",source_url:"javascript:alert(1)"},true)');
  assert(!escaped.includes('<script>')&&!escaped.includes('<img src=x>')&&!escaped.includes('javascript:'));
  assert.equal(run('priceRatio({...row,current_currency:"CNY"})'),null);
  const converted={...row,original_price:'74.62',currency:'CNY',current_price:'10.54',current_currency:'USD',ratio_percent:'94.2',fx:{available:true,status:'ok',rate:'6.668260987275517',converted_amount:'70.283470805884',from_currency:'USD',to_currency:'CNY',as_of:'2026-11-12',source_name:'ECB参考汇率'}};
  context.converted=converted;
  assert.equal(run('priceRatio(converted)'),94.2);
  const convertedHTML=run('renderPriceRecord(converted,true)');
  assert(convertedHTML.includes('≈94.2%')&&convertedHTML.includes('≈ CNY 70.28'));
  assert(convertedHTML.includes('6.668261')&&convertedHTML.includes('2026-11-12')&&convertedHTML.includes('ECB参考汇率'));
  assert(!convertedHTML.includes('币种不同不计算'));
  assert.equal(run('priceRatio({...converted,fx:{...converted.fx,status:"stale"}})'),null);
  assert.equal(run('priceRatio({...converted,fx:{...converted.fx,from_currency:"EUR"}})'),null);
  assert.equal(run('priceRatio({...converted,fx:null})'),null);
  const convertedDraft=run('renderPriceRecord(converted,true,{price:"9",currency:"USD",expected_context:converted.context_token})');
  assert(!convertedDraft.includes('≈94.2%')&&!convertedDraft.includes('≈ CNY 70.28'));
  assert(run('renderPriceRecord({...row,stale:true},false)').includes('未更新'));
  const settings=run('renderPriceSettings({can_edit:true,enabled:true})');
  assert(!settings.includes('daily_time')&&!settings.includes('name="enabled"'));

  const item=card();cards.push(item);run('mount()');
  assert(item.children.some(child=>child.className==='mpt-toggle'));
  assert(!item.actions.children.some(child=>child.className.includes('mpt-manage')));
  respond=async call=>({value:call.url.endsWith('/capabilities')?{available:true,can_use:true}:{can_edit:true,listings:[row,second]}});
  context.toggle=item.children.find(child=>child.className==='mpt-toggle');
  await run('openProduct(toggle)');
  assert(run('activePanel.root.innerHTML').includes('采集价格'));
  assert(!run('activePanel.root.innerHTML').includes('价格 / SKU 管理'));
  const panel=run('activePanel');
  const manual=new Form('manual-price',row.id,{price:'12.5',currency:'USD'});context.manual=manual;
  calls.length=0;events.length=0;
  respond=async call=>({value:call.method==='POST'?{...row,current_price:'12.5',source:'manual_entry'}:{can_edit:true,listings:[{...row,current_price:'12.5',ratio_percent:'62.5'},second]}});
  await run('saveManualPrice(manual)');
  assert.deepEqual(calls[0],{url:'/api/price-tracking/listings/listing-A/manual-price',method:'POST',body:{price:'12.5',currency:'USD',expected_context:'ctx-red'}});
  assert(!run('activePanel.manualDrafts.has(row.id)'));
  assert(events.some(event=>event.type==='midoo:price-tracking-saved'));
  assert.equal(row.original_price,'20.00');
  calls.length=0;
  respond=async()=>({status:409,value:{detail:'SKU 已变化，请重新打开'}});
  await run('saveManualPrice(manual)');
  assert.equal(run('activePanel.manualDrafts.get(row.id).price'),'12.5');
  assert(run('activePanel.root.innerHTML').includes('SKU 已变化'));
  run('activePanel.manualDrafts.clear();');
  run('activePanel.manualDrafts.set(row.id,{price:"12",currency:"USD",expected_context:"ctx-red"});activePanel.data.listings=[{...row,product_code:"NEW-SKU",context_token:"ctx-new"},second]');
  calls.length=0;
  await run('saveManualPrice(manual)');
  assert.equal(calls.length,0,'An old draft cannot be rebound to the new SKU context');
  assert(run('activePanel.root.innerHTML').includes('请核对后重新输入价格'));
  assert(run('activePanel.root.innerHTML').includes('value="12"'));
  run('activePanel.manualDrafts.clear();activePanel.data.listings=[row,second]');
  const pending=run('renderPriceRecord(row,true,{price:"10",currency:"USD",expected_context:"ctx-red"})');
  assert(pending.includes('待保存')&&!pending.includes('>75%<'),'Unsaved prices cannot show the saved discount');

  const refreshed=[{...row,current_price:'15',ratio_percent:'75'},{...second,error:'需要登录',current_price:'10',stale:true}];
  calls.length=0;
  respond=async call=>({value:call.url.endsWith('/browser/start')?{id:'job-B',listing_id:second.id,state:'ready'}:call.url.endsWith('/read')?{id:'job-B',listing_id:second.id,state:'preview',candidates:[{id:'quote',price:'10',currency:'USD'}]}:call.method==='POST'?{updated:call.url.includes('listing-A')}:{can_edit:true,listings:refreshed}});
  await run('collectPrices([row.id,second.id])');
  assert.deepEqual(calls.filter(call=>call.method==='POST').map(call=>call.url),['/api/price-tracking/listings/listing-A/refresh','/api/price-tracking/listings/listing-B/refresh','/api/price-tracking/listings/listing-B/browser/start','/api/price-tracking/browser/job-B/read']);
  assert(!calls.some(call=>call.url.endsWith('/confirm')),'Browser fallback previews never auto-confirm a SKU quote');
  assert(run('activePanel.message').includes('1 / 2'));
  assert(run('activePanel.root.innerHTML').includes('采集未成功，可手填价格'));
  assert(!run('activePanel.collecting'));
  assert.equal(run('activePanel.busyIds.size'),0);
  run('activePanel.data.can_edit=false');calls.length=0;
  await run('saveManualPrice(manual)');await run('collectPrices([row.id])');
  assert.equal(calls.length,0);
  run('closePanel()');
  console.log('Price/SKU UI checks passed: compact five fields, collection, safe inline manual price, context conflicts, no large manager.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
