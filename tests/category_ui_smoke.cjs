/* Custom product category UI contracts with synthetic records and no network. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements=new Map(),listeners=new Map();
const makeElement=()=>({
  innerHTML:'',textContent:'',style:{},dataset:{},disabled:false,isConnected:true,
  classList:{add(){},remove(){},toggle(){}},append(){},remove(){},focus(){},click(){},
  querySelector(){return null;},querySelectorAll(){return [];},
});
const element=selector=>{if(!elements.has(selector))elements.set(selector,makeElement());return elements.get(selector);};
class TestForm {
  constructor(id,values={},dataset={}){Object.assign(this,makeElement(),{id,values,dataset});}
}
class TestFormData {
  constructor(form){this.values=Object.entries(form?.values||{});}
  append(...entry){this.values.push(entry);}
  get(key){return this.values.find(entry=>entry[0]===key)?.[1]??null;}
  has(key){return this.values.some(entry=>entry[0]===key);}
  entries(){return this.values.map(entry=>entry.slice(0,2))[Symbol.iterator]();}
  [Symbol.iterator](){return this.entries();}
}
class TestURL extends URL {
  static createObjectURL(){return 'blob:category-qa';}
  static revokeObjectURL(){}
}
const context=vm.createContext({
  console,URL:TestURL,URLSearchParams,FormData:TestFormData,HTMLFormElement:TestForm,structuredClone,Blob,
  document:{
    querySelector:element,querySelectorAll(){return [];},
    addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);},
    body:{style:{}},createElement:makeElement,
  },
  window:{addEventListener(){},scrollTo(){}},
  location:{hash:'#library'},history:{replaceState(){}},navigator:{},
  setTimeout(){return 1;},clearTimeout(){},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/,''),context);
const run=code=>vm.runInContext(code,context);
const plain=value=>JSON.parse(JSON.stringify(value));
async function emit(type,target){for(const listener of listeners.get(type)||[])await listener({target,preventDefault(){},stopPropagation(){}});}
async function click(action,extra={}){
  const button={...makeElement(),dataset:{action,...extra},innerHTML:action};
  await emit('click',{closest:selector=>selector==='[data-action]'?button:null,matches:()=>false});
}
const paramsOf=call=>new URL(call.url,'http://localhost').searchParams;

async function main(){
  run(`toast=()=>{};state.user={id:'qa-user',name:'分类测试',role:'operator'};state.route='library';
    state.categories=[{name:'柱蜡',count:3},{name:'uncategorized',count:2},{name:'<img src=x onerror=evil()>',count:1}];
    state.categoryUncategorizedCount=4;`);
  const field=run(`categoryField('自定义" autofocus="evil')`);
  assert(field.includes('<input name="category"')&&field.includes('maxlength="60"')&&field.includes('list="product-category-options"'),'Category is a free-text field with existing suggestions');
  assert(!field.includes('<select')&&!field.includes('required'),'Custom category can be entered or cleared');
  assert(field.includes('value="自定义&quot; autofocus=&quot;evil"')&&!field.includes(' autofocus="evil'));
  assert(field.includes('&lt;img src=x onerror=evil()&gt;')&&!field.includes('<img'),'Suggestion labels are safely escaped');
  let options=run('categoryFilterOptions()');
  assert(options.includes('value="">全部分类</option>'));
  assert(options.includes('value="uncategorized"')&&options.includes('未分类（4）'));
  assert(options.includes('value="category:柱蜡"')&&options.includes('柱蜡（3）'));
  assert(options.includes('value="category:uncategorized"')&&options.includes('uncategorized（2）'),'Literal custom name is distinct from the uncategorized sentinel');
  assert(!options.includes('<img'));
  run(`state.libraryCategory='category:临时自定义分类';`);
  options=run('categoryFilterOptions()');
  assert(options.includes('value="category:临时自定义分类" selected>临时自定义分类（0）'),'An active category stays selected even if its current count is zero');

  context.calls=[];
  run(`api=async(url,options={})=>{
    calls.push({url,options});
    if(url.startsWith('/api/products?'))return {items:[],total:0};
    if(url==='/api/categories')return {items:[{name:'水晶',count:5}],uncategorized_count:6};
    if(url==='/api/stats')return {total_products:11};
    if(url==='/api/import')return {created:2};
    throw new Error('Unexpected request '+url);
  };state.libraryQuery='红色';state.libraryStatus='listed';state.libraryPlatform='Temu';state.listingStatus='archived';state.libraryOffset=24;`);
  for(const [selection,expectedCategory,expectedUncategorized] of [
    ['category:柱蜡','柱蜡',null],
    ['uncategorized',null,'true'],
    ['category:uncategorized','uncategorized',null],
    ['',null,null],
    ['category:配件 & 蜡/模具','配件 & 蜡/模具',null],
  ]){
    context.selection=selection;
    run('state.libraryCategory=selection;');
    await run('loadLibrary()');
    const params=paramsOf(context.calls.at(-1));
    assert.equal(params.get('category'),expectedCategory);
    assert.equal(params.get('uncategorized'),expectedUncategorized);
    assert.equal(params.get('q'),'红色');assert.equal(params.get('workflow_state'),'listed');
    assert.equal(params.get('platform'),'Temu');assert.equal(params.get('listing_status'),'archived');
    assert.equal(params.get('offset'),'24','Category queries preserve the current pagination unless selection changes');
  }
  await emit('change',{id:'library-category',value:'category:柱蜡',dataset:{}});
  assert.equal(run('state.libraryCategory'),'category:柱蜡');
  assert.equal(paramsOf(context.calls.at(-1)).get('offset'),'0','Choosing a category starts on the first page');
  await run('refreshCategories()');
  assert.deepEqual(plain(run('state.categories')),[{name:'水晶',count:5}]);
  assert(element('#library-category').innerHTML.includes('未分类（6）'),'Refreshed category counts reach the selector');

  run(`state.detail={id:'qa-product',name:'分类商品',code:'MD-QA',owner:'分类测试',created_by:'qa-user',category:'水晶',listings:[],images:[]};state.detailListings=[];state.detailEditing=true;renderDetail();`);
  assert(element('#overlay-root').innerHTML.includes('name="category" value="水晶"'),'Editing a product includes its current category');
  run('state.detailEditing=false;renderDetail();');
  assert(element('#overlay-root').innerHTML.includes('<dt>商品分类</dt><dd>水晶</dd>'),'Product detail displays its category');
  assert(!element('#overlay-root').innerHTML.includes('name="category"'));
  run(`state.detail.category='';renderDetail();`);
  assert(element('#overlay-root').innerHTML.includes('<dt>商品分类</dt><dd>未分类</dd>'));

  run(`state.route='import';globalThis.group=importGroup([{name:'main.png',size:20,type:'image/png'},{name:'side.png',size:20,type:'image/png'}],'分类导入');state.groups=[group];`);
  assert.equal(run('group.category'),'','New import groups have no invented category');
  run('groupNotes(group.id);');
  assert(element('#overlay-root').innerHTML.includes('name="category"'),'Import supplement modal accepts custom categories');
  const form=new TestForm('group-note-form',{category:'  水晶  ',specs:'原规格',notes:'原备注'},{id:String(run('group.id'))});
  await emit('submit',form);
  assert.equal(run('group.category'),'水晶','Saving the group trims category whitespace');
  assert.equal(run('group.notes'),'原备注');assert.equal(run('group.specs'),'原规格');
  const groupId=run('group.id');
  await click('split-group',{id:String(groupId)});
  assert.equal(run('state.groups.length'),2);
  assert(run(`state.groups.every(g=>g.category==='水晶')`),'Splitting images preserves their product category');
  run('state.selectedGroups=new Set(state.groups.map(g=>g.id));');
  await click('merge-groups');
  assert.equal(run('state.groups.length'),1);
  assert.equal(run('state.groups[0].category'),'水晶','Merging equal categories preserves the category');
  run(`globalThis.other=importGroup([{name:'other.png',size:20,type:'image/png'}],'不同分类');other.category='柱蜡';state.groups.push(other);state.selectedGroups=new Set(state.groups.map(g=>g.id));`);
  await click('merge-groups');
  assert.equal(run('state.groups.length'),1);
  assert.equal(run('state.groups[0].category'),'','Merging different categories clears the category for explicit reassignment');

  run(`state.groups[0].category='模具蜡';globalThis.uncategorizedGroup=importGroup([{name:'empty.png',size:20,type:'image/png'}],'未分类商品');state.groups.push(uncategorizedGroup);calls.length=0;`);
  await run('submitImport()');
  const imported=context.calls.find(call=>call.url==='/api/import');
  assert(imported&&imported.options.method==='POST');
  const manifest=JSON.parse(imported.options.body.get('manifest'));
  assert.deepEqual(manifest.groups.map(group=>group.category),['模具蜡',''],'Each import group submits its own category including an explicit empty value');
  assert.equal(manifest.groups[0].paths.length,3,'Category handling does not lose merged images');
  assert.equal(run('state.groups.length'),0,'Successful import clears only the submitted drafts');
  assert.equal(run('state.importing'),false);
  assert(context.calls.every(call=>call.url.startsWith('/api/')));

  console.log('CATEGORY_UI_SMOKE_OK: escaped free-text categories, counts and sentinel isolation, combined filters, detail/edit fields, trimmed import drafts, split/merge retention and import manifests');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
