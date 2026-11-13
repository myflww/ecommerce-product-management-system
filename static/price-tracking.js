/* Optional price tracking UI. Kept separate from the product/chat workspace. */

const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const downIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
const clockIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
const refreshIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/></svg>';
const BASE = '/api/price-tracking';
let activePanel = null;
let settingsHost = null;
let mountQueued = false;
let requestSequence = 0;
let browserPoll = null;
let browserEpoch = 0;
const browserJobs = new Map();
const browserIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/></svg>';
const BROWSER_TERMINAL = new Set(['closed','error']);

export function priceRatio(row) {
  const original = Number(row.original_price);
  const current = Number(row.current_price);
  if (row.current_price === '' || row.current_price == null || !Number.isFinite(original) || original <= 0 || !Number.isFinite(current) || current < 0) return null;
  if (!row.currency || !row.current_currency) return null;
  const sameCurrency=String(row.currency).toUpperCase() === String(row.current_currency).toUpperCase();
  if (!sameCurrency && !hasReferenceConversion(row)) return null;
  if (Object.prototype.hasOwnProperty.call(row, 'ratio_percent')) {
    if (row.ratio_percent == null || row.ratio_percent === '') return null;
    const verifiedRatio = Number(row.ratio_percent);
    return Number.isFinite(verifiedRatio) && verifiedRatio >= 0 ? verifiedRatio : null;
  }
  return sameCurrency ? current / original * 100 : null;
}

function hasReferenceConversion(row) {
  const fx=row.fx;
  return Boolean(fx?.available===true && fx.status==='ok' &&
    fx.from_currency===String(row.current_currency||'').toUpperCase() && fx.to_currency===String(row.currency||'').toUpperCase() &&
    Number.isFinite(Number(fx.rate)) && Number(fx.rate)>0 && Number.isFinite(Number(fx.converted_amount)) && Number(fx.converted_amount)>=0 &&
    /^\d{4}-\d{2}-\d{2}$/.test(String(fx.as_of||'')));
}

export function formatPrice(amount, currency) {
  if (amount === '' || amount == null || !Number.isFinite(Number(amount))) return '—';
  const number = Number(amount).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:4});
  return `${String(currency || '').toUpperCase()} ${number}`.trim();
}

export function priceStatus(row) {
  if (hasReferenceConversion(row) && !row.stale && !row.error) return '已按参考汇率换算比较';
  if (row.status === 'currency_mismatch' || (row.current_currency && row.currency && row.current_currency.toUpperCase() !== row.currency.toUpperCase() && !hasReferenceConversion(row))) return '不同币种，等待可用参考汇率';
  if (row.stale || row.status === 'stale') return '本次未更新，保留上次价格';
  if (row.status === 'error' || row.error) return row.current_price != null && row.current_price !== '' ? '本次未更新，保留上次价格' : '尚未采集到有效价格';
  if (row.status === 'unconfigured') return '尚未配置追踪';
  if (row.current_price === '' || row.current_price == null || row.status === 'never') return '等待手动采价';
  return '已获取最新采集价';
}

function dateLabel(value) {
  if (!value) return '尚无记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);
}

function safeSource(value) {
  try {
    const url = new URL(value);
    return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 90000);
  const headers = {...options.headers};
  let body = options.body;
  if (body) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  let response;
  try {
    response = await fetch(BASE + path, {...options, headers, body, credentials:'same-origin', signal:controller.signal});
  } catch (error) {
    throw new Error(error.name === 'AbortError' ? '请求等待超时，请稍后查看采集结果。' : '无法连接本地价格服务，请确认觅同仍在运行。');
  } finally { clearTimeout(timeout); }
  let value;
  try { value = await response.json(); } catch { value = {}; }
  if (!response.ok) {const error=new Error(typeof value.detail === 'string' ? value.detail : response.status === 401 ? '登录已过期，请刷新页面重新登录。' : `价格操作未完成（${response.status}）。`);error.status=response.status;throw error;}
  return value;
}

function banner(message, error = false) {
  const root = document.querySelector('#toast-root');
  if (!root) return;
  const toast = document.createElement('div');
  toast.className = 'toast' + (error ? ' error' : '');
  toast.textContent = message;
  root.append(toast);
  setTimeout(() => toast.remove(), error ? 6500 : 4000);
}

function ratioLabel(row) {
  const ratio = priceRatio(row);
  return ratio == null ? '—' : `${ratio.toLocaleString('zh-CN', {maximumFractionDigits:2})}%`;
}

function referenceNote(row) {
  if(!row.fx)return '';
  const fx=row.fx;
  if(!hasReferenceConversion(row))return `<small class="mpt-fx-note" title="${escapeHTML(fx.message||'参考汇率暂不可用')}">汇率暂不可用</small>`;
  const rate=Number(fx.rate).toLocaleString('zh-CN',{maximumFractionDigits:6});
  const amount=Number(fx.converted_amount).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const basis=`1 ${fx.from_currency} ≈ ${rate} ${fx.to_currency} · ${fx.source_name||'ECB'} ${fx.as_of}`;
  return `<small class="mpt-fx-note" title="${escapeHTML(basis)}">≈ ${escapeHTML(fx.to_currency)} ${escapeHTML(amount)}<span>${escapeHTML(`1 ${fx.from_currency} ≈ ${rate} ${fx.to_currency} · ${fx.as_of.slice(5).replace('-','/')}`)}</span></small>`;
}

