/* User-owned skills and projects. Opening a page never sends a model prompt. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const node = (tag, text, attrs = {}) => {
    const n = document.createElement(tag);
    if (text !== null) n.textContent = text;
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, value);
    return n;
  };
  const button = (text, fn, attrs = {}) => {
    const b = node('button', text, {type:'button', ...attrs}); b.onclick = fn; return b;
  };
  async function api(path, payload) {
    const response = await fetch(path, {method:payload === undefined ? 'GET' : 'POST',
      headers:{'X-Dream-Token':TOKEN, ...(payload === undefined ? {} : {'Content-Type':'application/json'})},
      cache:'no-store', ...(payload === undefined ? {} : {body:JSON.stringify(payload)})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Request failed (' + response.status + ')');
    return data;
  }
  const pages = {};
  // The list column: search, Show and Sort, count, keyboard hint and rows. DREAM-174: the Skills page's preset column
  // is built by the same function, so the two columns are identically structured.
  function sidebar(view, title, key=view, cls='library-sidebar') {
    const aside = node('aside',null,{class:cls,'aria-label':title+' list'});
    const search = node('input',null,{type:'search',placeholder:'Search '+title.toLowerCase(),'aria-label':'Search '+title.toLowerCase()});
    const list = node('div',null,{class:'library-list'});
    const filters=node('div',null,{class:'library-filters'});
    const filter=field(filters,'Show','select',{'aria-label':'Filter '+title.toLowerCase()}),sort=field(filters,'Sort','select',{'aria-label':'Sort '+title.toLowerCase()});
    const choices=view==='skills'?[['all','All skills'],['managed','Your versions'],['installed','Installed originals'],['enabled','Enabled'],['disabled','Disabled'],['plugins','Plugins'],['mcp','MCP servers'],['tools','Tool groups']]:view==='memory'?[['all','All memories'],['current','This project'],['everywhere','Everywhere'],['project','Project notebooks']]:view==='files'?[['workspace','Project folder'],['dream','Dream setup']]:[['all','All projects'],['current','Current workspace'],['conversations','With conversations']];
    for(const [value,label] of choices)filter.append(node('option',label,{value}));
    for(const [value,label] of view==='memory'?[['recent','Recently changed'],['name','Name A–Z']]:[['name','Name A–Z'],['reverse','Name Z–A'],...(view==='projects'?[['conversations','Most conversations']]:[])])sort.append(node('option',label,{value}));
    const count=node('p','',{class:'library-count',role:'status','aria-live':'polite'});
    const hint=node('p','Use ↑ and ↓ to browse, Enter to open.',{class:'library-keyboard-hint',id:'library-'+key+'-keys'});list.setAttribute('aria-describedby',hint.id);
    list.onkeydown=event=>{const items=[...list.querySelectorAll('button:not(.library-assign)')],index=items.indexOf(document.activeElement);if(index<0)return;let next;if(event.key==='ArrowDown')next=Math.min(index+1,items.length-1);else if(event.key==='ArrowUp')next=Math.max(0,index-1);else if(event.key==='Home')next=0;else if(event.key==='End')next=items.length-1;else return;event.preventDefault();items[next]?.focus();};
    search.onkeydown=event=>{if(event.key==='ArrowDown'){event.preventDefault();list.querySelector('button')?.focus();}};
    aside.append(search,filters,count,hint,list);
    return {aside, search, filter, sort, count, list};
  }
  function page(view, title, description) {
    const root = node('section', null, {id:'dream-' + view + '-page', class:'dream-library', 'aria-label':title});
    root.hidden = true;
    const header = node('div', null, {class:'library-heading'}), intro = node('div', null);
    intro.append(node('span','Your workspace',{class:'library-eyebrow'}),node('h1',title),node('p',description));
    const actions = node('div',null,{class:'library-actions'});
    header.append(intro, actions);
    const status = node('p','',{class:'library-status',role:'status','aria-live':'polite'});
    const layout = node('div',null,{class:'library-layout'}), detail = node('div',null,{class:'library-detail'});
    const side = sidebar(view, title);
    layout.append(side.aside,detail);root.append(header,status,layout);document.querySelector('.split').append(root);
    const state = {root, actions, status, ...side, detail, loaded:false};pages[view] = state;
    return state;
  }
  function report(p, text, error = false) { p.status.textContent = text;p.status.dataset.error = String(error); }
  function safe(p, fn) { return async event => {
    event?.preventDefault();const b=event?.currentTarget;
    if (b?.dataset.busy) return;
    if(b){b.dataset.busy='true';if(b.tagName==='BUTTON')b.disabled=true;}
    try { await fn(event); } catch(e) { report(p,e.message,true); }
    finally {if(b){delete b.dataset.busy;if(b.tagName==='BUTTON')b.disabled=false;}}
  }; }
  function field(container, text, tag='input', attrs={}) {
    const label=node('label',text),input=node(tag,null,attrs);label.append(input);container.append(label);return input;
  }
  function empty(p, title, text, action) {
    const box=node('div',null,{class:'library-empty'});box.append(node('h2',title),node('p',text));if(action)box.append(action);p.detail.replaceChildren(box);
  }
  function hide() {++handoffRequest;for(const p of Object.values(pages))p.root.hidden=true;}
  function show(view) {
    const p=pages[view];if(!p)return;
    hide();for(const id of ['dream-home','dream-inspector'])if($(id))$(id).hidden=true;
    document.documentElement.classList.remove('dream-expanded');document.documentElement.dataset.dreamView=view;
    document.querySelectorAll('#dream-nav [aria-current]').forEach(n=>n.removeAttribute('aria-current'));
    $('dream-nav-'+view)?.setAttribute('aria-current','page');p.root.hidden=false;
    document.querySelectorAll('dialog[open]').forEach(d=>d.close());
    if(!p.loaded)p.refresh();
  }
  function chatDraft(text) {
    hide();$('studio-interactions')?.click();companion?.compose();
    input.value = input.value ? input.value+'\n\n'+text : text;grow();draftAttachments.save();input.focus();
  }
  window.DreamLibrary = {show,hide};

  // Skills: raw Markdown remains inert and edits stay in the form while navigating.
  const skills=page('skills','Skills','Read the full instructions, make them your own, or build a reusable skill.');
  let skillRows=[], selectedSkill=null, skillDraft=false, skillRequest=0;
  skills.actions.append(button('Refresh',safe(skills,()=>skills.refresh())),button('New skill',()=>newSkill(),{class:'primary'}));
  // DREAM-171: the Skills workspace -- the library (skills, plugins, MCP servers, tool groups), the preset they are
  // assigned to (each row's toggle, or drag a row onto the preset: assigning never moves anything), the editor.
  // DREAM-174: the preset column is the library column again (same builder, same rows), with the preset list on top,
  // a × instead of the toggle, and the cost and actions below.
  const KINDS={skills:'Skills',tools:'Tool groups',mcp:'MCP servers',plugins:'Plugins'},DRAG='application/x-dream-item';
  let presetData=null,presetName=null,extRows=[],selectedItem=null,skillNotes=[];
  const col=sidebar('skills','Preset','preset','library-preset-col'),presetHead=node('div',null,{class:'library-preset-head'}),presetFoot=node('div',null,{class:'library-preset-foot'});
  col.aside.prepend(presetHead);col.aside.append(presetFoot);
  skills.root.querySelector('.library-layout').classList.add('skills-workspace');skills.detail.before(col.aside);
  const shownPreset=()=>presetData?.presets.find(p=>p.name===presetName)||presetData?.presets[0];
  const same=(a,b)=>a.toLowerCase()===b.toLowerCase();
  const has=(p,kind,name)=>!p?.items||(p.items[kind]||[]).some(v=>same(v,name));
  const via=(p,r)=>r.kind==='skills'&&r.row.plugin&&(p?.items?.plugins||[]).some(v=>same(v,r.row.plugin))?r.row.plugin:null;   // F4
  const tokens=c=>`${c.skills} skills · ${c.tools} tools · ~${(c.tokens/1000).toFixed(1)}K tokens`;
  function libraryRows(f){
    if(f==='plugins'||f==='mcp')return extRows.filter(r=>r.kind===(f==='mcp'?'mcp':'plugin')).map(r=>({kind:f,name:r.name,key:r.id,description:r.description||r.source||'',sub:KINDS[f].slice(0,-1)+(r.enabled?'':' · Off'),row:r}));
    if(f==='tools')return (presetData?.groups||[]).map(g=>({kind:'tools',name:g.name,key:'tools:'+g.name,description:g.tools+' tools'+(g.core?' · '+g.core+' always on':''),sub:'Tool group',row:g}));
    return skillRows.filter(s=>f==='all'||(f==='managed'?s.managed:f==='installed'?!s.managed:f==='enabled'?s.enabled!==false:s.enabled===false))
      .map(s=>({kind:'skills',name:s.name,key:s.name,description:s.description||'No description',row:s,
        sub:(s.copy_of?'Your version of '+s.copy_of:({yours:'Yours',copy:'Your version',plugin:'Plugin',builtin:'Built-in'}[s.origin]||(s.managed?'Your version':s.source||'Installed skill')))
          +(s.enabled===false?' · Off':'')+(skillNotes.some(n=>n.startsWith(`'${s.name}'`))?' · plugin original; the other version is used':'')}));
  }
  function presetRows(f,p){
    const lib=libraryRows(f);
    if(!p?.items)return lib.filter(r=>r.row.enabled!==false);
    const kind=KINDS[f]?f:'skills',named=(p.items[kind]||[]).map(n=>lib.find(r=>same(r.name,n))
      ||(kind==='tools'&&(presetData.groups||[]).some(g=>g.names?.includes(n))?{kind,name:n,key:'tools:'+n,description:'One tool from the '+presetData.groups.find(g=>g.names?.includes(n)).name+' group',sub:'Tool',row:{}}:null)
      ||(f==='all'||KINDS[f]?{kind,name:n,key:kind+':'+n,description:'Not in the library',sub:'Missing',row:{},missing:true}:null)).filter(Boolean);
    return [...named,...lib.filter(r=>!named.includes(r)&&via(p,r)).map(r=>({...r,sub:r.sub+' · via plugin '+via(p,r)}))];
  }
  function row(r,action,key,drag){
    const item=node('div',null,{class:'library-row',...(drag?{draggable:'true'}:{})});
    const b=button('',safe(skills,()=>r.missing?report(skills,r.name+' is not in the library.',true):r.kind==='skills'?openSkill(r.name):openItem(r)),{'aria-current':String(r.kind==='skills'?selectedSkill?.name===r.name:selectedItem===r.key),'data-key':key});
    b.append(node('strong',r.name),node('small',r.description),node('small',r.sub));
    if(drag)item.ondragstart=e=>{e.dataTransfer.setData(DRAG,JSON.stringify({kind:r.kind,name:r.name}));e.dataTransfer.effectAllowed='copy';};
    item.append(b);if(action)item.append(action);return item;
  }
  function fill(c,all,total,action,prefix,drag,none){
    const a=document.activeElement,keep=c.list.contains(a)?(a.dataset.key?`[data-key="${CSS.escape(a.dataset.key)}"]`:a.dataset.assign?`[data-assign="${CSS.escape(a.dataset.assign)}"]`:null):null;
    const query=c.search.value.trim().toLowerCase();
    const rows=all.filter(r=>(r.name+' '+r.description).toLowerCase().includes(query)).sort((x,y)=>x.name.localeCompare(y.name)*(c.sort.value==='reverse'?-1:1));
    c.count.textContent=rows.length+' of '+total;
    c.list.replaceChildren(...rows.map(r=>row(r,action(r),prefix+r.key,drag)));
    if(!rows.length)c.list.append(node('p',query||c.filter.value!=='all'?'No matching items. Change the search or filter.':none));
    if(keep)c.list.querySelector(keep)?.focus();
  }
  function toggle(r){
    const p=shownPreset(),on=has(p,r.kind,r.name),v=!on&&via(p,r);
    const t=button(on||v?'✓':'+',safe(skills,()=>assign(r.kind,r.name,!on,r.key)),{class:'library-assign','aria-pressed':String(on||!!v),'data-assign':r.key,
      'aria-label':'In '+(p?.name||'Default')+': '+r.name+(v?' (via plugin '+v+')':''),title:!p?.items?'Default includes everything that is on':v?'In '+p.name+' via plugin '+v:(on?'In ':'Add to ')+p.name});
    t.disabled=!p?.items||!!v;return t;
  }
  function removal(r){
    const p=shownPreset();
    return p?.items&&has(p,r.kind,r.name)?button('×',safe(skills,()=>assign(r.kind,r.name,false)),{class:'library-assign','aria-label':'Remove '+r.name+' from '+p.name,title:'Remove from '+p.name}):null;
  }
  function drawSkills() {
    const f=skills.filter.value,all=libraryRows(f),p=shownPreset(),pf=col.filter.value,mine=presetRows(pf,p);
    fill(skills,all,KINDS[f]?all.length+' '+KINDS[f].toLowerCase():skillRows.length+' skills',toggle,'',true,'No skills saved yet.');
    fill(col,mine,mine.length+' '+(KINDS[pf]||'skills').toLowerCase(),removal,'preset:',false,p?.items?'Nothing assigned yet. Drag a row here, or use its + button.':'Nothing is on.');
  }
  for(const c of [skills,col]){c.search.oninput=drawSkills;c.filter.onchange=drawSkills;c.sort.onchange=drawSkills;}
  async function presetOp(body){
    try{presetData=await api('/api/presets',{...body,sha256:presetData.sha256});window.dispatchEvent(new CustomEvent('dream:presets',{detail:'skills'}));}
    catch(e){presetData=await api('/api/presets');throw e;}
    finally{drawSkills();drawPreset();}
  }
  async function assign(kind,name,on,key){
    const p=shownPreset();await presetOp({op:'assign',preset:p.name,kind,item:name,on});
    report(skills,(on?'Added '+name+' to ':'Removed '+name+' from ')+p.name+'. It applies to sessions started with '+p.name+'.');
    if(key)skills.list.querySelector(`[data-assign="${CSS.escape(key)}"]`)?.focus();
  }
  function drawPreset(){
    const p=shownPreset();if(!p)return;presetName=p.name;
    const sel=node('select',null,{'aria-label':'Preset'}),label=node('label','Preset');label.append(sel);
    for(const x of presetData.presets)sel.append(node('option',x.name+(x.name===presetData.active?' · next session':''),{value:x.name}));
    sel.append(node('option','Add new…',{value:''}));sel.value=p.name;
    sel.onchange=()=>{if(!sel.value){sel.value=p.name;presetForm();return;}presetName=sel.value;drawSkills();drawPreset();};
    const origin={default:'Default filters nothing and takes no drops',builtin:'Built-in',edited:'Built-in · edited by you',user:'Yours'}[p.origin];
    presetHead.replaceChildren(label,node('p',origin+(p.name===presetData.active?' · active for new sessions':'')+' · this session: '+presetData.session,{class:'library-meta'}));
    const bar=node('div',null,{class:'library-actions'}),active=p.name===presetData.active;
    const use=button(active?'Active for new sessions':'Use for next session',safe(skills,async()=>{await presetOp({op:'use',preset:p.name});report(skills,'New sessions start with '+p.name+'. This session keeps '+presetData.session+'.');}),{class:'primary'});
    use.disabled=active;bar.append(use);
    if(p.origin==='user')bar.append(button('Rename',()=>presetForm(p.name)),button('Delete',safe(skills,async()=>{if(!confirm('Delete the preset "'+p.name+'"? Its skills stay in the library.'))return;await presetOp({op:'delete',preset:p.name});report(skills,'Preset '+p.name+' deleted.');})));
    if(p.origin==='edited')bar.append(button('Reset',safe(skills,async()=>{if(!confirm('Reset "'+p.name+'" to how it shipped? Your changes to it are dropped.'))return;await presetOp({op:'reset',preset:p.name});report(skills,p.name+' is back to how it shipped.');})));
    presetFoot.replaceChildren(node('p',tokens(p.cost),{class:'library-cost'}),bar);
  }
  function presetForm(from){
    const form=node('form',null,{class:'library-preset-form'}),input=field(form,from?'Rename preset':'New preset name','input',{required:'',maxlength:'60'});input.value=from||'';
    const bar=node('div',null,{class:'library-actions'});bar.append(node('button',from?'Rename':'Create preset',{type:'submit',class:'primary'}),button('Cancel',drawPreset));form.append(bar);
    form.onsubmit=safe(skills,async()=>{const name=input.value.trim();await presetOp(from?{op:'rename',preset:from,to:name}:{op:'create',name});presetName=name;drawSkills();drawPreset();report(skills,from?'Preset renamed to '+name+'.':'Preset '+name+' created. Add items with + or by dragging them here.');});
    presetHead.replaceChildren(form);input.focus();
  }
  col.aside.ondragover=e=>{if(shownPreset()?.items&&e.dataTransfer.types.includes(DRAG)){e.preventDefault();e.dataTransfer.dropEffect='copy';col.aside.classList.add('drop');}};
  col.aside.ondragleave=()=>col.aside.classList.remove('drop');
  col.aside.ondrop=safe(skills,async e=>{col.aside.classList.remove('drop');const d=JSON.parse(e.dataTransfer.getData(DRAG)||'null');if(d&&shownPreset()?.items)await assign(d.kind,d.name,true);});
  function openItem(r){
    if(!mayChangeSkill())return;
    ++skillRequest;selectedSkill=null;selectedItem=r.key;drawSkills();
    const box=node('div',null,{class:'library-editor'}),inAll=presetData.presets.filter(p=>p.items&&has(p,r.kind,r.name)).map(p=>p.name);
    box.append(node('h2',r.name),node('p',r.description,{class:'library-meta'}),node('p',r.sub+(r.kind==='plugins'?' · the plugin owns its skills and tools':''),{class:'library-meta'}),
      node('p','In presets: '+(inAll.join(', ')||'none')+'. Default includes it while it is on.'));
    if(r.kind!=='tools')box.append(button(r.row.enabled?'Turn off':'Turn on',safe(skills,async()=>{await api('/api/control',{action:'extension',id:r.row.id,enabled:!r.row.enabled});await skills.refresh();const again=libraryRows(skills.filter.value).find(x=>x.key===r.key);if(again)openItem(again);}),{'aria-pressed':String(!!r.row.enabled)}));
    skills.detail.replaceChildren(box);
  }
  // The composer's preset select changes the same file: follow it (one source of truth, /api/presets).
  window.addEventListener('dream:presets',async e=>{if(e.detail==='skills'||!skills.loaded)return;try{presetData=await api('/api/presets');drawSkills();drawPreset();}catch(err){report(skills,err.message,true);}});
  skills.refresh=safe(skills,async()=>{
    const [data,ext,presetsNow]=await Promise.all([api('/api/skills'),api('/api/extensions').catch(e=>({extensions:[],error:e.message})),api('/api/presets')]);
    skillRows=data.skills||[];skillNotes=data.notes||[];extRows=(ext.extensions||[]).filter(r=>r.kind==='plugin'||r.kind==='mcp');presetData=presetsNow;skills.loaded=true;drawSkills();drawPreset();
    const notes=presetData.notes||[],problem=data.warnings?.length?'Library warnings: '+data.warnings.join(' · '):presetData.error||(ext.error&&'Plugins and MCP servers could not be read: '+ext.error);
    report(skills,problem||'Skill library refreshed.'+(notes.length?' Note: '+notes.join(' · '):''),!!problem);
  });
  function mayChangeSkill() {
    if(!skillDraft)return true;
    report(skills,'Save this draft or choose Discard changes before opening another skill.',true);return false;
  }
  async function openSkill(name) {
    if(!mayChangeSkill())return;
    const request=++skillRequest;
    report(skills,'Reading full skill…');
    const data=await api('/api/skills/'+encodeURIComponent(name));
    if(request!==skillRequest)return;
    if(!mayChangeSkill())return;
    selectedSkill=data;selectedItem=null;drawSkills();editSkill(data);report(skills,'Full instructions loaded.');
  }
  function newSkill() {
    if(!mayChangeSkill())return;
    ++skillRequest;selectedSkill=null;drawSkills();
    editSkill({name:'',content:'---\nname: my-skill\ndescription: Describe when Dream should use this skill.\n---\n\n# My skill\n\n## When to use\n\nDescribe the task this skill helps with.\n\n## Instructions\n\n1. Inspect the relevant input.\n2. Complete the task.\n3. Verify the result and explain any limitations.\n',managed:true},true);
    report(skills,'Choose a unique skill name and edit its Markdown instructions.');
  }
  function editSkill(data, creating=false) {
    skillDraft=false;
    const form=node('form',null,{class:'library-editor'});
    form.append(node('h2',creating?'Build a skill':data.name));
    const name=field(form,'Skill name','input',{required:'',maxlength:'80',pattern:'[a-z0-9]+(?:-[a-z0-9]+)*',placeholder:'my-skill'});name.value=data.name||'';name.readOnly=!creating;
    form.append(node('p',creating?'Saved privately in your Dream skill library.':(data.managed?'Editing your saved version.':'Saving creates your own version; the installed original stays intact.')+' '+(data.path||data.source||''),{class:'library-meta'}));
    const content=field(form,'Full SKILL.md · Markdown','textarea',{required:'',maxlength:'120000',class:'skill-markdown',spellcheck:'false','aria-label':'Skill Markdown'});content.value=data.content||'';
    const notice=node('p','Changes become available to Dream through its skill catalog. Supporting files remain part of the skill package.',{class:'library-meta'});form.append(notice);
    const bar=node('div',null,{class:'library-savebar'}),saved=node('span','No unsaved changes',{role:'status'});
    const save=node('button','Save skill',{type:'submit',class:'primary'});
    bar.append(save,button('Discard changes',()=>{skillDraft=false;if(creating)newSkill();else editSkill(data);report(skills,'Draft discarded.');}),saved);
    if(!creating){const use=button('Use in chat',()=>chatDraft('Use the '+data.name+' skill.'));use.disabled=data.enabled===false;bar.append(use);}
    // DREAM-171/172: yours can be deleted (to the skills trash), your version of an installed or plugin skill removed
    // (the original returns); a plugin's skill opens read-only with Edit a copy -- the plugin's files are never written.
    const mine=data.origin==='yours'||data.origin==='copy';
    if(!creating&&mine)bar.append(button(data.origin==='yours'?'Delete':'Remove your version',safe(skills,async()=>{
      if(!confirm(data.origin==='yours'?'Delete "'+data.name+'"? It moves to the skills trash.':'Remove your version of "'+data.name+'"? The installed original returns.'))return;
      const r=await api('/api/skills/'+encodeURIComponent(data.name)+'/remove',{expected_sha256:data.sha256});
      skillDraft=false;selectedSkill=null;skillsEmpty();await skills.refresh();report(skills,r.restored?'Your version was removed; the installed original is back.':'Skill moved to the trash.');})));
    if(data.origin==='plugin'){content.readOnly=true;save.hidden=true;notice.textContent='A plugin skill: the plugin owns its files. Edit a copy to make your own version; Remove your version later brings the plugin\'s back.';
      const copy=button('Edit a copy',()=>{content.readOnly=false;save.hidden=false;copy.remove();content.focus();report(skills,'Saving creates your version; the plugin\'s files stay as they are.');});
      bar.append(copy,button(data.enabled===false?'Turn on':'Turn off',safe(skills,async()=>{await api('/api/control',{action:'extension',id:'skill:'+data.name,enabled:data.enabled===false});await skills.refresh();await openSkill(data.name);})));}
    form.append(bar);skills.detail.replaceChildren(form);
    form.oninput=()=>{skillDraft=true;saved.textContent='Unsaved changes';};
    name.oninput=()=>{
      if(creating)content.value=content.value.replace(/^(name:\s*).*$/m,(_,prefix)=>prefix+name.value);
    };
    form.onsubmit=safe(skills,async()=>{
      save.disabled=true;
      const snapshot=content.value,skillName=name.value;
      try {
        const result=await api(creating?'/api/skills':'/api/skills/'+encodeURIComponent(data.name),
          {name:skillName,content:snapshot,...(creating?{}:{expected_sha256:data.sha256})});
        if(!form.isConnected){await skills.refresh();return;}
        // Never discard typing made while the save was in flight.
        data={...data,...result,name:result.name||skillName,content:snapshot};
        selectedSkill=data;
        if(content.value===snapshot&&name.value===skillName){skillDraft=false;editSkill(data);}
        else{skillDraft=true;saved.textContent='Saved earlier version · newer edits unsaved';creating=false;name.readOnly=true;}
        await skills.refresh();report(skills,skillDraft?'Saved. Your newer edits are still unsaved.':'Skill saved in your private library.');
      }finally{save.disabled=false;}
    });
  }
  function skillsEmpty(){empty(skills,'Instructions you can shape','Choose a skill to read all of it, or create one for a workflow you repeat.',button('Create a skill',newSkill,{class:'primary'}));}
  skillsEmpty();

  const projects=page('projects','Projects','A place for each body of work. Return to its files, instructions, and conversations.');
  let projectRows=[],selectedProject=null,projectDraft=false,projectRequest=0,conversationRequest=0,documentRequest=0,handoffRequest=0;
  projects.actions.append(button('Refresh',safe(projects,()=>projects.refresh())),button('New project',()=>newProject(),{class:'primary'}));
  function drawProjects() {
    const focused=projects.list.contains(document.activeElement)?document.activeElement.dataset.key:null;
    const query=projects.search.value.trim().toLowerCase();projects.list.replaceChildren();
    const rows=projectRows.filter(p=>(p.name+' '+p.workspace).toLowerCase().includes(query)).filter(p=>projects.filter.value==='all'||(projects.filter.value==='current'?p.workspace===window.DREAM_SESSION?.workspace:(p.session_count||0)>0));
    rows.sort((a,b)=>(projects.sort.value==='conversations'?(b.session_count||0)-(a.session_count||0):0)||a.name.localeCompare(b.name)*(projects.sort.value==='reverse'?-1:1));
    projects.count.textContent=rows.length+' of '+projectRows.length+' projects';
    for(const p of rows){
      const b=button('',safe(projects,()=>openProject(p.id)),{'aria-current':String(selectedProject?.id===p.id),'data-key':p.id});
      b.append(node('strong',p.name),node('small',p.workspace),node('small',(p.session_count??0)+' conversations'+(p.workspace===window.DREAM_SESSION?.workspace?' · Current workspace':'')));projects.list.append(b);
    }
    if(!projects.list.children.length)projects.list.append(node('p',query||projects.filter.value!=='all'?'No matching projects. Change the search or filter.':'Your saved projects will appear here.'));
    [...projects.list.querySelectorAll('button')].find(b=>b.dataset.key===focused)?.focus();
  }
  projects.search.oninput=drawProjects;projects.filter.onchange=drawProjects;projects.sort.onchange=drawProjects;
  projects.refresh=safe(projects,async()=>{const data=await api('/api/projects');projectRows=data.projects||[];projects.loaded=true;drawProjects();report(projects,'Project library refreshed.');});
  function mayChangeProject() {
    if(!projectDraft)return true;
    report(projects,'Save this project draft or choose Discard changes before opening another project.',true);return false;
  }
  async function openProject(id) {
    if(!mayChangeProject())return;
    ++conversationRequest;++documentRequest;++handoffRequest;
    const request=++projectRequest;report(projects,'Reading saved project…');
    const data=await api('/api/projects/'+encodeURIComponent(id));if(request!==projectRequest)return;
    if(!mayChangeProject())return;
    selectedProject=data.project;drawProjects();projectDetail(data);report(projects,'Project loaded. Opening a conversation does not run its tools.');
  }
  function newProject() {
    if(!mayChangeProject())return;
    ++projectRequest;++conversationRequest;++documentRequest;++handoffRequest;selectedProject=null;drawProjects();
    projectForm({name:'',workspace:window.DREAM_SESSION?.workspace||'',instructions:''},true);
  }
  function projectForm(data, creating=false) {
    ++conversationRequest;++documentRequest;++handoffRequest;projectDraft=false;
    const form=node('form',null,{class:'library-editor'});form.append(node('h2',creating?'Create a project':'Project settings'));
    const name=field(form,'Project name','input',{required:'',maxlength:'120',placeholder:'What are you working on?'});name.value=data.name;
    const path=field(form,'Workspace folder','input',{required:'',placeholder:'/path/to/your/project'});path.value=data.workspace;path.readOnly=!creating;
    form.append(node('p','Use the folder that contains this project. Dream will keep shell execution and project files in the same workspace.',{class:'library-meta'}));
    const instructions=field(form,'Project instructions','textarea',{maxlength:'12000',rows:'8',placeholder:'What should Dream know about this project? Goals, preferences, and constraints…'});instructions.value=data.instructions||'';
    const bar=node('div',null,{class:'library-savebar'}),state=node('span','No unsaved changes',{role:'status'}),save=node('button','Save project',{type:'submit',class:'primary'});
    bar.append(save,button('Discard changes',()=>{projectDraft=false;if(creating)newProject();else openProject(data.id).catch(e=>report(projects,e.message,true));}),state);form.append(bar);projects.detail.replaceChildren(form);
    form.oninput=()=>{projectDraft=true;state.textContent='Unsaved changes';};
    form.onsubmit=safe(projects,async()=>{
      save.disabled=true;const payload={name:name.value,workspace:path.value,instructions:instructions.value,...(creating?{}:{expected_revision:data.revision})};
      try{
        const result=await api(creating?'/api/projects':'/api/projects/'+encodeURIComponent(data.id),payload);
        const saved=result.project||result;
        if(!form.isConnected){await projects.refresh();return;}
        if(name.value===payload.name&&path.value===payload.workspace&&instructions.value===payload.instructions){projectDraft=false;await projects.refresh();if(!form.isConnected)return;await openProject(saved.id);}
        else{data=saved;creating=false;path.readOnly=true;state.textContent='Saved earlier version · newer edits unsaved';await projects.refresh();}
        report(projects,projectDraft?'Saved. Your newer edits are still unsaved.':'Project saved. Open it to start working.');
      }finally{save.disabled=false;}
    });
  }
  async function activateProject(project, sessionId) {
    if(input.value.trim() || document.querySelector('#attachments .attachment, .attachment-chip'))throw Error('Your chat has an unsent draft or attachment. Return to Chat and send or clear it before changing conversations.');
    if(document.querySelector('.permission-card'))throw Error('Resolve the pending approval before changing projects.');
    if(window.DREAM_PROJECT_SWITCHING)throw Error('A project is already opening.');
    const footer=document.querySelector('footer'),wasInert=footer.inert,wasReadOnly=input.readOnly;
    const notice=node('span','Opening project…',{id:'dream-project-opening',role:'status'});
    ($('dream-workbar')||document.querySelector('header')).append(notice);
    window.DREAM_PROJECT_SWITCHING=true;footer.inert=true;input.readOnly=true;
    report(projects,'Opening project…');
    try {
      const data=await api('/api/control',{action:'project_open',project_id:project.id,...(sessionId?{session_id:sessionId}:{})});
      const result=data.result||data;
      if(result.project_id!==project.id || !result.session?.session_id || result.session.workspace!==project.workspace)throw Error('Dream did not confirm the selected project. Check the current workspace before retrying.');
      companion?.session(result.session);
      hide();$('studio-interactions')?.click();companion?.compose();
    } finally {
      window.DREAM_PROJECT_SWITCHING=false;footer.inert=wasInert;input.readOnly=wasReadOnly;notice.remove();
    }
    input.focus();
  }
  function projectDetail(data) {
    projectDraft=false;const p=data.project,box=node('section',null,{class:'library-editor'});
    box.append(node('h2',p.name),node('p',p.workspace,{class:'library-meta'}));
    const actions=node('div',null,{class:'library-actions'});
    actions.append(button('New chat in project',safe(projects,()=>activateProject(p)),{class:'primary'}),button('Edit project',()=>{if(mayChangeProject())projectForm(p);}));
    box.append(actions,node('p',p.instructions?'Project instructions saved.':'Add instructions to give new conversations a shared starting point.',{class:'library-meta'}));
    const tabs=node('div',null,{class:'library-tabs',role:'tablist','aria-label':'Project details'}),panels={};
    for(const [key,label] of [['conversations','Conversations'],['documents','Documents'],['memory','Memory'],['files','Files & context'],['instructions','Instructions']]){
      const panel=node('section',null,{class:'library-subpanel',id:'project-'+key,role:'tabpanel','aria-label':label});panels[key]=panel;panel.hidden=key!=='conversations';
      const b=button(label,()=>{++handoffRequest;for(const [name,part]of Object.entries(panels))part.hidden=name!==key;tabs.querySelectorAll('button').forEach(n=>n.setAttribute('aria-selected',String(n===b)));},{role:'tab','aria-controls':'project-'+key,'aria-selected':String(key==='conversations')});tabs.append(b);
    }
    box.append(tabs,...Object.values(panels));
    const linker=node('div',null);
    panels.conversations.append(button('Link past conversation',safe(projects,()=>linkConversation(p,linker))),linker);
    for(const s of data.sessions||[]){
      const row=node('div',null,{class:'library-session'}),info=node('div',null);
      info.append(node('strong',s.title||s.id),node('p',[s.started_at||s.created_at,s.turn_count===undefined?'':s.turn_count+' turns'].filter(Boolean).join(' · ')));
      row.append(info,button('Open conversation',safe(projects,()=>conversation(p,s,panels.conversations,openHandoff))));panels.conversations.append(row);
    }
    if(!(data.sessions||[]).length)panels.conversations.append(node('p','No conversations yet. Start a new chat in this project; it will appear here for your next visit.'));
    panels.instructions.append(node('pre',p.instructions||'No project instructions saved.',{class:'library-message'}));
    panels.instructions.style.whiteSpace='pre-wrap';panels.instructions.style.overflowWrap='anywhere';
    if(p.workspace===window.DREAM_SESSION?.workspace){
      ProjectPanel.mount(panels.files,{token:TOKEN,onHandoff:chatDraft,onResumePreview:run=>chatDraft('/loop-resume '+run.run_id)});
    }else panels.files.append(node('p','Open this project to browse its workspace files, pin context, and inspect saved runs.'));
    const draftDocument=documentPanel(p,panels.documents);
    function openHandoff(draft,notice) {
      tabs.querySelector('[aria-controls="project-documents"]').click();
      draftDocument(draft);report(projects,notice||'Review this partial draft before saving.');
    }
    memoryPanel(p,panels.memory);
    projects.detail.replaceChildren(box);
  }
  async function conversation(p,s,container,openHandoff) {
    ++handoffRequest;
    const expected=selectedProject?.id,request=++conversationRequest;
    const data=await api('/api/projects/'+encodeURIComponent(p.id)+'/sessions/'+encodeURIComponent(s.id));
    if(selectedProject?.id!==expected || request!==conversationRequest)return;
    container.replaceChildren(node('h3',s.title||s.id));
    container.append(node('p',data.recovery?.summary||'Saved chat outcome is unknown. Inspect existing results before continuing.',{class:'library-notice',role:'status','data-chat-recovery':''}));
    container.append(button('Continue conversation',safe(projects,()=>activateProject(p,s.id)),{class:'primary'}),node('p','Continue restores saved context into a new model session. Previously executed tools are not replayed.',{class:'library-notice'}));
    container.append(button('Draft project handoff',safe(projects,async()=>{
      if(!mayChangeProject())return;
      const handoff=++handoffRequest,sourceRequest=conversationRequest;
      const current=()=>handoff===handoffRequest && sourceRequest===conversationRequest && selectedProject?.id===p.id && container.isConnected && !container.hidden && !projects.root.hidden;
      report(projects,'Reading saved evidence for a handoff…');
      try {
        const result=await api('/api/projects/'+encodeURIComponent(p.id)+'/sessions/'+encodeURIComponent(s.id)+'/handoff');
        if(!current() || !mayChangeProject())return;
        if(result.source?.project_id!==p.id || result.source?.session_id!==s.id || typeof result.document?.content!=='string')throw Error('Handoff source did not match this conversation. Try again.');
        openHandoff({title:result.document.title,content:result.document.content,kind:'memory',include:false},result.notice);
      } catch(e) {if(current())throw e;}
    })));
    if(data.summary)container.append(node('p',data.summary));
    const messages=node('div',null,{class:'library-conversation'});
    for(const turn of data.turns||[]){if(turn.role==='turn_status')continue;const row=node('article',null,{class:'library-message'});row.append(node('strong',turn.role==='assistant_partial'?'Partial assistant reply':turn.role),node('pre',turn.content||''));messages.append(row);}
    if(data.partial)messages.prepend(node('p','Showing a bounded excerpt of this conversation.'));
    if(!messages.children.length)messages.append(node('p','No saved messages are available.'));
    container.append(messages);
  }

  async function linkConversation(project,container) {
    const data=await api('/api/projects/unassigned-sessions');
    container.replaceChildren(node('p',data.scope||'Older conversations have no reliable workspace label. Link only a conversation that belongs to this project.',{class:'library-notice'}));
    const form=node('form',null),select=field(form,'Past conversation','select',{required:''});
    select.append(node('option','Choose a conversation',{value:''}));
    for(const s of data.sessions||[])select.append(node('option',(s.title||s.id)+' · '+(s.started_at||s.id),{value:s.id}));
    if(!(data.sessions||[]).length){container.append(node('p','No unassigned conversations found in the recent archive.'));return;}
    form.append(node('p','Linking makes this conversation and its saved summary visible in this project. It does not run the conversation again.'));
    const save=node('button','Link to this project',{type:'submit'});form.append(save);container.append(form);
    form.onsubmit=safe(projects,async()=>{
      if(!mayChangeProject())return;
      await api('/api/projects/'+encodeURIComponent(project.id)+'/sessions',{session_id:select.value,confirmed_project:true});
      await projects.refresh();await openProject(project.id);report(projects,'Conversation linked to this project.');
    });
  }
  function documentPanel(project, container) {
    const actions=node('div',null,{class:'library-actions'}),list=node('div',null),editor=node('div',null);
    actions.append(button('New document',()=>{if(mayChangeProject())editDocument(project,editor,{},reload);}),button('New handoff',()=>{
      if(mayChangeProject())editDocument(project,editor,{title:'Project handoff',kind:'memory',include:false,content:'## Goal\n\n\n## Current artifacts\n\n\n## Checks performed\n\nRecord checks actually performed and their observed results.\n\n## Known defects\n\n\n## Next action\n\n\n## Source\n\nRecord source sessions, files, or observations.\n'},reload);
    }),button('Refresh documents',safe(projects,()=>reload())));
    container.append(node('p','Your private Markdown documents and memory notes. Select a note for project context to recall it in future messages.'),actions,list,editor);
    async function reload() {
      const data=await api('/api/projects/'+encodeURIComponent(project.id)+'/documents');
      list.replaceChildren();
      for(const d of data.documents||[]){const row=node('div',null,{class:'library-session'});row.append(node('span',d.title+' · '+d.kind+(d.include?' · In project context':'')),button('Edit document',safe(projects,async()=>{
        if(!mayChangeProject())return;
        const request=++documentRequest;
        const full=await api('/api/projects/'+encodeURIComponent(project.id)+'/documents/'+encodeURIComponent(d.id));
        if(request!==documentRequest || !container.isConnected || !mayChangeProject())return;
        editDocument(project,editor,full,reload);
      })));list.append(row);}
      if(!list.children.length)list.append(node('p','No documents yet. Add a reference, decision log, or project memory note.'));
    }
    safe(projects,reload)();
    return draft=>editDocument(project,editor,draft,reload);
  }
  function editDocument(project,container,initial,reload) {
    ++documentRequest;++handoffRequest;
    projectDraft=!initial.id && !!(initial.title || initial.content);
    // One project editor across the two tabs: a clean hidden form cannot later
    // reset another form's dirty state. Callers guard dirty edits before entry.
    projects.detail.querySelectorAll('[data-document-editor]').forEach(form=>form.remove());
    let data=initial;
    const form=node('form',null,{class:'library-editor','data-document-editor':''});
    const title=field(form,'Document title','input',{required:'',maxlength:'160'});title.value=data.title||'';
    const kind=field(form,'Document type','select');kind.append(node('option','Reference',{value:'reference'}),node('option','Project memory',{value:'memory'}));kind.value=data.kind||'reference';
    const body=field(form,'Document Markdown','textarea',{maxlength:'120000',rows:'12'});body.value=data.content||'';
    const include=field(form,'Include in project context','input',{type:'checkbox'});include.checked=data.include===true;include.style.width='auto';
    form.append(node('p','Selected notes are included in project messages, up to 6,000 characters total. Other documents remain available here without filling the model context.',{class:'library-meta'}));
    const state=node('p',projectDraft?'Unsaved changes':'No unsaved changes',{role:'status'}),bar=node('div',null,{class:'library-savebar'}),save=node('button','Save document',{type:'submit',class:'primary'});
    bar.append(save,button('Close editor',()=>{if(mayChangeProject())container.replaceChildren();}),button('Discard changes',()=>{projectDraft=false;container.replaceChildren();}));form.append(state,bar);container.replaceChildren(form);
    form.oninput=()=>{projectDraft=true;state.textContent='Unsaved changes';};
    form.onsubmit=safe(projects,async()=>{
      save.disabled=true;const payload={title:title.value,kind:kind.value,content:body.value,include:include.checked,...(data.id?{expected_sha256:data.sha256}:{})};
      try{
        const result=await api('/api/projects/'+encodeURIComponent(project.id)+'/documents'+(data.id?'/'+encodeURIComponent(data.id):''),payload);data=result;
        if(!form.isConnected){await reload();return;}
        const unchanged=title.value===payload.title&&kind.value===payload.kind&&body.value===payload.content&&include.checked===payload.include;
        projectDraft=!unchanged;state.textContent=unchanged?'Document saved':'Saved earlier version · newer edits unsaved';await reload();report(projects,'Private project document saved.');
      }finally{save.disabled=false;}
    });
  }
  function memoryPanel(project, container) {
    container.append(node('p','Saved session summaries and memories linked to this project. Original consolidation records are preserved; copy a lesson into a project note to revise or select it for recall.'));
    const list=node('div',null),editor=node('div',null);container.append(list,editor);
    container.append(button('New project memory',()=>{if(mayChangeProject())editDocument(project,editor,{kind:'memory'},async()=>{});}));
    safe(projects,async()=>{
      const data=await api('/api/projects/'+encodeURIComponent(project.id)+'/memory');
      const rows=[...(data.summaries||[]).map(s=>({title:s.title||'Session summary',body:s.summary,source_session:s.session_id||s.id})),...(data.memories||[])];
      for(const m of rows){const item=node('details',null,{class:'library-message'});item.append(node('summary',m.title||'Saved memory'),node('p','Source session: '+(m.source_session||'Unreported')));
        const text=node('pre',m.body||'');item.append(text,button('Copy to project memory',()=>{if(mayChangeProject())editDocument(project,editor,{title:m.title||'Session lesson',content:(m.body||'')+'\n\nSource session: '+(m.source_session||'Unreported'),kind:'memory'},async()=>{});}));list.append(item);}
      if(!rows.length)list.append(node('p','No consolidated summaries or linked memories found for this project. Ordinary conversations are still saved in Conversations.'));
      if(data.provenance)list.append(node('p',data.provenance,{class:'library-meta'}));
    })();
  }
  empty(projects,'Pick up where you left off','Save a workspace as a project. Its conversations and instructions stay together across visits.',button('Create a project',newProject,{class:'primary'}));
  window.addEventListener('dream:session',()=>{
    ++handoffRequest;drawProjects();projects.loaded=false;
    const files=projects.detail.querySelector('#project-files');
    if(files){
      if(selectedProject?.workspace!==window.DREAM_SESSION?.workspace)files.replaceChildren(node('p','Open this project to browse its workspace files and context.'));
      else if(!files.querySelector('.project-panel')){files.replaceChildren();ProjectPanel.mount(files,{token:TOKEN,onHandoff:chatDraft,onResumePreview:run=>chatDraft('/loop-resume '+run.run_id)});}
    }
  });
  window.addEventListener('dream:project-reset',()=>{hide();document.documentElement.dataset.dreamView='chat';});
  window.addEventListener('beforeunload',e=>{if(skillDraft||projectDraft||memoryDirty){e.preventDefault();e.returnValue='';}});
  // Memory (owner request): read, edit, delete and consolidate what Dream remembers --
  // global memories and per-project notebooks. Saving writes the file; nothing runs.
  // DREAM-183: Lucid Control -- the files that shape what Dream knows and how it answers, in one place: Global and
  // Current Project columns with the response styles (the board), and the memories list. Opening a file or a memory
  // swaps the board for its editor; "Back to Lucid Control" returns.
  const memory=page('memory','Lucid Control','What Dream knows and how it answers: your instructions, this project\'s files, the response style and every memory, in one place.');
  memory.root.classList.add('lucid');
  memory.aside.prepend(node('h2','Memories',{class:'lucid-heading'}));
  let lucidReturn=null;   // the card a file or memory was opened from: focus goes back there
  let lucidView=0;        // bumped on every navigation: a late answer for an earlier view is dropped (Codex re-review #7)
  memory.search.placeholder='Search memories';memory.search.setAttribute('aria-label','Search memories');
  let memoryRows=[], openMemory=null, memoryDirty=false;
  const picked=new Set();
  memory.actions.append(button('Refresh',safe(memory,()=>memory.refresh())));
  memory.aside.append(button('Consolidate selected',()=>consolidateMemories(),{class:'primary lucid-consolidate'}));
  // DREAM-185: the memories grouped by the project they belong to -- this project first, then the ones Dream uses
  // everywhere, then other projects by name, then unassigned -- newest first (or by name) inside each group.
  function drawMemory(){
    const query=memory.search.value.trim().toLowerCase();memory.list.replaceChildren();
    const show=memory.filter.value;
    const rows=memoryRows.filter(m=>(m.title+' '+m.description+' '+m.name+' '+(m.project_label||'')).toLowerCase().includes(query))
      .filter(m=>show==='all'||(show==='current'?m.current:show==='everywhere'?m.project==='user':show==='project'?m.scope==='project':true));
    rows.sort((a,b)=>memory.sort.value==='name'?a.title.localeCompare(b.title):String(b.updated).localeCompare(String(a.updated)));
    memory.count.textContent=rows.length+' of '+memoryRows.length+' memories';
    const rank=m=>m.current?0:m.project==='user'?1:m.project==='unassigned'?3:2;
    const groups=new Map();
    for(const m of [...rows].sort((a,b)=>rank(a)-rank(b)||(rank(a)===2?String(a.project_label).localeCompare(String(b.project_label)):0))){
      const name=m.current?'This project · '+(m.project_label||''):m.project==='user'?'Everywhere':m.project==='unassigned'?'Unassigned':(m.project_label||m.project);
      const id=m.current?'':m.project;        // keyed by project, so two same-named folders stay apart
      if(!groups.has(id))groups.set(id,{name,items:[]});groups.get(id).items.push(m);
    }
    for(const {name,items} of groups.values()){
      memory.list.append(node('h3',name+' ('+items.length+')',{class:'lucid-group'}));
      for(const m of items){
        const key=m.scope+'/'+m.name, row=node('div',null,{class:'memory-row'});
        const tick=node('input',null,{type:'checkbox','aria-label':'Select '+m.title});tick.checked=picked.has(key);
        tick.onchange=()=>{tick.checked?picked.add(key):picked.delete(key);};
        const b=button('',safe(memory,()=>openMem(m)),{'aria-current':String(openMemory?.key===key),'data-key':key});
        const when=String(m.updated||'').replace('T',' ');
        b.append(node('strong',m.title),node('small',(m.scope==='project'?'Project notebook':(m.kind||'memory'))+' · updated '+when+(m.created?' · created '+m.created:'')));
        row.append(tick,b);memory.list.append(row);
      }
    }
    if(!memory.list.children.length)memory.list.append(node('p',query?'No matching memories.':'Dream has not saved any memories yet.'));
  }
  memory.search.oninput=drawMemory;memory.filter.onchange=drawMemory;memory.sort.onchange=drawMemory;
  memory.refresh=safe(memory,async()=>{const data=await api('/api/memory');memoryRows=data.items||[];memory.loaded=true;drawMemory();
    if(!openMemory)await drawLucidBoard();report(memory,'Memory folder: '+(data.memory_dir||''));});
  async function drawLucidBoard(){
    const view=lucidView;const data=await api('/api/lucid');
    if(view!==lucidView||openMemory)return;   // the owner moved on (an editor is open): never replace it
    const board=node('div',null,{class:'lucid-board'});
    const column=(title,note,files)=>{const col=node('section',null,{class:'lucid-column dream-panel','aria-label':title});
      col.append(node('h2',title),node('p',note,{class:'lucid-note'}));
      for(const f of files){const card=button('',safe(memory,()=>openLucidFile(f)),{class:'lucid-file dream-card','data-key':f.scope+'/'+f.key});
        card.append(node('strong',f.label),node('span',f.problem||f.about),node('small',f.problem?'Unavailable':(f.exists?(f.size+' bytes'):'Not created yet')+(f.editable?'':' · read-only')));
        if(f.problem)card.disabled=true;
        col.append(card);}
      if(!files.length)col.append(node('p','Start Dream in a project folder to see its files here.',{class:'lucid-note'}));
      return col;};
    board.append(column('Global','Read in every session, whatever the project.',data.global),
                 column('Current project',data.workspace||'No project open',data.project));
    const styles=node('section',null,{class:'lucid-styles dream-panel','aria-label':'Response style'});
    styles.append(node('h2','Response style'),node('p','How Dream words its replies. A change applies to the next session.',{class:'lucid-note'}));
    const shown=node('div',null,{class:'lucid-style-text',role:'region','aria-live':'polite'});
    const grid=node('div',null,{class:'lucid-style-grid'});
    for(const st of data.styles){const card=button('',()=>{grid.querySelectorAll('[aria-pressed]').forEach(n=>n.setAttribute('aria-pressed','false'));
        card.setAttribute('aria-pressed','true');shown.replaceChildren(node('h3',st.label),node('p',st.summary),...(st.text?[node('pre',st.text)]:[]),
          st.active?node('p','In use for new sessions.',{class:'lucid-note'}):button('Use for new sessions',safe(memory,async()=>{
            // saved where the style in use comes from: a project value wins over the global one (Codex re-review #8)
            const scope=data.style_source==='project'?'project':'global';
            await api('/api/control',{action:'settings_save',key:'behaviour.response_style',value:st.key,scope});
            report(memory,st.label+' will be used from the next session (/new starts one)'+(scope==='project'?' in this project.':'.'));await drawLucidBoard();}),{class:'primary'}));},
        {class:'lucid-style dream-card','aria-pressed':'false','data-style':st.key});
      card.append(node('strong',st.label),node('small',st.active?'In use':st.summary));if(st.active)card.classList.add('lucid-active');grid.append(card);}
    styles.append(grid,shown);board.append(styles);memory.detail.replaceChildren(board);
  }
  async function openLucidFile(f){
    if(memoryDirty&&!confirm('Discard your unsaved changes?'))return;
    memoryDirty=false;const view=++lucidView;
    lucidReturn='.lucid-file[data-key="'+CSS.escape(f.scope+'/'+f.key)+'"]';
    const data=await api('/api/lucid/'+f.scope+'/'+f.key);if(view!==lucidView)return;
    if(memoryDirty&&!confirm('Discard your unsaved changes?'))return;   // typed while this was loading
    openMemory={key:'file/'+f.scope+'/'+f.key};memoryDirty=false;
    const box=node('div',null,{class:'memory-editor lucid-editor'});
    const text=node('textarea',null,{'aria-label':f.label+' text',spellcheck:'false'});text.value=data.text;text.readOnly=!data.editable;
    text.oninput=()=>{memoryDirty=true;};let stamp=data.stamp;
    const back=button('Back to Lucid Control',safe(memory,async()=>{if(memoryDirty&&!confirm('Discard your unsaved changes?'))return;
      memoryDirty=false;openMemory=null;lucidView++;drawMemory();await drawLucidBoard();refocus();}));
    const actions=node('div',null,{class:'library-actions'});
    if(data.editable)actions.append(button('Save',safe(memory,async()=>{const sent=text.value,mine=openMemory;
      const saved=await api('/api/lucid/'+f.scope+'/'+f.key,{text:sent,stamp});
      stamp=saved.stamp;if(openMemory===mine)memoryDirty=text.value!==sent;   // this editor's typing only (Codex re-review #6)
      report(memory,saved.warning||('Saved '+saved.path),Boolean(saved.warning));}),{class:'primary'}));
    actions.append(back);
    box.append(node('h2',f.label),node('p',data.about,{class:'lucid-note'}),node('p',data.path,{class:'memory-path'}),text,actions);
    memory.detail.replaceChildren(box);drawMemory();text.focus();
  }
  function refocus(){const target=lucidReturn&&memory.root.querySelector(lucidReturn);lucidReturn=null;target?.focus();}
  async function openMem(m){
    if(memoryDirty&&!confirm('Discard your unsaved changes?'))return;
    lucidReturn='.memory-row [data-key="'+CSS.escape(m.scope+'/'+m.name)+'"]';
    memoryDirty=false;const view=++lucidView;const data=await api('/api/memory/'+m.scope+'/'+encodeURIComponent(m.name));if(view!==lucidView)return;
    if(memoryDirty&&!confirm('Discard your unsaved changes?'))return;
    openMemory={key:m.scope+'/'+m.name,...m};memoryDirty=false;
    const box=node('div',null,{class:'memory-editor'});
    const text=node('textarea',null,{'aria-label':'Memory text',spellcheck:'false'});text.value=data.text;text.oninput=()=>{memoryDirty=true;};
    const save=button('Save',safe(memory,async()=>{const sent=text.value,mine=openMemory;await api('/api/memory/'+m.scope+'/'+encodeURIComponent(m.name),{text:sent});
      if(openMemory===mine)memoryDirty=text.value!==sent;await memory.refresh();report(memory,'Saved '+data.path);}),{class:'primary'});
    const del=button('Delete',safe(memory,async()=>{if(!confirm('Delete "'+m.title+'"? This removes the file.'))return;
      await api('/api/memory/'+m.scope+'/'+encodeURIComponent(m.name),{delete:true});memoryDirty=false;openMemory=null;
      picked.delete(m.scope+'/'+m.name);await memory.refresh();report(memory,'Deleted '+data.path);}));
    const back=button('Back to Lucid Control',safe(memory,async()=>{if(memoryDirty&&!confirm('Discard your unsaved changes?'))return;
      memoryDirty=false;openMemory=null;lucidView++;drawMemory();await drawLucidBoard();refocus();}));
    box.append(node('h2',m.title),node('p',data.path,{class:'memory-path'}),text,node('div',null,{class:'library-actions'}));
    box.lastChild.append(save,del,back);memory.detail.replaceChildren(box);drawMemory();text.focus();
  }
  function consolidateMemories(){
    const chosen=memoryRows.filter(m=>picked.has(m.scope+'/'+m.name));
    if(chosen.length<1){report(memory,'Tick the memories to consolidate first.',true);return;}
    chatDraft('Consolidate these memories into fewer, shorter ones without losing anything that still matters:\n'
      +chosen.map(m=>'- '+m.path).join('\n')
      +'\nRead each file, then show me the proposed result (what merges, what goes, the new text) and WAIT for my '
      +'approval before writing or deleting anything.');
  }

  // Files (owner request): the project folder and Dream's own setup folders as a tree.
  // Reading only -- this page never writes, and a folder's children load when it opens.
  const files=page('files','Files','Browse the project folder and Dream\'s own setup folders. Reading only — nothing here changes a file.');
  files.actions.append(button('Refresh',safe(files,()=>files.refresh())));
  const joinPath=(base,name)=>base?base+'/'+name:name;
  const bytes=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?Math.round(n/1024)+' KB':n+' B';
  function entryNodes(data){
    const query=files.search.value.trim().toLowerCase();
    const rows=(data.entries||[]).filter(e=>!query||e.name.toLowerCase().includes(query));
    rows.sort((a,b)=>a.type!==b.type?(a.type==='dir'?-1:1)
      :files.sort.value==='reverse'?b.name.localeCompare(a.name):a.name.localeCompare(b.name));
    return rows.map(e=>{
      const path=joinPath(data.path,e.name);
      return e.type==='dir'?folderNode(data.root,path,e.name):fileNode(data.root,path,e);
    });
  }
  function folderNode(root,path,name){
    const box=node('details',null,{class:'file-folder','data-path':path});
    box.append(node('summary',name));
    let loaded=false;
    box.ontoggle=safe(files,async()=>{
      if(!box.open||loaded)return;
      loaded=true;
      const data=await api('/api/files?root='+root+'&path='+encodeURIComponent(path));
      const children=entryNodes(data);
      box.append(...(children.length?children:[node('p','Empty folder.',{class:'library-meta'})]));
    });
    return box;
  }
  function fileNode(root,path,entry){
    const b=button('',safe(files,()=>openFile(root,path)),{class:'file-entry','data-path':path});
    b.append(node('span',entry.name),node('small',bytes(entry.size)));
    return b;
  }
  async function openFile(root,path){
    const data=await api('/api/files/read?root='+root+'&path='+encodeURIComponent(path));
    const box=node('div',null,{class:'file-view'});
    box.append(node('h2',path.split('/').pop()),node('p',path,{class:'memory-path'}));
    if(data.binary)box.append(node('p','Not a text file ('+bytes(data.size)+').'));
    else{
      box.append(node('pre',data.text,{class:'file-text'}));
      if(data.truncated)box.append(node('p','Showing the first '+bytes(data.text.length)+' of '+bytes(data.size)+'.',{class:'library-meta'}));
    }
    const actions=node('div',null,{class:'library-actions'});
    actions.append(button('Ask about this file',()=>chatDraft('Read `'+path+'` in the '
      +(root==='dream'?'Dream folder':'project folder')+' and tell me what it does.')));
    box.append(actions);
    files.detail.replaceChildren(box);
    files.list.querySelectorAll('[aria-current]').forEach(n=>n.removeAttribute('aria-current'));
    files.list.querySelector('.file-entry[data-path="'+CSS.escape(path)+'"]')?.setAttribute('aria-current','true');
  }
  files.refresh=safe(files,async()=>{
    files.loaded=true;
    const data=await api('/api/files?root='+files.filter.value);
    const children=entryNodes(data);
    files.list.replaceChildren(...(children.length?children:[node('p','Nothing to show here.')]));
    files.count.textContent=children.length+(children.length===1?' item':' items');
    report(files,data.base||'');
  });
  files.search.oninput=()=>files.refresh();
  files.filter.onchange=()=>{files.detail.replaceChildren();files.refresh();};
  files.sort.onchange=()=>files.refresh();

})();
