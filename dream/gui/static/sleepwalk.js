/* Sleepwalk (DREAM-156): automations, the editor, Run now, Runs and the reader. Phase 1: schedules are saved, not fired. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const root=document.documentElement;
  const el=(tag,text,attrs={})=>{const n=document.createElement(tag);if(text!==null)n.textContent=text;for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;};
  const btn=(text,fn,attrs={})=>{const b=el('button',text,{type:'button',...attrs});b.onclick=fn;return b;};
  async function api(path,payload){
    const r=await fetch(path,{method:payload===undefined?'GET':'POST',cache:'no-store',headers:{'X-Dream-Token':TOKEN,'Content-Type':'application/json'},...(payload===undefined?{}:{body:JSON.stringify(payload)})});
    const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed ('+r.status+')');return data;
  }
  const GLYPH={spark:'✦',sun:'☼',moon:'☾',bulb:'✺',book:'▤',mail:'✉',globe:'◍',pen:'✎',leaf:'❦',heart:'♥',star:'★',note:'♪'};
  const HUE={spark:'violet',sun:'amber',moon:'indigo',bulb:'amber',book:'teal',mail:'rose',globe:'blue',pen:'violet',leaf:'teal',heart:'rose',star:'amber',note:'blue'};
  const SHORT={codex:'Codex',grok:'Grok',anthropic:'Claude',gemini:'Gemini',machx:'MachX',openai:'OpenAI',xai:'xAI'};
  const DAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
  const EVERY=[['day','Daily'],['weekdays','Weekdays'],['week','Weekly'],['month','Monthly'],['hours','Every N hours']];
  let state={automations:[],templates:[],runners:[],runs:[],default_runner:null,icons:[]};
  const tile=(icon,cls='sw-tile')=>el('span',GLYPH[icon]||'✦',{class:cls,'data-hue':HUE[icon]||'violet','aria-hidden':'true'});
  function clock(at){const [h,m]=at.split(':').map(Number);return (h%12||12)+':'+String(m).padStart(2,'0')+(h<12?' AM':' PM');}
  function describe(t){
    const at=clock(t.at);
    if(t.every==='day')return 'Daily at '+at;
    if(t.every==='weekdays')return 'Weekdays at '+at;
    if(t.every==='week')return (t.days||[0]).map(d=>DAYS[d]+'s').join(', ')+' at '+at;
    if(t.every==='month')return 'Monthly on day '+t.day+' at '+at;
    return 'Every '+t.n+' hours from '+at;
  }
  const runnerText=r=>[SHORT[r.provider]||r.provider,r.model,r.effort].filter(Boolean).join(' · ');
  const when=s=>s?new Date(s).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'';

  // The page.
  const page=el('section',null,{id:'dream-sleepwalk-page',class:'dream-library sw','aria-label':'Sleepwalk Automations'});page.hidden=true;
  const head=el('div',null,{class:'library-heading'}),intro=el('div',null);
  intro.append(el('span','Sleepwalk',{class:'library-eyebrow'}),el('h1','Sleepwalk Automations'),
    el('p','Instructions Dream runs for you on a schedule, read-only, with the results kept here.'));
  const bg=btn('',async()=>{bg.disabled=true;try{state.background=(await api('/api/sleepwalk/background',{on:state.background!=='on'})).background;}
    catch(e){status.textContent=e.message;status.dataset.error='true';}drawBackground();},{class:'sw-bg',role:'switch'});
  function drawBackground(){const s=state.background||'unavailable';bg.textContent='Background runs · '+(s==='on'?'On':s==='off'?'Off':'Unavailable');
    bg.setAttribute('aria-checked',String(s==='on'));bg.disabled=s==='unavailable';
    bg.title=s==='unavailable'?'Needs a systemd user session (systemctl --user).':'Every 15 minutes while you are logged in, also with Dream closed. Cloud runners only.'+
      (state.linger?' Note: linger is on for your user, so the timer also wakes after you log out; runs are skipped then.':'');bg.dataset.linger=String(!!state.linger);}
  head.append(intro,bg);
  const bar=el('div',null,{class:'sw-bar'}),tabs=el('div',null,{class:'sw-tabs',role:'tablist'});
  const panels={automations:el('div',null,{class:'sw-panel',role:'tabpanel'}),runs:el('div',null,{class:'sw-panel',role:'tabpanel'})};
  for(const [key,label] of [['automations','Automations'],['runs','Runs']])tabs.append(btn(label,()=>showTab(key),{role:'tab','data-tab':key}));
  bar.append(tabs,btn('New automation',()=>edit(null),{class:'primary'}));
  const status=el('p','',{class:'library-status',role:'status','aria-live':'polite'});
  page.append(head,bar,status,panels.automations,panels.runs);
  document.querySelector('.split').append(page);
  function showTab(key){for(const [k,p] of Object.entries(panels))p.hidden=k!==key;tabs.querySelectorAll('[role=tab]').forEach(t=>t.setAttribute('aria-selected',String(t.dataset.tab===key)));if(key==='runs')drawRuns();}
  showTab('automations');

  async function refresh(){
    try{const [data,runs]=await Promise.all([api('/api/sleepwalk'),api('/api/sleepwalk/runs')]);state={...data,runs:runs.runs,problems:[...data.problems,...runs.problems]};status.textContent='';status.removeAttribute('data-error');draw();drawRuns();drawBackground();}
    catch(e){status.textContent=e.message;status.dataset.error='true';}
  }
  function draw(){
    const grid=el('div',null,{class:'sw-grid'});
    const build=el('article',null,{class:'sw-card sw-build'});
    build.append(topRow(tile('spark'),btn('Start',()=>edit(null),{class:'sw-pill'})),el('h3','Build your own'),
      el('p','Write your own instructions in plain words, choose who runs them, and read the result in Runs.',{class:'sw-summary'}));
    grid.append(build);
    for(const a of state.automations){
      const card=el('article',null,{class:'sw-card'}),last=state.runs.find(r=>r.automation===a.id);
      const foot=el('div',null,{class:'sw-foot'}),run=btn('Run now',()=>runNow(a,run),{class:'sw-pill'});
      const toggle=btn('',()=>setEnabled(a,toggle),{class:'sw-switch',role:'switch','aria-checked':String(a.enabled),'aria-label':(a.enabled?'Turn off ':'Turn on ')+a.title});
      foot.append(toggle,run,el('span',runnerText(a.runner),{class:'sw-chip'}),el('small',last?'Last ran '+when(last.started)+' · '+last.status:'Never run'));
      const clockLine=a.triggers.length?describe(a.triggers[0])+(a.triggers.length>1?' · +'+(a.triggers.length-1)+' more':''):'No schedule';
      const next=a.enabled?(a.next?' · next '+when(a.next):''):' · off';
      card.append(topRow(tile(a.icon),btn('Edit',()=>edit(a),{class:'sw-pill'})),el('h3',a.title),el('p',a.instructions,{class:'sw-summary'}),el('p','◷ '+clockLine+next,{class:'sw-when'}),foot);
      grid.append(card);
    }
    const list=el('div',null,{class:'sw-templates'});
    for(const t of state.templates){
      const row=el('div',null,{class:'sw-template'}),text=el('div',null);
      text.append(el('strong',t.title),el('small',t.summary));
      row.append(tile(t.icon),text,btn('Add',()=>edit({...t,runner:null}),{class:'sw-pill','aria-label':'Add '+t.title}));list.append(row);
    }
    panels.automations.replaceChildren(...notices(),el('h2','Your automations',{class:'sw-label'}),grid,el('h2','Templates',{class:'sw-label'}),list);
  }
  const notices=()=>(state.problems||[]).map(p=>el('p',p,{class:'library-notice sw-notice',role:'status'}));
  function topRow(...items){const r=el('div',null,{class:'sw-top'});r.append(...items);return r;}
  function drawRuns(){
    const rows=state.runs.map(r=>{const row=el('div',null,{class:'sw-run'}),text=el('div',null);
      text.append(el('strong',r.title),el('small',[runnerText(r.runner),when(r.started),r.trigger].filter(Boolean).join(' · ')));
      row.append(el('span',r.status,{class:'sw-status','data-status':r.status}),text,btn('Open',()=>openRun(r),{class:'sw-pill'}));return row;});
    panels.runs.replaceChildren(...notices(),...(rows.length?rows:[el('p','No runs yet. Use Run now on an automation.',{class:'library-empty'})]));
  }

  // Run now, the notice and the reader.
  async function runNow(a,button){
    button.disabled=true;button.textContent='Running…';
    try{const {run}=await api('/api/sleepwalk/run',{id:a.id});notify(run);await refresh();}
    catch(e){notify(null,a.title+': '+e.message);button.disabled=false;button.textContent='Run now';}
  }
  function notify(run,text){
    const toast=el('div',null,{class:'sw-toast',role:'status'});
    toast.append(el('span',text||(run.status==='OK'?run.title+' finished':run.title+' failed — open it to see why')));
    if(run)toast.append(btn('Open',()=>{toast.remove();openRun(run);},{class:'sw-pill'}));
    toast.append(btn('×',()=>toast.remove(),{'aria-label':'Dismiss'}));
    document.body.append(toast);setTimeout(()=>toast.remove(),12000);
  }
  const reader=el('dialog',null,{id:'sw-reader',class:'sw-dialog','aria-label':'Run result'});document.body.append(reader);
  async function openRun(r){
    reader.replaceChildren(el('p','Reading…'));if(!reader.open)reader.showModal();
    try{
      const {run}=await api('/api/sleepwalk/runs/'+encodeURIComponent(r.automation)+'/'+encodeURIComponent(r.run));
      const top=el('div',null,{class:'sw-dialog-head'});top.append(el('h2',run.title),btn('×',()=>reader.close(),{'aria-label':'Close'}));
      const body=el('div',null,{class:'sw-dialog-body'});
      body.append(el('p',[run.status,runnerText(run.runner),when(run.started)+' → '+when(run.finished)].join(' · '),{class:'sw-meta'}));
      if(run.status==='OK')body.append(el('pre',run.output,{class:'sw-output'}));
      else body.append(el('p','This run did not produce a result.'),el('pre',run.error||'',{class:'sw-output sw-error'}));
      body.append(el('p','Notified: '+(run.notified||[]).join(', ').replace('app','Dream')+(run.problems?.length?' · Notes: '+run.problems.join('; '):''),{class:'sw-meta'}));
      reader.replaceChildren(top,body);
    }catch(e){reader.replaceChildren(el('p',e.message),btn('Close',()=>reader.close()));}
  }

  // The editor.
  const editor=el('dialog',null,{id:'sw-editor',class:'sw-dialog','aria-label':'Automation editor'});document.body.append(editor);
  function edit(a){
    const draft=a?structuredClone(a):{icon:'spark',title:'',instructions:'',triggers:[{every:'day',at:'08:00'}]};
    draft.runner=draft.runner||state.default_runner||{provider:state.runners[0]?.key||'codex',model:null,effort:null};
    const heading=el('h2',draft.title||'New automation'),top=el('div',null,{class:'sw-dialog-head'});
    top.append(heading,btn('×',()=>editor.close(),{'aria-label':'Close editor'}));
    const body=el('div',null,{class:'sw-dialog-body'}),identity=el('div',null,{class:'sw-identity'});
    const iconButton=btn('',()=>{grid.hidden=!grid.hidden;},{class:'sw-icon-tile','aria-label':'Choose icon'});
    const setIcon=i=>{draft.icon=i;iconButton.replaceChildren(tile(i,'sw-tile sw-tile-lg'));grid.hidden=true;};
    const grid=el('div',null,{class:'sw-icon-grid'});grid.hidden=true;
    for(const i of state.icons)grid.append(btn('',()=>setIcon(i),{'data-icon':i,'aria-label':i}).appendChild(tile(i)).parentNode);
    const name=el('input',null,{'aria-label':'Automation name',placeholder:'Name this automation',maxlength:'120'});name.value=draft.title||'';
    name.oninput=()=>{heading.textContent=name.value||'New automation';};
    identity.append(iconButton,name);setIcon(draft.icon);
    const triggers=el('div',null,{class:'sw-triggers'});
    const drawTriggers=()=>triggers.replaceChildren(...draft.triggers.map((t,i)=>triggerRow(t,()=>{draft.triggers.splice(i,1);drawTriggers();})));
    drawTriggers();
    const add=btn('',()=>{draft.triggers.push({every:'day',at:'08:00'});drawTriggers();},{class:'sw-add'});add.append(el('i','+',{'aria-hidden':'true'}),el('span','Add another time'));
    const compose=el('div',null,{class:'sw-compose'}),text=el('textarea',null,{'aria-label':'Instructions',placeholder:'Write what this automation should do, in plain words.',maxlength:'8000'});
    text.value=draft.instructions||'';draft.connectors=draft.connectors||[];draft.notify=draft.notify||['app'];
    let notifyBox=notifyPicker(draft);
    const files=el('div',null,{class:'sw-files'}),pop=el('div',null,{class:'sw-popover'});pop.hidden=true;
    const picker=el('input',null,{type:'file',multiple:'',hidden:''});picker.onchange=()=>upload(a,[...picker.files],files);
    const conn=btn('Connectors '+draft.connectors.length,()=>{pop.hidden=!pop.hidden;conn.setAttribute('aria-expanded',String(!pop.hidden));if(!pop.hidden)drawConnectors(draft,pop,conn,()=>{const next=notifyPicker(draft);notifyBox.replaceWith(next);notifyBox=next;});},{class:'sw-pill','aria-expanded':'false'});
    const tools=el('div',null,{class:'sw-tools'});
    tools.append(btn('+ Attach',()=>picker.click(),{class:'sw-pill',...(a?.id?{}:{disabled:'',title:'Save the automation first'})}),conn,picker,runnerPicker(draft.runner));
    compose.append(text,tools,pop);drawFiles(a,a?.attachments||[],files);
    body.append(identity,grid,el('h3','Triggers',{class:'sw-label'}),el('p','In your local time, while Dream is open or Background runs are on.',{class:'sw-hint'}),triggers,add,
      el('h3','Instructions',{class:'sw-label'}),compose,el('h3','Attachments',{class:'sw-label'}),files,el('h3','Notification',{class:'sw-label'}),notifyBox,
      el('p','In Dream: every run is kept in Runs, with a notice while Dream is open. Runs use the runner’s read-only mode and are told never to send or change anything.',{class:'sw-note'}),whenCant(draft));
    const foot=el('div',null,{class:'sw-dialog-foot'}),error=el('p','',{class:'library-status',role:'alert'});
    if(a?.id)foot.append(btn('Delete',async()=>{if(!confirm('Delete "'+a.title+'"? Its past runs stay in Runs.'))return;try{await api('/api/sleepwalk/delete',{id:a.id});editor.close();refresh();}catch(e){error.textContent=e.message;}},{class:'sw-danger'}));
    foot.append(error,btn('Cancel',()=>editor.close()),btn('Save',async()=>{
      const automation={...draft,title:name.value,instructions:text.value};delete automation.summary;
      try{await api('/api/sleepwalk/save',{automation});editor.close();refresh();}catch(e){error.textContent=e.message;error.dataset.error='true';}
    },{class:'primary'}));
    editor.replaceChildren(top,body,foot);editor.showModal();name.focus();
  }
  function triggerRow(t,remove){
    const row=el('div',null,{class:'sw-trigger'}),every=el('select',null,{'aria-label':'Repeat'});
    for(const [v,l] of EVERY)every.append(el('option',l,{value:v}));every.value=t.every;
    const extra=el('span',null,{class:'sw-extra'});
    const time=el('input',null,{type:'time','aria-label':'Time',required:''});time.value=t.at;time.onchange=()=>{t.at=time.value||t.at;};
    const label=el('input',null,{'aria-label':'Label',placeholder:'label, optional',maxlength:'60'});label.value=t.label||'';
    label.oninput=()=>{if(label.value)t.label=label.value;else delete t.label;};
    function drawExtra(keep){
      if(!keep){delete t.days;delete t.day;delete t.n;}
      if(t.every==='week'){if(!Array.isArray(t.days)||!t.days.length)t.days=[0];
        extra.replaceChildren(...DAYS.map((d,i)=>{const b=btn(d.slice(0,2),()=>{const on=t.days.includes(i);if(on&&t.days.length===1)return;t.days=on?t.days.filter(x=>x!==i):[...t.days,i].sort();b.setAttribute('aria-pressed',String(!on));},{class:'sw-day','aria-label':d,'aria-pressed':String(t.days.includes(i))});return b;}));}
      else if(t.every==='month'||t.every==='hours'){const key=t.every==='month'?'day':'n';if(!t[key])t[key]=t.every==='month'?1:4;
        const n=el('input',null,{type:'number',min:'1',max:t.every==='month'?'31':'24','aria-label':t.every==='month'?'Day of month':'Hours'});n.value=t[key];n.onchange=()=>{t[key]=Number(n.value);};extra.replaceChildren(n);}
      else extra.replaceChildren();
    }
    drawExtra(true);every.onchange=()=>{t.every=every.value;drawExtra(false);};
    row.append(el('span','◷',{class:'sw-clock','aria-hidden':'true'}),every,extra,el('span','at',{class:'sw-at'}),time,label,btn('✕',remove,{'aria-label':'Remove this time',class:'sw-trash'}));
    return row;
  }
  async function setEnabled(a,toggle){
    toggle.disabled=true;
    try{await api('/api/sleepwalk/save',{automation:{...a,enabled:!a.enabled}});await refresh();}
    catch(e){status.textContent=e.message;status.dataset.error='true';toggle.disabled=false;}
  }
  const hasKind=(c,k)=>c.kinds.includes(k);
  function drawConnectors(draft,pop,conn,saved){
    pop.replaceChildren(...state.connectors.map(c=>{
      const row=el('div',null,{class:'sw-conn'}),head=el('div',null,{class:'sw-conn-head'}),form=el('form',null,{class:'sw-setup'});form.hidden=true;
      head.append(el('strong',c.label),el('small',c.ready?'Set up':'Not set up'));
      if(hasKind(c,'context')){const use=el('input',null,{type:'checkbox','aria-label':'Use '+c.label});use.checked=draft.connectors.includes(c.id);
        use.onchange=()=>{draft.connectors=use.checked?[...draft.connectors,c.id]:draft.connectors.filter(x=>x!==c.id);conn.textContent='Connectors '+draft.connectors.length;};head.prepend(use);}
      head.append(btn('Set up',()=>{form.hidden=!form.hidden;},{class:'sw-pill'}));
      for(const f of c.fields){const l=el('label',f.label),i=el('input',null,{name:f.name,type:f.secret?'password':'text',autocomplete:'off',
        placeholder:f.secret?(f.set?'Saved in the '+f.set:''):(f.default||'')});if(!f.secret)i.value=f.value||'';l.append(i);form.append(l);}
      const note=el('p','',{class:'sw-hint',role:'status'});
      const save=async()=>{const values=Object.fromEntries([...form.elements].filter(i=>i.name).map(i=>[i.name,i.value]));
        try{state.connectors=(await api('/api/sleepwalk/connectors',{id:c.id,values})).connectors;drawConnectors(draft,pop,conn,saved);saved();}catch(e){note.textContent=e.message;}};
      form.onsubmit=e=>{e.preventDefault();save();};   // Enter in a field must never submit the form as a GET with the secret in the URL
      form.append(note,btn('Save '+c.label,save,{class:'primary'}));
      row.append(head,form);return row;}));
  }
  function notifyPicker(draft){
    const box=el('details',null,{class:'sw-notify'}),summary=el('summary','');
    const label=()=>{summary.textContent=['Dream',...state.connectors.filter(c=>draft.notify.includes(c.id)).map(c=>c.label)].join(' + ');};
    box.append(summary,el('label','Dream (always)'));box.lastChild.prepend(el('input',null,{type:'checkbox',checked:'',disabled:''}));
    for(const c of state.connectors.filter(c=>hasKind(c,'notify'))){const l=el('label',c.label+(c.ready?'':' (not set up yet)')),i=el('input',null,{type:'checkbox'});
      i.checked=draft.notify.includes(c.id);i.onchange=()=>{draft.notify=i.checked?[...draft.notify,c.id]:draft.notify.filter(x=>x!==c.id);label();};l.prepend(i);box.append(l);}
    label();return box;
  }
  function drawFiles(a,names,files){
    if(!a?.id){files.replaceChildren(el('p','Save the automation first, then attach files.',{class:'sw-hint'}));return;}
    files.replaceChildren(...names.map(n=>{const chip=el('span',n,{class:'sw-chip'});chip.append(btn('×',async()=>{try{drawFiles(a,(await api('/api/sleepwalk/detach',{id:a.id,name:n})).attachments,files);}catch(e){status.textContent=e.message;}},{'aria-label':'Remove '+n}));return chip;}));
    if(!names.length)files.append(el('p','No files attached.',{class:'sw-hint'}));
  }
  async function upload(a,list,files){
    for(const f of list){try{const r=await fetch('/api/sleepwalk/attach?id='+encodeURIComponent(a.id)+'&name='+encodeURIComponent(f.name),{method:'POST',headers:{'X-Dream-Token':TOKEN},body:f});
      const data=await r.json();if(!r.ok)throw Error(data.error);drawFiles(a,data.attachments,files);}catch(e){files.append(el('p',f.name+': '+e.message,{class:'sw-hint'}));}}
  }
  let newest=null;   // scheduled runs that finish while Dream is open get the same notice as Run now
  setInterval(async()=>{try{const {runs}=await api('/api/sleepwalk/runs');if(newest!==null)for(const r of runs.filter(r=>r.run>newest&&r.trigger!=='Run now'))notify(r);newest=runs[0]?.run||newest||'';}catch{}},30000);
  function whenCant(draft){
    const box=el('details',null,{class:'sw-cant'}),missed=el('select',null,{'aria-label':'Missed while Dream was closed'}),fallback=el('select',null,{'aria-label':'Local model not loaded'});
    for(const [v,l] of [['run_once','Run once when Dream opens'],['skip','Skip']])missed.append(el('option',l,{value:v}));
    fallback.append(el('option','Skip the run',{value:''}),...state.runners.filter(c=>c.isolated&&c.key!=='machx').map(c=>el('option','Use '+(SHORT[c.key]||c.label),{value:c.key})));
    missed.value=draft.missed||'run_once';fallback.value=draft.fallback?.provider||'';
    missed.onchange=()=>{draft.missed=missed.value;};fallback.onchange=()=>{draft.fallback=fallback.value?{provider:fallback.value,model:null,effort:null}:null;};
    const row=(text,select)=>{const l=el('label',text);l.append(select);return l;};
    box.append(el('summary','When it can’t run'),row('Missed while Dream was closed',missed),row('Local model not loaded',fallback));
    return box;
  }
  function runnerPicker(r){
    const box=el('div',null,{class:'sw-runner'}),provider=el('select',null,{'aria-label':'Runner'}),model=el('select',null,{'aria-label':'Model'}),effort=el('select',null,{'aria-label':'Effort'});
    for(const c of state.runners)provider.append(el('option',(SHORT[c.key]||c.label)+(c.available?'':' (not found)')+(c.isolated?'':' (cannot run isolated)'),{value:c.key}));
    const row=()=>state.runners.find(c=>c.key===r.provider)||{models:[],efforts:[]};
    function fill(select,items,value,blank){select.replaceChildren(el('option',blank,{value:''}),...items.map(([v,l])=>el('option',l,{value:v})));if(value&&!items.some(([v])=>v===value))select.append(el('option',value,{value}));select.value=value||'';}
    function efforts(){const m=(row().models||[]).find(m=>m.id===r.model);const list=m?m.efforts:row().efforts||[];if(r.effort&&!list.includes(r.effort))r.effort=list.includes('high')?'high':null;fill(effort,list.map(e=>[e,e]),r.effort,'Default effort');}
    function models(){fill(model,(row().models||[]).map(m=>[m.id,m.id]),r.model,'Default model');efforts();}
    provider.value=r.provider;models();
    provider.onchange=()=>{r.provider=provider.value;const m=row().models?.[0];r.model=m?.id||null;r.effort=(m?.efforts||row().efforts||[]).includes('high')?'high':null;models();};
    model.onchange=()=>{r.model=model.value||null;efforts();};effort.onchange=()=>{r.effort=effort.value||null;};
    box.append(provider,model,effort);return box;
  }

  new MutationObserver(()=>{const on=root.dataset.dreamView==='sleepwalk';if(on===!page.hidden)return;page.hidden=!on;if(on)refresh();}).observe(root,{attributes:true,attributeFilter:['data-dream-view']});
})();