export function renderPriceRecord(row, canEdit=false, draft=null, busy=false) {
  const identity=[row.product_code, row.variant_hint].filter(Boolean).join(' · ');
  const source=safeSource(row.source_url);
  const currency=draft?.currency || row.current_currency || row.currency || 'CNY';
  const amount=draft?.price ?? row.current_price ?? '';
  const conflict=Boolean(draft?.expected_context && draft.expected_context!==row.context_token);
  const dirty=Boolean(draft && Object.hasOwn(draft,'price') && (String(draft.price)==='' || Number(draft.price)!==Number(row.current_price) || currency!==(row.current_currency||row.currency)));
  const ratio=priceRatio(row);
  const shownRatio=dirty||conflict?'—':`${hasReferenceConversion(row)&&ratio!=null?'≈':''}${ratioLabel(row)}`;
  const ratioTitle=hasReferenceConversion(row)?`前端价按 ${row.fx.source_name||'ECB'} ${row.fx.as_of} 参考汇率换算为原价币种后比较`:'前端价格 ÷ 原价格 × 100%；跨币种需可用参考汇率';
  const options=[...new Set([currency,row.currency,'CNY','USD','EUR','GBP','CAD','AUD','JPY','HKD'].filter(Boolean))];
  const input=canEdit ? `<form class="mpt-manual-price" data-price-form="manual-price" data-listing-id="${escapeHTML(row.id)}"><select name="currency" aria-label="前端价格币种" ${busy?'disabled':''}>${options.map(value=>`<option value="${escapeHTML(value)}" ${value===currency?'selected':''}>${escapeHTML(value)}</option>`).join('')}</select><input name="price" type="number" min="0.0001" max="999999999.9999" step="0.0001" value="${escapeHTML(amount)}" placeholder="手动输入" required aria-label="前端价格" ${busy?'disabled':''}><button type="submit" title="保存手填前端价" aria-label="保存手填前端价" ${busy||conflict?'disabled':''}>✓</button></form>` : escapeHTML(formatPrice(row.current_price,row.current_currency||row.currency));
  const message=conflict?'SKU 设置已变化，请核对后重新输入价格':draft?.error || (dirty?'待保存':row.error ? '采集未成功，可手填价格' : row.stale ? '未更新，保留上次价格' : row.source==='manual_entry' ? '手动录入' : '');
  return `<tr data-price-record="${escapeHTML(row.id)}"><td><span>${escapeHTML(row.platform||'—')}</span>${identity?`<small class="mpt-sku-label">${escapeHTML(identity)}</small>`:''}<div class="mpt-row-links">${source?`<a href="${escapeHTML(source)}" target="_blank" rel="noopener noreferrer">前端 ↗</a>`:''}${canEdit&&source?`<button type="button" data-price-action="collect-one" data-listing-id="${escapeHTML(row.id)}" ${busy?'disabled':''}>采价</button><button type="button" data-price-action="show-browser" data-listing-id="${escapeHTML(row.id)}" ${busy?'disabled':''}>浏览器</button>`:''}</div></td><td>${escapeHTML(row.store||'—')}</td><td>${escapeHTML(formatPrice(row.original_price,row.currency))}</td><td title="${escapeHTML(priceStatus(row))}">${input}${!dirty&&!conflict?referenceNote(row):''}${message?`<small class="mpt-inline-note" role="status">${escapeHTML(message)}</small>`:''}</td><td title="${escapeHTML(ratioTitle)}">${escapeHTML(shownRatio)}</td></tr>`;
}

export function renderPriceSummary(data, panel=null) {
  if(!data.listings?.length)return '<div class="mpt-empty">请在商品「编辑」中添加平台、SKC 和 SKU。</div>';
  return `<div class="mpt-table-scroll" tabindex="0" role="region" aria-label="平台与各 SKU 价格"><table class="mpt-summary-table"><thead><tr><th scope="col">平台 / SKU</th><th scope="col">店铺</th><th scope="col">原价格</th><th scope="col">前端价格</th><th scope="col" title="前端价格 ÷ 原价格 × 100%">折扣率</th></tr></thead><tbody>${data.listings.map(row=>renderPriceRecord(row,Boolean(data.can_edit),panel?.manualDrafts?.get(row.id),Boolean(panel?.busyIds?.has(row.id)))).join('')}</tbody></table></div>`;
}

function listingFingerprint(row) {
  return JSON.stringify([row.context_token || '',row.platform || '',row.store || '',row.source_url || '', row.region || '', row.variant_hint || '',row.product_code || '',row.product_number || '',String(row.original_price ?? ''),row.currency || '']);
}
function draftChanged(row, draft) {
  return ['source_url','region','variant_hint','product_code','product_number','currency'].some(key => draft[key] !== String(row?.[key] || '')) || draft.original_price !== String(row?.original_price ?? '');
}
function browserContextChanged(row, entry) {
  const draft = activePanel?.drafts.get(row.id);
  return entry && (entry.fingerprint !== listingFingerprint(row) || (draft && draftChanged(row,draft)));
}

