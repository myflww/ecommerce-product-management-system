/* The popup never sends captures to a server. The user exports a local file. */
(function () {
  'use strict';
  const core=globalThis.MidooCaptureCore, $=id=>document.getElementById(id), key='midoo_public_capture_v1';
  let current=null,pages=[],busy=false,rangeStart=0,selectedCards=new Set();
  function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
  function render(){
    $('count').textContent=String(pages.length);$('pages').replaceChildren();
    for(let i=0;i<pages.length;i++){const li=document.createElement('li'),span=document.createElement('span'),button=document.createElement('button');span.textContent=(pages[i].snapshot.product_title||pages[i].snapshot.title||'店铺页面')+' · '+pages[i].store_name;button.textContent='移除';button.type='button';button.setAttribute('aria-label','移除此页');button.addEventListener('click',()=>run(async()=>{const next=pages.filter((_,j)=>j!==i);await chrome.storage.local.set({[key]:next});pages=next;render();}));li.append(span,button);$('pages').append(li);}
    $('export').disabled=busy||!pages.length;$('clear').disabled=busy||!pages.length;$('read').disabled=busy;$('add').disabled=busy;
  }
  async function run(action){if(busy)return;busy=true;render();try{await action();}catch(error){status(error?.message||'操作失败，请重试。',true);}finally{busy=false;render();}}
  function storeRange(){
    rangeStart=Number($('store-batch').value)||0;$('store-cards').replaceChildren();selectedCards=new Set();
    const links=(current?.snapshot.links||[]).slice(rangeStart,rangeStart+20);
    const recount=()=>$('selected-count').textContent=`已选 ${selectedCards.size} 款；导出并清空后，可选择下一组商品。`;
    for(const link of links){const label=document.createElement('label'),checkbox=document.createElement('input'),span=document.createElement('span'),open=document.createElement('a'),id=core.productID(link.url);checkbox.type='checkbox';checkbox.checked=true;selectedCards.add(id);span.textContent=link.name||'商品 '+id;open.textContent='打开 ↗';open.href=core.productURL(link.url);open.target='_blank';open.rel='noopener noreferrer';checkbox.addEventListener('change',()=>{checkbox.checked?selectedCards.add(id):selectedCards.delete(id);recount();});label.append(checkbox,span,open);$('store-cards').append(label);}
    recount();
  }
  $('store-batch').addEventListener('change',storeRange);
  $('read').addEventListener('click',()=>run(async()=>{
    current=null;$('capture').hidden=true;status('正在读取当前已打开的页面…');
    const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
    if(!tab?.id||!(core.productURL(tab.url)||core.storeURL(tab.url)))throw Error('请在 Temu 商品详情页或店铺页点击此插件，搜索页不能导入。');
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['capture-core.js','collector.js']});
    const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>globalThis.MidooCaptureCollector.capture()});
    const result=results.find(r=>r.frameId===0)?.result;if(!result?.snapshot)throw Error('未取得页面内容，请确认页面已加载，再重试。');
    current=result;$('page-title').textContent=result.snapshot.product_title||result.snapshot.title;
    $('page-note').textContent=result.type==='store'?`店铺页：当前读到 ${result.public_product_count} 款商品，请选择本次要导出的商品。未打开详情的规格与价格需要后续补全。`:'商品详情：只保留页面明确提供的独立规格与报价，缺失部分会在导入时提示。';
    $('store-selection').hidden=result.type!=='store';
    if(result.type==='store'){$('store-batch').replaceChildren();for(let start=0;start<result.snapshot.links.length;start+=20){const option=document.createElement('option');option.value=String(start);option.textContent=`第 ${start+1}–${Math.min(start+20,result.snapshot.links.length)} 款`;$('store-batch').append(option);}$('store-batch').value='0';storeRange();}
    if(result.bounded)$('page-note').textContent+=' 页面内容较多，本次读取已达到上限。';
    $('store-url').value=result.detected_store_url||'';$('store-name').value=result.detected_store_name||'';$('confirmed').checked=false;$('capture').hidden=false;status('读取完成，请核对店铺后加入本批。');
  }));
  $('add').addEventListener('click',()=>run(async()=>{
    if(!current)throw Error('请先读取当前页面。');
    const store=core.storeURL($('store-url').value.trim());if(!store)throw Error('请填写有效的 Temu 店铺链接（含 mall_id）。');
    if(!$('confirmed').checked)throw Error('请先确认当前商品属于这家店铺。');
    const snapshot=current.type==='store'?core.selectStoreCards(current.snapshot,rangeStart,selectedCards):current.snapshot;
    const page={snapshot,store_url:store,store_name:core.text($('store-name').value,160),confirmed_store:true,captured_at:new Date().toISOString()};
    const next=core.queuePage(pages,page);await chrome.storage.local.set({[key]:next});pages=next;current=null;$('capture').hidden=true;status('已加入本批。你可以手动打开下一个商品继续采集，或导出文件。');
  }));
  $('clear').addEventListener('click',()=>run(async()=>{await chrome.storage.local.remove(key);pages=[];status('本批已清空。已下载的文件不受影响。');}));
  $('export').addEventListener('click',()=>run(async()=>{
    if(!pages.length)throw Error('请先加入要采集的页面。');
    const json=JSON.stringify(core.envelope(pages),null,2);if(new TextEncoder().encode(json).length>core.LIMITS.bytes)throw Error('本批文件过大，请移除部分页面后分批导出。');
    const blob=new Blob([json],{type:'application/json;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='midoo-page-capture-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);status('已开始下载采集文件。回到觅同选择此文件，核对后入库；本批暂时保留，确认导入后可清空。');
  }));
  run(async()=>{const stored=(await chrome.storage.local.get(key))[key];pages=Array.isArray(stored)?stored.slice(0,core.LIMITS.pages):[];if(pages.length)status('已恢复本批采集。导出的内容只保存在你的电脑。');});
})();
