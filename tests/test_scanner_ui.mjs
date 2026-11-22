// Lightweight DOM fixtures exercise UI state policy without scanner hardware or a browser runtime.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root = new URL('../tribuutscan/web/', import.meta.url);
const html=fs.readFileSync(new URL('scanner.html',root),'utf8');
const elements=[];
class Element {
  constructor(tag='div',attrs='') {
    this.tagName=tag.toUpperCase();this.style={setProperty(name,value){this[name]=value;}};this.dataset={};this.children=[];this.listeners={};this.disabled=false;
    this.hidden=/\bhidden\b/.test(attrs);this.type=(attrs.match(/type="([^"]*)"/)||[])[1];
    this.id=(attrs.match(/id="([^"]*)"/)||[])[1];this._value='';
    for(const [,name,value] of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g))this.dataset[name]=value;
    elements.push(this);
  }
  setAttribute(name,value){this[name]=value;}
  get options(){return this.children;}
  get value(){return this._value;}
  set value(value){this._value=value;}
  replaceChildren(...children){this.children=children;}
  addEventListener(name,callback){this.listeners[name]=callback;}
  append(...children){this.children.push(...children);}
  getContext(){return {fillRect(){},fillText(){},drawImage(){}};}
}
for(const [,tag,attrs] of html.matchAll(/<(\w+)([^>]*)>/g))new Element(tag,attrs);
const document={
  activeElement:null,hidden:false,addEventListener(){},querySelector(){return null;},
  getElementById(id){return elements.find(e=>e.id===id);},
  createElement(tag){return new Element(tag);},
  querySelectorAll(selector){
    const name=selector.match(/\[data-([^\]]+)\]/)?.[1];
    return elements.filter(e=>name && Object.hasOwn(e.dataset,name));
  }
};
const context=vm.createContext({document,window:{addEventListener(){}},location:{origin:'http://host',pathname:'/'},
  console,Date,JSON,URLSearchParams,Array,Number,setTimeout(){},fetch(){throw Error('Unexpected network request');}});
let source=fs.readFileSync(new URL('scanner.js',root),'utf8');
source=source.replace(/\(async\(\)=>\{[\s\S]*?\}\)\(\);/,'');
new vm.Script(source).runInContext(context);
function render(phase,extra={}) {
  const state={instance:'test-host',revision:1,phase,connected:true,demo:false,settings:{},controls:[],scans:[],streams:{},messages:[],...extra};
  context.fixture=state;vm.runInContext('render(fixture); pending=false; updateDisabled();',context);
}
const get=id=>document.getElementById(id);
render('idle');assert.equal(get('scan-main').disabled,false);assert.equal(get('scan-main').dataset.action,'start');
render('scanning');assert.equal(get('scan-main').dataset.action,'stop');assert.equal(get('pause').disabled,false);assert.equal(get('cancel').disabled,false);
render('paused');assert.equal(get('pause').dataset.action,'resume');assert.equal(get('scan-main').disabled,false);
render('saving');assert.equal(get('scan-main').disabled,true);assert.equal(get('pause').disabled,true);assert.equal(get('cancel').disabled,true);
render('counting');assert.equal(get('scan-main').dataset.action,'cancel');assert.equal(get('scan-main').disabled,false);
render('disconnected',{connected:false});assert.equal(get('scan-main').disabled,true);
render('idle',{demo:true,connected:false});assert.equal(get('scan-main').disabled,true);
render('idle',{processing:true});assert.equal(get('scan-main').disabled,true);
render('idle',{selected:'one-scan',scans:['one-scan']});assert.equal(get('model').src,'/model/?embed=1');
render('idle');assert.equal(get('model').src,'/model/?embed=1&live=1');
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