export function renderBrowserAssist(row, capabilities, entry) {
  const job=entry?.job, running=Boolean(job&&!BROWSER_TERMINAL.has(job.state));
  const allowed=Boolean(capabilities?.available&&capabilities?.can_use);
  const changed=browserContextChanged(row,entry);
  const candidates=Array.isArray(job?.candidates)?job.candidates.slice(0,12):[];
  const canConfirm=Boolean(!entry?.busy&&!changed&&job?.state==='preview'&&entry?.selection&&entry?.confirmedVariant&&entry?.confirmedBasis);
  const other=[...browserJobs.values()].find(item=>item.job?.listing_id!==row.id&&item.job&&!BROWSER_TERMINAL.has(item.job.state));
  const identity=[row.platform,row.store,row.product_code||row.variant_hint].filter(Boolean).join(' · ');
  return `<div class="mpt-compact-browser-head"><strong>${escapeHTML(identity)}</strong><button type="button" data-price-action="hide-browser" aria-label="收起浏览器采价">×</button></div><div class="mpt-browser-actions">${!running?`<button type="button" class="button sm" data-price-action="browser-start" data-listing-id="${escapeHTML(row.id)}" ${!allowed||!row.source_url||entry?.busy||other?'disabled':''}>打开采价窗口</button>`:`<button type="button" class="button sm" data-price-action="browser-read" data-listing-id="${escapeHTML(row.id)}" ${entry.busy||changed||['starting','reading'].includes(job.state)?'disabled':''}>读取页面价格</button><button type="button" class="button sm" data-price-action="browser-close" data-listing-id="${escapeHTML(row.id)}" ${entry.busy?'disabled':''}>关闭窗口</button>`}${other?`<button type="button" class="button sm" data-price-action="browser-close" data-listing-id="${escapeHTML(other.job.listing_id)}">关闭其他采价窗口</button>`:''}</div>${!running&&allowed?`<button type="button" class="mpt-profile-clear" data-price-action="clear-profile" data-listing-id="${escapeHTML(row.id)}" ${other?'disabled':''}>清除此店铺登录</button>`:''}<p class="mpt-help">${escapeHTML(!allowed ? capabilities?.note||'请在公司主机使用浏览器采价。' : '首次在专用窗口登录并选好 SKU；后续复用该店铺的登录状态。')}</p>${job?.message?`<p class="mpt-inline-note" role="status">${escapeHTML(job.message)}</p>`:''}${changed?'<p class="mpt-form-error">商品规格或链接已变化，请重新打开采价窗口。</p>':''}
    ${job?.state==='preview'&&candidates.length?`<form data-price-form="browser-confirm" data-listing-id="${escapeHTML(row.id)}"><label><span>选择当前 SKU 的售价</span><select name="candidate_id" required ${entry.busy||changed?'disabled':''}><option value="">请选择报价</option>${candidates.map(item=>`<option value="${escapeHTML(item.id)}" ${entry.selection===item.id?'selected':''}>${escapeHTML(formatPrice(item.price,item.currency)+' · '+String(item.evidence||'').slice(0,110))}</option>`).join('')}</select></label><div class="mpt-compact-checks"><label class="mpt-check"><input name="confirmed_variant" type="checkbox" ${entry.confirmedVariant?'checked':''} ${entry.busy||changed?'disabled':''}><span>已核对 SKU / 地区</span></label><label class="mpt-check"><input name="confirmed_price_basis" type="checkbox" ${entry.confirmedBasis?'checked':''} ${entry.busy||changed?'disabled':''}><span>已核对售价口径</span></label></div><button class="button primary sm" type="submit" ${canConfirm?'':'disabled'}>确认记录</button></form>`:''}${entry?.error?`<p class="mpt-form-error" role="alert">${escapeHTML(entry.error)}</p>`:''}`;
}

