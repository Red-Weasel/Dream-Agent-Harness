/* Guided tasks stay inside Create and dispatch only a reviewed saved task. */
(() => {
  'use strict';
  const panel=document.getElementById('mc-guided');
  const toggle=document.getElementById('mc-guided-open');
  if(!panel||!toggle)return;
  const token=new URLSearchParams(location.search).get('token')||'';
  const layout=document.querySelector('#dream-create .mc-layout');
  const heading=document.getElementById('mc-heading');
  let selected=null, recipe=null, timer=null, available=false, requestIds=new Map();
  const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};
  panel.innerHTML='<div class="gw-heading"><div><h3>Choose the work you want to finish</h3><p>Review the inputs and output, then start. Saved tasks keep their progress and earlier artifacts.</p></div><button id="gw-refresh">Refresh tasks</button></div><p id="gw-notice" role="status"></p><div id="gw-recipes"></div><div class="gw-layout"><aside><h3>Saved tasks</h3><div id="gw-tasks"></div></aside><main><form id="gw-form" hidden><h3 id="gw-title"></h3><p id="gw-description"></p><label>Goal, topic and audience<textarea id="gw-goal" required maxlength="6000" rows="3"></textarea></label><label>Workspace source paths, one per line<textarea id="gw-sources" maxlength="4000" rows="2" placeholder="notes/source.md"></textarea></label><p id="gw-required"></p><button class="mc-primary" type="submit">Review task</button></form><section id="gw-review" aria-label="Task review"></section></main></div>';
  const $=id=>document.getElementById(id);
  const notice=(text)=>{$('gw-notice').textContent=text;};
  async function api(action,payload){
    const response=await fetch('/api/workflows/'+action,{method:payload===undefined?'GET':'POST',headers:{'x-dream-token':token,...(payload===undefined?{}:{'content-type':'application/json'})},...(payload===undefined?{}:{body:JSON.stringify(payload)})});
    let data;try{data=await response.json();}catch{throw Error('Task response was unreadable. Refresh saved tasks before starting again.');}
    if(!response.ok)throw Error(data.error||'Task request failed');return data;
  }
  const safe=fn=>async event=>{event?.preventDefault();const control=event?.currentTarget;if(control?.dataset.busy)return;if(control)control.dataset.busy='true';try{await fn();}catch(e){notice(e.message);}finally{if(control)delete control.dataset.busy;}};
  const button=(text,fn)=>{const b=el('button',text);b.type='button';b.onclick=safe(fn);return b;};
  function show(showGuided){panel.hidden=!showGuided;layout.hidden=showGuided;toggle.setAttribute('aria-expanded',String(showGuided));toggle.textContent=showGuided?'Animation editor':'Guided tasks';heading.textContent=showGuided?'Create with a guided task':'Create an animation';if(!showGuided)$('mc-name').focus();}
  function choose(spec){
    if(spec.id==='animation'){show(false);return;}
    recipe=spec;selected=null;$('gw-review').replaceChildren();$('gw-form').hidden=false;
    $('gw-title').textContent=spec.title;$('gw-description').textContent=spec.description+' Output: '+spec.default_output+'.';
    $('gw-required').textContent='Required: '+spec.required_inputs.join(', ')+'. Sources must already exist in this workspace; attachments in Chat also show workspace paths.';
    $('gw-sources').required=spec.id==='analysis';$('gw-goal').focus();
  }
  function review(task){
    selected=task;$('gw-form').hidden=true;
    const area=$('gw-review');area.replaceChildren();area.append(el('h3',task.title),el('p','Attempt '+task.attempt+' · '+task.status.replaceAll('_',' ')),el('p',task.message,'gw-message'));
    area.append(el('p',task.inputs.goal,'gw-goal-summary'),el('p','Output: '+task.output_path.split('/').pop()+' · saved in this workspace','gw-path'));
    if(task.inputs.sources.length)area.append(el('p','Sources: '+task.inputs.sources.join(', '),'gw-path'));
    const instructions=el('details');instructions.className='gw-instructions';instructions.append(el('summary','Task instructions and save location'),el('p',task.output_path,'gw-path'));
    const prepared=el('textarea');prepared.value=task.prompt;prepared.readOnly=true;prepared.rows=8;prepared.setAttribute('aria-label','Reviewed task prompt');instructions.append(prepared);area.append(instructions);
    const actions=el('div',null,'gw-actions');area.append(actions);
    async function mutate(action){const result=await api(action,{task_id:task.id,expected_version:task.version});review(result.task);await refresh();}
    if(task.status==='draft'){
      if(!available)area.append(el('p','No agent is connected. Choose an agent before starting.'));
      const start=button('Start task',async()=>{
        const key=task.id+':'+task.attempt;
        if(!requestIds.has(key))requestIds.set(key,crypto.randomUUID());
        const result=await api('start',{task_id:task.id,expected_version:task.version,request_id:requestIds.get(key)});review(result.task);await refresh();
      });start.disabled=!available;start.className='mc-primary';actions.append(start);
    }
    if(['queued','running','unknown'].includes(task.status)){
      const label=el('label',null,'gw-recovery');const check=el('input');check.type='checkbox';label.append(check,document.createTextNode('I checked the conversation. This task is no longer active.'));area.append(label);
      const recover=button('Mark interrupted',()=>mutate('recover'));recover.disabled=true;check.onchange=()=>{recover.disabled=!check.checked;};actions.append(recover);
      area.append(el('p','Recovery does not cancel an agent or repeat any actions. Check uncertain side effects before another attempt.'));
    }else if(task.status!=='draft'){
      actions.append(button('Check output again',()=>mutate('check')),button('Review new attempt',()=>mutate('revise')));
    }
    if(task.steps.length){const details=el('details'),summary=el('summary','Observed steps');details.append(summary);task.steps.forEach(s=>details.append(el('p','Attempt '+s.attempt+' · '+s.status.replaceAll('_',' ')+': '+s.message)));area.append(details);}
    if(task.history.length){const details=el('details');details.append(el('summary','Earlier attempts'));task.history.forEach(h=>{details.append(el('p','Attempt '+h.attempt+' · '+h.status),el('pre',h.prompt));});area.append(details);}
    task.artifacts.forEach(a=>{const row=el('div',null,'gw-artifact');const link=el('a','Download '+a.name+' · attempt '+a.attempt);link.href='/api/workflows/artifacts/'+encodeURIComponent(task.id)+'/'+encodeURIComponent(a.id)+'?token='+encodeURIComponent(token);link.download=a.name;row.append(link,el('p',a.verification));area.append(row);});
  }
  async function refresh(){
    const result=await api('list');const availabilityChanged=available!==result.agent_available;available=result.agent_available;
    $('gw-tasks').replaceChildren();if(!result.tasks.length)$('gw-tasks').append(el('p','No saved tasks yet.'));
    result.tasks.forEach(task=>{$('gw-tasks').append(button(task.title+' · '+task.status.replaceAll('_',' '),()=>review(task)));});
    if(selected){const latest=result.tasks.find(t=>t.id===selected.id);if(latest&&(latest.version!==selected.version||availabilityChanged))review(latest);}
  }
  toggle.onclick=safe(async()=>{const opening=panel.hidden;show(opening);clearInterval(timer);if(opening){await refresh();const result=await api('recipes');$('gw-recipes').replaceChildren();result.recipes.forEach(spec=>{const card=el('article');card.append(button(spec.title,()=>choose(spec)),el('p',spec.description),el('small','Output: '+spec.default_output));$('gw-recipes').append(card);});timer=setInterval(()=>{if(!panel.hidden&&document.getElementById('dream-create').open)refresh().catch(e=>notice(e.message));},3000);}});
  $('gw-form').onsubmit=safe(async()=>{const result=await api('create',{recipe:recipe.id,inputs:{goal:$('gw-goal').value,sources:$('gw-sources').value}});review(result.task);notice('Task saved for review. Start sends it to the selected agent.');await refresh();});
  $('gw-refresh').onclick=safe(refresh);
  document.getElementById('dream-create').addEventListener('close',()=>{clearInterval(timer);show(false);});
})();
