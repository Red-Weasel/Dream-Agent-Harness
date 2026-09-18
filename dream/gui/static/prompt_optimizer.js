/* Explicit, session-bound prompt preparation. Nothing here submits a chat turn. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id), root = document.documentElement;
  const panel = document.createElement('aside');
  panel.id = 'dream-prompt-optimizer'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Prompt Optimizer');
  panel.innerHTML = `
    <div class="po-header"><div><h2>Prompt Optimizer</h2><p>Shape the request before you send it.</p></div><button id="po-close" type="button" aria-label="Close Prompt Optimizer">×</button></div>
    <div class="po-body">
      <div id="po-context-changed" class="po-warning" hidden><p>Your session or workspace changed. This draft stays with its original context.</p><button id="po-reset" type="button">Start a new draft here</button></div>
      <label for="po-draft">Your draft <span class="po-required">Required</span></label>
      <textarea id="po-draft" rows="5" maxlength="16000" placeholder="What would you like the agent to do?"></textarea>
      <label for="po-ideal">Ideal outcome <span>Optional</span></label>
      <textarea id="po-ideal" rows="2" maxlength="8000" placeholder="Describe what a good result looks like."></textarea>
      <div class="po-files"><button id="po-add-file" type="button">Attach reference files</button><span>Up to 8 files · 25 MB each</span><input id="po-file-input" type="file" multiple hidden><ul id="po-files" aria-label="Optimizer reference files"></ul></div>
      <div class="po-options"><label for="po-reasoning">Reasoning<select id="po-reasoning"><option value="standard">Standard</option><option value="careful">Careful</option><option value="alternatives">Compare alternatives</option></select></label><label for="po-target">Prompt guidance<select id="po-target"><option value="auto">Auto</option><option value="astra">Astra</option><option value="fable">Fable 5.1</option><option value="general">General</option></select></label></div>
      <p class="po-help">Writing guidance only; model and effort stay in Controls.</p>
      <details id="po-details"><summary>Context, constraints and verification</summary><label for="po-context">Context</label><textarea id="po-context" rows="2" maxlength="8000"></textarea><label for="po-constraints">Constraints</label><textarea id="po-constraints" rows="2" maxlength="8000" placeholder="What must stay unchanged? What should be avoided?"></textarea><label for="po-verification">Verification</label><textarea id="po-verification" rows="2" maxlength="8000" placeholder="How should the result be checked?"></textarea></details>
      <label class="po-checkbox"><input id="po-sources-only" type="checkbox">Use only the supplied sources</label>
      <p id="po-sources-note" class="po-help" hidden>Missing source details stay UNKNOWN. The prompt must not invent them.</p>
      <div id="po-questions" hidden><h3>A few details that matter</h3><p class="po-help">Answer what you know, then optimize again.</p><div id="po-question-fields"></div></div>
      <details id="po-saved-answers" hidden><summary>Clarifications kept with this draft</summary><div id="po-saved-answer-list"></div></details>
      <div class="po-actions"><button id="po-optimize" class="po-primary" type="button">Optimize prompt</button><button id="po-quick" type="button">Quick structure</button></div>
      <p class="po-help">Optimize uses the current agent when chat is idle. Quick structure formats your inputs without a model.</p>
      <p id="po-status" role="status" aria-live="polite"></p>
      <section id="po-output" hidden aria-label="Prepared prompt"><label for="po-result">Prepared prompt <span>Editable</span></label><textarea id="po-result" rows="10" maxlength="48000"></textarea><ul id="po-notes" class="po-notes"></ul><details id="po-evidence" hidden><summary>Source evidence</summary><ul id="po-evidence-list"></ul></details><div class="po-actions"><button id="po-use" class="po-primary" type="button">Use in chat</button><button id="po-copy" type="button">Copy</button></div><div id="po-append-choice" class="po-warning" hidden><p>Chat already has a draft. Keep it and append this prompt?</p><button id="po-append" type="button">Append to chat</button></div></section>
      <p class="po-private">Drafts stay in this tab’s memory. Copy anything you want to keep before reloading.</p>
    </div>`;
  document.querySelector('.split').append(panel);
  let bound = null, revision = 0, generation = 0, request = null, files = [], questions = [], dirty = false;
  let answers = new Map();
  const current = () => ({workspace: window.DREAM_SESSION?.workspace || '', session_id: window.DREAM_SESSION?.session_id || ''});
  const key = value => JSON.stringify([value?.workspace || '', value?.session_id || '']);
  const matches = () => bound && bound.workspace && bound.session_id && key(bound) === key(current()) && !window.DREAM_PROJECT_SWITCHING;
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  function status(text, error = false){ byId('po-status').textContent = text; byId('po-status').classList.toggle('po-error', error); }
  function changed(){ revision++; dirty = true; byId('po-append-choice').hidden = true; controls(); }
  function controls(){
    const unavailable = !matches(), busy = Boolean(request), uploading = files.some(f => !f.id);
    byId('po-context-changed').hidden = !bound || key(bound) === key(current());
    for(const id of ['po-optimize','po-quick']) byId(id).disabled = unavailable || busy || uploading || !byId('po-draft').value.trim();
    byId('po-add-file').disabled = unavailable || files.length >= 8;
    byId('po-use').disabled = unavailable || !byId('po-result').value.trim();
    byId('po-append').disabled = unavailable || !byId('po-result').value.trim();
    byId('po-copy').disabled = !byId('po-result').value.trim();
    byId('po-sources-note').hidden = !byId('po-sources-only').checked;
    panel.setAttribute('aria-busy', String(busy));
  }
  function show(){
    if(!bound) bound = current();
    panel.hidden = false; root.classList.add('prompt-optimizer-open');
    root.dataset.dreamView = 'optimizer'; controls(); byId('po-draft').focus();
  }
  function hide(focus = true){
    panel.hidden = true; root.classList.remove('prompt-optimizer-open');
    if(root.dataset.dreamView === 'optimizer') root.dataset.dreamView = 'chat';
    if(focus) byId('dream-nav-optimizer')?.focus();
  }
  function renderFiles(){
    const list = byId('po-files'); list.replaceChildren();
    for(const item of files){
      const row = document.createElement('li');
      row.append(node('span', item.name), node('small', item.error || (item.id ? 'Ready' : 'Uploading…')));
      if(item.error && item.file){ const retry = node('button','Retry'); retry.type='button'; retry.onclick=()=>upload(item); retry.disabled=!matches(); row.append(retry); }
      const remove = node('button','Remove'); remove.type='button'; remove.setAttribute('aria-label','Remove '+item.name);
      remove.onclick=()=>{item.controller?.abort(); files=files.filter(f=>f!==item); changed(); renderFiles();}; row.append(remove); list.append(row);
    }
    controls();
  }
  async function upload(item){
    if(!matches()) return;
    item.controller?.abort(); const controller = new AbortController(), origin = key(bound), version = generation;
    item.controller=controller; item.error=''; renderFiles();
    try{
      const response=await fetch('/api/upload?name='+encodeURIComponent(item.name), {method:'POST',headers:{'Content-Type':'application/octet-stream','X-Dream-Token':TOKEN},body:item.file,signal:controller.signal});
      const data=await response.json();
      if(version!==generation || origin!==key(current()) || !files.includes(item) || item.controller!==controller) return;
      if(!response.ok) throw Error(data.error || 'Upload failed. Retry or remove the file.');
      if(typeof data.id!=='string' || typeof data.path!=='string') throw Error('Upload was not confirmed. Retry or remove the file.');
      Object.assign(item,{id:data.id,path:data.path,name:data.name||item.name,size:data.size,workspace:bound.workspace,session_id:bound.session_id});
      item.file=null; changed();
    }catch(error){ if(files.includes(item)&&item.controller===controller&&error.name!=='AbortError') item.error=error.message || 'Upload failed.'; }
    finally{if(item.controller===controller && files.includes(item)) renderFiles();}
  }
  function addFiles(selected){
    if(!matches()) return;
    for(const file of selected){
      if(files.length>=8){status('Attach at most 8 files. Remove one before adding another.',true);break;}
      const item={name:file.name,size:file.size,file}; files.push(item); changed();
      if(file.size>25*1024*1024){item.error='Over 25 MB. Choose a smaller file.';item.file=null;renderFiles();}
      else upload(item);
    }
  }
  function payload(mode){
    const saved = new Map(answers);
    questions.forEach((q,i)=>{const answer=byId('po-answer-'+i)?.value.trim() || '';if(answer)saved.set(q.question,answer);else saved.delete(q.question);});
    if(saved.size>3) throw Error('This draft already has three saved clarifications. Remove an earlier answer or fold it into Context before adding another.');
    answers=saved; renderAnswers();
    return {draft:byId('po-draft').value,ideal_output:byId('po-ideal').value,
      context:byId('po-context').value,constraints:byId('po-constraints').value,verification:byId('po-verification').value,
      reasoning:byId('po-reasoning').value,target:byId('po-target').value,sources_only:byId('po-sources-only').checked,
      answers:[...answers].map(([question,answer])=>({question,answer})),
      attachments:files.map(f=>f.id),mode,...bound};
  }
  function renderAnswers(){
    const list=byId('po-saved-answer-list');list.replaceChildren();byId('po-saved-answers').hidden=!answers.size;
    for(const [question,answer] of answers){
      const row=document.createElement('div');row.append(node('strong',question),node('p',answer));
      const remove=node('button','Remove answer');remove.type='button';remove.onclick=()=>{answers.delete(question);questions.forEach((q,i)=>{if(q.question===question)byId('po-answer-'+i).value='';});changed();renderAnswers();};row.append(remove);list.append(row);
    }
  }
  function result(data){
    if(!data || typeof data.prompt!=='string' || data.prompt.length>48000) throw Error('Optimizer returned an invalid prompt. Your draft is unchanged.');
    byId('po-result').value=data.prompt;
    questions=(Array.isArray(data.questions)?data.questions:[]).filter(q=>q&&typeof q.question==='string'&&q.question.trim()).slice(0,3);
    const fields=byId('po-question-fields'); fields.replaceChildren();
    questions.forEach((q,i)=>{const label=node('label',q.question), answer=document.createElement('textarea');answer.id='po-answer-'+i;answer.rows=2;answer.maxLength=2000;answer.value=answers.get(q.question)||'';label.htmlFor=answer.id;fields.append(label);if(typeof q.reason==='string') fields.append(node('p',q.reason));fields.append(answer);});
    byId('po-questions').hidden=!questions.length;
    byId('po-notes').replaceChildren(...(Array.isArray(data.notes)?data.notes:[]).filter(n=>typeof n==='string').slice(0,8).map(n=>node('li',n)));
    const evidence=(Array.isArray(data.evidence)?data.evidence:[]).slice(0,8);
    byId('po-evidence').hidden=!evidence.length;
    byId('po-evidence-list').replaceChildren(...evidence.map(e=>{
      const row=document.createElement('li');
      if(typeof e==='string') row.textContent=e;
      else if(e&&typeof e==='object'){
        row.append(node('strong',String(e.name||e.path||'Source')));
        row.append(node('p',[e.kind,e.status].filter(v=>typeof v==='string').join(' · ')));
        if(typeof e.excerpt==='string') row.append(node('p',e.excerpt));
      }
      return row;
    }));
    byId('po-output').hidden=false; byId('po-append-choice').hidden=true; dirty=true; controls();
  }
  async function optimize(mode){
    if(request || !matches() || files.some(f=>!f.id) || !byId('po-draft').value.trim()) return;
    const savedRevision=revision, savedGeneration=generation, origin=key(bound), controller=new AbortController();
    request=controller; controls(); status(mode==='quick'?'Structuring your inputs without a model…':'Optimizing with the current agent…');
    try{
      const response=await fetch('/api/prompt-optimizer',{method:'POST',headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN},body:JSON.stringify(payload(mode)),signal:controller.signal});
      const data=await response.json();
      if(savedGeneration!==generation || origin!==key(current())) return;
      if(savedRevision!==revision){status('Your draft changed while this ran. The result was not applied; optimize again when ready.');return;}
      if(!response.ok) throw Error(data.error || 'The optimizer could not finish. Your draft is kept.');
      result(data); status(mode==='quick'?'Quick structure is ready. No model was used.':'Prepared prompt is ready to review and edit.');
    }catch(error){if(savedGeneration===generation && error.name!=='AbortError') status(error.message || 'Could not reach the optimizer. Your draft is kept.',true);}
    finally{if(request===controller){request=null;controls();}}
  }
  function use(append=false){
    if(!matches()){status('The session or workspace changed. Copy the prompt or start a new draft here.',true);return;}
    const prepared=byId('po-result').value.trim(); if(!prepared) return;
    const chat=byId('input');
    if(chat.value.trim()&&!append){byId('po-append-choice').hidden=false;byId('po-append').focus();return;}
    try{
      draftAttachments.adopt(files.map(({id,name,size,path,workspace,session_id})=>({id,name,size,path,workspace,session_id})));
      chat.value=chat.value.trim()?chat.value+'\n\n'+prepared:prepared;
      chat.dispatchEvent(new Event('input',{bubbles:true})); grow(); companion?.compose();
      status('Added to the chat draft. Review it before sending.');
      if(matchMedia('(max-width:999px)').matches) hide(false);
      chat.focus();
    }catch(error){status(error.message || 'Could not add to the chat draft. Nothing was replaced.',true);}
  }
  panel.addEventListener('input', changed);
  panel.addEventListener('change', e=>{if(e.target.tagName==='SELECT'||e.target.type==='checkbox')changed();});
  panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();hide();}});
  byId('po-close').onclick=()=>hide();
  byId('po-add-file').onclick=()=>byId('po-file-input').click();
  byId('po-file-input').onchange=()=>{addFiles(byId('po-file-input').files);byId('po-file-input').value='';};
  byId('po-optimize').onclick=()=>optimize('model'); byId('po-quick').onclick=()=>optimize('quick');
  byId('po-use').onclick=()=>use(); byId('po-append').onclick=()=>use(true);
  byId('po-copy').onclick=async()=>{try{await navigator.clipboard.writeText(byId('po-result').value);status('Prompt copied.');}catch{byId('po-result').focus();byId('po-result').select();status('Clipboard is unavailable. The prompt is selected for copying.');}};
  byId('po-reset').onclick=()=>{generation++;request?.abort();request=null;for(const file of files)file.controller?.abort();files=[];questions=[];answers.clear();renderAnswers();bound=current();revision++;dirty=false;panel.querySelectorAll('textarea').forEach(n=>n.value='');byId('po-output').hidden=true;byId('po-questions').hidden=true;byId('po-question-fields').replaceChildren();status('New draft ready for this session.');renderFiles();byId('po-draft').focus();};
  window.addEventListener('dream:session',()=>{
    if(!bound || (!dirty&&!files.length&&!request)){bound=current();controls();return;}
    if(key(bound)!==key(current())){generation++;request?.abort();request=null;for(const file of files){file.controller?.abort();if(!file.id)file.error='Session changed. Attach this file in a new draft.';}status('Session changed. Your original draft is kept; it will not be sent into the new session.',true);renderFiles();}
  });
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  new MutationObserver(()=>{if(!panel.hidden && (root.dataset.dreamView!=='optimizer'||!root.classList.contains('dream-design')))hide(false);}).observe(root,{attributes:true,attributeFilter:['data-dream-view','class']});
  window.PromptOptimizer={show,hide,isOpen:()=>!panel.hidden};
  controls();
})();
