// Lightweight DOM fixtures exercise UI state policy without scanner hardware or a browser runtime.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root = new URL('../tribuutscan/web/', import.meta.url);
const html=fs.readFileSync(new URL('scanner.html',root),'utf8');
const ids=Array.from(html.matchAll(/\bid="([^"]+)"/g),m=>m[1]);
assert.equal(new Set(ids).size,ids.length,'Workspace element IDs must be unique');
assert.equal((html.match(/Messages from the host/g)||[]).length,1);
const elements=[];
class Element {
  constructor(tag='div',attrs='') {
    this.tagName=tag.toUpperCase();this.style={setProperty(name,value){this[name]=value;}};this.dataset={};this.children=[];this.listeners={};this.disabled=false;
    this.hidden=/\bhidden\b/.test(attrs);this.type=(attrs.match(/type="([^"]*)"/)||[])[1];
    this.id=(attrs.match(/id="([^"]*)"/)||[])[1];this._value='';
    for(const [,name,value] of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g))this.dataset[name.replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;
    elements.push(this);
  }
  setAttribute(name,value){this[name]=value;}
  focus(){document.activeElement=this;}
  showModal(){this.open=true;}
  close(){this.open=false;this.listeners.close?.();}
  get options(){return this.children;}
  get value(){return this._value;}
  set value(value){this._value=value;}
  replaceChildren(...children){this.children=children;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  append(...children){for(const child of children){
    if(child.parentElement)child.parentElement.children=child.parentElement.children.filter(e=>e!==child);
    child.parentElement=this;this.children.push(child);
  }}
  getContext(){return this.context??={rects:[],fillRect(...rect){this.rects.push([this.fillStyle,...rect]);},fillText(){},drawImage(){}};}
}
const stack=[];
for(const [token,tag,attrs] of html.matchAll(/<\/?(\w+)([^>]*)>/g)) {
  if(token.startsWith('</')){stack.pop();continue;}
  const element=new Element(tag,attrs);
  if(stack.length)stack.at(-1).append(element);
  if(!['meta','link','input','br','hr','img'].includes(tag))stack.push(element);
}
const document={
  activeElement:null,hidden:false,addEventListener(){},querySelector(){return null;},
  getElementById(id){return elements.find(e=>e.id===id);},
  createElement(tag){return new Element(tag);},
  querySelectorAll(selector){
    const name=selector.match(/\[data-([^\]]+)\]/)?.[1]?.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
    return elements.filter(e=>name && Object.hasOwn(e.dataset,name));
  }
};
const context=vm.createContext({document,window:{addEventListener(){}},location:{origin:'http://host',pathname:'/'},
  console,Date,JSON,URLSearchParams,Array,Number,AbortController,AbortSignal,setTimeout(){},fetch(){throw Error('Unexpected network request');}});
