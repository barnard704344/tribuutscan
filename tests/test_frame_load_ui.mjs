import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const html=fs.readFileSync(new URL('../tribuutscan/web/index.html',import.meta.url),'utf8');
const block=html.slice(html.indexOf("$('frame-editor').addEventListener('load'"),html.indexOf('function selectTool(name,'));
function check({healthy=false,hidden=false,path='/frames'}={}){
 let listener;
 const frame={hidden,contentWindow:{location:{pathname:path}},contentDocument:{getElementById:()=>healthy?{}:null},addEventListener:(type,fn)=>listener=fn};
 vm.runInNewContext(block,{$:()=>frame});listener();return frame.srcdoc;
}
assert.match(check(),/Frame editor needs a host restart/);
assert.match(check(),/Reload saved workspace/);
assert.equal(check({healthy:true}),undefined);
assert.equal(check({hidden:true}),undefined);
assert.equal(check({path:'blank'}),undefined);
const frameHtml=fs.readFileSync(new URL('../tribuutscan/web/frame-editor.html',import.meta.url),'utf8');
assert.ok(frameHtml.indexOf('id="error"')<frameHtml.indexOf('id="timeline"'),'load errors must be visible above the timeline');
console.log('Missing frame backend shows restart guidance; healthy/hidden frames remain untouched; load errors stay visible');
