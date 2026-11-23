'use strict';
const $ = id => document.getElementById(id);
const toolTabs=Array.from(document.querySelectorAll('[data-tab]'));
function selectToolTab(name, focus=false) {
  if(!toolTabs.some(tab=>tab.dataset.tab===name))return;
  for(const tab of toolTabs) {
    const selected=tab.dataset.tab===name;
    tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
    if(selected&&focus)tab.focus();
  }
  for(const panel of document.querySelectorAll('[data-tab-panel]'))panel.hidden=panel.dataset.tabPanel!==name;
}
for(const [index,tab] of toolTabs.entries()) {
  tab.addEventListener('click',()=>selectToolTab(tab.dataset.tab));
  tab.addEventListener('keydown',event=>{
    const next={ArrowRight:(index+1)%toolTabs.length,ArrowLeft:(index+toolTabs.length-1)%toolTabs.length,Home:0,End:toolTabs.length-1}[event.key];
    if(next!==undefined){event.preventDefault();selectToolTab(toolTabs[next].dataset.tab,true);}
  });
}
let state = null, pending = false, paired = false, stale = true;
let lastReceived = 0, lastDistance = null;
let inspection=null, inspectionEpoch=0;
const titles = {idle:'Ready',counting:'Starting…',scanning:'Scanning',pausing:'Pausing…',paused:'Paused for inspection',resuming:'Resuming…',saving:'Saving…',optimising:'Optimising…',meshing:'Building mesh…',cancelling:'Cancelling…',disconnected:'Scanner disconnected'};
async function json(url, data) {
  const response = await fetch(url, {method:data === undefined ? 'GET' : 'POST',
    headers:data === undefined ? {} : {'Content-Type':'application/json','X-Tribuutscan':'1'},
    body:data === undefined ? undefined : JSON.stringify(data), signal:AbortSignal.timeout(25000)});
  const result = await response.json();
  if (response.status === 401) { paired = false; state=null; showPairing(); }
  if (!response.ok) throw new Error(result.error || 'The host could not complete this request');
  return result;
}
async function pair(token) {
  $('pair-error').textContent='';
  try {
    await json('/api/pair',{token});
    history.replaceState(null,'',location.pathname);
    $('token').value='';
    paired=true; state=null; lastDistance=null;
    await poll();
  } catch(e) {
    showPairing();
    $('pair-error').textContent=e.message;
  }
}
$('pair-form').addEventListener('submit',event=>{event.preventDefault();pair($('token').value.trim());});
function pairingCode(url) {
  const qr=qrcodegen.QrCode.encodeText(url,qrcodegen.QrCode.Ecc.MEDIUM);
  const canvas=document.createElement('canvas'), border=4, scale=8;
  canvas.className='pairing-qr';
  canvas.width=canvas.height=(qr.size+border*2)*scale;
  canvas.setAttribute('role','img');
  canvas.setAttribute('aria-label','Scan to pair with this scanner host');
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle='#000';
  for(let y=0;y<qr.size;y++)for(let x=0;x<qr.size;x++)
    if(qr.getModule(x,y))ctx.fillRect((x+border)*scale,(y+border)*scale,scale,scale);
  return canvas;
}
$('share').addEventListener('click',async()=>{
  try {
    const access=await json('/api/access');
    const links=$('access-links');
    links.replaceChildren(...access.urls.map(url=>{
      const card=document.createElement('div'), label=document.createElement('label'), input=document.createElement('input');
      card.className='pairing-address';
      try {card.append(pairingCode(url));}
      catch(e) {const note=document.createElement('p');note.textContent='QR code unavailable. Use the pairing link below.';card.append(note);}
      label.textContent='Open on the same network';input.readOnly=true;input.value=url;
      input.addEventListener('click',()=>input.select());label.append(input);card.append(label);return card;
    }));
    if(!access.urls.length)links.textContent='No LAN address found. Check the host network connection and listen address.';
    $('access-code').textContent=access.token;
    $('access-dialog').showModal();
  }catch(e){$('command-error').textContent=e.message;$('command-error').hidden=false;}
});
$('close-access').onclick=()=>$('access-dialog').close();
$('access-dialog').addEventListener('close',()=>{$('access-links').replaceChildren();$('access-code').textContent='';});
function showPairing() { $('access-dialog').close(); $('pairing').hidden = false; $('workspace').hidden = true; $('share').hidden=true; $('connection').textContent = 'Pairing required'; }
function options(select, values, selected) {
  if (JSON.stringify(Array.from(select.options, x=>x.value)) !== JSON.stringify(values)) {
    select.replaceChildren(...values.map(value => {const o=document.createElement('option');o.value=value;o.textContent=value;return o;}));
  }
  if (document.activeElement !== select) select.value = selected;
}
function renderDistance(reading, connected) {
  const gauge=$('distance-gauge'), marker=$('distance-marker'), track=$('distance-track');
  const valid=connected && reading && Number.isFinite(reading.cm) && reading.cm>0;
  const mode=reading?`${reading.near_cm}:${reading.far_cm}:${reading.estimated}`:null;
  if(lastDistance && mode && lastDistance.mode!==mode)lastDistance=null;
  if(valid)lastDistance={reading:{...reading},mode,at:Date.now()};
  const last=lastDistance?.reading, displayed=valid?reading:last;
  gauge.dataset.zone=valid?['close','optimal','far'][reading.zone]:'unknown';
  marker.dataset.stale=valid?'false':'true';
  $('distance-value').textContent=valid?`~${reading.cm.toFixed(1)} cm${reading.estimated?' (estimate)':''}`
    :last?`Last ~${last.cm.toFixed(1)} cm`:'No depth';
  const age=lastDistance?Math.floor((Date.now()-lastDistance.at)/1000):null;
  $('distance-status').textContent=valid?'Live distance'
    :last?`${connected?'No current depth':'Connection unavailable'} · last reading ${age}s ago`
    :connected?'No reliable depth · aim at the object':'Waiting for scanner';
  marker.hidden=!displayed;
  const scale=reading || last;
  if(scale) {
    $('distance-min').textContent=`${scale.min_cm} cm`;
    $('distance-max').textContent=`${scale.max_cm} cm`;
    $('distance-target').textContent=`Optimal: ${scale.near_cm}–${scale.far_cm} cm`;
    const width=scale.max_cm-scale.min_cm;
    track.style.setProperty('--near',`${100*(scale.near_cm-scale.min_cm)/width}%`);
    track.style.setProperty('--far',`${100*(scale.far_cm-scale.min_cm)/width}%`);
    if(displayed)marker.style.left=`${Math.max(0,Math.min(100,100*(displayed.cm-scale.min_cm)/width))}%`;
  }
  track.setAttribute('aria-label',valid?reading.text:$('distance-status').textContent
    +(last?`; last distance ${last.cm.toFixed(1)} cm`:'; aim at the object for reliable depth'));
}

