'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
const code=source.slice(source.indexOf('function renderList(){'),source.indexOf('async function loadList('));
const {ComposerDrafts}=require('../web/features/chat/composer-drafts.js');
function fixture(storage=new Map()){
 const nodes=new Map(),timers=new Map(),requests=[],dialogs=[],copies=[],opened=[],notices=[];let timer=0,sequence=0;
 const node=(tag='',cls='',textContent='')=>{const classes=new Set();return {tag,cls,textContent,value:'',selectionStart:0,selectionEnd:0,dataset:{},classList:{add(...values){values.forEach(value=>classes.add(value));},remove(...values){values.forEach(value=>classes.delete(value));},contains:value=>classes.has(value),toggle(value,on){if(on===false)classes.delete(value);else classes.add(value);}},children:[],events:{},scrollTop:0,isConnected:true,append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},addEventListener(name,run){this.events[name]=run;},setAttribute(){},setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.events.close?.();},remove(){this.isConnected=false;}};};
 const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};$('list-mode').value='recent';
 const memory={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)};
 const drafts=new ComposerDrafts(memory,{});drafts.coordinate=async update=>update(true);
 const scope=(id,host='local')=>({gateway:'gateway',provider:'codex',account:'account',host,session:id});
 const text='读取会话[示例](codex://threads/source)，继续完成';
 const context=vm.createContext({CliConnectionsPanel:class{},$,el:node,window:{BridgeSessionLinks:{text:identity=>'codex://threads/'+identity.id},BridgePresentation:{resizeMessage(){},showError(){}}},document:{hidden:false,body:{append:box=>dialogs.push(box)}},SessionMenu:class{constructor(){}bind(button,context){button.menuContext=context;}},
   BridgeClipboard:{copyWithFeedback:async(get,item)=>{if(item.disabled)return;copies.push(get());}},BridgeI18n:{locale:()=> 'en-US'},t:s=>s,localStorage:memory,
   workbench:{cancelLocate(){},rememberScroll(){},stopThumbnails(){}},browserNavigation:{sync:()=>true},listIdentity:'gateway',selectedCodexEntry:null,codexActiveAccountId:'account',chatOpenGeneration:0,csrf:'token',clientNavigation:{provider:'codex',clients:[]},state:null,listRows:[],currentId:null,currentHost:'local',listHasMore:false,listRenderStamp:'',listComplete:true,listReadFinished:true,availableSources:[],
   renderActivity(){},sessionAccess:v=>({source:'desktop',canRecover:true,canConfigure:true,canEditMessages:true,...v}),sourceLabel:()=> 'APP',dateText:()=>'',toast:text=>notices.push(text),pinRow:row=>row,sessionPins:{value:(_identity,pinned)=>!!pinned,set(identity,pinned){requests.push({url:`/${identity.host}/${identity.id}/pin`,body:{pinned}});}},desktopSessions:{refresh:async()=>{},onListEnter(){}},openDraftAccount:'account',openChat:async(id,host)=>{opened.push({id,host});context.currentId=id;context.currentHost=host;context.chatOpenGeneration++;context.state={id,host,connected:true,access:{canSend:true}};context.codexDraftScope=null;context.codexDraftTouched=false;$('message').value='';$('app').classList.add('chat-open');context.bindCodexDraft({draftAccountIdentity:context.openDraftAccount});},
   sessionStorage:memory,
   setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),uuid:()=>++sequence===1?'request-id':'request-'+sequence,
   draftGateway:'gateway',composerDrafts:drafts,codexDraftScope:null,codexDraftTouched:false,codexProvisionalDrafts:new Map(),restoringComposer:false,
   codexQuotes:null,selectedSkills:new Set(),invalidSkills:new Set(),sending:false,chatKey:(id=context.currentId,host=context.currentHost)=>host+'|'+id,
   attachments:{rows:[],ready:()=>true,ids:()=>[],references:()=>[],setLocked(){},clear(){}},workModes:{selection:'default',value:()=>context.workModes.selection||'default',render(){},submitted(){}},
   currentAccess:()=>({canSend:true}),bumpRecency(){},renderQueue(){},renderSkillPills(){},messageManagement:null,
   api:async(url,body)=>{requests.push({url,body});return {status:'confirmed'};},sessionUrl:(id,action,host)=>`/${host}/${id}/${action}`,loadList:async()=>{}});
 context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;context.URLSearchParams=URLSearchParams;
 vm.runInContext(code,context);
 vm.runInContext(source.slice(source.indexOf('function saveDraft(){'),source.indexOf('let catalogStarted=')),context);
 vm.runInContext(source.slice(source.indexOf('function visibleChat(){'),source.indexOf('\n',source.indexOf('function visibleChat(){'))),context);
 vm.runInContext(source.slice(source.indexOf("$('back').onclick="),source.indexOf('function applyEntryCapabilities(')),context);
 vm.runInContext(source.slice(source.indexOf("$('composer').onsubmit=async event=>{"),source.indexOf("$('message').onkeydown=")),context);
 $('send-mode').value='send';
 const row=(id,recency,pinned=false)=>({id,host:'local',title:id,projectKey:'p',projectName:'Project',hostLabel:'Local',recency,bridgePinned:pinned,access:{}});
 return {context,$,node,row,requests,dialogs,copies,opened,notices,storage,drafts,scope,text,fireTimers(){const work=[...timers.values()];timers.clear();work.forEach(fn=>fn());}};
}
test('recovery creates from the selected source without reading it on menu opening, then sends through the new chat',async()=>{
 const ui=fixture(),chat={...ui.row('source',1),host:'remote',access:{source:'cli',canRecover:true}};
 ui.context.currentId='another-chat';
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'remote',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'accepted'};
 };
 const menu=ui.context.sessionMenuContext(chat);
 assert.equal(typeof menu.recover,'function');
 assert.equal(ui.requests.length,0,'Opening the menu must not request or connect the source');
 const result=await menu.recover();
 assert.equal(result.status,'created');
 assert.deepEqual(ui.requests.map(row=>row.url),['/remote/source/recover','/remote/created/send']);
 assert.deepEqual(Object.keys(ui.requests[0].body),['id']);
 assert.equal(ui.requests[1].body.text,ui.text);
 assert.deepEqual(ui.opened,[{id:'created',host:'remote'}]);
 assert.equal(ui.drafts.load(ui.scope('created','remote')),null,'The normal send clears its confirmed draft');
});
test('reloading while opening the known destination keeps its draft and does not start its first message again',async()=>{
 const first=fixture(),chat=first.row('source',1);let opened,finish;
 first.context.api=async(url,body)=>{first.requests.push({url,body});return {operationId:body.id,status:'created',id:'created',host:'local',text:first.text,ready:true,draftAccountIdentity:'account'};};
 first.context.openChat=async()=>{opened=true;await new Promise(resolve=>{finish=resolve;});};
 const pending=first.context.sessionMenuContext(chat).recover();
 while(!opened)await new Promise(resolve=>setImmediate(resolve));
 const reloaded=fixture(first.storage);
 reloaded.context.api=async(url,body)=>{reloaded.requests.push({url,body});return {operationId:'request-id',status:'created',id:'created',host:'local',text:first.text,ready:true,draftAccountIdentity:'account'};};
 await reloaded.context.sessionMenuContext(chat).recover();
 assert.deepEqual(reloaded.requests.map(row=>row.body),[undefined],'A retained creation is checked once; first sending stays with the original attempt');
 assert.deepEqual(reloaded.opened,[{id:'created',host:'local'}]);
 assert.equal(reloaded.$('message').value,first.text);
 first.context.csrf='expired';finish();await pending;
});
test('a late created response saves only the target draft and preserves text already written there',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let finish;
 ui.context.currentId='source';ui.context.codexDraftScope=ui.scope('source');ui.$('message').value='source draft';
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return new Promise(resolve=>{finish=resolve;});};
 const pending=ui.context.sessionMenuContext(chat).recover();
 while(!finish)await new Promise(resolve=>setImmediate(resolve));
 ui.drafts.save(ui.scope('created'),{text:'already typing in the created chat'});
 ui.context.currentId='user-selected';ui.context.chatOpenGeneration++;ui.$('message').value='newer current draft';
 finish({operationId:'request-id',status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'});
 await pending;
 assert.equal(ui.drafts.load(ui.scope('created')).text,'already typing in the created chat');
 assert.equal(ui.$('message').value,'newer current draft');
 assert.deepEqual(ui.opened,[]);assert.equal(ui.requests.length,1);
});
test('a completed creation still opens with a private draft when storage becomes unavailable',async()=>{
 const ui=fixture();
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});ui.context.sessionStorage.setItem=()=>{throw Error('quota exhausted');};
  return {operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:false,message:'连接暂未完成',draftAccountIdentity:'account'};
 };
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);
 assert.equal(ui.context.codexProvisionalDrafts.get('local|created').text,ui.text);
 assert.equal(ui.$('message').value,ui.text);
 assert.equal(ui.$('send-error').textContent,'连接暂未完成');
});
test('an unready creation leaves the ordinary draft in the new chat for a manual first send',async()=>{
 const ui=fixture();
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:false,message:'模型设置未完成',draftAccountIdentity:'account'}:{status:'accepted'};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);assert.equal(ui.requests.length,1);
 assert.equal(ui.$('message').value,ui.text);assert.equal(ui.$('send-error').textContent,'模型设置未完成');
 await ui.$('composer').onsubmit({preventDefault(){}});
 assert.equal(ui.requests[1].url,'/local/created/send');assert.equal(ui.drafts.load(ui.scope('created')),null);
});
test('a rejected first send keeps the new chat and permits the normal send button to retry',async()=>{
 const ui=fixture();let sends=0;
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(url.endsWith('/recover'))return {operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'};
  if(++sends===1)throw Object.assign(Error('新会话尚未连接'),{status:400,code:'send_not_submitted'});
  return {status:'accepted'};
 };
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.equal(ui.$('message').value,ui.text);assert.equal(ui.drafts.pendingSend(ui.scope('created')),null);
 assert.equal(ui.$('send-error').textContent,'新会话尚未连接');
 await ui.$('composer').onsubmit({preventDefault(){}});
 assert.deepEqual(ui.requests.map(row=>row.url),['/local/source/recover','/local/created/send','/local/created/send']);
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);
 assert.equal(ui.drafts.load(ui.scope('created')),null);
});
test('an unknown first send retains the ordinary pending submission and its target draft',async()=>{
 const ui=fixture();
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'unknown'};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 const pending=ui.drafts.pendingSend(ui.scope('created'));
 assert.equal(pending.id,ui.requests[0].body.id);assert.equal(pending.body.text,ui.text);
 assert.equal(ui.drafts.load(ui.scope('created')).text,ui.text);assert.match(ui.$('send-error').textContent,/可能已送达/);
 assert.equal(ui.requests.filter(row=>row.url.endsWith('/recover')).length,1);
});
test('a lost creation response is checked once with the same ID, then the known chat receives its first message',async()=>{
 const ui=fixture();
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(url.endsWith('/recover'))throw Error('network interrupted');
  return url.includes('/recover?id=')?{operationId:'request-id',status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'accepted'};
 };
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.deepEqual(ui.requests.map(row=>row.url),['/local/source/recover','/local/source/recover?id='+ui.requests[0].body.id,'/local/created/send']);
 assert.equal(ui.requests.filter(row=>row.url==='/local/source/recover').length,1);
});
test('an unknown creation is only checked on the next click and is never automatically recreated',async()=>{
 const ui=fixture();
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});if(body)throw Error('network interrupted');return {operationId:'request-id',status:'unknown',id:null,host:'local',message:'创建结果尚未确认'};};
 const chat=ui.row('source',1);
 await assert.rejects(()=>ui.context.sessionMenuContext(chat).recover(),/尚未确认/);
 await assert.rejects(()=>ui.context.sessionMenuContext(chat).recover(),/尚未确认/);
 assert.deepEqual(ui.requests.map(row=>!!row.body),[true,false,false]);
 assert.ok(ui.requests.slice(1).every(row=>row.url.endsWith('id='+ui.requests[0].body.id)));
 assert.deepEqual(ui.opened,[]);
});
test('a missing receipt after a lost POST retains its ID and only another explicit click retries that same creation',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let writes=0;
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(!body)return {status:'idle',id:null,host:'local'};
  if(++writes===1)throw Error('lost response from a queued request');
  return {operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:false,draftAccountIdentity:'account'};
 };
 await assert.rejects(()=>ui.context.sessionMenuContext(chat).recover(),/尚未确认/);
 const id=ui.requests[0].body.id;assert.equal(writes,1,'A lost response never triggers another automatic POST');
 await ui.context.sessionMenuContext(chat).recover();
 assert.deepEqual(ui.requests.map(row=>!!row.body),[true,false,false,true]);
 assert.equal(ui.requests[3].body.id,id,'A read that found nothing cannot replace the queued request ID');
});
test('unavailable session storage keeps creation attempts in memory and still opens a later known target',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let known=false;
 ui.context.sessionStorage.getItem=()=>{throw Error('storage unavailable');};
 ui.context.sessionStorage.setItem=()=>{throw Error('storage unavailable');};
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});if(body)throw Error('lost response');
  return known?{operationId:new URL(url,'http://fixture').searchParams.get('id'),status:'created',id:'created',host:'local',text:ui.text,ready:false,draftAccountIdentity:'account'}:{status:'unknown',id:null,message:'创建结果尚未确认'};
 };
 await assert.rejects(()=>ui.context.sessionMenuContext(chat).recover(),/创建结果尚未确认/);
 assert.equal(ui.requests.length,2,'Storage failure must not prevent the first explicit creation');
 known=true;await ui.context.sessionMenuContext(chat).recover();
 assert.equal(ui.requests[2].body,undefined);assert.ok(ui.requests[2].url.endsWith('id='+ui.requests[0].body.id));
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);
});
test('an unknown draft account displays the private first message without guessing an account and later preserves edits',async()=>{
 const ui=fixture();ui.context.openDraftAccount='';
 ui.context.codexQuotes={quotes:[],restore(values){this.quotes=values;},reset(){this.quotes=[];},serialize:text=>text};
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return {operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:false,draftAccountIdentity:''};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.equal(ui.$('message').value,ui.text,'The real account binder must show the private text even before account evidence arrives');
 assert.equal(ui.context.codexDraftScope,null);assert.equal(ui.drafts.load(ui.scope('created')),null);
 ui.$('message').value='my new draft';ui.context.codexQuotes.quotes=[{text:'quoted edit'}];ui.$('message').oninput();
 ui.context.bindCodexDraft({draftAccountIdentity:''});
 assert.equal(ui.$('message').value,'my new draft');
 ui.context.bindCodexDraft({draftAccountIdentity:'account'});
 assert.equal(ui.drafts.load(ui.scope('created')).text,'my new draft');assert.equal(ui.drafts.load(ui.scope('created')).quotes[0].text,'quoted edit');
});
test('a target whose first view lacks account data still shows the creation text saved under its verified receipt account',async()=>{
 const ui=fixture();ui.context.openDraftAccount='';
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return {operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:false,draftAccountIdentity:'account'};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.equal(ui.$('message').value,ui.text);assert.equal(ui.context.codexDraftScope,null);
 ui.context.bindCodexDraft({draftAccountIdentity:'account'});assert.equal(ui.$('message').value,ui.text);
});
test('a fresh private first message can use ordinary sending when both browser storages are unavailable',async()=>{
 const ui=fixture();
 ui.context.sessionStorage.getItem=()=>{throw Error('storage unavailable');};ui.context.sessionStorage.setItem=()=>{throw Error('storage unavailable');};
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'accepted'};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.deepEqual(ui.requests.map(row=>row.url),['/local/source/recover','/local/created/send']);
 assert.equal(ui.requests[1].body.text,ui.text);assert.equal(ui.$('message').value,'');
});
for(const fields of [{text:'fixed'},{text:'',sendMode:'queue'},{text:'',workMode:'plan'},{text:'',skills:['chosen']}])test('recovery never overwrites or automatically sends an existing target draft '+JSON.stringify(fields),async()=>{
 const ui=fixture(),original={...fields,text:fields.text==='fixed'?ui.text:fields.text};
 ui.drafts.save(ui.scope('created'),original);
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'accepted'};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.equal(ui.requests.filter(row=>row.url.endsWith('/send')).length,0);
 assert.equal(ui.drafts.load(ui.scope('created')).text,original.text);
 if(original.sendMode)assert.equal(ui.drafts.load(ui.scope('created')).sendMode,original.sendMode);
 if(original.workMode)assert.equal(ui.drafts.load(ui.scope('created')).workMode,original.workMode);
 if(original.skills)assert.deepEqual(ui.drafts.load(ui.scope('created')).skills,original.skills);
});
test('a quote-only edit while the target opens is not included in automatic first sending or cleared',async()=>{
 const ui=fixture(),open=ui.context.openChat;
 ui.context.codexQuotes={quotes:[],restore(values){this.quotes=values;},reset(){this.quotes=[];},serialize(text){return this.quotes.map(quote=>'> '+quote.text).join('\n')+'\n'+text;}};
 ui.context.openChat=async(id,host)=>{await open(id,host);ui.context.codexQuotes.quotes=[{text:'user quote'}];ui.context.codexDraftTouched=true;ui.context.saveDraft();};
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'accepted'};};
 await ui.context.sessionMenuContext(ui.row('source',1)).recover();
 assert.equal(ui.requests.filter(row=>row.url.endsWith('/send')).length,0);
 assert.equal(ui.$('message').value,ui.text);assert.equal(ui.context.codexQuotes.quotes[0].text,'user quote');
});
test('returning to the list while target history loads prevents automatic sending to a hidden chat',async()=>{
 const ui=fixture(),open=ui.context.openChat;let finish;
 ui.context.openChat=async(id,host)=>{await open(id,host);await new Promise(resolve=>{finish=resolve;});};
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created',host:'local',text:ui.text,ready:true,draftAccountIdentity:'account'}:{status:'accepted'};};
 const pending=ui.context.sessionMenuContext(ui.row('source',1)).recover();while(!finish)await new Promise(resolve=>setImmediate(resolve));
 const generation=ui.context.chatOpenGeneration;ui.$('back').onclick();assert.equal(ui.context.currentId,'created');assert.equal(ui.context.chatOpenGeneration,generation);
 finish();await pending;
 assert.equal(ui.requests.filter(row=>row.url.endsWith('/send')).length,0);assert.equal(ui.drafts.load(ui.scope('created')).text,ui.text);
});
test('each settled source shortcut is a new creation attempt and default model warnings remain visible',async()=>{
 const ui=fixture();let created=0;
 ui.context.api=async(url,body)=>{ui.requests.push({url,body});return url.endsWith('/recover')?{operationId:body.id,status:'created',id:'created-'+(++created),host:'local',text:ui.text,ready:true,draftAccountIdentity:'account',warning:'源模型不可用，使用默认模型'}:{status:'accepted'};};
 const chat=ui.row('source',1);
 await ui.context.sessionMenuContext(chat).recover();await ui.context.sessionMenuContext(chat).recover();
 const creations=ui.requests.filter(row=>row.url.endsWith('/recover'));
 assert.equal(creations.length,2);assert.notEqual(creations[0].body.id,creations[1].body.id);
 assert.deepEqual(ui.opened,[{id:'created-1',host:'local'},{id:'created-2',host:'local'}]);
 assert.equal(ui.notices.at(-1),'源模型不可用，使用默认模型');
});
test('recovery visibility follows the reported capability for IDE, WSL and other clients',()=>{
 const ui=fixture();
 for(const access of [{source:'ide',canRecover:true},{source:'cli',runtime:'wsl',canRecover:true},{source:'desktop',canRecover:true}]){
  assert.equal(typeof ui.context.sessionMenuContext({...ui.row('source',1),access}).recover,'function');
 }
 assert.equal(ui.context.sessionMenuContext({...ui.row('source',1),access:{source:'cli',canRecover:false}}).recover,undefined);
 assert.equal(ui.context.sessionMenuContext({...ui.row('source',1),access:{source:'desktop',canRecover:true}},'claude').recover,undefined);
 assert.equal(ui.requests.length,0);
});
test('native organization preserves host and expected assignment and rejects stale authentication',async()=>{
 const ui=fixture(),chat={...ui.row('native',1),host:'remote'};
 const context=ui.context.sessionMenuContext(chat);
 await context.organization({kind:'project',targetId:'project-b',expectedId:'project-a'});
 assert.equal(ui.requests.at(-1).url,'/remote/native/organization');
 assert.equal(ui.requests.at(-1).body.expectedId,'project-a');
 ui.context.csrf='replacement';
 await assert.rejects(()=>context.organization(),/连接或账号/);
 assert.equal(ui.requests.filter(row=>row.body).length,1);
});
test('recent sections sort pinned and unpinned independently; project grouping ignores pins',()=>{
 const ui=fixture();ui.context.listRows=[ui.row('recent',30),ui.row('old-pin',1,true),ui.row('new-pin',20,true),ui.row('older',2)];
 ui.context.renderList();const rows=ui.$('sessions').children;
 assert.deepEqual(rows.map(n=>n.dataset.id||n.textContent),['置顶聊天','new-pin','old-pin','最近聊天','recent','older']);
 ui.$('list-mode').value='project';ui.context.renderList();
 assert.deepEqual(ui.$('sessions').children[0].children.slice(1).map(n=>n.dataset.id),['recent','new-pin','older','old-pin']);
});
test('shared context preserves original host for pin, rename and archive',async()=>{
 const ui=fixture(),chat={...ui.row('a',1),host:'remote'};
 const menu=ui.context.sessionMenuContext(chat);
 await menu.pin(true);await menu.rename('New');await menu.archive();
 const writes=ui.requests.filter(row=>row.body);
 assert.equal(writes[0].url,'/remote/a/pin');assert.equal(writes[0].body.pinned,true);
 assert.equal(writes[1].url,'/remote/a/rename');assert.equal(writes[1].body.title,'New');
 assert.equal(writes[2].url,'/remote/a/archive');assert.equal(writes[2].body.disconnect,true);
 assert.equal(menu.nativeOrganization,true);
});
test('project groups put unassigned hosts last and preserve activity order within each group',()=>{
 const ui=fixture();ui.$('list-mode').value='project';
 ui.context.listRows=[
  {...ui.row('unassigned-new',100),projectKey:'local|unassigned',projectName:'未归类'},
  {...ui.row('assigned-old',5),projectKey:'local|project-a'},
  {...ui.row('remote-unassigned',90),host:'remote',projectKey:'remote|unassigned',projectName:'未归类'},
  {...ui.row('assigned-new',20),projectKey:'local|project-a'},
  {...ui.row('named-unassigned',30),projectKey:'local|named-project',projectName:'未归类'},
  {...ui.row('unassigned-old',1),projectKey:'local|unassigned',projectName:'未归类'}];
 ui.context.renderList();
 assert.deepEqual(ui.$('sessions').children.map(group=>group.children.slice(1).map(row=>row.dataset.id)),
  [['named-unassigned'],['assigned-new','assigned-old'],['unassigned-new','unassigned-old'],['remote-unassigned']]);
});
test('recent chat order continues to use activity when a chat is unassigned',()=>{
 const ui=fixture();ui.context.listRows=[ui.row('assigned',10),{...ui.row('unassigned',20),projectKey:'local|unassigned',projectName:'未归类'}];
 ui.context.renderList();
 assert.deepEqual(ui.$('sessions').children.map(row=>row.dataset.id),['unassigned','assigned']);
});
test('directory mode groups by server identity and shows paths without replacing native projects',()=>{
 const ui=fixture();ui.$('list-mode').value='directory';
 const grouped=(id,recency,key,path,missing=false)=>({...ui.row(id,recency),directoryGroup:{key,name:missing?null:'shop',path,missing}});
 ui.context.listRows=[grouped('old',1,'local-shop','/work/shop'),grouped('other',20,'backup-shop','/backup/shop'),grouped('new',30,'local-shop','/work/shop'),grouped('missing',100,'no-cwd',null,true)];
 const before=JSON.stringify(ui.context.listRows);ui.context.renderList();
 const groups=ui.$('sessions').children;
 assert.deepEqual(groups.map(group=>group.children.slice(1).map(row=>row.dataset.id)),[['new','old'],['other'],['missing']]);
 assert.equal(groups[0].children[0].textContent,'shop · 此电脑');
 assert.equal(groups[0].children[0].children[0].textContent,'/work/shop');
 assert.equal(groups[1].children[0].children[0].textContent,'/backup/shop');
 assert.equal(groups[2].children[0].textContent,'无工作目录 · 此电脑');
 assert.equal(groups[0].children[1].children[2].children[0].textContent,'shop · 此电脑');
 assert.equal(JSON.stringify(ui.context.listRows),before);
 assert.equal(ui.context.sessionMenuContext(ui.context.listRows[0]).projectName,'Project');
});
test('directory metadata loading and unsupported rows are distinct from missing working directories',()=>{
 const ui=fixture();ui.$('list-mode').value='directory';
 ui.context.listRows=[{...ui.row('old-cache',2),cwd:'/recorded/path'}, {...ui.row('no-cwd',1),directoryGroup:{key:'missing',name:null,path:null,missing:true}}];
 ui.context.listReadFinished=false;ui.context.renderList();
 assert.equal(ui.$('sessions').children[0].textContent,'正在读取工作目录…');
 assert.equal(ui.$('sessions').children[1].children[0].textContent,'无工作目录 · 此电脑');
 ui.context.listReadFinished=true;ui.context.renderList();
 assert.equal(ui.$('sessions').children[0].textContent,'部分接入暂不支持按工作目录分类，请切换显示方式。');
 ui.context.listRows[0].directoryGroup={key:'known',name:'path',path:'/recorded/path',missing:false};ui.context.renderList();
 assert.equal(ui.$('sessions').children[0].children[0].textContent,'path · 此电脑');
});
test('directory folds survive added rows and stay separate across modes, gateways and entries',()=>{
 const ui=fixture();ui.$('list-mode').value='directory';
 const row=id=>({...ui.row(id,1),directoryGroup:{key:'shared-key',name:'shop',path:'/work/shop',missing:false}});
 ui.context.listRows=[row('first')];ui.context.renderList();let group=ui.$('sessions').children[0];
 group.open=false;group.ontoggle();ui.context.listRows.push(row('next'));ui.context.renderList();
 assert.equal(ui.$('sessions').children[0].open,false);
 ui.$('list-mode').value='project';ui.context.renderList();assert.equal(ui.$('sessions').children[0].open,true);
 ui.$('list-mode').value='directory';ui.context.selectedCodexEntry='codex:other';ui.context.renderList();assert.equal(ui.$('sessions').children[0].open,true);
 ui.context.selectedCodexEntry=null;ui.context.listIdentity='other-gateway';ui.context.renderList();assert.equal(ui.$('sessions').children[0].open,true);
 ui.context.listIdentity='gateway';ui.context.renderList();assert.equal(ui.$('sessions').children[0].open,false);
});
test('opaque provider IDs and archive capability stay isolated',async()=>{
 const ui=fixture(),chat={...ui.row('opaque/native-id',1),capabilities:{archive:true}};
 const menu=ui.context.sessionMenuContext(chat,'deepseek');
 assert.equal(typeof menu.rename,'function');assert.equal(menu.renameScope,'bridge');assert.equal(menu.fork,undefined);assert.equal(menu.nativeOrganization,true);assert.equal(menu.organizationScope,'bridge');
 await menu.archive();assert.equal(ui.requests[0].body.sessionId,chat.id);assert.equal(ui.requests[0].url,'/api/desktop-sessions/deepseek/archive');
 assert.equal(ui.context.sessionMenuContext({...chat,capabilities:{}},'claude').archive,undefined);
});
test('restoring an archived session never requests disconnect',async()=>{
 const ui=fixture();await ui.context.sessionMenuContext({...ui.row('a',1),archived:true}).archive();
 const write=ui.requests.find(row=>row.body);
 assert.equal(write.body.archived,false);assert.equal(write.body.disconnect,undefined);
});
test('account change invalidates a captured menu; read-only sources omit rename and fork',()=>{
 const ui=fixture(),menu=ui.context.sessionMenuContext(ui.row('a',1));assert.equal(menu.isCurrent(),true);
 ui.context.csrf='other';assert.equal(menu.isCurrent(),false);
 ui.context.sessionAccess=()=>({canConfigure:false,canEditMessages:false});
 const restricted=ui.context.sessionMenuContext(ui.row('a',1));assert.equal(restricted.rename,undefined);assert.equal(restricted.fork,undefined);
});
test('rename response for another active chat never replaces the current header',async()=>{
 const ui=fixture(),chat=ui.row('a',1);ui.context.currentId='b';ui.context.state={title:'Other'};ui.$('chat-title').textContent='Other';
 await ui.context.sessionMenuContext(chat).rename('New');assert.equal(ui.$('chat-title').textContent,'Other');
});
for(const [provider,runtime] of [['codex','desktop'],['codex','cli'],['claude','desktop'],['claude','code-cli'],['claude','cowork'],['deepseek','official'],['deepseek','manual']]){
 test(`${provider} ${runtime} menu keeps passive opening, notifications and supported capabilities`,async()=>{
  const ui=fixture(),chat={...ui.row('provider-session',1),runtime,capabilities:{fork:true,archive:true,unarchive:true}};
  let notified=0;
  if(provider==='codex'){
   ui.context.currentId=chat.id;ui.$('notify-button').onclick=()=>{notified++;};
  }else{
   ui.context.desktopSessions={provider,sid:chat.id,state:null,notifications:()=>{notified++;}};
  }
  const menu=ui.context.sessionMenuContext(chat,provider);
  assert.equal(ui.requests.filter(row=>row.body).length,0,'opening the menu does not write');
  assert.equal(ui.opened.length,0,'opening the menu does not activate chats');
  assert.ok(ui.requests.every(row=>row.url.endsWith('/recover')),'opening only reads the recovery context');
  assert.equal(menu.nativeOrganization,true);assert.equal(menu.organizationScope,provider==='codex'?'native':'bridge');assert.equal(typeof menu.rename,'function');
  assert.equal(typeof menu.pin,'function');assert.equal(typeof menu.fork,'function');assert.equal(typeof menu.archive,'function');
  await menu.notifications();assert.equal(notified,1);
  await menu.pin(true);assert.equal(ui.requests.at(-1).body.pinned,true);
  assert.equal(menu.link,provider==='codex'?'codex://threads/'+chat.id:null);
 });
}

test('desktop metadata actions retain opaque identity and do not activate chats',async()=>{
 const ui=fixture(),chat={...ui.row('code/native',1),capabilities:{rename:true}};
 const menu=ui.context.sessionMenuContext(chat,'claude');assert.equal(menu.renameScope,'native');
 await menu.organization();assert.equal(ui.requests[0].url,'/api/desktop-sessions/claude/organization?sessionId=code%2Fnative');
 await menu.organization({id:'stable-id',kind:'create-section',title:'Todo'});
 assert.equal(ui.requests[1].body.sessionId,'code/native');
 await menu.rename('Title');await menu.rename('Title');
 assert.equal(ui.requests[2].body.sessionId,'code/native');assert.equal(ui.requests[2].body.id,ui.requests[3].body.id);
 assert.ok(ui.requests.every(row=>!/detail|open|resume|activate/.test(row.url)));
});
