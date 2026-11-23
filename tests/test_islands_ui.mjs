import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const THREE=await import('../tribuutscan/web/three.module.min.js');
const html=fs.readFileSync(new URL('../tribuutscan/web/index.html',import.meta.url),'utf8');
const source=html.slice(html.indexOf('// Island previews alter'),html.indexOf('async function cleanupAction('));
function fixture(){
 const elements=new Map(),messages=[],applied=[];
 const $=id=>{if(!elements.has(id))elements.set(id,{value:id==='island-threshold'?'2':'',textContent:''});return elements.get(id);};
 const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute([0,0,0,1,0,0,0,1,0,0,0,1,5,0,0,6,0,0,5,1,0],3));
 geometry.setIndex([0,2,1,0,1,3,0,3,2,1,2,3,4,5,6]);
 const object=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial()),scene=new THREE.Scene();scene.add(object);
 const result={triangles:5,kept_triangles:4,removed_triangles:1,removed_components:1,components:2,mask:'EA==',min_triangles:2,mesh_hash:'a'.repeat(64)};
 const ctx={THREE,Uint8Array,Uint32Array,Float32Array,Number,atob,$,scene,islandPreview:null,busy:false,lastInfo:{name:'source-scan',version:'v1',mesh:'m1',phase:'done'},shapes:{mesh:{obj:object,version:'m1'}},
  clearLasso(){},showWhich(){},updateTools(){},status:(text,error=false)=>messages.push({text,error}),savedCommand:async()=>({job:'job1'}),waitJob:async()=>{},
  host:async path=>path.startsWith('/api/islands/')?({job:'job1',result}):({mesh_islands_preview:{job:'job1',result}}),loadMesh:async()=>true,cleanupAction:async(...args)=>applied.push(args)};
 vm.createContext(ctx);vm.runInContext(source,ctx);return {ctx,$,object,result,messages,applied};
}
{
 const f=fixture(),original=f.object.geometry.index;
 await f.$('island-preview').onclick();assert.equal(f.object.geometry.index.count,12);assert.equal(f.ctx.islandPreview.overlay.geometry.getAttribute('position').count,3);
 assert.match(f.$('island-status').textContent,/1 of 2/);assert.equal(f.applied.length,0,'preview must not edit');
 f.$('island-cancel').onclick();assert.equal(f.object.geometry.index,original);assert.equal(f.ctx.islandPreview,null);
 await f.$('island-preview').onclick();await f.$('island-apply').onclick();
 assert.deepEqual(f.applied[0][0],'remove-islands');assert.equal(f.applied[0][1].mesh_hash,f.result.mesh_hash);assert.equal(f.ctx.islandPreview,null);
}
{
 const f=fixture();f.$('island-threshold').value='bad';await f.$('island-preview').onclick();assert.equal(f.ctx.islandPreview,null);assert.equal(f.messages.at(-1).error,true);
 f.$('island-threshold').value='2';f.ctx.host=async()=>({mesh_islands_preview:{job:'other',result:f.result}});await f.$('island-preview').onclick();assert.equal(f.ctx.islandPreview,null);assert.equal(f.messages.at(-1).error,true);
 f.ctx.host=async()=>{f.ctx.lastInfo.name='other-scan';return {mesh_islands_preview:{job:'job1',result:f.result}};};await f.$('island-preview').onclick();assert.equal(f.ctx.islandPreview,null);assert.equal(f.applied.length,0);
}
console.log('Island preview colouring/index restoration, explicit apply, invalid thresholds and stale selection checks passed');
