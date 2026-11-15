/* Independent knowledge constellation UI contracts. Synthetic graph and DOM only. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/knowledge-nebula.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../static/knowledge-nebula.css'),'utf8');
function emitter(target={}){
  target.listeners=new Map();
  target.addEventListener=(name,fn)=>{if(!target.listeners.has(name))target.listeners.set(name,new Set());target.listeners.get(name).add(fn);};
  target.removeEventListener=(name,fn)=>target.listeners.get(name)?.delete(fn);
  target.emit=async(name,event={})=>{for(const fn of [...target.listeners.get(name)||[]])await fn({type:name,preventDefault(){},...event});};
  target.listenerCount=()=>[...target.listeners.values()].reduce((sum,list)=>sum+list.size,0);
  return target;
}
function harness({reduced=true,canvasAvailable=true}={}){
  const frames=new Map(),timers=new Map(),observers=[],drawn=[];let sequence=0;
  const brush=new Proxy({
    createLinearGradient(){return {addColorStop(){}};},createRadialGradient(){return {addColorStop(){}};},
    measureText(value){return {width:String(value).length*6};},
    fillText(value){drawn.push(value);},
  },{get(target,key){return key in target?target[key]:()=>{};}});
  class Element {
    constructor(tag='div'){
      this.tagName=tag.toUpperCase();this.dataset={};this.attributes=new Map();this.slots=new Map();this.children=[];
      this.innerHTML='';this.textContent='';this.value='';this.hidden=false;this.style={};this.parentNode=null;this.ownerDocument=doc;
      this.classList={add(){},remove(){}};emitter(this);
    }
    setAttribute(name,value){this.attributes.set(name,String(value));}
    getAttribute(name){return this.attributes.get(name)??null;}
    appendChild(child){child.parentNode=this;this.children.push(child);return child;}
    remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
    querySelector(selector){
      if(!this.slots.has(selector)){
        const match=selector.match(/^\[data-mkn="(.+)"\]$/),node=new Element(match?.[1]==='canvas'?'canvas':'div');
        if(match)node.dataset.mkn=match[1];
        const action=selector.match(/^\[data-mkn-action="(.+)"\]$/);if(action)node.dataset.mknAction=action[1];
        const item=selector.match(/^\[data-mkn-node="(\d+)"\]$/);if(item)node.dataset.mknNode=item[1];
        this.slots.set(selector,node);
      }
      return this.slots.get(selector);
    }
    querySelectorAll(selector){return selector==='[data-mkn-kind]'?['all','product','knowledge'].map(kind=>{const node=this.querySelector('[kind-'+kind+']');node.dataset.mknKind=kind;return node;}):[];}
    closest(selector){
      for(const [name,key] of [['node','mknNode'],['related','mknRelated'],['kind','mknKind'],['action','mknAction']])if(selector===`[data-mkn-${name}]`&&key in this.dataset)return this;
      return null;
    }
    getBoundingClientRect(){return {left:0,top:0,width:900,height:620};}
    getContext(){return canvasAvailable?brush:null;}
    setPointerCapture(){}releasePointerCapture(){}focus(){this.focused=true;}
  }
  const doc=emitter({hidden:false});doc.createElement=tag=>new Element(tag);
  const media=emitter({matches:reduced});
  const win=emitter({document:doc,devicePixelRatio:4,matchMedia:()=>media,
    requestAnimationFrame(fn){const id=++sequence;frames.set(id,fn);return id;},
    cancelAnimationFrame(id){frames.delete(id);},setTimeout(fn){const id=++sequence;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},
    ResizeObserver:class {constructor(fn){this.fn=fn;this.disconnected=false;observers.push(this);}observe(target){this.target=target;}disconnect(){this.disconnected=true;}},
  });
  const context=vm.createContext({window:win,AbortController,URLSearchParams,console});
  vm.runInContext(source,context);
  const container=new Element(),calls=[],opened=[],asked=[];
  let implementation=async()=>structuredClone(graph);
  const mount=()=>win.MidooKnowledgeNebula.mount({container,user:{id:'qa'},api:async(url,options)=>{calls.push({url,options});return implementation(url,options);},
    onOpenProduct:id=>opened.push({kind:'product',id}),onOpenKnowledge:id=>opened.push({kind:'knowledge',id}),onAsk:value=>asked.push(value)});
  return {win,doc,media,frames,timers,observers,container,calls,opened,asked,drawn,mount,
    setAPI(fn){implementation=fn;},root:()=>container.children[0],find(name){return container.children[0].querySelector(`[data-mkn="${name}"]`);},
    async click(dataset){const target=new Element('button');target.dataset=dataset;await container.children[0].emit('click',{target});},
  };
}
const graph={
  nodes:[
    {id:'root',kind:'root',label:'公司知识'},
    {id:'group:products',kind:'group',label:'商品档案'},
    {id:'category:candle',kind:'category',label:'柱蜡'},
    {id:'product:graph-id',kind:'product',label:'红色柱蜡',source_id:'real-product-id',category:'柱蜡',code:'MD-001',status:'listed'},
    {id:'document:graph-id',kind:'knowledge',label:'<script>采购规则</script>',source_id:'real-doc-id',category:'流程',revision:4,updated_at:'2026-11-13T08:00:00Z'},
    {id:'sku:graph-id',kind:'sku',label:'红色 / 2件装',source_id:'real-listing-id',product_id:'real-product-id',code:'001-RED',product_number:'SKC-001',platform:'Temu',store:'美国店',status:'pending',variant_hint:'红色 / 2件装'},
  ],edges:[
    {source:'root',target:'group:products',relation:'contains'},
    {source:'group:products',target:'category:candle',relation:'contains'},
    {source:'product:graph-id',target:'category:candle',relation:'classified_as'},
    {source:'sku:graph-id',target:'product:graph-id',relation:'variant_of'},
    {source:'document:graph-id',target:'product:graph-id',relation:'documented_by'},
    {source:'missing-private-node',target:'product:graph-id',relation:'documented_by'},
  ],totals:{products:9,documents:1,skus:1,sources:10},shown:{products:1,documents:1,skus:1},truncated:false,
  policy:{persistent:true,automatic_decay:false,automatic_cleanup:false},
};
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const plain=value=>JSON.parse(JSON.stringify(value));
async function main(){
  const h=harness(),instance=h.mount();await settle();
  assert.equal(typeof instance.dispose,'function');
  assert.equal(h.calls.length,1);
  const params=new URL(h.calls[0].url,'http://localhost').searchParams;
  assert.equal(new URL(h.calls[0].url,'http://localhost').pathname,'/api/knowledge/graph');
  assert.equal(params.get('kind'),'all');assert.equal(params.get('limit'),'240');
  assert(h.root().innerHTML.includes('公司知识星图')&&h.root().innerHTML.includes('role="img"'));
  assert(h.root().innerHTML.includes('节点清单')&&h.root().innerHTML.includes('Home重置'));
  assert(h.find('node-list').innerHTML.includes('&lt;script&gt;采购规则&lt;/script&gt;'));
  assert(!h.find('node-list').innerHTML.includes('<script>'));
  assert.equal(h.find('list-count').textContent,'6 / 6','Decorative stars never become data nodes');
  assert(h.find('totals').innerHTML.includes('<strong>9</strong>商品'),'Real total is separate from shown nodes');
  assert(h.find('summary').textContent.includes('1 款商品'));
  assert(h.find('policy').textContent.includes('不自动衰减或清理'));
  assert.equal(h.frames.size,0,'Reduced motion starts without a continuous animation loop');
  assert.equal(h.find('canvas').width,1800,'Device pixel ratio is capped at two');
  assert.equal(h.find('message').hidden,true);

  await h.click({mknNode:'3'});
  assert(h.find('inspector').innerHTML.includes('红色柱蜡')&&h.find('inspector').innerHTML.includes('已上架'));
  await h.click({mknAction:'open-source'});await h.click({mknAction:'ask'});
  assert.deepEqual(h.opened,[{kind:'product',id:'real-product-id'}],'Graph identity is never confused with source identity');
  assert.deepEqual(plain(h.asked[0]),{kind:'product',id:'real-product-id',title:'红色柱蜡'});
  await h.click({mknNode:'4'});assert(h.find('inspector').innerHTML.includes('&lt;script&gt;'));
  assert(!h.find('inspector').innerHTML.includes('<script>'));
  await h.click({mknAction:'open-source'});await h.click({mknAction:'ask'});
  assert.deepEqual(h.opened.at(-1),{kind:'knowledge',id:'real-doc-id'});
  assert.equal(h.asked.at(-1).kind,'knowledge');assert.equal(h.asked.at(-1).id,'real-doc-id');
  await h.click({mknNode:'5'});await h.click({mknAction:'ask'});
  assert.equal(h.asked.at(-1).id,'real-product-id','SKU questions target the real parent product');
  assert(h.find('inspector').innerHTML.includes('001-RED')&&h.find('inspector').innerHTML.includes('SKC-001'));
  await h.click({mknNode:'2'});
  assert(h.find('inspector').innerHTML.includes('分组节点')&&!h.find('inspector').innerHTML.includes('data-mkn-action="ask"'),'Structural nodes show context rather than pretend source documents');
  h.find('list-query').value='采购';await h.find('list-query').emit('input');
  assert.equal(h.find('list-count').textContent,'1 / 6');
  assert(!h.find('node-list').innerHTML.includes('红色柱蜡'));
  await h.click({mknNode:'0'});assert(h.find('inspector').innerHTML.includes('采购规则'));
  h.find('list-query').value='';await h.find('list-query').emit('input');
  const keyboardNode=h.root().querySelector('[data-mkn-node="3"]');
  await h.root().emit('keydown',{target:keyboardNode,key:'ArrowDown'});
  assert(h.find('inspector').innerHTML.includes('采购规则'),'Arrow keys select another node without a pointer');
  assert(h.root().querySelector('[data-mkn-node="4"]').focused);
  await h.click({mknKind:'knowledge'});await settle();
  assert.equal(new URL(h.calls.at(-1).url,'http://localhost').searchParams.get('kind'),'knowledge');

  const before=h.find('zoom').textContent;
  await h.find('canvas').emit('wheel',{clientX:450,clientY:310,deltaY:-80});
  assert.notEqual(h.find('zoom').textContent,before,'Wheel zoom works while animation is paused');
  const canvas=h.find('canvas');
  await canvas.emit('pointerdown',{pointerId:1,button:0,clientX:300,clientY:200});
  await canvas.emit('pointermove',{pointerId:1,clientX:345,clientY:240});
  await canvas.emit('pointercancel',{pointerId:1});
  await canvas.emit('keydown',{key:'Home'});
  await h.click({mknAction:'motion'});assert.equal(h.frames.size,1);
  h.doc.hidden=true;await h.doc.emit('visibilitychange');assert.equal(h.frames.size,0);
  h.doc.hidden=false;await h.doc.emit('visibilitychange');assert.equal(h.frames.size,1);
  h.media.matches=true;await h.media.emit('change');assert.equal(h.frames.size,0);
  const prior=h.calls.length;await h.doc.emit('midoo:knowledge-saved');await settle();
  assert.equal(h.calls.length,prior+1,'Knowledge save refreshes only the local graph');
  assert(h.calls.every(call=>call.url.startsWith('/api/knowledge/graph?')&&!call.options.body));

  instance.dispose();instance.dispose();
  assert.equal(h.container.children.length,0);
  assert(h.observers.every(observer=>observer.disconnected));
  assert.equal(h.frames.size,0);assert.equal(h.timers.size,0);
  assert.equal(h.doc.listenerCount(),0);assert.equal(h.win.listenerCount(),0);assert.equal(h.media.listenerCount(),0);
  assert.equal(h.calls.at(-1).options.signal.aborted,true);

  const race=harness();let resolveFirst;
  race.setAPI(()=>new Promise(resolve=>{resolveFirst=resolve;}));const old=race.mount();
  race.setAPI(async()=>({...graph,nodes:[{id:'new',kind:'knowledge',label:'新查询结果',source_id:'new-source'}],edges:[]}));
  race.find('query').value='新查询';await race.find('search-form').emit('submit');await settle();
  assert.equal(race.calls[0].options.signal.aborted,true);
  assert(race.find('node-list').innerHTML.includes('新查询结果'));
  resolveFirst(graph);await settle();
  assert(race.find('node-list').innerHTML.includes('新查询结果')&&!race.find('node-list').innerHTML.includes('红色柱蜡'),'Old query cannot repaint a newer graph');
  assert.equal(new URL(race.calls.at(-1).url,'http://localhost').searchParams.get('q'),'新查询');
  let resolveLate;race.setAPI(()=>new Promise(resolve=>{resolveLate=resolve;}));
  await race.click({mknAction:'refresh'});old.dispose();resolveLate(graph);await settle();
  assert.equal(race.container.children.length,0,'Disposed view ignores a late graph response');

  const empty=harness();empty.setAPI(async()=>({...graph,nodes:[graph.nodes[0]],edges:[],totals:{products:0,documents:0,skus:0}}));
  const emptyView=empty.mount();await settle();
  assert(!empty.find('message').hidden&&empty.find('message').innerHTML.includes('星图还没有资料'));
  assert(empty.find('message').innerHTML.includes('先在商品库录入商品'));emptyView.dispose();

  const error=harness();error.setAPI(async()=>{throw new Error('<img src=x onerror=evil()>');});
  const errorView=error.mount();await settle();
  assert(error.find('message').innerHTML.includes('&lt;img')&&!error.find('message').innerHTML.includes('<img'));
  assert(error.find('message').innerHTML.includes('重新读取'));
  error.setAPI(async()=>graph);await error.click({mknAction:'retry'});await settle();
  assert(error.find('message').hidden);errorView.dispose();

  const large=harness();large.setAPI(async()=>({...graph,nodes:[graph.nodes[0],...Array.from({length:400},(_,i)=>({id:'p'+i,kind:'product',label:'商品'+i,source_id:'real'+i}))],edges:[],totals:{products:400,documents:0,skus:0}}));
  const largeView=large.mount();await settle();
  assert.equal(large.find('list-count').textContent,'300 / 300');
  assert(large.find('summary').textContent.includes('部分节点'));assert(large.find('totals').innerHTML.includes('<strong>400</strong>'));
  largeView.dispose();

  const fallback=harness({canvasAvailable:false});const fallbackView=fallback.mount();await settle();
  assert(fallback.find('message').innerHTML.includes('无法绘制星图'));assert(fallback.find('node-list').innerHTML.includes('红色柱蜡'));
  assert.equal(fallback.frames.size,0);fallbackView.dispose();
  assert(css.includes('prefers-reduced-motion')&&css.includes('max-width:540px'));
  assert(!/https?:\/\//.test(source)&&!source.includes('WebGLRenderer')&&!source.includes('fetch('),'View uses only its injected local API');
  console.log('KNOWLEDGE_NEBULA_UI_SMOKE_OK: honest graph counts, source callbacks, escaped metadata, keyboard list, paused interaction, DPR cap, reduced motion, local refresh, stale isolation, errors, truncation and full disposal');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
