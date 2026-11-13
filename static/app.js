const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const app = $('#app');
const overlayRoot = $('#overlay-root');
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
 search:'<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.3 4.3"/>',
 image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m3 16 5-5 4 4 3-3 6 6"/>',
 layers:'<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
 upload:'<path d="M12 16V3m-4 4 4-4 4 4M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/>',
 folder:'<path d="M3 7a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
 plus:'<path d="M12 5v14M5 12h14"/>',
 arrow:'<path d="M4 12h15m-5-5 5 5-5 5"/>',
 chevron:'<path d="m9 5 7 7-7 7"/>',
 down:'<path d="m6 9 6 6 6-6"/>',
 back:'<path d="M20 12H5m5-5-5 5 5 5"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3ZM20 2v4m-2-2h4"/>',
 settings:'<path d="m9 3-.6 2.3-2 .9-2.1-.7-2 3.5L4 10.7v2.6l-1.7 1.7 2 3.5 2.2-.7 1.9.9L9 21h4l.7-2.3 1.9-.9 2.1.7 2-3.5-1.7-1.7v-2.6L19.7 9l-2-3.5-2.1.7-1.9-.9L13 3Z"/><circle cx="11" cy="12" r="3"/>',
 user:'<circle cx="12" cy="8" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
 users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2m3-10a3 3 0 0 0 0-6m3 16v-2a6 6 0 0 0-3-5.2"/>',
 logout:'<path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4m6-13 5 5-5 5M9 12h11"/>',
 shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
 check:'<path d="m5 12 4 4L19 6"/>',
 close:'<path d="m6 6 12 12M18 6 6 18"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
 help:'<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5m0 3h.01"/>',
 box:'<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V8M12 13v10M7.5 5.5l9 5"/>',
 menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
 edit:'<path d="m16 3 5 5-12 12-6 1 1-6L16 3Zm-2 2 5 5"/>',
 trash:'<path d="M3 6h18M9 6V3h6v3m-10 0 1 15h12l1-15M10 10v7m4-7v7"/>',
 download:'<path d="M12 3v13m-4-4 4 4 4-4M4 17v4h16v-4"/>',
 copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
 server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6m-6 11h6"/>',
 key:'<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-3-3 3-3m-6 0 3-3"/>',
 link:'<path d="m10 7 3-3a5 5 0 0 1 7 7l-3 3m-3 3-3 3a5 5 0 0 1-7-7l3-3m1 6 8-8"/>',
 send:'<path d="m3 3 18 9-18 9 3-9-3-9Zm3 9h15"/>',
 paperclip:'<path d="m8 12 6-6a4 4 0 0 1 6 6l-9 9a6 6 0 0 1-8-8l9-9m-6 11 9-9a2 2 0 0 1 3 3l-9 9a2 2 0 0 1-3-3l8-8"/>',
 file:'<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8m-8 4h6"/>',
 refresh:'<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
 book:'<path d="M3 4h6a4 4 0 0 1 3 2 4 4 0 0 1 3-2h6v16h-6a4 4 0 0 0-3 2 4 4 0 0 0-3-2H3V4Zm9 2v16"/>',
 scan:'<path d="M3 8V3h5m8 0h5v5m0 8v5h-5M8 21H3v-5M2 12h20"/>',
 lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
 eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
 merge:'<path d="M5 3v4c0 3 7 3 7 7m7-11v4c0 3-7 3-7 7m0 7v-7m-4 3 4 4 4-4"/>',
 split:'<path d="M12 21v-7c0-3-7-3-7-7V3m14 0v4c0 4-7 4-7 7M2 6l3-3 3 3m8 0 3-3 3 3"/>',
 more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
 warning:'<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3h.01"/>'
};
const icon = (name, cls='') => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.box}</svg>`;
const statusName = {pending:'准备上架',listed:'已上架',archived:'已下架'};
const workflowName = {unconfigured:'待完善',pricing:'待定价',pending:'准备上架',listed:'有平台在售',archived:'全部已下架',legacy:'历史资料待补'};
const workflowBadge = p => {const key=p.workflow_state==='legacy'?'unconfigured':p.workflow_state||'unconfigured';return `<span class="badge ${esc(key)}"><span class="dot"></span>${esc(workflowName[key]||workflowName.unconfigured)}</span>`;};
const statusBadge = v => `<span class="badge ${esc(v)}"><span class="dot"></span>${esc(statusName[v] || v || '未填写')}</span>`;
const state = {user:null,stats:{},route:'search',queryFiles:[],queryUrls:[],searchResult:null,searching:false,showAdditional:false,engineStatus:null,groups:[],selectedGroups:new Set(),importing:false,importResult:null,importStatus:'listed',products:[],totalProducts:0,libraryQuery:'',libraryStatus:'',libraryOffset:0,settingsTab:'ai',providerId:'deepseek',aiConfig:null,chatHistory:[],chatSending:false,detail:null,detailEditing:false,settingsData:null,libraryRequest:0,routeRequest:0};
let groupCounter = 0, searchDebounce, lastFocus, searchStatusTimer, searchStatusRequest=0;
Object.assign(state,{libraryPlatform:'',listingStatus:'',selectedProducts:new Set(),trashMode:false,titleBusy:new Set(),detailListings:[]});
Object.assign(state,{listingStatusBusy:new Set(),categories:[],categoryUncategorizedCount:0,libraryCategory:''});
Object.assign(state,{activityFilters:{actor_id:'',start_date:'',end_date:''},activityDraft:{actor_id:'',start_date:'',end_date:''},activityOffset:0,activityData:{items:[],total:0,actors:[]},activityLoading:false,activityError:'',activityRequest:0});
Object.assign(state,{chatDraft:'',chatFiles:[],chatUrls:new Set(),chatEfforts:{},chatPending:null,chatError:'',chatGeneration:0,chatProviderKey:'',assistantTab:'chat',chatMode:'auto',chatSearchEnabled:false,chatWebQuery:'',knowledgeQuery:'',knowledgeDeleted:false,knowledgeRequest:0,knowledgeOverlayRequest:0,knowledgeEditor:null,knowledgeBusy:false});
function activeAI(){return state.aiConfig?.providers?.find(p=>p.id===state.aiConfig.active_provider);}
function aiReady(){const p=activeAI();return Boolean(p?.configured&&p.enabled&&p.model?.trim());}
function aiControlsNote(){return aiReady()?`当前服务：${esc(activeAI().label)} · ${activeAI().vision?'已允许图片识别':'文字模式，图片识别需在设置开启'}`:'AI 功能需先由管理员配置 API 服务';}

async function api(path, options={}) {
  const headers = {...options.headers};
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== 'string') { headers['Content-Type']='application/json'; body=JSON.stringify(body); }
  let response;
  try { response = await fetch(path,{...options,body,headers,credentials:'same-origin'}); }
  catch { throw new Error('无法连接本地服务，请确认主机上的觅同仍在运行。'); }
  if (!response.ok) {
    let payload; try { payload=await response.json(); } catch { payload={}; }
    const raw = payload.detail || payload.message;
    let message = typeof raw==='string' ? raw : Array.isArray(raw) ? raw.map(x=>x.msg||'输入有误').join('；') : `请求未完成（${response.status}）`;
    if(response.status===401 && !['/api/login','/api/setup','/api/me'].includes(path)) { state.user=null;resetActivityState(); resetChatState();state.chatProviderKey='';renderAuth(false); message='登录已过期，请重新登录。'; }
    throw new Error(message);
  }
  if (response.status===204) return {};
  return response.headers.get('content-type')?.includes('application/json') ? response.json() : response;
}
function toast(message, error=false) {
  const el=document.createElement('div'); el.className=`toast${error?' error':''}`;
  el.innerHTML=icon(error?'warning':'check')+`<span>${esc(message)}</span>`;
  $('#toast-root').append(el); setTimeout(()=>el.remove(),error?6500:4000);
}
function dateText(date, full=false) {
  if(!date)return '尚无记录';
  const d=new Date(date); if(Number.isNaN(d.getTime())) return String(date);
  return new Intl.DateTimeFormat('zh-CN',full?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{month:'2-digit',day:'2-digit'}).format(d);
}
function safeLink(v){try{const url=new URL(v);return ['http:','https:'].includes(url.protocol)?url.href:'';}catch{return '';}}
function imageUrl(p,thumb=true){const image=p.images?.[0];return image?(thumb?image.thumb_url||image.url:image.url):'';}
function setBusy(button,busy,text){if(!button)return; if(busy){button.dataset.original=button.innerHTML;button.disabled=true;button.innerHTML='<span class="spinner"></span>'+esc(text||'处理中');}else{button.disabled=false;if(button.dataset.original)button.innerHTML=button.dataset.original;}}
function closeOverlay(){state.knowledgeOverlayRequest++;overlayRoot.innerHTML='';document.body.style.overflow='';if(lastFocus?.isConnected)lastFocus.focus();}
function openOverlay(html,modal=false){state.knowledgeOverlayRequest++;lastFocus=document.activeElement;overlayRoot.innerHTML=`<div class="overlay ${modal?'modal-overlay':''}" data-overlay-backdrop>${html}</div>`;document.body.style.overflow='hidden';setTimeout(()=>{const el=$('input:not([type=file]),textarea,button',overlayRoot);el?.focus();},60);}
function confirmDialog(title, copy, onConfirm, destructive=false){
  openOverlay(`<section class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header class="drawer-header"><h2>${esc(title)}</h2><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></header><div class="panel-body confirm-copy">${esc(copy)}</div><footer class="drawer-footer"><span></span><div class="flex gap8"><button class="button" data-action="close-overlay">取消</button><button class="button ${destructive?'danger':'primary'}" id="confirm-action">${destructive?'确认删除':'确认'}</button></div></footer></section>`,true);
  $('#confirm-action').onclick=async e=>{setBusy(e.currentTarget,true);try{await onConfirm();closeOverlay();}catch(error){toast(error.message,true);setBusy($('#confirm-action'),false);}};
}

async function init(){
  try{const boot=await api('/api/bootstrap');if(boot.needs_setup){renderAuth(true);return;}try{const me=await api('/api/me');state.user=me.user||me;}catch{renderAuth(false);return;}renderShell();await navigate(location.hash.slice(1)||'search',false);}
  catch(error){app.innerHTML=`<div class="boot-loading">${icon('warning')}<p>${esc(error.message)}</p><button class="button" data-action="reload">重新连接</button></div>`;}
}
function renderAuth(setup){
  closeOverlay();app.innerHTML=`<div class="auth-shell"><aside class="auth-aside"><div class="brand"><img src="/static/assets/mark.svg" alt="觅同"><div><div class="brand-name">觅同</div><div class="brand-caption">MIDOO WORKSPACE</div></div></div><div class="auth-copy"><div class="eyebrow">A SHARED PERSPECTIVE</div><h1>让每一次选品，<br>都有<span>据可查。</span></h1><p>把分散在不同运营手里的商品资料，<br>汇成公司共享的图片库。上品之前，先来觅同。</p></div><div class="auth-grid-art">${'<span></span>'.repeat(9)}</div><div class="auth-aside-footer">公司共享 · 本地存储 · 以图检索</div></aside><main class="auth-main"><form class="auth-card" id="auth-form" data-setup="${setup}"><div class="eyebrow">${setup?'WELCOME TO MIDOO':'YOUR PRODUCT WORKSPACE'}</div><h2>${setup?'建立你的公司商品库':'欢迎回来'}</h2><p>${setup?'先创建管理员账号。完成后，团队就能在局域网内共用这一份商品库。':'登录觅同，继续整理商品、核对同款。'}</p><div id="auth-error"></div><label><span class="label-text">${setup?'管理员名称':'账号名称'}</span><input name="name" autocomplete="username" placeholder="例如：商品管理员" required minlength="2" maxlength="40"></label><label><span class="label-text">登录密码</span><input name="password" type="password" autocomplete="${setup?'new-password':'current-password'}" placeholder="${setup?'设置至少 8 位密码':'输入你的密码'}" required ${setup?'minlength="8"':''} maxlength="128"></label>${setup?'<label><span class="label-text">确认密码</span><input name="confirm_password" type="password" autocomplete="new-password" placeholder="再次输入密码" required minlength="8" maxlength="128"></label>':''}<button class="button primary" type="submit">${setup?'创建商品库':'登录工作台'}${icon('arrow')}</button><p class="auth-note">${icon('lock','small')} 资料保存在公司主机，基础检索无需云端密钥</p></form></main></div>`;
}
function renderShell(){
  const nav=[['search','scan','以图查重'],['library','layers','公司商品库'],['import','upload','批量入库'],['activity','clock','最近动态'],['ai','sparkles','AI 管家']];
  app.innerHTML=`<div class="layout"><div class="mobile-nav-backdrop" data-action="toggle-menu"></div><aside class="sidebar" id="sidebar"><div class="brand"><img src="/static/assets/mark.svg" alt="觅同"><div><div class="brand-name">觅同</div><div class="brand-caption">MIDOO WORKSPACE</div></div></div><div class="workspace-label">商品工作台</div><nav class="nav-group" aria-label="主导航">${nav.map(([route,i,label])=>`<button class="nav-item" data-route="${route}">${icon(i)}<span>${label}</span>${route==='library'?'<span class="nav-count" id="nav-library-count">0</span>':''}${route==='ai'?'<span class="nav-count">AI</span>':''}</button>`).join('')}</nav><div class="workspace-label" style="margin-top:29px">工作空间</div><nav class="nav-group"><button class="nav-item" data-route="settings">${icon('settings')}<span>设置与备份</span></button></nav><div class="sidebar-guide"><div class="sidebar-guide-title">${icon('shield')} 上品前，先查一下</div>把正在准备的商品也登记进库，<br>让团队少一次重复选品。</div><div class="sidebar-bottom"><div class="server-status"><span class="dot"></span> 本地共享服务已连接</div><div class="profile"><span class="avatar">${esc(state.user.name?.slice(0,1)||'管')}</span><div style="min-width:0"><div class="name truncate">${esc(state.user.name)}</div><div class="role">${state.user.role==='admin'?'工作空间管理员':'团队成员'}</div></div><button class="icon-button" data-action="logout" title="退出登录" aria-label="退出登录">${icon('logout')}</button></div></div></aside><main class="main-shell"><header class="topbar"><div class="flex gap8"><button class="icon-button mobile-menu" data-action="toggle-menu" aria-label="展开菜单">${icon('menu')}</button><div class="breadcrumb">${icon('box')}<span>公司工作空间</span>${icon('chevron')}<strong id="breadcrumb-page">以图查重</strong></div></div><div class="topbar-right"><span class="date-label">${new Intl.DateTimeFormat('zh-CN',{month:'long',day:'numeric',weekday:'long'}).format(new Date())}</span><span class="topbar-divider"></span><span class="local-badge"><span class="dot"></span>局域网共享</span></div></header><div class="content" id="content"></div></main></div>`;
}
function titleBlock(title,desc,actions='',eyebrow='PRODUCT WORKSPACE'){return `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${desc}</p></div>${actions?`<div class="page-head-actions">${actions}</div>`:''}</div>`;}
function footer(){return '<div class="footer-note">觅同 · 公司商品库<span>·</span>让团队的商品经验，成为共享资产</div>';}
function loading(){return '<div class="loading-content"><span class="spinner"></span>正在读取商品库…</div>';}
function categoryField(value=''){return `<label><span class="label-text">商品分类（选填）</span><input name="category" value="${esc(value)}" maxlength="60" list="product-category-options" placeholder="输入分类，例如：柱蜡、蜡片、水晶、模具蜡"><datalist id="product-category-options">${state.categories.map(c=>`<option value="${esc(c.name)}"></option>`).join('')}</datalist><span class="field-note">可选择已有分类，也可直接输入新分类；留空表示未分类。</span></label>`;}
function categoryFilterOptions(){const items=[...state.categories];if(state.libraryCategory.startsWith('category:')&&!items.some(c=>'category:'+c.name===state.libraryCategory))items.push({name:state.libraryCategory.slice(9),count:0});return `<option value="">全部分类</option><option value="uncategorized" ${state.libraryCategory==='uncategorized'?'selected':''}>未分类（${state.categoryUncategorizedCount}）</option>`+items.map(c=>`<option value="${esc('category:'+c.name)}" ${state.libraryCategory==='category:'+c.name?'selected':''}>${esc(c.name)}（${Number(c.count)||0}）</option>`).join('');}
async function refreshCategories(){try{const data=await api('/api/categories');state.categories=data.items||[];state.categoryUncategorizedCount=Number(data.uncategorized_count)||0;const filter=$('#library-category');if(filter)filter.innerHTML=categoryFilterOptions();}catch{}}
async function refreshStats(){await Promise.all([refreshCategories(),(async()=>{try{state.stats=await api('/api/stats');if($('#nav-library-count'))$('#nav-library-count').textContent=state.stats.total_products??0;}catch{}})()]);}
async function navigate(route,changeHash=true){
  if(!state.user)return; route=route.split('?')[0];if(!['search','library','import','activity','ai','settings'].includes(route))route='search';state.route=route;
  clearTimeout(searchStatusTimer);searchStatusRequest++;
  if(changeHash && location.hash.slice(1)!==route)history.replaceState(null,'',`#${route}`);
  const titles={search:'以图查重',library:'公司商品库',import:'批量入库',activity:'最近动态',ai:'AI 管家',settings:'设置与备份'};
  $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.route===route));$('#breadcrumb-page').textContent=titles[route];document.title=`${titles[route]} · 觅同`;$('#sidebar').classList.remove('open');$('.mobile-nav-backdrop')?.classList.remove('show');
  const request=++state.routeRequest;$('#content').innerHTML=loading();await refreshStats();if(request!==state.routeRequest)return;
  try{if(['search','import'].includes(route))state.aiConfig=await api('/api/ai/config');if(request!==state.routeRequest)return;if(route==='search'){renderSearch();loadRecentSnippet();}if(route==='library')await renderLibrary();if(route==='import')renderImport();if(route==='activity')await renderActivity();if(route==='ai')await renderAI();if(route==='settings')await renderSettings();}
  catch(error){if(request===state.routeRequest)$('#content').innerHTML=`<div class="panel empty-state"><div class="empty-icon">${icon('warning')}</div><h3>暂时无法加载</h3><p>${esc(error.message)}</p><button class="button" data-route="${route}">${icon('refresh')}重新加载</button></div>`;}
}