let source=fs.readFileSync(new URL('scanner.js',root),'utf8');
new vm.Script(fs.readFileSync(new URL('qrcodegen.js',root),'utf8')).runInContext(context);
source=source.replace(/\(async\(\)=>\{[\s\S]*?\}\)\(\);/,'');
new vm.Script(source).runInContext(context);
function render(phase,extra={}) {
  const state={instance:'test-host',revision:1,phase,connected:true,demo:false,coverage_preview:true,coverage_rotation:true,coverage_kind:'flat',restart_supported:true,settings:{},controls:[],scans:[],streams:{},messages:[],...extra};
  context.fixture=state;vm.runInContext('render(fixture); pending=false; updateDisabled();',context);
}
const get=id=>document.getElementById(id);
assert.equal(get('tools-scan').hidden,false);
for(const name of ['camera','files','host'])assert.equal(get('tools-'+name).hidden,true);
get('tab-host').listeners.click();
assert.equal(get('tools-host').hidden,false);
assert.equal(get('tools-scan').hidden,true);
assert.equal(get('tab-host')['aria-selected'],'true');
assert.equal(get('tab-host').tabIndex,0);
get('tab-host').listeners.keydown({key:'ArrowRight',preventDefault(){}});
assert.equal(get('tools-scan').hidden,false);
assert.equal(document.activeElement,get('tab-scan'));
get('tab-scan').listeners.keydown({key:'End',preventDefault(){}});
assert.equal(get('tools-host').hidden,false);
get('tab-host').listeners.keydown({key:'Home',preventDefault(){}});
assert.equal(get('tools-scan').hidden,false);
// Changing a tool tab must not hide the persistent scan actions or move the cameras.
assert.equal(get('scan-main').parentElement,get('pause').parentElement.parentElement);
assert.notEqual(get('scan-main').parentElement,get('tools-scan'));
assert.equal(get('messages').parentElement.parentElement,get('tools-host'));
render('scanning');
const coveragePane=get('coverage-pane'),depthPane=get('depth-pane'),coverageCanvas=get('coverage-view');
assert.equal(coveragePane.parentElement,get('side-view'));
assert.equal(depthPane.parentElement,get('main-view'));
render('paused');
assert.equal(coveragePane.parentElement,get('main-view'));
assert.equal(depthPane.parentElement,get('side-view'));
assert.equal(get('coverage-view'),coverageCanvas);
assert.equal(get('main-title').textContent,'Coverage · Paused');
render('paused');assert.equal(coveragePane.parentElement,get('main-view'));
render('resuming');
assert.equal(coveragePane.parentElement,get('side-view'));
assert.equal(depthPane.parentElement,get('main-view'));
assert.equal(get('main-title').textContent,'Camera views');
render('idle');assert.equal(coveragePane.parentElement,get('side-view'));
assert.equal(get('coverage-piece'),undefined);
assert.equal(get('coverage-in'),undefined);
render('idle');assert.equal(get('scan-main').disabled,false);assert.equal(get('scan-main').dataset.action,'start');
render('scanning');assert.equal(get('scan-main').dataset.action,'stop');assert.equal(get('pause').disabled,false);assert.equal(get('cancel').disabled,false);
render('paused');assert.equal(get('pause').dataset.action,'resume');assert.equal(get('scan-main').disabled,false);
render('saving');assert.equal(get('scan-main').disabled,true);assert.equal(get('pause').disabled,true);assert.equal(get('cancel').disabled,true);
render('counting');assert.equal(get('scan-main').dataset.action,'cancel');assert.equal(get('scan-main').disabled,false);
render('disconnected',{connected:false});assert.equal(get('scan-main').disabled,true);
render('idle',{demo:true,connected:false});assert.equal(get('scan-main').disabled,true);
render('idle',{processing:true});assert.equal(get('scan-main').disabled,true);
render('idle',{selected:'one-scan',scans:['one-scan']});
assert.equal(get('model'),undefined);
assert.equal(get('coverage-view').tagName,'CANVAS');
assert.ok(html.indexOf('id="coverage-view"')<html.indexOf('id="colour"'));
assert.doesNotMatch(html,/<iframe|data-setting="preview_mode"/);
render('scanning',{coverage_preview:undefined,coverage_kind:undefined});
assert.match(get('coverage').textContent,/Restart the scanner app/);
assert.equal(get('coverage').hidden,false);
render('scanning',{coverage:'Visible object coverage'});
assert.equal(get('coverage').textContent,'Visible object coverage');
render('idle');
assert.equal(get('restart-host').disabled,false);
for(const phase of ['scanning','paused','saving','optimising','meshing']) {
  render(phase);assert.equal(get('restart-host').disabled,true);
}
render('idle',{recording:true});assert.equal(get('restart-host').disabled,true);
render('idle',{processing:true});assert.equal(get('restart-host').disabled,true);
render('idle',{restart_supported:undefined});assert.equal(get('restart-host').disabled,true);
render('idle',{restarting:true});assert.equal(get('scan-main').disabled,true);
render('idle');
vm.runInContext('stale=true;updateDisabled()',context);assert.equal(get('scan-main').disabled,true);
assert.equal(typeof get('pair-form').listeners.submit,'function');
const reading={cm:24,zone:1,min_cm:12,max_cm:36,near_cm:18,far_cm:30,estimated:false,text:'Centre: ~24.0 cm · Hold this distance'};
render('scanning',{distance_gauge:reading});
assert.equal(get('distance-marker').hidden,false);
assert.equal(get('distance-marker').style.left,'50%');
assert.equal(get('distance-gauge').dataset.zone,'optimal');
assert.equal(get('distance-target').textContent,'Optimal: 18–30 cm');
render('scanning',{distance_gauge:{...reading,cm:null,zone:null}});
assert.equal(get('distance-marker').hidden,false);
assert.equal(get('distance-marker').dataset.stale,'true');
assert.equal(get('distance-gauge').dataset.zone,'unknown');
assert.equal(get('distance-value').textContent,'Last ~24.0 cm');
render('disconnected',{connected:false,distance_gauge:reading});
assert.equal(get('distance-value').textContent,'Last ~24.0 cm');
assert.match(get('distance-status').textContent,/Connection unavailable/);
render('idle',{distance_gauge:{...reading,cm:null,near_cm:30,far_cm:50,min_cm:20,max_cm:60}});
assert.equal(get('distance-value').textContent,'No depth');
assert.equal(get('distance-marker').hidden,true);
render('scanning',{instance:'restarted-host',revision:0,distance_gauge:reading});
assert.equal(get('distance-value').textContent,'~24.0 cm');
render('idle',{settings:{scene_mode:'free'}});
const base=elements.find(e=>e.dataset.setting==='remove_base');
assert.equal(base.disabled,true);
render('idle',{settings:{scene_mode:'still'}});
assert.equal(base.disabled,false);
context.cameraFixture={width:320,height:200,getBoundingClientRect(){return {left:20,top:10,width:320,height:180};}};
let point=vm.runInContext('imageCoordinates(cameraFixture,180,100)',context);
assert.equal(point.x,.5);assert.equal(point.y,.5);
assert.equal(vm.runInContext('imageCoordinates(cameraFixture,25,100)',context),null);
assert.equal(vm.runInContext('imageCoordinates(cameraFixture,335,100)',context),null);
point=vm.runInContext('imageCoordinates(cameraFixture,36,10)',context);
assert.equal(point.x,0);assert.equal(point.y,0);
console.log('Scanner UI, distance, subject-mode and preview-coordinate checks passed');