function stopBrowserPoll() { clearTimeout(browserPoll); browserPoll=null; browserEpoch++; }
function renderBrowserSection(panel,id) {
  if (activePanel !== panel || !panel.root.isConnected) return;
  const row=panel.data?.listings.find(item => item.id===id);
  const section=[...panel.root.querySelectorAll('[data-browser-assist]')].find(item => item.dataset.browserAssist===id);
  if (row && section) {section.innerHTML=renderBrowserAssist(row,panel.capabilities,browserJobs.get(id));positionPanel();}
}
function applyBrowserJob(entry, job) {
  if (!job || typeof job.id !== 'string' || typeof job.state !== 'string') throw new Error('浏览器辅助状态格式不正确，请重新打开。');
  const old=JSON.stringify([entry.job?.state,entry.job?.candidates]);
  const next=JSON.stringify([job.state,job.candidates]);
  if (old!==next) {entry.selection='';entry.confirmedVariant=false;entry.confirmedBasis=false;}
  entry.job=job;
}
function scheduleBrowserPoll(panel) {
  clearTimeout(browserPoll);
  if (!panel.browserListingId) return;
  const entry=[...browserJobs.values()].find(item => panel.data?.listings.some(row => row.id===item.job?.listing_id) && !BROWSER_TERMINAL.has(item.job?.state));
  if (!entry || activePanel!==panel || !panel.root.isConnected) return;
  if (Date.now() > entry.deadline) {entry.error='辅助已超过约 15 分钟，请关闭后重新打开。';renderBrowserSection(panel,entry.job.listing_id);return;}
  const epoch=browserEpoch;
  browserPoll=setTimeout(async()=>{
    if (epoch!==browserEpoch || activePanel!==panel || !panel.root.isConnected) return;
    if (entry.busy) {scheduleBrowserPoll(panel);return;}
    const sequence=++entry.sequence;
    try {
      const job=await request('/browser/'+encodeURIComponent(entry.job.id),{timeout:20000});
      if (epoch!==browserEpoch || sequence!==entry.sequence || activePanel!==panel || browserJobs.get(job.listing_id)!==entry) return;
      const old=JSON.stringify(entry.job);applyBrowserJob(entry,job);entry.error='';
      if (old!==JSON.stringify(job)) renderBrowserSection(panel,job.listing_id);
      if(job.state==='ready'&&entry.autoRead){entry.autoRead=false;await browserAction({dataset:{listingId:job.listing_id}},'read');}
    } catch(error) {
      if (epoch!==browserEpoch || sequence!==entry.sequence || activePanel!==panel) return;
      entry.error=error.message;if([404,410].includes(error.status))entry.job={...entry.job,state:'closed',candidates:[],message:'辅助任务已失效，请重新打开。'};renderBrowserSection(panel,entry.job.listing_id);
      return; // A new explicit read/reopen retries; never loop on auth failures.
    }
    scheduleBrowserPoll(panel);
  },['starting','reading'].includes(entry.job.state)?1500:4000);
}
function savedBrowserRow(panel,id) {
  const row=panel.data?.listings.find(item=>item.id===id);
  if(!row||!panel.data.can_edit)throw new Error('当前没有维护此 SKU 的权限。');
  if(!row.source_url)throw new Error('请先在商品编辑中保存前端链接。');
  return row;
}

async function startBrowser(button) {
  const panel=activePanel,id=button.dataset.listingId;
  if (!panel || browserJobs.get(id)?.busy) return;
  let entry;
  try {
    const row=savedBrowserRow(panel,id);
    if (!panel.capabilities?.available || !panel.capabilities?.can_use) throw new Error(panel.capabilities?.note || '请在运行觅同的主机上使用浏览器辅助。');
    const other=[...browserJobs.values()].find(item=>item.job?.listing_id!==id&&item.job&&!BROWSER_TERMINAL.has(item.job.state));
    if(other){renderBrowserSection(panel,id);throw new Error('另有浏览器辅助窗口，请先关闭现有辅助窗口，再打开此商品。');}
    const previous=browserJobs.get(id);
    if (previous?.job && !BROWSER_TERMINAL.has(previous.job.state)) await request('/browser/'+encodeURIComponent(previous.job.id)+'/close',{method:'POST'});
    if (activePanel!==panel) return;
    entry={job:null,busy:true,error:'',selection:'',confirmedVariant:false,confirmedBasis:false,sequence:0,autoRead:button.dataset.autoRead==='true',fingerprint:listingFingerprint(row),deadline:Date.now()+900000};
    browserJobs.set(id,entry);renderBrowserSection(panel,id);
    const job=await request('/listings/'+encodeURIComponent(id)+'/browser/start',{method:'POST',timeout:35000});
    if (browserJobs.get(id)!==entry) return;
    applyBrowserJob(entry,job);entry.busy=false;
    if (activePanel===panel) {renderBrowserSection(panel,id);scheduleBrowserPoll(panel);}
    if(activePanel===panel&&job.state==='ready'&&entry.autoRead){entry.autoRead=false;await browserAction({dataset:{listingId:id}},'read');}
  }catch(error){
    if (entry) {entry.busy=false;entry.error=error.message;}
    if (activePanel===panel) {if(entry)renderBrowserSection(panel,id);else {panel.message=error.message;productPanelContents(panel,panel.data);}}
    banner(error.message,true);
  }
}
async function browserAction(button,action) {
  const panel=activePanel,id=button.dataset.listingId,entry=browserJobs.get(id);
  if (!panel || !entry?.job || entry.busy) return;
  try {
    if(action==='read') {const row=savedBrowserRow(panel,id);if(browserContextChanged(row,entry))throw new Error('追踪设置已变化，请关闭辅助并重新打开。');}
    entry.busy=true;entry.error='';entry.sequence++;renderBrowserSection(panel,id);
    const job=await request('/browser/'+encodeURIComponent(entry.job.id)+(action==='status'?'':'/'+action),{method:action==='status'?'GET':'POST',timeout:35000});
    if(browserJobs.get(id)!==entry)return;
    applyBrowserJob(entry,job);
  }catch(error){if(browserJobs.get(id)===entry){entry.error=error.message;if([404,410].includes(error.status))entry.job={...entry.job,state:'closed',candidates:[],message:'辅助任务已失效，请重新打开。'};}}
  finally{if(browserJobs.get(id)===entry){entry.busy=false;if(activePanel===panel){renderBrowserSection(panel,id);if(action==='close')panel.data.listings.forEach(row=>renderBrowserSection(panel,row.id));scheduleBrowserPoll(panel);}}}
}
async function confirmBrowser(form) {
  const panel=activePanel,id=form.dataset.listingId,entry=browserJobs.get(id);
  if (!panel || !entry?.job || entry.busy) return;
  try {
    const row=savedBrowserRow(panel,id),data=new FormData(form),candidate=data.get('candidate_id');
    if(browserContextChanged(row,entry))throw new Error('追踪设置已变化，请重新打开辅助并读取页面。');
    if(entry.job.state!=='preview' || !entry.job.candidates?.some(item=>item.id===candidate))throw new Error('请先读取页面，并主动选择对应的售价。');
    if(!data.has('confirmed_variant') || !data.has('confirmed_price_basis'))throw new Error('请先核对规格、地区和价格比较口径，再确认记录。');
    entry.busy=true;entry.error='';entry.sequence++;renderBrowserSection(panel,id);
    const result=await request('/browser/'+encodeURIComponent(entry.job.id)+'/confirm',{method:'POST',body:{candidate_id:candidate,confirmed_variant:true,confirmed_price_basis:true},timeout:35000});
    if(!result.updated)throw new Error(result.message || '该预览没有应用，请重新读取。');
    if(browserJobs.get(id)!==entry)return;
    entry.job={...entry.job,state:'closed',candidates:[],message:result.message || '价格已记录。'};entry.selection='';entry.confirmedVariant=false;entry.confirmedBasis=false;
    if(activePanel===panel){await reloadProduct(panel);document.dispatchEvent(new CustomEvent('midoo:price-tracking-saved',{detail:{productId:panel.productId,listingId:id}}));}
    banner(result.message || '已记录你确认的商品价格。');
  }catch(error){if(browserJobs.get(id)===entry)entry.error=error.message;}
  finally{if(browserJobs.get(id)===entry){entry.busy=false;if(activePanel===panel){renderBrowserSection(panel,id);scheduleBrowserPoll(panel);}}}
}