function render(next) {
  if(state && state.instance===next.instance && next.revision < state.revision)return;
  if(state && state.instance!==next.instance)lastDistance=null;
  const enteredPause=next.phase==='paused'&&(state?.phase!=='paused'||state?.instance!==next.instance);
  state=next; lastReceived=Date.now(); stale=false; paired=true;
  const inspect=state.phase==='paused';
  const mainPane=$(inspect?'coverage-pane':'depth-pane'), sidePane=$(inspect?'depth-pane':'coverage-pane');
  if(mainPane.parentElement!==$('main-view'))$('main-view').append(mainPane);
  if(sidePane.parentElement!==$('side-view'))$('side-view').append(sidePane);
  $('main-title').textContent=inspect?'Coverage · Paused':'Camera views';
  $('side-title').textContent=inspect?'Live depth':'Coverage';
  $('inspection-tools').hidden=!inspect;
  if(enteredPause)loadPausedInspection();
  else if(!inspect&&inspection)clearInspection();
  $('pairing').hidden=true; $('workspace').hidden=false; $('share').hidden=false;
  $('demo').hidden=!state.demo;
  $('connection').textContent=state.demo?'Demo preview':state.connected?'Connected to host':'Host connected · scanner offline';
  $('phase').textContent=titles[state.phase] || state.phase;
  $('notice').textContent=state.processing?'Processing saved scan on the host…':state.message || 'Ready. Aim at your object, then start a scan.';
  if(state.restarting)$('notice').textContent='Restarting scanner host… This page will reconnect automatically.';
  $('restart-hint').textContent=state.restart_supported===true
    ?'Available after scans, recordings and processing finish. Paired browsers reconnect automatically.'
    :'This older host needs one manual restart to install the in-app restart control.';
  $('subject-hint').textContent=state.settings.scene_mode==='free'
    ?'No base detection: the face or other broad surfaces will not be removed as a tabletop.'
    :'Detects a support surface. Use Head / free object for a person or an object held in the air.';
  $('distance').textContent=state.distance;
  renderDistance(state.distance_gauge,state.connected);
  const perf=state.performance;
  $('performance').title='Rolling average';
  $('performance').textContent=perf?`${perf.fps.toFixed(1)} fps · ${perf.ms.toFixed(1)} ms`:'—';
  $('scan-hint').textContent=[state.progress?.hint,state.progress?.unjoined].filter(Boolean).join(' ');
  $('scan-hint').hidden=!state.progress;
  if(state.progress?.recovered) $('scan-hint').textContent='Recovered';
  $('coverage').textContent=state.coverage_kind==='flat'?(state.coverage || '')
    :'Restart the scanner app on the host to load the flat coverage display. Finish and save your current scan first.';
  $('coverage').hidden=!$('coverage').textContent;
  $('reference-panel').hidden=!state.reference;
  for (const id of ['capture','tracking','geometry']) $(id).textContent=state.progress?.[id] || (state.phase==='paused'?'Paused':'—');
  for (const id of ['depth','colour','ir']) $(id+'-fps').textContent=state.streams[id]?`${state.streams[id].fps} fps`:'';
  $('messages').textContent=(state.messages || []).join('\n');
  options($('range'),state.ranges || [],state.range);
  const scans=state.scans || [];
  const chosen=$('saved').value;
  options($('saved'),scans.length?scans:[''],scans.includes(chosen)?chosen:scans.includes(state.selected)?state.selected:scans[0] || '');
  if(!scans.length) $('saved').options[0].textContent='No saved scans yet';
  $('auto-ir').checked=!!state.auto_ir;
  for(const el of document.querySelectorAll('[data-setting]')) {
    if(document.activeElement===el)continue;
    const value=state.settings[el.dataset.setting];
    if(value!==undefined) {if(el.type==='checkbox')el.checked=value;else el.value=value;}
  }
  const signature=JSON.stringify(state.controls.map(c=>[c.label,c.min,c.max,c.step]));
  if($('hardware-controls').dataset.signature!==signature) {
    $('hardware-controls').dataset.signature=signature;
    $('hardware-controls').replaceChildren(...state.controls.map(c=>{
      const label=document.createElement('label'), title=document.createElement('span'), output=document.createElement('span'), input=document.createElement('input');
      title.textContent=c.label;output.className='control-value';output.textContent=c.value;input.type='range';
      input.min=c.min;input.max=c.max;input.step=c.step;input.value=c.value;input.dataset.control=c.label;
      input.addEventListener('input',()=>{output.textContent=input.value;});
      input.addEventListener('change',()=>command('control',{label:c.label,value:Number(input.value)}));
      label.append(title,output,input);return label;
    }));
  }
  for(const c of state.controls) {
    const input=Array.from(document.querySelectorAll('[data-control]')).find(e=>e.dataset.control===c.label);
    if(input && document.activeElement!==input){input.value=c.value;input.previousElementSibling.textContent=c.value;}
  }
  $('imu').textContent=state.imu?`Pitch ${state.imu.pitch.toFixed(1)}° · roll ${state.imu.roll.toFixed(1)}° · ${state.imu.still?'Still':'Moving'}`:'IMU off or unavailable';
  $('scan-main').dataset.action=state.phase==='counting'?'cancel':state.phase==='idle'?'start':'stop';
  $('scan-main').textContent=state.phase==='counting'?'Cancel countdown':state.phase==='idle'?'Start scan':['scanning','pausing','paused','resuming'].includes(state.phase)?'Stop & save':titles[state.phase] || 'Start scan';
  updateDisabled();
}
async function poll() {
  try {render(await json('/api/state'));}
  catch(e) {
    stale=true;updateDisabled();renderDistance(state?.distance_gauge,false);
    if(paired){$('connection').textContent='Connection lost · retrying…';$('notice').textContent='The browser lost contact with the host. Any scan continues there. Reconnecting…';}
  }
}
async function command(action, data={}) {
  if(!state||pending||stale)return false;
  pending=true;updateDisabled();$('command-error').hidden=true;
  try {render(await json('/api/command',{action,revision:state.revision,instance:state.instance,...data}));return true;}
  catch(e){$('command-error').textContent=e.message;$('command-error').hidden=false;await poll();return false;}
  finally{pending=false;updateDisabled();}
}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',async()=>{
  const action=button.dataset.action;
  if(action==='cancel'&&state.phase!=='counting'){$('cancel-dialog').showModal();return;}
  const succeeded=await command(action,action==='open'?{name:$('saved').value}:{});
  if(action==='open'&&succeeded)location.assign('/model/');
}));
$('keep-scanning').onclick=()=>$('cancel-dialog').close();
$('confirm-cancel').onclick=()=>{$('cancel-dialog').close();command('cancel');};
document.querySelectorAll('[data-setting]').forEach(el=>el.addEventListener('change',()=>command('settings',{settings:{[el.dataset.setting]:el.type==='checkbox'?el.checked:el.value}})));
$('range').addEventListener('change',()=>command('range',{value:$('range').value}));
$('auto-ir').addEventListener('change',()=>command('auto-ir',{value:$('auto-ir').checked}));
function imageCoordinates(canvas,clientX,clientY) {
  const bounds=canvas.getBoundingClientRect();
  const scale=Math.min(bounds.width/canvas.width,bounds.height/canvas.height);
  if(!Number.isFinite(scale)||scale<=0)return null;
  const width=canvas.width*scale,height=canvas.height*scale;
  const x=(clientX-bounds.left-(bounds.width-width)/2)/width;
  const y=(clientY-bounds.top-(bounds.height-height)/2)/height;
  return x>=0&&x<=1&&y>=0&&y<=1?{x,y}:null;
}