render('paused');
const requested=[];
context.fetch=url=>{requested.push(url);throw Error('fixture offline');};
context.inspectionFixture={controller:new AbortController(),ready:true,loading:false,dirty:false,
  instance:'test-host',yaw:0,pitch:0,zoom:1,pointers:new Map()};
vm.runInContext('inspection=inspectionFixture',context);
const coverage=get('coverage-view');
coverage.listeners.pointerdown({pointerId:1,clientX:10,clientY:10,currentTarget:{setPointerCapture(){}}});
coverage.listeners.pointermove({pointerId:1,clientX:40,clientY:20});
assert.ok(vm.runInContext('inspection.yaw>0 && inspection.pitch>0',context));
assert.ok(requested.at(-1).startsWith('/model/coverage.png?'));
get('inspection-in').onclick();assert.ok(vm.runInContext('inspection.zoom>1',context));
get('inspection-reset').onclick();assert.equal(vm.runInContext('inspection.yaw',context),0);
coverage.listeners.pointerup({pointerId:1});
coverage.listeners.pointerdown({pointerId:1,clientX:0,clientY:0,currentTarget:{setPointerCapture(){}}});
coverage.listeners.pointerdown({pointerId:2,clientX:10,clientY:0,currentTarget:{setPointerCapture(){}}});
coverage.listeners.pointermove({pointerId:2,clientX:20,clientY:0});
assert.equal(vm.runInContext('inspection.zoom',context),2);
requested.length=0;
await vm.runInContext('previews()',context);
assert.ok(!requested.includes('/preview/coverage-view.png'));
render('resuming');assert.equal(vm.runInContext('inspection',context),null);
assert.equal(get('inspection-tools').hidden,true);
assert.equal(coveragePane.parentElement,get('side-view'));
assert.doesNotMatch(source,/inspectionSample|projectInspection|cloud\.bin|new DataView/);

let releaseImage,imageRequested;
const arrived=new Promise(resolve=>{imageRequested=resolve;});
const image=new Promise(resolve=>{releaseImage=resolve;});
const rotationRequests=[];
context.fetch=async url=>{
  rotationRequests.push(url);
  return {ok:true,blob:()=>{if(rotationRequests.length===1){imageRequested();return image;}return Promise.resolve({});}};
};
let painted=0;
context.createImageBitmap=async()=>({width:552,height:345,close(){painted++;}});
const loading=vm.runInContext('state.phase="paused";loadPausedInspection()',context);
await arrived;
vm.runInContext('inspection.yaw=.2;drawPausedInspection();inspection.yaw=.6;drawPausedInspection()',context);
releaseImage({});await loading;
assert.equal(rotationRequests.length,2);
assert.ok(rotationRequests[1].includes('yaw=0.6'));
assert.equal(get('coverage-view').dataset.inspecting,'true');
assert.equal(get('inspection-in').disabled,false);
render('resuming');