function statStrip(){return `<div class="panel stat-strip"><div class="stat-item"><div class="stat-symbol">${icon('layers')}</div><div><div class="stat-label">公司商品总数</div><div class="stat-value">${Number(state.stats.total_products||0).toLocaleString()}<small>款商品</small></div></div></div><div class="stat-item"><div class="stat-symbol">${icon('image')}</div><div><div class="stat-label">已收录商品图片</div><div class="stat-value">${Number(state.stats.total_images||0).toLocaleString()}<small>张图片</small></div></div></div><div class="stat-item"><div class="stat-symbol">${icon('clock')}</div><div><div class="stat-label">正在准备上架</div><div class="stat-value">${Number(state.stats.pending_products||0).toLocaleString()}<small>款商品</small></div></div></div></div>`;}
function uploadArt(){return `<div class="upload-art" aria-hidden="true"><span class="art-back"></span><span class="art-front">${icon('image')}</span><span class="art-badge">${icon('search')}</span></div>`;}
function renderSearch(){
  $('#content').innerHTML=titleBlock('上品之前，先觅同','拖入商品图片，在公司共享库中找一找，避免团队重复选品。',`<button class="button" data-route="import">${icon('plus')}<span>添加已有商品</span></button>`,'FIND BEFORE YOU LIST')+statStrip()+`<div id="search-engine-status" aria-live="polite">${searchEngineStatus()}</div><div class="search-workspace"><section class="panel search-panel"><div class="panel-head"><div class="panel-title">${icon('scan')}上传待上商品</div><span class="step-tag">IMAGE SEARCH</span></div><div class="dropzone ${state.queryFiles.length?'has-files':''}" id="search-drop" tabindex="0" role="button" aria-label="点击选择或拖入待上商品图片"><div id="query-area">${queryArea()}</div></div><input type="file" id="query-files" accept="image/jpeg,image/png,image/webp,image/bmp,image/gif" multiple></section><aside class="panel guide-panel"><div class="guide-label">一个更顺手的流程</div><h3>让同款，在上品前被发现</h3><div class="flow-step"><span class="step-number">01</span><div><div class="flow-title">放入商品图片</div><div class="flow-caption">主图、细节图，多张一起查询</div></div></div><div class="flow-step"><span class="step-number">02</span><div><div class="flow-title">对照已有商品</div><div class="flow-caption">查看命中图片、规格与负责人</div></div></div><div class="flow-step"><span class="step-number">03</span><div><div class="flow-title">核对，再决定</div><div class="flow-caption">关联同款，或登记新的选品</div></div></div><div class="guide-tip">${icon('info')}检索范围为公司已入库商品，结果需要结合实物规格核对。</div></aside></div><div class="search-tools">${icon('shield')}<span>本地图片检索，无需云端密钥。支持 JPG、PNG、WebP 等常见格式。</span><span class="spacer"></span><button class="text-button tiny" data-route="library">浏览商品库 ${icon('arrow','small')}</button></div><div id="search-results">${state.searchResult?searchResults():''}</div><section class="recent-block"><div class="section-heading"><h2>商品库最近动态</h2><button class="text-button tiny" data-route="activity">查看全部 ${icon('arrow','small')}</button></div><div class="panel" id="recent-snippet"><div class="inline-empty">${icon('clock')}正在读取最近记录…</div></div></section>`+footer();
  refreshSearchStatus();
}
function queryArea(){if(!state.queryFiles.length)return `${uploadArt()}<div class="drop-title">把商品图片拖到这里</div><p class="drop-description">主图、细节图都可以 · 支持多张图片</p><button class="button primary" data-action="choose-query">${icon('plus')}选择商品图片</button><div class="keyboard-hint">也可以直接粘贴截图 <kbd>Ctrl</kbd><span>+</span><kbd>V</kbd></div>`;
  return `<div class="query-tray">${state.queryFiles.map((f,i)=>`<div class="query-thumb"><img src="${state.queryUrls[i]}" alt="${esc(f.name)}"><button data-action="remove-query" data-index="${i}" aria-label="移除${esc(f.name)}">${icon('close')}</button></div>`).join('')}</div><div class="drop-title">${state.queryFiles.length} 张图片，查找同一个商品</div><p class="drop-description">多角度图片有助于找出相似候选</p><div class="query-actions"><button class="button light" data-action="run-ai-search" ${state.searching||!aiReady()||!activeAI()?.vision||!state.aiConfig?.share_library_context?'disabled':''} title="${esc(aiControlsNote())}">${icon('sparkles')}AI 查重</button><button class="button primary" data-action="run-search" ${state.searching?'disabled':''}>${state.searching?'<span class="spinner"></span>正在检索':icon('search')+'开始查重'}</button><button class="button" data-action="choose-query">继续添加</button><button class="button ghost" data-action="clear-query">清空</button></div><p class="ai-search-hint">${!aiReady()?'AI 查重需配置 API 服务':!activeAI()?.vision?'AI 查重需开启图片识别，并选择支持视觉的模型':!state.aiConfig?.share_library_context?'AI 查重需在模型设置中允许商品摘要发送':'AI 查重会将查询图片和最多8款候选的图片、摘要发送给当前模型'} · <button class="text-button tiny" data-action="open-ai-settings">模型设置</button></p>`;
}
function validFiles(files){let list=[...files].filter(f=>/\.(jpe?g|png|webp|bmp|gif|tiff?)$/i.test(f.name)||['image/jpeg','image/png','image/webp','image/bmp','image/gif','image/tiff'].includes(f.type));let skipped=files.length-list.length;if(skipped)toast(`已跳过 ${skipped} 个不支持的文件`);const large=list.filter(f=>f.size>20*1024*1024);if(large.length)toast('单张图片不能超过 20 MB，超出部分已跳过。',true);return list.filter(f=>f.size<=20*1024*1024);}
function addQuery(files){if(state.searching){toast('当前正在检索，请稍候再调整图片。');return;}const list=validFiles(files);if(!list.length)return;const room=5-state.queryFiles.length;if(list.length>room)toast('一次最多使用 5 张图片查询。');for(const f of list.slice(0,Math.max(room,0))){state.queryFiles.push(f);state.queryUrls.push(URL.createObjectURL(f));}state.searchResult=null;if(state.route==='search'){renderSearch();loadRecentSnippet();}}
function clearQuery(){state.queryUrls.forEach(u=>URL.revokeObjectURL(u));state.queryUrls=[];state.queryFiles=[];state.searchResult=null;state.showAdditional=false;}
function searchEngineStatus(){
  const info=state.engineStatus;
  if(!info)return `<div class="search-engine-bar checking">${icon('clock')}<span>正在检查本地视觉检索…</span></div>`;
  const ready=info.state==='ready'||info.ready===true;
  const indexing=info.state==='indexing';
  const indexed=Number(info.indexed_images||0),total=Number(info.total_images||0);
  const title=ready?'本地视觉检索已就绪':indexing?'正在准备商品图片索引':'视觉检索尚未就绪';
  const fallback=indexing?'完成前只能核对原图及已人工确认的记录。':'目前只能核对原图及已人工确认的记录，请检查模型和索引状态。';
  const message=ready?`已准备 ${indexed.toLocaleString()} / ${total.toLocaleString()} 张图片；只推荐少量值得核对的候选。`:(info.message||fallback);
  return `<div class="search-engine-bar ${ready?'ready':indexing?'indexing':'unavailable'}">${indexing?'<span class="spinner"></span>':icon(ready?'shield':'warning')}<div><strong>${title}${indexing&&total?` · ${indexed} / ${total}`:''}</strong><span>${esc(message)}</span>${!ready&&info.message?`<small>${fallback}</small>`:''}</div>${!ready&&!indexing?'<button class="text-button" data-action="refresh-search-status">重新检查</button>':''}${state.user?.role==='admin'&&!indexing?'<button class="text-button tiny" data-action="reindex-search" title="为已有商品重新准备视觉索引">更新图片索引</button>':''}</div>`;
}
function updateSearchEngineStatus(info){
  if(!info)return;
  state.engineStatus=info.engine_status||info.index_status||info;
  const target=$('#search-engine-status');if(target)target.innerHTML=searchEngineStatus();
  clearTimeout(searchStatusTimer);
  if(state.route==='search'&&state.user&&state.engineStatus.state==='indexing')searchStatusTimer=setTimeout(refreshSearchStatus,2000);
}
async function refreshSearchStatus(){
  clearTimeout(searchStatusTimer);const request=++searchStatusRequest;
  try{const info=await api('/api/search/status');if(request!==searchStatusRequest||state.route!=='search'||!state.user)return;updateSearchEngineStatus(info);}
  catch(error){if(request!==searchStatusRequest||state.route!=='search'||!state.user)return;updateSearchEngineStatus({state:'unavailable',ready:false,message:'暂时无法读取检索状态，请确认软件已更新并且主机服务正在运行。'});}
}
async function reindexSearch(button){
  setBusy(button,true,'准备中');
  try{const info=await api('/api/search/reindex',{method:'POST'});if(state.route==='search'){if(info.engine_status||info.index_status||info.state)updateSearchEngineStatus(info);await refreshSearchStatus();}toast('已提交图片索引更新，已有商品和图片会保留。');}
  catch(error){toast(error.message,true);setBusy(button,false);}
}
async function runSearch(useAI=false){
  if(!state.queryFiles.length||state.searching)return;state.searching=true;state.showAdditional=false;$('#query-area').innerHTML=queryArea();
  $('#search-results').innerHTML='<div class="result-section panel loading-content"><span class="spinner"></span>正在查找值得核对的商品…</div>';
  try{
    const form=new FormData();state.queryFiles.forEach(f=>form.append('images',f,f.name));
    const result=await api(useAI?'/api/ai/deduplicate':'/api/search',{method:'POST',body:form});
    result.items=(result.items||[]).slice(0,3);result.additional_items=(result.additional_items||[]).slice(0,5);result.client_excluded=[];
    state.searchResult=result;
    if(state.route==='search'){updateSearchEngineStatus(result.engine_status);$('#search-results').innerHTML=searchResults();}
  }catch(error){toast(error.message,true);if($('#search-results'))$('#search-results').innerHTML=state.searchResult?searchResults():'';}
  finally{state.searching=false;if($('#query-area'))$('#query-area').innerHTML=queryArea();}
}
function searchCandidateCard(r,i,area='main'){
  const p=r.product||r,prior=r.previous_decision;
  const confirmed=prior?.decision==='same'||r.match_type==='feedback'||r.match_type==='confirmed_same';
  const evidence=confirmed?'已人工确认':r.exact?'原图一致':area==='additional'?'较弱候选 · 待核实':r.evidence_label||(r.match_type==='semantic'?'商品外观接近':'候选待核实');
  const decisionLabel={same:'已确认同款',different:'已标记不同商品',uncertain:'待进一步核实'};
  return `<article class="panel result-card ${area==='additional'?'additional-card':''}"><div class="result-images"><div class="compare-image"><img src="${state.queryUrls[r.query_index||0]||state.queryUrls[0]}" alt="待上商品"><span>待上商品</span></div><span class="compare-arrow">${icon('arrow')}</span><button class="compare-image" data-action="detail" data-id="${esc(p.id)}" aria-label="查看${esc(p.name)}"><img src="${esc(r.matched_image_url||imageUrl(p))}" alt="${esc(p.name)}"><span>库内商品</span></button></div><div class="between" style="margin-bottom:7px">${workflowBadge(p)}<span class="match-evidence ${confirmed||r.exact?'confirmed':area==='additional'?'cautious':'semantic'}">${icon(confirmed||r.exact?'check':'eye','small')}${esc(evidence)}</span></div><button class="text-button result-name" data-action="detail" data-id="${esc(p.id)}">${esc(p.name||'未命名商品')}</button><div class="result-owner">${icon('user')}${esc(p.owner||'未分配')}<span style="margin:0 3px;color:#ccd1db">·</span><span>${esc(p.code||'')}</span></div><p class="result-reason">${esc(r.reason||'请对照图片中的商品细节，以及尺寸、材质、套装数量。')}</p>${r.ai_review?aiReviewBlock(r.ai_review):''}${prior?`<div class="prior-decision">${icon('clock')}团队核对：${esc(decisionLabel[prior.decision]||'已核对')} · ${esc(prior.actor||'团队成员')}</div>`:''}<div class="result-footer"><button class="button light" data-action="decision" data-area="${area}" data-index="${i}" data-id="${esc(p.id)}" data-decision="same" ${prior?.decision==='same'?'disabled':''}>${icon('check')}${prior?.decision==='same'?'已确认同款':'确认同款'}</button><button class="button" data-action="decision" data-area="${area}" data-index="${i}" data-id="${esc(p.id)}" data-decision="uncertain" ${prior?.decision==='uncertain'?'disabled':''}>待核实</button><button class="button ghost" data-action="decision" data-area="${area}" data-index="${i}" data-id="${esc(p.id)}" data-decision="different">不是同款</button></div></article>`;
}
function searchResults(){
  const result=state.searchResult;if(!result)return '';
  const items=(result.items||[]).filter(r=>r.previous_decision?.decision!=='different').slice(0,3);
  const additional=(result.additional_items||[]).filter(r=>r.previous_decision?.decision!=='different').slice(0,5);
  const localExcluded=result.client_excluded||[];
  const engine=result.engine_status||state.engineStatus;
  const engineReady=engine?.state==='ready'||engine?.ready===true;
  let main;
  if(items.length){
    main=`<div class="section-heading search-result-heading"><div class="flex gap8"><h2>建议先核对这 ${items.length} 款</h2><span class="badge">精选候选</span></div><span class="result-meta">检索 ${Number(result.total_searched||0).toLocaleString()} 款商品 · ${(Number(result.elapsed_ms||0)/1000).toFixed(2)} 秒</span></div><div class="notice search-result-notice">${icon('info')}只展示有依据的候选；外观接近仍需核对尺寸、材质和套装数量。${result.notice?`<span class="search-result-detail">${esc(result.notice)}</span>`:''}</div><div class="results">${items.map((r,i)=>searchCandidateCard(r,i)).join('')}</div>`;
  }else{
    const empty=!result.total_searched;
    const title=empty?'商品库还没有可供对照的图片':engineReady?'当前未找到可推荐的同款候选':'视觉检索尚未完成准备';
    const description=empty?'先把公司已有商品录入，也可以先登记这次准备上的商品。':engineReady?'弱匹配已收起。可以补充商品主体或细节图再查；未找到候选不代表确定没有同款。':'当前仅核对了原图和人工确认记录。请等待图片索引就绪后再查，避免把未检索到当成没有同款。';
    main=`<div class="panel empty-state search-no-match"><div class="empty-icon">${icon(empty?'layers':engineReady?'scan':'clock')}</div><h3>${title}</h3><p>${description}</p><button class="button primary" data-action="query-to-import">${icon('plus')}登记这个商品</button></div>`;
  }
  const extra=additional.length?`<div class="search-more-candidates"><button class="button ${state.showAdditional?'ghost':''}" data-action="toggle-search-additional" aria-expanded="${state.showAdditional}" aria-controls="additional-search-candidates">${icon(state.showAdditional?'down':'eye')}${state.showAdditional?'收起较弱候选':`查看较弱候选（${additional.length}款）`}</button><span>仅在需要时展开，最多 ${additional.length} 款，不会列出整个商品库。</span></div>${state.showAdditional?`<section id="additional-search-candidates" class="additional-search-section"><div class="notice amber">${icon('warning')}这些候选的匹配依据较弱，仅供补充检查；不要直接认定为同款。</div><div class="results">${additional.map((r,i)=>searchCandidateCard(r,i,'additional')).join('')}</div></section>`:''}`:'';
  const excludedCount=Math.max(Number(result.excluded_count||0),localExcluded.length);
  const excluded=excludedCount?`<div class="search-excluded-summary">${icon('check','small')}已依据人工核对排除 ${excludedCount} 款不同商品。${localExcluded.length?`<div class="search-excluded-list">${localExcluded.map((entry,i)=>`<div><span class="truncate">${esc(entry.item.product?.name||'已排除商品')}</span><button class="text-button tiny" data-action="reconsider-search-item" data-index="${i}">改为待核实</button></div>`).join('')}</div>`:''}</div>`:'';
  const footer=items.length?`<div class="between wrap search-result-bottom"><span class="tiny muted">${Number(result.hidden_count||result.filtered_count||0)>0?'其余较弱匹配已收起。':'商品记录越完整，核对越方便。'}</span><button class="button" data-action="query-to-import">${icon('plus')}仍需登记为新商品</button></div>`:'';
  return `<section class="result-section">${result.ai_summary?`<div class="notice ai-summary">${icon('sparkles')}<span><strong>AI 复核完成 · ${Number(result.reviewed_count||0)} 款候选</strong><br>${esc(result.ai_summary)}<br><small>仅复核本地召回候选，仍需人工确认；未检索到不代表没有重复。</small></span></div>`:''}${main}${extra}${excluded}${footer}</section>`;
}
async function decision(button){
  const {index,decision}=button.dataset,area=button.dataset.area||'main';
  const result=state.searchResult,pool=area==='additional'?result?.additional_items:result?.items;
  const entry=pool?.find(item=>item.product?.id===button.dataset.id)||pool?.[Number(index)];if(!entry)return;
  const id=entry.product?.id||button.dataset.id;
  setBusy(button,true);
  try{
    await api('/api/decisions',{method:'POST',body:{product_id:id,decision,note:'',query_fingerprint:entry.query_fingerprint||''}});
    if(state.searchResult!==result)return;
    entry.previous_decision={decision,actor:state.user.name};
    if(decision==='different'){
      const sameEntry=r=>(r.product?.id===id&&r.query_fingerprint===entry.query_fingerprint);
      result.items=(result.items||[]).filter(r=>!sameEntry(r));result.additional_items=(result.additional_items||[]).filter(r=>!sameEntry(r));
      result.client_excluded=result.client_excluded||[];
      if(!result.client_excluded.some(saved=>sameEntry(saved.item)))result.client_excluded.push({item:entry,area});
      result.excluded_count=Number(result.excluded_count||0)+1;
      toast('已排除此商品；之后用同一图片查询时会记住这次核对。');
    }else toast(decision==='same'?'同款核对已保存，请联系原商品负责人。':'已标记为待核实。');
    if(state.route==='search')$('#search-results').innerHTML=searchResults();
  }catch(error){toast(error.message,true);setBusy(button,false);}
}
async function reconsiderSearchItem(button){
  const result=state.searchResult,index=Number(button.dataset.index),saved=result?.client_excluded?.[index];if(!saved)return;
  setBusy(button,true);
  try{
    const entry=saved.item;
    await api('/api/decisions',{method:'POST',body:{product_id:entry.product.id,decision:'uncertain',note:'重新标记为待核实',query_fingerprint:entry.query_fingerprint||''}});
    if(state.searchResult!==result)return;
    entry.previous_decision={decision:'uncertain',actor:state.user.name};
    result.client_excluded.splice(index,1);result.excluded_count=Math.max(0,Number(result.excluded_count||0)-1);
    if(saved.area==='main'&&(result.items||[]).length<3)result.items.push(entry);
    else{result.additional_items=(result.additional_items||[]).concat(entry).slice(0,5);state.showAdditional=true;}
    if(state.route==='search')$('#search-results').innerHTML=searchResults();
    toast('已改为待核实，可以重新核对这款商品。');
  }catch(error){toast(error.message,true);setBusy(button,false);}
}
async function loadRecentSnippet(){try{const data=await api('/api/activity');const target=$('#recent-snippet');if(target)target.innerHTML=(data.items||[]).length?(data.items||[]).slice(0,3).map(activityRow).join(''):`<div class="inline-empty">${icon('clock')}还没有操作记录。导入第一批商品后，团队的动态会出现在这里。</div>`;}catch{if($('#recent-snippet'))$('#recent-snippet').innerHTML='<div class="inline-empty">暂时无法读取动态</div>';}}