function closePanel(restoreFocus = false) {
  if (!activePanel) return;
  const {root, button} = activePanel;
  stopBrowserPoll();
  activePanel = null;
  root.remove();
  button.setAttribute('aria-expanded','false');
  if (restoreFocus && button.isConnected) button.focus();
}

function positionPanel() {
  if (!activePanel) return;
  const {root,button} = activePanel;
  if (!button.isConnected) { closePanel(); return; }
  const rect = button.getBoundingClientRect();
  if (rect.bottom < 0 || rect.top > window.innerHeight) { closePanel(); return; }
  const width = Math.min(640, window.innerWidth - 24);
  root.style.width = width + 'px';
  root.style.maxHeight = Math.max(120, window.innerHeight - 24) + 'px';
  const cardLeft = button.closest('.product-card')?.getBoundingClientRect().left ?? rect.left;
  root.style.left = Math.max(12, Math.min(window.innerWidth - width - 12, Math.max(cardLeft, rect.right - width))) + 'px';
  const height = Math.min(root.scrollHeight, window.innerHeight - 24);
  const below = window.innerHeight - rect.bottom - 12;
  root.style.top = (below >= height ? rect.bottom + 7 : Math.max(12, rect.top - height - 7)) + 'px';
}

function productPanelContents(panel,data) {
  panel.data=data;
  if(panel.browserListingId&&!data.listings.some(row=>row.id===panel.browserListingId))panel.browserListingId=null;
  const row=data.listings?.find(item=>item.id===panel.browserListingId);
  panel.root.innerHTML=`<header class="mpt-panel-head"><h2 id="mpt-panel-title">平台价格</h2><div class="mpt-head-actions">${data.can_edit?`<button type="button" class="mpt-collect" data-price-action="collect-product" ${panel.collecting?'disabled':''}>${refreshIcon}${panel.collecting?'正在采价…':'采集价格'}</button>`:''}<button class="mpt-close" type="button" data-price-action="close" aria-label="关闭价格看板">×</button></div></header><div class="mpt-panel-body">${renderPriceSummary(data,panel)}${row?`<section class="mpt-browser mpt-compact-browser" data-browser-assist="${escapeHTML(row.id)}">${renderBrowserAssist(row,panel.capabilities,browserJobs.get(row.id))}</section>`:''}<p class="mpt-product-message" role="status">${escapeHTML(panel.message||'')}</p></div>`;
  positionPanel();scheduleBrowserPoll(panel);
}

async function reloadProduct(panel) {
  const data = await request('/products/' + encodeURIComponent(panel.productId));
  if (activePanel !== panel || !panel.button.isConnected) return;
  productPanelContents(panel, data);
}

