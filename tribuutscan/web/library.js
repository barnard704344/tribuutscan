const $=id=>document.getElementById(id), params=new URLSearchParams(location.search);
let items=[],state=null,pending=false,trash=params.get('trash')==='1',dialogItem=null,epoch=0,lastSignature='',focused=false;
const kindNames={capture:'Original capture',edited:'Editing copy',aligned:'Aligned scan'};
const sizes=value=>{if(!Number.isFinite(value)||value<0)return '—';const units=['B','KB','MB','GB','TB'];let index=0;while(value>=1024&&index<4){value/=1024;index++;}return `${value.toFixed(index?1:0)} ${units[index]}`;};
const date=value=>new Date(value*1000).toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
function el(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function error(message=''){$('error').textContent=message;$('error').hidden=!message;}
function blocked(){return !state||state.processing||state.recording||state.meshing_recording||state.restarting||!['idle','disconnected'].includes(state.phase);}
async function api(path,data){const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-Tribuutscan':'1'},body:data===undefined?undefined:JSON.stringify(data),signal:AbortSignal.timeout(25000)});if(response.status===401)throw Error('Pair this browser in Capture, then return to the Scan Library.');const result=await response.json();if(!response.ok)throw Error(result.error||'Host request failed');return result;}
async function command(action,extra){const current=await api('/api/state');return api('/api/command',{action,...extra,instance:current.instance,revision:current.revision});}
function button(text,action,item,handler){const node=el('button',text);node.type='button';node.dataset.action=action;node.disabled=pending||blocked()||(action!=='open'&&state?.demo);node.onclick=handler;return node;}
async function open(item){if(pending)return;pending=true;error();render();try{await command('open',{name:item.id});location.assign('/model/');}catch(e){error(e.message);}finally{pending=false;render();}}
function showDialog(name,item){dialogItem={...item};for(const id of ['rename-error','trash-error'])$(id).hidden=true;if(name==='rename-dialog')$('display-name').value=item.display_name;if(name==='trash-dialog')$('trash-name').textContent=item.display_name;$(name).showModal();if(name==='rename-dialog')$('display-name').select();}
async function mutate(operation,item,title){
 if(pending)return;pending=true;render();for(const node of document.querySelectorAll('dialog button'))node.disabled=true;
 try{await command('library-'+operation,{name:item.id,entry_revision:item.revision,...(title===undefined?{}:{display_name:title})});
  $('rename-dialog').close();$('trash-dialog').close();$('notice').textContent=operation==='trash'?'Moved to Trash. Files and related recording links are retained.':operation==='restore'?'Scan restored to the library.':'Display name saved.';error();await load(true);
 }catch(e){const id=operation==='rename'?'rename-error':operation==='trash'?'trash-error':null;if(id){$(id).hidden=false;$(id).textContent=e.message;}else error(e.message);}
 finally{pending=false;for(const node of document.querySelectorAll('dialog button'))node.disabled=false;render();}
}
function details(item){
 $('details-title').textContent=item.display_name;$('details-id').textContent='Folder: '+item.id;
 const values=[['Type',kindNames[item.kind]],['Created',date(item.created)],['Saved points',Number.isFinite(item.points)?item.points.toLocaleString():'—'],['Geometry & session',sizes(item.storage.geometry||0)],['Meshes',sizes(item.storage.meshes||0)],['Undo/history',sizes(item.storage.history||0)],['Recordings in this folder',sizes(item.storage.recordings||0)],['Total in this folder',sizes(item.bytes)]];
 $('details').replaceChildren(...values.flatMap(([key,value])=>[el('dt',key),el('dd',value)]));
 const links=[];for(const [relation,ids] of [['Derived from',item.parents],['Used by',item.dependents||[]]])for(const id of ids){
  const other=items.find(candidate=>candidate.id===id);const node=el('button',other?.display_name||id);node.append(el('small',relation+(other?.trashed_at?' · in Trash':other?'':' · not found')));
  node.disabled=!other;node.onclick=()=>{trash=!!other.trashed_at;$('search').value=other.id;$('kind').value=$('output').value='all';$('details-dialog').close();render();};links.push(node);
 }
 $('relations').replaceChildren(...(links.length?links:[el('p','No related versions recorded.')]));
 $('recordings').replaceChildren(...(item.recordings.length?item.recordings.map(rec=>el('li',`${rec.name} · ${rec.available?'available':'missing'}${rec.external?' · outside captures':''}`)):[el('li','No recording references saved.')]));
 if(!$('details-dialog').open)$('details-dialog').showModal();
}
function card(item){
 const node=el('article',undefined,'card');node.dataset.id=item.id;if(params.get('focus')===item.id)node.dataset.focused='true';
 const preview=el('div',undefined,'preview'),placeholder=el('span','Preview unavailable','placeholder');preview.append(placeholder);
 if(item.thumbnail_version!=='pending'){
  const image=el('img');image.alt='Preview of '+item.display_name;image.loading='lazy';image.decoding='async';image.width=400;image.height=240;image.src='/api/library/thumbnail?scan='+encodeURIComponent(item.id)+'&v='+encodeURIComponent(item.thumbnail_version);image.onload=()=>placeholder.hidden=true;image.onerror=()=>image.hidden=true;preview.append(image);
 }
 const content=el('div',undefined,'card-content');content.append(el('h3',item.display_name),el('p',date(item.created)));
 const badges=el('div',undefined,'badges');badges.append(el('span',kindNames[item.kind],'badge'));
 if(item.exports.mesh)badges.append(el('span','Surface mesh','badge'));if(item.exports.stl)badges.append(el('span',item.closure==='closed'?'Closed STL':'STL · check closure','badge'+(item.closure==='closed'?' closed':'')));if(!item.exports.mesh)badges.append(el('span','Point cloud','badge'));
 if(item.recordings.some(rec=>!rec.available))badges.append(el('span','Recording link missing','badge missing'));
 content.append(badges,el('p',`${Number.isFinite(item.points)?item.points.toLocaleString()+' points':'Points unavailable'} · ${sizes(item.bytes)} on host`));
 if(item.parents.length)content.append(el('p','From '+item.parents.map(id=>items.find(other=>other.id===id)?.display_name||id).join(' + ')));
 if(item.warning)content.append(el('p',item.warning));
 const actions=el('div',undefined,'card-actions');
 const primary=button(trash?'Restore':'Open scan',trash?'restore':'open',item,()=>trash?mutate('restore',item):open(item));primary.classList.add('primary');actions.append(primary);
 const info=el('button','Details');info.dataset.action='details';info.onclick=()=>details(item);actions.append(info);
 const secondary=el('div',undefined,'card-secondary');secondary.append(button('Rename','rename',item,()=>showDialog('rename-dialog',item)));
 if(!trash)secondary.append(button('Move to Trash','trash',item,()=>showDialog('trash-dialog',item)));
 content.append(actions,secondary);node.append(preview,content);return node;
}
function render(){
 const previous=document.activeElement,focusId=previous?.closest?.('.card')?.dataset.id,focusAction=previous?.dataset.action;
 const active=items.filter(item=>!item.trashed_at),removed=items.filter(item=>item.trashed_at);
 $('active-count').textContent=active.length;$('trash-count').textContent=removed.length;
 $('active-tab').setAttribute('aria-pressed',String(!trash));$('trash-tab').setAttribute('aria-pressed',String(trash));$('trash-note').hidden=!trash;
 const query=$('search').value.trim().toLocaleLowerCase(),kind=$('kind').value,output=$('output').value;
 const visible=(trash?removed:active).filter(item=>{
  const related=items.filter(other=>other.family===item.family).map(other=>other.display_name).join(' ');
  return (!query||[item.display_name,item.id,date(item.created),related].join(' ').toLocaleLowerCase().includes(query))&&(kind==='all'||item.kind===kind)&&(output==='all'||(output==='mesh'?item.exports.mesh:!item.exports.mesh));
 });
 const sort=$('sort').value;visible.sort((a,b)=>sort==='name'?a.display_name.localeCompare(b.display_name):sort==='size'?b.bytes-a.bytes:sort==='oldest'?a.created-b.created:b.created-a.created);
 const groups=new Map();for(const item of visible){if(!groups.has(item.family))groups.set(item.family,[]);groups.get(item.family).push(item);}
 const sections=[];for(const [family,members] of groups){
  const all=items.filter(item=>item.family===family),root=items.find(item=>item.id===family)||members[0],section=el('section',undefined,'family'),heading=el('div',undefined,'family-heading');
  section.dataset.single=String(all.length===1);section.setAttribute('aria-label',root.display_name);heading.hidden=all.length===1;
  heading.append(el('h2',root.display_name),el('span',`${all.length} related version${all.length===1?'':'s'}${all.some(item=>item.trashed_at)?' · includes Trash':''}`));
  const cards=el('div',undefined,'cards');cards.append(...members.map(card));section.append(heading,cards);sections.push(section);
 }
 $('families').replaceChildren(...sections);$('families').setAttribute('aria-busy',String(pending));$('empty').hidden=!!visible.length;
 $('empty-title').textContent=query||kind!=='all'||output!=='all'?'No matching scans':trash?'Trash is empty':'No saved scans yet';
 $('empty-message').textContent=query||kind!=='all'||output!=='all'?'Try another search or clear the filters.':trash?'Scans moved here can be restored.':'Capture and save a scan to begin your library.';
 $('summary').textContent=`${visible.length} shown · ${sizes((trash?removed:active).reduce((sum,item)=>sum+item.bytes,0))} in ${trash?'trashed':'active'} scan folders`;
 $('host-status').textContent=blocked()?'Capture or processing is active · browsing available':state?.demo?'Demo host · organisation disabled':'Host ready';
 const saved=new URLSearchParams();if(trash)saved.set('trash','1');if($('search').value)saved.set('q',$('search').value);if(kind!=='all')saved.set('kind',kind);if(output!=='all')saved.set('output',output);if(sort!=='newest')saved.set('sort',sort);if(params.has('focus'))saved.set('focus',params.get('focus'));history.replaceState(null,'','/library'+(saved.size?'?'+saved:''));
 if(focusId&&focusAction){const found=[...$('families').querySelectorAll('.card')].find(node=>node.dataset.id===focusId);found?.querySelector(`[data-action="${focusAction}"]`)?.focus();}
 if(!focused&&params.has('focus')){const found=[...$('families').querySelectorAll('.card')].find(node=>node.dataset.id===params.get('focus'));if(found){found.scrollIntoView({block:'center'});focused=true;}}
}
async function load(force=false){
 const id=++epoch;try{const [catalogue,host]=await Promise.all([api('/api/library'),api('/api/state')]);if(id!==epoch)return;
  const signature=JSON.stringify([catalogue.items,host.phase,host.processing,host.recording,host.meshing_recording,host.demo,host.restarting]);state=host;items=catalogue.items;
  if(force||signature!==lastSignature){lastSignature=signature;render();}error();
 }catch(e){error(e.message);$('families').setAttribute('aria-busy','false');}
}
$('search').value=params.get('q')||'';for(const id of ['kind','output','sort']){if(params.has(id))$(id).value=params.get(id);if(!$(id).value)$(id).selectedIndex=0;}
for(const id of ['search','kind','output','sort'])$(id).addEventListener(id==='search'?'input':'change',render);
$('active-tab').onclick=()=>{trash=false;render();};$('trash-tab').onclick=()=>{trash=true;render();};$('refresh').onclick=()=>load(true);
$('rename-form').onsubmit=event=>{event.preventDefault();mutate('rename',dialogItem,$('display-name').value);};$('trash-form').onsubmit=event=>{event.preventDefault();mutate('trash',dialogItem);};
for(const node of document.querySelectorAll('[data-close]'))node.onclick=()=>$(node.dataset.close).close();
for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
await load(true);setInterval(()=>{if(!pending)load();},5000);
