/* UI contract checks without a browser, cloud API, or real company data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, {innerHTML:'',textContent:'',style:{},append(){},querySelector(){return null},querySelectorAll(){return []}});
  return elements.get(selector);
};
const scheduled=[];
const context = vm.createContext({
  console, URL, FormData,
  document:{querySelector:element,querySelectorAll(){return []},addEventListener(){},body:{style:{}},createElement(){return {remove(){}}}},
  window:{addEventListener(){}},
  setTimeout(fn,delay){scheduled.push(delay);return scheduled.length},clearTimeout(){},
});
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/, '');
vm.runInContext(source,context);

async function main(){
  await vm.runInContext(`(async()=>{
    toast=()=>{};
    api=async()=>({ok:true});
    state.user={id:'tester',name:'测试成员',role:'operator'};
    state.queryUrls=['blob:query'];state.route='search';
    state.engineStatus={state:'ready',ready:true,indexed_images:20,total_images:20};
    const item=id=>({product:{id,name:'商品-'+id,owner:'测试成员',code:'MD-'+id,status:'listed',images:[{url:'/media/'+id}]},query_index:0,query_fingerprint:'known-query',match_type:'semantic',evidence_label:'商品外观接近',reason:'主体外观接近，仍需核对规格。',score:92});
    state.searchResult={items:[item('A'),item('B'),item('C')],additional_items:[item('D'),item('E'),item('F'),item('G'),item('H')],client_excluded:[],total_searched:100,engine_status:state.engineStatus};
    state.showAdditional=false;
    globalThis.first=searchResults();
    state.showAdditional=true;
    globalThis.expanded=searchResults();
    state.showAdditional=false;
    const negative={dataset:{id:'A',index:'0',area:'main',decision:'different'},innerHTML:'不是同款'};
    await decision(negative);
    globalThis.afterNegative={count:state.searchResult.items.length,excluded:state.searchResult.excluded_count,removed:!state.searchResult.items.some(r=>r.product.id==='A')};
    await reconsiderSearchItem({dataset:{index:'0'},innerHTML:'改为待核实'});
    globalThis.reconsidered={count:state.searchResult.items.length,excluded:state.searchResult.excluded_count,decision:state.searchResult.items.find(r=>r.product.id==='A').previous_decision.decision};
    await decision({dataset:{id:'B',index:'1',area:'main',decision:'same'},innerHTML:'确认同款'});
    globalThis.confirmed=searchResults();
    state.searchResult={items:[],additional_items:[],total_searched:7,engine_status:{state:'unavailable',ready:false}};
    globalThis.unavailable=searchResults();
    updateSearchEngineStatus({state:'indexing',ready:false,indexed_images:3,total_images:7,message:'更新中'});
    updateSearchEngineStatus({state:'ready',ready:true,indexed_images:7,total_images:7});
    globalThis.readyStatus=searchEngineStatus();
    globalThis.missingNumbers=!/相似分\\s*\\d/.test(first)&&!first.includes('92%');
  })()`,context);
  assert.equal((context.first.match(/<article /g)||[]).length,3,'Only three recommended cards are visible initially');
  assert(!context.first.includes('商品-D'),'Additional candidate identity is not rendered before expanding');
  assert.equal((context.expanded.match(/<article /g)||[]).length,8,'Explicit expansion shows at most five additional cards');
  assert(context.missingNumbers,'An uncalibrated score is not displayed as certainty');
  assert.deepEqual(JSON.parse(JSON.stringify(context.afterNegative)),{count:2,excluded:1,removed:true});
  assert.deepEqual(JSON.parse(JSON.stringify(context.reconsidered)),{count:3,excluded:0,decision:'uncertain'});
  assert(context.confirmed.includes('已人工确认')&&context.confirmed.includes('团队核对：已确认同款'));
  assert(context.unavailable.includes('视觉检索尚未完成准备')&&!context.unavailable.includes('当前未找到可推荐的同款候选'));
  assert(scheduled.includes(2000),'Polling occurs while the index is preparing');
  assert(context.readyStatus.includes('本地视觉检索已就绪'));
  console.log('SEARCH_UI_SMOKE_OK: default top3, explicit extra5, no numeric certainty, feedback exclusion/reconsideration, honest engine states');
}
main().catch(error=>{console.error(error);process.exitCode=1});
