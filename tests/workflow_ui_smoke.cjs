/* Workflow UI contract checks. No browser, network, credentials or company data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
const listeners = new Map();
const makeElement = () => ({
  innerHTML: '', textContent: '', style: {}, dataset: {}, disabled: false,
  isConnected: true, classList: {add(){},remove(){},toggle(){}},
  append(){},remove(){},focus(){},click(){},
  querySelector(){return null;},querySelectorAll(){return [];},
});
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, makeElement());
  return elements.get(selector);
};
class TestForm {
  constructor(id, values={}, dataset={}) {
    Object.assign(this, makeElement(), {id, values, dataset});
  }
}
class TestFormData {
  constructor(form) {this.values=Object.entries(form?.values||{});}
  append(...entry) {this.values.push(entry);}
  get(key) {return this.values.find(entry=>entry[0]===key)?.[1]??null;}
  has(key) {return this.values.some(entry=>entry[0]===key);}
  entries() {return this.values.map(entry=>entry.slice(0,2))[Symbol.iterator]();}
  [Symbol.iterator]() {return this.entries();}
}
class TestURL extends URL {
  static createObjectURL() {return 'blob:workflow-qa';}
  static revokeObjectURL() {}
}
const context = vm.createContext({
  console, URL:TestURL, URLSearchParams, FormData:TestFormData,
  HTMLFormElement:TestForm, structuredClone, Blob,
  document: {
    querySelector:element,querySelectorAll(){return [];},
    addEventListener(name,fn) {if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);},
    body:{style:{}},createElement:makeElement,
  },
  window:{addEventListener(){},scrollTo(){}},
  location:{hash:'#library'},history:{replaceState(){}},navigator:{},
  setTimeout(){return 1;},clearTimeout(){},
});
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/, '');
vm.runInContext(source,context);
const run = code => vm.runInContext(code,context);
const plain = value => JSON.parse(JSON.stringify(value));
const click = async (action,extra={}) => {
  const button={...makeElement(),dataset:{action,...extra},innerHTML:action};
  const target={closest:selector=>selector==='[data-action]'?button:null,matches:()=>false};
  for(const listener of listeners.get('click')||[])await listener({target,preventDefault(){},stopPropagation(){}});
};

async function main(){
  run(`toast=()=>{};state.user={id:'qa-user',name:'运营测试',role:'operator'};state.route='import';`);

  const group=plain(run(`globalThis.group=importGroup([{name:'sample.png',size:30,type:'image/png'}],'手动商品名');state.groups=[group];group;`));
  assert.deepEqual(group.listings,[],'New imports start without invented platform data');
  const emptyImport=run('importWorkspace()');
  assert(emptyImport.includes('待完善')&&!emptyImport.includes('id="import-status"'),'Classification is derived from platform records');
  assert.match(run('groupCard(group)'),/data-action="ai-title"[^>]*disabled/,'Unconfigured AI is visibly unavailable');
  assert.equal(run('aiReady()'),false);
  run(`state.aiConfig={active_provider:'qa',providers:[{id:'qa',label:'测试服务',configured:true,enabled:false,model:'vision-test',vision:false}]};`);
  assert.equal(run('aiReady()'),false,'Saved but disabled services cannot call AI');
  run(`state.aiConfig.providers[0].enabled=true;state.aiConfig.providers[0].model=' ';`);
  assert.equal(run('aiReady()'),false,'A usable model must be configured');
  run(`state.aiConfig.providers[0].model='vision-test';`);
  assert.equal(run('aiReady()'),true);
  assert(!/data-action="ai-title"[^>]*disabled/.test(run('groupCard(group)')),'Configured AI enables naming');
  assert(run('aiControlsNote()').includes('文字'),'Text mode is disclosed');
  run(`state.aiConfig.providers[0].vision=true;`);
  assert(run('aiControlsNote()').includes('图片'),'Image sharing is disclosed');

  const listings=[
    {platform:'Temu',store:'一号店',product_code:'001-RED',product_number:'000123',status:'archived',price:'8.50',currency:'USD',link:'https://example.com/a'},
    {platform:'Amazon',store:'US',product_code:'AM-RED',product_number:'B001',status:'listed',price:'19.99',currency:'USD',link:''},
  ];
  context.listingInput=listings;
  const editor=run('listingEditor(listingInput)');
  for(const field of ['platform','store','product_code','product_number','status','price','currency','link'])assert.equal((editor.match(new RegExp(`data-listing-field="${field}"`,'g'))||[]).length,2,`${field} has a separate input per platform`);
  assert(editor.includes('value="000123"'),'Numeric identifiers retain leading zeros');
  const rows=listings.map(listing=>({querySelectorAll:()=>Object.entries(listing).map(([key,value])=>({dataset:{listingField:key},value:` ${value} `}))}));
  element('#overlay-root').querySelectorAll=()=>rows;
  assert.deepEqual(plain(run('readListings()')),listings,'Platform fields are submitted independently as strings');
  assert.throws(()=>run(`validateListings([{...blankListing(),platform:''}])`),/平台名称/);
  for(const price of ['0','-1','NaN','Infinity'])assert.throws(()=>run(`validateListings([{...blankListing(),platform:'Temu',price:${JSON.stringify(price)}}])`),/大于0/);
  assert.equal(run(`validateListings([{...blankListing(),platform:'Temu',price:''}])[0].price`),'','Unknown prices may remain blank');
  assert.equal(run(`validateListings([{...blankListing(),platform:'Temu',price:'0.01'}])[0].price`),'0.01');
  const detail=run('listingDetails(listingInput)');
  assert(detail.includes('Temu')&&detail.includes('Amazon')&&detail.includes('已下架')&&detail.includes('已上架'),'One platform may be down while another remains live');
  assert(!run(`listingDetails([{...blankListing(),platform:'<img src=x>',link:'javascript:alert(1)'}])`).includes('<img'),'Untrusted platform text and links are safely rendered');

  run(`globalThis.product={id:'qa-product',code:'MD-QA',name:'测试商品',owner:'运营测试',created_by:'qa-user',status:'listed',workflow_state:'listed',images:[],listings:listingInput};`);
  const card=run('productCard(product)');
  assert(card.startsWith('<article')&&card.includes('data-action="edit-card"')&&card.includes('data-product-select'),'Cards have independent detail, edit and selection controls');
  assert(card.includes('有平台在售')&&card.includes('已下架')&&card.includes('USD 19.99'),'Cards preserve mixed platform state');
  assert.equal(run(`workflowBadge({...product,workflow_state:'unconfigured'})`).includes('待完善'),true);
  assert.equal(run(`workflowBadge({...product,workflow_state:'legacy'})`).includes('待完善'),true,'Legacy records with no manual platform remain visible in the preparation queues');
  run(`state.trashMode=true;`);
  const trashCard=run('productCard(product)');
  assert(trashCard.includes('restore-product')&&!trashCard.includes('edit-card'),'Recycle bin offers restoration');
  run(`state.trashMode=false;state.user={id:'other',name:'其他运营',role:'operator'};`);
  assert(!run('productCard(product)').includes('edit-card'),'Unrelated operators cannot edit or remove a product');
  run(`state.user={id:'qa-user',name:'运营测试',role:'operator'};`);

  context.calls=[];
  run(`api=async(url,options={})=>{calls.push({url,options});return {items:[product],total:1};};state.route='library';state.libraryQuery='001';state.libraryStatus='listed';state.libraryPlatform='Temu';state.listingStatus='archived';state.libraryOffset=0;`);
  await run('loadLibrary()');
  const params=new URL(context.calls[0].url,'http://localhost').searchParams;
  assert.equal(params.get('workflow_state'),'listed');
  assert.equal(params.get('platform'),'Temu');
  assert.equal(params.get('listing_status'),'archived','Platform state is sent alongside the selected platform');
  assert.equal(params.get('q'),'001');
  run(`state.trashMode=true;`);
  await run('loadLibrary()');
  assert(context.calls.at(-1).url.startsWith('/api/products/trash?'),'Recycle bin reads the isolated deleted-record endpoint');
  run(`state.trashMode=false;state.route='import';state.groups=[group];`);
  run('groupNotes(group.id)');
  assert(element('#overlay-root').innerHTML.includes('id="listing-editor"')&&element('#overlay-root').innerHTML.includes('添加平台'),'Import supplement modal includes the structured platform editor');

  run(`group.listings=structuredClone(listingInput);group.notes='保留备注';group.specs='保留规格';group.files.push({file:{name:'side.png'},url:'blob:side'});state.selectedGroups=new Set([group.id]);`);
  await click('split-group',{id:String(group.id)});
  assert.equal(run('state.groups.length'),2);
  assert(run('state.groups.every(g=>g.listings.length===2&&g.notes==="保留备注")'),'Splitting groups keeps their platform and note data');
  run(`state.groups[0].listings[0].platform='Changed';`);
  assert.equal(run('state.groups[1].listings[0].platform'),'Temu','Split platform drafts are independent copies');
  run(`state.selectedGroups=new Set(state.groups.map(g=>g.id));`);
  await click('merge-groups');
  assert.equal(run('state.groups.length'),1);
  for(const platform of ['Changed','Amazon','Temu'])assert(run(`state.groups[0].listings.some(l=>l.platform===${JSON.stringify(platform)})`),'Merging groups preserves distinct source platform records');

  run(`state.queryFiles=[{name:'query.png',size:30,type:'image/png'}];state.queryUrls=['blob:query'];state.aiConfig.share_library_context=true;state.aiConfig.providers[0].vision=false;`);
  assert.match(run('queryArea()'),/data-action="run-ai-search"[^>]*disabled/,'AI image comparison requires image sharing to be enabled');
  assert(run('queryArea()').includes('支持视觉的模型'));
  run(`state.aiConfig.providers[0].vision=true;state.aiConfig.share_library_context=false;`);
  assert.match(run('queryArea()'),/data-action="run-ai-search"[^>]*disabled/,'Candidate text cannot be sent without library-context permission');
  assert(run('queryArea()').includes('允许商品摘要发送'));
  run(`state.aiConfig.share_library_context=true;`);
  assert(!/data-action="run-ai-search"[^>]*disabled/.test(run('queryArea()')));

  run(`state.route='import';state.groups=[group];group.name='运营手工名称';state.aiConfig.providers[0].vision=false;calls.length=0;api=(url,options)=>{calls.push({url,options});return new Promise(resolve=>globalThis.resolveTitle=resolve);};`);
  const titlePromise=run('generateTitle(group.id)');
  assert.equal(run('state.titleBusy.has(group.id)'),true);
  run(`group.name='请求期间手动改名';resolveTitle({title:'简短商品名'});`);
  await titlePromise;
  assert.equal(run('group.name'),'请求期间手动改名','Naming never silently overwrites an operator edit');
  assert.equal(run('group.aiSuggestion'),'简短商品名');
  assert.equal(run('state.titleBusy.has(group.id)'),false);
  assert.equal(context.calls[0].url,'/api/ai/title');
  assert.equal(context.calls[0].options.body.values.filter(entry=>entry[0]==='images').length,0,'Text-mode naming never sends pictures');
  assert(run('groupCard(group)').includes('data-action="accept-title"'),'Generated titles require explicit adoption');
  await click('accept-title',{id:String(group.id)});
  assert.equal(run('group.name'),'简短商品名');
  assert.equal(run('group.aiSuggestion'),'');
  run(`state.aiConfig.providers[0].vision=true;group.files=Array.from({length:5},(_,i)=>({file:{name:'picture-'+i+'.png',size:30},url:'blob:'+i}));api=async(url,options)=>{calls.push({url,options});return {title:'视觉商品名'};};`);
  await run('generateTitle(group.id)');
  assert.equal(context.calls.at(-1).options.body.values.filter(entry=>entry[0]==='images').length,3,'Naming sends at most three selected pictures');
  assert.equal(run('group.name'),'简短商品名','A later generated title remains a suggestion');

  run(`state.route='search';state.engineStatus={state:'ready',ready:true,indexed_images:1,total_images:1};globalThis.candidate={product,query_index:0,query_fingerprint:'test-query',match_type:'semantic',evidence_label:'商品外观接近',reason:'本地检索依据',previous_decision:{decision:'same',actor:'运营测试'},ai_review:{decision:'different',reason:'模型发现差异',differences:['颜色不同']}};state.searchResult={items:[candidate],additional_items:[],total_searched:1,engine_status:state.engineStatus};globalThis.previousSearch=state.searchResult;api=async()=>{throw new Error('测试模型暂不可用');};`);
  await run('runSearch(true)');
  assert.equal(run('state.searchResult===previousSearch'),true,'AI failures preserve the prior local results');
  assert.equal(run('state.searching'),false,'AI failures restore the query controls');
  const retained=element('#search-results').innerHTML;
  assert(retained.includes('测试商品')&&retained.includes('本地检索依据'),'Local evidence remains visible after AI failure');
  assert(retained.includes('团队核对：已确认同款')&&retained.includes('AI 复核')&&retained.includes('颜色不同'),'AI advice and saved human decisions remain distinct');
  assert(!run(`aiReviewBlock({decision:'same',reason:'<script>bad()</script>',differences:['<img src=x>']})`).includes('<script>'),'Model outputs are rendered as text');

  console.log('WORKFLOW_UI_SMOKE_OK: platform drafts and filters, safe rendering, recovery controls, merge/split retention, AI permissions, title adoption, AI failure recovery and independent human feedback');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