async function renderLibrary(){
  state.selectedProducts.clear();
  const counts=state.stats.workflow_counts||{};
  $('#content').innerHTML=titleBlock('公司商品库','一款商品，多平台协作。每个平台独立记录编号、定价与上架状态。',`<button class="button" data-action="toggle-trash">${icon(state.trashMode?'back':'trash')}${state.trashMode?'返回商品库':'回收站'}</button><button class="button primary" data-route="import">${icon('plus')}添加商品</button>`,'ONE LIBRARY, SHARED BY YOUR TEAM')+
  (state.trashMode?'<div class="notice amber">'+icon('trash')+'回收站 · 误移除的商品可恢复，平台资料和原始图片会一并保留。</div>':`<div class="workflow-tabs" aria-label="商品工作分类"><button class="workflow-tab ${!state.libraryStatus?'active':''}" data-workflow=""><span>全部商品</span><strong>${Number(state.stats.total_products||0)}</strong></button>${Object.entries(workflowName).filter(([v])=>v!=='legacy').map(([v,label])=>`<button class="workflow-tab ${state.libraryStatus===v?'active':''}" data-workflow="${v}"><span>${label}</span><strong>${Number(counts[v]||0)}</strong></button>`).join('')}</div>`)+
  `${!state.trashMode?'<p class="field-note" style="margin:-10px 0 17px">任一 SKU 手动确认在售，即有平台在售；所有档案均已下架，才算全部已下架。待完善、待定价可与在售重叠；待上架 SKU 列入准备上架。</p>':''}<div class="toolbar"><div class="search-field">${icon('search')}<input id="library-search" placeholder="搜索名称、平台、商品代码、商品编号…" aria-label="搜索公司商品库" value="${esc(state.libraryQuery)}"></div>${!state.trashMode?`<select id="library-platform" aria-label="筛选平台"><option value="">全部平台</option>${(state.stats.platforms||[]).map(v=>`<option ${state.libraryPlatform===v?'selected':''} value="${esc(v)}">${esc(v)}</option>`).join('')}</select><select id="listing-status" aria-label="筛选平台上架状态"><option value="">全部平台状态</option>${Object.entries(statusName).map(([v,l])=>`<option value="${v}" ${state.listingStatus===v?'selected':''}>${l}</option>`).join('')}</select><select id="library-category" aria-label="筛选商品分类">${categoryFilterOptions()}</select>`:''}<span class="count" id="library-count"></span></div><div id="library-selection"></div><div id="library-results">${loading()}</div>`+footer();
  await loadLibrary();
}
async function loadLibrary(){
  const request=++state.libraryRequest, trash=state.trashMode;
  const params=new URLSearchParams({q:state.libraryQuery,workflow_state:state.libraryStatus,platform:state.libraryPlatform,listing_status:state.listingStatus,limit:24,offset:state.libraryOffset});
  if(state.libraryCategory==='uncategorized')params.set('uncategorized','true');else if(state.libraryCategory.startsWith('category:'))params.set('category',state.libraryCategory.slice(9));
  try{
    const data=await api(trash?'/api/products/trash?'+new URLSearchParams({q:state.libraryQuery,limit:24,offset:state.libraryOffset}):'/api/products?'+params);
    if(request!==state.libraryRequest||state.route!=='library'||trash!==state.trashMode)return;
    state.products=data.items||[];state.totalProducts=data.total||0;
    state.selectedProducts.clear();renderSelection();
    $$('[data-workflow]').forEach(button=>{const count=$('strong',button);if(count)count.textContent=button.dataset.workflow?Number(state.stats.workflow_counts?.[button.dataset.workflow]||0):Number(state.stats.total_products||0);});
    $('#library-count').textContent=`共 ${state.totalProducts.toLocaleString()} 款商品`;
    $('#library-results').innerHTML=state.products.length?`<div class="product-grid">${state.products.map(productCard).join('')}</div>${state.totalProducts>24?`<div class="pagination"><button class="button sm" data-action="page-prev" ${state.libraryOffset===0?'disabled':''}>${icon('back')}上一页</button><span>${Math.floor(state.libraryOffset/24)+1} / ${Math.ceil(state.totalProducts/24)}</span><button class="button sm" data-action="page-next" ${state.libraryOffset+24>=state.totalProducts?'disabled':''}>下一页${icon('arrow')}</button></div>`:''}`:`<div class="panel empty-state"><div class="empty-icon">${icon(trash?'trash':'layers')}</div><h3>${trash?'回收站中暂无商品':'没有符合当前条件的商品'}</h3><p>${trash?'移除的商品会保留在这里，可以随时恢复。':'换个关键词或筛选条件；新导入的商品可在「待完善」中找到。'}</p>${!trash?'<button class="button" data-action="reset-library">清除筛选</button>':''}</div>`;
  }catch(error){if(request===state.libraryRequest)toast(error.message,true);}
}
function mayEdit(p){return state.user.role==='admin'||p.created_by===state.user.id||p.owner===state.user.name;}
function listingPreview(p){return (p.listings||[]).slice(0,3).map(l=>`<span class="platform-chip ${esc(l.status)}">${esc(l.platform)}${l.store?' · '+esc(l.store):''} <b>${l.status==='archived'?'已下架':(l.price&&Number(l.price)>0?esc(l.currency)+' '+esc(l.price):'待定价')+' · '+(l.status==='listed'?'已上架':'待上架')}</b></span>`).join('')+((p.listings||[]).length>3?`<span class="tiny muted">另 ${p.listings.length-3} 个平台记录</span>`:'');}
function productCard(p){return `<article class="panel product-card" data-product-card="${esc(p.id)}"><button class="product-open" data-action="detail" data-id="${esc(p.id)}"><div class="product-image">${imageUrl(p)?`<img src="${esc(imageUrl(p))}" alt="${esc(p.name)}" loading="lazy">`:icon('image')}${workflowBadge(p)}<span class="image-count">${icon('image')}${p.images?.length||0}</span></div><div class="product-info"><div class="product-code">${esc(p.code)}</div><div class="product-name truncate">${esc(p.name||'未命名商品')}</div>${p.category?`<div class="product-category truncate">${esc(p.category)}</div>`:''}<div class="platform-preview">${listingPreview(p)||'<span class="tiny muted">尚未填写平台资料</span>'}</div><div class="product-info-bottom"><span class="flex gap8">${icon('user')}${esc(p.owner||'未分配')}</span><time>${dateText(p.created_at)}</time></div></div></button><div class="product-actions">${mayEdit(p)?state.trashMode?`<button class="button light sm" data-action="restore-product" data-id="${esc(p.id)}">${icon('refresh')}恢复商品</button>`:`<label class="flex gap8"><input type="checkbox" data-product-select="${esc(p.id)}" aria-label="选择${esc(p.name)}">选择</label><span class="spacer"></span><button class="text-button" data-action="edit-card" data-id="${esc(p.id)}">${icon('edit','small')}编辑</button><button class="text-button delete-link" data-action="delete-product" data-id="${esc(p.id)}">${icon('trash','small')}移除</button>`:'<span class="tiny muted">由负责人维护</span>'}</div></article>`;}
function renderSelection(){const el=$('#library-selection');if(!el)return;el.innerHTML=!state.trashMode&&state.products.some(mayEdit)?`<div class="selection-bar"><label class="flex gap8"><input type="checkbox" id="select-all-products" ${state.products.filter(mayEdit).every(p=>state.selectedProducts.has(p.id))?'checked':''}>全选本页可维护商品</label><span>已选 ${state.selectedProducts.size} 款</span><button class="button danger sm" data-action="bulk-delete" ${!state.selectedProducts.size?'disabled':''}>${icon('trash')}移入回收站</button></div>`:'';}
function removeProducts(ids){confirmDialog(ids.length>1?`将这 ${ids.length} 款商品移入回收站？`:'将这款商品移入回收站？','移除后不再参与正常查重，原图和平台资料保留，可从商品库的回收站恢复。',async()=>{if(ids.length===1)await api('/api/products/'+encodeURIComponent(ids[0]),{method:'DELETE'});else await api('/api/products/bulk-delete',{method:'POST',body:{ids}});await refreshStats();if(state.route==='library')await renderLibrary();toast('已移入回收站');},true);}
async function showDetail(id){
  openOverlay(`<section class="drawer" role="dialog" aria-modal="true" aria-label="商品详情"><header class="drawer-header"><h2>商品详情</h2><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></header>${loading()}</section>`);
  try{state.detail=null;const p=await api((state.trashMode&&state.route==='library'?'/api/products/trash/':'/api/products/')+encodeURIComponent(id));state.detail=p.product||p;state.detailEditing=false;renderDetail();}catch(error){toast(error.message,true);closeOverlay();}
}
function renderDetail(){
  const p=state.detail;if(!p)return;const edit=state.detailEditing&&!p.deleted,canEdit=mayEdit(p)&&!p.deleted,link=safeLink(p.link);
  overlayRoot.innerHTML=`<div class="overlay" data-overlay-backdrop><section class="drawer product-drawer" role="dialog" aria-modal="true" aria-label="${edit?'编辑商品':'商品详情'}"><header class="drawer-header"><span class="badge">${icon('box','small')}</span><h2>${edit?'编辑商品资料':'商品详情'}</h2><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></header><div class="drawer-body">${edit?`<form id="product-edit-form" class="stack"><label><span class="label-text">商品名称</span><input name="name" value="${esc(p.name)}" required maxlength="160"></label><label><span class="label-text">负责人</span><input name="owner" value="${esc(p.owner)}" maxlength="80"></label>${categoryField(p.category||'')}<label><span class="label-text">规格 / 尺寸 / 材质</span><textarea name="specs" maxlength="3000">${esc(p.specs)}</textarea></label><section class="listing-section"><div class="between"><h3>平台与上架资料</h3><button class="button sm" type="button" data-action="add-listing">${icon('plus')}添加平台链接</button></div><p class="field-note">每个产品链接 / SKC 下添加多个 SKU，分别填写规格、原价格和上下架状态。删除 SKU 或整组后可撤销，保存资料后才生效。</p><div id="listing-editor">${listingEditor(state.detailListings)}</div></section><label><span class="label-text">商品链接（选填）</span><input name="link" type="url" maxlength="2000" value="${esc(p.link)}" placeholder="https://..."></label><label><span class="label-text">其他备注</span><textarea name="notes" maxlength="6000">${esc(p.notes)}</textarea></label></form>`:`${p.images?.length?`<img class="detail-main-image" id="detail-main-image" src="${esc(p.images[0].url)}" alt="${esc(p.name)}"><div class="detail-thumbs">${p.images.map((im,i)=>`<button data-action="detail-image" data-url="${esc(im.url)}" class="${i===0?'active':''}" aria-label="查看第${i+1}张图片"><img src="${esc(im.thumb_url||im.url)}" alt="商品图片${i+1}"></button>`).join('')}</div>`:''}<div class="detail-code">${esc(p.code)}</div><h2 class="detail-name">${esc(p.name)}</h2>${workflowBadge(p)}${p.workflow_state==='legacy'?'<p class="field-note">升级前状态：'+esc(statusName[p.status]||p.status)+'。补充平台和价格后将自动重新分类。</p>':''}<dl class="detail-meta"><dt>负责人</dt><dd>${esc(p.owner||'未分配')}</dd><dt>商品分类</dt><dd>${esc(p.category||'未分类')}</dd><dt>商品规格</dt><dd>${esc(p.specs||'尚未补充')}</dd><dt>商品链接</dt><dd>${link?`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">打开商品链接 ${icon('link','small')}</a>`:'尚未补充'}</dd><dt>录入时间</dt><dd>${dateText(p.created_at,true)}</dd><dt>最近更新</dt><dd>${dateText(p.updated_at,true)}</dd></dl><section class="detail-section"><h3>平台上架档案</h3>${listingDetails(p.listings||[],canEdit,p.id)}</section><section class="detail-section"><h3>商品备注</h3><p class="small muted" style="white-space:pre-wrap">${esc(p.notes||'暂无其他备注')}</p></section><section class="detail-section"><div class="between"><h3 style="margin:0">补充商品图片</h3>${canEdit?`<button class="button sm" data-action="add-product-images">${icon('plus')}添加图片</button>`:''}</div><p class="field-note">添加主图和细节图，让后续查重有更多依据。</p><input type="file" id="product-more-images" accept="image/*" multiple></section>`}</div><footer class="drawer-footer">${edit?`<button class="button ghost" data-action="cancel-edit">取消编辑</button><button class="button primary" type="submit" form="product-edit-form">${icon('check')}保存资料</button>`:(p.deleted?`<span class="field-note">商品在回收站中</span><button class="button primary" data-action="restore-product" data-id="${esc(p.id)}">${icon('refresh')}恢复商品</button>`:canEdit?`<button class="button ghost sm" data-action="delete-product" data-id="${esc(p.id)}">${icon('trash')}移入回收站</button><button class="button primary" data-action="edit-product">${icon('edit')}编辑资料</button>`:'<span class="field-note">资料由负责人维护</span><button class="button" data-action="close-overlay">关闭</button>')}</footer></section></div>`;
}
let listingGroupSequence=0;
let listingRemoved=[];
const listingSharedFields=['platform','store','product_number','link','region'];
function blankListing(){return {platform:'',store:'',product_code:'',product_number:'',status:'pending',price:'',currency:'CNY',link:'',region:'',variant_hint:''};}
function setListingGroup(row,key){Object.defineProperty(row,'_editorGroup',{value:key,writable:true,configurable:true});return row;}
function copyListingDraft(row){const copy={...row};return row._editorGroup?setListingGroup(copy,row._editorGroup):copy;}
function listingGroups(rows){
  const groups=[],known=new Map();
  for(const [index,row] of rows.entries()){
    const identified=Boolean(String(row.product_number||'').trim()||String(row.link||'').trim());
    const key=row._editorGroup||(!identified?`empty:${index}`:JSON.stringify(listingSharedFields.map(field=>String(row[field]||'').trim())));
    let group=known.get(key);
    if(!group){group={key:row._editorGroup||`listing-group-${++listingGroupSequence}`,rows:[],shared:row};known.set(key,group);groups.push(group);}
    group.rows.push(row);
  }
  return groups;
}
function listingEditor(rows){
  let index=0;
  return `<datalist id="platform-options">${['Temu','Amazon','Walmart','Etsy','eBay','Shopify','TikTok Shop','其他'].map(v=>`<option value="${v}">`).join('')}</datalist>${listingRemoved.length?`<div class="listing-undo-note"><span>删除在保存资料后生效</span><button type="button" class="text-button" data-action="undo-listing-removal">${icon('back','small')}撤销删除</button></div>`:''}${rows.length?listingGroups(rows).map((group,g)=>{
    const l=group.shared;
    return `<fieldset class="listing-row listing-skc-group" data-listing-group="${g}" data-listing-group-key="${esc(group.key)}"><legend>产品链接 / SKC ${g+1}</legend><div class="listing-row-head"><span class="tiny muted">本组共用平台、店铺和产品链接</span><button type="button" class="text-button delete-link" data-action="remove-listing-group" data-group-key="${esc(group.key)}">删除整组</button></div><div class="form-grid"><label><span class="label-text">平台 *</span><input data-group-field="platform" data-listing-field="platform" list="platform-options" value="${esc(l.platform)}" placeholder="如 Temu" required maxlength="60"></label><label><span class="label-text">店铺（选填）</span><input data-group-field="store" data-listing-field="store" value="${esc(l.store)}" placeholder="同一店铺的 SKU 放在一组" maxlength="80"></label><label><span class="label-text">商品编号 / SKC</span><input data-group-field="product_number" data-listing-field="product_number" value="${esc(l.product_number)}" placeholder="多个 SKU 共用此 SKC" maxlength="120"></label><label><span class="label-text">站点 / 地区（选填）</span><input data-group-field="region" data-listing-field="region" value="${esc(l.region||'')}" placeholder="如美国站" maxlength="80"></label><label class="full"><span class="label-text">产品前端链接</span><input data-group-field="link" data-listing-field="link" value="${esc(l.link)}" maxlength="2000" type="url" placeholder="https://..."></label></div><div class="listing-skus-heading"><strong>本链接的 SKU <span>${group.rows.length}</span></strong><button class="button sm" type="button" data-action="add-listing-sku" data-group-key="${esc(group.key)}">${icon('plus','small')}添加 SKU</button></div><div class="listing-skus">${group.rows.map(sku=>{
      const i=index++;
      return `<section class="listing-sku-row" data-listing-index="${i}" data-listing-id="${esc(sku.id||'')}"><div class="listing-sku-head"><span>SKU ${group.rows.indexOf(sku)+1}</span><button type="button" class="text-button delete-link" data-action="remove-listing" data-index="${i}" aria-label="删除 SKU ${esc(sku.product_code||group.rows.indexOf(sku)+1)}">删除 SKU</button></div><div class="form-grid"><label><span class="label-text">SKU 编号</span><input data-listing-field="product_code" value="${esc(sku.product_code)}" placeholder="如 RED-2PCS" maxlength="120"></label><label><span class="label-text">具体规格</span><input data-listing-field="variant_hint" value="${esc(sku.variant_hint||'')}" placeholder="如红色 / 2件装" maxlength="200"></label></div><div class="listing-sku-pricing"><label><span class="label-text">原价格</span><input data-listing-field="price" type="number" min="0.0001" max="999999999.9999" step="0.0001" placeholder="可留空" value="${esc(sku.price)}"></label><label><span class="label-text">币种</span><select data-listing-field="currency">${[...new Set(['CNY','USD','EUR','GBP','CAD','AUD','JPY','HKD',sku.currency||'CNY'])].map(v=>`<option ${v===sku.currency?'selected':''}>${v}</option>`).join('')}</select></label><label><span class="label-text">人工上下架</span><select class="listing-sku-status" data-listing-field="status">${Object.entries(statusName).map(([v,n])=>`<option value="${v}" ${v===sku.status?'selected':''}>${n}</option>`).join('')}</select></label></div></section>`;
    }).join('')}</div></fieldset>`;
  }).join(''):'<div class="listing-empty">尚未添加产品链接。可先保存入库，之后再补充。</div>'}`;
}
function readListings(){
  const groups=$$('[data-listing-group]',overlayRoot).filter(group=>group.dataset?.listingGroup!=null);
  if(!groups.length)return $$('[data-listing-index]',overlayRoot).map(row=>{const l=row.dataset?.listingId?{id:row.dataset.listingId}:{};$$('[data-listing-field]',row).forEach(el=>l[el.dataset.listingField]=el.value.trim());return l;});
  return groups.flatMap(group=>{
    const shared={};$$('[data-group-field]',group).forEach(el=>shared[el.dataset.groupField]=el.value.trim());
    return $$('[data-listing-index]',group).map(row=>{
      const l={...shared,...(row.dataset?.listingId?{id:row.dataset.listingId}:{})};
      $$('[data-listing-field]',row).forEach(el=>l[el.dataset.listingField]=el.value.trim());
      return setListingGroup(l,group.dataset.listingGroupKey);
    });
  });
}
function validateListings(rows){for(const row of rows){if(!row.platform)throw new Error('请填写每条记录的平台名称，或移除空白平台记录。');if(row.price&&(!Number.isFinite(Number(row.price))||Number(row.price)<=0))throw new Error('确定上架价必须大于0，未确定时请留空。');}return rows;}
function listingDetails(rows,canEdit=false,productId=''){
  if(!rows.length)return '<p class="field-note">尚未登记平台。点击编辑资料，补充平台、商品代码和商品编号。</p>';
  return '<p class="field-note listing-status-note">每个 SKU 独立确认上下架。绿色表示已确认在售；关闭即标记下架。上下架由人工维护，链接与价格仅在主动操作时查询。</p>'+rows.map(l=>{
    const editable=canEdit&&Boolean(l.id)&&Boolean(productId),listed=l.status==='listed',busy=state.listingStatusBusy.has(l.id);
    const label=[l.platform,l.store,l.product_code||l.product_number||'此条档案'].filter(Boolean).join(' · ');
    const data=`data-product-id="${esc(productId)}" data-listing-id="${esc(l.id||'')}" data-status="${esc(l.status)}"`;
    return `<div class="listing-detail" data-listing-detail="${esc(l.id||'')}"><div class="between listing-detail-head"><strong>${esc(l.platform)}${l.store?' · '+esc(l.store):''}</strong><div class="listing-status-control">${editable?`<button type="button" class="listing-sale-switch" role="switch" aria-checked="${listed}" aria-label="${esc(label)}：${listed?'点击确认下架':'点击确认上架'}" title="${listed?'手动确认此 SKU 已下架':'手动确认此 SKU 已上架成功'}" data-action="toggle-listing-status" ${data} data-next-status="${listed?'archived':'listed'}" ${busy?'disabled aria-busy="true"':''}><span aria-hidden="true"></span></button>`:''}<span class="badge ${esc(l.status)}">${esc(statusName[l.status]||l.status)}</span></div></div><dl class="detail-meta"><dt>SKU 代码</dt><dd>${esc(l.product_code||'未填写')}</dd><dt>SKC 编号</dt><dd>${esc(l.product_number||'未填写')}</dd><dt>确定价格</dt><dd>${l.price?esc(l.currency)+' '+esc(l.price):'待定价'}</dd>${safeLink(l.link)?`<dt>平台链接</dt><dd><a href="${esc(safeLink(l.link))}" target="_blank" rel="noopener noreferrer">打开平台商品 ${icon('link','small')}</a></dd>`:''}</dl>${editable&&l.status==='pending'?`<div class="listing-pending-actions"><span>尚未确认上架</span><button type="button" class="text-button" data-action="toggle-listing-status" ${data} data-next-status="archived" ${busy?'disabled':''}>标记为已下架</button></div>`:''}</div>`;
  }).join('');
}
function renderDetailKeepingScroll(){const scroll=$('.drawer-body',overlayRoot)?.scrollTop||0;renderDetail();const body=$('.drawer-body',overlayRoot);if(body)body.scrollTop=scroll;}
async function toggleListingStatus(button){
  const {productId,listingId,status,nextStatus}=button.dataset,p=state.detail;
  if(!p||p.id!==productId||p.deleted||state.detailEditing||!mayEdit(p)||state.listingStatusBusy.has(listingId))return;
  state.listingStatusBusy.add(listingId);button.disabled=true;button.setAttribute('aria-busy','true');
  try{
    const result=await api('/api/products/'+encodeURIComponent(productId)+'/listings/'+encodeURIComponent(listingId)+'/status',{method:'PATCH',body:{status:nextStatus,expected_status:status}});
    if(state.detail?.id===productId&&!state.detailEditing&&$('.product-drawer',overlayRoot)){state.detail=result.product;renderDetailKeepingScroll();}
    toast(nextStatus==='listed'?'已手动确认此 SKU 上架成功':'已手动确认此 SKU 下架');
    await refreshStats();if(state.route==='library'){state.libraryOffset=0;await loadLibrary();}
    document.dispatchEvent(new CustomEvent('midoo:listing-status-saved',{detail:{productId,listingId}}));
  }catch(error){
    toast(error.message,true);
    // A concurrent edit may have changed this SKU. Reload local records only.
    if(state.detail?.id===productId&&!state.detailEditing&&$('.product-drawer',overlayRoot)){
      try{const fresh=await api('/api/products/'+encodeURIComponent(productId));if(state.detail?.id===productId&&!state.detailEditing&&$('.product-drawer',overlayRoot)){state.detail=fresh.product||fresh;renderDetailKeepingScroll();}}catch{}
    }
  }finally{
    state.listingStatusBusy.delete(listingId);
    if(state.detail?.id===productId&&!state.detailEditing&&$('.product-drawer',overlayRoot))renderDetailKeepingScroll();
    else if(button.isConnected){button.disabled=false;button.removeAttribute('aria-busy');}
  }
}
function importGroup(files,name){return {id:++groupCounter,name:String(name||'未命名商品').slice(0,160),files:files.map(f=>({file:f,url:URL.createObjectURL(f)})),notes:'',specs:'',category:'',listings:[]};}
function addImportFiles(files,asOne=false){let list=validFiles(files);if(!list.length)return;const remaining=300-state.groups.reduce((n,g)=>n+g.files.length,0);if(list.length>remaining){toast('每批最多 300 张图片，请分批导入。',true);return;}if(list.reduce((n,f)=>n+f.size,0)+state.groups.reduce((n,g)=>n+g.files.reduce((v,im)=>v+im.file.size,0),0)>250*1024*1024){toast('每批图片总大小不能超过 250 MB，请分批导入。',true);return;}state.importResult=null;const buckets=new Map();if(asOne)buckets.set(list[0].name.replace(/\.[^.]+$/,''),list);else{for(const f of list){const path=f.webkitRelativePath||'';const parts=path.split('/');let key=parts.length>2?parts.slice(0,-1).join('/'):(parts.length===2?parts[0]:`__single_${groupCounter++}_${f.name}`);if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(f);}}for(const [key,group] of buckets){let name=key.startsWith('__single_')?group[0].name.replace(/\.[^.]+$/,''):key.split('/').pop();if(group.length>30){toast(`“${name}”超过单个商品 30 张图片的限制，请减少图片后重试。`,true);continue;}state.groups.push(importGroup(group,name));}if(state.route!=='import')navigate('import');else renderImport();}
function renderImport(){
  $('#content').innerHTML=titleBlock('把已有商品，轻松收进来','先有图，再完善资料。支持文件夹批量导入，也可以直接粘贴截图。',state.groups.length&&!state.importing?'<button class="button" data-action="clear-import">清空本批</button>':'','BUILD YOUR SHARED LIBRARY')+`<input type="file" id="import-files" accept="image/*" multiple><input type="file" id="import-folder" accept="image/*" webkitdirectory directory multiple><div id="import-work">${state.importing?importProgress():state.importResult?importSuccess():importWorkspace()}</div>`+footer();
}
function importWorkspace(){return `<div class="import-choices"><button class="panel import-choice" data-action="choose-folder"><span class="choice-icon">${icon('folder')}</span><div><h3>按商品文件夹导入</h3><p>一个文件夹对应一个商品<br>同文件夹里的多张图片自动归为一组</p></div>${icon('arrow','arrow')}</button><button class="panel import-choice" data-action="choose-import"><span class="choice-icon">${icon('image')}</span><div><h3>批量选择商品图片</h3><p>散图先逐张分组，预览时可以合并<br>也支持直接粘贴一张商品截图</p></div>${icon('arrow','arrow')}</button></div>${state.groups.length?`<div style="margin-top:24px"><div class="panel import-summary"><span class="import-summary-label">待入库 <strong>${state.groups.length}</strong> 款商品，<strong>${state.groups.reduce((n,g)=>n+g.files.length,0)}</strong> 张图片</span><div class="flex gap8"><span class="tiny muted">${state.selectedGroups.size?`已选 ${state.selectedGroups.size} 组`:''}</span><button class="button sm" data-action="merge-groups" ${state.selectedGroups.size<2?'disabled':''}>${icon('merge')}合并选中</button></div></div><div class="ai-feature-note">${icon('sparkles')}<span>${aiControlsNote()}。AI 命名会发送当前图片（视觉模式）或所填文字资料。</span><button class="text-button tiny" data-action="open-ai-settings">模型设置</button></div><div class="notice" style="margin-bottom:17px">${icon('info')}请先确认分组。同一商品的多张图片放在一组；不同规格或套装数量可以在备注里区分。</div><div class="group-grid">${state.groups.map(groupCard).join('')}</div><div class="import-bottom"><div class="import-classification">${icon('layers')}<span>按各平台资料自动分类<br><small>未填写平台与定价的商品进入待完善</small></span></div><button class="button primary" data-action="submit-import">${icon('upload')}确认入库 ${state.groups.length} 款商品</button></div></div>`:`<div class="dropzone import-drop" id="import-drop" tabindex="0" role="button" aria-label="拖入或选择商品图片">${icon('upload')}<div class="drop-title">也可以把图片拖到这里</div><p class="drop-description">每批最多 300 张 · 每商品最多 30 张 · 负责人默认为当前录入人</p></div><div class="notice" style="margin-top:20px">${icon('shield')}原始图片与商品资料保存在公司主机。批量提交前可以自由调整分组；入库后可在最近动态撤销符合条件的导入。</div>`}`;}
function groupCard(g){return `<article class="panel group-card ${state.selectedGroups.has(g.id)?'selected':''}"><div class="group-card-head"><label class="flex gap8"><input type="checkbox" data-group-select="${g.id}" ${state.selectedGroups.has(g.id)?'checked':''}><span>商品 ${state.groups.indexOf(g)+1}</span></label><button class="icon-button" data-action="remove-group" data-id="${g.id}" aria-label="移除这组商品" style="width:24px;height:24px">${icon('close','small')}</button></div><div class="group-thumbs">${g.files.map(im=>`<img src="${im.url}" alt="${esc(im.file.name)}">`).join('')}</div><div class="group-name-row"><input data-group-name="${g.id}" value="${esc(g.name)}" maxlength="160" aria-label="商品名称"><button class="button light sm" data-action="ai-title" data-id="${g.id}" ${!aiReady()||state.titleBusy.has(g.id)?'disabled':''} title="${esc(aiControlsNote())}">${state.titleBusy.has(g.id)?'<span class="spinner"></span>':icon('sparkles')}AI 命名</button></div>${g.aiSuggestion?`<div class="title-suggestion"><span>建议：${esc(g.aiSuggestion)}</span><button class="text-button tiny" data-action="accept-title" data-id="${g.id}">采用</button></div>`:''}<div class="group-details"><span>${g.files.length} 张图片</span><div class="group-toolbar">${g.files.length>1?`<button class="text-button tiny" data-action="split-group" data-id="${g.id}">拆分</button><span style="margin:0 5px;color:#d5d9e3">·</span>`:''}<button class="text-button tiny" data-action="group-note" data-id="${g.id}">${g.category||g.specs||g.notes||g.listings?.length?'已补充资料':'补充资料 / 平台'}</button></div></div></article>`;}
function importProgress(){return `<div class="panel import-progress"><span class="spinner"></span><h3 style="margin-top:14px;color:#7d8aa4">正在保存图片与检索特征</h3><p class="field-note">图片较多时需要一些时间，请保持页面打开。</p><div class="progress-track"><div class="progress-bar"></div></div></div>`;}
function importSuccess(){const r=state.importResult;return `<div class="panel import-success"><span class="success-mark">${icon('check')}</span><h2>这一批商品，已在共享库中就位</h2><p>团队现在可以用图片查到这些商品。</p><div class="success-stats"><div><strong>${r.created||0}</strong><span>新增商品</span></div><div><strong>${r.images||0}</strong><span>收录图片</span></div><div><strong>${Array.isArray(r.duplicates)?r.duplicates.length:r.duplicates||0}</strong><span>重复图片提示</span></div></div>${r.duplicate_matches?.length?`<div class="notice amber" style="margin:0 auto 24px;max-width:560px;text-align:left">${icon('info')}<span>部分图片也出现在已有记录中，请核对是否同款：${[...new Map(r.duplicate_matches.flatMap(match=>match.products||[match]).filter(p=>p.id).map(p=>[p.id,p])).values()].slice(0,5).map(p=>`<button class="text-button tiny" data-action="detail" data-id="${esc(p.id)}">${esc(p.name||p.code)}</button>`).join('、')}。</span></div>`:''}<div class="flex gap8" style="justify-content:center"><button class="button primary" data-route="library">${icon('layers')}查看商品库</button><button class="button" data-action="import-another">继续导入</button></div>${r.batch_id?`<p class="field-note" style="margin-top:20px">批次 ${esc(String(r.batch_id).slice(0,14))} · 可以在最近动态中查看及撤销</p>`:''}</div>`;}
async function submitImport(){if(!state.groups.length||state.importing)return;if(state.titleBusy.size){toast('请等标题建议生成后再入库');return;}const status='pending';const form=new FormData(),manifest={groups:[]};state.groups.forEach(g=>{const paths=[];g.files.forEach((im,i)=>{const path=`group_${g.id}/${i}_${im.file.name}`;paths.push(path);form.append('images',im.file,path);});manifest.groups.push({name:g.name.trim()||'未命名商品',paths,owner:state.user.name,status,listings:g.listings||[],notes:g.notes||'',specs:g.specs||'',category:g.category||''});});form.append('manifest',JSON.stringify(manifest));state.importing=true;$('#import-work').innerHTML=importProgress();try{state.importResult=await api('/api/import',{method:'POST',body:form});state.groups.forEach(g=>g.files.forEach(im=>URL.revokeObjectURL(im.url)));state.groups=[];state.selectedGroups.clear();await refreshStats();toast('商品入库成功');}catch(error){toast(error.message,true);}finally{state.importing=false;if(state.route==='import')renderImport();}}
function groupNotes(id){const g=state.groups.find(g=>g.id===id);if(!g)return;listingRemoved=[];openOverlay(`<section class="modal product-modal" role="dialog" aria-modal="true" aria-label="补充商品资料"><header class="drawer-header"><h2>补充商品资料</h2><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></header><form id="group-note-form" data-id="${id}"><div class="panel-body stack"><p class="tiny muted">${esc(g.name)} · 可以先入库，后续再补齐</p>${categoryField(g.category||'')}<label><span class="label-text">规格 / 尺寸 / 材质</span><textarea name="specs" placeholder="例如：白色 / 30 × 20 cm / 2件装" maxlength="3000">${esc(g.specs)}</textarea></label><section class="listing-section"><div class="between"><h3>平台与商品标识</h3><button class="button sm" type="button" data-action="add-listing">${icon('plus')}添加平台链接</button></div><p class="field-note">同一产品链接 / SKC 下可添加多个 SKU，原价格与上下架状态分别保存。价格未确定时留空。</p><div id="listing-editor">${listingEditor(g.listings||[])}</div></section><label><span class="label-text">其他备注</span><textarea name="notes" placeholder="其他需要团队知道的信息" maxlength="6000">${esc(g.notes)}</textarea></label></div><footer class="drawer-footer"><span></span><button class="button primary" type="submit">保存本组资料</button></footer></form></section>`,true);}
function activityRow(item){
  const kind=item.action||item.type||item.kind||'update';const kindNames={import:'批量导入商品',import_batch:'批量导入商品',product_created:'添加商品',create:'添加商品',create_product:'添加商品',update:'更新商品资料',update_product:'更新商品资料',delete:'删除商品',delete_product:'删除商品',decision:'保存同款核对结果',search:'完成图片检索',backup:'创建数据备份',undo_import:'撤销导入批次',login:'登录工作台',setup:'建立工作空间',listing_status:'人工确认上下架'};
  let details=item.details||item.detail||{};if(typeof details==='string'){try{details=JSON.parse(details);}catch{details={message:details};}}
  const title=item.title||item.description||kindNames[kind]||kind;
  const actor=item.actor||item.user_name||item.username||item.operator||'团队成员';
  const caption=item.summary||details.name||details.message||(details.created?`新增 ${details.created} 款商品`:'');
  const batchId=item.batch_id||details.batch_id;const undoable=item.can_undo??item.undoable??(batchId && /import/.test(kind) && !/undo/.test(kind));
  return `<div class="recent-row"><span class="activity-symbol">${icon(/import|create/.test(kind)?'upload':/decision/.test(kind)?'check':/backup/.test(kind)?'download':/search/.test(kind)?'search':'clock')}</span><div style="min-width:0"><div class="activity-title truncate">${esc(title)}</div><div class="activity-caption truncate">${esc(typeof actor==='object'?actor.name:actor)}${caption?' · '+esc(caption):''}</div></div><time class="activity-time">${dateText(item.created_at||item.timestamp,true)}</time>${undoable&&batchId?`<button class="text-button tiny" data-action="undo-batch" data-id="${esc(batchId)}">撤销</button>`:''}</div>`;
}
function resetActivityState(){state.activityRequest++;state.activityFilters={actor_id:'',start_date:'',end_date:''};state.activityDraft={...state.activityFilters};state.activityOffset=0;state.activityData={items:[],total:0,actors:[]};state.activityError='';state.activityLoading=false;}
function activityCalendarDay(date=new Date()){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);return ['year','month','day'].map(type=>parts.find(p=>p.type===type).value).join('-');}
function activityDateRange(range,date=new Date()){if(range==='all')return {start_date:'',end_date:''};const end_date=activityCalendarDay(date),start=new Date(end_date+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-(range==='7'?6:range==='30'?29:0));return {start_date:start.toISOString().slice(0,10),end_date};}
function activityFilterControls(){
  const f=state.activityDraft,actors=state.activityData.actors||[],known=actors.some(a=>String(a.id)===f.actor_id);
  return `<form class="activity-filter-form" id="activity-filter-form"><label class="activity-actor"><span class="label-text">操作者</span><select name="actor_id" id="activity-actor" aria-label="按操作者筛选"><option value="">全部操作者</option>${!known&&f.actor_id?`<option value="${esc(f.actor_id)}" selected>已选择的操作者</option>`:''}${actors.map(actor=>`<option value="${esc(actor.id)}" ${String(actor.id)===f.actor_id?'selected':''}>${esc(actor.name)}</option>`).join('')}</select></label><div class="activity-date-fields"><label><span class="label-text">开始日期</span><input type="date" name="start_date" id="activity-start" aria-label="开始日期" value="${esc(f.start_date)}"></label><span class="activity-date-separator" aria-hidden="true">—</span><label><span class="label-text">结束日期</span><input type="date" name="end_date" id="activity-end" aria-label="结束日期" value="${esc(f.end_date)}"></label></div><div class="activity-filter-actions"><button class="button primary" type="submit">${icon('search')}筛选记录</button><button class="button ghost" type="button" data-action="clear-activity-filters">清除筛选</button></div></form><div class="activity-quick-row"><span>时间范围</span><div class="activity-quick-buttons">${[['today','今天'],['7','近 7 天'],['30','近 30 天'],['all','全部']].map(([range,label])=>{const dates=activityDateRange(range),active=f.start_date===dates.start_date&&f.end_date===dates.end_date;return `<button class="activity-range ${active?'active':''}" type="button" data-action="activity-range" data-range="${range}" aria-pressed="${active}">${label}</button>`;}).join('')}</div><span class="activity-timezone">按北京时间，包含起止当天</span></div>`;
}
function activityResults(){
  if(state.activityLoading)return `<div class="activity-loading" role="status"><span class="spinner"></span>正在筛选操作记录…</div>`;
  if(state.activityError)return `<div class="empty-state activity-empty" role="alert"><div class="empty-icon">${icon('warning')}</div><h3>暂时无法读取记录</h3><p>${esc(state.activityError)}</p><button class="button" data-action="retry-activity">${icon('refresh')}重试</button></div>`;
  const data=state.activityData,items=data.items||[],total=Number(data.total)||0,filtered=Object.values(state.activityFilters).some(Boolean),page=Math.floor(state.activityOffset/40)+1,pages=Math.max(1,Math.ceil(total/40));
  const list=items.length?items.map(activityRow).join(''):`<div class="empty-state activity-empty"><div class="empty-icon">${icon('clock')}</div><h3>${filtered?'这个范围内没有操作记录':'还没有商品操作记录'}</h3><p>${filtered?'试试调整操作者或扩大日期范围。':'导入第一批商品，开始积累团队的共享商品经验。'}</p>${filtered?'<button class="button" data-action="clear-activity-filters">清除筛选</button>':'<button class="button primary" data-route="import">'+icon('plus')+'添加商品</button>'}</div>`;
  return `<div class="activity-result-heading"><span>${filtered?'筛选结果':'全部记录'} <strong>${total.toLocaleString()}</strong> 条</span><span>${items.length?`第 ${state.activityOffset+1}–${Math.min(state.activityOffset+items.length,total)} 条 · `:''}按时间倒序</span></div>${list}<footer class="activity-pagination"><span>第 ${page} / ${pages} 页 · 每页 40 条</span><div><button class="button sm" data-action="activity-page" data-direction="prev" ${state.activityOffset===0?'disabled':''}>${icon('back')}上一页</button><button class="button sm" data-action="activity-page" data-direction="next" ${state.activityOffset+40>=total?'disabled':''}>下一页${icon('arrow')}</button></div></footer>`;
}
async function renderActivity(){
  $('#content').innerHTML=titleBlock('团队的每一步，都有记录','按操作者与日期查阅入库、修改和同款核对，找到你关心的那一步。','','WORKSPACE ACTIVITY')+`<section class="panel activity-filter-panel" aria-label="操作记录筛选"><div id="activity-filter-controls">${activityFilterControls()}</div></section><section class="panel activity-results-panel" aria-label="操作记录"><header class="panel-head"><div class="panel-title">${icon('clock')}操作记录</div><span class="tiny muted">团队操作留痕</span></header><div id="activity-results" aria-live="polite">${activityResults()}</div></section>`+footer();await loadActivity();
}
async function loadActivity(){
  const request=++state.activityRequest,routeRequest=state.routeRequest,params=new URLSearchParams({limit:'40',offset:String(state.activityOffset)});for(const [key,value] of Object.entries(state.activityFilters))if(value)params.set(key,value);
  state.activityLoading=true;state.activityError='';if($('#activity-results')&&state.route==='activity')$('#activity-results').innerHTML=activityResults();
  try{
    const data=await api('/api/activity?'+params);if(request!==state.activityRequest||routeRequest!==state.routeRequest||state.route!=='activity')return;
    const total=Math.max(0,Number(data.total)||0),offset=Number(params.get('offset')),lastOffset=Math.max(0,Math.floor((total-1)/40)*40);
    state.activityData={items:Array.isArray(data.items)?data.items:[],total,actors:Array.isArray(data.actors)?data.actors:[]};
    if(offset>lastOffset){state.activityOffset=lastOffset;return await loadActivity();}
  }catch(error){if(request!==state.activityRequest||routeRequest!==state.routeRequest||state.route!=='activity')return;state.activityError=error.message;}
  finally{if(request===state.activityRequest&&routeRequest===state.routeRequest&&state.route==='activity'){state.activityLoading=false;if($('#activity-results'))$('#activity-results').innerHTML=activityResults();if($('#activity-filter-controls'))$('#activity-filter-controls').innerHTML=activityFilterControls();}}
}
async function applyActivityFilters(values){
  const f={actor_id:String(values.actor_id||''),start_date:String(values.start_date||''),end_date:String(values.end_date||'')};
  if((f.start_date&&!/^\d{4}-\d{2}-\d{2}$/.test(f.start_date))||(f.end_date&&!/^\d{4}-\d{2}-\d{2}$/.test(f.end_date))){toast('请选择有效的起止日期。',true);return;}
  if(f.start_date&&f.end_date&&f.start_date>f.end_date){toast('开始日期不能晚于结束日期。',true);return;}
  state.activityFilters=f;state.activityDraft={...f};state.activityOffset=0;return await loadActivity();
}

