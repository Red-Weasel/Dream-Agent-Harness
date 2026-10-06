/* Nested Dream, the right-hand pane (DREAM-243). The owner, 2026-10-04: [feedback]; [feedback]; and, with a screenshot of the Understand
   map forced beside the view, [feedback] (three squeezed columns). The workers' bar's title becomes three
   tabs. Workers is the view as it was. Preview is the page's own preview panel (#artpanel: the same frame, title bar
   and actions as beside the chat) laid over the workers' area; what Dream builds opens there, under the chat's own
   never-steal rules. Map is the Understand dock (#dream-understand), laid over the same area instead of squeezing the
   view. The orchestrator column is not touched. Own files only; nested.js builds the bar, this mounts the tabs in it.
   The tabs follow the ARIA tabs pattern (roving tabindex, the arrows, Home and End). */
(() => {
  'use strict';
  if(!COMPANION) return;
  const $=id=>document.getElementById(id), root=document.documentElement, body=document.body;
  const page=$('dream-nested-page'), split=document.querySelector('.split'), panel=$('artpanel');
  if(!page||!split||!panel) return;
  const PANES=['workers','preview','map'], LABEL={workers:'Workers',preview:'Preview',map:'Map'};
  let pane='workers';
  const nested=()=>root.dataset.dreamView==='nested';
  const understand=()=>window.DreamUnderstand;

  const ro=new ResizeObserver(()=>place());
  // --- the tabs: the bar's title is the first; Preview and Map follow it -----------------------------------------------
  const tabs=document.createElement('div'); tabs.className='nd-panes'; tabs.setAttribute('role','tablist'); tabs.setAttribute('aria-label','What the right side shows');
  const more={};
  for(const name of PANES.slice(1)){ const b=document.createElement('button'); b.type='button'; b.className='nd-pane-tab'; b.id='nd-pane-'+name; b.textContent=LABEL[name]; b.setAttribute('role','tab'); more[name]=b; }
  more.preview.title='What Dream built: a page, a report, a picture, a clip';
  more.map.title='The code map of this project (Understand)';
  // The Preview pane with nothing open: what it is for, and the way to what was built.
  const empty=document.createElement('div'); empty.className='nd-pane-empty'; empty.id='nd-pane-empty'; empty.hidden=true;
  empty.innerHTML='<div class="nd-pane-none"><h2>Nothing to preview yet</h2><p>When the orchestrator builds a page, a report, a picture or a clip, it opens here.</p>'
    +'<button type="button" class="nd-pill" id="nd-pane-latest" hidden></button><button type="button" class="nd-pill" id="nd-pane-all">All artifacts</button></div>';
  function mount(){
    const top=page.querySelector('.nd-top'), title=top&&top.querySelector('.nd-title'), app=page.querySelector('.nd-app');
    if(!top||!title||!app) return false;
    if(!tabs.isConnected){ title.before(tabs); tabs.append(title,more.preview,more.map);
      title.id='nd-pane-workers'; title.setAttribute('role','tab'); title.classList.add('nd-pane-tab'); }
    if(!empty.isConnected) app.append(empty);
    ro.observe(top);   // the bar is as wide as the workers' area: it follows the column's resize edge and its side
    return true;
  }
  const tabOf=name=>name==='workers'?$('nd-pane-workers'):more[name];
  function paint(){
    root.dataset.ndPane=pane;
    for(const name of PANES){ const t=tabOf(name); if(!t) continue; const on=name===pane; t.setAttribute('aria-selected',String(on)); t.tabIndex=on?0:-1; }
    const has=body.classList.contains('has-artifact');
    empty.hidden=!(pane==='preview'&&!has);
    if(!empty.hidden){
      const last=typeof artifacts!=='undefined'?[...artifacts.values()].pop():null, b=$('nd-pane-latest');
      b.hidden=!last; if(last){ b.textContent='Open '+String(last.title||'the latest').slice(0,80); b.dataset.k=last.key; }
    }
    place();
  }
  // Choosing a pane. Preview shows the open artifact (one opened in the Artifacts tab comes along, its frame not
  // rebuilt); Map opens the Understand dock; leaving Map closes it. Nothing here moves the focus.
  function set(name,focus){
    if(!PANES.includes(name)) return;
    pane=name;
    if(nested()){
      const u=understand();
      if(name==='map'){ if(u&&!u.isOpen()) u.open(); }
      else if(u&&u.isOpen()) u.close();
      if(name==='preview'&&typeof panelBesideChat==='function') panelBesideChat();
    }
    paint();
    if(focus) tabOf(name)?.focus({preventScroll:true});
  }
  tabs.addEventListener('click',e=>{ const t=e.target.closest('[role=tab]'); if(t) set(t.id.slice('nd-pane-'.length),false); });
  tabs.addEventListener('keydown',e=>{
    let i=PANES.indexOf(pane);
    if(e.key==='ArrowRight') i=(i+1)%PANES.length;
    else if(e.key==='ArrowLeft') i=(i-1+PANES.length)%PANES.length;
    else if(e.key==='Home') i=0;
    else if(e.key==='End') i=PANES.length-1;
    else if((e.key==='Enter'||e.key===' ')&&e.target.id==='nd-pane-workers'){ e.preventDefault(); set('workers',false); return; }   // the title is a span: its own activation
    else return;
    e.preventDefault(); set(PANES[i],true);
  });
  empty.addEventListener('click',e=>{
    if(e.target.id==='nd-pane-all') $('dream-nav-studio')?.click();
    else if(e.target.id==='nd-pane-latest'&&typeof openCard==='function') openCard(e.target.dataset.k,false);
  });
  // The column's own tabs (Agents | Workflows) choose what the Workers pane shows: a click on either returns to it.
  page.addEventListener('click',e=>{ if(e.target.closest&&e.target.closest('#nd-tab-agents,#nd-tab-workflows')&&pane!=='workers') set('workers',false); });

  // --- where the pane is: the workers' area, measured (the orchestrator column is resizable and can change sides) -----
  let raf=0;
  function place(){
    if(raf) return;
    raf=requestAnimationFrame(()=>{
      raf=0; if(!nested()||page.hidden) return;
      const top=page.querySelector('.nd-top'), s=split.getBoundingClientRect(); if(!top) return;
      const t=top.getBoundingClientRect(); if(!t.width) return;
      split.style.setProperty('--nd-pane-left',Math.round(t.left-s.left-split.clientLeft)+'px');
      split.style.setProperty('--nd-pane-top',Math.round(t.bottom-s.top-split.clientTop)+'px');
      split.style.setProperty('--nd-pane-width',Math.round(t.width)+'px');
    });
  }
  ro.observe(page);
  window.addEventListener('resize',place);

  // --- what the page does, followed: a preview that opens here shows; one that closes gives the workers back; the
  //     Understand dock opened or closed by its own switch is the Map pane ----------------------------------------------
  let had=body.classList.contains('has-artifact');
  new MutationObserver(()=>{
    const has=body.classList.contains('has-artifact');
    if(has!==had){ had=has; if(nested()){ if(has) pane='preview'; else if(pane==='preview') pane='workers'; } }
    paint();
  }).observe(body,{attributes:true,attributeFilter:['class']});
  new MutationObserver(()=>{
    if(!nested()) return;
    const open=root.classList.contains('understand-open');
    if(open&&pane!=='map') pane='map'; else if(!open&&pane==='map') pane='workers';
    if(mount()) paint();
    // Coming to this view with a preview open elsewhere: it is here too, one click away (the Preview tab).
  }).observe(root,{attributes:true,attributeFilter:['class','data-dream-view']});
  window.DreamNested?.subscribe(()=>{ if(!tabs.isConnected||!empty.isConnected){ if(mount()) paint(); } else place(); });
  if(mount()) paint();
  window.DreamNestedPane={get:()=>pane,set:name=>set(name,false)};
})();
