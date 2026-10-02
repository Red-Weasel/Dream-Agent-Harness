/* Nested Dream, the orchestrator's footer and the Agents / Workflows tabs (DREAM-199). Under the composer, the row
   the chat's footer has: the permission-mode chip, Read ×2, then the last request's speeds and context. The two
   controls are the chat's own (#chat-mode, which asks tui/app.py's 'permission_mode' through /api/control, and
   #read-twice, the stored preference the chat's composer sends as read_twice on /api/prompt): this row mirrors them
   and clicks them, so there is one request path and one truth, never a second copy of the state. The numbers come
   from real `stats` events, computed as the chat's perf() does (index.html), and stay blank until one arrives. The
   tabs follow the ARIA tabs pattern (roving tabindex, the arrows, Home and End) over the workers area nested.js
   renders and the Workflows panel of nested-workflows.js. Own containers only; nested.js is not touched. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const $=id=>document.getElementById(id);
  const page=$('dream-nested-page'); if(!page) return;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  // --- the footer row ------------------------------------------------------------------------------------------------
  const foot=document.createElement('div'); foot.className='nd-foot'; foot.id='nd-foot';
  foot.innerHTML='<button type="button" class="nd-chip" id="nd-mode" aria-live="polite" title="Permission mode. Click to cycle to the next mode." hidden></button>'
    +'<button type="button" class="nd-chip" id="nd-read-twice" aria-pressed="false" title="Read ×2: the model reads each message you type twice (only your words, never the history) (applies to the chat composer for now)">Read ×2</button>'
    +'<span class="nd-perf" id="nd-perf" title="Last request: prompt reading speed, generation speed, and how full the context window is"></span>';
  const modeChip=foot.querySelector('#nd-mode'), readChip=foot.querySelector('#nd-read-twice'), perfEl=foot.querySelector('#nd-perf');
  function mountFoot(){
    if(foot.isConnected) return true;
    const composer=page.querySelector('.nd-orch .nd-composer'); if(!composer) return false;
    composer.append(foot); return true;
  }
  // The row gives way from the left when the column is narrow: prefill, then decode, then the context wraps.
  let raf=0;
  function fit(){
    if(raf) return;
    raf=requestAnimationFrame(()=>{
      raf=0; if(!foot.isConnected||page.hidden) return;
      foot.classList.remove('nd-tight1','nd-tight2','nd-tight3');
      for(const c of ['nd-tight1','nd-tight2','nd-tight3']){ if(foot.scrollWidth<=foot.clientWidth+1) break; foot.classList.add(c); }
    });
  }
  new ResizeObserver(fit).observe(foot);
  // The mode chip: the chat's control, mirrored. Hidden until Dream has confirmed a mode (the chat's data-mode); from
  // then on it says what the chat's button says, including an unconfirmed cycle.
  const chatMode=$('chat-mode'); let confirmed=false;
  function syncMode(){
    if(chatMode.dataset.mode) confirmed=true;
    modeChip.hidden=!confirmed;
    modeChip.textContent=chatMode.textContent;
    if(chatMode.dataset.mode) modeChip.dataset.mode=chatMode.dataset.mode; else delete modeChip.dataset.mode;
    modeChip.disabled=chatMode.disabled;
    fit();
  }
  if(chatMode){
    new MutationObserver(syncMode).observe(chatMode,{childList:true,characterData:true,subtree:true,attributes:true,attributeFilter:['data-mode','disabled']});
    syncMode();
  }
  modeChip.onclick=()=>chatMode?.click();
  // Read ×2: the chat's toggle, mirrored; a click here is a click there.
  const chatRead=$('read-twice');
  const syncRead=()=>readChip.setAttribute('aria-pressed',chatRead.getAttribute('aria-pressed')==='true'?'true':'false');
  if(chatRead){ new MutationObserver(syncRead).observe(chatRead,{attributes:true,attributeFilter:['aria-pressed']}); syncRead(); }
  else readChip.hidden=true;
  readChip.onclick=()=>chatRead?.click();
  // The last request's speed and context use, as the chat's perf() computes them: prefill over the tokens really read
  // (prompt minus cached), decode over the generated ones, context as prompt / window. Nothing until a stats event.
  const num=v=>typeof v==='number'&&Number.isFinite(v)?v:0;
  function perf(s){
    const parts=[];
    if(s&&typeof s==='object'){
      const fresh=Math.max(0,num(s.prompt_tokens??s.pp_n)-num(s.cached));
      if(num(s.pp_ms)>0&&fresh>0) parts.push(['nd-pp',`prefill ${Math.round(fresh*1000/s.pp_ms).toLocaleString()} tok/s`]);
      if(num(s.gen_ms)>0&&num(s.gen_n)>0) parts.push(['nd-dd',`decode ${(s.gen_n*1000/s.gen_ms).toFixed(1)} tok/s`]);
      if(num(s.prompt_tokens)>0&&num(s.window)>0)
        parts.push(['nd-cx',`context ${s.prompt_tokens.toLocaleString()} / ${s.window.toLocaleString()}${num(s.cached)?` (${Math.round(s.cached/1000)}K cached)`:''}`]);
    }
    perfEl.innerHTML=parts.map(([c,t],i)=>`<span class="${c}">${esc(t)}${i<parts.length-1?' · ':''}</span>`).join('');
    fit();
  }

  // --- the tabs --------------------------------------------------------------------------------------------------------
  const tabs=document.createElement('div'); tabs.className='nd-tabs'; tabs.setAttribute('role','tablist'); tabs.setAttribute('aria-label','Views');
  tabs.innerHTML='<button type="button" role="tab" id="nd-tab-agents" aria-selected="true" aria-controls="nd-agents" tabindex="0">Agents</button>'
    +'<button type="button" role="tab" id="nd-tab-workflows" aria-selected="false" aria-controls="nd-workflows" tabindex="-1">Workflows</button>';
  const VIEWS=['agents','workflows']; let view='agents';
  function mountTabs(){
    if(tabs.isConnected) return true;
    const top=page.querySelector('.nd-top'); if(!top) return false;
    const title=top.querySelector('.nd-title'); if(title) title.after(tabs); else top.prepend(tabs);
    return true;
  }
  // The workers area nested.js renders is the Agents panel: it gets the id and the roles the tabs point at, nothing else.
  function panels(){
    const main=page.querySelector('.nd-main');
    if(main){ if(!main.id) main.id='nd-agents'; main.setAttribute('role','tabpanel'); main.setAttribute('aria-labelledby','nd-tab-agents'); }
    return {main,wf:$('nd-workflows')};
  }
  function select(v,focus){
    view=v; const {main,wf}=panels();
    for(const b of tabs.querySelectorAll('[role=tab]')){
      const on=b.id==='nd-tab-'+v;
      b.setAttribute('aria-selected',String(on)); b.tabIndex=on?0:-1;
      if(on&&focus) b.focus({preventScroll:true});
    }
    if(main) main.hidden=v!=='agents';
    if(wf) wf.hidden=v!=='workflows';
    if(v!=='agents') window.DreamNestedDrawer?.close();   // the drawer lists the workers; it goes with them
  }
  tabs.addEventListener('click',e=>{ const b=e.target.closest('[role=tab]'); if(b) select(b.id.slice('nd-tab-'.length),false); });
  tabs.addEventListener('keydown',e=>{
    let i=VIEWS.indexOf(view);
    if(e.key==='ArrowRight') i=(i+1)%VIEWS.length;
    else if(e.key==='ArrowLeft') i=(i-1+VIEWS.length)%VIEWS.length;
    else if(e.key==='Home') i=0;
    else if(e.key==='End') i=VIEWS.length-1;
    else return;
    e.preventDefault(); select(VIEWS[i],true);
  });

  // --- wiring ----------------------------------------------------------------------------------------------------------
  function mountAll(){ mountFoot(); if(mountTabs()) select(view,false); }
  let session;
  window.addEventListener('dream:event',e=>{
    const m=e.detail&&e.detail.m; if(!m) return;
    try{
      if(m.kind==='stats') perf(m.data);
      else if(m.kind==='hello'){ const id=m.data&&m.data.session_id; if(id!==session){ session=id; perf(null); } }   // another session's numbers are not this one's
    }catch(err){ console.error(err); }
  });
  // nested.js renders on every event and on showing the view: re-mount if the column or the top row was rebuilt.
  window.DreamNested?.subscribe(()=>{ if(!foot.isConnected||!tabs.isConnected||!page.querySelector('.nd-main[role=tabpanel]')) mountAll(); });
  mountAll();
})();
