import * as THREE from 'three';
import {OrbitControls} from '/model/OrbitControls.js';
const $=id=>document.getElementById(id), canvas=$('view');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
const scene=new THREE.Scene();scene.background=new THREE.Color('#070e17');
const camera=new THREE.PerspectiveCamera(45,1,.0001,100), controls=new OrbitControls(camera,canvas);
controls.enableDamping=true;
const ray=new THREE.Raycaster(), pointer=new THREE.Vector2();
let clouds={}, loaded=null, epoch=0, loading=false, saving=false, awaiting=false, mode=null;
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
 $('load').disabled=loading||saving||awaiting;
 for(const id of ['reference','moving'])$(id).disabled=loading||saving||awaiting;
 for(const id of nudges)$(id).disabled=!ready();
 $('pair-count').textContent=`Reference: ${landmarks.reference.length} · Moving: ${landmarks.moving.length}`;
}
function matrix(){return clouds.moving?.matrix.clone()||new THREE.Matrix4();}
function apply(m,remember=true){
 if(remember){past.push(matrix());if(past.length>100)past.shift();future=[];}
 base.copy(m);clouds.moving.matrix.copy(m);clouds.moving.matrixWorldNeedsUpdate=true;
 for(const id of nudges)$(id).value='0';$('fit-report').textContent='';buttons();
}
function pickMode(next){mode=next;for(const key of ['reference','moving']){
 if(clouds[key])clouds[key].visible=mode?key===mode:$('visibility').value==='both'||$('visibility').value===key;
 $('pick-'+key).setAttribute('aria-pressed',String(mode===key));
 }canvas.style.cursor=mode?'crosshair':'grab';
}
function dispose(){for(const item of Object.values(clouds)){
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
async function load(){
 if($('reference').value===$('moving').value)return error('Choose two different scans.');
 loading=true;loaded=null;buttons();error();$('result').hidden=true;const id=++epoch;
 try{
 const names={reference:$('reference').value,moving:$('moving').value};
 const fetched=await Promise.all(Object.entries(names).map(async([key,name])=>{
 const r=await fetch('/api/alignment/cloud?scan='+encodeURIComponent(name),{signal:AbortSignal.timeout(30000)});
 if(!r.ok){let result;try{result=await r.json();}catch{}throw Error(result?.error||'Could not load scan; check browser pairing.');}
 return [key,await r.arrayBuffer(),r.headers.get('X-Scan-Version')];}));
 if(id!==epoch)return;dispose();for(const [key,buf] of fetched)clouds[key]=makeCloud(buf,key);
 loaded={...names,versions:fetched.map(x=>x[2])};base.identity();past=[];future=[];clearPoints();pickMode(null);frame();
 $('status').textContent='Previews loaded (up to 200,000 points per scan). Align, inspect, then save a new session.';
 }catch(e){dispose();loaded=null;error(e.message);}finally{loading=false;buttons();}
}
function clearPoints(){
 landmarks={reference:[],moving:[]};for(const item of Object.values(clouds))for(const marker of [...item.children]){
 item.remove(marker);marker.geometry.dispose();marker.material.dispose();
 }$('fit-report').textContent='';buttons();
}
$('load').onclick=load;
for(const key of ['reference','moving']){
 $(key).onchange=()=>{loaded=null;epoch++;dispose();clearPoints();pickMode(null);$('result').hidden=true;$('status').textContent='Load the selected scans to begin.';buttons();};
 $('pick-'+key).onclick=()=>{pickMode(mode===key?null:key);$('status').textContent=mode?`Click distinct features on the ${key} scan in matching order. Click its pick button again to finish.`:'Orbit to inspect the alignment.';};
}
$('visibility').onchange=()=>pickMode(null);
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
 $('result').onclick=async event=>{event.preventDefault();try{await command({action:'open',name:job.output});location.href='/model/';}catch(e){error(e.message);}};
 $('result').hidden=false;$('status').textContent=`Saved ${job.output}. Open the result to inspect, crop and mesh it.`;break;}
 }catch(e){error(e.message);}finally{saving=false;buttons();}
};
new ResizeObserver(()=>{const r=canvas.parentElement.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();}).observe(canvas.parentElement);
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera);});
try{
 const state=await api('/api/state');for(const key of ['reference','moving']){
 $(key).replaceChildren(...state.scans.map(name=>{const option=document.createElement('option');option.value=option.textContent=name;return option;}));
 }if(state.scans.length>1)$('moving').selectedIndex=1;
 $('load').disabled=state.scans.length<2;$('status').textContent=state.scans.length<2?'Two saved scans are needed. You can return here after capturing them.':'Choose two saved scans, then load their previews.';
}catch(e){error(e.message);$('load').disabled=true;}
