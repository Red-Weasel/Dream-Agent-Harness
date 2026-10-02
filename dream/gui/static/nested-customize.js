/* Nested Dream, the Customize panel (DREAM-200): text size, the orchestrator's side, which pips show, the card width
   and thinking on cards. A non-modal dialog opened from the top row, over the workers on either side, closed by Escape
   from anywhere on the view and never open together with the All-agents drawer; every choice goes through
   window.DreamNested, which keeps and persists it. No theme choice yet: Dream has one (dark) theme, and the view's
   tokens are scoped under the page so a light one is a token map. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const N=window.DreamNested; if(!N||!N.prefs) return;
  const $=id=>document.getElementById(id);
  const page=$('dream-nested-page'), panel=$('nd-custom'), btn=$('nd-custom-btn');
  if(!page||!panel||!btn) return;
  const GROUPS=[
    ['fz','Text size',[['0.93','Small'],['1','Default'],['1.143','Large'],['1.286','Larger']]],
    ['side','Orchestrator side',[['left','Left'],['right','Right']]],
    ['pips','Status icons',[['all','Every agent'],['active','Active only']]],
    ['cardw','Card width',[['272','Small'],['320','Medium'],['368','Large']]]];
  panel.innerHTML=`<div class="nd-wrow"><h2 id="nd-custom-h">Customize</h2><button type="button" class="nd-iconbtn nd-close" id="nd-custom-close" aria-label="Close customize">×</button></div>
    ${GROUPS.map(([k,label,opts])=>`<div class="nd-field"><span id="nd-custom-${k}-l">${label}</span><div class="nd-seg" role="group" aria-labelledby="nd-custom-${k}-l" data-opt="${k}">${opts.map(([v,t])=>`<button type="button" data-v="${v}" aria-pressed="false">${t}</button>`).join('')}</div></div>`).join('')}
    <label class="nd-check"><input type="checkbox" id="nd-custom-think"> Show thinking on cards</label>
    <p class="nd-muted">Dream's own theme and fonts. These choices, the sizes, the side and the pins stay in this browser; Reset layout in the top row clears them.</p>`;
  function sync(){
    const p=N.prefs(), cur={fz:String(p.fz),side:N.layout().side,pips:p.pips,cardw:String(p.cardw)};
    panel.querySelectorAll('[data-opt]').forEach(g=>g.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.v===cur[g.dataset.opt]))));
    $('nd-custom-think').checked=p.cardThink;
  }
  // The panel and the All-agents drawer share the workers' corner: opening one closes the other, so neither covers the
  // other's header or Close. `restore` puts the focus back on the button; the drawer closing it keeps the focus it moves.
  function open(){ window.DreamNestedDrawer?.close(); panel.hidden=false; btn.setAttribute('aria-expanded','true'); sync(); $('nd-custom-close').focus({preventScroll:true}); }
  function close(restore=true){ if(panel.hidden) return; panel.hidden=true; btn.setAttribute('aria-expanded','false'); if(restore) btn.focus({preventScroll:true}); }
  btn.onclick=()=>{ if(panel.hidden) open(); else close(); };
  panel.addEventListener('click',e=>{
    if(e.target.closest('#nd-custom-close')){ close(); return; }
    const b=e.target.closest('[data-opt] button'); if(!b) return;
    const k=b.parentElement.dataset.opt, v=b.dataset.v;
    if(k==='side'){ N.setLayout('side',v); if(window.DreamNestedLayout) window.DreamNestedLayout.apply(); }
    else N.setPref(k,k==='pips'?v:Number(v));
    sync();
  });
  $('nd-custom-think').addEventListener('change',e=>{ N.setPref('cardThink',e.target.checked); });
  // Escape closes the open panel from anywhere on the view (capture, like the drawer's; the two are never open together).
  // The focus returns to the button from inside the panel; a text box or a window that holds it keeps it.
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape'||panel.hidden||page.hidden||e.isComposing) return;
    e.preventDefault(); e.stopPropagation();
    const a=document.activeElement; close(!a||a===document.body||panel.contains(a));
  },true);
  N.subscribe(()=>{ if(!panel.hidden) sync(); });   // Reset layout, and a side change made elsewhere
  window.DreamNestedCustomize={open,close,sync};
})();
