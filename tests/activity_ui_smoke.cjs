/* Server-side activity filtering, calendar boundaries and async UI races. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const elements=new Map(),listeners=new Map();
const makeElement=()=>({innerHTML:'',textContent:'',value:'',hidden:false,disabled:false,dataset:{},style:{},isConnected:true,classList:{add(){},remove(){},toggle(){}},append(){},remove(){},focus(){},click(){},querySelector(){return null;},querySelectorAll(){return [];}});
const element=selector=>{if(!elements.has(selector))elements.set(selector,makeElement());return elements.get(selector);};
class TestForm{constructor(id,values={}){Object.assign(this,makeElement(),{id,values});}}
class TestFormData{constructor(form){this.values=Object.entries(form?.values||{});}append(...entry){this.values.push(entry);}get(key){return this.values.find(entry=>entry[0]===key)?.[1]??null;}[Symbol.iterator](){return this.values[Symbol.iterator]();}}
const context=vm.createContext({console,URL,URLSearchParams,FormData:TestFormData,HTMLFormElement:TestForm,structuredClone,Blob,document:{querySelector:element,querySelectorAll(){return [];},addEventListener(name,listener){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(listener);},body:{style:{}},createElement:makeElement},window:{addEventListener(){},scrollTo(){}},location:{hash:'#activity'},history:{replaceState(){}},navigator:{},setTimeout(){return 1;},clearTimeout(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/,''),context);
const run=code=>vm.runInContext(code,context);
const plain=value=>JSON.parse(JSON.stringify(value));
context.calls=[];context.notices=[];
run(`toast=(message,error)=>notices.push({message,error});state.user={id:'operator',name:'运营测试',role:'operator'};state.route='activity';globalThis.response={items:[{id:'log-1',action:'create',actor:'运营测试',created_at:'2026-11-12T04:00:00Z',details:{name:'商品一'}}],total:81,actors:[{id:'operator',name:'运营测试'},{id:'disabled',name:'历史操作者'},{id:'__system__',name:'系统'},{id:'same-name',name:'运营测试'},{id:'unsafe',name:'<img src=x>'}]};api=async(url,options={})=>{calls.push({url,options});return response;};`);
const lastParams=()=>new URL(context.calls.at(-1).url,'http://localhost').searchParams;
const click=async(action,extra={})=>{const button={...makeElement(),dataset:{action,...extra}};const target={closest:selector=>selector==='[data-action]'?button:null,matches:()=>false};for(const listener of listeners.get('click')||[])await listener({target,preventDefault(){},stopPropagation(){}});};
async function main(){
  await run('renderActivity()');
  const initial=new URL(context.calls[0].url,'http://localhost').searchParams;
  assert.equal(initial.get('limit'),'40');assert.equal(initial.get('offset'),'0');
  for(const key of ['actor_id','start_date','end_date'])assert.equal(initial.has(key),false,'Empty filters are omitted');
  const controls=element('#activity-filter-controls').innerHTML;
  assert(controls.includes('历史操作者')&&controls.includes('__system__'),'Historical and system actors come from visible activity metadata');
  assert.equal((controls.match(/>运营测试<\/option>/g)||[]).length,2,'Same names remain distinct actor IDs');
  assert(!controls.includes('<img src=x>'),'Actor names are escaped');
  assert(controls.includes('北京时间')&&controls.includes('包含起止当天'),'Calendar boundaries are disclosed');
  assert(!context.calls.some(call=>call.url.startsWith('/api/team')),'Regular operators do not need administrator team access');
  assert(element('#activity-results').innerHTML.includes('81')&&element('#activity-results').innerHTML.includes('每页 40 条'),'Results show server total and pagination');

  context.filterForm=new TestForm('activity-filter-form',{actor_id:'disabled',start_date:'2026-10-01',end_date:'2026-11-13'});
  for(const listener of listeners.get('submit')||[])await listener({target:context.filterForm,preventDefault(){}});
  assert.equal(lastParams().get('actor_id'),'disabled');assert.equal(lastParams().get('start_date'),'2026-10-01');assert.equal(lastParams().get('end_date'),'2026-11-13');
  assert.equal(run('state.activityOffset'),0,'Applying filters resets pagination');
  await click('activity-page',{direction:'next'});
  assert.equal(lastParams().get('offset'),'40');assert.equal(lastParams().get('actor_id'),'disabled','Pagination preserves the server filters');
  await click('activity-page',{direction:'next'});assert.equal(lastParams().get('offset'),'80');
  await click('activity-page',{direction:'prev'});assert.equal(lastParams().get('offset'),'40');
  const beforeInvalid=context.calls.length;
  await run(`applyActivityFilters({start_date:'2026-11-14',end_date:'2026-11-13'})`);
  assert.equal(context.calls.length,beforeInvalid,'Invalid date order does not issue a request');
  assert(context.notices.at(-1).error);

  assert.equal(run(`activityCalendarDay(new Date('2026-01-01T16:30:00Z'))`),'2026-01-02','Today uses Beijing time even when the host day differs');
  assert.deepEqual(plain(run(`activityDateRange('today',new Date('2026-01-01T16:30:00Z'))`)),{start_date:'2026-01-02',end_date:'2026-01-02'});
  assert.deepEqual(plain(run(`activityDateRange('7',new Date('2026-01-01T16:30:00Z'))`)),{start_date:'2025-12-27',end_date:'2026-01-02'},'Last seven days include today and cross year boundaries');
  assert.deepEqual(plain(run(`activityDateRange('30',new Date('2026-01-01T16:30:00Z'))`)),{start_date:'2025-12-04',end_date:'2026-01-02'});
  await click('activity-range',{range:'7'});
  assert.equal(lastParams().get('actor_id'),'disabled','Date shortcuts preserve the chosen actor');
  assert(lastParams().get('start_date')&&lastParams().get('end_date'));
  await click('activity-range',{range:'all'});
  assert.equal(lastParams().get('actor_id'),'disabled');assert.equal(lastParams().has('start_date'),false,'All-time clears only dates');
  await click('clear-activity-filters');
  for(const key of ['actor_id','start_date','end_date'])assert.equal(lastParams().has(key),false,'Clear resets every filter');

  run(`response={items:[],total:0,actors:response.actors};`);
  await run(`applyActivityFilters({actor_id:'__system__'})`);
  assert(element('#activity-results').innerHTML.includes('这个范围内没有操作记录'),'Filtered emptiness suggests adjusting filters');
  assert.equal(lastParams().get('actor_id'),'__system__');
  run(`api=async(url)=>{calls.push({url});throw new Error('测试网络异常');};`);
  await run('loadActivity()');
  assert(element('#activity-results').innerHTML.includes('测试网络异常')&&element('#activity-results').innerHTML.includes('retry-activity'),'Failures preserve filters and provide a retry');
  assert.equal(run('state.activityLoading'),false);

  run(`globalThis.rebounds=0;state.activityOffset=80;api=async(url)=>{calls.push({url});rebounds++;return {items:[{action:'update',actor:'运营测试',created_at:'2026-11-13T00:00:00Z'}],total:12,actors:response.actors};};`);
  await run('loadActivity()');
  assert.equal(run('state.activityOffset'),0,'A now-empty last page returns to the last valid page');
  assert.equal(run('rebounds'),2,'Page rebound loads the valid server slice');
  assert.equal(lastParams().get('offset'),'0');

  run(`globalThis.resolvers=[];api=()=>new Promise(resolve=>resolvers.push(resolve));globalThis.first=applyActivityFilters({actor_id:'old'});globalThis.second=applyActivityFilters({actor_id:'new'});`);
  assert(element('#activity-results').innerHTML.includes('正在筛选'),'Pending requests have a loading state');
  run(`resolvers[1]({items:[],total:22,actors:[{id:'new',name:'最新操作者'}]});`);await run('second');
  run(`resolvers[0]({items:[],total:999,actors:[{id:'old',name:'旧操作者'}]});`);await run('first');
  assert.equal(run('state.activityData.total'),22,'A slow old result cannot replace a newer filter result');
  assert.equal(run('state.activityFilters.actor_id'),'new');
  assert(element('#activity-filter-controls').innerHTML.includes('最新操作者')&&!element('#activity-filter-controls').innerHTML.includes('旧操作者'));
  run(`globalThis.third=loadActivity();state.route='library';state.routeRequest++;resolvers[2]({items:[],total:777,actors:[]});`);await run('third');
  assert.equal(run('state.activityData.total'),22,'Leaving the page prevents a stale DOM/state update');
  run(`state.route='activity';api=async()=>({items:[],total:22,actors:[{id:'new',name:'最新操作者'}]});`);await run('renderActivity()');
  assert.equal(run('state.activityFilters.actor_id'),'new','Returning to the page preserves the filters');

  run(`globalThis.didRefresh=false;confirmDialog=async(title,description,action)=>action();refreshStats=async()=>{didRefresh=true;};api=async(url,options={})=>{calls.push({url,options});return url.includes('/undo')?{ok:true}:{items:[],total:0,actors:[{id:'new',name:'最新操作者'}]};};`);
  await click('undo-batch',{id:'test-batch'});
  // The dialog callback is asynchronous and intentionally independent of the click turn.
  await new Promise(resolve=>setImmediate(resolve));
  assert(run('didRefresh'),'Undo refreshes workspace counts');
  assert(context.calls.some(call=>call.url==='/api/import/test-batch/undo'));
  assert.equal(lastParams().get('actor_id'),'new','Undo refreshes the current activity filters');
  assert.equal(run('state.activityData.total'),0,'Undo refreshes result totals');
  run('resetActivityState();');
  assert.equal(run('state.activityData.actors.length'),0,'Account reset removes previous actor metadata');
  console.log('ACTIVITY_UI_SMOKE_OK: server actor/date filters, historical actors, Beijing calendar shortcuts, pagination, invalid input, empty/error/retry, page rebound, stale-request isolation and undo refresh');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
