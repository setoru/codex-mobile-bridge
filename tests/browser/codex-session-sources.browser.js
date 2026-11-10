// Run only in bootstrap-fixture.py; all session requests use synthetic data.
async function runCodexSessionSourcesChecks(){
  const checks=[],originalApi=api;
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const rows=Array.from({length:151},(_,i)=>{
    const source=i<12?'cli':i<48?'desktop':'ide';
    return {id:'11111111-1111-4111-8111-'+String(i).padStart(12,'0'),host:'local',
      title:source+' fixture '+i,recency:151-i,projectKey:'local|fixture',projectName:'Fixture',
      access:{source,canReconnect:true,readOnly:true}};
  });
  const reads=[],writes=[];
  try{
    await BridgeFeatures.load('chat');
    api=async(url,body)=>{
      const parsed=new URL(url,location.origin);
      if(parsed.pathname==='/api/activity')return {sessions:[]};
      if(body){writes.push(parsed.pathname);throw Error('Unexpected write: '+parsed.pathname);}
      if(parsed.pathname!=='/api/sessions')return {};
      const source=parsed.searchParams.get('source'),offset=Number(parsed.searchParams.get('offset'));
      reads.push({source,offset,host:parsed.searchParams.get('host')});
      return {sessions:rows.filter(row=>source==='all'||row.access.source===source).slice(0,offset+101),
        availableSources:['cli','desktop','ide'],remoteHosts:[],complete:true};
    };
    $('login').hidden=true;$('app').hidden=false;$('search').value='';$('archived').checked=false;$('list-mode').value='recent';
    clientNavigation.applyClients({clients:[{id:'codex',name:'Codex',enabled:true}]});
    await clientNavigation.choose('codex',{loadList:false});
    selectedCodexEntry=null;listRows=[];listHostIds=[];listFilterKey='';listSource='all';
    await loadList(true);
    check(listRows.length===100&&$('sessions').querySelectorAll('.session').length===100,'The default local list displays the first 100 mixed-source chats');
    check([...$('source-filter').options].some(option=>option.value==='ide'),'The default list offers the VS Code source filter');
    await loadList();
    check(listRows.length===151&&new Set(listRows.map(row=>row.id)).size===151,'Loading the next prefix keeps all 151 chats without duplicates');
    check(reads.every(read=>read.host==='local')&&reads.some(read=>read.offset===100),'Pagination reads only the selected local configuration');
    listSource='ide';await loadList(true);await loadList();
    check(listRows.length===103&&listRows.every(row=>row.access.source==='ide'),'The VS Code filter includes IDE chats beyond the first 100');
    check($('sessions').querySelectorAll('.session').length===103,'All IDE results are rendered in the list');
    listSource='cli';await loadList(true);
    check(listRows.length===12&&listRows.every(row=>row.access.source==='cli'),'CLI filtering stays separate from IDE');
    listSource='desktop';await loadList(true);
    check(listRows.length===36&&listRows.every(row=>row.access.source==='desktop'),'APP filtering stays separate from IDE');
    check(writes.length===0,'Listing and filtering issue no session writes');
    return {passed:checks.length,checks};
  }finally{api=originalApi;$('app').hidden=true;}
}

async function runCodexDeepLinkChecks(){
  const checks=[],originalApi=api,pending=new Map(),activations=[];
  const check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const until=async predicate=>{const end=performance.now()+4500;while(!predicate()){if(performance.now()>end)throw Error('Timed out waiting for synthetic history');await new Promise(resolve=>setTimeout(resolve,20));}};
  const ide='22222222-2222-4222-8222-222222222222',app='33333333-3333-4333-8333-333333333333';
  const stale='44444444-4444-4444-8444-444444444444',current='55555555-5555-4555-8555-555555555555';
  const history=(id,source)=>({sequence:1,epoch:id,before:id+'.saved',hasMore:false,files:[],
    meta:{id,host:'local',title:id,requests:[],submissions:[],connected:false,loadingHistory:false,historyComplete:true,status:'idle',
      access:{source,runtime:null,canReconnect:true,canSend:false,canChangeModel:false,canSelectSkills:false,readOnly:true}},
    rows:[{key:'saved',order:0,role:'assistant',version:'v1',text:'Saved '+source+' history'}]});
  try{
    await BridgeFeatures.load('chat');
    api=async(url,body,signal)=>{
      const parsed=new URL(url,location.origin),id=parsed.pathname.split('/')[3];
      if(parsed.pathname.endsWith('/reconnect')){activations.push({id,host:parsed.searchParams.get('host'),body});return {ok:true};}
      if(parsed.pathname.endsWith('/timeline'))return new Promise(resolve=>pending.set(id,resolve));
      if(parsed.pathname.endsWith('/changes'))return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true}));
      if(parsed.pathname==='/api/activity')return {sessions:[]};
      if(body)throw Error('Unexpected write: '+parsed.pathname);
      if(parsed.pathname.endsWith('/notifications'))return {available:false};
      return {sessions:[],remoteHosts:[],availableSources:[],complete:true};
    };
    $('login').hidden=true;$('app').hidden=false;
    clientNavigation.applyClients({clients:[{id:'codex',name:'Codex',enabled:true}]});
    await clientNavigation.choose('codex',{loadList:false});listRows=[];
    const openingIde=clientNavigation.openChatLink('#'+ide+'~local',openChat);
    await until(()=>pending.has(ide));
    check(!listRows.some(row=>row.id===ide),'The IDE deep link opens without a previously loaded list row');
    check(activations.length===0,'An unknown source does not activate while history is pending');
    pending.get(ide)(history(ide,'ide'));await openingIde;
    check(activations.length===0&&$('messages').textContent.includes('Saved ide history'),'IDE deep links read saved history without activation');
    const openingApp=clientNavigation.openChatLink('#'+app+'~local',openChat);
    await until(()=>pending.has(app));
    check(activations.length===0,'An APP deep link also waits for authoritative source metadata');
    pending.get(app)(history(app,'desktop'));await openingApp;
    check(activations.length===1&&activations[0].id===app&&activations[0].host==='local'&&activations[0].body.activate===true,'Fresh APP metadata preserves automatic owner activation');
    const openingStale=clientNavigation.openChatLink('#'+stale+'~local',openChat);
    await until(()=>pending.has(stale));
    const openingCurrent=clientNavigation.openChatLink('#'+current+'~local',openChat);
    await until(()=>pending.has(current));
    pending.get(current)(history(current,'ide'));await openingCurrent;
    pending.get(stale)(history(stale,'desktop'));await openingStale;
    check(activations.length===1,'Late APP metadata cannot activate a chat after navigation');
    check(currentId===current&&$('chat-title').textContent===current,'Late history cannot replace the selected IDE chat');
    return {passed:checks.length,checks};
  }finally{
    chatTimeline?.dispose();chatTimeline=null;currentId=null;state=null;
    for(const [id,resolve] of pending)resolve(history(id,'ide'));
    api=originalApi;$('app').hidden=true;
  }
}