function chatProviderKey(){const p=activeAI();return p?`${p.id}:${p.model}`:'';}
function chatReasoning(){return activeAI()?.reasoning||{options:[{value:'default',label:'官方默认'}],note:'此模型暂未提供可核验的思考选项，使用官方默认设置。'};}
function chatEffort(){const options=chatReasoning().options||[];const selected=state.chatEfforts[chatProviderKey()]||'default';return options.some(o=>o.value===selected)?selected:'default';}
function resetChatState(){state.chatGeneration++;state.chatUrls.forEach(url=>URL.revokeObjectURL(url));state.chatUrls.clear();state.chatFiles=[];state.chatHistory=[];state.chatDraft='';state.chatError='';state.chatPending=null;state.chatSending=false;state.chatMode='auto';state.chatSearchEnabled=false;state.chatWebQuery='';state.assistantTab='chat';state.knowledgeRequest++;state.knowledgeOverlayRequest++;state.knowledgeEditor=null;state.knowledgeQuery='';state.knowledgeDeleted=false;state.knowledgeBusy=false;}
function chatAttachmentCard(item,index=null){
  const name=item.name||item.file?.name||'附件',image=item.kind==='image';
  const caption=index!==null?(image?'图片 · ':'' )+chatFileSize(item.file.size):item.truncated?'已提取文字 · 内容已截断':image?'图片':Number.isFinite(item.characters)?`已提取 ${item.characters.toLocaleString()} 字`:'资料文件';
  return `<div class="chat-attachment ${image?'is-image':''}">${image&&item.preview?`<img src="${esc(item.preview)}" alt="${esc(name)}">`:`<span class="chat-attachment-symbol">${icon(image?'image':'file')}</span>`}<div class="chat-attachment-info"><strong title="${esc(name)}">${esc(name)}</strong><span>${esc(caption)}</span></div>${index!==null?`<button class="chat-attachment-remove" type="button" data-action="remove-chat-file" data-index="${index}" aria-label="移除附件 ${esc(name)}" ${state.chatSending?'disabled':''}>${icon('close')}</button>`:''}</div>`;
}
function chatFileSize(bytes){return bytes>=1024*1024?`${(bytes/1024/1024).toFixed(1)} MB`:`${Math.max(1,Math.ceil(bytes/1024))} KB`;}
function renderChatAttachments(){const el=$('#chat-attachments');if(el)el.innerHTML=state.chatFiles.map((f,i)=>chatAttachmentCard(f,i)).join('');}
function updateChatControls(){
  if(state.route!=='ai')return;
  const ready=aiReady();
  for(const selector of ['#chat-input','#chat-mode','#chat-web-query','#chat-effort','#chat-files','[data-action="choose-chat-files"]','[data-action="clear-chat"]','.chat-send']){const el=$(selector);if(el)el.disabled=!ready||state.chatSending||(selector==='#chat-effort'&&(chatReasoning().options||[]).length<2);}
  $$('[data-action="remove-chat-file"],.prompt-card').forEach(el=>el.disabled=state.chatSending);
  const webToggle=$('#chat-search-enabled');if(webToggle)webToggle.disabled=!ready||state.chatSending||!state.aiConfig?.web_search?.available;
  const error=$('#chat-error');if(error){error.textContent=state.chatError;error.hidden=!state.chatError;}
  const busy=$('#chat-busy');if(busy)busy.hidden=!state.chatSending;
}
function addChatFiles(files){
  if(state.chatSending||!aiReady())return;
  const candidates=[...files];let count=state.chatFiles.length,total=state.chatFiles.reduce((sum,item)=>sum+item.file.size,0);const accepted=[],errors=[];
  for(const file of candidates){
    const ext=file.name.split('.').pop().toLowerCase(),image=['jpg','jpeg','png','webp','gif'].includes(ext);
    if(!image&&!['txt','md','csv','json','pdf','docx','xlsx'].includes(ext)){errors.push(`${file.name}：暂不支持此格式`);continue;}
    if(image&&!activeAI()?.vision){errors.push('上传图片需要管理员在模型设置中允许图片识别，并选择支持视觉的模型');continue;}
    if(!file.size){errors.push(`${file.name}：文件为空`);continue;}
    if(file.size>10*1024*1024){errors.push(`${file.name}：每个附件不能超过 10 MB`);continue;}
    if(count>=6){errors.push('每次最多添加 6 个附件');break;}
    if(total+file.size>20*1024*1024){errors.push('本次附件合计不能超过 20 MB');continue;}
    if([...state.chatFiles,...accepted].some(item=>item.file.name===file.name&&item.file.size===file.size&&item.file.lastModified===file.lastModified))continue;
    const preview=image?URL.createObjectURL(file):'';if(preview)state.chatUrls.add(preview);
    accepted.push({file,name:file.name,kind:image?'image':'document',preview});count++;total+=file.size;
  }
  state.chatFiles.push(...accepted);if(accepted.length)state.chatError='';renderChatAttachments();updateChatControls();if(errors.length)toast([...new Set(errors)].slice(0,3).join('；'),true);
}
function removeChatFile(index){if(state.chatSending)return;const item=state.chatFiles.splice(index,1)[0];if(item?.preview){URL.revokeObjectURL(item.preview);state.chatUrls.delete(item.preview);}renderChatAttachments();}
async function clearChat(){if(state.chatSending)return;try{await api('/api/ai/chat/attachments/clear',{method:'POST'});resetChatState();if(state.route==='ai')await renderAI();}catch(error){toast('未能清空对话：'+error.message,true);}}
function assistantTabs(){return `<div class="assistant-tabs" role="tablist" aria-label="AI 管家工作区"><button role="tab" aria-selected="${state.assistantTab==='chat'}" class="${state.assistantTab==='chat'?'active':''}" data-action="assistant-tab" data-tab="chat">${icon('sparkles')}对话助手</button><button role="tab" aria-selected="${state.assistantTab==='knowledge'}" class="${state.assistantTab==='knowledge'?'active':''}" data-action="assistant-tab" data-tab="knowledge">${icon('book')}公司知识</button></div>`;}
function assistantHeader(){return titleBlock('让公司知识，随问随用','找到已有商品和团队经验，也能带着新问题继续思考。',`<button class="button" data-action="open-ai-settings">${icon('settings')}模型设置</button>`,'YOUR COMPANY KNOWLEDGE, CONNECTED')+assistantTabs();}
function assistantLink(value){if(typeof value!=='string'||/[\u0000-\u0020\u007f]/.test(value))return '';try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}}
function assistantMedia(value){return typeof value==='string'&&/^\/media\/[a-zA-Z0-9_-]+(?:\?thumb=(?:1|0|true|false))?$/.test(value)?value:'';}
function markdownInline(value){
  const tokens=[];let text=String(value||'').replace(/\u0000/g,'');
  text=text.replace(/`([^`\n]+)`|\[([^\]\n]{1,180})\]\(([^)\s]{1,1800})\)/g,(all,code,label,target)=>{
    const href=code===undefined?assistantLink(target):'';
    const rendered=code!==undefined?`<code>${esc(code)}</code>`:href?`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`:esc(label);
    return '\u0000'+(tokens.push(rendered)-1)+'\u0000';
  });
  text=esc(text).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/__([^_\n]+)__/g,'<strong>$1</strong>').replace(/(^|\s)\*([^*\n]+)\*(?=\s|$|[，。！？])/g,'$1<em>$2</em>');
  return text.replace(/\u0000(\d+)\u0000/g,(_,index)=>tokens[Number(index)]||'');
}
function chatMarkdown(value){
  const lines=String(value||'').slice(0,60000).replace(/\r\n?/g,'\n').split('\n'),out=[];let i=0;
  const tableLine=line=>/^\s*\|?\s*:?-{3,}:?\s*\|/.test(line||'');
  const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').slice(0,12).map(c=>c.trim());
  const block=line=>/^\s*(?:```|#{1,6}\s|[-*+]\s|\d+\.\s|>\s)/.test(line||'');
  while(i<lines.length){let line=lines[i];if(!line.trim()){i++;continue;}
    if(/^\s*```/.test(line)){const lang=line.trim().slice(3).replace(/[^\w+-]/g,'').slice(0,24),code=[];i++;while(i<lines.length&&!/^\s*```/.test(lines[i]))code.push(lines[i++]);if(i<lines.length)i++;out.push(`<pre><code${lang?` data-language="${esc(lang)}"`:''}>${esc(code.join('\n'))}</code></pre>`);continue;}
    if(i+1<lines.length&&line.includes('|')&&tableLine(lines[i+1])){const head=cells(line);i+=2;const rows=[];while(i<lines.length&&lines[i].trim()&&lines[i].includes('|')&&rows.length<50)rows.push(cells(lines[i++]));out.push(`<div class="chat-table-wrap"><table><thead><tr>${head.map(c=>`<th>${markdownInline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${head.map((_,j)=>`<td>${markdownInline(row[j]||'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);continue;}
    const heading=line.match(/^\s*(#{1,6})\s+(.+)$/);if(heading){const level=Math.min(heading[1].length+2,6);out.push(`<h${level}>${markdownInline(heading[2])}</h${level}>`);i++;continue;}
    if(/^\s*(?:[-*+]\s|\d+\.\s)/.test(line)){const ordered=/^\s*\d+\./.test(line),tag=ordered?'ol':'ul',items=[];while(i<lines.length&&(ordered?/^\s*\d+\.\s/:/^\s*[-*+]\s/).test(lines[i]))items.push(lines[i++].replace(/^\s*(?:[-*+]|\d+\.)\s+/,''));out.push(`<${tag}>${items.map(t=>`<li>${markdownInline(t)}</li>`).join('')}</${tag}>`);continue;}
    if(/^\s*>\s?/.test(line)){const quote=[];while(i<lines.length&&/^\s*>/.test(lines[i]))quote.push(lines[i++].replace(/^\s*>\s?/,''));out.push(`<blockquote>${quote.map(markdownInline).join('<br>')}</blockquote>`);continue;}
    const para=[line];i++;while(i<lines.length&&lines[i].trim()&&!block(lines[i])&&!(i+1<lines.length&&lines[i].includes('|')&&tableLine(lines[i+1])))para.push(lines[i++]);out.push(`<p>${para.map(markdownInline).join('<br>')}</p>`);
  }return out.join('');
}
function chatProductCards(items){return Array.isArray(items)&&items.length?`<div class="chat-product-grid">${items.slice(0,6).filter(p=>p&&typeof p.id==='string').map(p=>{const image=assistantMedia(p.images?.[0]?.thumb_url||p.images?.[0]?.url);return `<button class="chat-product-card" data-action="detail" data-id="${esc(p.id)}">${image?`<img src="${esc(image)}" alt="${esc(p.name||'公司商品')}" loading="lazy">`:`<span class="chat-product-placeholder">${icon('image')}</span>`}<span class="chat-product-copy"><span class="chat-product-code">${esc(p.code||'公司商品')}</span><strong>${esc(p.name||'未命名商品')}</strong><span>${esc([p.category,p.owner].filter(Boolean).join(' · '))}</span><span class="chat-product-open">查看商品与原图 ${icon('arrow','small')}</span></span></button>`;}).join('')}</div>`:'';}
function chatSourceCards(items){if(!Array.isArray(items)||!items.length)return '';return `<details class="chat-sources"><summary>${icon('book')}参考资料 <span>${Math.min(items.length,12)}</span></summary><div class="chat-source-list">${items.slice(0,12).filter(x=>x&&typeof x==='object').map(source=>{const label=source.kind==='web'?'网页':source.kind==='product'?'商品':'公司知识',url=source.kind==='web'?assistantLink(source.url):'',text=`<span class="chat-source-type">${label}${source.id?' · '+esc(source.id):''}</span><strong>${esc(source.title||'参考资料')}</strong><span>${esc(String(source.excerpt||'').slice(0,400))}</span>`;return url?`<a class="chat-source" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${text}</a>`:source.kind==='knowledge'&&source.document_id?`<button class="chat-source" data-action="knowledge-view" data-id="${esc(source.document_id)}">${text}</button>`:`<div class="chat-source">${text}</div>`;}).join('')}</div></details>`;}
function chatEvidence(message){return chatProductCards(message.product_cards)+chatSourceCards(message.sources)+(Array.isArray(message.notices)?message.notices.slice(0,6).map(note=>`<div class="chat-answer-notice">${icon('info')}<span>${esc(note)}</span></div>`).join(''):'')+(message.web_search&&message.web_search.status!=='off'?`<div class="chat-search-status">${icon('search')}<span>${esc(message.web_search.status==='ok'?'已联网检索：'+(message.web_search.query||''):message.web_search.note||'本次未获得可用网页结果')}</span></div>`:'');}
function chatModeNote(){return state.chatMode==='company'?'从公司商品和已启用知识中查找依据，引用可打开核对。':state.chatMode==='general'?'使用当前模型的通用问答能力；本轮不检索公司资料。':'按问题检索公司资料，也可讨论选品、写作与一般问题。';}
function renderChatSearch(){const box=$('#chat-web-panel');if(box)box.hidden=!state.chatSearchEnabled;}
async function renderAI(){
  const request=state.routeRequest,user=state.user?.id,config=await api('/api/ai/config');if(state.route!=='ai'||request!==state.routeRequest||state.user?.id!==user)return;state.aiConfig=config;
  const active=activeAI(),ready=aiReady(),providerKey=chatProviderKey();
  if(state.chatProviderKey&&state.chatProviderKey!==providerKey)resetChatState();state.chatProviderKey=providerKey;
  if(state.assistantTab==='knowledge'){await renderKnowledge();return;}
  const reasoning=chatReasoning(),options=reasoning.options||[{value:'default',label:'官方默认'}],web=state.aiConfig.web_search||{};
  if(!web.available)state.chatSearchEnabled=false;
  $('#content').innerHTML=assistantHeader()+`<div class="ai-layout assistant-workspace"><section class="panel chat-panel"><header class="chat-top"><span class="ai-orb">${icon('sparkles')}</span><div><h3 class="chat-assistant-name">觅同 AI 管家</h3><p class="chat-status">${ready?esc(active.label)+' · '+esc(active.model):'等待连接模型服务'}</p></div><span class="spacer"></span><span class="chat-busy" id="chat-busy" ${state.chatSending?'':'hidden'}>正在思考</span><button class="icon-button" data-action="clear-chat" title="清空对话与本次上传的附件" aria-label="清空对话" ${state.chatSending?'disabled':''}>${icon('refresh')}</button></header>
  <div class="assistant-context-bar"><label><span class="sr-only">问答模式</span><select id="chat-mode" aria-label="问答模式" ${state.chatSending?'disabled':''}>${[['auto','智能助手'],['company','公司知识'],['general','通用对话']].map(([v,t])=>`<option value="${v}" ${state.chatMode===v?'selected':''}>${t}</option>`).join('')}</select></label><p id="chat-mode-note">${chatModeNote()}</p>${!state.aiConfig.share_library_context&&!state.aiConfig.share_knowledge_context?'<button class="text-button tiny" data-action="open-ai-settings">开启公司资料</button>':''}</div>
  <div class="chat-messages" id="chat-messages" role="log" aria-label="AI 对话记录" aria-live="polite">${state.chatHistory.length||state.chatPending?chatMessages():`<div class="ai-welcome"><span class="ai-orb">${icon('sparkles')}</span><h3>${ready?'从公司的已知，走向新的想法':'先连接一个你信任的模型'}</h3><p>${ready?'用自然语言找商品、查看原图、询问团队经验。也能联网检索，深入讨论。':'已预留 DeepSeek、通义千问、OpenAI 等服务接口。公司知识可先整理入库，随时再连接模型。'}</p>${!ready?'<button class="button light" data-action="open-ai-settings">配置模型服务 '+icon('arrow')+'</button>':`<div class="prompt-grid">${(state.aiConfig.share_library_context?['找一下公司里的圣诞树蜡烛，把图片给我','哪些商品还缺少规格信息？','帮我整理准备上架的商品']:['帮我梳理这份商品资料的卖点','帮我完善一个选品创意','如何区分外观相似但规格不同的商品？']).map(p=>`<button class="prompt-card" type="button" data-action="chat-prompt" data-prompt="${esc(p)}" ${state.chatSending?'disabled':''}>${esc(p)}${icon('arrow')}</button>`).join('')}</div>`}</div>`}</div>
  <form class="chat-form" id="chat-form"><div class="chat-composer" id="chat-drop"><div class="chat-drop-hint">${icon('upload')}松开，添加到本次对话</div><div class="chat-attachments" id="chat-attachments"></div><div class="chat-input-wrap"><textarea id="chat-input" maxlength="6000" placeholder="问公司里的知识，或聊一个新的问题…" aria-label="给 AI 管家的问题" ${!ready||state.chatSending?'disabled':''}>${esc(state.chatDraft)}</textarea></div>
  <div class="chat-toolbar"><button class="chat-attach-button" type="button" data-action="choose-chat-files" ${!ready||state.chatSending?'disabled':''}>${icon('paperclip')}添加附件</button><input id="chat-files" type="file" multiple accept=".jpg,.jpeg,.png,.webp,.gif,.txt,.md,.csv,.json,.pdf,.docx,.xlsx"><span class="chat-toolbar-divider"></span><label class="chat-effort-control"><span>思考强度</span><select id="chat-effort" aria-label="思考强度" title="${esc(reasoning.note||'官方默认')}" ${!ready||state.chatSending||options.length<2?'disabled':''}>${options.map(o=>`<option value="${esc(o.value)}" ${chatEffort()===o.value?'selected':''}>${esc(o.label)}</option>`).join('')}</select></label><label class="chat-web-toggle" title="${esc(web.note||'联网搜索需在模型设置中启用')}" ><input id="chat-search-enabled" type="checkbox" ${state.chatSearchEnabled?'checked':''} ${!ready||state.chatSending||!web.available?'disabled':''}>${icon('search')}联网</label><span class="spacer"></span><button type="submit" class="chat-send" aria-label="发送问题" ${!ready||state.chatSending?'disabled':''}>${state.chatSending?'<span class="spinner"></span>':icon('send')}</button></div>
  <div class="chat-web-panel" id="chat-web-panel" ${state.chatSearchEnabled?'':'hidden'}><label><span>公开搜索关键词 <small>只向搜索服务发送这段文字</small></span><input id="chat-web-query" maxlength="128" value="${esc(state.chatWebQuery)}" placeholder="例如：美国圣诞蜡烛市场趋势 2026" aria-label="公开搜索关键词" ${state.chatSending?'disabled':''}></label><p>DeepSeek Search 可能额外计费；请勿填写公司机密。检索结果会交给当前模型作答；一次联网问答计入 2 次请求额度。</p></div></div>
  <p class="chat-error" id="chat-error" role="alert" ${state.chatError?'':'hidden'}>${esc(state.chatError)}</p><div class="chat-disclosure">${icon('shield')}<span>发送时，附件${active?.vision?'图片和文件中提取的文字':'中提取的文字'}会交给当前服务商。${state.aiConfig.share_library_context?'已允许发送必要商品摘要。':'商品摘要未开启。'}${state.aiConfig.share_knowledge_context?'已允许发送相关知识片段。':''}<span class="chat-key-hint"> Enter 发送 · Shift + Enter 换行</span></span></div></form></section>
  <aside class="panel ai-aside assistant-aside"><h3>连接团队的每一份经验</h3><div class="ai-capability">${icon('book')}<div><strong>公司知识，持久保存</strong><p>把操作流程、选品经验和规格资料整理进共享知识。回答会附参考条目，方便核对。</p><button class="text-button tiny" data-action="assistant-tab" data-tab="knowledge">管理公司知识 ${icon('arrow','small')}</button></div></div><div class="ai-capability">${icon('image')}<div><strong>找到商品，也看得见</strong><p>相关公司商品会显示原图卡片，可直接打开档案。图片显示与模型视觉识别分别工作。</p></div></div><div class="ai-capability">${icon('sparkles')}<div><strong>按模型官方能力思考</strong><p>${esc(reasoning.note||'使用官方默认思考设置。')}</p>${reasoning.docs_url?`<a class="chat-doc-link" href="${esc(assistantLink(reasoning.docs_url))}" target="_blank" rel="noopener noreferrer">查看官方说明 ${icon('link','small')}</a>`:''}</div></div><div class="ai-capability">${icon('search')}<div><strong>${web.available?'联网搜索已就绪':'联网搜索待启用'}</strong><p>${esc(web.note||'在模型设置中启用联网，并配置官方 DeepSeek 搜索服务。')}</p></div></div><div class="guide-tip">支持 TXT、MD、CSV、JSON、PDF、Word、Excel（DOCX / XLSX）及图片附件。每次最多 6 个、单个 10 MB、合计 20 MB。对话附件在主机保留约 60 分钟，公司知识则持久保存。AI 建议仍需结合实际资料核实。</div></aside></div>`+footer();
  renderChatAttachments();updateChatControls();scrollChat();
}
async function renderKnowledge(){
  const request=++state.knowledgeRequest,route=state.routeRequest,user=state.user?.id;
  $('#content').innerHTML=assistantHeader()+`<div id="knowledge-workspace">${loading()}</div>`;
  try{const params=new URLSearchParams({query:state.knowledgeQuery,deleted:String(state.knowledgeDeleted)});const [data,stats]=await Promise.all([api('/api/knowledge/documents?'+params),api('/api/knowledge/stats')]);
    if(state.user?.id!==user||state.route!=='ai'||state.assistantTab!=='knowledge'||route!==state.routeRequest||request!==state.knowledgeRequest)return;
    const items=Array.isArray(data.items)?data.items:[];
    $('#knowledge-workspace').innerHTML=`<section class="knowledge-intro"><span class="knowledge-intro-icon">${icon('book')}</span><div><h2>让有用的知识，留在团队里</h2><p>操作流程、产品资料、选品经验，都可以成为下一次回答的依据。</p></div><div class="knowledge-totals"><strong>${Number(stats.documents||0)}</strong><span>篇有效资料</span></div></section><div class="knowledge-toolbar"><form id="knowledge-search-form" class="search-field">${icon('search')}<input id="knowledge-search" placeholder="搜索知识标题与内容…" value="${esc(state.knowledgeQuery)}" aria-label="搜索公司知识"><button class="text-button" type="submit">搜索</button></form><button class="button ${state.knowledgeDeleted?'light':''}" data-action="knowledge-trash">${icon('refresh')}${state.knowledgeDeleted?'返回有效资料':'已停用资料'}</button><span class="spacer"></span><button class="button" data-action="knowledge-import" ${state.knowledgeBusy?'disabled':''}>${state.knowledgeBusy?'<span class="spinner"></span>':icon('upload')}导入文件</button><button class="button primary" data-action="knowledge-new">${icon('plus')}新建知识</button><input type="file" id="knowledge-files" multiple accept=".txt,.md,.csv,.json,.pdf,.docx,.xlsx" hidden></div><p class="knowledge-scope-note">${state.knowledgeDeleted?'停用的资料不参与 AI 检索，可由作者或管理员恢复。':`共 ${Number(data.total||0)} 篇${state.knowledgeQuery?'匹配资料':'资料'} · 知识与商品资料在本机检索，发送片段需由管理员在模型设置中授权。`}</p><div class="knowledge-grid">${items.map(item=>`<article class="panel knowledge-card"><button class="knowledge-card-main" data-action="knowledge-view" data-id="${esc(item.id)}"><span class="knowledge-card-symbol">${icon(item.source_name?'file':'book')}</span><span class="knowledge-card-type">${esc(item.category||'团队资料')}${item.truncated?' · 部分内容':''}</span><h3>${esc(item.title||'未命名知识')}</h3><p>${esc(item.source_name||'手写知识条目')} · ${Number(item.characters||0).toLocaleString()} 字</p></button><div class="knowledge-card-footer"><span>${esc(item.owner||'团队成员')} · ${esc(dateText(item.updated_at))}</span>${item.can_edit?`<button class="text-button tiny" data-action="${state.knowledgeDeleted?'knowledge-restore':'knowledge-edit'}" data-id="${esc(item.id)}">${state.knowledgeDeleted?'恢复':'编辑'}</button>${state.knowledgeDeleted?'':`<button class="text-button tiny" data-action="knowledge-disable" data-id="${esc(item.id)}">停用</button>`}`:''}</div></article>`).join('')||`<div class="panel knowledge-empty"><span class="empty-icon">${icon('book')}</span><h3>${state.knowledgeQuery?'没有找到相关资料':state.knowledgeDeleted?'没有停用的资料':'从第一份团队资料开始'}</h3><p>${state.knowledgeQuery?'换一个关键词，或清空搜索后查看已有知识。':'新建一条经验，或导入操作文档。公司商品无需重复导入。'}</p></div>`}</div><div class="knowledge-storage-note">${icon('shield')}<p>持久保存提取的文字，原文件不保存。支持 TXT、MD、CSV、JSON、PDF、DOCX、XLSX；每次最多 6 个，单个 10 MB、合计 20 MB。每份文件最多提取 16000 字，不含完整排版或内嵌图片，超出解析范围会标记“部分内容”；长文可拆分导入，或新建最多 10 万字的知识条目。</p></div>`;
  }catch(error){if(state.user?.id===user&&state.route==='ai'&&state.assistantTab==='knowledge'&&route===state.routeRequest&&request===state.knowledgeRequest)$('#knowledge-workspace').innerHTML=`<div class="panel empty-state"><h3>暂时无法读取公司知识</h3><p>${esc(error.message)}</p><button class="button" data-action="knowledge-reload">重新加载</button></div>`;}
}
function knowledgeDialog(item={},editing=false){
  state.knowledgeEditor={...item};const title=editing?(item.id?'编辑知识':'新建知识'):'知识详情';
  openOverlay(`<section class="drawer knowledge-drawer" role="dialog" aria-modal="true" aria-label="${title}"><header class="drawer-header"><span class="badge">${icon('book','small')}</span><h2>${title}</h2><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></header><div class="drawer-body">${item.truncated?`<div class="notice amber">${icon('info')}此资料仅保存了已提取部分。完整内容请查看原文件，或拆分后重新导入。</div>`:''}${editing?`<form id="knowledge-form" class="stack" data-id="${esc(item.id||'')}"><label><span class="label-text">知识标题</span><input name="title" value="${esc(item.title||'')}" required maxlength="120" placeholder="例如：圣诞蜡烛上品检查流程"></label><label><span class="label-text">分类（选填）</span><input name="category" value="${esc(item.category||'')}" maxlength="60" placeholder="操作流程、产品知识、团队经验…"></label><label><span class="label-text">知识内容</span><textarea name="content" class="knowledge-editor" required maxlength="100000" placeholder="写下流程、经验、注意事项和可以核对的依据…">${esc(item.content||'')}</textarea></label><p class="field-note">手写条目最多 10 万字，由本机持久保存，已登录团队成员可查阅。发送相关片段给 AI 需单独开启授权。</p><p id="knowledge-form-error" class="chat-error" hidden role="alert"></p></form>`:`<div class="knowledge-detail-meta">${item.category?`<span class="badge">${esc(item.category)}</span>`:''}<span>${esc(item.owner||'团队资料')} · ${esc(dateText(item.updated_at,true))}</span></div><h2 class="knowledge-detail-title">${esc(item.title||'未命名知识')}</h2><p class="field-note">${esc(item.source_name||'手写知识条目')} · ${Number(item.characters||String(item.content||'').length).toLocaleString()} 字</p><div class="knowledge-document">${esc(item.content||'暂无内容')}</div>`}</div><footer class="drawer-footer"><button class="button ghost" data-action="close-overlay">${editing?'取消编辑':'关闭'}</button>${editing?`<button class="button primary" type="submit" form="knowledge-form">${icon('check')}保存知识</button>`:item.can_edit?`<button class="button primary" data-action="knowledge-edit" data-id="${esc(item.id)}">${icon('edit')}编辑</button>`:''}</footer></section>`);
}
async function openKnowledge(id,editing=false){const request=++state.knowledgeOverlayRequest,user=state.user?.id,route=state.routeRequest;const item=await api('/api/knowledge/documents/'+encodeURIComponent(id));if(request!==state.knowledgeOverlayRequest||user!==state.user?.id||route!==state.routeRequest||state.route!=='ai')return;knowledgeDialog(item,editing&&Boolean(item.can_edit));}
async function saveKnowledge(form){const user=state.user?.id,route=state.routeRequest,editor=state.knowledgeEditor,overlay=state.knowledgeOverlayRequest,fd=new FormData(form),button=$('button[form="knowledge-form"]');const body={title:String(fd.get('title')||'').trim(),category:String(fd.get('category')||'').trim(),content:String(fd.get('content')||'').trim()};if(!body.title||!body.content)throw new Error('请填写知识标题与内容。');setBusy(button,true,'保存中');try{await api('/api/knowledge/documents'+(form.dataset.id?'/'+encodeURIComponent(form.dataset.id):''),{method:form.dataset.id?'PATCH':'POST',body});if(user!==state.user?.id||route!==state.routeRequest||editor!==state.knowledgeEditor||overlay!==state.knowledgeOverlayRequest)return;closeOverlay();toast('知识已保存');if(state.assistantTab==='knowledge')await renderKnowledge();}catch(error){if(user===state.user?.id&&editor===state.knowledgeEditor&&overlay===state.knowledgeOverlayRequest){const box=$('#knowledge-form-error');if(box){box.textContent=error.message+' 内容已保留，可修改后重试。';box.hidden=false;}}}finally{if(button?.isConnected)setBusy(button,false);}}
async function importKnowledge(files){if(state.knowledgeBusy)return;const entries=[...files];if(!entries.length)return;if(entries.length>6)throw new Error('每次最多导入 6 个知识文件。');let total=0;for(const file of entries){if(!/\.(txt|md|csv|json|pdf|docx|xlsx)$/i.test(file.name))throw new Error('请选择 TXT、MD、CSV、JSON、PDF、DOCX 或 XLSX。');if(!file.size||file.size>10*1024*1024)throw new Error('单个文件需有内容，且不能超过 10 MB。');total+=file.size;}if(total>20*1024*1024)throw new Error('每次导入合计不能超过 20 MB。');const body=new FormData();entries.forEach(file=>body.append('files',file,file.name));const user=state.user?.id,route=state.routeRequest;state.knowledgeBusy=true;try{const result=await api('/api/knowledge/documents/import',{method:'POST',body});if(user!==state.user?.id||route!==state.routeRequest)return;toast(`已导入 ${result.items?.length||entries.length} 份知识${result.items?.some(item=>item.truncated)?'，部分资料已标记截断，请核对':''}`);state.knowledgeBusy=false;if(state.assistantTab==='knowledge')await renderKnowledge();}finally{if(user===state.user?.id)state.knowledgeBusy=false;}}
async function setKnowledgeActive(id,restore=false){const user=state.user?.id,route=state.routeRequest;await api('/api/knowledge/documents/'+encodeURIComponent(id)+(restore?'/restore':''),{method:restore?'POST':'DELETE'});if(user!==state.user?.id||route!==state.routeRequest)return;toast(restore?'知识已恢复':'知识已停用，可在已停用资料中恢复');if(state.assistantTab==='knowledge')await renderKnowledge();}
function chatMessages(){
  return [...state.chatHistory,...(state.chatPending?[state.chatPending]:[])].map(m=>`<div class="chat-message ${m.role}">${m.role==='assistant'?'<div class="message-label">MIDOO ASSISTANT</div>':''}${m.attachments?.length?`<div class="chat-message-attachments">${m.attachments.map(item=>chatAttachmentCard(item)).join('')}</div>`:''}${m.role==='assistant'?`<div class="chat-markdown">${chatMarkdown(m.content)}</div>${chatEvidence(m)}`:esc(m.content)}</div>`).join('')+(state.chatSending?'<div class="chat-message assistant"><span class="spinner"></span><span class="chat-wait-text">正在阅读资料与思考，请稍候…</span></div>':'');
}
function scrollChat(){const el=$('#chat-messages');if(el)el.scrollTop=el.scrollHeight;}
async function sendChat(message){
  if(state.chatSending)return;message=String(message||'').trim();if(!message&&!state.chatFiles.length)return;
  if(!aiReady()){toast('请先配置并启用模型服务',true);return;}
  if(state.chatFiles.some(item=>item.kind==='image')&&!activeAI()?.vision){toast('当前模型未允许图片识别，请在模型设置中开启或移除图片附件。',true);return;}
  if(state.chatSearchEnabled&&(!state.aiConfig?.web_search?.available||!state.chatWebQuery.trim())){state.chatError='请填写本次公开搜索关键词（最多 128 字）。';updateChatControls();return;}
  state.chatDraft=message;state.chatError='';const generation=state.chatGeneration,files=state.chatFiles.slice(),effort=chatEffort();
  const history=state.chatHistory.slice(-10).map(m=>({role:m.role,content:m.content,...(m.role==='assistant'&&m.source_refs?{source_refs:m.source_refs}:{}),...(m.attachments?.length?{attachments:m.attachments.map(a=>({id:a.id,name:a.name,kind:a.kind}))}:{})}));
  const content=message||'请根据这些附件，概括主要内容，并给出有依据的分析和建议。';
  const pending={role:'user',content,attachments:files.map(item=>({name:item.name,kind:item.kind,preview:item.preview}))};
  state.chatPending=pending;state.chatSending=true;if($('#chat-messages'))$('#chat-messages').innerHTML=chatMessages();updateChatControls();scrollChat();
  let body={message:content,history,reasoning_effort:effort,mode:state.chatMode,search_enabled:state.chatSearchEnabled,web_query:state.chatSearchEnabled?state.chatWebQuery.trim():''};
  if(files.length){body=new FormData();body.append('message',content);body.append('history',JSON.stringify(history));body.append('reasoning_effort',effort);body.append('mode',state.chatMode);body.append('search_enabled',String(state.chatSearchEnabled));body.append('web_query',state.chatSearchEnabled?state.chatWebQuery.trim():'');files.forEach(item=>body.append('files',item.file,item.name));}
  try{
    const result=await api('/api/ai/chat',{method:'POST',body});if(generation!==state.chatGeneration)return;
    pending.attachments=(result.attachments||[]).map((item,index)=>({...item,preview:files[index]?.preview||''}));
    state.chatHistory.push(pending,{role:'assistant',content:result.reply||'服务未返回文字内容，请检查模型设置。',product_cards:result.product_cards||[],sources:result.sources||[],web_search:result.web_search||null,notices:result.notices||[],source_refs:result.source_refs||null});state.chatFiles=[];state.chatDraft='';if($('#chat-input'))$('#chat-input').value='';
  }catch(error){if(generation!==state.chatGeneration)return;state.chatError='本次未发送成功：'+error.message+' 草稿和附件已保留，可以重试。';toast(error.message,true);}
  finally{if(generation===state.chatGeneration){state.chatPending=null;state.chatSending=false;if(state.route==='ai'&&$('#chat-messages')){$('#chat-messages').innerHTML=chatMessages();renderChatAttachments();updateChatControls();scrollChat();}}}
}

async function renderSettings(){
  if(!state.aiConfig||state.settingsTab==='ai')state.aiConfig=await api('/api/ai/config');
  $('#content').innerHTML=titleBlock('把工作空间，调整到顺手','管理模型服务、团队账号与本地数据，保持共享库稳定运行。','','WORKSPACE SETTINGS')+`<div class="settings-layout"><nav class="panel settings-nav" aria-label="设置分类">${[['ai','sparkles','AI 模型服务'],['system','server','局域网与备份'],['team','users','团队账号'],['account','user','我的账号']].map(([tab,i,label])=>`<button class="settings-tab ${state.settingsTab===tab?'active':''}" data-settings-tab="${tab}">${icon(i)}${label}</button>`).join('')}</nav><div class="settings-content" id="settings-content">${loading()}</div></div>`+footer();
  if(state.settingsTab==='ai')renderAISettings();if(state.settingsTab==='system')await renderSystemSettings();if(state.settingsTab==='team')await renderTeamSettings();if(state.settingsTab==='account')renderAccountSettings();
}
function renderAISettings(){const config=state.aiConfig;const providers=config.providers||[];if(!providers.length){$('#settings-content').innerHTML='<div class="panel empty-state"><h3>模型配置暂不可用</h3></div>';return;}const p=providers.find(p=>p.id===state.providerId)||providers[0];state.providerId=p.id;const admin=state.user.role==='admin';$('#settings-content').innerHTML=`<section class="panel"><header class="panel-head"><div class="panel-title">${icon('sparkles')}模型服务连接</div><span class="badge">可选功能</span></header><div class="panel-body"><div class="notice" style="margin-bottom:21px">${icon('info')}基础入库与图片检索无需配置 API。仅在发送问题、AI 命名、AI 查重或测试连接时调用云端服务，费用由所选服务商收取。</div><div class="provider-tabs">${providers.map(x=>`<button class="provider-tab ${p.id===x.id?'active':''}" data-provider="${esc(x.id)}">${esc(x.label)}${x.configured?'<span class="dot"></span>':''}</button>`).join('')}</div><div class="between"><h3 class="provider-title">${esc(p.label)}</h3><span class="config-indicator ${p.configured?'ready':''}"><span class="dot"></span>${p.configured?'已保存密钥':'尚未配置密钥'}</span></div><div class="provider-meta">${esc(p.protocol||'OpenAI compatible')}${p.docs_url?` · <a href="${esc(safeLink(p.docs_url))}" target="_blank" rel="noopener noreferrer">官方接入文档 ${icon('link','small')}</a>`:''}</div><form id="provider-form" data-provider="${esc(p.id)}"><div class="form-grid"><label class="full"><span class="label-text">服务地址 Base URL</span><input name="base_url" type="url" value="${esc(p.base_url||'')}" placeholder="${admin?'https://api.example.com/v1':'由工作空间管理员维护'}" required ${!admin?'disabled':''}><p class="field-note">${admin?'按服务商官方文档填写，不包含最终的聊天接口路径。':'服务地址由管理员维护，普通成员可查看模型启用状态。'}</p></label><label class="full"><span class="label-text">模型名称</span><input name="model" value="${esc(p.model)}" placeholder="填写此账号实际可用的模型 ID" required maxlength="150" ${!admin?'disabled':''}></label><label class="full"><span class="label-text">API 密钥</span><input name="api_key" type="password" autocomplete="new-password" placeholder="${p.configured?'已保存，留空则保留原密钥':'填入服务商提供的 API 密钥'}" ${!admin?'disabled':''}><p class="field-note">密钥在主机保存，不回显到页面，也不会保存在浏览器本地。</p></label><label class="check-label"><input type="checkbox" name="enabled" ${p.enabled||!p.configured?'checked':''} ${!admin?'disabled':''}>启用此服务</label><label class="check-label"><input type="checkbox" name="active" ${config.active_provider===p.id?'checked':''} ${!admin?'disabled':''}>设为 AI 管家默认服务</label><label class="check-label full"><input type="checkbox" name="vision" ${p.vision?'checked':''} ${!admin?'disabled':''}>此模型支持视觉，并允许 AI 聊天 / AI 命名 / AI 查重发送图片</label><p class="field-note full" style="margin-top:-12px">请先确认所填模型支持图片输入。开启后，在 AI 聊天主动发送图片附件，或点击 AI 命名、AI 查重时发送对应图片；本地查重仍在主机完成。</p><label class="check-label full"><input type="checkbox" name="share_library_context" ${config.share_library_context?'checked':''} ${!admin?'disabled':''}>允许 AI 管家发送必要的商品文字摘要</label><p class="field-note full" style="margin-top:-12px">关闭时不发送商品资料；公司知识由下方独立开关控制。开启后，相关名称、规格、负责人等摘要会发送给所选服务商。</p><label class="check-label full"><input type="checkbox" name="share_knowledge_context" ${config.share_knowledge_context?'checked':''} ${!admin?'disabled':''}>允许 AI 管家发送相关公司知识文字片段</label><p class="field-note full" style="margin-top:-12px">知识在本机检索，只把与问题相关的片段交给当前模型；关闭后仍可本机管理和查阅知识。</p><label class="check-label full"><input type="checkbox" name="allow_web_search" ${config.allow_web_search?'checked':''} ${!admin?'disabled':''}>允许在对话中使用 DeepSeek 联网搜索</label><p class="field-note full" style="margin-top:-12px">需配置并启用官方 DeepSeek 服务。仅发送用户填写的公开关键词，可能产生额外搜索费用；每次对话可单独选择是否联网。</p><label class="full"><span class="label-text">每日请求次数上限</span><input type="number" name="daily_request_limit" value="${Number(config.daily_request_limit||100)}" min="1" max="10000" ${!admin?'disabled':''}><p class="field-note">今天已请求 ${Number(config.requests_today||0)} 次。次数限制不等于服务商账单金额限制。</p></label></div><div class="form-actions">${p.configured&&admin?'<button class="button ghost sm" type="button" data-action="clear-provider-key" data-id="'+esc(p.id)+'">移除密钥</button>':''}<span class="spacer"></span><button class="button" type="button" data-action="test-provider" data-id="${esc(p.id)}" ${!p.configured||!admin?'disabled':''}>${icon('refresh')}测试连接</button><button class="button primary" type="submit" ${!admin?'disabled':''}>${icon('check')}保存设置</button></div>${!admin?'<p class="field-note">模型服务设置由管理员维护。</p>':''}</form></div></section><div class="notice" style="margin-top:18px">${icon('shield')}${config.share_library_context?'已允许发送必要商品文字摘要。AI 图片功能还需单独开启视觉授权。':'商品摘要发送已关闭。'}${config.share_knowledge_context?'已允许发送相关公司知识片段。':'公司知识片段发送未开启。'}${config.allow_web_search?'联网仍需在每次对话中主动选择并填写公开关键词。':''}AI 建议需核实；命名先预览再采用，查重仍由人工确认。</div>`;}
async function saveProvider(form){const button=$('button[type=submit]',form);const fd=new FormData(form);const current=state.aiConfig.providers.find(p=>p.id===form.dataset.provider);const item={id:current.id,base_url:fd.get('base_url'),model:fd.get('model'),enabled:fd.has('enabled'),vision:fd.has('vision')};if(fd.get('api_key'))item.api_key=fd.get('api_key');const body={providers:[item],daily_request_limit:Number(fd.get('daily_request_limit')),share_library_context:fd.has('share_library_context'),share_knowledge_context:fd.has('share_knowledge_context'),allow_web_search:fd.has('allow_web_search')};if(fd.has('active'))body.active_provider=current.id;setBusy(button,true,'保存中');try{await api('/api/ai/config',{method:'PUT',body});state.aiConfig=await api('/api/ai/config');resetChatState();renderAISettings();toast('模型设置已保存');}catch(error){toast(error.message,true);setBusy(button,false);}}
async function renderSystemSettings(){const s=await api('/api/system');state.settingsData=s;const urls=Array.isArray(s.lan_urls)?s.lan_urls:[];$('#settings-content').innerHTML=`<section class="panel"><header class="panel-head"><div class="panel-title">${icon('server')}本地共享服务</div><span class="badge listed"><span class="dot"></span>服务已连接</span></header><div class="panel-body"><p class="small muted" style="margin-bottom:12px">团队电脑连接同一个公司网络后，在浏览器打开下面的局域网地址即可访问。</p>${networkRow('本机地址',s.local_url||location.origin)}${urls.map((u,i)=>networkRow(i===0?'局域网地址':'可用地址',u)).join('')}${!urls.length?'<p class="field-note">未发现可用局域网地址，请检查主机网络连接。也可以使用主机的 IPv4 地址加当前端口访问。</p>':''}${networkRow('资料位置',s.data_path||'主机 data 目录',false)}${networkRow('软件版本',s.version||'1.0.0',false)}<div class="notice amber" style="margin-top:18px">${icon('info')}主机需要保持开机，团队才能访问共享库。若其他电脑无法连接，请检查是否处于同一网络，以及主机防火墙的专用网络访问权限。</div></div></section><section class="panel"><header class="panel-head"><div class="panel-title">${icon('shield')}数据备份</div><span class="tiny muted">商品资料 + 原始图片</span></header><div class="panel-body"><div class="backup-panel"><span class="choice-icon">${icon('download')}</span><div><h3 style="font-size:12px;color:#7c8aa3">下载完整数据副本</h3><p>备份包含数据库及商品图片，建议保存到另一台设备。</p></div><button class="button primary" data-action="backup" ${state.user.role!=='admin'?'disabled':''}>${icon('download')}下载备份</button></div><p class="field-note" style="margin-top:17px">${s.last_backup?'最近备份：'+esc(dateText(s.last_backup,true)):'还没有备份记录。'}${state.user.role!=='admin'?'备份由管理员执行。':''}</p></div></section>`;}
function networkRow(label,value,copy=true){return `<div class="network-row"><span>${esc(label)}</span><code>${esc(value)}</code>${copy?`<button class="icon-button" data-action="copy" data-value="${esc(value)}" title="复制地址" aria-label="复制${esc(label)}">${icon('copy')}</button>`:''}</div>`;}
async function renderTeamSettings(){const data=await api('/api/team');$('#settings-content').innerHTML=`<section class="panel"><header class="panel-head"><div class="panel-title">${icon('users')}团队账号</div>${state.user.role==='admin'?'<button class="button primary sm" data-action="new-member">'+icon('plus')+'添加成员</button>':''}</header><table class="team-list"><thead><tr><th>账号名称</th><th>角色</th><th>创建时间</th></tr></thead><tbody>${(data.items||[]).map(u=>`<tr><td><span class="flex gap8"><span class="avatar">${esc(u.name?.slice(0,1))}</span>${esc(u.name)}${String(u.id)===String(state.user.id)?'<span class="badge">我</span>':''}</span></td><td>${u.role==='admin'?'管理员':'团队成员'}</td><td>${dateText(u.created_at)}</td></tr>`).join('')}</tbody></table></section><div class="notice" style="margin-top:18px">${icon('info')}每位运营使用自己的账号，入库人和操作记录会自动留存。请通过独立账号共享工作空间。</div>`;}
function renderAccountSettings(){$('#settings-content').innerHTML=`<section class="panel"><header class="panel-head"><div class="panel-title">${icon('user')}我的账号</div><span class="badge">${state.user.role==='admin'?'管理员':'团队成员'}</span></header><div class="panel-body"><div class="flex gap12" style="margin-bottom:25px"><span class="avatar" style="background:#eef1fc;color:#8b9cd1;width:42px;height:42px">${esc(state.user.name?.slice(0,1))}</span><div><h3 style="font-size:14px;color:#7c8aa3">${esc(state.user.name)}</h3><p class="field-note">当前已登录公司工作空间</p></div></div><form id="password-form" class="stack"><label><span class="label-text">当前密码</span><input type="password" name="old_password" autocomplete="current-password" required></label><div class="form-grid"><label><span class="label-text">新密码</span><input type="password" name="new_password" autocomplete="new-password" minlength="8" required maxlength="128"></label><label><span class="label-text">确认新密码</span><input type="password" name="confirm_password" autocomplete="new-password" minlength="8" required maxlength="128"></label></div><div class="form-actions"><button class="button primary" type="submit">${icon('lock')}更新密码</button></div></form></div></section>`;}
function newMember(){openOverlay(`<section class="modal" role="dialog" aria-modal="true" aria-label="添加团队成员"><header class="drawer-header"><h2>添加团队成员</h2><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon('close')}</button></header><form id="member-form"><div class="panel-body stack"><label><span class="label-text">账号名称</span><input name="name" required minlength="2" maxlength="40" placeholder="运营的姓名或常用名称" autocomplete="off"></label><label><span class="label-text">初始密码</span><input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位，由成员登录后自行修改"></label><label><span class="label-text">成员角色</span><select name="role"><option value="operator">团队成员</option><option value="admin">管理员</option></select></label></div><footer class="drawer-footer"><span></span><button class="button primary" type="submit">${icon('plus')}创建账号</button></footer></form></section>`,true);}


function aiReviewBlock(review){const labels={same:'疑似同款',similar:'相似款',different:'差异明显',uncertain:'待核实'};return `<div class="ai-review ${esc(review.decision)}"><strong>${icon('sparkles','small')}AI 复核 · ${esc(labels[review.decision]||'待核实')}</strong><p>${esc(review.reason)}</p>${Array.isArray(review.differences)&&review.differences.length?`<ul>${review.differences.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`:''}<small>模型建议，请结合人工核对结果判断</small></div>`;}
async function generateTitle(id){
  const g=state.groups.find(g=>g.id===id);if(!g||state.titleBusy.has(id)||!aiReady())return;
  const originalName=g.name;state.titleBusy.add(id);renderImport();
  try{const form=new FormData();if(activeAI()?.vision)g.files.slice(0,3).forEach(im=>form.append('images',im.file,im.file.name));form.append('metadata',JSON.stringify({name:g.name,specs:g.specs||'',notes:g.notes||''}));const result=await api('/api/ai/title',{method:'POST',body:form});if(state.groups.includes(g)){g.aiSuggestion=result.title;g.aiOriginalName=originalName;toast('标题建议已生成，点击采用即可使用');}}
  catch(error){toast(error.message,true);}
  finally{state.titleBusy.delete(id);if(state.route==='import'&&!state.importing)renderImport();}
}
function renderListingDraft(rows,focusSelector=''){
  const body=$('.drawer-body',overlayRoot)||$('.panel-body',overlayRoot),scroll=body?.scrollTop||0;
  $('#listing-editor').innerHTML=listingEditor(rows);
  if($('#product-edit-form'))state.detailListings=rows;
  if(body)body.scrollTop=scroll;
  if(focusSelector)$$(focusSelector,overlayRoot).at(-1)?.focus();
}
function updateListingEditor(add,index){
  const rows=readListings();
  if(add){if(rows.length>=100){toast('每个商品最多100条 SKU 记录',true);return;}rows.push(blankListing());}
  else {if(!Number.isInteger(index)||index<0||index>=rows.length)return;listingRemoved.push({index,rows:[copyListingDraft(rows[index])]});rows.splice(index,1);}
  renderListingDraft(rows,add?'[data-group-field="platform"]':'');
}
function addListingSku(groupKey){
  const rows=readListings(),members=rows.filter(row=>row._editorGroup===groupKey);
  if(!members.length)return;
  if(rows.length>=100){toast('每个商品最多100条 SKU 记录',true);return;}
  const parent=members[0],newSku=blankListing();
  for(const field of listingSharedFields)newSku[field]=String(parent[field]||'');
  newSku.currency=parent.currency||'CNY';setListingGroup(newSku,groupKey);
  rows.splice(rows.lastIndexOf(members.at(-1))+1,0,newSku);
  renderListingDraft(rows,`[data-listing-group-key="${groupKey}"] [data-listing-field="product_code"]`);
}
function removeListingGroup(groupKey){
  const rows=readListings(),index=rows.findIndex(row=>row._editorGroup===groupKey);
  if(index<0)return;
  listingRemoved.push({index,rows:rows.filter(row=>row._editorGroup===groupKey).map(copyListingDraft)});
  renderListingDraft(rows.filter(row=>row._editorGroup!==groupKey));
}
function undoListingRemoval(){
  const removed=listingRemoved.at(-1);if(!removed)return;
  const rows=readListings();
  if(rows.length+removed.rows.length>100){toast('恢复后将超过100条 SKU，请先删除多余草稿。',true);return;}
  const restored=removed.rows.map(row=>{
    const copy=copyListingDraft(row),current=rows.find(item=>item._editorGroup===copy._editorGroup);
    if(current)for(const field of listingSharedFields)copy[field]=current[field]||'';
    return copy;
  });
  rows.splice(Math.min(removed.index,rows.length),0,...restored);listingRemoved.pop();renderListingDraft(rows);
}

document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-route]');if(nav){event.preventDefault();await navigate(nav.dataset.route);return;}
  const tab=event.target.closest('[data-settings-tab]');if(tab){state.settingsTab=tab.dataset.settingsTab;await navigate('settings');return;}
  const provider=event.target.closest('[data-provider]:not(form)');if(provider){state.providerId=provider.dataset.provider;renderAISettings();return;}
  if(event.target.matches('[data-overlay-backdrop]')){closeOverlay();return;}
  const drop=event.target.closest('#search-drop');if(drop&&!event.target.closest('button')&&!state.queryFiles.length){$('#query-files').click();return;}
  const importDrop=event.target.closest('#import-drop');if(importDrop){$('#import-files').click();return;}
  const workflow=event.target.closest('[data-workflow]');if(workflow){state.libraryStatus=workflow.dataset.workflow;state.libraryOffset=0;await renderLibrary();return;}
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const action=button.dataset.action;
  try{

    if(action==='run-ai-search')await runSearch(true);
    if(action==='ai-title')await generateTitle(Number(button.dataset.id));
    if(action==='accept-title'){const g=state.groups.find(g=>g.id===Number(button.dataset.id));if(g?.aiSuggestion){g.name=g.aiSuggestion;g.aiSuggestion='';renderImport();}}
    if(action==='toggle-listing-status'){await toggleListingStatus(button);return;}
    if(action==='add-listing')updateListingEditor(true);
    if(action==='remove-listing')updateListingEditor(false,Number(button.dataset.index));
    if(action==='add-listing-sku')addListingSku(button.dataset.groupKey);
    if(action==='remove-listing-group')removeListingGroup(button.dataset.groupKey);
    if(action==='undo-listing-removal')undoListingRemoval();
    if(action==='edit-card'){await showDetail(button.dataset.id);if(state.detail?.id===button.dataset.id){listingRemoved=[];state.detailListings=structuredClone(state.detail.listings||[]);state.detailEditing=true;renderDetail();}}
    if(action==='bulk-delete')removeProducts([...state.selectedProducts]);
    if(action==='toggle-trash'){state.trashMode=!state.trashMode;state.libraryOffset=0;await renderLibrary();}
    if(action==='restore-product'){setBusy(button,true);try{await api('/api/products/'+encodeURIComponent(button.dataset.id)+'/restore',{method:'POST'});closeOverlay();await refreshStats();await renderLibrary();toast('商品已恢复');}finally{if(button.isConnected)setBusy(button,false);}}
    if(action==='reset-library'){state.libraryQuery='';state.libraryStatus='';state.libraryPlatform='';state.listingStatus='';state.libraryCategory='';state.libraryOffset=0;await renderLibrary();}
    if(action==='reload')location.reload();
    if(action==='toggle-menu'){$('#sidebar')?.classList.toggle('open');$('.mobile-nav-backdrop')?.classList.toggle('show');}
    if(action==='close-overlay')closeOverlay();
    if(action==='logout'){await api('/api/logout',{method:'POST'});state.user=null;clearTimeout(searchStatusTimer);searchStatusRequest++;resetChatState();state.chatProviderKey='';resetActivityState();state.groups.forEach(g=>g.files.forEach(im=>URL.revokeObjectURL(im.url)));state.groups=[];state.selectedGroups.clear();state.importResult=null;state.aiConfig=null;state.detail=null;state.detailListings=[];state.products=[];state.selectedProducts.clear();state.trashMode=false;state.titleBusy.clear();clearQuery();renderAuth(false);}
    if(action==='choose-query'){$('#query-files').click();event.stopPropagation();}
    if(action==='remove-query'){if(state.searching)return;const i=Number(button.dataset.index);URL.revokeObjectURL(state.queryUrls[i]);state.queryFiles.splice(i,1);state.queryUrls.splice(i,1);state.searchResult=null;renderSearch();loadRecentSnippet();}
    if(action==='clear-query'){if(state.searching)return;clearQuery();renderSearch();loadRecentSnippet();}
    if(action==='run-search')await runSearch();
    if(action==='toggle-search-additional'){state.showAdditional=!state.showAdditional;$('#search-results').innerHTML=searchResults();}
    if(action==='refresh-search-status')await refreshSearchStatus();
    if(action==='reindex-search')await reindexSearch(button);
    if(action==='reconsider-search-item')await reconsiderSearchItem(button);
    if(action==='query-to-import'){state.importStatus='pending';if(state.queryFiles.length)addImportFiles(state.queryFiles,true);}
    if(action==='decision')await decision(button);
    if(action==='choose-import')$('#import-files').click();
    if(action==='choose-folder')$('#import-folder').click();
    if(action==='clear-import')confirmDialog('清空这次待入库商品？','只会清除尚未提交的图片分组，不会删除商品库里的资料。',()=>{state.groups.forEach(g=>g.files.forEach(im=>URL.revokeObjectURL(im.url)));state.groups=[];state.selectedGroups.clear();state.importResult=null;renderImport();});
    if(action==='remove-group'){const id=Number(button.dataset.id),g=state.groups.find(g=>g.id===id);g?.files.forEach(im=>URL.revokeObjectURL(im.url));state.groups=state.groups.filter(g=>g.id!==id);state.selectedGroups.delete(id);renderImport();}
    if(action==='merge-groups'){const selected=state.groups.filter(g=>state.selectedGroups.has(g.id));if(selected.length<2)return;if(selected.reduce((n,g)=>n+g.files.length,0)>30){toast('每个商品最多 30 张图片，无法合并这么多图片。',true);return;}const first=selected[0];first.files=selected.flatMap(g=>g.files);first.category=selected.every(g=>(g.category||'')===(first.category||''))?(first.category||''):'';first.notes=selected.map(g=>g.notes).filter(Boolean).join('\n');first.specs=selected.map(g=>g.specs).filter(Boolean).join('\n');first.listings=[...new Map(selected.flatMap(g=>g.listings||[]).map(l=>[JSON.stringify(l),l])).values()];state.groups=state.groups.filter(g=>!state.selectedGroups.has(g.id)||g.id===first.id);state.selectedGroups.clear();renderImport();toast('已合并为一个商品，可继续调整名称');}
    if(action==='split-group'){const g=state.groups.find(g=>g.id===Number(button.dataset.id));const index=state.groups.indexOf(g);const split=g.files.map(im=>({id:++groupCounter,name:im.file.name.replace(/\.[^.]+$/,''),files:[im],notes:g.notes,specs:g.specs,category:g.category||'',listings:structuredClone(g.listings||[])}));state.groups.splice(index,1,...split);state.selectedGroups.delete(g.id);renderImport();toast('已拆分为逐张图片分组');}
    if(action==='group-note')groupNotes(Number(button.dataset.id));
    if(action==='submit-import')await submitImport();
    if(action==='import-another'){state.importResult=null;renderImport();}
    if(action==='detail')await showDetail(button.dataset.id);
    if(action==='detail-image'){$('#detail-main-image').src=button.dataset.url;$$('.detail-thumbs button').forEach(b=>b.classList.toggle('active',b===button));}
    if(action==='edit-product'){listingRemoved=[];state.detailListings=structuredClone(state.detail.listings||[]);state.detailEditing=true;renderDetail();}
    if(action==='cancel-edit'){listingRemoved=[];state.detailEditing=false;renderDetail();}
    if(action==='delete-product')removeProducts([button.dataset.id]);
    if(action==='add-product-images')$('#product-more-images').click();
    if(action==='page-next'){state.libraryOffset+=24;await loadLibrary();window.scrollTo({top:0,behavior:'smooth'});}
    if(action==='page-prev'){state.libraryOffset=Math.max(0,state.libraryOffset-24);await loadLibrary();window.scrollTo({top:0,behavior:'smooth'});}
    if(action==='activity-range')await applyActivityFilters({...state.activityDraft,...activityDateRange(button.dataset.range)});
    if(action==='clear-activity-filters')await applyActivityFilters({});
    if(action==='retry-activity')await loadActivity();
    if(action==='activity-page'){state.activityOffset=Math.max(0,state.activityOffset+(button.dataset.direction==='next'?40:-40));await loadActivity();}
    if(action==='undo-batch')confirmDialog('撤销这一批商品导入？','仅撤销符合条件、尚未被后续修改的本批商品。已被其他操作引用的记录将由服务端检查。',async()=>{await api('/api/import/'+encodeURIComponent(button.dataset.id)+'/undo',{method:'POST'});await refreshStats();if(state.route==='activity')await loadActivity();else await navigate('activity');toast('已撤销这批导入');});
    if(action==='assistant-tab'){state.assistantTab=button.dataset.tab==='knowledge'?'knowledge':'chat';state.knowledgeRequest++;await renderAI();}
    if(action==='knowledge-new'){state.knowledgeOverlayRequest++;knowledgeDialog({},true);}
    if(action==='knowledge-view')await openKnowledge(button.dataset.id);
    if(action==='knowledge-edit')await openKnowledge(button.dataset.id,true);
    if(action==='knowledge-import')$('#knowledge-files')?.click();
    if(action==='knowledge-trash'){state.knowledgeDeleted=!state.knowledgeDeleted;await renderKnowledge();}
    if(action==='knowledge-reload')await renderKnowledge();
    if(action==='knowledge-disable')await setKnowledgeActive(button.dataset.id);
    if(action==='knowledge-restore')await setKnowledgeActive(button.dataset.id,true);
    if(action==='open-ai-settings'){state.settingsTab='ai';await navigate('settings');}
    if(action==='clear-chat')await clearChat();
    if(action==='choose-chat-files')$('#chat-files')?.click();
    if(action==='remove-chat-file')removeChatFile(Number(button.dataset.index));
    if(action==='chat-prompt')await sendChat(button.dataset.prompt);
    if(action==='new-member')newMember();
    if(action==='test-provider'){setBusy(button,true,'测试中');try{const r=await api('/api/ai/test',{method:'POST',body:{provider_id:button.dataset.id}});toast(r.message||'连接测试完成',r.ok===false);}finally{setBusy(button,false);}}
    if(action==='clear-provider-key')confirmDialog('移除这个服务的 API 密钥？','移除后，使用此服务的 AI 管家将暂停响应，直到管理员重新配置。',async()=>{await api('/api/ai/config',{method:'PUT',body:{providers:[{id:button.dataset.id,clear_key:true}]}});state.aiConfig=await api('/api/ai/config');renderAISettings();toast('密钥已移除');});
    if(action==='copy'){if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(button.dataset.value);toast('地址已复制');}else{openOverlay(`<section class="modal" role="dialog" aria-modal="true" aria-label="复制地址"><header class="drawer-header"><h2>复制访问地址</h2><button class="icon-button" data-action="close-overlay">${icon('close')}</button></header><div class="panel-body"><input readonly value="${esc(button.dataset.value)}" id="copy-address"><p class="field-note">选中后按 Ctrl + C 复制。</p></div></section>`,true);setTimeout(()=>$('#copy-address')?.select(),100);}}
    if(action==='backup'){setBusy(button,true,'打包中');try{const response=await api('/api/backup',{method:'POST'});const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`觅同备份-${new Date().toISOString().slice(0,10)}.zip`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('备份已生成，请保存到另一位置');}finally{setBusy(button,false);}}
  }catch(error){toast(error.message,true);}
});

document.addEventListener('submit',async event=>{
  const form=event.target;if(!(form instanceof HTMLFormElement))return;event.preventDefault();const fd=new FormData(form);const button=$('button[type=submit]',form)||$(`button[form="${form.id}"]`);let busy=false;
  try{
    if(form.id==='auth-form'){
      const setup=form.dataset.setup==='true';if(setup&&fd.get('password')!==fd.get('confirm_password'))throw new Error('两次输入的密码不一致。');setBusy(button,true,setup?'正在创建':'登录中');busy=true;
      await api(setup?'/api/setup':'/api/login',{method:'POST',body:{name:fd.get('name'),password:fd.get('password')}});const me=await api('/api/me');state.user=me.user||me;state.aiConfig=null;renderShell();await navigate('search');return;
    }
    if(form.id==='product-edit-form'){setBusy(button,true,'保存中');busy=true;const body={...Object.fromEntries(fd),listings:validateListings(readListings())};const p=await api('/api/products/'+encodeURIComponent(state.detail.id),{method:'PATCH',body});state.detail=p.product||p;state.detailEditing=false;renderDetail();await refreshStats();toast('商品资料已保存');if(state.route==='library')loadLibrary();}
    if(form.id==='group-note-form'){const g=state.groups.find(g=>g.id===Number(form.dataset.id));if(g){g.listings=validateListings(readListings());g.notes=fd.get('notes');g.specs=fd.get('specs');g.category=String(fd.get('category')||'').trim();}closeOverlay();renderImport();}
    if(form.id==='activity-filter-form')await applyActivityFilters(Object.fromEntries(fd));
    if(form.id==='provider-form')await saveProvider(form);
    if(form.id==='knowledge-form')await saveKnowledge(form);
    if(form.id==='knowledge-search-form'){state.knowledgeQuery=$('#knowledge-search').value.trim();await renderKnowledge();}
    if(form.id==='chat-form')await sendChat($('#chat-input').value);
    if(form.id==='member-form'){setBusy(button,true);busy=true;await api('/api/team',{method:'POST',body:Object.fromEntries(fd)});closeOverlay();await renderTeamSettings();toast('团队成员已添加');}
    if(form.id==='password-form'){if(fd.get('new_password')!==fd.get('confirm_password'))throw new Error('两次输入的新密码不一致。');setBusy(button,true);busy=true;await api('/api/me/password',{method:'PUT',body:{old_password:fd.get('old_password'),current_password:fd.get('old_password'),new_password:fd.get('new_password')}});form.reset();toast('密码已更新');}
  }catch(error){if(form.id==='auth-form')$('#auth-error').innerHTML=`<div class="form-error" role="alert">${esc(error.message)}</div>`;else toast(error.message,true);}
  finally{if(busy&&button?.isConnected)setBusy(button,false);}
});

document.addEventListener('change',async event=>{
  const el=event.target;try{
    if(el.id==='activity-actor')state.activityDraft.actor_id=el.value;
    if(el.id==='activity-start')state.activityDraft.start_date=el.value;
    if(el.id==='activity-end')state.activityDraft.end_date=el.value;
    if(el.id==='chat-files'){addChatFiles(el.files);el.value='';}
    if(el.id==='chat-mode'){state.chatMode=['auto','company','general'].includes(el.value)?el.value:'auto';const note=$('#chat-mode-note');if(note)note.textContent=chatModeNote();}
    if(el.id==='chat-search-enabled'){state.chatSearchEnabled=el.checked&&Boolean(state.aiConfig?.web_search?.available);renderChatSearch();}
    if(el.id==='knowledge-files'){try{await importKnowledge(el.files);}finally{el.value='';}}
    if(el.id==='chat-effort'){state.chatEfforts[chatProviderKey()]=el.value;}
    if(el.id==='query-files'){addQuery(el.files);el.value='';}
    if(el.id==='import-files'||el.id==='import-folder'){addImportFiles(el.files);el.value='';}
    if(el.dataset.groupSelect){const id=Number(el.dataset.groupSelect);el.checked?state.selectedGroups.add(id):state.selectedGroups.delete(id);renderImport();}
    if(el.id==='import-status'){state.importStatus=el.value;}

    if(el.id==='library-platform'){state.libraryPlatform=el.value;state.libraryOffset=0;await loadLibrary();}
    if(el.id==='listing-status'){state.listingStatus=el.value;state.libraryOffset=0;await loadLibrary();}
    if(el.id==='library-category'){state.libraryCategory=el.value;state.libraryOffset=0;await loadLibrary();}
    if(el.dataset.productSelect){el.checked?state.selectedProducts.add(el.dataset.productSelect):state.selectedProducts.delete(el.dataset.productSelect);renderSelection();}
    if(el.id==='select-all-products'){state.products.filter(mayEdit).forEach(p=>el.checked?state.selectedProducts.add(p.id):state.selectedProducts.delete(p.id));$$('[data-product-select]').forEach(input=>input.checked=state.selectedProducts.has(input.dataset.productSelect));renderSelection();}

    if(el.id==='product-more-images'){const files=validFiles(el.files);if(!files.length)return;const form=new FormData();files.forEach(f=>form.append('images',f,f.name));await api('/api/products/'+encodeURIComponent(state.detail.id)+'/images',{method:'POST',body:form});const p=await api('/api/products/'+encodeURIComponent(state.detail.id));state.detail=p.product||p;renderDetail();await refreshStats();toast('图片已补充');}
  }catch(error){toast(error.message,true);}
});
document.addEventListener('input',event=>{const el=event.target;if(el.id==='chat-input')state.chatDraft=el.value;if(el.id==='chat-web-query')state.chatWebQuery=el.value.slice(0,128);if(el.id==='library-search'){state.libraryQuery=el.value;state.libraryOffset=0;clearTimeout(searchDebounce);searchDebounce=setTimeout(loadLibrary,300);}if(el.dataset.groupName){const g=state.groups.find(g=>g.id===Number(el.dataset.groupName));if(g)g.name=el.value;}});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){closeOverlay();$('#sidebar')?.classList.remove('open');$('.mobile-nav-backdrop')?.classList.remove('show');}
  if(event.target.id==='chat-input'&&event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendChat(event.target.value);}
  if((event.key==='Enter'||event.key===' ')&&event.target.id==='search-drop'){event.preventDefault();$('#query-files').click();}
  if((event.key==='Enter'||event.key===' ')&&event.target.id==='import-drop'){event.preventDefault();$('#import-files').click();}
  if(event.key==='Tab'&&overlayRoot.firstElementChild){const focusable=$$('button:not(:disabled),input:not(:disabled):not([type=file]),textarea:not(:disabled),select:not(:disabled),a[href]',overlayRoot);const first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
});
document.addEventListener('paste',event=>{if(!state.user)return;const chat=state.route==='ai'&&Boolean(event.target.closest('#chat-form'));if(!chat&&(!['search','import'].includes(state.route)||event.target.closest('input,textarea,[contenteditable=true]')))return;const files=[...(event.clipboardData?.items||[])].filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile()).filter(Boolean);if(!files.length)return;event.preventDefault();if(chat)addChatFiles(files);else state.route==='search'?addQuery(files):addImportFiles(files,true);});
for(const type of ['dragenter','dragover'])document.addEventListener(type,event=>{if(event.dataTransfer?.types.includes('Files')){event.preventDefault();event.target.closest('.dropzone,#chat-drop')?.classList.add('dragover');}});
document.addEventListener('dragleave',event=>{const zone=event.target.closest('.dropzone,#chat-drop');if(zone&&!zone.contains(event.relatedTarget))zone.classList.remove('dragover');});
document.addEventListener('drop',async event=>{if(!event.dataTransfer?.files?.length&&!event.dataTransfer?.items?.length)return;event.preventDefault();$$('.dragover').forEach(z=>z.classList.remove('dragover'));if(!state.user)return;if(state.route==='ai'){if(event.target.closest('#chat-drop'))addChatFiles(event.dataTransfer.files);return;}try{const files=await readDroppedFiles(event.dataTransfer);if(state.route==='search')addQuery(files);else if(state.route==='import')addImportFiles(files);}catch(error){toast(error.message,true);}});
async function readDroppedFiles(dataTransfer){
  const entries=[...dataTransfer.items||[]].map(x=>x.webkitGetAsEntry?.()).filter(Boolean);if(!entries.some(e=>e.isDirectory))return [...dataTransfer.files];
  const files=[];async function walk(entry,path=''){if(files.length>=300)throw new Error('一次最多拖入 300 张图片，请分批导入。');if(entry.isFile){const file=await new Promise((resolve,reject)=>entry.file(resolve,reject));Object.defineProperty(file,'webkitRelativePath',{value:path+file.name});files.push(file);}else if(entry.isDirectory){const reader=entry.createReader();let batch;do{batch=await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));for(const e of batch)await walk(e,path+entry.name+'/');}while(batch.length);}}for(const entry of entries)await walk(entry);return files;
}
window.addEventListener('hashchange',()=>{if(state.user)navigate(location.hash.slice(1),false);});
document.addEventListener('midoo:price-tracking-saved',async event=>{if(!state.user)return;await refreshStats();if(state.user&&state.route==='library')await loadLibrary();const id=event.detail?.productId;if(id&&state.detail?.id===id&&!state.detailEditing&&$('.product-drawer',overlayRoot)){try{const fresh=await api('/api/products/'+encodeURIComponent(id));if(state.detail?.id===id&&!state.detailEditing&&$('.product-drawer',overlayRoot)){state.detail=fresh.product||fresh;renderDetailKeepingScroll();}}catch{}}});
init();

