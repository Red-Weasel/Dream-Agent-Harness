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
  function page(view, title, description) {
    const root = node('section', null, {id:'dream-' + view + '-page', class:'dream-library', 'aria-label':title});
    root.hidden = true;
    const header = node('div', null, {class:'library-heading'}), intro = node('div', null);
    intro.append(node('span','Your workspace',{class:'library-eyebrow'}),node('h1',title),node('p',description));
    const actions = node('div',null,{class:'library-actions'});
    header.append(intro, actions);
    const status = node('p','',{class:'library-status',role:'status','aria-live':'polite'});
    const layout = node('div',null,{class:'library-layout'}), aside = node('aside',null,{class:'library-sidebar','aria-label':title+' list'});
    const search = node('input',null,{type:'search',placeholder:'Search '+title.toLowerCase(),'aria-label':'Search '+title.toLowerCase()});
    const list = node('div',null,{class:'library-list'}), detail = node('div',null,{class:'library-detail'});
    const filters=node('div',null,{class:'library-filters'});
    const filter=field(filters,'Show','select',{'aria-label':'Filter '+title.toLowerCase()}),sort=field(filters,'Sort','select',{'aria-label':'Sort '+title.toLowerCase()});
    const choices=view==='skills'?[['all','All skills'],['managed','Your versions'],['installed','Installed originals'],['enabled','Enabled'],['disabled','Disabled']]:[['all','All projects'],['current','Current workspace'],['conversations','With conversations']];
    for(const [value,label] of choices)filter.append(node('option',label,{value}));
    for(const [value,label] of [['name','Name A–Z'],['reverse','Name Z–A'],...(view==='projects'?[['conversations','Most conversations']]:[])])sort.append(node('option',label,{value}));
    const count=node('p','',{class:'library-count',role:'status','aria-live':'polite'});
    const hint=node('p','Use ↑ and ↓ to browse, Enter to open.',{class:'library-keyboard-hint',id:'library-'+view+'-keys'});list.setAttribute('aria-describedby',hint.id);
    list.onkeydown=event=>{const items=[...list.querySelectorAll('button')],index=items.indexOf(document.activeElement);if(index<0)return;let next;if(event.key==='ArrowDown')next=Math.min(index+1,items.length-1);else if(event.key==='ArrowUp')next=Math.max(0,index-1);else if(event.key==='Home')next=0;else if(event.key==='End')next=items.length-1;else return;event.preventDefault();items[next]?.focus();};
    search.onkeydown=event=>{if(event.key==='ArrowDown'){event.preventDefault();list.querySelector('button')?.focus();}};
    aside.append(search,filters,count,hint,list);layout.append(aside,detail);root.append(header,status,layout);document.querySelector('.split').append(root);
    const state = {root, actions, status, search, filter, sort, count, list, detail, loaded:false};pages[view] = state;
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
  function drawSkills() {
    const focused=skills.list.contains(document.activeElement)?document.activeElement.dataset.key:null;
    const query=skills.search.value.trim().toLowerCase();skills.list.replaceChildren();
    const rows=skillRows.filter(s=>(s.name+' '+s.description).toLowerCase().includes(query)).filter(s=>skills.filter.value==='all'||(skills.filter.value==='managed'?s.managed:skills.filter.value==='installed'?!s.managed:skills.filter.value==='enabled'?s.enabled!==false:s.enabled===false));
    rows.sort((a,b)=>a.name.localeCompare(b.name)* (skills.sort.value==='reverse'?-1:1));
    skills.count.textContent=rows.length+' of '+skillRows.length+' skills';
    for(const s of rows){
      const b=button('',safe(skills,()=>openSkill(s.name)),{'aria-current':String(selectedSkill?.name===s.name),'data-key':s.name});
      b.append(node('strong',s.name),node('small',s.description||'No description'),node('small',s.managed?'Your version':s.source||'Installed skill'));skills.list.append(b);
    }
    if(!skills.list.children.length)skills.list.append(node('p',query||skills.filter.value!=='all'?'No matching skills. Change the search or filter.':'No skills saved yet.'));
    [...skills.list.querySelectorAll('button')].find(b=>b.dataset.key===focused)?.focus();
  }
  skills.search.oninput=drawSkills;skills.filter.onchange=drawSkills;skills.sort.onchange=drawSkills;
  skills.refresh=safe(skills,async()=>{const data=await api('/api/skills');skillRows=data.skills||[];skills.loaded=true;drawSkills();report(skills,data.warnings?.length?'Library warnings: '+data.warnings.join(' · '):'Skill library refreshed.',!!data.warnings?.length);});
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
    selectedSkill=data;drawSkills();editSkill(data);report(skills,'Full instructions loaded.');
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
  empty(skills,'Instructions you can shape','Choose a skill to read all of it, or create one for a workflow you repeat.',button('Create a skill',newSkill,{class:'primary'}));

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
  window.addEventListener('beforeunload',e=>{if(skillDraft||projectDraft){e.preventDefault();e.returnValue='';}});
})();
