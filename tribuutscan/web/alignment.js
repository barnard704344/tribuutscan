import * as THREE from 'three';
import {OrbitControls} from '/model/OrbitControls.js';
const $=id=>document.getElementById(id), canvas=$('view');
const embedded=new URLSearchParams(location.search).get('embedded')==='1';
if(embedded)document.body.classList.add('embedded');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
const scene=new THREE.Scene();scene.background=new THREE.Color('#070e17');
const camera=new THREE.PerspectiveCamera(45,1,.0001,100), controls=new OrbitControls(camera,canvas);
controls.enableDamping=true;
const ray=new THREE.Raycaster(), pointer=new THREE.Vector2();
let clouds={}, loaded=null, epoch=0, loading=false, saving=false, awaiting=false, mode=null;
let manipulation='orbit', drag=null;
let region=null, regionSelecting=false, regionDrag=null;
const movingBox=new THREE.Box3Helper(new THREE.Box3(),0xffc45c);movingBox.visible=false;scene.add(movingBox);
let base=new THREE.Matrix4(), past=[], future=[], landmarks={reference:[],moving:[]};
const nudges=['tx','ty','tz','rx','ry','rz'];
function error(message=''){$('error').textContent=message;$('error').hidden=!message;}
async function api(path,data){
 const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-Tribuutscan':'1'},body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(25000)});
 if(response.status===401)throw Error('Pair this browser in the capture workspace first.');
 const result=await response.json();if(!response.ok)throw Error(result.error||'Host request failed');return result;
}
async function command(data){const state=await api('/api/state');return api('/api/command',{...data,revision:state.revision,instance:state.instance});}
function ready(){return !!loaded&&!loading&&!saving&&!awaiting;}
function buttons(){
 for(const id of ['centre','frame','pick-reference','pick-moving','clear-points','reset','save'])$(id).disabled=!ready();
 $('fit').disabled=!ready()||landmarks.reference.length<3||landmarks.reference.length!==landmarks.moving.length;
 $('undo').disabled=!ready()||!past.length;$('redo').disabled=!ready()||!future.length;
 $('load').disabled=loading||saving||awaiting||!$('reference').value;
 $('frame').disabled=loading||saving||awaiting||!clouds.reference;
 for(const id of ['reference','moving'])$(id).disabled=loading||saving||awaiting;
 for(const id of nudges)$(id).disabled=!ready();
 for(const id of ['refine','select-overlap','match-distance'])$(id).disabled=!ready();
 $('clear-overlap').disabled=!ready()||(!region&&!regionSelecting);
 $('select-overlap').setAttribute('aria-pressed',String(regionSelecting));
 for(const option of $('moving').options)option.disabled=!!option.value&&option.value===$('reference').value;
 syncManipulation();
 $('pair-count').textContent=`Reference: ${landmarks.reference.length} · Moving: ${landmarks.moving.length}`;
}
function matrix(){return clouds.moving?.matrix.clone()||new THREE.Matrix4();}
function apply(m,remember=true){
 clearOverlap();$('refine-report').textContent='Inspect the placement from several angles before saving.';
 if(remember){past.push(matrix());if(past.length>100)past.shift();future=[];}
 base.copy(m);clouds.moving.matrix.copy(m);clouds.moving.matrixWorldNeedsUpdate=true;
 for(const id of nudges)$(id).value='0';$('fit-report').textContent='';buttons();
}
function pickMode(next){cancelDrag();if(next){cancelRegionDrag();regionSelecting=false;manipulation='orbit';}mode=next;for(const key of ['reference','moving']){
 if(clouds[key])clouds[key].visible=mode?key===mode:$('visibility').value==='both'||$('visibility').value===key;
 $('pick-'+key).setAttribute('aria-pressed',String(mode===key));
 }$('select-overlap').setAttribute('aria-pressed',String(regionSelecting));syncManipulation();
}
function dispose(){cancelDrag();clearOverlap();movingBox.visible=false;for(const item of Object.values(clouds)){
 scene.remove(item);item.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});
 }clouds={};}
