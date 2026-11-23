// Library UI behaviour: search/grouping, stable IDs, safe names, trash/restore and busy guards.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const root=new URL('../tribuutscan/web/',import.meta.url),html=fs.readFileSync(new URL('library.html',root),'utf8');
const all=[];
class Element{
 constructor(tag,attrs=''){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.classList={values:new Set(),add(value){this.values.add(value);}};this.id=attrs.match(/id="([^"]+)"/)?.[1];this.hidden=/\bhidden\b/.test(attrs);this.value=attrs.match(/value="([^"]*)"/)?.[1]||'';for(const [,key,value] of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g))this.dataset[key]=value;all.push(this);}
 set className(value){for(const c of value.split(' '))this.classList.add(c);}get className(){return [...this.classList.values].join(' ');}
 setAttribute(name,value){this[name]=value;}append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 addEventListener(type,fn){this.listeners[type]=fn;}focus(){document.activeElement=this;}select(){this.focus();}showModal(){this.open=true;}close(){this.open=false;}scrollIntoView(){}
 closest(selector){for(let node=this;node;node=node.parentElement)if(matches(node,selector))return node;return null;}
 querySelectorAll(selector){const out=[];for(const node of this.children){if(matches(node,selector))out.push(node);out.push(...node.querySelectorAll(selector));}return out;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
function matches(node,selector){if(selector.startsWith('.'))return node.classList.values.has(selector.slice(1));const action=selector.match(/^\[data-action="?([^"\]]+)"?\]$/);if(action)return node.dataset.action===action[1];if(selector==='[data-close]')return node.dataset.close!==undefined;if(selector==='dialog button')return node.tagName==='BUTTON'&&!!node.closest('dialog');return node.tagName===selector.toUpperCase();}
const stack=[];for(const [token,tag,attrs] of html.matchAll(/<\/?(\w+)([^>]*)>/g)){if(token.startsWith('</')){stack.pop();continue;}const node=new Element(tag,attrs);if(stack.length)stack.at(-1).append(node);if(!['meta','link','input','img','br'].includes(tag))stack.push(node);}
const document={activeElement:null,getElementById:id=>all.find(node=>node.id===id),createElement:tag=>new Element(tag),querySelectorAll:selector=>all.filter(node=>matches(node,selector))};
const get=id=>document.getElementById(id);get('kind').value=get('output').value='all';get('sort').value='newest';
const entry=(id,name,kind,family,parents=[],trashed_at=null)=>({id,display_name:name,kind,family,parents,trashed_at,revision:0,created:1795420000,points:100,storage:{geometry:1000},bytes:1000,exports:{mesh:kind==='capture'},recordings:[],dependents:[],thumbnail_version:'1'});
let items=[entry('a-scan','Raw <head>','capture','a-scan'),entry('b-scan','Cleanup copy','edited','a-scan',['a-scan']),entry('c-scan','Earlier scan','capture','c-scan',[],1)];items[0].dependents=['b-scan'];
let state={phase:'idle',processing:false,instance:'host',revision:1},requests=[],navigations=[];
const fetch=async(path,options)=>{
 if(path==='/api/library/bin-plan')return {ok:true,status:200,json:async()=>({signature:'reviewed',deletable:['c-scan'],protected:[],bytes:1000})};
 if(path==='/api/state')return {ok:true,status:200,json:async()=>({...state})};
 if(path==='/api/library')return {ok:true,status:200,json:async()=>({items:structuredClone(items)})};
 const data=JSON.parse(options.body);requests.push(data);const item=items.find(i=>i.id===data.name);
 if(data.action==='library-empty-bin'){assert.equal(data.signature,'reviewed');items=items.filter(i=>i.id!=='c-scan');return {ok:true,status:200,json:async()=>({deleted:['c-scan'],protected:[],failed:[]})};}
 if(data.action==='open')return {ok:true,status:200,json:async()=>({...state})};
 if(data.entry_revision!==item.revision)return {ok:false,status:400,json:async()=>({error:'Entry changed; refresh and try again'})};
 if(data.action==='library-rename')item.display_name=data.display_name;
 if(data.action==='library-trash')item.trashed_at=2;
 if(data.action==='library-restore')item.trashed_at=null;
 item.revision++;state.revision++;
 return {ok:true,status:200,json:async()=>({...item})};
};
const context=vm.createContext({document,console,URLSearchParams,Date,Number,JSON,Map,Set,Array,AbortSignal,setTimeout,setInterval(){},history:{replaceState(){}},location:{search:'',assign:url=>navigations.push(url)},fetch});
await vm.runInContext('(async()=>{'+fs.readFileSync(new URL('library.js',root),'utf8')+'})()',context);
const cards=()=>get('families').querySelectorAll('.card'),card=id=>cards().find(node=>node.dataset.id===id);
const control=(id,action)=>card(id).querySelector('[data-action="'+action+'"]');
const until=async fn=>{for(let i=0;i<100;i++){if(fn())return;await new Promise(resolve=>setTimeout(resolve,5));}throw Error('Library UI did not settle');};
assert.equal(cards().length,2);assert.equal(get('families').querySelectorAll('.family').length,1);
assert.equal(card('a-scan').querySelector('h3').textContent,'Raw <head>');
get('search').value='raw';get('search').listeners.input();assert.equal(cards().length,2,'search should include related versions');
get('kind').value='edited';get('kind').listeners.change();assert.equal(cards().length,1);assert.equal(cards()[0].dataset.id,'b-scan');
get('search').value='';get('kind').value='all';get('kind').listeners.change();
control('a-scan','rename').onclick();get('display-name').value='Friendly <scan>';
get('rename-form').onsubmit({preventDefault(){}});await until(()=>!get('rename-dialog').open);
assert.equal(card('a-scan').querySelector('h3').textContent,'Friendly <scan>');
await control('a-scan','open').onclick();assert.equal(requests.at(-1).name,'a-scan');assert.equal(navigations.at(-1),'/model/');
control('a-scan','trash').onclick();assert.equal(get('trash-dialog').open,true);get('trash-form').onsubmit({preventDefault(){}});
await until(()=>!get('trash-dialog').open);assert.equal(cards().length,1);assert.ok(card('b-scan'),'derived copy remains in active library');
get('trash-tab').onclick();assert.equal(cards().length,2);assert.equal(get('trash-note').hidden,false);
await control('a-scan','restore').onclick();get('active-tab').onclick();assert.equal(cards().length,2);
control('b-scan','details').onclick();assert.equal(get('details-dialog').open,true);assert.equal(get('relations').children.length,1);get('details-dialog').close();
// Concurrent edits must keep the typed name and display the server's rejection.
control('a-scan','rename').onclick();items[0].revision++;get('display-name').value='My pending title';get('rename-form').onsubmit({preventDefault(){}});
await until(()=>!get('rename-error').hidden);assert.equal(get('rename-dialog').open,true);assert.equal(get('display-name').value,'My pending title');assert.equal(items[0].display_name,'Friendly <scan>');get('rename-dialog').close();
get('trash-tab').onclick();await get('empty-bin').onclick();assert.equal(get('empty-bin-dialog').open,true);
assert.equal(get('empty-bin-confirm').disabled,false);assert.ok(items.some(i=>i.id==='c-scan'),'review must not delete files');
await get('empty-bin-form').onsubmit({preventDefault(){}});assert.equal(get('empty-bin-dialog').open,false);assert.ok(!items.some(i=>i.id==='c-scan'));
get('active-tab').onclick();
state.phase='scanning';await get('refresh').onclick();assert.equal(control('a-scan','open').disabled,true);assert.equal(control('a-scan','trash').disabled,true);assert.equal(control('a-scan','details').disabled,undefined);
console.log('Library search/grouping, stable open IDs, display-name safety, trash/restore, stale rename and busy-state checks passed');
