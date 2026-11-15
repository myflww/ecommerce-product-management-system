/* Offline DOM/API contracts; no user browser or application data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const events = [], calls = [], messages = [];
const context = vm.createContext({URL, AbortController, Map, Set, JSON, String, Number, Array, Boolean,
  setTimeout, clearTimeout, queueMicrotask, console,
  document:{querySelector(){return null;},querySelectorAll(){return [];},activeElement:null},
  window:{dispatchEvent(event){events.push(event);}},
  CustomEvent:class{constructor(type, options){this.type=type;this.detail=options.detail;}}
});
const source = fs.readFileSync(path.join(__dirname,'../static/product-merge.js'),'utf8').replace(/export /g, '').replace(/if \(typeof document !== 'undefined'\) \{[\s\S]*$/, '');
vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
context.events = events; context.calls = calls; context.messages = messages;
run(`renderDialog=()=>{};notify=(message,failed)=>messages.push({message,failed});
  const products=[{id:'a',code:'MD-A',name:'主商品<script>alert(1)</script>',owner:'甲',images:[],listings:[{id:'l-a',platform:'Temu',store:'一店',product_code:'RED',product_number:'SKC1',price:'20',currency:'USD',status:'listed',link:'javascript:alert(1)'}]},
    {id:'b',code:'MD-B',name:'同品',owner:'乙',images:[],listings:[{id:'l-b',platform:'Temu',store:'二店',product_code:'RED',price:'30',currency:'USD',status:'archived',link:'https://shop.example.com/p/1'}]},
    {id:'c',code:'MD-C',name:'亚马逊',images:[],listings:[{id:'l-c',platform:'Amazon',store:'一店',product_code:'AMZ'}]}];
  const draft={ids:['a','b','c'],target_id:'a',products,image_count:3,listing_count:3,expected_token:'f'.repeat(64),metadata_policy:'保留主资料',listing_policy:'保留所有SKU'};
  function session(){return {kind:'merge',phase:'preview',ids:draft.ids,targetId:'a',products,draft:{...draft},busy:false,error:''};}
  let s=session(); active=s;`);

async function main() {
  assert.equal(run(`canMerge({role:'owner'})`), false);
  assert.equal(run(`canMerge({permissions:['products.merge']})`), true);
  assert.equal(run(`canMerge({permissions:{'products.merge':true}})`), false);
  assert.equal(run(`canMerge({permissions:['products.edit_all']})`), false);
  assert.equal(run(`safeLink('javascript:alert(1)')`), '');
  assert.equal(run(`safeLink('https://user:secret@example.com')`), '');
  assert.equal(run(`safeImage('https://images.example.com/p.jpg')`), '');
  assert.equal(run(`safeImage('/media/'+'a'.repeat(32)+'?thumb=1')`), '/media/'+'a'.repeat(32)+'?thumb=1');
  assert.equal(run(`groupListings(products).length`), 3);
  const markup = run('previewMarkup(draft)');
  assert(!markup.includes('<script>'));
  assert(!markup.includes('javascript:'));
  assert(markup.includes('&lt;script&gt;'));
  assert(markup.includes('Temu') && markup.includes('Amazon') && markup.includes('二店') && markup.includes('RED'));
  assert(markup.includes('原价 USD 20') && markup.includes('已下架') && markup.includes('https://shop.example.com/p/1'));
  const choices = run('choiceMarkup(products,"b")');
  assert(choices.includes('value="b" checked') && !choices.includes('<script>'));
  assert(!run(`historyMarkup([{id:'x',target_id:'a',created_at:'now',can_undo:false,original_products:[{id:'a',name:'<b>unsafe</b>'}]}])`).includes('data-pm-undo'));
  assert(run(`historyMarkup([{id:'x',target_id:'a',can_undo:true,original_products:[]}])`).includes('data-pm-undo="x"'));

  run(`apiRequest=async(path,options)=>{calls.push({path,options});return {product:{id:'a',name:'主商品'},merged_ids:['b','c'],merge_id:'merge-1',undo_token:'u'};};`);
  await Promise.all([run('doCommit(s)'), run('doCommit(s)')]);
  assert.equal(calls.length, 1, 'repeated click must not submit twice');
  assert.equal(calls[0].path, '/api/products/merge');
  assert.equal(calls[0].options.body.expected_token, 'f'.repeat(64));
  assert.equal(events[0].type, 'midoo:products-changed');
  assert.equal(events[0].detail.action, 'merge');
  assert.equal(run('s.phase'), 'done');
  assert.equal(run('s.draft'), null);

  calls.length = 0;
  run(`s=session();active=s;apiRequest=async(path,options)=>{calls.push({path,options});const error=new Error('资料已更新');error.status=409;throw error;};`);
  await run('doCommit(s)');
  assert.equal(run('s.phase'), 'select');
  assert.equal(run('s.draft'), null, 'stale token must not remain usable');
  assert.equal(run('s.error'), '资料已更新');
  assert.equal(run('s.busy'), false);
  await run('doCommit(s)');
  assert.equal(calls.length, 1);

  calls.length = 0;
  run(`s=session();s.phase='select';s.draft=null;active=s;apiRequest=async(path,options)=>{calls.push({path,options});return {...draft,target_id:options.body.target_id,expected_token:'new-token'};};s.targetId='b';`);
  await run('doPreview(s)');
  assert.equal(calls[0].path, '/api/products/merge/preview');
  assert.equal(calls[0].options.body.target_id, 'b');
  assert.equal(run('s.phase'), 'preview');
  assert.equal(run('s.draft.expected_token'), 'new-token');

  calls.length = 0; events.length = 0;
  run(`s=session();s.phase='done';s.result={merge_id:'merge-1',undo_token:'undo-token'};active=s;
    apiRequest=async(path,options)=>{calls.push({path,options});return path.endsWith('/undo')?{product:{id:'a'},restored_ids:['b','c']}:{items:[]};};`);
  await Promise.all([run('doUndo(s,"merge-1")'), run('doUndo(s,"merge-1")')]);
  assert.equal(calls.filter(call => call.path.endsWith('/undo')).length, 1);
  assert.equal(calls[0].options.body.expected_token, 'undo-token');
  assert.equal(events[0].detail.action, 'undo-merge');
  assert.equal(run('s.phase'), 'history');
  console.log('Product merge UI: permission, escaping, platform/store grouping, preview, concurrent submit, conflict and undo contracts passed.');
}
main().catch(error => { console.error(error); process.exitCode=1; });