let releaseLate,lateRequested;
const lateArrived=new Promise(resolve=>{lateRequested=resolve;});
const lateImage=new Promise(resolve=>{releaseLate=resolve;});
context.fetch=async()=>({ok:true,blob:()=>{lateRequested();return lateImage;}});
const lateLoading=vm.runInContext('state.phase="paused";loadPausedInspection()',context);
await lateArrived;
render('resuming');releaseLate({});await lateLoading;
assert.equal(vm.runInContext('inspection',context),null);
assert.equal(get('coverage-view').dataset.inspecting,'false');
console.log('Coverage camera rotation, touch zoom, coalesced requests and resume checks passed');

// Pairing QR codes encode the exact host-provided link, including its token fragment.
const urls=['http://192.168.1.20:8765/#token=example-private-code',
  'http://10.0.0.20:8765/#token=example-private-code'];
let accessRequests=[];
context.fetch=async url=>{accessRequests.push(url);return {ok:true,json:async()=>({urls,token:'example-private-code'})};};
await get('share').listeners.click();
assert.deepEqual(accessRequests,['/api/access']);
assert.equal(get('access-dialog').open,true);
assert.equal(get('access-code').textContent,'example-private-code');
assert.equal(get('access-links').children.length,2);
for(const [i,card] of get('access-links').children.entries()) {
  const [canvas,label]=card.children;
  assert.equal(canvas.tagName,'CANVAS');
  assert.equal(label.children[0].value,urls[i]);
  context.qrUrl=urls[i];
  const qr=vm.runInContext('qrcodegen.QrCode.encodeText(qrUrl,qrcodegen.QrCode.Ecc.MEDIUM)',context);
  assert.equal(canvas.width,(qr.size+8)*8);
  assert.equal(canvas.height,canvas.width);
  assert.deepEqual(canvas.context.rects[0],['#fff',0,0,canvas.width,canvas.height]);
  const expected=[];
  for(let y=0;y<qr.size;y++)for(let x=0;x<qr.size;x++)
    if(qr.getModule(x,y))expected.push(['#000',(x+4)*8,(y+4)*8,8,8]);
  assert.deepEqual(canvas.context.rects.slice(1),expected);
}
get('close-access').onclick();
assert.equal(get('access-links').children.length,0);
assert.equal(get('access-code').textContent,'');
context.fetch=async()=>({ok:true,json:async()=>({urls:[],token:'example-private-code'})});
await get('share').listeners.click();
assert.match(get('access-links').textContent,/No LAN address found/);
get('access-dialog').close();
console.log('Local pairing QR codes, multiple addresses, empty network and credential cleanup checks passed');

// A browser refresh must not replace the user's next scan choice with the host's old selection.
render('idle',{scans:['a-scan','b-scan'],selected:'a-scan',revision:10});
get('saved').value='b-scan';document.activeElement=null;
render('idle',{scans:['a-scan','b-scan'],selected:'a-scan',revision:11});
assert.equal(get('saved').value,'b-scan');
const navigations=[],openRequests=[];
context.location.assign=url=>navigations.push(url);
context.fetch=async(url,options)=>{
  openRequests.push(JSON.parse(options.body));
  return {ok:true,status:200,json:async()=>({...context.fixture,selected:'b-scan',revision:12})};
};
await get('open-scan').listeners.click();
assert.equal(openRequests[0].action,'open');
assert.equal(openRequests[0].name,'b-scan');
assert.deepEqual(navigations,['/model/']);
// A rejected open stays on the workspace and shows its error; it never opens the wrong model.
context.fetch=async(url,options)=> options?.body
  ?{ok:false,status:409,json:async()=>({error:'Finish scanning before opening a saved scan'})}
  :{ok:true,status:200,json:async()=>({...context.fixture,revision:12})};
await get('open-scan').listeners.click();
assert.equal(navigations.length,1);
assert.match(get('command-error').textContent,/Finish scanning/);
console.log('Saved-scan selection survives polling; successful open navigates and failed open stays put');
render('idle',{scans:['remaining-scan'],selected:'deleted-scan',revision:13});
assert.equal(get('saved').value,'remaining-scan');