function makeCloud(buf,key){
 const [n]=new Uint32Array(buf,0,2);if(!n)throw Error('This scan has no visible points.');
 const xyz=new Float32Array(buf,8,n*3);for(const value of xyz)if(!Number.isFinite(value))throw Error('Scan contains invalid coordinates.');
 const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(xyz,3));geometry.computeBoundingBox();
 const material=new THREE.PointsMaterial({color:key==='reference'?'#53b9ff':'#ffc45c',size:.0015,sizeAttenuation:true});
 const points=new THREE.Points(geometry,material);points.matrixAutoUpdate=false;scene.add(points);return points;
}
function frame(){
 const box=new THREE.Box3();for(const item of Object.values(clouds))box.union(new THREE.Box3().setFromObject(item));
 const centre=box.getCenter(new THREE.Vector3()), size=Math.max(box.getSize(new THREE.Vector3()).length(),.01);
 controls.target.copy(centre);camera.position.copy(centre).add(new THREE.Vector3(size*.3,size*.15,size*1.5));camera.near=Math.max(size/10000,.00001);camera.far=Math.max(100,size*10);camera.updateProjectionMatrix();
 ray.params.Points.threshold=size/100;controls.update();
}
async function fetchCloud(key,name){
 const r=await fetch('/api/alignment/cloud?scan='+encodeURIComponent(name),{signal:AbortSignal.timeout(30000)});
 if(!r.ok){let result;try{result=await r.json();}catch{}throw Error(result?.error||'Could not load scan; check browser pairing.');}
 return [key,await r.arrayBuffer(),r.headers.get('X-Scan-Version')];
}
async function loadReference(){
 if(!$('reference').value)return;
 cancelDrag();manipulation='orbit';loading=true;loaded=null;buttons();error();$('result').hidden=true;const id=++epoch;
 $('status').textContent='Loading the opened scan as reference…';
 try{
  const [key,buf]=await fetchCloud('reference',$('reference').value);
  if(id!==epoch)return;
  dispose();clouds[key]=makeCloud(buf,key);base.identity();past=[];future=[];clearPoints();$('visibility').value='both';pickMode(null);frame();
  $('status').textContent='Opened scan loaded as reference. Choose a second scan to align with it.';
 }catch(e){if(id===epoch){dispose();error(e.message);}}
 finally{if(id===epoch){loading=false;buttons();}}
}
async function load(){
 if(!$('moving').value)return loadReference();
 if($('reference').value===$('moving').value)return error('Choose two different scans.');
 cancelDrag();manipulation='orbit';loading=true;loaded=null;buttons();error();$('result').hidden=true;const id=++epoch;
 $('status').textContent='Loading the second scan for alignment…';
 try{
 const names={reference:$('reference').value,moving:$('moving').value};
 const fetched=await Promise.all(Object.entries(names).map(([key,name])=>fetchCloud(key,name)));
 if(id!==epoch)return;dispose();for(const [key,buf] of fetched)clouds[key]=makeCloud(buf,key);
 loaded={...names,versions:fetched.map(x=>x[2])};base.identity();past=[];future=[];clearPoints();pickMode(null);frame();
 $('status').textContent='Both scans loaded (up to 200,000 points each). Align, inspect, then save a new session.';
 }catch(e){if(id===epoch){loaded=null;error(e.message);}}
 finally{if(id===epoch){loading=false;buttons();}}
}
function clearPoints(){
 landmarks={reference:[],moving:[]};for(const item of Object.values(clouds))for(const marker of [...item.children]){
 item.remove(marker);marker.geometry.dispose();marker.material.dispose();
 }$('fit-report').textContent='';buttons();
}
$('load').onclick=load;
for(const key of ['reference','moving']){
 $(key).onchange=()=>{cancelDrag();manipulation='orbit';loaded=null;epoch++;clearPoints();pickMode(null);$('result').hidden=true;
  if(key==='reference')dispose();
  else if(clouds.moving){scene.remove(clouds.moving);clouds.moving.geometry.dispose();clouds.moving.material.dispose();delete clouds.moving;}
  if($('reference').value===$('moving').value)$('moving').value='';
  load();
 };
 $('pick-'+key).onclick=()=>{pickMode(mode===key?null:key);$('status').textContent=mode?`Click distinct features on the ${key} scan in matching order. Click its pick button again to finish.`:'Orbit to inspect the alignment.';};
}
function syncManipulation(){
 const editing=manipulation!=='orbit'&&!mode&&!regionSelecting&&ready()&&clouds.moving?.visible;
 for(const name of ['orbit','move','rotate']){
  $('tool-'+name).setAttribute('aria-pressed',String(manipulation===name));
  $('tool-'+name).disabled=name!=='orbit'&&(!ready()||!clouds.moving?.visible);
 }
 controls.mouseButtons.LEFT=editing?null:THREE.MOUSE.ROTATE;
 controls.touches.ONE=editing?null:THREE.TOUCH.ROTATE;
 controls.enableDamping=!editing&&!regionSelecting;controls.enabled=!drag&&!regionSelecting;
 movingBox.visible=!!editing;
 canvas.style.cursor=mode||regionSelecting?'crosshair':editing?(drag?'grabbing':manipulation==='rotate'?'grab':'move'):'grab';
 $('transform-hint').textContent=mode?'Click corresponding features. Drag still orbits the camera.':editing
  ?manipulation==='move'?'Drag the amber scan to move it across the view. Release to apply; Escape cancels the drag.':'Drag the amber scan to rotate it about its centre. Release to apply; Escape cancels the drag.'
  :'Orbit to inspect. Choose Move scan or Rotate scan to drag the amber scan; the blue reference stays fixed.';
 const legend=document.querySelector('#legend span:last-child');
 if(legend)legend.textContent=editing?'Drag amber scan · right-drag pans · wheel zooms · Orbit view rotates camera':'Drag to orbit · right-drag to pan · scroll to zoom';
}
for(const name of ['orbit','move','rotate'])$('tool-'+name).onclick=()=>{
 cancelRegionDrag();regionSelecting=false;cancelDrag();mode=null;manipulation=name;pickMode(null);controls.update();buttons();
};
function cancelDrag(){
 if(!drag)return;const previous=drag;drag=null;
 if(clouds.moving){clouds.moving.matrix.copy(previous.start);clouds.moving.matrixWorldNeedsUpdate=true;}
 if(canvas.hasPointerCapture(previous.id))canvas.releasePointerCapture(previous.id);
 syncManipulation();
}
function pointerRay(event){
 const rect=canvas.getBoundingClientRect();
 pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
 camera.updateMatrixWorld(true);scene.updateMatrixWorld(true);ray.setFromCamera(pointer,camera);
 return ray.ray;
}
canvas.addEventListener('pointerdown',event=>{
 if(drag){if(event.pointerId!==drag.id)cancelDrag();return;}
 if(!ready()||mode||manipulation==='orbit'||event.button!==0||!clouds.moving.visible)return;
 pointerRay(event);if(!ray.intersectObject(clouds.moving,false).length)return;
 const centre=clouds.moving.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(matrix());
 const plane=new THREE.Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()),centre);
 const anchor=ray.ray.intersectPlane(plane,new THREE.Vector3());if(!anchor)return;
 drag={id:event.pointerId,x:event.clientX,y:event.clientY,start:matrix(),centre,plane,anchor:anchor.clone(),
  right:new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,0),up:new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,1)};
 canvas.setPointerCapture(event.pointerId);syncManipulation();event.preventDefault();
});
canvas.addEventListener('pointermove',event=>{
 if(!drag||event.pointerId!==drag.id)return;
 let result;
 if(manipulation==='move'){
  const point=pointerRay(event).intersectPlane(drag.plane,new THREE.Vector3());if(!point)return;
  result=new THREE.Matrix4().makeTranslation(...point.sub(drag.anchor).toArray()).multiply(drag.start);
 }else{
  const yaw=new THREE.Quaternion().setFromAxisAngle(drag.up,(event.clientX-drag.x)*.007);
  const pitch=new THREE.Quaternion().setFromAxisAngle(drag.right,(event.clientY-drag.y)*.007);
  const rotation=new THREE.Matrix4().makeRotationFromQuaternion(pitch.multiply(yaw));
  result=new THREE.Matrix4().makeTranslation(...drag.centre.toArray()).multiply(rotation)
   .multiply(new THREE.Matrix4().makeTranslation(...drag.centre.clone().negate().toArray())).multiply(drag.start);
 }
 clouds.moving.matrix.copy(result);clouds.moving.matrixWorldNeedsUpdate=true;
});
canvas.addEventListener('pointerup',event=>{
 if(!drag||event.pointerId!==drag.id)return;
 const previous=drag,final=matrix();drag=null;
 if(canvas.hasPointerCapture(previous.id))canvas.releasePointerCapture(previous.id);
 clouds.moving.matrix.copy(previous.start);
 if(!final.equals(previous.start))apply(final);else syncManipulation();
});
for(const event of ['pointercancel','lostpointercapture'])canvas.addEventListener(event,e=>{if(drag?.id===e.pointerId)cancelDrag();});
window.addEventListener('blur',()=>{cancelDrag();cancelRegionDrag();});
window.addEventListener('keydown',event=>{if(event.key==='Escape'){cancelRegionDrag();regionSelecting=false;cancelDrag();manipulation='orbit';pickMode(null);buttons();}});
$('visibility').onchange=()=>{if($('visibility').value==='reference')manipulation='orbit';pickMode(null);};
canvas.addEventListener('pointerdown',e=>{canvas.down={x:e.clientX,y:e.clientY};});
canvas.addEventListener('pointerup',e=>{
 if(!ready()||!mode||e.button!==0||!canvas.down||Math.hypot(e.clientX-canvas.down.x,e.clientY-canvas.down.y)>5)return;
 if(landmarks[mode].length>=12)return error('A maximum of 12 points can be used per scan.');
 const b=canvas.getBoundingClientRect();pointer.set((e.clientX-b.left)/b.width*2-1,-(e.clientY-b.top)/b.height*2+1);
 scene.updateMatrixWorld(true);ray.setFromCamera(pointer,camera);const hit=ray.intersectObject(clouds[mode],false)[0];if(!hit)return;
 const p=new THREE.Vector3().fromBufferAttribute(clouds[mode].geometry.attributes.position,hit.index);
 landmarks[mode].push(p.toArray());const marker=new THREE.Mesh(new THREE.SphereGeometry(ray.params.Points.threshold*.6,12,8),new THREE.MeshBasicMaterial({color:'#ff668c'}));
 marker.position.copy(p);clouds[mode].add(marker);error();buttons();
});
$('clear-points').onclick=clearPoints;
$('fit').onclick=async()=>{awaiting=true;buttons();error();try{
 const result=await command({action:'alignment-fit',reference_points:landmarks.reference,moving_points:landmarks.moving});
 apply(new THREE.Matrix4().set(...result.transform.flat()));pickMode(null);
 $('fit-report').textContent=`Picked-point fit: ${result.landmark_rmse_mm.toFixed(2)} mm RMS. This is not a surface-accuracy measurement.`;
 }catch(e){error(e.message);}finally{awaiting=false;buttons();}};