async function openProduct(button) {
  if (activePanel?.button === button) { closePanel(true); return; }
  closePanel();
  const root = document.createElement('section');
  root.className = 'mpt-panel mpt-summary';
  root.id = 'midoo-price-panel';
  root.setAttribute('role','region');
  root.setAttribute('aria-label','平台价格');
  root.innerHTML = `<header class="mpt-panel-head"><h2>平台价格</h2><button class="mpt-close" type="button" data-price-action="close" aria-label="关闭价格看板">×</button></header><div class="mpt-loading" role="status"><span class="spinner"></span>正在读取平台价格…</div>`;
  const panel = {root,button,productId:button.dataset.priceProduct,drafts:new Map(),manualDrafts:new Map(),busyIds:new Set(),browserListingId:null,collecting:false,message:'',data:null,capabilities:null};
  activePanel = panel;
  button.setAttribute('aria-expanded','true');
  document.body.append(root);
  positionPanel();
  try {
    const capability=request('/browser/capabilities').then(data=>{panel.capabilities=data;}).catch(error=>{panel.capabilities={available:false,can_use:null,note:error.message};});
    await Promise.all([reloadProduct(panel),capability]);
    if(activePanel===panel && panel.data)productPanelContents(panel,panel.data);
  }
  catch (error) {
    if (activePanel !== panel) return;
    const loading=root.querySelector('.mpt-loading');
    if (loading) loading.innerHTML = `<div class="mpt-error">${escapeHTML(error.message)}</div><button class="button sm" type="button" data-price-action="retry-product">重新读取</button>`;
    positionPanel();
  }
}

function listingDraft(form) {
  const data = new FormData(form);
  return {product_code:String(data.get('product_code') || '').trim(),product_number:String(data.get('product_number') || '').trim(),currency:String(data.get('currency') || '').trim().toUpperCase(),original_price:String(data.get('original_price') || '').trim(),source_url:String(data.get('source_url') || '').trim(),region:String(data.get('region') || '').trim(),variant_hint:String(data.get('variant_hint') || '').trim()};
}

function formError(form, message) {
  const output = form?.querySelector('.mpt-form-error');
  if (output) output.textContent = message;
}

function setFormBusy(form, busy) {
  for (const input of form.querySelectorAll('input,textarea,button')) {
    if (busy) { input.dataset.priceWasDisabled = String(input.disabled); input.disabled = true; }
    else { input.disabled = input.dataset.priceWasDisabled === 'true'; delete input.dataset.priceWasDisabled; }
  }
  form.setAttribute('aria-busy',String(busy));
}

async function collectPrices(ids) {
  const panel=activePanel;
  if(!panel?.data?.can_edit||panel.collecting)return;
  const rows=panel.data.listings.filter(row=>ids.includes(row.id)&&row.source_url);
  if(!rows.length){panel.message='请先在商品编辑中填写前端链接。';productPanelContents(panel,panel.data);return;}
  panel.collecting=true;let success=0,firstFailed=null;
  try {
    for(let index=0;index<rows.length;index++){
      if(activePanel!==panel)break;
      const row=rows[index];panel.busyIds.add(row.id);panel.message=`正在采集 ${index+1} / ${rows.length}`;
      if(activePanel===panel)productPanelContents(panel,panel.data);
      try{
        const result=await request('/listings/'+encodeURIComponent(row.id)+'/refresh',{method:'POST',timeout:160000});
        if(result.updated){success++;const draft=panel.manualDrafts.get(row.id);if(draft){delete draft.error;if(!Object.hasOwn(draft,'price'))panel.manualDrafts.delete(row.id);}}
        else firstFailed ||= row.id;
      }catch(error){firstFailed ||= row.id;const draft=panel.manualDrafts.get(row.id)||{expected_context:row.context_token};panel.manualDrafts.set(row.id,{...draft,error:error.message});}
      finally{panel.busyIds.delete(row.id);}
      if(activePanel===panel)await reloadProduct(panel);
    }
    panel.message=`已采集 ${success} / ${rows.length} 个 SKU${success<rows.length?'；未成功的可手填或点击「浏览器」。':''}`;
    if(firstFailed&&activePanel===panel){
      await showBrowser(firstFailed);
      if(panel.capabilities?.available&&panel.capabilities?.can_use){
        const entry=browserJobs.get(firstFailed);
        if(entry?.job&&!BROWSER_TERMINAL.has(entry.job.state))await browserAction({dataset:{listingId:firstFailed}},'read');
        else await startBrowser({dataset:{listingId:firstFailed,autoRead:'true'}});
      }
    }
  }catch(error){panel.message=error.message;}
  finally{panel.collecting=false;if(activePanel===panel)productPanelContents(panel,panel.data);}
}

async function saveManualPrice(form) {
  const panel=activePanel,id=form.dataset.listingId;
  if(!panel?.data?.can_edit||panel.busyIds.has(id))return;
  const row=panel.data.listings.find(item=>item.id===id);if(!row)return;
  const values=new FormData(form),price=String(values.get('price')||'').trim(),currency=String(values.get('currency')||'').toUpperCase();
  if(!price||!Number.isFinite(Number(price))||Number(price)<=0)return;
  const expected=panel.manualDrafts.get(id)?.expected_context || row.context_token;
  if(expected!==row.context_token){panel.message='SKU 设置已变化，请核对后重新输入价格。';productPanelContents(panel,panel.data);return;}
  panel.manualDrafts.set(id,{price,currency,expected_context:expected});panel.busyIds.add(id);productPanelContents(panel,panel.data);
  try{
    await request('/listings/'+encodeURIComponent(id)+'/manual-price',{method:'POST',body:{price,currency,expected_context:expected}});
    panel.manualDrafts.delete(id);
    panel.message='已保存手填前端价格';
    if(activePanel===panel)await reloadProduct(panel);
    document.dispatchEvent(new CustomEvent('midoo:price-tracking-saved',{detail:{productId:panel.productId,listingId:id}}));
    banner('前端价格已保存');
  }catch(error){panel.manualDrafts.set(id,{price,currency,expected_context:expected,error:error.message});}
  finally{panel.busyIds.delete(id);if(activePanel===panel)productPanelContents(panel,panel.data);}
}

