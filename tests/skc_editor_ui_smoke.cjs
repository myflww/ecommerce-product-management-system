/* SKC editor interaction checks: synthetic DOM, local VM, no company data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const listeners = new Map();
const elements = new Map();
const decode = text => String(text).replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const attribute = (html,name) => decode(html.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`))?.[1] || '');
const element = () => ({innerHTML:'',textContent:'',dataset:{},style:{},disabled:false,isConnected:true,scrollTop:0,classList:{add(){},remove(){},toggle(){}},focus(){},click(){},querySelector(){return null;},querySelectorAll(){return [];}});
function fields(html) {
  return [...html.matchAll(/<input\b([^>]*)>|<select\b([^>]*)>([\s\S]*?)<\/select>/g)].map(match=>{
    const attrs=match[1]||match[2],field=attribute(attrs,'data-listing-field'),shared=attribute(attrs,'data-group-field');
    if(!field&&!shared)return null;
    let value=attribute(attrs,'value');
    if(match[2]){
      const options=[...match[3].matchAll(/<option\b([^>]*)>([^<]*)<\/option>/g)];
      const selected=options.find(option=>/\bselected\b/.test(option[1]))||options[0];
      value=selected?(attribute(selected[1],'value')||decode(selected[2])):'';
    }
    return {...element(),dataset:{...(field?{listingField:field}:{}),...(shared?{groupField:shared}:{})},value};
  }).filter(Boolean);
}
let groups=[];
const editor=element();
let markup='';
Object.defineProperty(editor,'innerHTML',{get:()=>markup,set:html=>{
  markup=html;
  groups=[...html.matchAll(/<fieldset\b([^>]*data-listing-group="\d+"[^>]*)>([\s\S]*?)<\/fieldset>/g)].map(match=>{
    const group={...element(),dataset:{listingGroup:attribute(match[1],'data-listing-group'),listingGroupKey:attribute(match[1],'data-listing-group-key')}};
    group.shared=fields(match[2].split('<div class="listing-skus-heading">')[0]);
    group.rows=[...match[2].matchAll(/<section\b([^>]*data-listing-index="\d+"[^>]*)>([\s\S]*?)<\/section>/g)].map(row=>({
      ...element(),dataset:{listingIndex:attribute(row[1],'data-listing-index'),listingId:attribute(row[1],'data-listing-id')},fields:fields(row[2]),
      querySelectorAll(selector){return selector==='[data-listing-field]'?this.fields:[];},
    }));
    group.querySelectorAll=selector=>selector==='[data-group-field]'?group.shared:selector==='[data-listing-index]'?group.rows:[];
    return group;
  });
}});
const overlay=element(),body=element();
body.scrollTop=280;
class Form {constructor(id,values={}){Object.assign(this,element(),{id,values});}}
const productForm=new Form('product-edit-form',{name:'保留商品名',owner:'运营',category:'蜡烛',notes:'保留备注',specs:'保留总规格'});
const submitButton=element();
productForm.querySelector=()=>submitButton;
overlay.querySelector=selector=>selector==='#listing-editor'?editor:selector==='#product-edit-form'?productForm:selector==='.drawer-body'?body:null;
overlay.querySelectorAll=selector=>selector==='[data-listing-group]'?groups:selector==='[data-listing-index]'?groups.flatMap(group=>group.rows):[];
function lookup(selector){if(selector==='#overlay-root')return overlay;if(selector==='#listing-editor')return editor;if(selector==='#product-edit-form')return productForm;if(!elements.has(selector))elements.set(selector,element());return elements.get(selector);}
class FormDataMock {constructor(form){this.values=Object.entries(form.values||{});}get(key){return this.values.find(item=>item[0]===key)?.[1]??null;}entries(){return this.values[Symbol.iterator]();}[Symbol.iterator](){return this.entries();}}
const context=vm.createContext({
  console,URL,URLSearchParams,structuredClone,Blob,HTMLFormElement:Form,FormData:FormDataMock,
  document:{querySelector:lookup,querySelectorAll(){return [];},addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);},body:{style:{}},createElement:element},
  window:{addEventListener(){},scrollTo(){}},location:{hash:'#library'},history:{replaceState(){}},navigator:{},setTimeout(){return 1;},clearTimeout(){},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/,''),context);
const run=code=>vm.runInContext(code,context);
const plain=value=>JSON.parse(JSON.stringify(value));
const calls=[],messages=[];
context.calls=calls;context.messages=messages;context.productForm=productForm;
context.seed=[
  {id:'red',platform:'Temu',store:'美国店',product_number:'000123',product_code:'RED-2',link:'https://shop.example.com/item',region:'US',variant_hint:'红色 / 2件装',status:'listed',price:'20',currency:'USD'},
  {id:'amazon',platform:'Amazon',store:'美国店',product_number:'B001',product_code:'AM-1',link:'https://other.example.com/a',region:'US',variant_hint:'白色',status:'archived',price:'40',currency:'USD'},
  {id:'blue',platform:'Temu',store:'美国店',product_number:'000123',product_code:'BLUE-4',link:'https://shop.example.com/item',region:'US',variant_hint:'蓝色 / 4件装',status:'archived',price:'30',currency:'USD'},
];
run(`toast=(message,error=false)=>messages.push({message,error});state.user={id:'qa',name:'运营',role:'operator'};state.route='library';state.detail={id:'product',name:'保留商品名',owner:'运营',created_by:'qa',listings:structuredClone(seed),images:[]};state.detailEditing=true;state.detailListings=structuredClone(seed);api=async(url,options={})=>{calls.push(JSON.parse(JSON.stringify({url,options})));return {...state.detail,...options.body};};`);
function render(){editor.innerHTML=run('listingEditor(state.detailListings)');}
function rows(){return plain(run('readListings()'));}
const groupField=(g,field)=>groups[g].shared.find(input=>input.dataset.groupField===field);
const skuField=(g,s,field)=>groups[g].rows[s].fields.find(input=>input.dataset.listingField===field);
async function click(action,extra={}){
  const button={...element(),dataset:{action,...extra}};
  for(const fn of listeners.get('click')||[])await fn({target:{closest:selector=>selector==='[data-action]'?button:null,matches:()=>false},preventDefault(){},stopPropagation(){}});
}

async function main(){
  render();
  assert.equal(groups.length,2,'One shared link/SKC has one editor group, even when flat rows were non-adjacent');
  assert.equal(groups[0].rows.length,2);
  assert.equal(groupField(0,'product_number').value,'000123','Identifiers retain leading zeroes');
  assert.deepEqual(rows().map(row=>row.id),['red','blue','amazon'],'Grouping preserves every stable SKU ID');
  for(const key of ['platform','store','product_number','link','region'])assert.equal(groups[0].shared.filter(input=>input.dataset.groupField===key).length,1,`${key} is shared, not repeated for each SKU`);
  for(const key of ['product_code','variant_hint','status','price','currency'])assert.equal(groups[0].rows[0].fields.filter(input=>input.dataset.listingField===key).length,1,`${key} remains independently editable per SKU`);
  assert.deepEqual(rows().find(row=>row.id==='blue'),context.seed[2]);
  assert(!JSON.stringify(run('readListings()')).includes('_editorGroup'),'Transient grouping keys never enter transport JSON');
  const noIdentity=run('listingGroups([blankListing(),blankListing()]).length');
  assert.equal(noIdentity,2,'Blank platform/link records never collapse accidentally');
  assert.equal(run(`listingGroups([{...seed[0],region:'US'},{...seed[2],region:'CA'}]).length`),2,'Different regions are not silently merged');
  assert(!run(`listingEditor([{...seed[0],platform:'<script>bad()</script>',product_code:'\" onclick=\"bad()'}])`).includes('<script>'),'Untrusted titles and SKU identifiers are escaped');

  groupField(0,'link').value='https://shop.example.com/changed';
  skuField(0,0,'variant_hint').value='红色 / 3件装';
  const firstGroupKey=groups[0].dataset.listingGroupKey;
  await click('add-listing-sku',{groupKey:firstGroupKey});
  assert.equal(calls.length,0,'Editing SKUs never makes background HTTP calls');
  assert.equal(rows().length,4);
  const child=rows()[2];
  assert.equal(child.status,'pending','A new SKU never inherits listed/archived status');
  assert.equal(child.price,'');assert.equal(child.product_code,'');assert.equal(child.variant_hint,'');assert(!child.id);
  assert.equal(child.currency,'USD');assert.equal(child.link,'https://shop.example.com/changed');assert.equal(child.product_number,'000123');
  assert.equal(rows()[0].variant_hint,'红色 / 3件装','Repainting for a new SKU preserves unsaved sibling edits');
  assert.equal(rows()[1].status,'archived');
  assert.equal(body.scrollTop,280,'Adding SKU rows does not jump the product drawer');
  skuField(0,2,'product_code').value='GREEN-6';skuField(0,2,'variant_hint').value='绿色 / 6件装';skuField(0,2,'price').value='60';

  await click('remove-listing',{index:'1'});
  assert.deepEqual(rows().map(row=>row.id||row.product_code),['red','GREEN-6','amazon']);
  assert(markup.includes('撤销删除'));
  groupField(0,'region').value='CA';skuField(0,0,'price').value='25';
  await click('undo-listing-removal');
  assert.equal(rows().find(row=>row.id==='red').price,'25','Undoing deletion does not discard later price edits');
  assert.equal(rows().find(row=>row.id==='blue').region,'CA','A restored SKU adopts the currently edited shared region');
  assert.equal(rows().find(row=>row.id==='blue').status,'archived');
  assert.equal(rows().find(row=>row.product_code==='GREEN-6').price,'60');
  assert.equal(calls.length,0,'Undo also stays in the draft');

  await click('remove-listing-group',{groupKey:groups[0].dataset.listingGroupKey});
  assert.deepEqual(rows().map(row=>row.id),['amazon'],'Removing an SKC group removes only its draft SKUs');
  skuField(0,0,'price').value='45';
  await click('undo-listing-removal');
  assert.equal(rows().length,4);
  assert.equal(rows().find(row=>row.id==='amazon').price,'45','Undoing a group deletion preserves edits in other groups');
  const beforeSave=rows();
  run('renderDetail=()=>{};refreshStats=async()=>{};loadLibrary=async()=>{};');
  for(const fn of listeners.get('submit')||[])await fn({target:productForm,preventDefault(){}});
  assert.equal(calls.length,1,'All grouped changes are persisted by one explicit save');
  assert.equal(calls[0].url,'/api/products/product');assert.equal(calls[0].options.method,'PATCH');
  assert.deepEqual(plain(calls[0].options.body.listings),beforeSave);
  assert.equal(calls[0].options.body.name,'保留商品名');assert.equal(calls[0].options.body.notes,'保留备注');
  assert.equal(calls[0].options.body.listings[0].region,'CA');
  assert(!JSON.stringify(calls[0]).includes('_editorGroup'));

  run('state.detailEditing=true;state.detailListings=structuredClone(seed);listingRemoved=[];');render();
  await click('remove-listing-group',{groupKey:groups[0].dataset.listingGroupKey});
  const previousCallCount=calls.length;
  await click('cancel-edit');
  assert.equal(calls.length,previousCallCount,'Canceling draft deletion never sends a removal request');
  assert.equal(run('listingRemoved.length'),0,'A canceled edit cannot restore deleted rows in a later editor');
  assert.equal(run('state.detailEditing'),false);

  console.log('SKC_EDITOR_UI_SMOKE_OK: shared link/SKC groups, stable SKU IDs, independent status/prices/specs, pending new SKU, draft deletion/undo, shared field propagation, one explicit save and cancellation');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
