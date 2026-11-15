/* Saved-product merging: optional module, independent of catalog rendering. */
export const escapeText = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function canMerge(user) { return Array.isArray(user?.permissions) && user.permissions.includes('products.merge'); }
export function groupListings(products) {
  const groups = new Map();
  for (const product of products || []) for (const listing of product.listings || []) {
    const key = JSON.stringify([listing.platform || '', listing.store || '']);
    if (!groups.has(key)) groups.set(key, {platform:listing.platform || '', store:listing.store || '', listings:[]});
    groups.get(key).listings.push({...listing, source_code:product.code || ''});
  }
  return [...groups.values()];
}
export function safeImage(value) { return /^\/media\/[a-f0-9]{32}(?:\?thumb=1)?$/.test(String(value || '')) ? value : ''; }
export function safeLink(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; }
  catch { return ''; }
}
const states = {listed:'已上架', pending:'待上架', archived:'已下架'};
const e = escapeText;
function image(product) {
  const src = safeImage(product.images?.[0]?.thumb_url || product.images?.[0]?.url);
  return src ? `<img src="${e(src)}" alt="" loading="lazy">` : '<span class="pm-no-image" aria-hidden="true">图</span>';
}
export function choiceMarkup(products, targetId) {
  return (products || []).map(product => `<label class="pm-choice ${product.id === targetId ? 'selected' : ''}"><input type="radio" name="pm-main" value="${e(product.id)}" ${product.id === targetId ? 'checked' : ''}>${image(product)}<span class="pm-choice-description"><strong>${e(product.name || '未命名商品')}</strong><span>${e(product.code)} · ${e(product.owner || '未分配')}</span><span>${e(product.images?.length || 0)} 张图片 · ${e(product.listings?.length || 0)} 个平台 SKU</span><span class="pm-shop-summary">${groupListings([product]).map(group => e(group.platform) + ' / ' + e(group.store || '未填店铺')).join('；') || '未填写平台资料'}</span></span><span class="pm-main-label">${product.id === targetId ? '保留为主商品' : '选为主商品'}</span></label>`).join('');
}
export function previewMarkup(draft) {
  const main = (draft.products || []).find(product => product.id === draft.target_id);
  if (!main) return '<p>主商品已失效，请重新选择。</p>';
  const groups = groupListings(draft.products);
  return `<div class="pm-main-summary">${image(main)}<div><span class="pm-eyebrow">保留的主商品</span><h3>${e(main.name)}</h3><p>${e(main.code)} · ${e(main.owner)}${main.category ? ' · ' + e(main.category) : ''}</p></div></div><div class="pm-counts"><span><b>${e(draft.products.length)}</b> 款合为 1 款</span><span><b>${e(draft.image_count)}</b> 张图片</span><span><b>${e(groups.length)}</b> 组平台／店铺</span><span><b>${e(draft.listing_count)}</b> 个 SKU</span></div><p class="pm-note">${e(draft.metadata_policy)}<br>${e(draft.listing_policy)}</p><div class="pm-group-list">${groups.map(group => `<section class="pm-platform"><h4>${e(group.platform)}<span>${e(group.store || '未填店铺')}</span><small>${e(group.listings.length)} 个 SKU</small></h4><ul>${group.listings.map(row => {const link = safeLink(row.link);return `<li><span><b>${e(row.product_code || '未提供 SKU 编号')}</b>${row.variant_hint ? ' · ' + e(row.variant_hint) : ''}<small>${row.product_number ? 'SKC ' + e(row.product_number) + ' · ' : ''}${e(row.source_code)} · ${e(states[row.status] || row.status || '未确认')}</small></span><span class="pm-sku-price">${row.price ? '原价 ' + e(row.currency) + ' ' + e(row.price) : '原价未填写'}${link ? `<a href="${e(link)}" target="_blank" rel="noopener noreferrer">前端链接 ↗</a>` : ''}</span></li>`;}).join('')}</ul></section>`).join('') || '<p class="pm-note">这些商品尚未填写平台档案，原商品资料将保存在合并记录中。</p>'}</div><p class="pm-note pm-undo-note">合并后可在“最近合并”中撤销；主商品或其 SKU、报价后续有修改时，直接撤销会停止，避免覆盖新资料。</p>`;
}
export function historyMarkup(items) {
  return (items || []).map(item => {
    const main = (item.original_products || []).find(product => product.id === item.target_id);
    const names = (item.original_products || []).map(product => product.code || product.name).join('、');
    return `<article class="pm-history-item"><div><strong>${e(main?.name || '商品合并')}</strong><p>${e(names)}</p><small>${e(String(item.created_at || '').replace('T', ' ').slice(0, 19))} · ${item.undone_at ? '已撤销' : item.can_undo ? '可撤销' : '已有后续修改或无恢复权限'}</small></div>${item.can_undo ? `<button type="button" class="button sm" data-pm-undo="${e(item.id)}">撤销合并</button>` : ''}</article>`;
  }).join('') || '<div class="pm-empty">暂无可查看的最近合并记录。</div>';
}

