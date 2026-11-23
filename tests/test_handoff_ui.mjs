import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const html=fs.readFileSync(new URL('../tribuutscan/web/index.html',import.meta.url),'utf8');
const source=html.slice(html.indexOf('// Desktop-only file handoff:'),html.indexOf('function selectTool(name,'));
function fixture(phone=false){
 const nodes=new Map(),downloads=[],requests=[],messages=[],revoked=[],changes=[];
 const element=id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id);};
 const media={matches:phone,addEventListener:(event,fn)=>changes.push(fn)};
 const ctx={lastInfo:{phase:'done',name:'current-scan',version:'cloud-v2',mesh:'mesh-v3'},busy:false,tool:'view',
  $:element,URLSearchParams,URL:{createObjectURL:()=> 'blob:local-mesh',revokeObjectURL:url=>revoked.push(url)},
  document:{body:{append(){}},createElement(){return {click(){downloads.push({href:this.href,name:this.download});},remove(){}};}},
  window:{matchMedia:()=>media},setTimeout:fn=>fn(),updateTools(){},status:(message,error=false)=>messages.push({message,error}),
  selectTool(name){ctx.tool=name;},fetch:async url=>{requests.push(url);return {ok:true,blob:async()=> 'mesh'};}};
 vm.createContext(ctx);vm.runInContext(source,ctx);return {ctx,nodes,downloads,requests,messages,revoked,media,changes};
}
{
 const f=fixture();await f.nodes.get('cad-download').onclick();
 assert.equal(f.downloads[0].name,'current-scan-surface-mm.stl');assert.equal(f.downloads[0].href,'blob:local-mesh');
 const url=new URL(f.requests[0],'http://scanner/model/');assert.equal(url.pathname,'/model/handoff.stl');
 assert.equal(url.searchParams.get('name'),'current-scan');assert.equal(url.searchParams.get('version'),'cloud-v2');assert.equal(url.searchParams.get('mesh'),'mesh-v3');
 assert.equal(f.ctx.busy,false);assert.match(f.messages.at(-1).message,/millimetres/);assert.deepEqual(f.revoked,['blob:local-mesh']);
}
{
 const f=fixture();f.ctx.fetch=async()=>({ok:false,status:409});await f.nodes.get('cad-download').onclick();
 assert.equal(f.downloads.length,0);assert.equal(f.ctx.busy,false);assert.equal(f.messages.at(-1).error,true);assert.match(f.messages.at(-1).message,/changed/);
 f.ctx.lastInfo.mesh=null;await f.nodes.get('cad-download').onclick();assert.equal(f.downloads.length,0);
}
{
 const f=fixture(true);assert.equal(f.nodes.get('tab-cad').hidden,true);
 f.media.matches=false;f.changes[0]();assert.equal(f.nodes.get('tab-cad').hidden,false);
 f.ctx.tool='cad';f.media.matches=true;f.changes[0]();assert.equal(f.ctx.tool,'view');
}
assert.match(html,/href="https:\/\/scanruler.com\/\?workspace=reverse" target="_blank" rel="noopener noreferrer"/);
console.log('Desktop handoff download identity, mm guidance, stale errors, private routes and phone exclusion passed');