function clearInspection() {
  inspectionEpoch++;
  inspection?.controller.abort();inspection=null;
  $('coverage-view').dataset.inspecting='false';
}
async function loadPausedInspection() {
  clearInspection();
  inspection={epoch:inspectionEpoch,instance:state.instance,controller:new AbortController(),
    ready:false,loading:false,dirty:false,yaw:0,pitch:0,zoom:1,pointers:new Map()};
  $('inspection-status').textContent='Loading paused coverage…';
  $('inspection-retry').hidden=true;
  for(const id of ['inspection-in','inspection-out','inspection-reset'])$(id).disabled=true;
  if(!state.coverage_rotation){
    $('inspection-status').textContent='Restart the scanner host to enable moving paused coverage.';
    return;
  }
  await drawPausedInspection();
}
async function drawPausedInspection() {
  const item=inspection;
  if(!item||state?.phase!=='paused')return;
  item.dirty=true;
  if(item.loading)return;
  item.loading=true;
  const current=()=>inspection===item&&state?.phase==='paused'&&state.instance===item.instance;
  try {
    while(current()&&item.dirty) {
      item.dirty=false;
      const query=new URLSearchParams({yaw:String(item.yaw),pitch:String(item.pitch),zoom:String(item.zoom)});
      const timer=setTimeout(()=>item.controller.abort(),20000);
      let bitmap;
      try {
        const response=await fetch('/model/coverage.png?'+query,{signal:item.controller.signal,cache:'no-store'});
        if(!response.ok)throw Error('Could not render paused coverage. Try loading it again.');
        bitmap=await createImageBitmap(await response.blob());
      } finally {if(timer!==undefined)clearTimeout(timer);}
      if(!current()){bitmap.close();return;}
      const canvas=$('coverage-view');
      canvas.width=bitmap.width;canvas.height=bitmap.height;
      canvas.getContext('2d').drawImage(bitmap,0,0);bitmap.close();
      item.ready=true;
      canvas.dataset.inspecting='true';
      $('inspection-status').textContent='Paused coverage · Drag to turn the scan · Scroll or pinch to zoom';
      for(const id of ['inspection-in','inspection-out','inspection-reset'])$(id).disabled=false;
    }
  } catch(error) {
    if(current()){
      item.dirty=false;
      $('inspection-status').textContent=item.controller.signal.aborted?'Loading timed out. Try again.':error.message;
      $('inspection-retry').hidden=false;
    }
  } finally {item.loading=false;}
}
function zoomInspection(factor) {
  if(!inspection?.ready||state?.phase!=='paused')return;
  inspection.zoom=Math.max(.4,Math.min(6,inspection.zoom*factor));drawPausedInspection();
}
$('inspection-in').onclick=()=>zoomInspection(1.2);
$('inspection-out').onclick=()=>zoomInspection(1/1.2);
$('inspection-reset').onclick=()=>{
  if(!inspection?.ready)return;
  Object.assign(inspection,{yaw:0,pitch:0,zoom:1});drawPausedInspection();
};
$('inspection-retry').onclick=()=>{if(state?.phase==='paused')loadPausedInspection();};
$('coverage-view').addEventListener('pointerdown',event=>{
  if(state?.phase!=='paused'||!inspection?.ready)return;
  inspection.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  event.currentTarget.setPointerCapture(event.pointerId);
});
$('coverage-view').addEventListener('pointermove',event=>{
  if(!inspection?.ready||!inspection.pointers.has(event.pointerId))return;
  const old=inspection.pointers.get(event.pointerId),others=[...inspection.pointers.entries()].filter(([id])=>id!==event.pointerId);
  inspection.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  if(others.length){
    const other=others[0][1],before=Math.hypot(old.x-other.x,old.y-other.y),after=Math.hypot(event.clientX-other.x,event.clientY-other.y);
    if(before>2)zoomInspection(after/before);
  } else {
    const yaw=inspection.yaw+(event.clientX-old.x)*.012;
    inspection.yaw=Math.atan2(Math.sin(yaw),Math.cos(yaw));
    inspection.pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,inspection.pitch+(event.clientY-old.y)*.012));
    drawPausedInspection();
  }
});
for(const name of ['pointerup','pointercancel','lostpointercapture'])$('coverage-view').addEventListener(name,event=>inspection?.pointers.delete(event.pointerId));
$('coverage-view').addEventListener('wheel',event=>{
  if(state?.phase==='paused'&&inspection?.ready){event.preventDefault();zoomInspection(event.deltaY<0?1.12:1/1.12);}
},{passive:false});
for(const kind of ['depth','colour']) $(kind).addEventListener('click',event=>{
  const point=imageCoordinates(event.currentTarget,event.clientX,event.clientY);
  if(point)command('pick',{kind,...point});
});

