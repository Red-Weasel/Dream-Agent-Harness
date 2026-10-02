/* Nested Dream, the layout (DREAM-196): the three resize edges by pointer and keyboard (ARIA separators, bounded),
   the orchestrator's side, pinning by drag-and-drop, the card conveyor (arrows, an auto-scroll that holds still under
   the pointer or the focus, discrete 4 s steps under reduced motion) and Reset layout. It reads and writes the layout
   nested.js keeps and persists; the page works the same when storage is refused. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const N=window.DreamNested; if(!N||!N.layout) return;
  const $=id=>document.getElementById(id);
  const page=$('dream-nested-page'), app=page&&page.querySelector('.nd-app'), main=page&&page.querySelector('.nd-main'), belt=page&&page.querySelector('.nd-belt'), track=$('nd-track');
  if(!page||!app||!main||!belt||!track) return;
  const BOUNDS={orchW:{min:360},pinSplit:{min:25,max:75,step:2,fallback:50},topH:{min:30,max:80,step:2,fallback:60}};
  const reduce=matchMedia('(prefers-reduced-motion: reduce)');
  const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
  const orchMax=()=>Math.max(BOUNDS.orchW.min,Math.round(app.getBoundingClientRect().width-8-480));

  // --- sizes and side: CSS variables from the kept layout, ARIA from what is on screen -----------------------------
  function apply(){
    const L=N.layout();
    app.dataset.side=L.side;
    // The kept width goes in as it is; the stylesheet's clamp() bounds it (the page may be hidden here, with no size to clamp by).
    if(L.orchW!=null) app.style.setProperty('--nd-orch-w',Math.round(L.orchW)+'px'); else app.style.removeProperty('--nd-orch-w');
    if(L.pinSplit!=null) main.style.setProperty('--nd-pin-split',L.pinSplit+'%'); else main.style.removeProperty('--nd-pin-split');
    if(L.topH!=null) main.style.setProperty('--nd-top-h',L.topH+'%'); else main.style.removeProperty('--nd-top-h');
    aria();
  }
  function aria(){
    const L=N.layout(), orch=page.querySelector('.nd-orch'), aw=app.getBoundingClientRect().width;
    const set=(h,now,min,max)=>{ if(!h) return; h.setAttribute('aria-valuemin',min); h.setAttribute('aria-valuemax',max); h.setAttribute('aria-valuenow',now); h.setAttribute('aria-valuetext',now+'%'); };
    if(orch&&aw){ const lo=Math.round(BOUNDS.orchW.min/aw*100), hi=Math.max(lo,Math.round(orchMax()/aw*100));   // the real bounds: 360 px to the width less the workers' 488
      set($('nd-h-orch'),clamp(Math.round(orch.getBoundingClientRect().width/aw*100),lo,hi),lo,hi); }
    set($('nd-h-pin'),Math.round(L.pinSplit??BOUNDS.pinSplit.fallback),25,75);   // whole percents: a pointer drag keeps a decimal
    set($('nd-h-top'),Math.round(L.topH??BOUNDS.topH.fallback),30,80);
  }
  const keyOf=h=>({'nd-h-orch':'orchW','nd-h-pin':'pinSplit','nd-h-top':'topH'})[h.id]||null;
  function fromPointer(key,x,y){
    if(key==='orchW'){ const r=app.getBoundingClientRect(); return clamp(Math.round(app.dataset.side==='right'?r.right-x:x-r.left),BOUNDS.orchW.min,orchMax()); }
    if(key==='pinSplit'){ const r=$('nd-pinned').getBoundingClientRect(); return +clamp((x-r.left-16)/Math.max(1,r.width-40)*100,25,75).toFixed(1); }
    const r=main.getBoundingClientRect(); return +clamp((y-r.top)/Math.max(1,r.height)*100,30,80).toFixed(1);
  }
  page.addEventListener('pointerdown',e=>{
    const h=e.target.closest&&e.target.closest('.nd-handle'), key=h&&keyOf(h); if(!key) return;
    e.preventDefault(); h.setPointerCapture(e.pointerId); h.classList.add('nd-drag');
    const move=ev=>{ N.setLayout(key,fromPointer(key,ev.clientX,ev.clientY)); apply(); };
    const up=()=>{ h.classList.remove('nd-drag'); h.removeEventListener('pointermove',move); h.removeEventListener('pointerup',up); h.removeEventListener('pointercancel',up); };
    h.addEventListener('pointermove',move); h.addEventListener('pointerup',up); h.addEventListener('pointercancel',up);
  });
  page.addEventListener('keydown',e=>{
    const h=e.target.closest&&e.target.closest('.nd-handle'), key=h&&keyOf(h); if(!key) return;
    const horizontal=key!=='topH', back=horizontal?'ArrowLeft':'ArrowUp', fwd=horizontal?'ArrowRight':'ArrowDown';
    if(e.key!==back&&e.key!==fwd) return;
    e.preventDefault(); e.stopPropagation();
    let dir=e.key===fwd?1:-1; if(key==='orchW'&&app.dataset.side==='right') dir=-dir;
    if(key==='orchW'){ const cur=page.querySelector('.nd-orch').getBoundingClientRect().width; N.setLayout('orchW',clamp(Math.round(cur+dir*(e.shiftKey?64:16)),BOUNDS.orchW.min,orchMax())); }
    else { const b=BOUNDS[key], cur=N.layout()[key]??b.fallback; N.setLayout(key,clamp(cur+dir*(e.shiftKey?8:b.step),b.min,b.max)); }
    apply();
  });
  $('nd-side').onclick=()=>{ N.setLayout('side',app.dataset.side==='right'?'left':'right'); apply(); };
  new ResizeObserver(aria).observe(app);
  N.subscribe(aria);

  // --- pinning by drag-and-drop: a card dropped on a window takes its slot ----------------------------------------
  page.addEventListener('dragstart',e=>{ const c=e.target.closest&&e.target.closest('.nd-card[draggable=true]'); if(!c) return; e.dataTransfer.setData('text/plain','worker:'+c.dataset.runId); e.dataTransfer.effectAllowed='move'; });
  page.addEventListener('dragover',e=>{ const w=e.target.closest&&e.target.closest('.nd-win[data-slot]'); if(!w) return; e.preventDefault(); w.classList.add('nd-over'); });
  page.addEventListener('dragleave',e=>{ const w=e.target.closest&&e.target.closest('.nd-win[data-slot]'); if(w&&!w.contains(e.relatedTarget)) w.classList.remove('nd-over'); });
  page.addEventListener('drop',e=>{ const w=e.target.closest&&e.target.closest('.nd-win[data-slot]'); if(!w) return; e.preventDefault(); w.classList.remove('nd-over'); const d=e.dataTransfer.getData('text/plain'); if(d.startsWith('worker:')) N.pin(d.slice(7),+w.dataset.slot); });

  // --- the conveyor: arrows, and an auto-scroll that holds still under the pointer or the focus -------------------
  const autoBtn=$('nd-auto');
  let auto=false, hover=false, focusIn=false, manualUntil=0, last=0, stepT=0, pos=0, raf=0;
  const stepPx=()=>(parseInt(getComputedStyle(page).getPropertyValue('--nd-card-w'))||320)+16;
  function frame(t){
    raf=0; if(!auto) return;
    const dt=last?Math.min(64,t-last):16; last=t;
    const max=track.scrollWidth-track.clientWidth;
    if(!page.hidden&&max>0&&!hover&&!focusIn&&t>manualUntil){
      if(Math.abs(track.scrollLeft-Math.round(pos))>1) pos=track.scrollLeft;   // the owner scrolled: carry on from there
      // Reduced motion: one card-sized step every 4 s and nothing in between; otherwise a slow steady drift.
      if(reduce.matches){ stepT+=dt; if(stepT>=4000){ stepT=0; pos=pos>=max-1?0:Math.min(max,pos+stepPx()); track.scrollLeft=Math.round(pos); } }
      else { pos=pos>=max-1?0:Math.min(max,pos+dt*0.03); track.scrollLeft=Math.round(pos); }
    }
    raf=requestAnimationFrame(frame);
  }
  function setAuto(on){ auto=on; autoBtn.setAttribute('aria-pressed',String(on)); track.classList.toggle('nd-auto',on); last=0; stepT=0; pos=track.scrollLeft; if(on&&!raf) raf=requestAnimationFrame(frame); }
  belt.addEventListener('pointerenter',()=>{ hover=true; }); belt.addEventListener('pointerleave',()=>{ hover=false; });
  belt.addEventListener('focusin',()=>{ focusIn=true; }); belt.addEventListener('focusout',e=>{ if(!belt.contains(e.relatedTarget)) focusIn=false; });
  autoBtn.onclick=()=>setAuto(!auto);
  const nudge=d=>{ manualUntil=performance.now()+800; track.scrollBy({left:d*stepPx(),behavior:reduce.matches?'auto':'smooth'}); };
  $('nd-prev').onclick=()=>nudge(-1); $('nd-next').onclick=()=>nudge(1);
  // With nothing to scroll the arrows and Auto-scroll are off, and an auto-scroll that was on stops.
  function conveyor(){ if(page.hidden||!track.clientWidth) return;   // hidden, the track measures 0 x 0: no verdict on what can scroll
    const can=track.scrollWidth>track.clientWidth+1; $('nd-prev').disabled=$('nd-next').disabled=autoBtn.disabled=!can; if(!can&&auto) setAuto(false); }
  new ResizeObserver(conveyor).observe(track);
  N.subscribe(conveyor);
  conveyor();

  $('nd-reset').onclick=()=>{ N.resetLayout(); setAuto(false); apply(); };
  apply();
  window.DreamNestedLayout={apply,setAuto,isAuto:()=>auto};
})();
