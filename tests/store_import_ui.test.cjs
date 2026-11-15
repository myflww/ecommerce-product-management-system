/* Store import interaction regressions: local VM/DOM/API fixtures only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/store-import.js'), 'utf8').replace(/export /g, '');
const plain = value => JSON.parse(JSON.stringify(value));
const tick = async () => { for(let i=0;i<8;i++)await Promise.resolve(); };
const decode = value => String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const attr = (html, key) => decode(html.match(new RegExp('(?:^|\\s)'+key+'="([^"]*)"'))?.[1]||'');
const permissionUser = {id:'operator',permissions:['products.import','products.create']};
const product = {id:'target',code:'MD-000001',name:'人工公司同款',updated_at:'version-1'};
const item = (id='stable-product-key', fields={}) => ({id,platform:'Temu',store_id:'700000000000003',store_name:'Fixture shop',source_id:'700000000000005',
  title:'Fixture candle',url:'https://www.temu.com/p-g-700000000000005.html',has_image:true,can_import:true,warnings:[],existing_product:null,
  variants:[{id:'public-sku-1',spec:'红色 / 2件',price:'10.54',currency:'USD'}],...fields});
const preview = (items=[item()]) => ({id:'server-draft-id',committed:false,items});
const job = fields => ({id:'job',state:'preview',message:'可核对',progress:1,candidates:[],items:[],draft_id:'server-draft-id',...fields});

function element(dataset={}) {
  return {dataset,innerHTML:'',textContent:'',value:'',checked:false,disabled:false,isConnected:true,classList:{toggle(){}},
    remove(){this.isConnected=false;},querySelector(){return null;},querySelectorAll(){return [];},
    matches(selector){const name=selector.match(/^\[data-([\w-]+)\]$/)?.[1];return Boolean(name)&&Object.hasOwn(this.dataset,name.replace(/-([a-z])/g,(_,c)=>c.toUpperCase()));}};
}
function makeDialog() {
  const root=element(), notice=element(), work=element(), buttons=new Map();
  root.fields=[];root.url=element({siUrl:''});root.url.value='https://www.temu.com/mall.html?mall_id=700000000000003';root.url.root=root;
  root.file=element({siFile:''});root.file.root=root;root.fileHelp=element({siFileHelp:''});
  let markup='';
  Object.defineProperty(work,'innerHTML',{get:()=>markup,set:html=>{
    markup=html;root.fields=[];
    for(const match of html.matchAll(/<input\b([^>]*)>|<select\b([^>]*)>([\s\S]*?)<\/select>/g)){
      const attrs=match[1]||match[2],dataset={};
      for(const data of attrs.matchAll(/\bdata-([\w-]+)(?:="([^"]*)")?/g))dataset[data[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=decode(data[2]||'');
      const field=element(dataset);field.root=root;field.value=attr(attrs,'value');field.checked=/\bchecked\b/.test(attrs);
      if(match[2]){const options=[...match[3].matchAll(/<option\b([^>]*)>/g)];const selected=options.find(o=>/\bselected\b/.test(o[1]))||options[0];field.value=selected?attr(selected[1],'value'):'';}
      root.fields.push(field);
    }
  }});
  root.querySelector=selector=>{
    if(selector==='[data-si-work]')return work;
    if(selector==='[data-si-notice]')return notice;
    if(selector==='[data-si-url]')return root.url;
    if(selector==='[data-si-file]')return root.file;
    if(selector==='[data-si-file-help]')return root.fileHelp;
    const action=selector.match(/^\[data-si="(.*?)"\]$/)?.[1];
    if(action){if(!buttons.has(action)){const b=element({si:action});b.root=root;buttons.set(action,b);}return buttons.get(action);}
    return root.fields.find(f=>f.matches(selector))||null;
  };
  root.querySelectorAll=selector=>root.fields.filter(f=>f.matches(selector));
  root.contains=target=>target?.root===root;
  root.showModal=()=>{};root.work=work;root.notice=notice;
  root.addEventListener=()=>{};
  return root;
}
function harness() {
  const listeners=new Map(),calls=[],events=[],timers=new Map(),dialogs=[];let timerId=0;
  const document={body:{append(){}},querySelector(){return null;},createElement(tag){const node=tag==='dialog'?makeDialog():element();if(tag==='dialog')dialogs.push(node);return node;},
    addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);}};
  class FakeFormData {constructor(){this.fields=new Map();}append(key,value){this.fields.set(key,value);}get(key){return this.fields.get(key);}}
  const context=vm.createContext({console,URL,document,Map,Set,JSON,Number,String,Boolean,Array,Error,FormData:FakeFormData,
    CustomEvent:class{constructor(type,options={}){this.type=type;this.detail=options.detail;}},
    MutationObserver:class{observe(){}},queueMicrotask:fn=>fn(),
    setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
    window:{alert:message=>events.push({type:'alert',message}),dispatchEvent:event=>events.push(event)},
    fetch(){throw Error('Live fetch is forbidden in UI test');},calls});
  vm.runInContext(source,context);
  const run=code=>vm.runInContext(code,context);
  run('const transportApi=api;');
  const dialog=makeDialog();context.testDialog=dialog;context.user=plain(permissionUser);context.draftSeed=preview();context.jobSeed=job();
  run('dialog=testDialog;currentUser=user;draft=draftSeed;job=jobSeed;choices=new Map(draft.items.map(i=>[i.id,{action:"new"}]));');
  const reply=async(url,options={})=>{calls.push({url,options:plain(options)});if(url==='/api/me')return {user:plain(permissionUser)};
    if(url.includes('/browser/'))return plain(job());return {};};
  context.reply=reply;run('api=reply;render();');
  return {context,run,dialog,calls,events,timers,listeners,dialogs,
    setApi(fn){context.reply=fn;run('api=reply;');},
    async act(action,extra={}){const button=element({si:action,...extra});button.root=run('dialog');context.button=button;await run('act(button.dataset.si,button)');await tick();},
    async importFile(file={name:'midoo-page-capture.json',size:1024}){context.testFile=file;await run('importCaptureFile(testFile,testDialog.file)');await tick();},
    change(field){for(const fn of listeners.get('change')||[])fn({target:field});},
    input(field){for(const fn of listeners.get('input')||[])fn({target:field});},
    dispatch(type,detail){for(const fn of listeners.get(type)||[])fn({detail});},
    setDraft(value){context.draftSeed=value;run('draft=draftSeed;choices=new Map(draft.items.map(i=>[i.id,i.existing_product?{action:"merge",product_id:i.existing_product.id,expected_updated_at:i.existing_product.updated_at}:{action:"new"}]));render();');},
  };
}

const cases=[];
function test(name,fn){cases.push({name,fn});}

test('permission arrays gate import and creation independently',async()=>{
  const h=harness();h.run('currentUser={id:"operator",role:"owner",permissions:[]};');
  assert.equal(h.run('allowed("products.import")'),false,'A role label alone cannot grant import');
  h.run('currentUser={id:"operator",permissions:["products.import"]};render();');
  assert.match(h.dialog.work.innerHTML,/<option value="new" disabled/);
  h.setApi(async url=>url==='/api/me'?{user:{id:'operator',permissions:[]}}:{});
  h.run('dismiss();');
  await assert.rejects(h.run('open()'),/没有批量导入权限/);
});

test('preview preserves product/source/draft identities and escapes external labels',async()=>{
  const h=harness();h.setDraft(preview([item('stable-key',{platform:'Amazon',title:'<script>bad</script>',store_name:'" onerror="bad',source_id:'000001'})]));
  const html=h.dialog.work.innerHTML;
  assert.match(html,/images\/stable-key/);assert.match(html,/公开商品编号 000001/);assert.match(html,/Amazon ·/);
  assert(!html.includes('<script>bad</script>'));assert(html.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.equal(h.run('safeProductLink("javascript:alert(1)")'),'');assert.equal(h.run('safeProductLink("https://user:secret@example.com/p")'),'');
  assert(h.run('safeProductLink("https://www.temu.com/p?sku_id=1")').includes('sku_id=1'));
});

test('zero and missing quotes are never shown as verified storefront prices',async()=>{
  const h=harness();h.setDraft(preview([item('zero',{variants:[{id:'v',spec:'zero',price:'0',currency:'USD'},{id:'n',spec:'missing',price:null,currency:'USD'}]})]));
  assert.equal((h.dialog.work.innerHTML.match(/未采到独立报价/g)||[]).length,2);
  assert(!h.dialog.work.innerHTML.includes('USD 0'));
});

test('query text and listed confirmation survive rerender and unrelated choice changes',async()=>{
  const h=harness();h.setDraft(preview([item('one'),item('two')]));
  let field=h.dialog.querySelectorAll('[data-si-choice]')[0];field.value='merge';h.change(field);
  field=h.dialog.querySelectorAll('[data-si-query]')[0];field.value='圣诞蜡烛 & 2件';h.input(field);
  const listed=h.dialog.querySelector('[data-si-listed]');listed.checked=true;h.change(listed);
  field=h.dialog.querySelectorAll('[data-si-choice]')[1];field.value='skip';h.change(field);
  assert.equal(h.dialog.querySelectorAll('[data-si-query]')[0].value,'圣诞蜡烛 & 2件');
  assert.equal(h.dialog.querySelector('[data-si-listed]').checked,true);
  h.run('render();');assert.equal(h.dialog.querySelectorAll('[data-si-query]')[0].value,'圣诞蜡烛 & 2件');
});

test('search keeps selected target and its original optimistic version while adding results',async()=>{
  const h=harness();h.run('choices.set("stable-product-key",{action:"merge",product_id:"target",expected_updated_at:"version-1"});');
  h.context.target=product;h.run('productOptions.set("stable-product-key",[target]);render();');
  const field=h.dialog.querySelectorAll('[data-si-query]')[0];field.value='different query';h.input(field);
  h.setApi(async(url,options={})=>{h.calls.push({url,options});return url.startsWith('/api/products?')?{items:[{id:'other',code:'MD-2',name:'其他',updated_at:'version-2'}]}:plain(job());});
  await h.act('search',{item:'stable-product-key'});
  assert.match(h.calls[0].url,/q=different%20query/);
  assert.equal(h.run('choices.get("stable-product-key").product_id'),'target');
  assert.equal(h.run('choices.get("stable-product-key").expected_updated_at'),'version-1');
  assert(h.run('productOptions.get("stable-product-key").some(p=>p.id==="target")'));
  assert.equal(h.dialog.querySelectorAll('[data-si-query]')[0].value,'different query');
});

test('known source always keeps its mapped target/version when toggled away and back',async()=>{
  const h=harness();h.setDraft(preview([item('existing',{existing_product:product})]));
  assert.match(h.dialog.work.innerHTML,/<option value="new" disabled/);
  let field=h.dialog.querySelectorAll('[data-si-choice]')[0];field.value='skip';h.change(field);
  field=h.dialog.querySelectorAll('[data-si-choice]')[0];field.value='merge';h.change(field);
  assert.deepEqual(plain(h.run('choices.get("existing")')),{action:'merge',product_id:'target',expected_updated_at:'version-1'});
  assert.equal(h.dialog.querySelectorAll('[data-si-query]').length,0);
});

test('multi-product merge transport contains server draft/item keys and no client prices',async()=>{
  const h=harness();h.setDraft(preview([item('one'),item('two',{store_id:'another-store'})]));
  h.context.target=product;h.run('for(const i of draft.items){productOptions.set(i.id,[target]);choices.set(i.id,{action:"merge",product_id:target.id,expected_updated_at:target.updated_at});}render();');
  const listed=h.dialog.querySelector('[data-si-listed]');listed.checked=true;h.change(listed);
  h.setApi(async(url,options={})=>{h.calls.push({url,options:plain(options)});if(url.endsWith('/commit'))return {created:0,merged:2,new_listings:2,items:[]};return url.includes('/browser/')?plain(job({draft_id:''})):{};});
  await h.act('commit');
  const saved=h.calls.find(c=>c.url.endsWith('/commit'));
  assert.equal(saved.url,'/api/store-import/previews/server-draft-id/commit');
  assert.deepEqual(saved.options.body,{selections:[{item_id:'one',action:'merge',product_id:'target',expected_updated_at:'version-1'},
    {item_id:'two',action:'merge',product_id:'target',expected_updated_at:'version-1'}],mark_listed:true});
  assert(!JSON.stringify(saved.options.body).includes('10.54'));
  assert(h.calls.some(c=>c.options.method==='DELETE'&&c.url.endsWith('/server-draft-id')),'Successful commit releases server images');
  assert.equal(h.run('draft.committed'),true);assert.equal(h.run('job.draft_id'),'');
  assert(h.events.some(e=>e.type==='midoo:products-changed'));
  const count=h.calls.filter(c=>c.url.endsWith('/commit')).length;
  await h.act('commit');assert.equal(h.calls.filter(c=>c.url.endsWith('/commit')).length,count,'Committed result cannot be resubmitted');
});

test('lost commit response can retry same server draft without advancing local success',async()=>{
  const h=harness();let commits=0;
  h.setApi(async(url,options={})=>{h.calls.push({url,options:plain(options)});if(url.endsWith('/commit')){if(++commits===1)throw Error('fixture lost response');return {created:1,merged:0,new_listings:1,items:[]};}return url.includes('/browser/')?plain(job({draft_id:commits>1?'':'server-draft-id'})):{};});
  await h.act('commit');
  assert.equal(h.run('draft.committed'),false);
  assert(!h.calls.some(c=>c.options.method==='DELETE'));assert(!h.calls.some(c=>c.url.endsWith('/discover')));
  await h.act('commit');assert.equal(commits,2);assert.equal(h.run('draft.committed'),true);
  assert.deepEqual(h.calls.filter(c=>c.url.endsWith('/commit')).map(c=>c.url),Array(2).fill('/api/store-import/previews/server-draft-id/commit'));
});

test('uncommitted previews never discover next batch and explicit discard only releases draft',async()=>{
  const h=harness();await h.act('next');
  assert(!h.calls.some(c=>c.url.endsWith('/discover')));
  h.setApi(async(url,options={})=>{h.calls.push({url,options:plain(options)});return url.includes('/browser/')?plain(job({draft_id:''})):{};});
  await h.act('discard');
  assert(h.calls.some(c=>c.options.method==='DELETE'));
  assert(!h.calls.some(c=>c.url.endsWith('/commit')));assert(!h.calls.some(c=>c.url.endsWith('/discover')));
  assert.equal(h.run('draft'),null);
});

test('discard reconciles already-committed server state instead of claiming nothing was saved',async()=>{
  const h=harness();
  h.setApi(async(url,options={})=>{h.calls.push({url,options:plain(options)});return options.method==='DELETE'?{ok:true,committed:true,result:{created:1,new_listings:1}}:plain(job({draft_id:''}));});
  await h.act('discard');
  assert.equal(h.run('draft.committed'),true);assert.equal(h.run('draft.result.created'),1);
  assert(!h.dialog.notice.textContent.includes('未发生变化'));
  assert(h.events.some(e=>e.type==='midoo:products-changed'));
  assert(!h.calls.some(c=>c.url.endsWith('/commit')));
});

test('dismissing retains uncommitted local preview for resuming',async()=>{
  const h=harness();h.run('dismiss();');
  assert.equal(h.run('dialog'),null);assert.equal(h.run('draft.id'),'server-draft-id');
  assert(!h.calls.some(c=>c.options.method==='DELETE'));
});

test('starting a new window cannot silently discard an uncommitted preview',async()=>{
  const h=harness();h.run('job={...job,state:"closed"};render();');
  assert.equal(h.dialog.querySelector('[data-si="start"]').disabled,true);
  await h.act('start');
  assert.equal(h.run('draft.id'),'server-draft-id');
  assert(!h.calls.some(c=>c.options.method==='DELETE'||c.url.endsWith('/start')));
});

test('late preview response cannot replace a newly opened dialog draft',async()=>{
  const h=harness();let resolve;
  h.run('draft=null;job={...job,draft_id:"old-draft"};');
  h.setApi(async url=>url.includes('/browser/')?plain(job({draft_id:'old-draft'})):new Promise(r=>{resolve=r;}));
  const pending=h.run('poll()');await tick();assert(resolve);
  h.run('dismiss();dialog=testDialog;draft={id:"new-draft",items:[],committed:false};job={id:"new-job",state:"closed",draft_id:"new-draft"};');
  resolve({id:'old-draft',items:[item()],committed:false});await pending;
  assert.equal(h.run('draft.id'),'new-draft');assert.equal(h.run('job.id'),'new-job');
});

test('late search response cannot install options in a new dialog/session',async()=>{
  const h=harness();let resolve;h.run('choices.set("stable-product-key",{action:"merge"});render();');
  h.setApi(async url=>url.startsWith('/api/products?')?new Promise(r=>{resolve=r;}):plain(job()));
  const pending=h.act('search',{item:'stable-product-key'});await tick();assert(resolve);
  h.run('dismiss();dialog=testDialog;currentUser={id:"other",permissions:["products.import"]};productOptions.clear();');
  resolve({items:[product]});await pending;assert.equal(h.run('productOptions.size'),0);
});

test('late start response is closed instead of replacing a new job',async()=>{
  const h=harness();let resolve;h.run('draft=null;job=null;render();');
  h.setApi(async(url,options={})=>{h.calls.push({url,options:plain(options)});if(url.endsWith('/start'))return new Promise(r=>{resolve=r;});return {id:'orphan',state:'closed'};});
  const pending=h.act('start');await tick();assert(resolve);
  h.run('dismiss();dialog=testDialog;job={id:"new-job",state:"closed",draft_id:""};');
  resolve({id:'orphan',state:'ready',draft_id:''});await pending;
  assert.equal(h.run('job.id'),'new-job');assert(h.calls.some(c=>c.url.endsWith('/orphan/close')));
});

test('late commit cannot replace another draft or send success into another session',async()=>{
  const h=harness();let resolve;
  h.setApi(async(url,options={})=>{h.calls.push({url,options:plain(options)});if(url.endsWith('/commit'))return new Promise(r=>{resolve=r;});return {};});
  const pending=h.act('commit');await tick();assert(resolve);
  h.run('dismiss();dialog=testDialog;draft={id:"new-draft",committed:false,items:[]};job={id:"new-job",state:"closed",draft_id:""};');
  resolve({created:1,new_listings:1});await pending;
  assert.equal(h.run('draft.id'),'new-draft');assert.equal(h.run('draft.committed'),false);
  assert(!h.events.some(e=>e.type==='midoo:products-changed'));
});

test('same-account reopen reconciles a late commit using a fresh scoped server read',async()=>{
  const h=harness();let resolve;let serverCommitted=false;
  h.setApi(async(url,options={})=>{
    h.calls.push({url,options:plain(options)});
    if(url.endsWith('/commit'))return new Promise(r=>{resolve=value=>{serverCommitted=true;r(value);};});
    if(url.endsWith('/previews/server-draft-id')&&options.method!=='DELETE')return {id:'server-draft-id',committed:serverCommitted,result:{created:1,new_listings:1}};
    return {};
  });
  const pending=h.act('commit');await tick();assert(resolve);
  h.run('dismiss();dialog=testDialog;');
  resolve({created:999,new_listings:999});await pending;await tick();
  assert.equal(h.run('draft.committed'),true);
  assert.equal(h.run('draft.result.created'),1,'Use the freshly authenticated server result, not the late response');
  assert(h.calls.some(c=>c.url.endsWith('/previews/server-draft-id')&&c.options.method!=='DELETE'));
  assert(h.calls.some(c=>c.options.method==='DELETE'));
});

test('in-flight status from before an action cannot overwrite the action result',async()=>{
  const h=harness();let resolve;
  h.setApi(async(url,options={})=>options.method==='POST'?plain(job({state:'closed',draft_id:'server-draft-id'})):new Promise(r=>{resolve=r;}));
  const pending=h.run('poll()');await tick();const oldResolve=resolve;assert(oldResolve);
  await h.act('stop');
  oldResolve(plain(job({state:'collecting',message:'stale status'})));await pending;
  assert.equal(h.run('job.state'),'closed');
});

test('permission revocation closes dialog and drops locally cached source data',async()=>{
  const h=harness();h.dispatch('midoo:permissions-updated',{user:{id:'operator',permissions:[]}});
  assert.equal(h.run('dialog'),null);assert.equal(h.run('draft'),null);assert.equal(h.run('job'),null);
});

test('preferred file workflow explains installation and keeps dedicated browser secondary',async()=>{
  const h=harness();h.run('dismiss();draft=null;job=null;');
  await h.run('open()');const html=h.dialogs.at(-1).innerHTML;
  assert.match(html,/浏览器插件采集（推荐）/);assert.match(html,/page-extension\.zip/);
  assert.match(html,/开发者模式/);assert.match(html,/加载解压缩的扩展/);
  assert.match(html,/页面已提供的信息/);assert.match(html,/SKU 和独立价格会保留为待补全/);
  assert.match(html,/<details class="si-legacy-browser"[^>]*><summary>专用浏览器采集 · 试用/);
  assert(html.indexOf('data-si-file')<html.indexOf('si-legacy-browser'));
});

test('multipart transport leaves FormData and browser content boundary intact',async()=>{
  const h=harness();let seen;
  h.context.fetch=async(url,options)=>{seen={url,options};return {ok:true,json:async()=>preview()};};
  const result=await h.run('const uploadBody=new FormData();uploadBody.append("file",{name:"capture.json",size:30});transportApi(BASE+"/page-file",{method:"POST",body:uploadBody})');
  assert.equal(result.id,'server-draft-id');assert.equal(seen.options.credentials,'same-origin');
  assert.equal(seen.options.headers['Content-Type'],undefined,'Browser creates multipart boundary');
  assert(seen.options.body instanceof h.context.FormData);
  assert.equal(seen.options.body.get('file').name,'capture.json');
});

test('file import creates a preview only and preserves mapped merge choices',async()=>{
  const h=harness();h.run('clearDraft();job=null;render();');
  let uploaded;
  h.setApi(async(url,options={})=>{h.calls.push({url,options});if(url.endsWith('/page-file')){uploaded=options.body;return preview([item('existing',{existing_product:product}),item('fresh')]);}return {};});
  await h.importFile();
  assert(uploaded instanceof h.context.FormData);assert.equal(uploaded.get('file').name,'midoo-page-capture.json');
  assert.equal(h.run('draft.id'),'server-draft-id');assert.equal(h.run('job'),null);
  assert.deepEqual(plain(h.run('choices.get("existing")')),{action:'merge',product_id:'target',expected_updated_at:'version-1'});
  assert.equal(h.run('choices.get("fresh").action'),'new');
  assert(!h.calls.some(c=>c.url.includes('/browser/')||c.url.endsWith('/commit')));
  assert(!h.events.some(e=>e.type==='midoo:products-changed'));
  assert.match(h.dialog.work.innerHTML,/midoo-page-capture\.json/);
  assert.equal(h.dialog.file.disabled,true,'Review cannot be silently replaced');
  assert.equal(h.run('working'),false);assert.equal(h.timers.size,0);
});

test('file import retains incomplete SKU warnings and never supplies manual base prices',async()=>{
  const h=harness();h.run('clearDraft();job=null;');
  h.setApi(async()=>preview([item('partial',{warnings:['店铺归属由你手动确认。','尚未取得可绑定具体子产品的完整规格，保留商品档案供补全。'],variants:[{id:'',spec:'规格尚未完整采集',price:'',currency:''}]})]));
  await h.importFile();
  assert.match(h.dialog.work.innerHTML,/规格尚未完整采集/);assert.match(h.dialog.work.innerHTML,/未采到独立报价/);
  assert.match(h.dialog.work.innerHTML,/店铺归属由你手动确认/);
  assert.equal(h.run('draft.items[0].variants[0].price'),'');
});

test('file summary makes partial captures and failed images visible before import',async()=>{
  const h=harness();h.setDraft({...preview(),capture_summary:{pages:3,products:1,partial:1,image_failures:1,source:'browser_file'}});
  assert.match(h.dialog.work.innerHTML,/从 3 个页面读取 1 款商品/);
  assert.match(h.dialog.work.innerHTML,/1 款规格信息待补全/);
  assert.match(h.dialog.work.innerHTML,/1 款主图下载失败，暂不能入库/);
});

test('unfinished preview and live dedicated job both block file replacement',async()=>{
  const h=harness();await h.importFile();
  assert(!h.calls.some(c=>c.url.endsWith('/page-file')||c.options.method==='DELETE'));
  assert.equal(h.run('draft.id'),'server-draft-id');assert.match(h.dialog.notice.textContent,/先确认导入或放弃/);
  h.run('clearDraft();job={id:"live-job",state:"ready",draft_id:""};render();');
  assert.equal(h.dialog.file.disabled,true);await h.importFile();
  assert(!h.calls.some(c=>c.url.endsWith('/page-file')));assert.match(h.dialog.notice.textContent,/先关闭专用采集窗口/);
});

test('empty oversized and non-JSON uploads fail before network or preview cleanup',async()=>{
  for(const file of [{name:'capture.json',size:0},{name:'capture.json',size:8*1024*1024+1},{name:'capture.html',size:50}]){
    const h=harness();h.run('draft.committed=true;job=null;render();');await h.importFile(file);
    assert.equal(h.calls.length,0);assert.equal(h.run('draft.id'),'server-draft-id');
    assert.equal(h.run('working'),false);assert.equal(h.dialog.file.value,'');assert(h.dialog.notice.textContent);
  }
});

test('committed file batch can release and import another file without browser commands',async()=>{
  const h=harness();h.run('draft.committed=true;captureFileName="first.json";job=null;render();');
  assert.equal(h.dialog.file.disabled,false);assert.match(h.dialog.work.innerHTML,/继续选择下一份采集文件/);
  h.setApi(async(url,options={})=>{h.calls.push({url,options});return url.endsWith('/page-file')?{...preview([item('next')]),id:'next-draft'}:{};});
  await h.importFile({name:'second.json',size:100});
  assert(h.calls[0].url.endsWith('/server-draft-id')&&h.calls[0].options.method==='DELETE');
  assert(h.calls[1].url.endsWith('/page-file'));assert.equal(h.run('draft.id'),'next-draft');
  assert.equal(h.run('captureFileName'),'second.json');assert(!h.calls.some(c=>c.url.includes('/browser/')));
});

test('late upload after dismissal releases its draft without altering a reopened preview',async()=>{
  const h=harness();let resolve;h.run('clearDraft();job=null;');
  h.setApi(async(url,options={})=>{h.calls.push({url,options});return url.endsWith('/page-file')?new Promise(r=>{resolve=r;}):{};});
  const pending=h.importFile();await tick();assert(resolve);
  h.run('dismiss();dialog=testDialog;draft={id:"new-draft",committed:false,items:[]};');
  resolve({...preview(),id:'late-file-draft'});await pending;
  assert.equal(h.run('draft.id'),'new-draft');assert.equal(h.run('captureFileName'),'');
  assert(h.calls.some(c=>c.url.endsWith('/late-file-draft')&&c.options.method==='DELETE'));
  assert(!h.events.some(e=>e.type==='midoo:products-changed'));
});

test('permission withdrawal during upload drops response and cleans its temporary draft',async()=>{
  const h=harness();let resolve;h.run('clearDraft();job=null;');
  h.setApi(async(url,options={})=>{h.calls.push({url,options});return url.endsWith('/page-file')?new Promise(r=>{resolve=r;}):{};});
  const pending=h.importFile();await tick();assert(resolve);
  h.dispatch('midoo:permissions-updated',{user:{id:'operator',permissions:[]}});
  resolve({...preview(),id:'revoked-draft'});await pending;
  assert.equal(h.run('dialog'),null);assert.equal(h.run('draft'),null);assert.equal(h.run('captureFileName'),'');
  assert(h.calls.some(c=>c.url.endsWith('/revoked-draft')&&c.options.method==='DELETE'));
});

test('file permission denial does not leave the dialog busy or send a request',async()=>{
  const h=harness();h.run('clearDraft();job=null;currentUser={id:"operator",permissions:[]};');
  await h.importFile();assert.equal(h.calls.length,0);assert.equal(h.run('working'),false);
  assert.match(h.dialog.notice.textContent,/没有批量导入权限/);
});

test('opening as a different member never retains the prior capture filename',async()=>{
  const h=harness();h.run('captureFileName="private-member-file.json";dismiss();');
  h.setApi(async()=>({user:{id:'other',permissions:['products.import']}}));
  await h.run('open()');assert.equal(h.run('captureFileName'),'');assert.equal(h.run('draft'),null);
  assert(!h.dialogs.at(-1).work.innerHTML.includes('private-member-file.json'));
});

test('old browser status cannot overwrite a file preview',async()=>{
  const h=harness();let resolve;h.run('clearDraft();job={id:"closed-job",state:"closed",draft_id:""};');
  h.setApi(async(url,options={})=>{h.calls.push({url,options});if(url.includes('/browser/'))return new Promise(r=>{resolve=r;});if(url.endsWith('/page-file'))return {...preview(),id:'file-draft'};return {};});
  const pending=h.run('poll()');await tick();assert(resolve);
  await h.importFile();resolve(job({state:'preview',draft_id:'stale-draft'}));await pending;
  assert.equal(h.run('draft.id'),'file-draft');assert.equal(h.run('job'),null);
  assert(!h.calls.some(c=>c.url.endsWith('/previews/stale-draft')));
});

(async()=>{
  let failures=0;
  for(const {name,fn} of cases){try{await fn();console.log('PASS '+name);}catch(error){failures++;console.error('FAIL '+name);console.error(error.stack);}}
  if(failures){process.exitCode=1;return;}
  console.log(`STORE_IMPORT_UI_OK: ${cases.length} interaction checks, no browser/network/company data`);
})().catch(error=>{console.error(error);process.exitCode=1;});
