import * as THREE from 'three';
import {OrbitControls} from '/model/OrbitControls.js';
const $=id=>document.getElementById(id),params=new URLSearchParams(location.search);
if(params.get('embedded')==='1')document.body.classList.add('embedded');
const renderer=new THREE.WebGLRenderer({canvas:$('view'),antialias:true}),scene=new THREE.Scene();scene.background=new THREE.Color('#070e17');renderer.setPixelRatio(Math.min(devicePixelRatio,2));
const camera=new THREE.PerspectiveCamera(45,1,.0001,100),controls=new OrbitControls(camera,$('view'));controls.enableDamping=true;
let scan=params.get('scan'),data=null,objects={},busy=false,epoch=0,pending=null,modelVersion=null,visible=true;
function error(text=''){$('error').textContent=text;$('error').hidden=!text;}
async function api(path){const r=await fetch(path,{signal:AbortSignal.timeout(45000)});const result=await r.json();if(!r.ok)throw Error(result.error||'Could not read frame data');return result;}
function range(){const first=Number($('first').value),last=Number($('last').value);if(!Number.isInteger(first)||!Number.isInteger(last)||first<0||last<first)throw Error('Choose an inclusive frame range');return {first,last};}
function buttons(){for(const id of ['reload','preview','first','last','timeline','scrub','reset'])$(id).disabled=busy||(!data&&id!=='reload');for(const id of ['exclude','include'])$(id).disabled=busy||!data?.editable;
 try{const selected=range(),hasPose=data?.rows.some(row=>row.has_pose&&selected.first<=row.index&&row.index<=selected.last);if(!hasPose){$('exclude').disabled=true;$('include').disabled=true;}}catch{$('exclude').disabled=true;$('include').disabled=true;}
 $('reset').disabled=busy||!data?.editable||!data?.operations;
 $('align').disabled=busy||!data?.editable||!objects.reference?.geometry.attributes.position.count||!objects.moving?.geometry.attributes.position.count;
 $('undo').disabled=busy||!pending?.can_undo;$('redo').disabled=busy||!pending?.can_redo;}
function details(index){const row=data?.rows[index];if(!row)return;$('details').textContent=`Frame ${row.index} · ${row.status} · ${row.has_pose?(row.enabled?'included':'excluded'):'no pose'} · ${row.keyframe?'keyframe samples':'pose only'}${row.fitness==null?'':' · overlap '+Math.round(row.fitness*100)+'%'}${row.rmse_mm==null?'':' · residual '+row.rmse_mm.toFixed(2)+' mm'}`;}
function clear(){for(const object of Object.values(objects)){scene.remove(object);object.geometry.dispose();object.material.dispose();}objects={};}
function cloud(buffer,key){const n=new Uint32Array(buffer,0,2)[0],positions=new Float32Array(buffer,8,n*3),g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(positions,3));const p=new THREE.Points(g,new THREE.PointsMaterial({color:key==='moving'?'#ffc45c':'#53b9ff',size:.0015}));scene.add(p);return p;}
function fit(){const box=new THREE.Box3();for(const [key,object] of Object.entries(objects))if(object.geometry.attributes.position.count&&(key!=='poses'||!objects.moving?.geometry.attributes.position.count))box.union(new THREE.Box3().setFromObject(object));if(box.isEmpty())return;const center=box.getCenter(new THREE.Vector3()),size=Math.max(box.getSize(new THREE.Vector3()).length(),.02);controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(size*.3,size*.2,size*1.5));camera.near=Math.max(size/10000,.00001);camera.far=Math.max(100,size*10);camera.updateProjectionMatrix();controls.update();}
function visibility(){for(const [key,obj] of Object.entries(objects))obj.visible=key==='poses'?$('visibility').value!=='reference':$('visibility').value==='both'||$('visibility').value===key;}
async function preview(){if(!data||busy)return;busy=true;buttons();error();const id=++epoch;
 try{const selected=range(),query=new URLSearchParams({scan,...selected,signature:data.signature});const parts=await Promise.all(['reference','moving'].map(async key=>{const r=await fetch('/api/frames/preview?'+query+'&part='+key,{signal:AbortSignal.timeout(45000)});if(!r.ok){const e=await r.json();throw Error(e.error);}return [key,await r.arrayBuffer()];}));if(id!==epoch)return;clear();for(const [key,buffer] of parts)objects[key]=cloud(buffer,key);
 // Camera positions let accepted non-keyframes remain inspectable without inventing depth samples.
 const positions=data.rows.filter(row=>row.position&&selected.first<=row.index&&row.index<=selected.last).flatMap(row=>row.position);
 if(positions.length){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));objects.poses=new THREE.Points(g,new THREE.PointsMaterial({color:'#ff6496',size:.003}));scene.add(objects.poses);}
 visibility();fit();$('track').textContent='Pink dots show saved camera positions for this range.';$('status').textContent='Preview only. Select a range, then rebuild or align it.';
 }catch(e){error(e.message);}finally{busy=false;buttons();}}
