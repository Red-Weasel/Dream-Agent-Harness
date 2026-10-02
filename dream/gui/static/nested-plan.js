/* Nested Dream, the plan tracker (DREAM-198): the plan the orchestrator keeps with its update_plan tool
   (tools/project.py emits Event('plan', {title, phases[{name, status, summary, steps[{name, status}]}], path,
   updated_at}); the statuses are pending, in_progress and done), read from the chat's dream:event hook live and on
   history replay, like nested.js. Two rows: the current phase with its steps, then Next; "All phases" is a disclosure
   over the whole list, at most five rows tall. Nothing shows without a plan, and no worker sits under a phase: a
   plan event names none. Its own container in the orchestrator column, above the composer; nested.js is not touched. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const page=document.getElementById('dream-nested-page'); if(!page) return;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const CLASS={pending:'nd-pending',in_progress:'nd-now',done:'nd-done'};
  const clip=(v,n)=>String(v??'').slice(0,n);
  const MAX=64;
  let plan=null, all=false;
  // The event's phases, bounded and with only what the tracker shows; null when there are none.
  function take(d){
    const phases=d&&typeof d==='object'&&Array.isArray(d.phases)?d.phases.slice(0,MAX):[];
    const out=[];
    for(const p of phases){
      if(!p||typeof p!=='object') continue;
      const steps=(Array.isArray(p.steps)?p.steps.slice(0,MAX):[]).filter(s=>s&&typeof s==='object')
        .map(s=>({name:clip(s.name,200),status:CLASS[s.status]?s.status:'pending'}));
      out.push({name:clip(p.name,200),status:CLASS[p.status]?p.status:'pending',summary:clip(p.summary,1000),steps});
    }
    return out.length?{phases:out}:null;
  }
  const box=document.createElement('section');
  box.className='nd-plan'; box.id='nd-plan'; box.setAttribute('aria-label','Plan'); box.hidden=true;
  function mount(){
    if(box.isConnected) return true;
    const orch=page.querySelector('.nd-orch'); if(!orch) return false;
    const composer=orch.querySelector('.nd-composer');
    if(composer) orch.insertBefore(box,composer); else orch.append(box);
    return true;
  }
  // The current phase: the one in progress, else the first not done; -1 once every phase is done.
  function current(ph){ const i=ph.findIndex(p=>p.status==='in_progress'); return i>=0?i:ph.findIndex(p=>p.status!=='done'); }
  const stepsHTML=p=>p.steps.length?`<span class="nd-psteps">${p.steps.map(s=>`<span class="nd-pstep ${CLASS[s.status]}"><i class="nd-pdot" aria-hidden="true"></i>${esc(s.name)}</span>`).join('')}</span>`:'';
  function render(){
    if(!mount()) return;
    // The button is rebuilt with the rows: if it had the focus (a plan update lands while the owner is on it), it keeps it.
    const had=document.activeElement&&document.activeElement.id==='nd-plan-toggle';
    if(!plan){ box.hidden=true; box.innerHTML=''; return; }
    const ph=plan.phases, n=ph.length, cur=current(ph), c=cur>=0?ph[cur]:null, nx=cur>=0?ph[cur+1]:null;
    const where=c?`Phase ${cur+1} of ${n}`:`All ${n} phase${n===1?'':'s'} done`;
    const toggle=`<button type="button" class="nd-ptog" id="nd-plan-toggle" aria-expanded="${all}" aria-controls="nd-plan-all">${all?'Current only':'All phases'}</button>`;
    box.innerHTML=`<div class="nd-pnow"><span class="nd-pk">${where}</span>${c&&!all?`<span class="nd-pt">${esc(c.name)}</span>${stepsHTML(c)}`:''}${toggle}</div>`
      +(nx&&!all?`<div class="nd-pnext"><span class="nd-pk">Next</span><span class="nd-pt">${esc(nx.name)}</span></div>`:'')
      +`<ol class="nd-phases" id="nd-plan-all" aria-label="All phases"${all?'':' hidden'}>`
      +ph.map((p,i)=>`<li class="nd-ph ${i===cur?'nd-now':CLASS[p.status]}"${i===cur?' aria-current="step"':''}${p.status==='done'&&p.summary?` title="${esc(p.summary)}"`:''}><span class="nd-pn">${i+1}</span><span class="nd-pt">${esc(p.name)}</span>${i===cur?stepsHTML(p):''}</li>`).join('')
      +'</ol>';
    box.hidden=false;
    if(had) box.querySelector('#nd-plan-toggle')?.focus({preventScroll:true});
    align();
  }
  // The open list keeps the current phase in view (five rows show at most), scrolled to a row boundary: the row
  // before the current one sits at the top, so the current phase is whole and has one row of context above it.
  function align(){
    if(!all||box.hidden) return;
    const list=box.querySelector('#nd-plan-all'), li=list&&list.querySelector('.nd-ph[aria-current]');
    if(!li||!list.clientHeight) return;
    const top=li.offsetTop, h=list.clientHeight;
    if(top<list.scrollTop||top+li.offsetHeight>list.scrollTop+h) list.scrollTop=(li.previousElementSibling||li).offsetTop;
  }
  box.addEventListener('click',e=>{
    if(!e.target.closest('#nd-plan-toggle')) return;
    all=!all; render();
    box.querySelector('#nd-plan-toggle')?.focus({preventScroll:true});
  });
  window.addEventListener('dream:event',e=>{
    const m=e.detail&&e.detail.m; if(!m) return;
    try{
      if(m.kind==='plan'){ plan=take(m.data); render(); }
      else if(m.kind==='history'){ plan=null; render(); }   // the replayed events rebuild it
    }catch(err){ console.error(err); }
  });
  // nested.js renders on every event and on showing the view: re-mount if the column was rebuilt, align the list.
  window.DreamNested?.subscribe(()=>{ if(mount()) align(); });
  mount();
})();
