/* Presentation/navigation only. Reuses the live session and its action handlers. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const root=document.documentElement, $=id=>document.getElementById(id);
  let sessionGeneration=0,sessionKey='';
  let classic=false; try{classic=localStorage.getItem('dream.presentation')==='classic';}catch{}
  if(new URLSearchParams(location.search).get('design')==='classic') classic=true;
  root.classList.add('dream-design'); root.dataset.dreamView='chat';
  function preference(key, fallback){try{return localStorage.getItem('dream.'+key)||fallback;}catch{return fallback;}}
  function savePreference(key,value){try{localStorage.setItem('dream.'+key,value);}catch{}}
  root.dataset.density=preference('density','comfortable')==='compact'?'compact':'comfortable';
  root.dataset.banners=preference('banners','expanded')==='collapsed'?'collapsed':'expanded';
  root.dataset.atmosphere=preference('atmosphere','full')==='quiet'?'quiet':'full';
  const el=(tag,text,attrs={})=>{const n=document.createElement(tag); if(text!==null)n.textContent=text; for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;};
  const button=(text,fn,id)=>{const b=el('button',text,{type:'button'});if(id)b.id=id;b.onclick=fn;return b;};
  const nav=el('nav',null,{id:'dream-nav','aria-label':'Workspace'});
  const brand=el('div',null,{class:'dream-nav-brand'});
  const brandText=el('div','DREAM');brandText.append(el('small','Build · Explore · Align'));
  brand.append(el('img',null,{src:'/assets/dream-mark.svg',alt:'',width:'44',height:'44'}),brandText);nav.append(brand);
  const views=[['home','◉','Home'],['chat','◌','Chat'],['studio','◇','Studio'],['projects','▱','Projects'],['optimizer','✎','Prompt Optimizer'],['skills','✧','Skills'],['memory','▧','Memory'],['files','▤','Files'],['settings','⚙','Settings']];
  for(const [view,icon,label] of views){const b=button('',()=>navigate(view),'dream-nav-'+view);b.title=label;b.setAttribute('aria-label','Go to '+label);b.append(el('i',icon,{'aria-hidden':'true'}),el('span',label));nav.append(b);}
  nav.append(button('⌘',()=>palette.showModal(),'dream-command-open'));$('dream-command-open')?.setAttribute('aria-label','Command palette');
  document.body.prepend(nav);$('dream-command-open').setAttribute('aria-label','Command palette');
  const welcome=$('studio-interactions-empty');
  welcome.innerHTML='<div class="dream-welcome-copy"><span class="dream-eyebrow">Welcome to Dream</span><h2>Your ideas.<br>A higher state.</h2><p>A place to think, build, and create.<br>Your models. Your workspace.</p><div class="dream-welcome-actions"></div></div>';
  for(const [label,draft] of [['Research','Help me research '],['Build','Help me build '],['Create','Help me create ']]){
    welcome.querySelector('.dream-welcome-actions').append(button(label,()=>{const input=$('input');if(!input.value)input.value=draft;input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();}));
  }
  $('input').placeholder='What shall we explore today?';
  const workbar=el('div',null,{id:'dream-workbar','aria-label':'Current workspace and model'});
  const workspace=button('Workspace unreported',()=>{workspace.classList.toggle('full-path');workspace.textContent=workspace.classList.contains('full-path')?workspace.title:shortWorkspace();},'dream-workspace');
  const model=button('Model unreported',()=>$('dream-council-open')?.click(),'dream-model');
  let performanceSupported=false;
  const effort=button('Effort · unreported',()=>{$(performanceSupported?'dream-controls-open':'dream-council-open').click();},'dream-effort');
  effort.title='Adjust supported reasoning settings; provider handoffs apply between turns';
  const context=button('Context',()=>{inspector.hidden=!inspector.hidden;if(!inspector.hidden){refresh();environment();}},'dream-inspect-toggle');
  const utilityNav=document.querySelector('.studio-nav'), utilityAnchor=document.createComment('Classic workspace navigation');
  utilityNav.before(utilityAnchor);
  const utilityMenu=el('details',null,{id:'dream-workspace-tools'});utilityMenu.append(el('summary','Tools'));
  const narrowTools=matchMedia('(max-width:900px)');
  function toolLayout(){utilityMenu.open=!narrowTools.matches;}
  narrowTools.addEventListener('change',toolLayout);toolLayout();
  utilityMenu.addEventListener('keydown',e=>{if(e.key==='Escape'&&narrowTools.matches){e.preventDefault();utilityMenu.open=false;utilityMenu.querySelector('summary').focus();}});
  workbar.append(workspace,model,effort,context,utilityMenu);utilityNav.after(workbar);
  const home=el('section',null,{id:'dream-home','aria-label':'Dream Home'});home.hidden=true;
  home.innerHTML='<div class="dream-hero"><div class="dream-hero-copy"><span class="dream-eyebrow">Welcome to Dream</span><h1>Your ideas<br>have a <em>higher state.</em></h1><p>Your models. Your workspace.<br>A place to think, build, and create.</p><div class="dream-actions"></div></div></div><div class="dream-home-grid"><section class="dream-home-card"><h2>YOUR WORKSPACE</h2><p id="dream-home-path">Workspace unreported</p><p id="dream-home-session"></p><div id="dream-home-project"></div></section><section class="dream-home-card"><h2>RECENT WORK</h2><div id="dream-recovery">Open Home to read saved run records.</div></section></div>';
  home.querySelector('.dream-actions').append(button('Resume work →',()=>navigate('chat'),'dream-resume'),button('Open project',()=>navigate('projects')));
  home.querySelector('#dream-home-project').append(button('Browse files',()=>navigate('projects')));
  document.querySelector('.split').append(home);
  const inspector=el('aside',null,{id:'dream-inspector','aria-label':'Session context'});inspector.hidden=true;
  inspector.append(button('Close context',()=>{inspector.hidden=true;context.focus();}),el('h2','Session context'),el('p','Not yet observed',{id:'dream-observed'}),el('dl',null,{id:'dream-facts'}),el('h2','Recent failed tools'),el('div',null,{id:'dream-failures'}),el('h2','Environment readiness'),el('p','Not yet observed',{id:'dream-environment'}),el('h2','Pinned context'),el('div',null,{id:'dream-pins'}),el('h2','Memory saving'),el('p','No save status reported',{id:'dream-memory-state'}),el('h2','Council activity'),el('div',null,{id:'dream-council-activity'}),button('Detailed controls',()=>$('dream-controls-open').click()),button('Quiet / atmospheric',()=>{root.dataset.atmosphere=root.dataset.atmosphere==='quiet'?'full':'quiet';savePreference('atmosphere',root.dataset.atmosphere);}),button('Use classic presentation',()=>presentation(false)));
  const displayOptions=el('fieldset',null,{class:'dream-display-options'});displayOptions.append(el('legend','Presentation'));
  const densityLabel=el('label','Spacing'),density=el('select',null,{'aria-label':'Workspace spacing'});
  for(const [value,label] of [['comfortable','Comfortable'],['compact','Compact']])density.append(el('option',label,{value}));
  density.value=root.dataset.density;density.onchange=()=>{root.dataset.density=density.value;savePreference('density',density.value);};densityLabel.append(density);
  const banners=button('',()=>{root.dataset.banners=root.dataset.banners==='collapsed'?'expanded':'collapsed';savePreference('banners',root.dataset.banners);syncBanners();},'dream-banners-toggle');
  function syncBanners(){banners.textContent=root.dataset.banners==='collapsed'?'Expand artwork banners':'Collapse artwork banners';banners.setAttribute('aria-expanded',String(root.dataset.banners!=='collapsed'));}
  syncBanners();displayOptions.append(densityLabel,banners);inspector.append(displayOptions);
  document.querySelector('.split').append(inspector);
  const palette=el('dialog',null,{id:'dream-palette','aria-label':'Command palette'});
  palette.append(el('input',null,{id:'dream-command-search',type:'search',placeholder:'Go to…','aria-label':'Find a destination'}),el('div',null,{id:'dream-command-list'}),button('Close',()=>palette.close()));document.body.append(palette);
  function commands(){const q=$('dream-command-search').value.toLowerCase();$('dream-command-list').replaceChildren(...views.filter(v=>v[2].toLowerCase().includes(q)).map(v=>button(v[2],()=>{palette.close();navigate(v[0]);})));}
  $('dream-command-search').oninput=commands;commands();
  $('dream-command-open').onclick=()=>{palette.showModal();$('dream-command-search').focus();};
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();palette.showModal();$('dream-command-search').focus();}if(e.key==='Escape'&&!inspector.hidden){inspector.hidden=true;context.focus();}});
  const destinations={chat:'studio-interactions',studio:'studio-preview',projects:'studio-project',skills:'studio-skills',settings:'dream-controls-open'};
  function navigate(view){
    if(!views.some(v=>v[0]===view))return;
    window.DreamLibrary?.hide();
    window.PromptOptimizer?.hide(false);
    root.classList.remove('dream-expanded');
    document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
    if(view==='chat'||view==='studio')inspector.hidden=true;
    root.dataset.dreamView=view;home.hidden=view!=='home';
    nav.querySelectorAll('[aria-current]').forEach(n=>n.removeAttribute('aria-current'));$('dream-nav-'+view)?.setAttribute('aria-current','page');
    if(view==='optimizer'){inspector.hidden=true;$('studio-interactions')?.click();window.PromptOptimizer?.show();}
    else if(view==='home'){inspector.hidden=true;loadHome();}
    else if(view==='memory'){inspector.hidden=true;window.DreamLibrary?.show('memory');}
    else if(view==='files'){inspector.hidden=true;window.DreamLibrary?.show('files');}
    else if(destinations[view])$(destinations[view])?.click();
  }
  let pendingNative=null;
  window.addEventListener('dream:navigate',e=>{if(!window.DREAM_SESSION){pendingNative=e.detail;return;}if(e.detail?.session_id&&e.detail.session_id===window.DREAM_SESSION?.session_id)navigate(e.detail.view);});
  window.addEventListener('dream:session',()=>{const key=[window.DREAM_SESSION?.session_id,window.DREAM_SESSION?.workspace].join('|');if(key!==sessionKey){sessionKey=key;sessionGeneration++;}environmentLoaded=false;if(pendingNative?.session_id && pendingNative.session_id===window.DREAM_SESSION?.session_id)navigate(pendingNative.view);pendingNative=null;workspace.title=window.DREAM_SESSION.workspace||'Workspace unreported';workspace.textContent=shortWorkspace();model.textContent=window.DREAM_SESSION.model||window.DREAM_SESSION.provider||'Model unreported';effort.textContent='Effort · '+(Object.hasOwn(window.DREAM_SESSION,'reasoning_effort')?(window.DREAM_SESSION.reasoning_effort||'provider default'):'unreported');model.title=[window.DREAM_SESSION.provider,window.DREAM_SESSION.model].filter(Boolean).join(' · ');$('dream-home-path').textContent=workspace.title;$('dream-home-session').textContent=window.DREAM_SESSION.session_id||'Session identity unreported';});
  function shortWorkspace(){return (window.DREAM_SESSION?.workspace||'Workspace unreported').replace(/[\\/]+$/,'').split(/[\\/]/).pop();}
  async function read(path){const generation=sessionGeneration;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);try{const response=await fetch(path,{headers:{'X-Dream-Token':TOKEN},cache:'no-store',signal:controller.signal});const data=await response.json();if(generation!==sessionGeneration){const stale=new Error('Workspace changed while loading.');stale.stale=true;throw stale;}if(!response.ok)throw new Error(data.error||'Unavailable ('+response.status+')');return data;}finally{clearTimeout(timer);}}
  let homeLoading=false;
  async function loadHome(){if(homeLoading)return;homeLoading=true;try{const data=await read('/api/project/recovery');const box=$('dream-recovery');box.replaceChildren();if(!data.runs?.length)box.append(el('p',data.message||'No saved runs in this workspace.'));for(const run of (data.runs||[]).slice(0,10)){const p=el('p',[run.run_id,run.state||run.status||'Outcome unreported'].filter(Boolean).join(' · '));box.append(p);}box.append(button('Inspect saved runs',()=>navigate('projects')));}catch(e){if(e.stale)return;$('dream-recovery').textContent=e.message;}finally{homeLoading=false;}}
  let environmentLoaded=false;
  async function environment(){if(environmentLoaded)return;environmentLoaded=true;try{const [media,project]=await Promise.all([read('/api/media/status'),read('/api/project/manifest')]);$('dream-environment').textContent='Media status · observed '+new Date().toLocaleTimeString()+' · ffmpeg '+(media.renderer?.ffmpeg?'found':'not found')+' · ffprobe '+(media.renderer?.ffprobe?'found':'not found')+' · Blender '+(media.blender?.available?'found':'not found')+' · Cycles '+(media.blender?.cycles?'reported':'unverified')+' · headless render '+(media.blender?.headless_render||'unverified')+'. '+(media.blender?.note||'Blender qualification unreported');$('dream-pins').replaceChildren(...(project.pins||[]).slice(0,12).map(p=>el('p',p.label||p.path||p.text||p.id)));if(!$('dream-pins').children.length)$('dream-pins').append(el('p','No pinned context reported.'));}catch(e){if(e.stale)return;$('dream-environment').textContent=e.message;environmentLoaded=false;}}
  window.addEventListener('dream:media-state',e=>{const d=e.detail;$('dream-output-evidence').textContent=d.state==='error'?'Preview reported media error '+String(d.error||'unknown')+' · open the file externally or inspect the source':d.state==='timeupdate'?'Preview reported playback at '+d.time.toFixed(1)+'s · visual review unreported':'Preview reported '+d.state+' · visual review unreported';});
  let refreshing=false, observed=0;
  async function refresh(){if(refreshing||document.hidden)return;refreshing=true;try{const r=await read('/api/runtime');performanceSupported=r.performance?.supported===true;observed=Date.now();const value=v=>v===null||v===undefined?'Unreported':typeof v==='object'?JSON.stringify(v).slice(0,4000):String(v);const facts=[['Model',r.model],['Provider',r.provider],['Workspace',r.workspace],['Context owner',r.context_owner],['HTTP read timeout',r.transport?.available?(r.transport.read_timeout_s===null?'No cutoff':r.transport.read_timeout_s+' s'):'Unreported'],['Run state',r.run?.state],['Execution',r.execution?.checked===false?'Not checked':r.execution?.reason],['Reasoning',(r.performance?.effective?.reasoning_effort ?? window.DREAM_SESSION?.reasoning_effort)],['Context',r.context]];const list=$('dream-facts');list.replaceChildren();for(const [key,v] of facts)list.append(el('dt',key),el('dd',value(v)));effort.textContent='Effort · '+value((r.performance?.effective?.reasoning_effort ?? window.DREAM_SESSION?.reasoning_effort));$('dream-observed').textContent='Runtime API · observed '+new Date(observed).toLocaleTimeString();$('dream-memory-state').textContent=r.memory?.save?[r.memory.save.state,r.memory.save.updated_at,r.memory.save.workspace,r.memory.save.error].filter(Boolean).join(' · '):'No save status reported. Session persistence and memory consolidation are separate.';const failed=[...document.querySelectorAll('#stream .tool.err')].slice(-5);$('dream-failures').replaceChildren(...(failed.length?failed.map(n=>el('p',n.querySelector('summary')?.textContent?.slice(0,240))):[el('p','No failed tools in the loaded feed.')]));}catch(e){if(e.stale)return;$('dream-observed').textContent=(observed?'Stale · last observed '+new Date(observed).toLocaleTimeString()+' · ':'')+e.message;}finally{refreshing=false;}}
  setInterval(()=>{if(!inspector.hidden&&!document.hidden)refresh();},10000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!inspector.hidden)refresh();});
  // Observe existing permission delivery: an expanded output never hides approval.
  new MutationObserver(()=>{if(document.querySelector('.permission-card')){window.DreamLibrary?.hide();window.PromptOptimizer?.hide(false);root.classList.remove('dream-expanded');home.hidden=true;inspector.hidden=true;root.dataset.dreamView='chat';}}).observe($('stream'),{childList:true,subtree:true});
  const approval=button('Approval waiting',()=>navigate('chat'),'dream-approval-return');approval.hidden=true;workbar.append(approval);
  new MutationObserver(()=>{approval.hidden=!document.querySelector('.permission-card');}).observe($('stream'),{childList:true,subtree:true});
  const output=el('div',null,{id:'dream-output-tools'});output.append(el('span','Output not selected',{id:'dream-output-evidence'}),button('Expand',()=>{root.classList.toggle('dream-expanded');},'dream-output-expand'),button('Compare',compare,'dream-output-compare'),button('History',history,'dream-output-history'));
  const resize=el('input',null,{id:'dream-split-size',type:'range',min:'30',max:'65',value:'45','aria-label':'Conversation share of split view'});resize.oninput=()=>root.style.setProperty('--dream-chat-share',resize.value+'%');output.append(resize);$('artbody').before(output);
  const compareDialog=el('dialog',null,{id:'dream-compare','aria-label':'Compare preserved output revisions'});compareDialog.append(el('h2','Preserved source revisions'),el('p','Comparison is read-only. It does not overwrite files or establish visual quality.'),el('div',null,{class:'dream-compare-grid'}),button('Close',()=>compareDialog.close()));document.body.append(compareDialog);
  async function history(){const dialog=compareDialog,grid=dialog.querySelector('.dream-compare-grid');dialog.querySelector('h2').textContent='Registered output history';grid.replaceChildren(el('p','Reading saved media records…'));dialog.showModal();try{const data=await read('/api/media/history');grid.replaceChildren();for(const row of data.outputs||[]){const col=el('section',null);col.append(el('h3',row.name),el('p',row.path),el('p','Saved '+row.created),el('p','Asset '+row.id),el('p','Revision '+(row.provenance?.revision??'unreported')+' · task '+(row.provenance?.job_id??'unreported')),el('p','Verification '+row.verification));grid.append(col);}if(!grid.children.length)grid.append(el('p','No registered outputs. Studio source revisions remain available in Compare for this session.'));}catch(e){if(e.stale)return;grid.replaceChildren(el('p',e.message));}}
  function compare(){compareDialog.querySelector('h2').textContent='Preserved source revisions';const a=artifacts.get(artOpen);if(!a)return;const grid=compareDialog.querySelector('.dream-compare-grid');grid.replaceChildren();for(const i of [...new Set([Math.max(0,artVerIdx-1),artVerIdx])]){const col=el('section',null);col.append(el('h3',a.title+' · v'+(i+1)),el('pre',a.versions[i]));grid.append(col);}compareDialog.showModal();}
  window.addEventListener('dream:artifact',()=>{window.DreamLibrary?.hide();home.hidden=true;root.dataset.dreamView='studio';nav.querySelectorAll('[aria-current]').forEach(n=>n.removeAttribute('aria-current'));$('dream-nav-studio').setAttribute('aria-current','page');const a=artifacts.get(artOpen);$('dream-output-evidence').textContent=a?`${a.title} · v${artVerIdx+1} · output received · visual review unreported`:'No output selected';$('dream-output-compare').disabled=!a||a.versions.length<2;});
  const councilTasks=new Map();
  window.addEventListener('dream:council-activity',e=>{const row=e.detail;if(!row||typeof row.task_id!=='string')return;councilTasks.set(row.task_id,row);if(councilTasks.size>32)councilTasks.delete(councilTasks.keys().next().value);$('dream-council-activity').replaceChildren(...[...councilTasks.values()].slice(-8).map(r=>el('p',[r.member,r.model,r.state,r.ownership,r.observed_at,r.detail].filter(Boolean).join(' · '))));});
  window.addEventListener('dream:project-reset',()=>{sessionGeneration++;environmentLoaded=false;councilTasks.clear();$('dream-council-activity').replaceChildren(el('p','No Council work events in this session.'));$('dream-pins').replaceChildren();$('dream-facts').replaceChildren();$('dream-environment').textContent='Not yet observed';$('dream-output-evidence').textContent='Output not selected';});
  $('dream-council-activity').append(el('p','No Council work events in this loaded session.'));
  for(const [id,view] of [['studio-interactions','chat'],['studio-preview','studio']])$(id).addEventListener('click',()=>{window.DreamLibrary?.hide();home.hidden=true;root.dataset.dreamView=view;nav.querySelectorAll('[aria-current]').forEach(n=>n.removeAttribute('aria-current'));$('dream-nav-'+view).setAttribute('aria-current','page');});
  // Navigation marks the current page, independently of the conversation drawer.
  function syncDestination() {
    const libraryView=['projects','skills'].find(name=>{const page=$('dream-'+name+'-page');return page&&!page.hidden;});
    const view=root.classList.contains('dream-design')?root.dataset.dreamView:(libraryView||(document.body.classList.contains('interactions-open')?'chat':'studio'));
    nav.querySelectorAll('[aria-current]').forEach(n=>n.removeAttribute('aria-current'));
    $('dream-nav-'+view)?.setAttribute('aria-current','page');
    for(const [name,id] of Object.entries({chat:'studio-interactions',studio:'studio-preview',projects:'studio-project',skills:'studio-skills'})){
      const item=$(id);if(!item)continue;
      const current=name===view;item.classList.toggle('selected',current);
      if(current)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');
    }
  }
  new MutationObserver(syncDestination).observe(root,{attributes:true,attributeFilter:['data-dream-view','class']});
  window.addEventListener('dream:drawer',syncDestination);
  syncDestination();
  const restore=button('Dream design',()=>presentation(true),'dream-restore-design');document.querySelector('header').append(restore);
  function presentation(on){if(on)utilityMenu.append(utilityNav);else utilityAnchor.after(utilityNav);window.DreamLibrary?.hide();root.classList.toggle('dream-design',on);root.classList.remove('dream-expanded');for(const n of [nav,workbar,output])n.hidden=!on;home.hidden=true;inspector.hidden=true;restore.hidden=on;try{localStorage.setItem('dream.presentation',on?'dream':'classic');}catch{}}
  $('dream-controls').addEventListener('close',refresh);
  navigate('chat');presentation(!classic);refresh();
})();