async function load(){busy=true;buttons();error();try{data=await api('/api/frames/catalogue?scan='+encodeURIComponent(scan));const options=data.rows.map((row,index)=>{const option=document.createElement('option');option.value=index;option.textContent=`${row.index}  ${row.has_pose?(row.enabled?'✓':'×'):'–'} ${row.keyframe?'K':' '} ${row.status}`;return option;});$('timeline').replaceChildren(...options);$('scrub').max=Math.max(0,data.rows.length-1);const initial=Math.max(0,data.rows.findIndex(row=>row.keyframe&&row.enabled));$('scrub').value=initial;$('timeline').value=initial;details(initial);$('first').value=$('last').value=data.rows[initial]?.index||0;
 $('summary').textContent=`${data.rows.length.toLocaleString()} recorded statuses · ${data.rows.filter(row=>row.enabled).length.toLocaleString()} accepted poses included`;
 if(!data.editable)error(data.reason);
 const info=await api('/model/info.json');if(info.name!==scan)throw Error('The opened scan changed. Return to the saved workspace.');pending=info.editing;modelVersion=info.version;}
 catch(e){data=null;clear();error(e.message);}finally{busy=false;buttons();}if(data)await preview();}
function inspect(index){const row=data.rows[index];$('timeline').value=index;$('scrub').value=index;$('first').value=$('last').value=row.index;details(index);}
$('timeline').onchange=()=>{inspect(Number($('timeline').value));preview();};$('scrub').oninput=()=>inspect(Number($('scrub').value));$('scrub').onchange=preview;
$('preview').onclick=preview;$('reload').onclick=load;$('fit-view').onclick=fit;$('visibility').onchange=visibility;
async function mutate(operation,extra={}){if(busy||!data)return;if(parent===window){error('Open the Frames tab in the saved-scan workspace to save edits.');return;}try{const selected=range();busy=true;buttons();error();$('status').textContent='Rebuilding on the host; source scan is preserved…';parent.postMessage({type:'frame-edit',name:scan,model_version:modelVersion,operation,rule:{...selected,signature:data.signature,...extra}},location.origin);}catch(e){busy=false;buttons();error(e.message);}}
$('exclude').onclick=()=>mutate('frame-exclude');$('include').onclick=()=>mutate('frame-include');$('reset').onclick=()=>mutate('frame-reset');$('undo').onclick=()=>mutate('undo');$('redo').onclick=()=>mutate('redo');
$('align').onclick=()=>{try{const selected=range();$('section-align').src='/alignment?embedded=1&frame_scan='+encodeURIComponent(scan)+'&'+new URLSearchParams({...selected,signature:data.signature});$('section-align').hidden=false;$('back').hidden=false;renderer.setAnimationLoop(null);}catch(e){error(e.message);}};
$('back').onclick=()=>{$('section-align').hidden=true;$('back').hidden=true;$('section-align').src='about:blank';renderer.setAnimationLoop(visible?animate:null);};
window.addEventListener('message',async event=>{if(event.origin!==location.origin)return;
 if(event.source===parent&&event.data?.type==='frame-edit-result'){busy=false;if(!event.data.ok){buttons();error(event.data.message);return;}scan=event.data.name;$('back').onclick();await load();$('status').textContent=event.data.message;}
 if(event.source===$('section-align').contentWindow&&event.data?.type==='frame-section-save'){const selected=range();if(event.data.first!==selected.first||event.data.last!==selected.last||event.data.signature!==data.signature){error('The frame selection changed; reload the section alignment.');return;}const transform=event.data.transform;$('back').onclick();await mutate('frame-transform',{transform});}
 if(event.source===$('section-align').contentWindow&&event.data?.type==='alignment-ready')$('section-align').contentWindow.postMessage({type:'alignment-visible',visible:!$('section-align').hidden},location.origin);
 if(event.source===parent&&event.data?.type==='frames-visible'){visible=event.data.visible;renderer.setAnimationLoop(visible&&$('section-align').hidden?animate:null);if(!visible)$('back').onclick();}
});
new ResizeObserver(()=>{const r=$('view').parentElement.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();}).observe($('view').parentElement);
const animate=()=>{controls.update();renderer.render(scene,camera);};renderer.setAnimationLoop(animate);
parent.postMessage({type:'frames-ready'},location.origin);await load();
