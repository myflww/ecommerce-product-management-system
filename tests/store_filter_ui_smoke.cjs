const assert=require('node:assert/strict');
const {appFixture}=require('./ui_fixture.cjs');const f=appFixture(),{run,element,context,emit,makeElement}=f;
context.calls=[];
run(`toast=()=>{};state.user={id:'owner',role:'owner',name:'老板'};state.route='library';state.stats={platforms:['Temu','Amazon'],platform_stores:[{platform:'Temu',store:'共同店铺'},{platform:'Amazon',store:'共同店铺'},{platform:'Temu',store:'<img onerror=x>'}]};api=async(url,options)=>{calls.push({url,options});return {items:[],total:0};};`);
async function main(){
 let html=run('storeFilterOptions()');assert(html.includes('全部店铺')&&html.includes('Temu · 共同店铺')&&html.includes('Amazon · 共同店铺'));assert(!html.includes('<img')&&html.includes('&lt;img'));
 run(`state.libraryPlatform='Temu';`);html=run('storeFilterOptions()');assert(!html.includes('Amazon')&&html.includes('>共同店铺</option>'));
 run(`state.libraryStore=JSON.stringify(['Temu','共同店铺']);state.libraryCategory='category:柱蜡';state.libraryQuery='圣诞';state.listingStatus='listed';state.libraryStatus='pricing';state.libraryOffset=24;`);await run('loadLibrary()');let params=new URL(context.calls.at(-1).url,'http://local').searchParams;for(const [key,value]of Object.entries({platform:'Temu',store:'共同店铺',category:'柱蜡',q:'圣诞',listing_status:'listed',workflow_state:'pricing',offset:'24'}))assert.equal(params.get(key),value);
 await emit('change',{...makeElement(),id:'library-platform',value:'Amazon'});assert.equal(run('state.libraryStore'),'');assert.equal(run('state.libraryOffset'),0);assert(!new URL(context.calls.at(-1).url,'http://local').searchParams.has('store'));
 await emit('change',{...makeElement(),id:'library-store',value:JSON.stringify(['Temu','共同店铺'])});assert.equal(run('state.libraryPlatform'),'Temu');assert.equal(element('#library-platform').value,'Temu');params=new URL(context.calls.at(-1).url,'http://local').searchParams;assert.equal(params.get('store'),'共同店铺');assert.equal(params.get('category'),'柱蜡');
 await run('renderLibrary()');html=element('#content').innerHTML;assert(html.indexOf('id="library-platform"')<html.indexOf('id="library-store"')&&html.indexOf('id="library-store"')<html.indexOf('id="listing-status"'));
 console.log('STORE_FILTER_UI_SMOKE_OK: full-catalog shops, platform/name ambiguity, combined category/search/workflow/paging, compatible reset and placement');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
