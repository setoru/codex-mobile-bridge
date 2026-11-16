'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
const code=source.slice(source.indexOf('function renderList(){'),source.indexOf('async function loadList('));
function fixture(){
 const nodes=new Map(),timers=new Map(),requests=[],dialogs=[],copies=[],opened=[],notices=[],storage=new Map();let timer=0;
 const node=(tag='',cls='',textContent='')=>({tag,cls,textContent,dataset:{},classList:{add(){}},children:[],events:{},scrollTop:0,isConnected:true,append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},addEventListener(name,run){this.events[name]=run;},setAttribute(){},focus(){this.focused=true;},showModal(){this.open=true;},close(){this.open=false;this.events.close?.();},remove(){this.isConnected=false;}});
 const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};$('list-mode').value='recent';
 const context=vm.createContext({CliConnectionsPanel:class{},$,el:node,window:{BridgeSessionLinks:{text:identity=>'codex://threads/'+identity.id}},document:{body:{append:box=>dialogs.push(box)}},SessionMenu:class{constructor(){}bind(button,context){button.menuContext=context;}},
   BridgeClipboard:{copyWithFeedback:async(get,item)=>{if(item.disabled)return;copies.push(get());}},BridgeI18n:{locale:()=> 'en-US'},t:s=>s,localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
   workbench:{},listIdentity:'gateway',selectedCodexEntry:null,codexActiveAccountId:'account',chatOpenGeneration:0,csrf:'token',clientNavigation:{provider:'codex',clients:[]},state:null,listRows:[],currentId:null,currentHost:'local',listHasMore:false,listRenderStamp:'',listComplete:true,listReadFinished:true,availableSources:[],
   renderActivity(){},sessionAccess:v=>({source:'desktop',canConfigure:true,canEditMessages:true,...v}),sourceLabel:()=> 'APP',dateText:()=>'',toast:text=>notices.push(text),pinRow:row=>row,sessionPins:{value:(_identity,pinned)=>!!pinned,set(identity,pinned){requests.push({url:`/${identity.host}/${identity.id}/pin`,body:{pinned}});}},desktopSessions:{refresh:async()=>{}},openChat:async(id,host)=>opened.push({id,host}),
   sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
   setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),uuid:()=> 'request-id',
   api:async(url,body)=>{requests.push({url,body});return {status:'confirmed'};},sessionUrl:(id,action,host)=>`/${host}/${id}/${action}`,loadList:async()=>{}});
 context.ClientIdentity=require('../web/features/clients/client-lifecycle.js').ClientIdentity;context.URLSearchParams=URLSearchParams;
 vm.runInContext(code,context);
 const row=(id,recency,pinned=false)=>({id,host:'local',title:id,projectKey:'p',projectName:'Project',hostLabel:'Local',recency,bridgePinned:pinned,access:{}});
 return {context,$,node,row,requests,dialogs,copies,opened,notices,storage,fireTimers(){const work=[...timers.values()];timers.clear();work.forEach(fn=>fn());}};
}
test('recovery creates and sends from the captured source then opens the returned conversation',async()=>{
 const ui=fixture(),chat={...ui.row('source',1),host:'remote',access:{source:'cli'}};
 ui.context.currentId='another-chat';
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  return body?{operationId:body.id,status:'confirmed',phase:'complete',id:'created',host:'remote'}:{status:'idle',operationId:null,id:null,host:'remote'};
 };
 const context=ui.context.sessionMenuContext(chat),progress=[];
 assert.equal(typeof context.recover,'function');
 assert.equal(ui.requests.length,0);
 const result=await context.recover(value=>progress.push(value));
 assert.equal(result.status,'confirmed');
 assert.deepEqual(ui.requests.map(row=>row.url),['/remote/source/recover','/remote/source/recover']);
 assert.equal(ui.requests[1].body.id,'request-id');
 assert.deepEqual(Object.keys(ui.requests[1].body),['id']);
 assert.deepEqual(ui.opened,[{id:'created',host:'remote'}]);
 assert.equal(progress.at(-1).status,'confirmed');
 assert.equal([...ui.storage.keys()].filter(key=>key.startsWith('session-recovery:')).length,0);
});
test('recovery preserves newer navigation and rejects a changed account before dispatch',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let finish;
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  return body?new Promise(resolve=>{finish=resolve;}):{status:'idle',operationId:null};
 };
 const menu=ui.context.sessionMenuContext(chat),pending=menu.recover();
 await new Promise(resolve=>setImmediate(resolve));
 ui.context.currentId='user-selected';ui.context.chatOpenGeneration++;
 finish({status:'confirmed',phase:'complete',operationId:'request-id',id:'created',host:'local'});
 await pending;assert.deepEqual(ui.opened,[]);
 const stale=ui.context.sessionMenuContext(chat);ui.context.codexActiveAccountId='new-account';
 await assert.rejects(()=>stale.recover(),/连接或账号/);
 assert.equal(ui.requests.length,2);
});
test('reopening an unsent recovery retains its text and only an explicit continue submits the same operation',async()=>{
 const ui=fixture(),chat=ui.row('source',1),saved={status:'not_submitted',phase:'sending',operationId:'previous',id:'created',host:'local',text:'读取会话[示例](codex://threads/source)，继续完成'};
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});return body?{...saved,status:'confirmed',phase:'complete'}:saved;
 };
 const first=await ui.context.sessionMenuContext(chat).recover();
 assert.equal(first.text,saved.text);assert.equal(ui.requests.filter(row=>row.body).length,0);assert.deepEqual(ui.opened,[]);
 const freshMenu=ui.context.sessionMenuContext(chat);
 await freshMenu.recover(()=>{},{mode:'continue'});
 assert.equal(ui.requests.at(-1).body.id,'previous');
 assert.equal(ui.requests.filter(row=>row.body).length,1);
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);
});
test('lost recovery responses are reconciled by reads and unknown sends are never resubmitted',async()=>{
 const ui=fixture(),chat=ui.row('source',1),unknown={operationId:'request-id',status:'unknown',phase:'sending',id:'created',host:'local',message:'结果待核对'};
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(body)throw Error('network interrupted');
  return url.includes('id=')?unknown:{status:'idle',operationId:null};
 };
 const result=await ui.context.sessionMenuContext(chat).recover();
 assert.equal(result.status,'unknown');assert.equal(result.id,'created');
 await ui.context.sessionMenuContext(chat).recover(()=>{},{mode:'continue'});
 assert.equal(ui.requests.filter(row=>row.body).length,1);
 assert.equal(ui.requests[2].url,'/local/source/recover?id=request-id');
 assert.deepEqual(ui.opened,[]);
});
test('a stored recovery with no server record can only be explicitly continued using its original ID',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let accept=false;
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(body){
   if(!accept)throw Error('request did not reach the server');
   return {status:'confirmed',phase:'complete',operationId:body.id,id:'created',host:'local'};
  }
  return {status:'idle',operationId:null,id:null,host:'local'};
 };
 const interrupted=await ui.context.sessionMenuContext(chat).recover();
 assert.equal(interrupted.status,'not_submitted');assert.equal(interrupted.phase,'creating');assert.equal(interrupted.operationId,'request-id');
 const reopened=ui.context.sessionMenuContext(chat);
 const checked=await reopened.recover(()=>{},{mode:'check'});
 assert.equal(checked.status,'not_submitted');assert.equal(ui.requests.filter(row=>row.body).length,1);
 accept=true;await reopened.recover(()=>{},{mode:'continue'});
 const writes=ui.requests.filter(row=>row.body);
 assert.equal(writes.length,2);assert.equal(writes[0].body.id,writes[1].body.id);
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);
});
test('a pending recovery reports server progress while the original write remains in flight',async()=>{
 const ui=fixture(),chat=ui.row('source',1),progress=[];let finish;
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(body)return new Promise(resolve=>{finish=resolve;});
  return url.includes('id=')?{status:'running',phase:'settings',operationId:'request-id',id:'created',host:'local'}:{status:'idle',operationId:null};
 };
 const pending=ui.context.sessionMenuContext(chat).recover(value=>progress.push(value));
 await new Promise(resolve=>setImmediate(resolve));ui.fireTimers();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(progress.at(-1).phase,'settings');
 finish({status:'confirmed',phase:'complete',operationId:'request-id',id:'created',host:'local'});
 await pending;ui.fireTimers();assert.equal(progress.at(-1).status,'confirmed');
 assert.equal(ui.requests.filter(row=>row.body).length,1);
});
test('the retained recovery destination opens explicitly and cannot cross the original host',async()=>{
 const ui=fixture(),menu=ui.context.sessionMenuContext({...ui.row('source',1),host:'remote'});
 assert.equal(typeof menu.openRecovery,'function');
 await menu.openRecovery({id:'created',host:'remote'});
 assert.deepEqual(ui.opened,[{id:'created',host:'remote'}]);
 await assert.rejects(()=>menu.openRecovery({id:'wrong',host:'local'}),/新会话/);
 ui.context.selectedCodexEntry='other';
 await assert.rejects(()=>menu.openRecovery({id:'created',host:'remote'}),/连接或账号/);
});
test('reopening a running recovery follows it with reads until completion',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let reads=0;
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});reads++;
  return {status:reads===1?'running':'confirmed',phase:reads===1?'sending':'complete',operationId:'existing',id:'created',host:'local'};
 };
 const pending=ui.context.sessionMenuContext(chat).recover();
 await new Promise(resolve=>setImmediate(resolve));ui.fireTimers();
 const result=await pending;
 assert.equal(result.status,'confirmed');assert.equal(ui.requests.length,2);assert.equal(ui.requests.filter(row=>row.body).length,0);
 assert.equal(ui.requests[1].url,'/local/source/recover?id=existing');
 assert.deepEqual(ui.opened,[{id:'created',host:'local'}]);
});
test('a rejected recovery can be explicitly retried with a new operation after its inputs are corrected',async()=>{
 const ui=fixture(),chat=ui.row('source',1);let writes=0,sequence=0,lastOperation;
 ui.context.uuid=()=> 'operation-'+(++sequence);
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(body){writes++;lastOperation=body.id;return {operationId:body.id,status:writes===1?'failed':'confirmed',phase:writes===1?'validating':'complete',id:writes===1?null:'created',host:'local',message:'原目录不存在'};}
  return writes?{operationId:lastOperation,status:'failed',phase:'validating',id:null,host:'local'}:{status:'idle',operationId:null};
 };
 const menu=ui.context.sessionMenuContext(chat);
 assert.equal((await menu.recover()).status,'failed');
 assert.equal((await menu.recover(()=>{},{mode:'retry'})).status,'confirmed');
 const submitted=ui.requests.filter(row=>row.body);assert.equal(submitted.length,2);assert.notEqual(submitted[0].body.id,submitted[1].body.id);
});
test('a recovery deduplicated by the server follows the returned operation identity',async()=>{
 const ui=fixture(),chat=ui.row('source',1);
 ui.context.api=async(url,body)=>{
  ui.requests.push({url,body});
  if(body)return {status:'running',phase:'sending',operationId:'other-device',id:'created',host:'local'};
  if(url.includes('id='))return {status:'confirmed',phase:'complete',operationId:'other-device',id:'created',host:'local'};
  return {status:'idle',operationId:null};
 };
 const pending=ui.context.sessionMenuContext(chat).recover();
 await new Promise(resolve=>setImmediate(resolve));ui.fireTimers();await pending;
 assert.equal(ui.requests.at(-1).url,'/local/source/recover?id=other-device');
 assert.equal(ui.requests.filter(row=>row.body).length,1);
});
test('native organization preserves host and expected assignment and rejects stale authentication',async()=>{
 const ui=fixture(),chat={...ui.row('native',1),host:'remote'};
 const context=ui.context.sessionMenuContext(chat);
 await context.organization({kind:'project',targetId:'project-b',expectedId:'project-a'});
 assert.equal(ui.requests.at(-1).url,'/remote/native/organization');
 assert.equal(ui.requests.at(-1).body.expectedId,'project-a');
 ui.context.csrf='replacement';
 await assert.rejects(()=>context.organization(),/连接或账号/);
 assert.equal(ui.requests.length,1);
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
 assert.equal(ui.requests[0].url,'/remote/a/pin');assert.equal(ui.requests[0].body.pinned,true);
 assert.equal(ui.requests[1].url,'/remote/a/rename');assert.equal(ui.requests[1].body.title,'New');
 assert.equal(ui.requests[2].url,'/remote/a/archive');assert.equal(ui.requests[2].body.disconnect,true);
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
 assert.equal(ui.requests[0].body.archived,false);assert.equal(ui.requests[0].body.disconnect,undefined);
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
  assert.equal(ui.requests.length,0,'opening the menu does not activate or write');
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