async function showBrowser(id) {
  const panel=activePanel;if(!panel?.data?.can_edit)return;
  panel.browserListingId=id;
  if(!panel.capabilities){try{panel.capabilities=await request('/browser/capabilities');}catch(error){panel.capabilities={note:error.message};}}
  if(activePanel===panel)productPanelContents(panel,panel.data);
}

function rememberManualDraft(form) {
  const panel=activePanel,row=panel?.data?.listings.find(item=>item.id===form.dataset.listingId);
  if(!row)return;
  const data=new FormData(form);
  panel.manualDrafts.set(row.id,{price:String(data.get('price')||''),currency:String(data.get('currency')||''),expected_context:row.context_token});
  const tr=form.closest('tr');
  if(tr){
    tr.querySelectorAll('.mpt-fx-note').forEach(note=>note.remove());
    const ratio=tr.querySelector('td:last-child');if(ratio)ratio.textContent='—';
    let note=tr.querySelector('.mpt-inline-note');
    if(!note){note=document.createElement('small');note.className='mpt-inline-note';form.parentElement.append(note);}
    note.textContent='待保存';
    const save=form.querySelector('[type=submit]');if(save)save.disabled=false;
  }
}

async function clearBrowserProfile(button) {
  const panel=activePanel,id=button.dataset.listingId;
  if(!panel?.data?.can_edit||!window.confirm('清除此店铺的采价登录状态？下次采价时需要重新登录。'))return;
  button.disabled=true;
  try{const result=await request('/listings/'+encodeURIComponent(id)+'/browser/profile/clear',{method:'POST'});banner(result.message||'已清除此店铺的采价登录状态');}
  catch(error){banner(error.message,true);}
  finally{if(button.isConnected)button.disabled=false;}
}

export function renderPriceSettings(data) {
  const admin = Boolean(data.can_edit);
  return `<header class="panel-head"><div class="panel-title">${clockIcon}手动采价</div><span class="badge">仅主动操作</span></header><div class="panel-body"><p class="mpt-settings-intro">价格只在你点击「手动采价」或「浏览器辅助」后读取。保存前端链接不会查询价格或上下架状态，后台不运行定时采价。</p><form data-price-form="settings"><label class="mpt-check"><input type="checkbox" name="allow_ai" ${data.allow_ai ? 'checked' : ''} ${!admin ? 'disabled' : ''}><span>允许已配置 AI 辅助提取手动采价的公开页面文字</span></label><p class="mpt-help">此选项不会发起采价。你主动采价且需要 AI 辅助时，必要页面文字才会发送至已配置服务并计入调用额度；无法确认 SKU 时不会猜测。</p><div class="mpt-form-error" role="alert"></div>${admin ? '<div class="mpt-form-actions"><button class="button primary" type="submit">保存手动采价设置</button></div>' : '<p class="mpt-help">AI 提取权限由管理员维护。</p>'}</form></div>`;
}

async function loadSettings(host) {
  const requestId = ++requestSequence;
  try {
    const data = await request('/settings');
    if (!host.isConnected || settingsHost !== host || requestId !== requestSequence) return;
    const editing = host.querySelector('form[data-price-form="settings"]');
    if (editing?.dataset.priceDirty !== 'true') host.innerHTML = renderPriceSettings(data);
  } catch (error) {
    if (host.isConnected && settingsHost === host) host.innerHTML = `<div class="panel-body"><h3>手动采价</h3><p class="mpt-error">${escapeHTML(error.message)}</p><button class="button sm" data-price-action="retry-settings">重新读取</button></div>`;
  }
}

async function saveSettings(form) {
  if (form.getAttribute('aria-busy') === 'true') return;
  const host = form.closest('.mpt-settings');
  const values = new FormData(form);
  setFormBusy(form,true);
  formError(form,'');
  try {
    await request('/settings',{method:'PUT',body:{allow_ai:values.has('allow_ai')}});
    delete form.dataset.priceDirty;
    await loadSettings(host);
    banner('手动采价设置已保存');
  } catch (error) { formError(form,error.message); }
  finally { if (form.isConnected) setFormBusy(form,false); }
}

