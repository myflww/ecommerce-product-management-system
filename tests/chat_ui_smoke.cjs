/* Chat feature contracts; mocks only, no company data or cloud requests. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const elements=new Map(),listeners=new Map(),revoked=[];
let urlIndex=0;
const makeElement=()=>({innerHTML:'',textContent:'',value:'',hidden:false,disabled:false,dataset:{},style:{},isConnected:true,classList:{add(){},remove(){},toggle(){}},append(){},remove(){},focus(){},click(){},querySelector(){return null;},querySelectorAll(){return [];}});
const element=selector=>{if(!elements.has(selector))elements.set(selector,makeElement());return elements.get(selector);};
class TestFormData{constructor(){this.values=[];}append(...entry){this.values.push(entry);}get(name){return this.values.find(entry=>entry[0]===name)?.[1]??null;}}
class TestURL extends URL{static createObjectURL(){return 'blob:chat-qa-'+(++urlIndex);}static revokeObjectURL(value){revoked.push(value);}}
const context=vm.createContext({console,URL:TestURL,URLSearchParams,FormData:TestFormData,HTMLFormElement:class{},structuredClone,Blob,document:{querySelector:element,querySelectorAll(){return [];},addEventListener(name,listener){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(listener);},body:{style:{}},createElement:makeElement},window:{addEventListener(){},scrollTo(){}},location:{hash:'#ai'},history:{replaceState(){}},navigator:{},setTimeout(){return 1;},clearTimeout(){}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/,''),context);
const run=code=>vm.runInContext(code,context);
const plain=value=>JSON.parse(JSON.stringify(value));
context.notices=[];context.calls=[];
run(`toast=(message,error)=>notices.push({message,error});state.user={id:'qa-user',name:'测试运营',role:'admin'};state.route='ai';globalThis.config={active_provider:'deepseek',share_library_context:false,providers:[{id:'deepseek',label:'DeepSeek',model:'deepseek-flash',enabled:true,configured:true,vision:true,reasoning:{options:[{value:'default',label:'官方默认'},{value:'high',label:'高'},{value:'max',label:'最大'}],note:'官方模型能力说明',docs_url:'https://api-docs.deepseek.com/guides/thinking_mode'}}]};state.aiConfig=config;api=async(url,options={})=>{calls.push({url,options});return url.endsWith('/config')?config:{reply:'测试分析',attachments:[]};};`);
const file=(name,size=1024,type='text/plain')=>({name,size,type,lastModified:42});
async function main(){
  await run('renderAI()');
  const page=element('#content').innerHTML;
  assert(page.includes('id="chat-effort"')&&page.includes('value="max"'),'Effort options come from the selected provider capability');
  assert(page.includes('添加附件')&&page.includes('id="chat-files"'),'Chat has an attachment picker');
  assert(page.includes('TXT、MD、CSV、JSON')&&page.includes('DOCX / XLSX'),'Available document formats are disclosed');
  assert(page.includes('60 分钟')&&page.includes('当前服务商'),'Attachment retention and transfer are disclosed');
  run(`state.chatEfforts[chatProviderKey()]='max';`);
  assert.equal(run('chatEffort()'),'max');
  run(`config.providers[0].model='unknown-new-model';config.providers[0].reasoning={options:[{value:'default',label:'官方默认'}]};`);
  assert.equal(run('chatEffort()'),'default','A choice never leaks to another model');
  await run('renderAI()');
  assert.match(element('#content').innerHTML,/id="chat-effort"[^>]*disabled/,'Unknown models cannot select invented effort settings');
  run(`config.providers[0].model='deepseek-flash';config.providers[0].reasoning={options:[{value:'default',label:'官方默认'},{value:'max',label:'最大'}]};`);
  await run('renderAI()');
  assert.equal(run('chatEffort()'),'max','Returning to a model restores its session choice');

  context.inputFiles=[file('创意草稿.txt'),file('商品.png',2048,'image/png')];
  run('addChatFiles(inputFiles)');
  assert.equal(run('state.chatFiles.length'),2);
  assert(element('#chat-attachments').innerHTML.includes('<img')&&element('#chat-attachments').innerHTML.includes('创意草稿.txt'),'Pictures preview while documents show a named card');
  run('addChatFiles(inputFiles)');
  assert.equal(run('state.chatFiles.length'),2,'Re-selecting the same attachments does not duplicate them');
  run('removeChatFile(1)');
  assert.equal(revoked.length,1,'Removing a draft picture releases its object URL');
  run('removeChatFile(0)');
  context.inputFiles=[file('malware.exe'),file('empty.txt',0),file('huge.pdf',11*1024*1024),file('small.txt')];
  run('addChatFiles(inputFiles)');
  assert.equal(run('state.chatFiles.length'),1,'Unsupported, empty and oversized files stay out of the draft');
  assert(context.notices.at(-1).error);
  run('resetChatState();config.providers[0].vision=false;');
  context.inputFiles=[file('商品.png',2000,'image/png'),file('规格.csv')];
  run('addChatFiles(inputFiles)');
  assert.equal(run('state.chatFiles.length'),1,'Image permission does not block text documents');
  assert.equal(run('state.chatFiles[0].kind'),'document');
  run('resetChatState();config.providers[0].vision=true;');
  context.inputFiles=Array.from({length:7},(_,i)=>file('资料'+i+'.txt'));
  run('addChatFiles(inputFiles)');
  assert.equal(run('state.chatFiles.length'),6,'Each draft is limited to six attachments');
  run('resetChatState();');
  context.inputFiles=[file('a.pdf',10*1024*1024),file('b.pdf',10*1024*1024),file('c.pdf',1)];
  run('addChatFiles(inputFiles)');
  assert.equal(run('state.chatFiles.length'),2,'The total size limit applies across files');

  run('resetChatState();');
  context.inputFiles=[file('<script>alert(1)</script>.txt')];
  run('addChatFiles(inputFiles)');
  assert(!element('#chat-attachments').innerHTML.includes('<script>'),'File names are displayed as text');
  const sentCount=context.calls.length;
  run(`api=async(url,options={})=>{calls.push({url,options});throw new Error('模型暂不可用');};`);
  await run(`sendChat('请完善创意')`);
  assert.equal(run('state.chatHistory.length'),0,'Failed requests do not contaminate the conversation history');
  assert.equal(run('state.chatFiles.length'),1,'Failed requests preserve attachments for retry');
  assert.equal(run('state.chatDraft'),'请完善创意','Failed requests preserve the draft text');
  assert(run('state.chatError').includes('可以重试'));
  assert.equal(run('state.chatSending'),false);
  const failed=context.calls[sentCount].options.body;
  assert(failed instanceof TestFormData);
  assert.equal(failed.get('reasoning_effort'),'max','Multipart sends the selected official effort');
  assert.equal(failed.values.filter(v=>v[0]==='files').length,1);

  run(`api=async(url,options={})=>{calls.push({url,options});return {reply:'已分析资料',attachments:[{id:'file-qa',name:'创意.txt',kind:'document',characters:120,truncated:true}]};};`);
  await run(`sendChat('请完善创意')`);
  assert.equal(run('state.chatFiles.length'),0,'Successful sends clear the composer');
  assert.equal(run('state.chatDraft'),'');
  assert.equal(run('state.chatHistory.length'),2);
  assert(run('chatMessages()').includes('内容已截断'),'Partial document extraction remains visible in the transcript');
  await run(`sendChat('继续解释附件')`);
  const followup=context.calls.at(-1).options.body;
  assert.equal(followup.reasoning_effort,'max','Text requests also carry the selected effort');
  assert.deepEqual(plain(followup.history[0].attachments),[{id:'file-qa',name:'创意.txt',kind:'document'}],'Follow-up requests use server attachment references');
  assert(!JSON.stringify(followup).includes('blob:'),'Local preview URLs never reach the provider');

  run('resetChatState();');
  context.inputFiles=[file('图片.png',2000,'image/png')];run('addChatFiles(inputFiles)');
  run(`api=async(url,options={})=>{calls.push({url,options});return {reply:'已阅读图片',attachments:[{id:'image-qa',name:'图片.png',kind:'image'}]};};`);
  await run(`sendChat('')`);
  assert(run('state.chatHistory[0].content').includes('根据这些附件'),'An attachment-only message has a useful default question');
  assert(run('chatMessages()').includes('blob:chat-qa-'),'Sent image previews remain in the conversation');
  const beforeClear=revoked.length;
  run(`api=async(url,options={})=>{calls.push({url,options});return url.endsWith('/config')?config:{ok:true};};`);
  await run('clearChat()');
  assert(context.calls.some(call=>call.url==='/api/ai/chat/attachments/clear'),'Clear also removes server-side attachment references');
  assert.equal(run('state.chatHistory.length'),0);
  assert(revoked.length>beforeClear,'Clearing a conversation releases sent previews');

  run(`api=()=>new Promise(resolve=>{globalThis.resolveChat=resolve;});globalThis.pendingPromise=sendChat('发送中的问题');`);
  assert.equal(run('state.chatSending'),true);
  assert.equal(element('#chat-input').disabled,true,'A pending request cannot have its draft changed');
  const beforePendingClear=context.calls.length;
  await run('clearChat()');
  assert.equal(context.calls.length,beforePendingClear,'Clear does not race an active request');
  run(`state.route='library';resolveChat({reply:'切页后的回复',attachments:[]});`);
  await run('pendingPromise');
  assert.equal(run('state.chatHistory.at(-1).content'),'切页后的回复','Leaving the page safely preserves the completed reply');
  assert.equal(run('state.chatSending'),false);
  run(`state.route='ai';globalThis.pendingPromise=sendChat('旧账号的问题');resetChatState();resolveChat({reply:'旧账号回复',attachments:[]});`);
  await run('pendingPromise');
  assert.equal(run('state.chatHistory.length'),0,'A reset discards in-flight replies from the previous context');

  // Chinese IME Enter must finish composing rather than accidentally submit.
  let prevented=false;
  for(const listener of listeners.get('keydown')||[])listener({target:{id:'chat-input',value:'输入中'},key:'Enter',shiftKey:false,isComposing:true,preventDefault(){prevented=true;}});
  assert.equal(prevented,false,'IME Enter does not send a premature message');
  console.log('CHAT_UI_SMOKE_OK: official per-model effort, safe attachments and previews, permission/size limits, failure retry, reference history, clear/revoke, route and account races, IME');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
