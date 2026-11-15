/* Frontend store collection and explicit, server-owned import previews. */
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const BASE = '/api/store-import';
const busyStates = new Set(['opening','discovering','collecting','closing']);
const warningNames = {
  variants_missing:'尚未读到子产品规格', variant_prices_incomplete:'部分规格缺少独立报价',
  variant_list_unverified:'规格列表完整性待核对', main_image_missing:'未读到主图',
  main_image_download_failed:'主图下载失败', store_membership_unverified:'店铺归属未确认',
  detail_read_failed_or_login_required:'详情读取失败，请检查登录或验证',
  variant_price_conflict:'同一规格报价冲突', variant_limit_reached:'规格数量超出本批上限',
  store_membership_needs_detail_verification:'详情页仍需确认店铺归属'
};
let dialog = null, job = null, draft = null, timer = null, epoch = 0, working = false;
let choices = new Map(), candidates = new Set(), productOptions = new Map();
let currentUser = null;
let queries = new Map(), listedConfirmation = false, opening = false, pollVersion = 0;

async function api(path, options={}) {
  const response = await fetch(path, {credentials:'same-origin', ...options,
    headers:{...(options.body?{'Content-Type':'application/json'}:{}),...options.headers},
    ...(options.body?{body:JSON.stringify(options.body)}:{})});
  const body = await response.json().catch(()=>({}));
  if (!response.ok) {const error=new Error(typeof body.detail==='string'?body.detail:'操作失败，请刷新后重试。');error.status=response.status;throw error;}
  return body;
}
function note(message, error=false) {
  const area = dialog?.querySelector('[data-si-notice]');
  if(area){area.textContent=message;area.classList.toggle('error',error);}
}
function allowed(key) {
  return Array.isArray(currentUser?.permissions)&&currentUser.permissions.includes(key);
}
export function safeProductLink(value) {
  try {const u=new URL(value);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}
}
function warnings(item) {
  const list=(item.warnings||[]).slice(0,5).map(w=>warningNames[w]||w);
  return list.length?`<p class="si-warnings">${list.map(esc).join('；')}</p>`:'';
}
function statusLine() {
  return `<div class="si-status" role="status"><span>${esc(job?.message||'填写自有店铺的前端链接，先读取预览，再决定入库。')}</span>${busyStates.has(job?.state)?'<span class="spinner"></span>':''}</div>`;
}
function candidateList() {
  const items=job?.candidates||[];
  if(!items.length)return '';
  return `<section class="si-candidates"><div class="between"><strong>本批候选 ${items.length} 款</strong><label><input data-si-all type="checkbox" ${items.every(i=>candidates.has(i.product_id))?'checked':''}> 全选本批</label></div><div class="si-candidate-grid">${items.map(i=>`<label class="si-candidate"><input type="checkbox" data-si-candidate="${esc(i.product_id)}" ${candidates.has(i.product_id)?'checked':''}><span><b>${esc(i.name||'商品 '+i.product_id)}</b><small>公开商品编号 ${esc(i.product_id)}</small></span></label>`).join('')}</div><button class="button primary" data-si="collect" ${!candidates.size||working||!['discovered','attention','preview'].includes(job.state)?'disabled':''}>${job.state==='attention'?'继续读取详情':'读取选中商品的详情与规格'}（${candidates.size}）</button></section>`;
}
function previewCard(item) {
  const c=choices.get(item.id)||{action:'skip'};
  const link=safeProductLink(item.url);
  const target=item.existing_product;
  const selected=productOptions.get(item.id)||[];
  return `<article class="si-preview-card"><div class="si-product-top">${item.has_image?`<img src="${BASE}/previews/${encodeURIComponent(draft.id)}/images/${encodeURIComponent(item.id)}" alt="${esc(item.title)}">`:'<span class="si-no-image">主图缺失</span>'}<div><h3>${esc(item.title)}</h3><p>${esc(item.platform)} · ${esc(item.store_name)} · 店铺 ${esc(item.store_id)}</p><small>公开商品编号 ${esc(item.source_id)}</small>${link?`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">打开前端商品 ↗</a>`:''}</div></div><table><thead><tr><th>子产品 / 规格</th><th>前端价格</th></tr></thead><tbody>${item.variants.map(v=>`<tr><td>${esc(v.spec||'未注明规格')}${v.id?`<small>公开变体 ${esc(v.id)}</small>`:''}</td><td>${Number(v.price)>0?esc(v.currency)+' '+esc(v.price):'未采到独立报价'}</td></tr>`).join('')}</tbody></table>${warnings(item)}<label class="si-choice">入库方式<select data-si-choice="${esc(item.id)}" ${!item.can_import?'disabled':''}><option value="new" ${!allowed('products.create')||target?'disabled':''} ${c.action==='new'?'selected':''}>新建公司商品</option><option value="merge" ${c.action==='merge'?'selected':''}>并入已有商品</option><option value="skip" ${c.action==='skip'?'selected':''}>本次跳过</option></select></label>${c.action==='merge'?target?`<p class="field-note">此来源已关联 ${esc(target.code)} · ${esc(target.name)}；重复规格会跳过，需要调整归属时请使用商品合并。</p>`:`<div class="si-target"><label>查找已有商品<input type="search" data-si-query="${esc(item.id)}" value="${esc(queries.get(item.id)||'')}" placeholder="名称或公司商品编号"></label><button class="button sm" data-si="search" data-item="${esc(item.id)}">查找</button><select data-si-target="${esc(item.id)}" aria-label="选择并入的公司商品"><option value="">请选择同款商品</option>${selected.map(p=>`<option value="${esc(p.id)}" ${c.product_id===p.id?'selected':''}>${esc(p.code)} · ${esc(p.name)}</option>`).join('')}</select>${c.product_id?'<small>平台、店铺、规格和链接将追加到所选商品；同来源重复项会跳过。</small>':''}</div>`:''}</article>`;
}
function previewList() {
  if(!draft)return '';
  if(draft.committed)return `<div class="si-complete"><h3>本批已入库</h3><p>新增 ${Number(draft.result?.created||0)} 款，关联 ${Number(draft.result?.merged||0)} 款，新增 ${Number(draft.result?.new_listings||0)} 条平台规格档案。</p><button class="button" data-si="next" ${!job||['closed','error'].includes(job.state)?'disabled':''}>继续下一批</button></div>`;
  return `<section class="si-preview"><h3>核对后入库 · ${draft.items.length} 款</h3><p class="field-note">前端报价单独保存，原价格可后续填写。同款请手动选“并入已有商品”；重复采集同一来源不会重复新建。</p><div class="si-preview-grid">${draft.items.map(previewCard).join('')}</div><label class="si-listing-confirm"><input type="checkbox" data-si-listed ${listedConfirmation?'checked':''}> 我已核对本批新增规格，人工确认它们已上架</label><p class="field-note">未勾选时保存为待确认；已有档案的状态、人工原价和手填报价保留。</p><div class="si-preview-actions"><button class="button primary" data-si="commit" ${working?'disabled':''}>${working?'正在保存…':'确认导入本批'}</button><button class="button" data-si="discard" ${working?'disabled':''}>放弃本批预览</button></div></section>`;
}
function render() {
  if(!dialog)return;
  const work=dialog.querySelector('[data-si-work]');
  work.innerHTML=statusLine()+(!draft?candidateList():'')+previewList()+
    (job?.items?.some(i=>!i.importable)?`<section class="si-failed"><h3>待补全 ${job.items.filter(i=>!i.importable).length} 款</h3>${job.items.filter(i=>!i.importable).map(i=>`<div><b>${esc(i.name||i.product_id)}</b>${warnings(i)}</div>`).join('')}</section>`:'');
  dialog.querySelector('[data-si="read"]').disabled=working||Boolean(draft&&!draft.committed)||!job||busyStates.has(job.state)||['closed','error'].includes(job.state);
  dialog.querySelector('[data-si="start"]').disabled=working||Boolean(draft&&!draft.committed)||Boolean(job&&!['closed','error'].includes(job.state));
  dialog.querySelector('[data-si="stop"]').disabled=!job||['closed','error'].includes(job.state);
}
async function poll() {
  clearTimeout(timer);
  if(!dialog||!job||working)return;
  const e=epoch,id=job.id,v=pollVersion;
  try {
    const next=await api(`${BASE}/browser/${encodeURIComponent(id)}`);
    if(e!==epoch||v!==pollVersion||!dialog||job?.id!==id)return;
    let changed=next.state!==job.state||next.progress!==job.progress||next.draft_id!==job.draft_id;
    const newlyDiscovered=next.state==='discovered'&&job.state!=='discovered';
    job=next;
    if(newlyDiscovered)candidates=new Set(job.candidates.map(i=>i.product_id));
    if(job.draft_id&&draft?.id!==job.draft_id) {
      const draftId=job.draft_id;
      const fetched=await api(`${BASE}/previews/${encodeURIComponent(draftId)}`);
      if(e!==epoch||v!==pollVersion||!dialog||job?.id!==id||job.draft_id!==draftId)return;
      draft=fetched;queries.clear();listedConfirmation=false;
      changed=true;
      choices=new Map(draft.items.map(i=>[i.id,i.existing_product?
        {action:i.can_import?'merge':'skip',product_id:i.existing_product.id,expected_updated_at:i.existing_product.updated_at}:
        {action:i.can_import&&allowed('products.create')?'new':'skip'}]));
      productOptions=new Map(draft.items.filter(i=>i.existing_product).map(i=>[i.id,[i.existing_product]]));
    }
    if(changed)render();
  } catch(error){
    if(e!==epoch||v!==pollVersion||!dialog)return;
    if(error.status===404){job={...job,state:'error',message:'采集窗口已过期，请重新打开专用浏览器。'};render();}
    note(error.message,true);
  }
  if(e===epoch&&v===pollVersion&&!working&&dialog&&job&&!['closed','error'].includes(job.state))timer=setTimeout(poll,1200);
}
function dismiss() {
  clearTimeout(timer);epoch++;dialog?.remove();dialog=null;working=false;opening=false;
}
async function open() {
  if(dialog||opening)return;
  const e=epoch;opening=true;
  let me;
  try {me=(await api('/api/me')).user;} finally {if(e===epoch)opening=false;}
  if(e!==epoch||dialog)return;
  if(currentUser&&currentUser.id!==me?.id){job=null;draft=null;choices.clear();candidates.clear();productOptions.clear();queries.clear();listedConfirmation=false;}
  currentUser=me;
  if(!allowed('products.import'))throw new Error('当前账号没有批量导入权限。');
  for(const [id,c] of choices)if(c.action==='new'&&!allowed('products.create'))choices.set(id,{action:'skip'});
  dialog=document.createElement('dialog');dialog.className='store-import-dialog';
  dialog.innerHTML=`<header><div><h2>从店铺前端导入 · 试用</h2><p>主图、名称、子产品规格、各自价格与前端链接</p></div><button class="icon-button" data-si="dismiss" aria-label="关闭店铺导入">×</button></header><div class="si-body"><p class="notice amber">此功能仍在验证真实 Temu 登录与规格采集，暂不保证能完成采集。若登录后反复回跳，请关闭采集窗口，继续使用图片批量入库，无需反复注册。</p><form data-si-form><label>Temu 店铺链接<input type="url" data-si-url placeholder="https://www.temu.com/mall.html?mall_id=…" required value="${esc(job?.store_url||'')}"></label><button class="button primary" data-si="start">打开专用浏览器</button></form><p class="field-note">首次在专用窗口正常登录。采集只在你点击后运行；每批最多 20 款。登录与验证码由你操作，平台仍可能要求重新验证。</p><div class="si-browser-actions"><button class="button" data-si="read">读取店铺</button><button class="button" data-si="stop">关闭采集窗口</button></div><p data-si-notice role="status"></p><div data-si-work></div></div>`;
  document.body.append(dialog);dialog.showModal();
  dialog.addEventListener('cancel',event=>{event.preventDefault();dismiss();});
  dialog.addEventListener('submit',event=>event.preventDefault());
  render();
  if(draft&&!draft.committed)reconcilePreview(draft.id).catch(error=>{if(dialog)note(error.message,true);});
  poll();
}
function clearDraft() {
  draft=null;choices.clear();productOptions.clear();queries.clear();listedConfirmation=false;
}
async function releaseDraft(id) {
  try {return await api(`${BASE}/previews/${encodeURIComponent(id)}`,{method:'DELETE'});}
  catch(error){if(error.status!==404)throw error;return {ok:true};}
}
async function reconcilePreview(id) {
  // A dialog can be closed while a save is still in flight. Read the current
  // authenticated server draft rather than installing an old response.
  const e=epoch,root=dialog,userId=currentUser?.id;
  if(!root||draft?.id!==id||draft.committed)return;
  const fetched=await api(`${BASE}/previews/${encodeURIComponent(id)}`);
  if(e!==epoch||dialog!==root||currentUser?.id!==userId||draft?.id!==id||!fetched.committed)return;
  draft={...draft,committed:true,result:fetched.result};
  window.dispatchEvent(new CustomEvent('midoo:products-changed'));render();note('已核对服务器结果，本批商品已入库。');
  try {await releaseDraft(id);if(e===epoch&&dialog===root&&job?.draft_id===id)job={...job,draft_id:''};}
  catch(error){if(e===epoch&&dialog===root)note('商品已入库，预览暂未释放；继续下一批时会重试清理。',true);}
}
async function act(action,button) {
  if(action==='dismiss'){dismiss();return;}
  if(working||!dialog)return;
  const e=epoch,root=dialog,userId=currentUser?.id;
  const valid=()=>e===epoch&&dialog===root;
  working=true;button.disabled=true;
  clearTimeout(timer);pollVersion++;
  try {
    if(action==='start') {
      if(draft&&!draft.committed)throw new Error('请先确认导入或明确放弃本批预览，再打开新的采集窗口。');
      if(draft){await releaseDraft(draft.id);if(!valid())return;clearDraft();}
      const opened=await api(`${BASE}/browser/start`,{method:'POST',body:{url:root.querySelector('[data-si-url]').value.trim()}});
      if(!valid()){api(`${BASE}/browser/${encodeURIComponent(opened.id)}/close`,{method:'POST'}).catch(()=>{});return;}
      job=opened;clearDraft();candidates.clear();render();
    }
    if(action==='read'||action==='next') {
      if(draft&&!draft.committed)throw new Error('请先确认导入或放弃本批预览。');
      if(draft){await releaseDraft(draft.id);if(!valid())return;clearDraft();}
      const next=await api(`${BASE}/browser/${encodeURIComponent(job.id)}/discover`,{method:'POST'});
      if(!valid())return;job=next;render();
    }
    if(action==='collect') {const next=await api(`${BASE}/browser/${encodeURIComponent(job.id)}/collect`,{method:'POST',body:{ids:[...candidates]}});if(!valid())return;job=next;render();}
    if(action==='stop') {const next=await api(`${BASE}/browser/${encodeURIComponent(job.id)}/close`,{method:'POST'});if(!valid())return;job=next;render();}
    if(action==='discard') {
      const id=draft?.id;if(!id)return;
      const released=await releaseDraft(id);if(!valid()||draft?.id!==id)return;
      if(job?.draft_id===id)job={...job,draft_id:''};
      if(released?.committed){draft={...draft,committed:true,result:released.result};window.dispatchEvent(new CustomEvent('midoo:products-changed'));render();note('已核对服务器结果，本批此前已入库；预览已释放。');}
      else {clearDraft();render();note(released?.committed===false?'已放弃本批预览，商品库未发生变化。':'已关闭本批预览。');}
    }
    if(action==='search') {
      const id=button.dataset.item;
      const query=[...root.querySelectorAll('[data-si-query]')].find(n=>n.dataset.siQuery===id)?.value.trim()||'';
      queries.set(id,query);
      const result=await api('/api/products?limit=30&q='+encodeURIComponent(query));
      if(!valid())return;
      const prior=choices.get(id)?.product_id;
      const selected=(productOptions.get(id)||[]).find(p=>p.id===prior);
      const options=result.items||[];
      productOptions.set(id,selected&&!options.some(p=>p.id===selected.id)?[selected,...options]:options);render();
    }
    if(action==='commit') {
      if(draft?.committed)return;
      const id=draft.id;
      const selected=draft.items.map(i=>({item_id:i.id,...choices.get(i.id)}));
      if(!selected.some(i=>i.action!=='skip'))throw new Error('请至少选择一款商品入库。');
      if(selected.some(i=>i.action==='merge'&&!i.product_id))throw new Error('请先为“并入已有商品”选择对应的同款。');
      if(!allowed('products.create')&&selected.some(i=>i.action==='new'))throw new Error('当前账号没有创建商品权限，请选择并入已有商品。');
      const marked=root.querySelector('[data-si-listed]').checked;listedConfirmation=marked;
      const result=await api(`${BASE}/previews/${encodeURIComponent(id)}/commit`,{method:'POST',body:{selections:selected,mark_listed:marked}});
      if(!valid()) {
        if(currentUser?.id===userId&&draft?.id===id){if(dialog)reconcilePreview(id).catch(()=>{});}
        else releaseDraft(id).catch(()=>{});
        return;
      }
      draft={...draft,committed:true,result};
      window.dispatchEvent(new CustomEvent('midoo:products-changed'));render();note('本批入库完成。');
      try {await releaseDraft(id);if(valid()&&job?.draft_id===id)job={...job,draft_id:''};}
      catch(error){if(valid())note('商品已入库，预览暂未释放；继续下一批时会重试清理。',true);}
    }
  } catch(error){
    if(valid()){
      note(error.message,true);
      if(action==='commit'&&(!error.status||error.status>=500)&&draft?.id)reconcilePreview(draft.id).catch(()=>{});
    }
  }
  finally {if(valid()){working=false;render();poll();}}
}
document.addEventListener('click',event=>{
  const trigger=event.target.closest('[data-store-import]');
  if(trigger){open().catch(error=>window.alert(error.message));return;}
  const button=event.target.closest('[data-si]');
  if(button&&dialog?.contains(button))act(button.dataset.si,button);
});
document.addEventListener('change',event=>{
  if(!dialog?.contains(event.target))return;
  const el=event.target;
  if(el.matches('[data-si-candidate]')){el.checked?candidates.add(el.dataset.siCandidate):candidates.delete(el.dataset.siCandidate);render();}
  if(el.matches('[data-si-all]')){candidates=new Set(el.checked?job.candidates.map(i=>i.product_id):[]);render();}
  if(el.matches('[data-si-choice]')){
    const item=draft?.items.find(i=>i.id===el.dataset.siChoice);
    if(!item?.can_import)return;
    const target=item.existing_product;
    const action=el.value==='new'&&!allowed('products.create')?'skip':target&&el.value!=='skip'?'merge':el.value;
    choices.set(el.dataset.siChoice,{action,...(target?{product_id:target.id,expected_updated_at:target.updated_at}:{})});render();
  }
  if(el.matches('[data-si-listed]'))listedConfirmation=el.checked;
  if(el.matches('[data-si-target]')) {
    const item=(productOptions.get(el.dataset.siTarget)||[]).find(i=>i.id===el.value);
    choices.set(el.dataset.siTarget,{action:'merge',...(item?{product_id:item.id,expected_updated_at:item.updated_at}:{})});
  }
});
document.addEventListener('input',event=>{
  if(dialog?.contains(event.target)&&event.target.matches('[data-si-query]'))queries.set(event.target.dataset.siQuery,event.target.value);
});
document.addEventListener('midoo:permissions-updated',event=>{
  const user=event.detail?.user;
  if(!currentUser)return;
  if(!user||user.id!==currentUser.id||!Array.isArray(user.permissions)||!user.permissions.includes('products.import')){
    dismiss();currentUser=null;job=null;clearDraft();candidates.clear();return;
  }
  currentUser=user;
  for(const [id,c] of choices)if(c.action==='new'&&!allowed('products.create'))choices.set(id,{action:'skip'});
  render();
});
let mounting=false;
function mount() {
  if(mounting)return;mounting=true;
  queueMicrotask(()=>{
    mounting=false;
    const host=document.querySelector('.import-choices');
    if(host&&!host.querySelector('[data-store-import]')) {
      const button=document.createElement('button');button.type='button';button.className='panel import-choice';button.dataset.storeImport='';
      button.innerHTML='<span class="choice-icon">↧</span><div><h3>从店铺前端导入 · 试用</h3><p>读取主图、名称、规格与前端价格<br>真实平台采集仍在验证</p></div><span>→</span>';
      host.append(button);
    }
  });
}
new MutationObserver(mount).observe(document.body,{childList:true,subtree:true});mount();
