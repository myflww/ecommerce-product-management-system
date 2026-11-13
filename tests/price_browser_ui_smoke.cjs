/* Browser-assistance UI contracts: fake DOM/API only, no browser or company data. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const listeners=new Map(), timers=new Map(), sent=[], notices=[];
let nextTimer=0;
const values={original_price:'74.62',source_url:'https://www.temu.com/us-zh-Hans/test-g-123456.html',region:'美国站',variant_hint:'套装D',product_code:'',product_number:'',currency:'CNY'};
const make=()=>({innerHTML:'',textContent:'',dataset:{},isConnected:true,disabled:false,style:{},
  setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k]??null;},remove(){this.isConnected=false;},
  focus(){},classList:{toggle(){},add(){},remove(){}},querySelector(){return null;},querySelectorAll(){return [];}});
const form={...make(),values:{...values},dataset:{listingId:'listing'},querySelector(selector){return selector==='.mpt-form-error'?this.error:null;},error:make()};
const root={...make(),querySelectorAll(selector){return selector==='.mpt-record-form'?[form]:[];}};
const document={readyState:'loading',body:{append(){}},querySelector(){return null;},querySelectorAll(){return [];},createElement:make,
  addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);},
  dispatchEvent(event){sent.push({event:event.type,detail:event.detail});}};
class TestFormData{constructor(form){this.values=form.values||{};}get(k){return this.values[k]??null;}has(k){return Boolean(this.values[k]);}}
const context=vm.createContext({console,document,URL,Date,Map,Set,JSON,Intl,Number,String,Boolean,Array,FormData:TestFormData,
  AbortController,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},
  MutationObserver:class{observe(){}},requestAnimationFrame(){},
  setTimeout(fn,delay){const id=++nextTimer;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);},
  window:{innerHeight:800,innerWidth:1200,addEventListener(name,fn){if(!listeners.has('window:'+name))listeners.set('window:'+name,[]);listeners.get('window:'+name).push(fn);}},
});
const source=fs.readFileSync(path.join(__dirname,'../static/price-tracking.js'),'utf8').replace(/export /g,'').replace(/if \(typeof document !== 'undefined'\) \{[\s\S]*$/, '');
vm.runInContext(source,context);
const run=code=>vm.runInContext(code,context);
context.sent=sent;context.notices=notices;context.testRoot=root;context.testForm=form;
run(`banner=(message,error)=>notices.push({message,error});renderBrowserSection=()=>{};reloadProduct=async()=>{};
  const row={id:'listing',original_price:'74.62',currency:'CNY',source_url:'https://www.temu.com/us-zh-Hans/test-g-123456.html',region:'美国站',variant_hint:'套装D',enabled:false};
  const panel={root:testRoot,button:{isConnected:true,setAttribute(){}},productId:'product',browserListingId:'listing',drafts:new Map(),data:{can_edit:true,listings:[row]},capabilities:{available:true,can_use:true,browser:'Edge'}};
  activePanel=panel;
  function freshEntry(){return {fingerprint:listingFingerprint(row),deadline:Date.now()+900000,sequence:0,busy:false,error:'',selection:'',confirmedVariant:false,confirmedBasis:false,job:{id:'job',listing_id:'listing',state:'preview',message:'已读取',candidates:[{id:'usd',price:'12.90',currency:'USD',evidence:'商品现价 US$12.90',title:'套装D'}],notices:[]}};}
  let entry=freshEntry();browserJobs.set('listing',entry);
  request=async(path,options={})=>{sent.push({path,options});return path.endsWith('/confirm')?{updated:true,message:'价格已记录'}:path.endsWith('/start')?{id:'new-job',listing_id:'listing',state:'ready',message:'请选好规格'}:path.endsWith('/close')?{id:'job',listing_id:'listing',state:'closed',message:'窗口已关闭'}:{id:'job',listing_id:'listing',state:'reading',message:'正在读取'};};`);

async function main(){
  const rendered=run('renderBrowserAssist(row,panel.capabilities,entry)');
  assert(rendered.includes('读取页面价格')&&rendered.includes('选择当前 SKU 的售价'));
  assert(!rendered.includes('name="enabled"'));
  const manualSettings=run('renderPriceSettings({can_edit:true,enabled:true,allow_ai:false,daily_time:"09:00"})');
  assert(manualSettings.includes('后台不运行定时采价'));
  assert(!manualSettings.includes('name="enabled"')&&!manualSettings.includes('name="daily_time"')&&!manualSettings.includes('data-price-action="refresh-all"'),'Legacy settings cannot re-expose scheduled or bulk actions');
  assert(rendered.includes('确认记录')&&!rendered.includes('<option value="usd" selected>'));
  assert.equal(run('priceRatio({original_price:74.62,current_price:12.9,currency:"CNY",current_currency:"USD"})'),null);
  assert(run('renderPriceSummary(panel.data)').includes('平台与各 SKU 价格'));
  assert(!run('renderPriceSummary(panel.data)').includes('data-price-action="browser-start"'),'Summary is a read-only price overview');
  const escaped=run(`renderBrowserAssist(row,panel.capabilities,{...entry,job:{...entry.job,title:'<img src=x onerror=1>',message:'<script>bad</script>',notices:['<svg/onload=1>'],candidates:[{id:'a" onclick="bad',price:'12.9',currency:'USD',evidence:'<script>price</script>'}]}})`);
  assert(!escaped.includes('<script>')&&!escaped.includes('<img src=x'));
  assert(run('renderBrowserAssist(row,{available:true,can_use:false,note:"remote"},null)').includes('remote'));
  assert(run('renderBrowserAssist(row,{available:false,can_use:true,note:"未找到Edge"},null)').includes('未找到Edge'));
  assert(!run('renderPriceRecord(row,false)').includes('data-price-action="browser-start"'));
  const otherAssist=run("renderBrowserAssist({...row,id:'other-listing'},panel.capabilities,null)");
  assert(otherAssist.includes('关闭其他采价窗口')&&otherAssist.includes('data-listing-id="listing"'));

  // A legacy disabled flag is irrelevant to an explicit manual browser action.
  run('browserJobs.clear();');
  await run("startBrowser({dataset:{listingId:'listing'}})");
  assert(sent.some(item=>item.path==='/listings/listing/browser/start'),'A saved link can open assistance while the legacy enabled flag is false');
  assert.equal(run("browserJobs.get('listing').job.state"),'ready');
  assert.equal(run('row.enabled'),false,'Starting a browser must not change legacy settings');
  run("entry=freshEntry();browserJobs.set('listing',entry);");

  // Confirm is a distinct operation and requires an explicit selection and both checks.
  const confirmForm={...make(),dataset:{listingId:'listing'},values:{candidate_id:'usd'}};context.confirmForm=confirmForm;
  await run('confirmBrowser(confirmForm)');
  assert(!sent.some(item=>item.path?.endsWith('/confirm')),'Unchecked preview must never write a price');
  assert(run('entry.error').includes('核对规格'));
  confirmForm.values.confirmed_variant=true;confirmForm.values.confirmed_price_basis=true;
  await run('confirmBrowser(confirmForm)');
  const confirmation=sent.find(item=>item.path?.endsWith('/confirm'));
  assert.deepEqual(JSON.parse(JSON.stringify(confirmation.options.body)),{candidate_id:'usd',confirmed_variant:true,confirmed_price_basis:true});
  assert(!('price' in confirmation.options.body)&&!('currency' in confirmation.options.body),'Server must resolve its own candidate');
  assert.equal(run('entry.job.state'),'closed');
  assert(sent.some(item=>item.event==='midoo:price-tracking-saved'));

  // Unsaved settings and saved config changes invalidate all old previews.
  run("entry=freshEntry();browserJobs.set('listing',entry);panel.drafts.set('listing',{...row,original_price:'74.62',source_url:'https://example.test/new'});");
  assert(run('renderBrowserAssist(row,panel.capabilities,entry)').includes('商品规格或链接已变化'));
  form.values.source_url='https://example.test/new';
  const count=sent.length;
  await run('confirmBrowser(confirmForm)');
  assert.equal(sent.length,count,'Unsaved form must stop confirmation before HTTP');
  assert(run('entry.error').includes('变化'));
  form.values.source_url=values.source_url;
  run("panel.drafts.clear();row.variant_hint='套装A';");
  await run('confirmBrowser(confirmForm)');
  assert.equal(sent.length,count,'A saved configuration change must also invalidate old previews');
  run("row.variant_hint='套装D';entry=freshEntry();browserJobs.set('listing',entry);");

  // Reading only creates a preview; it never auto-confirms a quote.
  context.readButton={dataset:{listingId:'listing'}};
  await run("browserAction(readButton,'read')");
  assert.equal(sent.at(-1).path,'/browser/job/read');
  assert.equal(run('entry.job.state'),'reading');
  assert.equal(run('entry.selection'),'');
  assert.equal(run('entry.confirmedVariant'),false);
  const selected=run("entry=freshEntry();entry.selection='usd';entry.confirmedVariant=true;entry.confirmedBasis=true;browserJobs.set('listing',entry);renderBrowserAssist(row,panel.capabilities,entry)");
  assert(selected.includes('<option value="usd" selected>'));
  assert(selected.includes('class="button primary sm" type="submit" >'));

  // Polling reads status only, stops on panel close and suppresses late old replies.
  run('scheduleBrowserPoll(panel)');
  let poll=[...timers.values()].find(item=>item.delay===4000);assert(poll);
  let resolvePoll;context.slow=()=>new Promise(resolve=>{resolvePoll=resolve;});
  run('request=slow;');
  const pending=poll.fn();
  run('stopBrowserPoll();activePanel=null;');
  resolvePoll({id:'job',listing_id:'listing',state:'preview',message:'旧请求',candidates:[{id:'bad'}]});
  await pending;
  assert.notEqual(run('entry.job.message'),'旧请求');
  run('activePanel=panel;entry=freshEntry();browserJobs.set("listing",entry);');

  // A missing/expired job can be restarted after a service restart.
  run("request=async()=>{const error=new Error('已过期');error.status=404;throw error;};scheduleBrowserPoll(panel);");
  poll=[...timers.values()].find(item=>item.delay===4000);await poll.fn();
  assert.equal(run('entry.job.state'),'closed');
  assert(run('renderBrowserAssist(row,panel.capabilities,entry)').includes('打开采价窗口'));

  // Pending actions after account reset cannot install a job into a new session.
  let resolveStart;
  context.slowStart=()=>new Promise(resolve=>{resolveStart=resolve;});
  run("browserJobs.clear();request=slowStart;");
  const opening=run("startBrowser({dataset:{listingId:'listing'}})");
  run('browserJobs.clear();activePanel=null;stopBrowserPoll();');
  resolveStart({id:'new-job',listing_id:'listing',state:'ready'});await opening;
  assert.equal(run('browserJobs.size'),0);

  // Install event listeners with a stub mount, then verify explicit checked inputs.
  run('const savedMount=mount;mount=()=>{};initialize();activePanel=panel;entry=freshEntry();browserJobs.set("listing",entry);');
  confirmForm.values={candidate_id:'usd',confirmed_variant:true,confirmed_price_basis:true};
  const confirmButton=make();
  confirmForm.querySelector=selector=>selector==='[type=submit]'?confirmButton:null;
  const target={closest:selector=>selector.includes('browser-confirm')?confirmForm:null};
  for(const fn of listeners.get('change')||[])fn({target});
  assert.equal(confirmButton.disabled,false);
  assert.equal(run('entry.selection'),'usd');
  for(const fn of listeners.get('window:hashchange')||[])fn();
  assert.equal(run('activePanel'),null);
  assert.equal(run('browserPoll'),null);

  // Price baseline and form drafts are not changed by the browser workflow.
  assert.equal(run('row.original_price'),'74.62');
  assert.equal(run('row.currency'),'CNY');
  run('savedMount();');
  assert.equal(run('browserJobs.size'),0,'Logging out removes client references');

  // The large management entry has been removed from product cards.
  const card=make();card.dataset.productCard='product';
  card.querySelector=selector=>selector==='.mpt-toggle'?{}:null;
  document.querySelector=selector=>selector==='#content'?root:null;
  document.querySelectorAll=selector=>selector==='.product-card[data-product-card]'?[card]:[];
  run('activePanel=null;savedMount();');
  console.log('PRICE_BROWSER_UI_SMOKE_OK: compact controls, explicit confirmation, escaping, host-only access, currency guards, stale preview isolation, polling cleanup and no large manager');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