function clearOverlap(){
 cancelRegionDrag();region=null;regionSelecting=false;
 for(const [key,item] of Object.entries(clouds)){
  item.geometry.deleteAttribute('color');item.material.vertexColors=false;item.material.color.set(key==='reference'?'#53b9ff':'#ffc45c');item.material.needsUpdate=true;
 }
 $('overlap-status').textContent='Using whole scans. Only nearby surface matches influence refinement.';
}
function cancelRegionDrag(){
 const previous=regionDrag;regionDrag=null;$('overlap-box').setAttribute('hidden','');
 if(previous&&canvas.hasPointerCapture(previous.id))canvas.releasePointerCapture(previous.id);
}
function regionPoint(event){
 const rect=canvas.getBoundingClientRect();return {x:Math.max(0,Math.min(rect.width,event.clientX-rect.left)),y:Math.max(0,Math.min(rect.height,event.clientY-rect.top)),width:rect.width,height:rect.height};
}
function showOverlap(){
 const projection=new THREE.Matrix4().set(...region.matrix.flat()), counts=[];
 const polygon=region.polygon, xmin=Math.min(...polygon.map(p=>p[0])),xmax=Math.max(...polygon.map(p=>p[0])),ymin=Math.min(...polygon.map(p=>p[1])),ymax=Math.max(...polygon.map(p=>p[1]));
 for(const [key,item] of Object.entries(clouds)){
  const attribute=item.geometry.attributes.position,colors=new Float32Array(attribute.count*3),bright=new THREE.Color(key==='reference'?'#53b9ff':'#ffc45c'),dim=new THREE.Color('#354254');let count=0;
  const matrix=projection.clone().multiply(item.matrix);
  for(let i=0;i<attribute.count;i++){
   const p=new THREE.Vector4(attribute.getX(i),attribute.getY(i),attribute.getZ(i),1).applyMatrix4(matrix);
   const x=p.x/p.w,y=p.y/p.w,z=p.z/p.w,inside=p.w>0&&z>=-1&&z<=1&&x>=xmin&&x<=xmax&&y>=ymin&&y<=ymax;
   (inside?bright:dim).toArray(colors,i*3);if(inside)count++;
  }
  counts.push(`${key}: ${count.toLocaleString()}`);item.geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));item.material.color.set('#ffffff');item.material.vertexColors=true;item.material.needsUpdate=true;
 }
 $('overlap-status').textContent=`Selected preview points — ${counts.join(' · ')}. Dim points are excluded. Moving either scan clears this selection.`;
}
$('select-overlap').onclick=()=>{
 const next=!regionSelecting;clearOverlap();cancelDrag();mode=null;manipulation='orbit';pickMode(null);regionSelecting=next;
 if(next){$('visibility').value='both';pickMode(null);$('overlap-status').textContent='Drag a box across the shared surface of both scans. Escape cancels.';}
 buttons();
};
$('clear-overlap').onclick=()=>{clearOverlap();buttons();};
canvas.addEventListener('pointerdown',event=>{
 if(!regionSelecting||!ready())return;
 event.stopImmediatePropagation();event.preventDefault();
 if(event.button!==0)return;
 if(regionDrag){cancelRegionDrag();return;}
 regionDrag={id:event.pointerId,start:regionPoint(event)};canvas.setPointerCapture(event.pointerId);
},true);
canvas.addEventListener('pointermove',event=>{
 if(!regionDrag||event.pointerId!==regionDrag.id)return;
 event.stopImmediatePropagation();const p=regionPoint(event),a=regionDrag.start,box=$('overlap-box');
 $('overlap-overlay').setAttribute('viewBox',`0 0 ${p.width} ${p.height}`);
 for(const [name,value] of Object.entries({x:Math.min(a.x,p.x),y:Math.min(a.y,p.y),width:Math.abs(a.x-p.x),height:Math.abs(a.y-p.y)}))box.setAttribute(name,value);
 box.removeAttribute('hidden');
},true);
canvas.addEventListener('pointerup',event=>{
 if(!regionDrag||event.pointerId!==regionDrag.id)return;
 event.stopImmediatePropagation();const a=regionDrag.start,p=regionPoint(event);cancelRegionDrag();regionSelecting=false;
 if(Math.abs(a.x-p.x)<8||Math.abs(a.y-p.y)<8){error('Draw a larger overlap box.');buttons();return;}
 camera.updateMatrixWorld(true);const projection=camera.projectionMatrix.clone().multiply(camera.matrixWorldInverse),e=projection.elements;
 const xmin=Math.min(a.x,p.x)/p.width*2-1,xmax=Math.max(a.x,p.x)/p.width*2-1,ymin=1-Math.max(a.y,p.y)/p.height*2,ymax=1-Math.min(a.y,p.y)/p.height*2;
 region={matrix:Array.from({length:4},(_,r)=>Array.from({length:4},(_,c)=>e[c*4+r])),polygon:[[xmin,ymin],[xmax,ymin],[xmax,ymax],[xmin,ymax]]};
 showOverlap();error();buttons();
},true);
for(const type of ['pointercancel','lostpointercapture'])canvas.addEventListener(type,event=>{if(regionDrag?.id===event.pointerId){cancelRegionDrag();buttons();}});
$('refine').onclick=async()=>{
 if(!$('match-distance').reportValidity())return;
 cancelDrag();cancelRegionDrag();regionSelecting=false;pickMode(null);awaiting=true;buttons();error();
 const id=epoch;
 $('refine-report').textContent='Refining shared surfaces on the host…';
 try{
  const instance=(await api('/api/state')).instance,deadline=Date.now()+120000;
  const elements=matrix().elements,transform=Array.from({length:4},(_,r)=>Array.from({length:4},(_,c)=>elements[c*4+r]));
  const job=await command({action:'alignment-refine',...loaded,transform,max_distance_mm:Number($('match-distance').value),region});
  for(;;){
   if(Date.now()>deadline)throw Error('The host is still busy. Check Host status before retrying.');
   await new Promise(resolve=>setTimeout(resolve,500));const state=await api('/api/state');
   if(state.instance!==instance)throw Error('Host restarted; keep the manual placement and retry.');
   if(state.processing)continue;
   const refinement=state.alignment_refinement;
   if(refinement?.job!==job.job)throw Error('Another refinement replaced this result. Retry with the current scans.');
   if(refinement.error||state.job_failed)throw Error(refinement.error||state.job_result||'Refinement failed');
   const result=refinement.result;if(!result)throw Error('Refinement did not return a proposal');
   if(id!==epoch)return;
   apply(new THREE.Matrix4().set(...result.transform.flat()));
   $('refine-report').textContent=`Surface match: ${result.before_rmse_mm.toFixed(2)} → ${result.after_rmse_mm.toFixed(2)} mm RMS; moving-scan overlap ${(result.before_overlap*100).toFixed(0)}% → ${(result.after_overlap*100).toFixed(0)}% within ${result.max_distance_mm} mm. Shift ${result.shift_mm.toFixed(2)} mm, turn ${result.rotation_degrees.toFixed(2)}°. Sampled surfaces only; inspect the result. Undo restores the previous placement.`;
   $('status').textContent='Refinement preview applied. Inspect from several angles, then save or Undo.';break;
  }
 }catch(e){error(e.message);$('refine-report').textContent='Refinement was not applied. Your previous placement is unchanged.';}
 finally{awaiting=false;buttons();}
};
$('centre').onclick=()=>{const a=clouds.reference.geometry.boundingBox.getCenter(new THREE.Vector3()),b=clouds.moving.geometry.boundingBox.getCenter(new THREE.Vector3());apply(new THREE.Matrix4().makeTranslation(...a.sub(b).toArray()));pickMode(null);frame();};
$('frame').onclick=frame;
$('reset').onclick=()=>apply(new THREE.Matrix4());
$('undo').onclick=()=>{future.push(matrix());apply(past.pop(),false);};
$('redo').onclick=()=>{past.push(matrix());apply(future.pop(),false);};
function nudge(commit=false){
 if(!ready())return;const values=nudges.map(id=>Number($(id).value));if(values.some(v=>!Number.isFinite(v))||nudges.some(id=>!$(id).checkValidity()))return;
 const centre=clouds.moving.geometry.boundingBox.getCenter(new THREE.Vector3()).applyMatrix4(base),delta=new THREE.Vector3(...values.slice(0,3)).multiplyScalar(.001);
 const q=new THREE.Quaternion().setFromEuler(new THREE.Euler(...values.slice(3).map(THREE.MathUtils.degToRad),'XYZ'));
 const m=new THREE.Matrix4().makeTranslation(...centre.clone().add(delta).toArray()).multiply(new THREE.Matrix4().makeRotationFromQuaternion(q)).multiply(new THREE.Matrix4().makeTranslation(...centre.clone().negate().toArray())).multiply(base);
 clouds.moving.matrix.copy(m);clouds.moving.matrixWorldNeedsUpdate=true;
 if(commit){clouds.moving.matrix.copy(base);apply(m);}
}
for(const id of nudges){$(id).addEventListener('input',()=>nudge());$(id).addEventListener('change',()=>nudge(true));}
$('save').onclick=async()=>{
 saving=true;buttons();error();pickMode(null);try{
 const m=matrix(),elements=m.elements,transform=Array.from({length:4},(_,r)=>Array.from({length:4},(_,c)=>elements[c*4+r]));
 const job=await command({action:'alignment-save',...loaded,transform});$('status').textContent='Saving a new aligned session on the host…';
 while(saving){await new Promise(r=>setTimeout(r,1000));const status=await api('/api/state');if(status.processing)continue;
 if(!status.scans.includes(job.output))throw Error(status.message||'Alignment did not produce a new session.');
 $('result').onclick=async event=>{event.preventDefault();try{await command({action:'open',name:job.output});if(embedded)parent.postMessage({type:'alignment-result'},location.origin);else location.href='/model/';}catch(e){error(e.message);}};
 $('result').hidden=false;$('status').textContent=`Saved ${job.output}. Open the result to inspect, crop and mesh it.`;break;}
 }catch(e){error(e.message);}finally{saving=false;buttons();}
};
new ResizeObserver(()=>{const r=canvas.parentElement.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();}).observe(canvas.parentElement);
const animate=()=>{controls.update();if(movingBox.visible&&clouds.moving)movingBox.box.setFromObject(clouds.moving);renderer.render(scene,camera);};
renderer.setAnimationLoop(animate);
if(embedded){window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===parent&&event.data?.type==='alignment-visible'){if(!event.data.visible){cancelDrag();cancelRegionDrag();}renderer.setAnimationLoop(event.data.visible?animate:null);}});parent.postMessage({type:'alignment-ready'},location.origin);}
try{
 const state=await api('/api/state');
 const options=()=>state.scans.map(name=>{const option=document.createElement('option');option.value=option.textContent=name;return option;});
 $('reference').replaceChildren(...options());
 const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Choose a second scan…';
 $('moving').replaceChildren(placeholder,...options());$('moving').value='';
 const requested=new URLSearchParams(location.search).get('reference');
 const reference=state.scans.includes(requested)?requested:state.selected;
 if(state.scans.includes(reference))$('reference').value=reference;
 $('load').textContent='Reload previews';
 if($('reference').value)await loadReference();
 else{$('status').textContent='No saved scans yet. Capture a scan, then open it from Files.';buttons();}
}catch(e){error(e.message);$('load').disabled=true;}
