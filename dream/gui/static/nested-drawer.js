/* Nested Dream, the All-agents drawer and the keyboard (DREAM-195). It reads the state nested.js keeps
   (window.DreamNested) and holds only what is open and which filter is on: the list of every worker with counts,
   one worker's whole retained transcript, Pin, Pause and Stop (DREAM-192), a worker's waiting requests with their
   choices and a failed or stopped worker's retry (DREAM-194), a message box to that worker (DREAM-193), Esc with focus
   return, inert while closed; the digits open worker N through a two-digit buffer, `a` toggles the drawer (also from
   inside it; never from inside the Customize panel or a text box), `i` cycles the workers that need the owner.
   Opening it closes the Customize panel. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const N=window.DreamNested; if(!N) return;
  const root=document.documentElement, $=id=>document.getElementById(id);
  const page=$('dream-nested-page'), drawer=$('nd-drawer'), btn=$('nd-drawer-btn');
  if(!page||!drawer||!btn) return;
  const {WORD,esc,tail,written,thinking,stateOf}=N;
  const FILTERS=[['all','All'],['needs','Needs you'],['working','Working'],['failed','Failed or stopped'],['idle','Idle']];
  // A worker with a request waiting shows as Needs you (DREAM-194), so it matches that filter and no other but All. An
  // unknown outcome ended without a clean finish: it is listed with the failed and the stopped, as itself.
  const matches=(l,f)=>{ const st=stateOf(l); return f==='all'||(f==='working'?['working','verifying','pausing','stopping'].includes(st)
    :f==='failed'?['failed','stopped','unknown'].includes(st):f==='idle'?['done','queued','paused'].includes(st):st===f); };
  const D={open:false,filter:'all',agent:null,opener:null};   // opener: the control that opened the drawer, by id or data-fk
  drawer.innerHTML=`<div class="nd-dhead" id="nd-dhead"><div class="nd-wrow"><h2 id="nd-drawer-title">All agents</h2><span class="nd-dcount nd-num" id="nd-dcount"></span><button type="button" class="nd-iconbtn nd-close" id="nd-drawer-close" data-fk="drawer-close" aria-label="Close all agents">×</button></div><div class="nd-filters" id="nd-filters" role="group" aria-label="Show"></div></div><div class="nd-dlist" id="nd-dlist"></div><div class="nd-detail" id="nd-detail" hidden></div>`;
  const dhead=$('nd-dhead'), dlist=$('nd-dlist'), detail=$('nd-detail'), filters=$('nd-filters'), dcount=$('nd-dcount');
  const pct=l=>Math.round(100*l.context.used/l.context.window);
  // A run id's head names it (a uuid); every Claude run's starts "claude-toolu", so its tail does (DREAM-212).
  const runTag=l=>N.readOnly(l)?`claude-…${l.id.slice(-8)}`:l.id.slice(0,12);
  const empty=all=>all.length?'No workers match this filter. Choose All to see every worker.'
    :N.claude()?"No workers yet. Claude's sub-agents appear here as read-only cards once one runs, and its local helpers as full cards."
    :(N.silent&&N.silent())?'This provider does not report worker activity, so there is nothing to list here.'
    :'No workers yet. Workers appear when the orchestrator delegates with its task tool during a turn.';

  function keepFocus(fn){const a=document.activeElement,k=a&&a.dataset?a.dataset.fk:null;fn();if(k){const n=drawer.querySelector(`[data-fk="${k}"]`);if(n&&n!==document.activeElement)n.focus({preventScroll:true});}}
  function render(){
    drawer.classList.toggle('nd-open',D.open); drawer.inert=!D.open; drawer.setAttribute('aria-hidden',String(!D.open));
    btn.setAttribute('aria-expanded',String(D.open));
    if(!D.open){ detail.hidden=true; delete detail.dataset.runId; dhead.hidden=false; dlist.hidden=false; return; }
    const all=N.order(), agent=D.agent?N.lane(D.agent):null, collapsed=!!D.agent&&!agent;
    if(collapsed) D.agent=null;                                          // gone with a history reset: back to the list
    dhead.hidden=!!agent; dlist.hidden=!!agent; detail.hidden=!agent;
    if(agent){ renderDetail(agent); return; }
    delete detail.dataset.runId;
    const w=all.filter(l=>matches(l,'working')).length, nn=all.filter(l=>stateOf(l)==='needs').length;
    dcount.textContent=`${all.length} worker${all.length===1?'':'s'} · ${w} working${nn?` · ${nn} ${nn===1?'needs':'need'} you`:''}`;
    filters.innerHTML=FILTERS.map(([f,label])=>`<button type="button" data-f="${f}" data-fk="filter-${f}" aria-pressed="${String(f===D.filter)}">${label} <span class="nd-num">${all.filter(l=>matches(l,f)).length}</span></button>`).join('');
    const rows=all.filter(l=>matches(l,D.filter));
    dlist.innerHTML=rows.map(l=>{ const st=stateOf(l); return `<button type="button" class="nd-item nd-${st}" data-run-id="${esc(l.id)}" data-fk="row-${l.n}" aria-label="${l.n} ${esc(l.agent)}, ${WORD[st]}">
        <span class="nd-pip nd-${st}" aria-hidden="true">${l.n}</span>
        <span class="nd-l1" aria-hidden="true"><span class="nd-nm">${esc(l.agent)}</span>${l.model?`<span class="nd-md">${esc(l.model)}</span>`:''}</span>
        <span class="nd-imeta" aria-hidden="true"><span class="nd-state">${WORD[st]}</span>${N.clockHTML(l)}${l.context?`<span class="nd-num" title="Context used">${pct(l)}%</span>`:''}</span>
        <span class="nd-l2" aria-hidden="true">${esc(tail(written(l)||thinking(l)||l.text,160))}</span></button>`; }).join('')
      ||`<p class="nd-muted nd-dempty">${empty(all)}</p>`;
    if(collapsed) $('nd-drawer-close').focus({preventScroll:true});
  }
  const entryHTML=e=>e.kind==='status'?`<p class="nd-entry nd-e-status"><span class="nd-badge">${WORD[e.state]||esc(e.state)}</span> ${esc(e.text)}</p>`
    :e.kind==='request'||e.kind==='response'?`<p class="nd-entry nd-e-req">${esc(e.text)}</p>`
    :e.kind==='thinking'?`<div class="nd-entry nd-e-think"><span class="nd-think-label">Thinking</span><p class="nd-full">${esc(e.text)}</p></div>`
    :e.kind==='message'?`<p class="nd-entry nd-e-msg"><span class="nd-badge">Your message</span> ${esc(e.text)}</p>`   // P6: taken, or not delivered
    :e.kind==='tool'?`<div class="nd-entry nd-tool"><span class="nd-tool-name">${esc(e.row.name)}</span><span class="nd-tool-args">${esc(e.row.args)}</span><span class="nd-badge">${esc(e.row.badge)}</span>${e.row.out?`<span class="nd-tool-out">${esc(e.row.out)}</span>`:''}</div>`
    :`<p class="nd-entry nd-stream">${esc(e.text)}</p>`;
  // One worker's detail keeps its frame and its composer (P6) while it shows that worker: a draft survives the renders.
  function renderDetail(l){
    const same=detail.dataset.runId===l.id;
    if(!same){ detail.dataset.runId=l.id; detail.innerHTML='<div class="nd-dtop"></div><div class="nd-dask"></div><div class="nd-dbody"></div><div class="nd-dacts"></div>'; detail.append(N.sayNode(l,'d')); }
    const b=detail.querySelector('.nd-dbody'), stick=b.scrollHeight-b.scrollTop-b.clientHeight<48, st=b.scrollTop;
    const here=N.pins().includes(l.id), s=stateOf(l), part=c=>detail.querySelector(c);
    // A worker that needs the owner shows its waiting requests with their choices (keys 1-9 answer them here too);
    // a failed or stopped one offers the retry through the orchestrator (DREAM-194).
    part('.nd-dtop').innerHTML=`<button type="button" class="nd-back" id="nd-back" data-fk="back">‹ All agents</button>
        <div class="nd-wrow nd-${s}"><span class="nd-pip nd-${s}" aria-hidden="true">${l.n}</span><span class="nd-nm">${esc(l.agent)}</span><span class="nd-rt"><span class="nd-state">${WORD[s]}</span>${N.clockHTML(l)}<button type="button" class="nd-iconbtn nd-close" id="nd-detail-close" data-fk="detail-close" aria-label="Close all agents">×</button></span></div>
        <p class="nd-headline">${esc(l.text)}</p>${N.noteHTML(l)}
        <div class="nd-dfacts">${l.model?`<span>Model <b>${esc(l.model)}</b></span>`:''}${l.context?`<span>Context <b>${pct(l)}% · ${l.context.used.toLocaleString()}/${l.context.window.toLocaleString()}</b></span>`:''}${l.usage&&l.usage.completion!==null&&l.duration>0?`<span>Reply <b>${(l.usage.completion/(l.duration/1000)).toFixed(1)} tok/s</b></span>`:''}<span title="${esc(l.id)}">Run <b>${esc(runTag(l))}</b></span></div>`;
    part('.nd-dask').innerHTML=s==='needs'?N.asksHTML(l,'d'):''; part('.nd-dask').hidden=s!=='needs';
    b.innerHTML=`${l.omitted?`<p class="nd-muted">${l.omitted} earlier entries are no longer retained.</p>`:''}${l.log.map(entryHTML).join('')||'<p class="nd-muted">Nothing recorded for this worker yet.</p>'}`;
    part('.nd-dacts').innerHTML=`${N.controlsHTML(l,'d')}<button type="button" class="nd-pill" id="nd-pin-btn" data-fk="pin" aria-pressed="${String(here)}">${here?'Unpin':'Pin to a window'}</button>${l.state==='failed'||l.state==='stopped'?N.retryHTML(l,'d'):''}`;
    b.scrollTop=(!same||stick)?b.scrollHeight:st;
    N.syncSay(l,'d');
  }

  // --- opening, closing, focus -----------------------------------------------------------------------------------------
  function rememberOpener(){ const a=document.activeElement; D.opener=a&&a!==document.body&&page.contains(a)&&!drawer.contains(a)?{id:a.id||'',fk:a.dataset.fk||''}:null; }
  function restoreFocus(){ const o=D.opener; let n=null; if(o){ if(o.fk) n=page.querySelector(`[data-fk="${CSS.escape(o.fk)}"]`); if(!n&&o.id) n=$(o.id); } (n||btn).focus({preventScroll:true}); }
  function open(id){
    const l=N.lane(id); if(!l) return false;
    if(!D.open) rememberOpener();
    if(!matches(l,D.filter)) D.filter='all';
    window.DreamNestedCustomize?.close(false);                           // never under the Customize panel (DREAM-200)
    D.agent=id; D.open=true; render();
    // The list (and the row that was clicked) is hidden now; focus lands on Back unless it is already in the detail.
    const b=$('nd-back'); if(b&&!detail.contains(document.activeElement)) b.focus({preventScroll:true});
    return true;
  }
  function back(){
    const id=D.agent; D.agent=null; render();
    const row=id?dlist.querySelector(`.nd-item[data-run-id="${CSS.escape(id)}"]`):null;
    (row||$('nd-drawer-close')).focus({preventScroll:true});
  }
  function close(){ cancelDigits(); if(!D.open) return; D.open=false; D.agent=null; render(); restoreFocus(); }
  function toggle(){ cancelDigits(); if(D.open){ close(); return; } rememberOpener(); window.DreamNestedCustomize?.close(false); D.agent=null; D.open=true; render(); $('nd-drawer-close').focus({preventScroll:true}); }
  function cycle(kind){
    const v=N.order().filter(l=>kind==='failed'?l.state==='failed'||l.state==='stopped':stateOf(l)===kind);
    if(!v.length) return false;
    const i=v.findIndex(l=>l.id===D.agent);
    open(v[(i+1)%v.length].id); return true;
  }
  drawer.addEventListener('click',e=>{
    const t=e.target;
    if(t.closest('#nd-drawer-close,#nd-detail-close')){ close(); return; }
    if(t.closest('#nd-back')){ back(); return; }
    const f=t.closest('#nd-filters [data-f]'); if(f){ D.filter=f.dataset.f; keepFocus(render); return; }
    const row=t.closest('.nd-item[data-run-id]'); if(row){ open(row.dataset.runId); return; }
    if(t.closest('#nd-pin-btn')&&D.agent){ if(N.pins().includes(D.agent)) N.unpin(D.agent); else N.pin(D.agent); }
  });
  btn.onclick=toggle;
  N.subscribe(()=>{ if(D.open) keepFocus(render); });

  // --- the keyboard: only on the Nested view; Escape anywhere on it, the rest never inside a text box ------------------
  let buf='', bufAt=0, timer=0;
  function cancelDigits(){ clearTimeout(timer); timer=0; buf=''; }
  function digit(k){
    const now=performance.now(); buf=now-bufAt<600?buf+k:k; bufAt=now; clearTimeout(timer); timer=0;
    const all=N.order(), n=Number(buf);
    if(!n||n>all.length){ buf=''; return; }
    if(n*10<=all.length) timer=setTimeout(()=>{buf='';timer=0;open(all[n-1].id);},600);   // a second digit could still name a worker
    else { buf=''; open(all[n-1].id); }
  }
  const typing=t=>!!t&&(t.isContentEditable||(t.matches&&t.matches('input,textarea,select')));
  document.addEventListener('keydown',e=>{
    if(root.dataset.dreamView!=='nested'||page.hidden||e.ctrlKey||e.metaKey||e.altKey||e.isComposing||e.repeat) return;
    if(e.key==='Escape'){
      const pending=!!timer; cancelDigits();                          // a digit still waiting for its second is dropped
      if(!D.open){ if(pending){ e.preventDefault(); e.stopPropagation(); } return; }
      e.preventDefault(); e.stopPropagation(); if(D.agent) back(); else close(); return;
    }
    if(typing(e.target)) return;
    if(/^[0-9]$/.test(e.key)){ if(e.target.closest&&e.target.closest('.nd-ask[data-ask]')) return;   // a focused request answers them (nested.js)
      e.preventDefault(); e.stopPropagation(); digit(e.key); return; }   // digits as typed: a Shift on an AZERTY row still counts
    if(e.shiftKey) return;
    if(e.key==='a'){ if(e.target.closest&&e.target.closest('#nd-custom')) return;   // never from inside the Customize panel; inside the drawer it closes it (the mockup's toggle)
      e.preventDefault(); e.stopPropagation(); toggle(); return; }
    if(e.key==='i'&&cycle('needs')){ e.preventDefault(); e.stopPropagation(); }
  },true);
  render();
  window.DreamNestedDrawer={open,close,toggle,back,cycle,isOpen:()=>D.open};
})();
