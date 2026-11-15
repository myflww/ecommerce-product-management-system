/* Independent Canvas view of explicit company records and their real links.
 * No third-party graph code, remote assets, WebGL or automatic data changes. */
(function (host) {
  'use strict';
  const mounted = new WeakMap();
  const COLORS = {root:'#eff3ff',group:'#9baed8',category:'#c7adf6',product:'#8eabff',knowledge:'#79ddc4',sku:'#ebc685'};
  const KINDS = {root:'公司资料',group:'资料分组',category:'分类',product:'商品',knowledge:'知识条目',sku:'SKU'};
  const RELATIONS = {contains:'包含',classified_as:'分类归属',variant_of:'商品规格',documented_by:'资料关联'};
  const STATUS = {listed:'已上架',pending:'准备上架',archived:'已下架',draft:'草稿',published:'已发布'};
  const MAX_NODES = 300;
  let serial = 0;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = (value, limit=240) => typeof value === 'string' ? value.slice(0,limit) : '';
  const count = value => Number.isFinite(Number(value)) ? Math.max(0,Math.floor(Number(value))) : 0;
  const clamp = (value,min,max) => Math.max(min,Math.min(max,value));
  function hash(value) {
    let result=2166136261;
    for(const ch of String(value)){result^=ch.charCodeAt(0);result=Math.imul(result,16777619);}
    return (result>>>0)/4294967296;
  }
  function normalize(raw) {
    if(!raw || !Array.isArray(raw.nodes) || !Array.isArray(raw.edges))throw new Error('知识星图资料格式暂不可用，请重试。');
    const nodes=[],ids=new Set();
    for(const item of raw.nodes){
      if(nodes.length>=MAX_NODES)break;
      if(!item || typeof item.id!=='string' || !item.id || !Object.hasOwn(COLORS,item.kind) || ids.has(item.id))continue;
      const node={id:text(item.id,500),kind:item.kind,label:text(item.label)||KINDS[item.kind]};
      if(ids.has(node.id))continue;
      for(const key of ['source_id','product_id','category','code','product_number','variant_hint','region','platform','store','status','updated_at'])node[key]=text(item[key],500);
      if(typeof item.revision==='string'||typeof item.revision==='number')node.revision=String(item.revision).slice(0,60);
      nodes.push(node);ids.add(node.id);
    }
    const edges=[],edgeIds=new Set();
    for(const edge of raw.edges){
      if(edges.length>=1200)break;
      if(!edge || !ids.has(edge.source) || !ids.has(edge.target) || edge.source===edge.target || !Object.hasOwn(RELATIONS,edge.relation))continue;
      const key=JSON.stringify([edge.source,edge.target,edge.relation]);
      if(!edgeIds.has(key)){edgeIds.add(key);edges.push({source:edge.source,target:edge.target,relation:edge.relation});}
    }
    return {nodes,edges,totals:raw.totals||{},policy:raw.policy||{},truncated:Boolean(raw.truncated)||raw.nodes.length>MAX_NODES};
  }
  function layout(nodes,edges) {
    const byId=new Map(nodes.map(node=>[node.id,node])),adjacent=new Map(nodes.map(node=>[node.id,[]]));
    for(const edge of edges){adjacent.get(edge.source).push(edge.target);adjacent.get(edge.target).push(edge.source);}
    const origin=nodes.find(node=>node.kind==='root')||nodes[0],visited=new Set(),children=new Map(),depth=new Map();
    if(origin){
      const queue=[origin.id];visited.add(origin.id);depth.set(origin.id,0);
      for(let i=0;i<queue.length;i++){
        const id=queue[i];children.set(id,[]);
        for(const next of adjacent.get(id))if(!visited.has(next)){visited.add(next);depth.set(next,depth.get(id)+1);children.get(id).push(next);queue.push(next);}
      }
      const weights=new Map();
      for(let i=queue.length-1;i>=0;i--){const id=queue[i];weights.set(id,Math.max(1,children.get(id).reduce((sum,next)=>sum+weights.get(next),0)));}
      function place(id,start,end){
        const node=byId.get(id),d=depth.get(id),angle=(start+end)/2,radius=d===0?0:175+d*145;
        node.x=Math.cos(angle)*radius;node.y=Math.sin(angle)*radius;
        let cursor=start;
        for(const next of children.get(id)){const span=(end-start)*weights.get(next)/weights.get(id);place(next,cursor,cursor+span);cursor+=span;}
      }
      place(origin.id,-Math.PI/2,Math.PI*1.5);
    }
    const isolated=nodes.filter(node=>!visited.has(node.id));
    isolated.forEach((node,index)=>{const angle=(index/Math.max(1,isolated.length))*Math.PI*2;node.x=Math.cos(angle)*610;node.y=Math.sin(angle)*610;});
    // A tiny deterministic offset separates records occupying the same tree ray.
    nodes.forEach(node=>{if(node!==origin){node.x+=(hash(node.id+'x')-.5)*24;node.y+=(hash(node.id+'y')-.5)*24;}node.phase=hash(node.id)*Math.PI*2;});
    return {byId,adjacent};
  }
  function mount(options) {
    const {container,api,onOpenProduct,onOpenKnowledge,onAsk}=options||{};
    if(!container || typeof container.appendChild!=='function' || typeof api!=='function')throw new TypeError('知识星图需要容器和本地 API。');
    mounted.get(container)?.dispose();
    const doc=container.ownerDocument||host.document,id='mkn-'+(++serial),root=doc.createElement('section');
    root.className='mkn';root.setAttribute('aria-labelledby',id+'-title');
    root.innerHTML=`<header class="mkn-heading"><div><p class="mkn-eyebrow">CONNECTED COMPANY KNOWLEDGE</p><h2 id="${id}-title">公司知识星图</h2><p class="mkn-intro">从一个节点出发，找到商品、规格与知识的真实出处。</p></div><div class="mkn-totals" data-mkn="totals" aria-live="polite"></div></header>
      <div class="mkn-toolbar"><form class="mkn-search" data-mkn="search-form" role="search"><label class="mkn-sr" for="${id}-query">搜索星图资料</label><input id="${id}-query" data-mkn="query" type="search" maxlength="120" placeholder="搜索商品、SKU、分类或知识…"><button type="submit">搜索</button></form><div class="mkn-kind-filter" role="group" aria-label="星图资料类型"><button type="button" data-mkn-kind="all" aria-pressed="true">全部</button><button type="button" data-mkn-kind="product" aria-pressed="false">商品 / SKU</button><button type="button" data-mkn-kind="knowledge" aria-pressed="false">知识</button></div><button class="mkn-refresh" type="button" data-mkn-action="refresh">刷新本地图</button></div>
      <div class="mkn-body"><div class="mkn-stage" data-mkn="stage"><canvas data-mkn="canvas" tabindex="0" role="img" aria-label="商品与公司知识关系图。可拖动平移、滚轮缩放；键盘方向键平移，加减号缩放，Home重置。右侧节点清单提供同等访问。"></canvas><div class="mkn-stage-top"><span class="mkn-live-label"><i></i>真实资料 · 持续积累</span><span class="mkn-viewport-tools"><button type="button" data-mkn-action="zoom-out" aria-label="缩小星图">−</button><span data-mkn="zoom">100%</span><button type="button" data-mkn-action="zoom-in" aria-label="放大星图">＋</button><button type="button" data-mkn-action="reset">重置视野</button></span></div><div class="mkn-message" data-mkn="message" role="status" aria-live="polite"></div><div class="mkn-tooltip" data-mkn="tooltip" role="tooltip" hidden></div><div class="mkn-stage-bottom"><span>拖动平移 · 滚轮缩放 · 点击探索</span><button type="button" data-mkn-action="motion" aria-pressed="false">暂停动态</button></div></div>
      <aside class="mkn-side" aria-label="星图节点与出处"><div class="mkn-inspector" data-mkn="inspector"></div><section class="mkn-node-panel"><div class="mkn-node-heading"><h3>节点清单</h3><span data-mkn="list-count"></span></div><label class="mkn-sr" for="${id}-list-query">筛选当前节点清单</label><input id="${id}-list-query" data-mkn="list-query" type="search" maxlength="120" placeholder="在当前节点中查找…"><div class="mkn-node-list" data-mkn="node-list" role="group" aria-label="当前星图节点，可使用方向键选择"></div></section></aside></div>
      <footer class="mkn-foot"><div class="mkn-legend" aria-label="节点图例">${['product','knowledge','sku','category'].map(kind=>`<span><i class="mkn-dot mkn-dot-${kind}"></i>${KINDS[kind]}</span>`).join('')}</div><p data-mkn="summary" aria-live="polite"></p><p class="mkn-policy" data-mkn="policy">资料由团队人工维护。连线表示实际归属或人工关联。</p></footer>`;
    container.appendChild(root);
    const find = name=>root.querySelector(`[data-mkn="${name}"]`);
    const canvas=find('canvas'),stage=find('stage'),message=find('message'),tooltip=find('tooltip');
    let ctx=null;
    try{ctx=canvas.getContext('2d',{alpha:false});}catch{}
    let disposed=false,generation=0,controller=null,raf=0,searchTimer=0,lastFrame=0,time=0,observer=null;
    let graph={nodes:[],edges:[],totals:{},policy:{},truncated:false},index={byId:new Map(),adjacent:new Map()};
    let width=800,height=590,zoom=1,offsetX=0,offsetY=0,selected='',hover='',query='',kind='all',loading=false,pointer=null;
    const media=host.matchMedia?.('(prefers-reduced-motion: reduce)'),cleanups=[],sprites=new Map();
    let paused=Boolean(media?.matches),visibleNodes=[];
    function listen(target,type,fn,opts){target.addEventListener(type,fn,opts);cleanups.push(()=>target.removeEventListener(type,fn,opts));}
    function active(){return !disposed&&!doc.hidden&&!paused&&Boolean(ctx)&&graph.nodes.length>0;}
    function updateMotion(){const button=root.querySelector('[data-mkn-action="motion"]');button.textContent=paused?'动态已暂停':'暂停动态';button.setAttribute('aria-pressed',String(paused));}
    function stop(){if(raf){host.cancelAnimationFrame(raf);raf=0;}}
    function start(){if(active()&&!raf)raf=host.requestAnimationFrame(frame);}
    function frame(stamp){raf=0;if(!active())return;if(stamp-lastFrame>=40){time=stamp/1000;draw();lastFrame=stamp;}start();}
    function sprite(color){
      if(sprites.has(color))return sprites.get(color);
      const image=doc.createElement('canvas');image.width=image.height=64;
      const brush=image.getContext('2d');
      if(brush){const gradient=brush.createRadialGradient(32,32,0,32,32,32);gradient.addColorStop(0,color);gradient.addColorStop(.2,color+'8c');gradient.addColorStop(1,color+'00');brush.fillStyle=gradient;brush.fillRect(0,0,64,64);}
      sprites.set(color,image);return image;
    }
    function fit(){
      if(!graph.nodes.length){zoom=1;offsetX=offsetY=0;return;}
      const xs=graph.nodes.map(node=>node.x),ys=graph.nodes.map(node=>node.y);
      const left=Math.min(...xs)-70,right=Math.max(...xs)+70,top=Math.min(...ys)-70,bottom=Math.max(...ys)+70;
      zoom=clamp(Math.min((width-52)/Math.max(240,right-left),(height-130)/Math.max(240,bottom-top)),.18,1.25);
      offsetX=-(left+right)*zoom/2;offsetY=-(top+bottom)*zoom/2;
    }
    function point(node){return {x:width/2+offsetX+node.x*zoom,y:height/2+offsetY+node.y*zoom};}
    function draw(){
      if(disposed||!ctx)return;
      const dpr=clamp(host.devicePixelRatio||1,1,2);
      ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
      const background=ctx.createLinearGradient(0,0,width,height);background.addColorStop(0,'#111b31');background.addColorStop(.6,'#10192b');background.addColorStop(1,'#171d34');ctx.fillStyle=background;ctx.fillRect(0,0,width,height);
      for(let n=0;n<65;n++){const x=hash('star-x'+n)*width,y=hash('star-y'+n)*height;ctx.globalAlpha=.14+hash(n)*.3;ctx.fillStyle='#dce6ff';ctx.beginPath();ctx.arc(x,y,.45+hash('r'+n)*.7,0,Math.PI*2);ctx.fill();}
      ctx.globalAlpha=1;
      const focus=selected||hover,neighbors=new Set(focus?[focus,...(index.adjacent.get(focus)||[])]:[]);
      for(const node of graph.nodes){
        if(!['root','group','category'].includes(node.kind))continue;
        const p=point(node),radius=clamp(130*zoom,35,105),color=COLORS[node.kind];
        ctx.globalAlpha=.11;ctx.drawImage(sprite(color),p.x-radius,p.y-radius,radius*2,radius*2);
      }
      for(const edge of graph.edges){
        const a=point(index.byId.get(edge.source)),b=point(index.byId.get(edge.target));
        const highlighted=edge.source===focus||edge.target===focus;
        ctx.globalAlpha=focus?(highlighted?.72:.075):.23;ctx.strokeStyle=highlighted?'#b9cffb':'#7186b8';ctx.lineWidth=highlighted?1.25:.65;
        ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();
      }
      for(const node of graph.nodes){
        const p=point(node),isFocus=node.id===focus,structural=['root','group','category'].includes(node.kind);
        const radius=clamp((node.kind==='root'?12:structural?8:node.kind==='sku'?4.8:6)*Math.sqrt(zoom),node.kind==='root'?6:2.7,16);
        node.screenX=p.x;node.screenY=p.y;node.screenRadius=radius;
        if(p.x<-40||p.x>width+40||p.y<-40||p.y>height+40)continue;
        const color=COLORS[node.kind],alpha=focus&&!neighbors.has(node.id)?.24:1;
        const pulse=paused?1:1+Math.sin(time*.7+node.phase)*.06;
        ctx.globalAlpha=alpha*(isFocus?.85:.44);ctx.drawImage(sprite(color),p.x-radius*4*pulse,p.y-radius*4*pulse,radius*8*pulse,radius*8*pulse);
        ctx.globalAlpha=alpha;ctx.fillStyle=isFocus?'#ffffff':color;ctx.beginPath();ctx.arc(p.x,p.y,radius,0,Math.PI*2);ctx.fill();
        if(node.id===selected){ctx.strokeStyle=color;ctx.lineWidth=1.3;ctx.beginPath();ctx.arc(p.x,p.y,radius+5,0,Math.PI*2);ctx.stroke();}
        if(isFocus||structural||zoom>1.05){
          const label=node.label.length>17?node.label.slice(0,16)+'…':node.label;
          ctx.font=(isFocus?'600 ':'')+(structural?11:10)+'px "Segoe UI","Microsoft YaHei",sans-serif';ctx.textAlign='center';
          ctx.fillStyle='#0e1729';ctx.globalAlpha=.85;const size=ctx.measureText(label).width;ctx.fillRect(p.x-size/2-4,p.y+radius+7,size+8,18);
          ctx.globalAlpha=alpha;ctx.fillStyle=isFocus?'#fff':structural?'#d8e1f9':'#aabada';ctx.fillText(label,p.x,p.y+radius+20);
        }
      }
      ctx.globalAlpha=1;find('zoom').textContent=Math.round(zoom*100)+'%';
    }
    function resize(){
      if(disposed)return;
      const size=stage.getBoundingClientRect();width=Math.max(260,Math.round(size.width||800));height=Math.max(360,Math.round(size.height||590));
      const dpr=clamp(host.devicePixelRatio||1,1,2);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
      draw();
    }
    function zoomAt(factor,x=width/2,y=height/2){const next=clamp(zoom*factor,.18,4),ratio=next/zoom;offsetX=x-width/2-(x-width/2-offsetX)*ratio;offsetY=y-height/2-(y-height/2-offsetY)*ratio;zoom=next;draw();}
    function coordinates(event){const bounds=canvas.getBoundingClientRect();return {x:(event.clientX-bounds.left)*width/Math.max(1,bounds.width),y:(event.clientY-bounds.top)*height/Math.max(1,bounds.height)};}
    function hit(p){let found=null,best=Infinity;for(const node of graph.nodes){const distance=Math.hypot(node.screenX-p.x,node.screenY-p.y);if(distance<Math.max(11,node.screenRadius+5)&&distance<best){found=node;best=distance;}}return found;}
    function renderList(){
      const local=find('list-query').value.trim().toLocaleLowerCase();
      visibleNodes=graph.nodes.filter(node=>!local||[node.label,node.code,node.category,node.platform,node.store,KINDS[node.kind]].some(value=>String(value||'').toLocaleLowerCase().includes(local)));
      const selectedIndex=visibleNodes.findIndex(node=>node.id===selected),tabIndex=selectedIndex<0?0:selectedIndex;
      find('list-count').textContent=visibleNodes.length+' / '+graph.nodes.length;
      find('node-list').innerHTML=visibleNodes.length?visibleNodes.map((node,i)=>`<button type="button" class="mkn-node-btn ${node.id===selected?'is-selected':''}" data-mkn-node="${i}" tabindex="${i===tabIndex?0:-1}" aria-pressed="${node.id===selected}"><i class="mkn-dot mkn-dot-${node.kind}"></i><span>${escape(node.label)}<small>${escape(KINDS[node.kind])}${node.code?' · '+escape(node.code):''}</small></span></button>`).join(''):'<p class="mkn-no-nodes">当前清单中没有匹配节点。</p>';
    }
    function detailField(label,value){return value?`<dt>${label}</dt><dd>${escape(value)}</dd>`:'';}
    function renderInspector(){
      const node=index.byId.get(selected),panel=find('inspector');
      if(!node){panel.innerHTML='<p class="mkn-inspector-eyebrow">沿着资料，找到答案</p><h3>选择一个节点</h3><p class="mkn-inspector-copy">点击星图或下方清单，查看它的归属、关联与原始资料。</p><div class="mkn-empty-orbit" aria-hidden="true"><span></span><i></i><i></i><i></i></div><p class="mkn-readonly">此视图只读。资料长期积累，不因浏览次数或使用频率减少。</p>';return;}
      const neighbors=(index.adjacent.get(node.id)||[]).map(id=>index.byId.get(id));
      const origin=node.kind==='product'||node.kind==='knowledge'||node.kind==='sku';
      const sourceId=node.kind==='sku'?node.product_id:node.source_id;
      const date=node.updated_at?new Date(node.updated_at):null;
      const updated=date&&!Number.isNaN(date.getTime())?date.toLocaleString('zh-CN',{hour12:false}):node.updated_at;
      panel.innerHTML=`<p class="mkn-inspector-eyebrow"><i class="mkn-dot mkn-dot-${node.kind}"></i>${escape(KINDS[node.kind])}</p><h3 class="mkn-selected-title">${escape(node.label)}</h3><dl class="mkn-meta">${detailField('分类',node.category)}${detailField('编号 / SKU',node.code)}${detailField('SKC',node.product_number)}${detailField('具体规格',node.variant_hint)}${detailField('平台',node.platform)}${detailField('店铺',node.store)}${detailField('地区',node.region)}${detailField('人工状态',STATUS[node.status]||node.status)}${detailField('最近更新',updated)}${detailField('版本',node.revision)}<dt>相连节点</dt><dd>${neighbors.length}</dd></dl>${origin?`<div class="mkn-source-actions"><button type="button" data-mkn-action="open-source" ${!sourceId?'disabled':''}>查看${node.kind==='knowledge'?'原知识':'原商品'}</button><button type="button" data-mkn-action="ask" ${!sourceId?'disabled':''}>带这份资料问 AI</button></div>`:'<p class="mkn-inspector-copy">这是资料的分组节点。下方是与它直接相连的真实记录。</p>'}${neighbors.length?`<div class="mkn-related"><h4>直接关联</h4>${neighbors.slice(0,12).map(other=>`<button type="button" data-mkn-related="${escape(other.id)}"><i class="mkn-dot mkn-dot-${other.kind}"></i>${escape(other.label)}</button>`).join('')}${neighbors.length>12?'<p>更多关联可从节点清单中查找。</p>':''}</div>`:''}<p class="mkn-readonly">星图只展示资料及实际关联。修改请进入原资料，按账号权限操作。</p><p class="mkn-action-error" data-mkn="action-error" role="alert"></p>`;
    }
    function select(identity,focus=false){
      selected=index.byId.has(identity)?identity:'';tooltip.hidden=true;hover='';renderInspector();renderList();
      if(focus&&selected){const node=index.byId.get(selected);offsetX=-node.x*zoom;offsetY=-node.y*zoom;}
      draw();
    }
    function renderSummary(){
      const totals=graph.totals;
      find('totals').innerHTML=[['商品',totals.products],['知识',totals.documents],['SKU',totals.skus]].map(([label,value])=>`<span><strong>${count(value).toLocaleString()}</strong>${label}</span>`).join('');
      const shown=kind=>graph.nodes.filter(node=>node.kind===kind).length;
      find('summary').textContent=`当前显示 ${shown('product')} 款商品 · ${shown('knowledge')} 份知识 · ${shown('sku')} 个 SKU${graph.truncated?'。资料较多，当前仅显示部分节点，请搜索或切换类型缩小范围。':''}`;
      find('policy').textContent=graph.policy.persistent&&graph.policy.automatic_decay===false&&graph.policy.automatic_cleanup===false?'长期保留 · 不自动衰减或清理。连线只表示实际归属或人工关联。':'连线表示实际归属或人工关联；资料状态以原档案为准。';
    }
    async function load(){
      if(disposed)return;
      const request=++generation;controller?.abort();controller=new AbortController();loading=true;root.setAttribute('aria-busy','true');message.hidden=false;message.innerHTML='<span class="mkn-loader" aria-hidden="true"></span><strong>正在展开公司知识星图…</strong>';
      try{
        const params=new URLSearchParams({q:query,kind,limit:'240'}),raw=await api('/api/knowledge/graph?'+params,{signal:controller.signal});
        if(disposed||request!==generation)return;
        graph=normalize(raw);index=layout(graph.nodes,graph.edges);if(!index.byId.has(selected))selected='';
        resize();fit();renderSummary();renderInspector();renderList();draw();
        const actual=graph.nodes.some(node=>['product','knowledge','sku'].includes(node.kind));
        message.hidden=actual;
        if(!actual)message.innerHTML=`<strong>${query||kind!=='all'?'没有匹配的资料':'星图还没有资料'}</strong><p>${query||kind!=='all'?'试试其他关键词，或查看全部资料。':'先在商品库录入商品，或在公司知识库添加条目，再从这里探索。'}</p>${query||kind!=='all'?'<button type="button" data-mkn-action="clear">查看全部资料</button>':''}`;
        else if(!ctx){message.hidden=false;message.innerHTML='<strong>当前环境无法绘制星图</strong><p>所有节点仍可从右侧清单查看，资料不会受影响。</p>';}
        start();
      }catch(error){
        if(disposed||request!==generation)return;
        if(error?.name==='AbortError')return;
        message.hidden=false;message.innerHTML=`<strong>星图暂时没有展开</strong><p>${escape(text(error?.message)||'读取资料失败，请重试。')}</p><button type="button" data-mkn-action="retry">重新读取</button>`;
      }finally{if(!disposed&&request===generation){loading=false;root.setAttribute('aria-busy','false');}}
    }
    async function sourceAction(action){
      const node=index.byId.get(selected);if(!node)return;
      const originalKind=node.kind==='knowledge'?'knowledge':'product';
      const identity=node.kind==='sku'?node.product_id:node.source_id;
      if(!identity||!['product','knowledge','sku'].includes(node.kind))return;
      try{
        if(action==='ask'&&typeof onAsk==='function')await onAsk({kind:originalKind,id:identity,title:node.label});
        else if(action==='open-source'){const callback=originalKind==='knowledge'?onOpenKnowledge:onOpenProduct;if(typeof callback==='function')await callback(identity);}
      }catch(error){if(!disposed){const target=find('action-error');if(target)target.textContent=text(error?.message)||'操作未完成，请稍后重试。';}}
    }
    listen(root,'click',event=>{
      const nodeButton=event.target.closest('[data-mkn-node]');
      if(nodeButton){const node=visibleNodes[Number(nodeButton.dataset.mknNode)];if(node)select(node.id,true);return;}
      const related=event.target.closest('[data-mkn-related]');if(related){select(related.dataset.mknRelated,true);return;}
      const filter=event.target.closest('[data-mkn-kind]');
      if(filter){kind=filter.dataset.mknKind;if(!['all','product','knowledge'].includes(kind))return;root.querySelectorAll('[data-mkn-kind]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mknKind===kind)));load();return;}
      const action=event.target.closest('[data-mkn-action]')?.dataset.mknAction;
      if(action==='zoom-in')zoomAt(1.25);else if(action==='zoom-out')zoomAt(.8);
      else if(action==='reset'){fit();draw();}
      else if(action==='motion'){paused=!paused;updateMotion();stop();draw();start();}
      else if(action==='retry'||action==='refresh')load();
      else if(action==='clear'){query='';kind='all';find('query').value='';root.querySelectorAll('[data-mkn-kind]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mknKind==='all')));load();}
      else if(action==='open-source'||action==='ask')sourceAction(action);
    });
    listen(find('search-form'),'submit',event=>{event.preventDefault();host.clearTimeout(searchTimer);query=find('query').value.trim().slice(0,120);load();});
    listen(find('query'),'input',()=>{host.clearTimeout(searchTimer);searchTimer=host.setTimeout(()=>{query=find('query').value.trim().slice(0,120);load();},350);});
    listen(find('list-query'),'input',renderList);
    listen(root,'keydown',event=>{
      const button=event.target.closest('[data-mkn-node]');
      if(!button)return;
      const current=Number(button.dataset.mknNode),next=event.key==='ArrowDown'?Math.min(visibleNodes.length-1,current+1):event.key==='ArrowUp'?Math.max(0,current-1):event.key==='Home'?0:event.key==='End'?visibleNodes.length-1:-1;
      if(next>=0){event.preventDefault();const node=visibleNodes[next];select(node.id);root.querySelector(`[data-mkn-node="${next}"]`)?.focus();}
    });
    listen(canvas,'wheel',event=>{event.preventDefault();tooltip.hidden=true;const p=coordinates(event);zoomAt(Math.exp(-clamp(event.deltaY,-100,100)*.004),p.x,p.y);},{passive:false});
    listen(canvas,'pointerdown',event=>{if(event.button!==undefined&&event.button!==0)return;const p=coordinates(event);pointer={id:event.pointerId,x:p.x,y:p.y,lastX:p.x,lastY:p.y,moved:false};canvas.setPointerCapture?.(event.pointerId);canvas.classList.add('is-dragging');tooltip.hidden=true;});
    listen(canvas,'pointermove',event=>{
      const p=coordinates(event);
      if(pointer&&pointer.id===event.pointerId){offsetX+=p.x-pointer.lastX;offsetY+=p.y-pointer.lastY;pointer.lastX=p.x;pointer.lastY=p.y;pointer.moved=pointer.moved||Math.hypot(p.x-pointer.x,p.y-pointer.y)>4;draw();return;}
      const node=hit(p);if((node?.id||'')!==hover){hover=node?.id||'';draw();}
      tooltip.hidden=!node;canvas.style.cursor=node?'pointer':'';
      if(node){tooltip.textContent=node.label+' · '+KINDS[node.kind];tooltip.style.left=clamp(p.x+14,12,width-210)+'px';tooltip.style.top=clamp(p.y+16,52,height-70)+'px';}
    });
    function release(event){if(!pointer||(event?.pointerId!==undefined&&pointer.id!==event.pointerId))return;const prior=pointer;pointer=null;canvas.classList.remove('is-dragging');if(event?.type==='pointerup'&&!prior.moved)select(hit(coordinates(event))?.id||'');try{canvas.releasePointerCapture?.(prior.id);}catch{}}
    listen(canvas,'pointerup',release);listen(canvas,'pointercancel',release);listen(canvas,'lostpointercapture',release);listen(host,'blur',()=>release());
    listen(canvas,'pointerleave',()=>{if(!pointer){hover='';tooltip.hidden=true;draw();}});
    listen(canvas,'keydown',event=>{
      const keys=['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Home','Escape'];if(!keys.includes(event.key))return;event.preventDefault();
      if(event.key==='+'||event.key==='=')zoomAt(1.25);else if(event.key==='-')zoomAt(.8);else if(event.key==='Home'){fit();draw();}else if(event.key==='Escape')select('');
      else{offsetX+=event.key==='ArrowLeft'?32:event.key==='ArrowRight'?-32:0;offsetY+=event.key==='ArrowUp'?32:event.key==='ArrowDown'?-32:0;draw();}
    });
    listen(doc,'visibilitychange',()=>{if(doc.hidden)stop();else{draw();start();}});
    listen(doc,'midoo:knowledge-saved',()=>load());
    const reduced=()=>{if(media.matches){paused=true;updateMotion();stop();draw();}};
    if(media?.addEventListener){media.addEventListener('change',reduced);cleanups.push(()=>media.removeEventListener('change',reduced));}
    else if(media?.addListener){media.addListener(reduced);cleanups.push(()=>media.removeListener(reduced));}
    if(host.ResizeObserver){observer=new host.ResizeObserver(resize);observer.observe(stage);}else listen(host,'resize',resize);
    const instance={dispose(){if(disposed)return;disposed=true;generation++;controller?.abort();host.clearTimeout(searchTimer);stop();observer?.disconnect();cleanups.splice(0).forEach(cleanup=>cleanup());sprites.clear();if(root.parentNode===container)root.remove();if(mounted.get(container)===instance)mounted.delete(container);}};
    mounted.set(container,instance);updateMotion();renderInspector();renderList();resize();load();return instance;
  }
  host.MidooKnowledgeNebula=Object.freeze({mount});
})(window);