export async function apiRequest(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const headers = {...options.headers};
  const body = options.body == null ? undefined : JSON.stringify(options.body);
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try { response = await fetch(path, {...options, headers, body, credentials:'same-origin', signal:controller.signal}); }
  catch (error) { throw new Error(error.name === 'AbortError' ? '请求等待超时，请先查看最近合并记录，确认结果后再重试。' : '无法连接本地服务，请稍后重试。'); }
  finally { clearTimeout(timer); }
  let value; try { value = await response.json(); } catch { value = {}; }
  if (!response.ok) { const error = new Error(typeof value.detail === 'string' ? value.detail : `操作未完成（${response.status}）。`); error.status = response.status; throw error; }
  return value;
}

let active = null, dialogRoot = null, lastSelectionHost = null, currentUser = null, authSequence = 0, mountQueued = false;
function selectedIds() { return [...new Set([...document.querySelectorAll('[data-product-select]:checked')].map(input => input.dataset.productSelect).filter(Boolean))]; }
function notify(message, failed = false) {
  const root = document.querySelector('#toast-root');
  if (!root) return;
  const node = document.createElement('div'); node.className = 'toast' + (failed ? ' error' : ''); node.textContent = message;
  root.append(node); setTimeout(() => node.remove(), 5000);
}
function closeDialog() {
  if (active?.busy) return;
  const focus = active?.previousFocus; active = null;
  dialogRoot?.remove(); dialogRoot = null;
  if (focus?.isConnected) focus.focus();
}
function renderDialog() {
  if (!active) return;
  if (!dialogRoot) { dialogRoot = document.createElement('div'); dialogRoot.className = 'pm-backdrop'; document.body.append(dialogRoot); }
  const s = active, busy = s.busy;
  let content, buttons = '';
  if (s.phase === 'loading') content = '<div class="pm-empty" role="status">正在读取商品资料…</div>';
  else if (s.phase === 'select') {
    content = '<p class="pm-intro">确认是同一款实物后再合并。先选择保留哪一款商品的名称、负责人和资料。</p><div class="pm-choices">' + choiceMarkup(s.products, s.targetId) + '</div>';
    buttons = '<button type="button" class="button primary" data-pm-action="preview">预览合并结果</button>';
  } else if (s.phase === 'preview') {
    content = previewMarkup(s.draft);
    buttons = '<button type="button" class="button" data-pm-action="back">重新选择主商品</button><button type="button" class="button primary" data-pm-action="commit">确认合并同款</button>';
  } else if (s.phase === 'done') {
    content = `<div class="pm-done"><span aria-hidden="true">✓</span><h3>已合为一款公司商品</h3><p>${e(s.result.product.name)}</p><p>各平台、店铺的 SKU、链接和报价历史均已保留。</p></div>`;
    buttons = `<button type="button" class="button" data-pm-undo="${e(s.result.merge_id)}">撤销这次合并</button><button type="button" class="button primary" data-pm-action="close">完成</button>`;
  } else {
    content = '<p class="pm-intro">最近合并记录会持久保存。发生后续编辑或报价变化时，会停止直接撤销。</p>' + historyMarkup(s.history);
    buttons = '<button type="button" class="button" data-pm-action="refresh-history">刷新记录</button>';
  }
  dialogRoot.innerHTML = `<section class="pm-dialog" role="dialog" aria-modal="true" aria-labelledby="pm-dialog-title" tabindex="-1"><header><div><h2 id="pm-dialog-title">${s.kind === 'history' ? '最近合并' : '人工合并同款'}</h2><p>一款公司商品，保留多平台与多店铺档案</p></div><button type="button" class="icon-button" data-pm-action="close" aria-label="关闭合并窗口" ${busy ? 'disabled' : ''}>×</button></header><div class="pm-body">${s.error ? `<div class="pm-error" role="alert">${e(s.error)}</div>` : ''}${content}</div><footer><span class="pm-busy" role="status">${busy ? '正在处理，请稍候…' : ''}</span>${buttons}</footer></section>`;
  if (busy) dialogRoot.querySelectorAll('button,input').forEach(node => { node.disabled = true; });
  dialogRoot.querySelector('.pm-dialog')?.focus({preventScroll:true});
}
async function verifyUser() {
  const data = await apiRequest('/api/me');
  if (!canMerge(data.user)) throw new Error('当前账号没有合并商品的权限。');
  currentUser = data.user; return data.user;
}
export async function openMerge(ids = selectedIds()) {
  if (active?.busy) return;
  if (ids.length < 2 || ids.length > 50) { notify('请选择 2 至 50 款需要合并的商品。', true); return; }
  active = {kind:'merge', phase:'loading', previousFocus:document.activeElement, busy:false, ids:[...ids], products:[], targetId:ids[0], error:''};
  const s = active; renderDialog();
  try {
    await verifyUser();
    const products = await Promise.all(s.ids.map(id => apiRequest('/api/products/' + encodeURIComponent(id))));
    if (active !== s) return;
    s.products = products.map(value => value.product || value); s.phase = 'select'; renderDialog();
  } catch (error) { if (active !== s) return; s.error = error.message; s.phase = 'history'; s.history = []; renderDialog(); }
}
async function loadHistory(s) {
  s.busy = true; s.error = ''; renderDialog();
  try { await verifyUser(); const data = await apiRequest('/api/product-merges'); if (active === s) { s.history = data.items || []; s.phase = 'history'; } }
  catch (error) { if (active === s) { s.error = error.message; s.phase = 'history'; } }
  finally { if (active === s) { s.busy = false; renderDialog(); } }
}
async function openHistory() {
  if (active?.busy) return;
  active = {kind:'history', phase:'loading', previousFocus:document.activeElement, busy:false, history:[], error:''};
  await loadHistory(active);
}
async function doPreview(s) {
  if (s.busy) return; s.busy = true; s.error = ''; renderDialog();
  try { const draft = await apiRequest('/api/products/merge/preview', {method:'POST', body:{ids:s.ids, target_id:s.targetId}}); if (active === s) { s.draft = draft; s.phase = 'preview'; } }
  catch (error) { if (active === s) s.error = error.message; }
  finally { if (active === s) { s.busy = false; renderDialog(); } }
}
function changed(detail) { window.dispatchEvent(new CustomEvent('midoo:products-changed', {detail})); }
async function doCommit(s) {
  if (s.busy || s.phase !== 'preview' || !s.draft) return;
  const draft = s.draft; s.busy = true; s.error = ''; renderDialog();
  try {
    const result = await apiRequest('/api/products/merge', {method:'POST', body:{ids:draft.ids, target_id:draft.target_id, expected_token:draft.expected_token}});
    if (active !== s) return;
    s.result = result; s.phase = 'done'; s.draft = null;
    changed({action:'merge', productId:result.product.id, sourceIds:result.merged_ids, mergeId:result.merge_id});
    notify('同款已合并，平台与店铺档案已保留。');
  } catch (error) {
    if (active !== s) return;
    s.error = error.message; s.draft = null; s.phase = 'select';
    // Any changed preview must be fetched again before another write.
  } finally { if (active === s) { s.busy = false; renderDialog(); } }
}
async function doUndo(s, mergeId) {
  if (s.busy) return;
  const item = s.result?.merge_id === mergeId ? {undo_token:s.result.undo_token} : s.history?.find(row => row.id === mergeId);
  if (!item?.undo_token) return;
  s.busy = true; s.error = ''; renderDialog();
  try {
    const result = await apiRequest('/api/product-merges/' + encodeURIComponent(mergeId) + '/undo', {method:'POST', body:{expected_token:item.undo_token}});
    if (active !== s) return;
    changed({action:'undo-merge', productId:result.product.id, restoredIds:result.restored_ids, mergeId});
    notify('已撤销合并，商品及各自档案已恢复。');
    s.result = null; s.kind = 'history'; s.phase = 'history';
    const data = await apiRequest('/api/product-merges'); if (active === s) s.history = data.items || [];
  } catch (error) { if (active === s) s.error = error.message; }
  finally { if (active === s) { s.busy = false; renderDialog(); } }
}
function queueMount() { if (mountQueued) return; mountQueued = true; queueMicrotask(() => { mountQueued = false; mount(); }); }
function mount() {
  const host = document.querySelector('#library-selection');
  if (!host) { if (active && !active.busy) closeDialog(); if (lastSelectionHost) { lastSelectionHost = null; currentUser = null; authSequence++; } return; }
  if (host !== lastSelectionHost) {
    lastSelectionHost = host; currentUser = null;
    const sequence = ++authSequence;
    apiRequest('/api/me').then(data => { if (sequence === authSequence && host === lastSelectionHost) { currentUser = data.user; queueMount(); } }).catch(() => {});
  }
  const previous = host.querySelector('.pm-library-actions');
  if (!canMerge(currentUser)) { previous?.remove(); return; }
  const bar = host.querySelector('.selection-bar') || host;
  let tools = previous;
  if (!tools || tools.parentNode !== bar) { previous?.remove(); tools = document.createElement('div'); tools.className = 'pm-library-actions'; bar.append(tools); }
  const count = selectedIds().length;
  const button = tools.querySelector('[data-pm-open]');
  if (count >= 2 && !button) { const node = document.createElement('button'); node.type = 'button'; node.className = 'button sm'; node.dataset.pmOpen = ''; node.textContent = '合并同款'; tools.prepend(node); }
  else if (count < 2 && button) button.remove();
  if (!tools.querySelector('[data-pm-history]')) { const node = document.createElement('button'); node.type = 'button'; node.className = 'text-button tiny'; node.dataset.pmHistory = ''; node.textContent = '最近合并'; tools.append(node); }
}
if (typeof document !== 'undefined') {
  document.addEventListener('click', event => {
    if (event.target.closest('[data-pm-open]')) { event.preventDefault(); openMerge(); return; }
    if (event.target.closest('[data-pm-history]')) { event.preventDefault(); openHistory(); return; }
    if (!active || !dialogRoot?.contains(event.target)) return;
    if (event.target === dialogRoot) { closeDialog(); return; }
    const action = event.target.closest('[data-pm-action]')?.dataset.pmAction;
    if (action === 'close') closeDialog();
    if (active?.busy) return;
    if (action === 'preview') doPreview(active);
    if (action === 'back') { active.phase = 'select'; active.draft = null; renderDialog(); }
    if (action === 'commit') doCommit(active);
    if (action === 'refresh-history') loadHistory(active);
    const mergeId = event.target.closest('[data-pm-undo]')?.dataset.pmUndo;
    if (mergeId) doUndo(active, mergeId);
  });
  document.addEventListener('change', event => {
    if (event.target.matches('[data-product-select],#select-all-products')) queueMount();
    if (event.target.matches('input[name="pm-main"]') && active && !active.busy && active.phase === 'select') { active.targetId = event.target.value; renderDialog(); }
  });
  document.addEventListener('keydown', event => {
    if (!active || !dialogRoot) return;
    if (event.key === 'Escape') { event.preventDefault(); closeDialog(); }
    if (event.key !== 'Tab') return;
    const focusable = [...dialogRoot.querySelectorAll('button:not([disabled]),input:not([disabled]),a[href]')];
    if (!focusable.length) { event.preventDefault(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !focusable.includes(document.activeElement))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || !focusable.includes(document.activeElement))) { event.preventDefault(); first.focus(); }
  });
  window.addEventListener('hashchange', () => { currentUser = null; lastSelectionHost = null; authSequence++; queueMount(); });
  window.addEventListener('midoo:permissions-changed', () => { currentUser = null; lastSelectionHost = null; authSequence++; closeDialog(); queueMount(); });
  new MutationObserver(queueMount).observe(document.body, {childList:true, subtree:true});
  queueMount();
}
