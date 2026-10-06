/* The desktop page without its bars (DREAM-240 for Chat and Artifacts, DREAM-242 for every view; chat-nested.css
   hides them, as nested.css does in the Nested view). What the bars held sits under the composer: the model, Effort,
   Vision and the internet switch as chips, the page's status, and a More menu (New chat, Controls, Council, Create, Context, Design,
   Checkpoints, Metrics). Every one mirrors the page's own control and clicks it, so there is one path and one truth
   for each. Beside an open preview the chat is a column with a resize edge: by pointer and by the arrow keys, bounded,
   remembered in this browser (the page is the same with storage refused). Presentation only. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const $=id=>document.getElementById(id), root=document.documentElement, body=document.body;
  const hint=document.querySelector('footer .hint'), footer=document.querySelector('body>footer'), main=$('main'), panel=$('artpanel'), split=document.querySelector('.split');
  if(!hint||!footer||!main||!panel||!split) return;
  const el=(tag,cls,attrs={})=>{ const n=document.createElement(tag); if(cls) n.className=cls; for(const [k,v] of Object.entries(attrs)) n.setAttribute(k,v); return n; };

  // --- the chips: the work bar's Effort and Vision, the header's internet switch and status, mirrored -------------
  const mirror=(chip,source,sync)=>{ new MutationObserver(sync).observe(source,{childList:true,characterData:true,subtree:true,attributes:true}); sync(); chip.onclick=()=>source.click(); };
  const perf=$('perf'), before=n=>{ if(perf&&perf.parentNode===hint) hint.insertBefore(n,perf); else hint.append(n); };
  for(const [id,theirs,title] of [['cn-model','dream-model','The model of this session. Click to open Council, where the main model changes between turns.'],['cn-effort','dream-effort','Reasoning effort for this session. Click to open Controls.'],['cn-vision','dream-vision','Image input for this session. Click to open Controls.']]){
    const source=$(theirs); if(!source) continue;
    const chip=el('button','cn-chip',{type:'button',id,title}); before(chip);
    mirror(chip,source,()=>{ chip.textContent=source.textContent; chip.hidden=/unreported$/i.test(source.textContent); });
  }
  const net=$('netpill');
  if(net){
    const chip=el('button','cn-chip',{type:'button',id:'cn-net'}); chip.append(el('span','cn-dot'),'internet'); before(chip);
    mirror(chip,net,()=>{ chip.dataset.state=net.classList.contains('off')?'off':'on'; chip.title=net.title; chip.setAttribute('aria-pressed',String(!net.classList.contains('off'))); });
  }

  // --- what must stay in sight while the bars are not: a screen recording in progress (the header's indicator) and,
  //     away from the conversation, a waiting approval (the work bar's "Approval waiting"). First in the row.
  // Where a view has no composer (Projects, Skills, Sleepwalk) the pair floats at the work area's lower right.
  const float=el('div','',{id:'cn-float'}); split.append(float);
  for(const [name,theirs] of [['approval','dream-approval-return'],['recording','dream-recording']]){
    const source=$(theirs); if(!source) continue;
    for(const [id,home] of [['cn-'+name,hint],['cn-'+name+'-float',float]]){
      const chip=el('button','cn-chip cn-'+name,{type:'button',id}); home.prepend(chip);
      mirror(chip,source,()=>{ chip.textContent=source.textContent; chip.hidden=source.hidden; chip.title=source.title; chip.classList.toggle('cn-unknown',source.classList.contains('dc-unknown')); });
    }
  }

  // --- the More menu: a row clicks the page's own control; a click outside or Escape closes it ------------------
  const MENU=[['New chat','studio-new'],['Controls','dream-controls-open'],['Council','dream-council-open'],['Create','dream-create-open'],
    ['Context','dream-inspect-toggle'],['Design','studio-design'],['Checkpoints','studio-checkpoints'],['Metrics','railtoggle']];
  const menu=el('details','cn-menu',{id:'chat-menu'});
  const summary=el('summary','cn-chip',{'aria-label':'More: new chat, controls, council, create, context, design, checkpoints, metrics',title:'More'}); summary.textContent='More';
  const list=el('div','cn-menu-list',{role:'menu'});
  for(const [label,id] of MENU){ const b=el('button','',{type:'button',role:'menuitem','data-proxy':id}); b.textContent=label; list.append(b); }
  menu.append(summary,list); before(menu);
  menu.addEventListener('click',e=>{ const b=e.target.closest('[data-proxy]'); if(!b) return; menu.open=false; $(b.dataset.proxy)?.click();
    // A dialog opened from here gives the focus back here when it closes (its own opener is in a bar these views hide).
    document.querySelector('dialog[open]')?.addEventListener('close',()=>{ const a=document.activeElement; if(!a||a===body||!a.getClientRects().length) summary.focus(); },{once:true}); });
  document.addEventListener('click',e=>{ if(menu.open&&!menu.contains(e.target)) menu.open=false; });
  menu.addEventListener('keydown',e=>{ if(e.key==='Escape'&&menu.open){ e.stopPropagation(); menu.open=false; summary.focus(); } });
  // A control this page does not have (no Create on a provider without media) is not offered.
  menu.addEventListener('toggle',()=>{ if(menu.open) list.querySelectorAll('[data-proxy]').forEach(b=>{ b.hidden=!$(b.dataset.proxy); }); });

  // --- the side drawer (Design, Checkpoints, Metrics) or the Context panel, opened from a More menu, gives the focus
  //     back to that menu when it closes: its own opener is in a bar the view hides, so the focus would be lost. The
  //     Nested view's menu (nested.js) gets the same. Only after a menu opened it: a drawer that closes for another
  //     reason (a preview opening) moves no focus.
  const RAIL=new Set(['studio-design','studio-checkpoints','railtoggle']), rail=$('rail'), inspector=$('dream-inspector');
  let opened=null;
  document.addEventListener('click',e=>{ const b=e.target.closest?.('#chat-menu [data-proxy], #nd-menu [data-proxy]'); if(!b) return;
    opened=RAIL.has(b.dataset.proxy)?'rail':b.dataset.proxy==='dream-inspect-toggle'?'inspector':null; },true);
  const giveBack=which=>{ if(opened!==which) return; opened=null;
    // Lost: on nothing, on something no longer shown, or on the menu's own row inside its closed list.
    const shown=a=>a.checkVisibility?a.checkVisibility():a.getClientRects().length>0;
    requestAnimationFrame(()=>{ const a=document.activeElement;
      if(a&&a!==body&&shown(a)&&!(a.tagName!=='SUMMARY'&&a.closest('details:not([open])'))) return;
      [...document.querySelectorAll('#chat-menu > summary, #nd-menu > summary')].find(s=>s.getClientRects().length)?.focus(); }); };
  if(rail) new MutationObserver(()=>{ if(rail.classList.contains('hide')) giveBack('rail'); }).observe(rail,{attributes:true,attributeFilter:['class']});
  if(inspector) new MutationObserver(()=>{ if(inspector.hidden) giveBack('inspector'); }).observe(inspector,{attributes:true,attributeFilter:['hidden']});

  // --- the page's status (the header's dot and word), at the end of the row ----------------------------------------
  const stat=$('stat'), dot=$('dot');
  if(stat&&dot){
    const s=el('span','cn-stat',{id:'cn-stat','aria-hidden':'true'}), word=el('span'); s.append(el('span','cn-dot'),word); hint.append(s);   // #stat itself stays the live region
    const sync=()=>{ word.textContent=stat.textContent; s.dataset.on=String(dot.classList.contains('on')); };
    new MutationObserver(sync).observe(stat,{childList:true,characterData:true,subtree:true});
    new MutationObserver(sync).observe(dot,{attributes:true,attributeFilter:['class']}); sync();
  }

  // --- the resize edge between the chat column and the preview ---------------------------------------------------------
  const MIN=360, PANEL_MIN=388, KEY='dream.chat.width';
  const edge=el('div','',{id:'chat-split',role:'separator','aria-orientation':'vertical','aria-label':'Resize the chat column',tabindex:'0',title:'Drag to resize the chat column; double-click to reset'});
  panel.prepend(edge);
  const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
  const maxW=()=>Math.max(MIN,Math.round(split.clientWidth-PANEL_MIN));
  function setWidth(px,keep){
    if(px==null){ root.style.removeProperty('--dream-chat-share'); if(keep) try{ localStorage.removeItem(KEY); }catch{} }
    else { px=clamp(Math.round(px),MIN,maxW()); root.style.setProperty('--dream-chat-share',px+'px'); if(keep) try{ localStorage.setItem(KEY,String(px)); }catch{} }
    measure();
  }
  try{ const saved=parseInt(localStorage.getItem(KEY),10); if(saved>=MIN) root.style.setProperty('--dream-chat-share',saved+'px'); }catch{}
  edge.addEventListener('pointerdown',e=>{
    if(e.button) return;
    e.preventDefault(); edge.setPointerCapture(e.pointerId); edge.classList.add('cn-drag'); body.classList.add('cn-dragging');
    const left=main.getBoundingClientRect().left;
    const move=ev=>setWidth(ev.clientX-left,false);
    const up=ev=>{ edge.classList.remove('cn-drag'); body.classList.remove('cn-dragging'); edge.removeEventListener('pointermove',move); edge.removeEventListener('pointerup',up); edge.removeEventListener('pointercancel',up);
      if(ev.type==='pointerup') setWidth(main.getBoundingClientRect().width,true); };
    edge.addEventListener('pointermove',move); edge.addEventListener('pointerup',up); edge.addEventListener('pointercancel',up);
  });
  edge.addEventListener('keydown',e=>{
    if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight') return;
    e.preventDefault(); setWidth(main.getBoundingClientRect().width+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?64:16),true);
  });
  edge.addEventListener('dblclick',()=>setWidth(null,true));

  // --- the column: the footer as wide as the conversation, the preview down beside it ----------------------------------
  const wide=matchMedia('(min-width:1000px)');
  let raf=0;
  function measure(){
    if(raf) return;
    raf=requestAnimationFrame(()=>{
      raf=0;
      const on=wide.matches&&root.dataset.dreamView==='chat'&&root.classList.contains('dream-design')&&body.classList.contains('has-artifact');
      if(!on){ if(body.classList.contains('cn-column')){ body.classList.remove('cn-column'); body.style.removeProperty('--cn-foot-w'); body.style.removeProperty('--cn-foot-h'); } return; }
      const m=main.getBoundingClientRect(), f=footer.getBoundingClientRect(), sw=split.clientWidth;
      if(!m.width||!f.height) return;
      body.style.setProperty('--cn-foot-w',Math.round(m.right-f.left)+'px');
      body.style.setProperty('--cn-foot-h',Math.max(0,Math.round(f.bottom-m.bottom))+'px');
      if(!body.classList.contains('cn-column')) body.classList.add('cn-column');
      const lo=Math.round(MIN/sw*100), hi=Math.max(lo,Math.round(maxW()/sw*100)), now=clamp(Math.round(m.width/sw*100),lo,hi);
      edge.setAttribute('aria-valuemin',lo); edge.setAttribute('aria-valuemax',hi); edge.setAttribute('aria-valuenow',now); edge.setAttribute('aria-valuetext',now+'%');
    });
  }
  const ro=new ResizeObserver(measure); for(const n of [main,footer,split]) ro.observe(n);
  new MutationObserver(measure).observe(body,{attributes:true,attributeFilter:['class']});
  new MutationObserver(measure).observe(root,{attributes:true,attributeFilter:['data-dream-view','class']});
  wide.addEventListener('change',measure);
  measure();
})();
