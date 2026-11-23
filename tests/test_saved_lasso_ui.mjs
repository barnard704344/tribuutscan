// Exercise the saved workspace's actual lasso handlers without WebGL or a scanner.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const THREE=await import('../tribuutscan/web/three.module.min.js');
const html=fs.readFileSync(new URL('../tribuutscan/web/index.html',import.meta.url),'utf8');
const source=html.slice(html.indexOf('async function waitRenderedChange('),html.indexOf('async function runAction('))
 +html.slice(html.indexOf('async function runAction('),html.indexOf('function updateTools('))
 +html.slice(html.indexOf('async function cleanupAction('),html.indexOf("for(const button of document.querySelectorAll('[data-direction]'))"));
function fixture(){
 const elements=new Map(),listeners=new Map(),capture=new Set(),messages=[];
 function element(id){if(!elements.has(id))elements.set(id,{textContent:'',disabled:false,width:200,height:200,value:'cloud',getContext:()=>({clearRect(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){},stroke(){}})});return elements.get(id);}
 const canvas={getBoundingClientRect:()=>({left:0,top:0,width:200,height:200}),
  addEventListener(type,handler){listeners.set(type,handler);},setPointerCapture(id){capture.add(id);},hasPointerCapture(id){return capture.has(id);},
  releasePointerCapture(id){capture.delete(id);listeners.get('lostpointercapture')?.({pointerId:id});}};
 const camera=new THREE.PerspectiveCamera(45,1,.1,10);camera.updateMatrixWorld(true);
 const xyz=new Float32Array([-.2,0,-1,.2,0,-1,0,.2,-1]);
 const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(xyz,3));
 const context={THREE,console,setTimeout,Date,Promise,canvas,camera,points:new THREE.Points(geometry,new THREE.PointsMaterial()),
  data:{n:3,xyz},lastInfo:{name:'source-scan',version:'v1',phase:'done',editing:{}},renderedModel:{name:'source-scan',version:'v1'},
  controls:{enabled:true,enableDamping:true,update(){}},busy:false,lasso:false,lassoPointer:null,selectedRegion:null,stroke:[],selectionKeep:null,selectionCount:0,deletePreview:null,desiredMesh:true,cloudKey:'v1',
  $:element,colours:()=>new Float32Array(9),updateTools(){},showWhich(){},status(message,error=false){messages.push({message,error});},
  closeCrop(){context.cropPreviewClosed=true;},editable:async()=>{},host:async()=>({revision:1,instance:'test'}),savedCommand:async()=>{},waitJob:async()=>'',window:{addEventListener(){}},messages};
 vm.createContext(context);vm.runInContext(source,context);
 function pointer(type,x,y,id=1){listeners.get(type)?.({pointerId:id,button:0,clientX:x,clientY:y,preventDefault(){},stopImmediatePropagation(){}});}
 function select(){element('lasso-start').onclick();pointer('pointerdown',30,30);pointer('pointermove',100,30);pointer('pointermove',100,170);pointer('pointermove',30,170);pointer('pointerup',30,30);}
 return {context,element,pointer,select};
}
{
 const f=fixture();f.element('lasso-start').onclick();f.pointer('pointerdown',30,30);f.pointer('pointermove',100,30);f.pointer('pointercancel',100,30);
 assert.equal(f.context.selectedRegion,null);assert.equal(f.context.lassoPointer,null);assert.match(f.element('selection-count').textContent,/cancelled/);
 f.select();assert.equal(f.context.selectionCount,1);assert.ok(f.context.selectedRegion);assert.equal(f.context.controls.enableDamping,false);
 // Completing a gesture releases capture without accidentally cancelling its finished selection.
 assert.equal(f.context.lassoPointer,null);assert.equal(f.context.selectionKeep.length,2);
}
{
 const f=fixture();f.select();let finish;
 f.context.waitJob=()=>new Promise(resolve=>{finish=resolve;});
 const operation=f.element('lasso-delete').onclick();
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(f.context.points.geometry.index.count,2,'pending deletion should immediately hide selected points');
 assert.equal(f.context.busy,true);
 f.context.lastInfo={...f.context.lastInfo,version:'v2'};
 finish('Removed 1 point; 2 remain.');
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(f.context.busy,true,'do not declare completion before the saved model has reloaded');
 f.context.renderedModel={name:'source-scan',version:'v2'};
 await operation;
 assert.equal(f.context.busy,false);assert.equal(f.context.selectedRegion,null);assert.match(f.context.messages.at(-1).message,/Removed 1/);
}
{
 const f=fixture();f.select();f.context.savedCommand=async()=>{throw Error('Simulated host failure');};
 await f.element('lasso-delete').onclick();
 assert.equal(f.context.points.geometry.index,null,'a failed save must restore the local preview');
 assert.equal(f.context.busy,false);assert.equal(f.context.messages.at(-1).error,true);assert.match(f.context.messages.at(-1).message,/Simulated host failure/);
}
for(const action of ['crop','crop-off']){
 const f=fixture();f.context.waitJob=async()=>{f.context.lastInfo={...f.context.lastInfo,version:'v2'};return 'cropped: 2 points';};
 const operation=f.context.runAction(action,'cropping');
 await new Promise(resolve=>setTimeout(resolve,20));assert.equal(f.context.busy,true,'crop must wait for the updated geometry');
 f.context.renderedModel={name:'source-scan',version:'v2'};await operation;
 assert.equal(f.context.busy,false);assert.match(f.context.messages.at(-1).message,/cropped/);
 if(action==='crop-off')assert.equal(f.context.cropPreviewClosed,true,'Clear crop must also remove any active preview clipping');
}
{
 const f=fixture();f.context.waitJob=async()=>'mesh available';
 assert.equal(await f.context.runAction('mesh','meshing'),'mesh available');
 assert.equal(f.context.busy,false,'mesh generation need not change the point-cloud version');
}
console.log('Saved lasso selection/cancellation, pending deletion rollback, crop reload and mesh completion checks passed');
