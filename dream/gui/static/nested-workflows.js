/* Nested Dream, the Workflows tab (DREAM-199): an honest empty state. Dream runs no multi-step workflow with judges
   and loops yet, so this panel lists nothing, shows no sample, and points at what exists: Create → Guided tasks
   (dream/workflows: a report, an analysis or a presentation from a saved task the owner reviews before it starts).
   The tabs live in nested-footer.js; this file owns only the panel, mounted after the workers area nested.js renders. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const $=id=>document.getElementById(id);
  const page=$('dream-nested-page'); if(!page) return;
  const panel=document.createElement('section');
  panel.className='nd-wf'; panel.id='nd-workflows'; panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby','nd-tab-workflows');
  panel.hidden=true;
  panel.innerHTML='<div class="nd-wf-empty" role="status"><h2>No workflows here yet</h2>'
    +'<p>Nested Dream shows one orchestrator and the workers it delegates to. A workflow with several steps, judges and a loop has no backend in Dream yet, so nothing is listed here and nothing here is a sample.</p>'
    +'<p>Next: the guided tasks that exist today live in Create → Guided tasks: a report, an analysis or a presentation from a saved task you review before it starts.</p>'
    +'<button type="button" class="nd-wf-create" id="nd-wf-create">Open Create → Guided tasks</button></div>';
  // The pointer is the real door: the chat's Create button, then its Guided tasks toggle.
  const door=panel.querySelector('#nd-wf-create');
  door.onclick=()=>{
    const open=$('dream-create-open'); if(!open) return;
    open.click();
    const guided=$('mc-guided-open');
    if(guided&&guided.getAttribute('aria-expanded')!=='true') guided.click();
  };
  if(!$('dream-create-open')) door.hidden=true;   // no Create on this page: the words still say where
  function mount(){
    if(panel.isConnected) return true;
    const main=page.querySelector('.nd-main'); if(!main) return false;
    main.after(panel); return true;
  }
  mount();
  window.DreamNested?.subscribe(mount);
})();