function mount() {
  mountQueued = false;
  if(!document.querySelector('#content')){stopBrowserPoll();browserJobs.clear();if(activePanel)closePanel();}
  for (const card of document.querySelectorAll('.product-card[data-product-card]')) {
    card.querySelector('.mpt-manage')?.remove();
    if (card.querySelector('.mpt-toggle') || card.querySelector('[data-action="restore-product"]')) continue;
    const button = document.createElement('button');
    button.className = 'mpt-toggle';
    button.type = 'button';
    button.dataset.priceProduct = card.dataset.productCard;
    button.setAttribute('aria-label','展开此商品的平台价格');
    button.setAttribute('aria-expanded','false');
    button.setAttribute('aria-controls','midoo-price-panel');
    button.innerHTML = `价格变化 ${downIcon}`;
    card.append(button);
  }
  if (activePanel && !activePanel.button.isConnected) {
    const replacement = [...document.querySelectorAll('.mpt-toggle')].find(button=>button.dataset.priceProduct === activePanel.productId);
    if (replacement) {
      activePanel.button = replacement;
      replacement.setAttribute('aria-expanded','true');
      const panel = activePanel;
      reloadProduct(panel).catch(error=>{ if (activePanel === panel) banner(error.message,true); });
    } else closePanel();
  }
  const content = document.querySelector('#settings-content');
  if (!content) { settingsHost = null; return; }
  if (content.querySelector('.mpt-settings')) return;
  const host = document.createElement('section');
  host.className = 'panel mpt-settings';
  host.setAttribute('aria-label','手动采价设置');
  host.innerHTML = '<div class="mpt-loading"><span class="spinner"></span>正在读取手动采价设置…</div>';
  content.append(host);
  settingsHost = host;
  loadSettings(host);
}

function queueMount() {
  if (mountQueued) return;
  mountQueued = true;
  requestAnimationFrame(mount);
}

function initialize() {
  mount();
  new MutationObserver(queueMount).observe(document.body,{childList:true,subtree:true});
  document.addEventListener('click',async event=>{
    const toggle = event.target.closest('.mpt-toggle');
    if (toggle) { event.preventDefault(); event.stopPropagation(); await openProduct(toggle); return; }
    const action = event.target.closest('[data-price-action]');
    if (action) {
      event.preventDefault(); event.stopPropagation();
      if (action.dataset.priceAction === 'close') closePanel(true);
      if (action.dataset.priceAction === 'retry-product' && activePanel) {
        try { await reloadProduct(activePanel); } catch (error) { banner(error.message,true); }
      }
      if (action.dataset.priceAction === 'browser-start') await startBrowser(action);
      if (action.dataset.priceAction === 'browser-read') await browserAction(action,'read');
      if (action.dataset.priceAction === 'browser-status') await browserAction(action,'status');
      if (action.dataset.priceAction === 'browser-close') await browserAction(action,'close');
      if (action.dataset.priceAction === 'clear-profile') await clearBrowserProfile(action);
      if (action.dataset.priceAction === 'collect-one') await collectPrices([action.dataset.listingId]);
      if (action.dataset.priceAction === 'collect-product' && activePanel) await collectPrices(activePanel.data.listings.map(row=>row.id));
      if (action.dataset.priceAction === 'show-browser') await showBrowser(action.dataset.listingId);
      if (action.dataset.priceAction === 'hide-browser' && activePanel) {activePanel.browserListingId=null;stopBrowserPoll();productPanelContents(activePanel,activePanel.data);}
        if (action.dataset.priceAction === 'retry-settings' && settingsHost) await loadSettings(settingsHost);
      return;
    }
    if (activePanel && !activePanel.root.contains(event.target)) closePanel();
  },true);
  document.addEventListener('submit',event=>{
    const form = event.target.closest('form[data-price-form]');
    if (!form) return;
    event.preventDefault(); event.stopPropagation();
    if (form.dataset.priceForm === 'settings') saveSettings(form);
    if (form.dataset.priceForm === 'browser-confirm') confirmBrowser(form);
    if (form.dataset.priceForm === 'manual-price') saveManualPrice(form);
  },true);
  document.addEventListener('input',event=>{
    const manual=event.target.closest('form[data-price-form="manual-price"]');
    if(manual&&activePanel)rememberManualDraft(manual);
    const settingsForm = event.target.closest('form[data-price-form="settings"]');
    if (settingsForm) settingsForm.dataset.priceDirty = 'true';
  });
  document.addEventListener('change',event=>{
    const manual=event.target.closest('form[data-price-form="manual-price"]');
    if(manual&&activePanel)rememberManualDraft(manual);
    const form=event.target.closest('form[data-price-form="browser-confirm"]');
    if(!form || !activePanel)return;const id=form.dataset.listingId,entry=browserJobs.get(id);if(!entry || entry.busy)return;
    const data=new FormData(form);entry.selection=String(data.get('candidate_id')||'');entry.confirmedVariant=data.has('confirmed_variant');entry.confirmedBasis=data.has('confirmed_price_basis');
    const row=activePanel.data?.listings.find(item=>item.id===id);
    const confirm=form.querySelector('[type=submit]');if(confirm)confirm.disabled=!(entry.selection&&entry.confirmedVariant&&entry.confirmedBasis) || !row || Boolean(browserContextChanged(row,entry));
    form.querySelectorAll('.mpt-candidate').forEach(label=>label.classList.toggle('selected',Boolean(label.querySelector('input:checked'))));
  });
  document.addEventListener('keydown',event=>{
    if (event.key === 'Escape' && activePanel) { event.preventDefault(); event.stopImmediatePropagation(); closePanel(true); }
  },true);
  document.addEventListener('toggle',event=>{ if (event.target.matches('.mpt-record')) positionPanel(); },true);
  window.addEventListener('hashchange',()=>{stopBrowserPoll();if(activePanel)closePanel();});
  window.addEventListener('pagehide',stopBrowserPoll);
  window.addEventListener('resize',positionPanel);
  window.addEventListener('scroll',positionPanel,true);
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',initialize,{once:true});
  else initialize();
}