(async()=>{
  const token=new URLSearchParams(location.hash.slice(1)).get('token');
  if(token)await pair(token);else await poll();
  heartbeat();previews();
})();

function updateDisabled() {
  if(!state)return;
  const phase=state.phase, active=['scanning','pausing','paused','resuming'].includes(phase);
  const busy=pending||stale||state.processing||state.meshing_recording||state.restarting;
  const connected=state.connected&&!state.demo;
  for(const el of document.querySelectorAll('[data-action]')) {
    const action=el.dataset.action;
    let enabled=false;
    if(action==='live')enabled=!state.processing;
    else if(action==='restart')enabled=state.restart_supported===true&&['idle','disconnected'].includes(phase)&&!state.recording;
    else if(action==='open')enabled=!!state.scans.length&&['idle','disconnected'].includes(phase)&&!state.recording;
    else if(action==='retry')enabled=!state.demo;
    else if(action==='stop')enabled=active;
    else if(action==='cancel')enabled=active||phase==='counting';
    else if(action==='start'||action==='continue')enabled=connected&&phase==='idle'&&!state.recording;
    else if(action==='pause')enabled=connected&&phase==='scanning';
    else if(action==='resume')enabled=connected&&phase==='paused';
    else if(action==='separate')enabled=connected&&state.can_separate;
    else if(action==='save-frame'||action==='clear-aim')enabled=connected;
    else if(action==='record'||action==='stop-recording')enabled=connected&&phase==='idle';
    else if(action==='mesh-recording')enabled=connected&&phase==='idle'&&!state.recording;
    else if(action==='reset-controls')enabled=connected&&phase==='idle'&&!state.recording;
    el.disabled=busy||!enabled;
  }
  $('pause').dataset.action=phase==='paused'?'resume':'pause';
  $('pause').textContent=phase==='paused'?'Resume scan':phase==='pausing'?'Finishing frames…':'Pause & inspect';
  $('pause').disabled=busy||!connected||!['scanning','paused'].includes(phase);
  $('record').dataset.action=state.recording?'stop-recording':'record';
  $('record').textContent=state.recording?'Stop recording':'Record raw frames';
  for(const el of document.querySelectorAll('[data-setting]')) {
    const capture=['scene_mode','preset','keep_raw','remove_base','mesh_after','countdown','record_rate'].includes(el.dataset.setting);
    el.disabled=busy||!connected||(capture&&(phase!=='idle'||state.recording))
      ||(el.dataset.setting==='remove_base'&&state.settings.scene_mode==='free');
  }
  for(const el of [$('range'),$('auto-ir'),...document.querySelectorAll('[data-control]')])
    el.disabled=busy||!connected||phase!=='idle'||state.recording;
}
async function heartbeat() {
  if(!pending)await poll();
  if(state)updateDisabled();
  setTimeout(heartbeat,750);
}
function clearCanvas(kind) {
  const canvas=$(kind), context=canvas.getContext('2d');
  context.fillStyle='#070e17';context.fillRect(0,0,canvas.width,canvas.height);
  context.fillStyle='#a4b6ca';context.font='14px system-ui';context.textAlign='center';
  const message=kind==='coverage-view'&&state?.connected
    ?(state.coverage_preview!==true?'Scanner host restart needed'
      :['scanning','pausing','resuming'].includes(state.phase)?'Waiting for coverage':'Coverage is shown during a scan')
    :'Waiting for scanner';
  context.fillText(message,canvas.width/2,canvas.height/2);
}
async function previews() {
  await Promise.all(['depth','colour','ir','coverage-view','reference'].map(async kind=>{
    if(kind==='coverage-view'&&state?.phase==='paused'&&inspection?.ready)return;
    if(!paired||document.hidden||!state?.connected||Date.now()-lastReceived>5000) {
      clearCanvas(kind);return;
    }
    if(kind==='reference'&&!state.reference)return;
    if(kind==='coverage-view'&&state.coverage_preview!==true){clearCanvas(kind);return;}
    try {
      const response=await fetch(`/preview/${kind}.png`,{signal:AbortSignal.timeout(5000)});
      if(!response.ok||response.status===204){clearCanvas(kind);return;}
      const blob=await response.blob();
      const bitmap=await createImageBitmap(blob);
      if(kind==='coverage-view'&&state?.phase==='paused'&&inspection?.ready){bitmap.close();return;}
      const canvas=$(kind);
      if(canvas.width!==bitmap.width||canvas.height!==bitmap.height){canvas.width=bitmap.width;canvas.height=bitmap.height;}
      canvas.getContext('2d').drawImage(bitmap,0,0);bitmap.close();
    }catch(e){clearCanvas(kind);}
  }));
  setTimeout(previews,150);
}

document.addEventListener('keydown',event=>{
  if(event.ctrlKey||event.altKey||event.metaKey||['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName)
     || document.querySelector('dialog[open]') || !paired)return;
  const shortcuts={c:$('scan-main'),p:$('pause'),r:$('record')};
  const key=event.key.toLowerCase();
  if(shortcuts[key]&&!shortcuts[key].disabled){event.preventDefault();shortcuts[key].click();}
  else if(key==='escape')command(state?.phase==='counting'?'cancel':'clear-aim');
  else if(key==='s')command('save-frame');
  else if(key==='m')command('mesh-recording');
});
