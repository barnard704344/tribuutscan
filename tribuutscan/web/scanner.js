'use strict';
const $ = id => document.getElementById(id);
let state = null, pending = false, paired = false, stale = true;
let lastReceived = 0, lastDistance = null;
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
$('share').addEventListener('click',async()=>{
  try {
    const access=await json('/api/access');
    const links=$('access-links');
    links.replaceChildren(...access.urls.map(url=>{
      const label=document.createElement('label'), input=document.createElement('input');
      label.textContent='Open on the same network';input.readOnly=true;input.value=url;
      input.addEventListener('click',()=>input.select());label.append(input);return label;
    }));
    $('access-code').textContent=access.token;
    $('access-dialog').showModal();
  }catch(e){$('command-error').textContent=e.message;$('command-error').hidden=false;}
});
$('close-access').onclick=()=>$('access-dialog').close();
function showPairing() { $('pairing').hidden = false; $('workspace').hidden = true; $('share').hidden=true; $('connection').textContent = 'Pairing required'; }
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
  state=next; lastReceived=Date.now(); stale=false; paired=true;
  $('pairing').hidden=true; $('workspace').hidden=false; $('share').hidden=false;
  const modelKey=state.selected || 'live';
  if($('model').dataset.source!==modelKey) {
    $('model').dataset.source=modelKey;
    $('model').src=state.selected?'/model/?embed=1':'/model/?embed=1&live=1';
    $('model-link').href=state.selected?'/model/':'/model/?live=1';
  }
  $('demo').hidden=!state.demo;
  $('connection').textContent=state.demo?'Demo preview':state.connected?'Connected to host':'Host connected · scanner offline';
  $('phase').textContent=titles[state.phase] || state.phase;
  $('notice').textContent=state.processing?'Processing saved scan on the host…':state.message || 'Ready. Aim at your object, then start a scan.';
  $('subject-hint').textContent=state.settings.scene_mode==='free'
    ?'No base detection: the face or other broad surfaces will not be removed as a tabletop.'
    :'Detects a support surface. Use Head / free object for a person or an object held in the air.';
  $('distance').textContent=state.distance;
  renderDistance(state.distance_gauge,state.connected);
  $('scan-hint').textContent=[state.progress?.hint,state.progress?.unjoined].filter(Boolean).join(' ');
  $('scan-hint').hidden=!state.progress;
  $('coverage').textContent=state.coverage || ''; $('coverage').hidden=!state.coverage;
  $('reference-panel').hidden=!state.reference;
  for (const id of ['capture','tracking','geometry']) $(id).textContent=state.progress?.[id] || (state.phase==='paused'?'Paused':'—');
  for (const id of ['depth','colour','ir']) $(id+'-fps').textContent=state.streams[id]?`${state.streams[id].fps} fps`:'';
  $('messages').textContent=(state.messages || []).join('\n');
  options($('range'),state.ranges || [],state.range);
  const scans=state.scans || [];
  options($('saved'),scans.length?scans:[''],state.selected || $('saved').value || scans[0]);
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
  if(!state||pending||stale)return;
  pending=true;updateDisabled();$('command-error').hidden=true;
  try {render(await json('/api/command',{action,revision:state.revision,instance:state.instance,...data}));}
  catch(e){$('command-error').textContent=e.message;$('command-error').hidden=false;await poll();}
  finally{pending=false;updateDisabled();}
}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>{
  const action=button.dataset.action;
  if(action==='cancel'&&state.phase!=='counting'){$('cancel-dialog').showModal();return;}
  command(action,action==='open'?{name:$('saved').value}:{});
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
  const busy=pending||stale||state.processing||state.meshing_recording;
  const connected=state.connected&&!state.demo;
  for(const el of document.querySelectorAll('[data-action]')) {
    const action=el.dataset.action;
    let enabled=false;
    if(action==='live')enabled=!state.processing;
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
  context.fillText('Waiting for scanner',canvas.width/2,canvas.height/2);
}
async function previews() {
  await Promise.all(['depth','colour','ir','reference'].map(async kind=>{
    if(!paired||document.hidden||!state?.connected||Date.now()-lastReceived>5000) {
      clearCanvas(kind);return;
    }
    if(kind==='reference'&&!state.reference)return;
    try {
      const response=await fetch(`/preview/${kind}.png`,{signal:AbortSignal.timeout(5000)});
      if(!response.ok||response.status===204){clearCanvas(kind);return;}
      const blob=await response.blob();
      const bitmap=await createImageBitmap(blob);
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
