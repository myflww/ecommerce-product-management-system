/* Minimal isolated DOM for UI contract tests. Never reads company data or credentials. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function appFixture(){
 const elements=new Map(),listeners=new Map(),events=[];
 const makeElement=()=>({innerHTML:'',textContent:'',value:'',hidden:false,disabled:false,name:'',dataset:{},style:{},isConnected:true,classList:{add(){},remove(){},toggle(){}},append(){},remove(){},focus(){},click(){},querySelector(){return null;},querySelectorAll(){return [];},closest(){return null;}});
 const element=s=>{if(!elements.has(s))elements.set(s,makeElement());return elements.get(s);};
 class TestFormData{constructor(form){this.values=Object.entries(form?.fields||{});}append(...v){this.values.push(v);}get(k){return this.values.find(v=>v[0]===k)?.[1]??null;}has(k){return this.values.some(v=>v[0]===k);}[Symbol.iterator](){return this.values[Symbol.iterator]();}}
 const document={querySelector:element,querySelectorAll(){return [];},createElement:makeElement,body:{style:{}},addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);},dispatchEvent(event){events.push(event);for(const fn of listeners.get(event.type)||[])fn(event);}};
 const context=vm.createContext({console,URL,URLSearchParams,FormData:TestFormData,HTMLFormElement:class{},structuredClone,Blob,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},document,window:{addEventListener(){},scrollTo(){}},location:{hash:'#library'},history:{replaceState(){}},navigator:{},setTimeout(){return 1;},clearTimeout(){}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/init\(\);\s*$/,''),context);
 const run=c=>vm.runInContext(c,context);
 async function emit(type,target){for(const fn of listeners.get(type)||[])await fn({target,preventDefault(){},stopPropagation(){}});}
 return {context,run,element,elements,listeners,makeElement,emit,events,TestFormData};
}
module.exports={appFixture};
