/* The Understand-Anything dock (DREAM-087): the codebase map beside the chat, and this session's file changes side by
   side. A dock, not a page: it stays open while the view changes underneath it (a permission card, a new turn), so the
   owner keeps the map in sight while working. Only its close button or the Understand nav entry closes it. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id), root = document.documentElement;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const panel = document.createElement('aside');
  panel.id = 'dream-understand'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Understand');
  panel.innerHTML = `
    <div class="ua-bar" role="tablist">
      <h2>Understand</h2>
      <button id="ua-tab-map" type="button" role="tab" aria-selected="true">Map</button>
      <button id="ua-tab-changes" type="button" role="tab" aria-selected="false">Changes</button>
      <button id="ua-refresh" type="button" title="Reload the map or the change list">Refresh</button>
      <span id="ua-status"></span>
      <button id="ua-close" type="button" aria-label="Close Understand">×</button>
    </div>
    <div class="ua-body">
      <section id="ua-empty" hidden>
        <h3>No codebase map yet</h3>
        <p>Understand-Anything maps a codebase into a knowledge graph (<code>.ua/knowledge-graph.json</code>) you can
        explore here — layers, files, functions and guided tours — while the conversation continues beside it.</p>
        <p>Ask the model to run the <code>understand</code> skill on this project. The map appears here when the graph is written.</p>
        <button id="ua-ask" type="button">Ask for a map of this project</button>
      </section>
      <!-- The dashboard is trusted local software served by Dream itself (a build of the upstream plugin, not text a
           model wrote), so unlike the artifact frames it is a plain same-origin frame and gets the session token. -->
      <iframe id="ua-frame" title="Understand-Anything map" hidden></iframe>
      <section id="ua-changes" hidden></section>
    </div>`;
  document.querySelector('.split').append(panel);
  const frame = byId('ua-frame'), empty = byId('ua-empty'), changes = byId('ua-changes'), status = byId('ua-status');
  const ASK = 'Use the understand skill (Understand-Anything) to map this project into .ua/knowledge-graph.json, then tell me when the map is ready. '
    + 'Its helper scripts run through the ua_run tool (the plugin is installed and built; skip locating it). '
    + "Exclude Dream's own state folders: pass --exclude \".dream/**,.remember/**\" to the scan.";
  let tab = 'map', graph = null, analyzedAt = null, timer = null, loadingChanges = false, workspaceKey = null;
  const token = () => typeof TOKEN === 'string' ? TOKEN : (new URLSearchParams(location.search).get('token') || '');
  async function read(path) {
    const r = await fetch(path, { headers: { 'X-Dream-Token': token() }, cache: 'no-store' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || ('Unavailable (' + r.status + ')'));
    return data;
  }

  function open() { panel.hidden = false; root.classList.add('understand-open'); byId('dream-nav-understand')?.setAttribute('aria-pressed', 'true'); poll(); if (tab === 'changes') loadChanges(); }
  function close() { panel.hidden = true; root.classList.remove('understand-open'); byId('dream-nav-understand')?.setAttribute('aria-pressed', 'false'); clearTimeout(timer); timer = null; }
  function toggle() { panel.hidden ? open() : close(); }
  function showTab(name) {
    tab = name;
    for (const t of ['map', 'changes']) byId('ua-tab-' + t).setAttribute('aria-selected', String(t === name));
    changes.hidden = name !== 'changes';
    if (name === 'changes') { frame.hidden = true; empty.hidden = true; loadChanges(); } else paintMap();
  }
  function paintMap() {
    if (tab !== 'map') return;
    empty.hidden = Boolean(graph); frame.hidden = !graph;
    if (graph && !frame.getAttribute('src')) frame.src = '/ua/?token=' + encodeURIComponent(token());
  }
  // The dashboard's header keeps its legend and view toggles in a strip that scrolls sideways with the scrollbar
  // hidden; in the dock's width that reads as pills cut off (owner, 2026-09-23: INFRA clipped, DATA and DOMAIN
  // unseen). Same origin, so the dock can restyle it: wrap the strip instead. Upstream's class names are the hook;
  // tests/test_understand_panel.py checks every legend pill is whole at the owner's window size.
  // Two rows: title and view tabs left, tools right; then the strip (view toggles, type pills, one pill per layer) on
  // its own full-width row, wrapping only when a graph has many layers.
  const FIT = 'header { flex-wrap: wrap; row-gap: 6px; }\n'
    + 'header > .overflow-x-auto { overflow: visible !important; order: 3; flex: 0 0 100%; min-width: 0; }\n'
    + 'header > .overflow-x-auto > .w-max { width: auto !important; }\n'   // the row is sized max-content; let it wrap
    + 'header > .overflow-x-auto div { flex-wrap: wrap; row-gap: 6px; flex-shrink: 1; min-width: 0; }\n'
    + 'header > div:last-child { margin-left: auto; }';
  frame.addEventListener('load', () => {
    try {
      const doc = frame.contentDocument;
      if (doc && doc.head && !doc.getElementById('dream-ua-fit')) {
        const style = doc.createElement('style'); style.id = 'dream-ua-fit'; style.textContent = FIT; doc.head.append(style);
      }
    } catch (e) { /* not our document: leave it alone */ }
  });
  async function poll() {
    clearTimeout(timer); timer = null;
    try {
      const s = await read('/api/understand/status');
      graph = s.graph || null;
      status.textContent = graph
        ? [graph.name || 'project', graph.nodes + ' nodes', graph.edges + ' edges', graph.analyzedAt ? 'analyzed ' + new Date(graph.analyzedAt).toLocaleString() : ''].filter(Boolean).join(' · ')
        : (s.dashboard ? 'No graph in this workspace yet' : 'Dashboard not built — open the Map tab for the build command');
      if (graph && analyzedAt && graph.analyzedAt !== analyzedAt && frame.getAttribute('src')) frame.src = frame.getAttribute('src');   // a new analysis: reload
      analyzedAt = graph ? graph.analyzedAt : null;
      paintMap();
    } catch (e) { status.textContent = e.message; }
    if (!panel.hidden) timer = setTimeout(poll, graph ? 15000 : 4000);
  }

  /* ---- Changes: this session's checkpoint diffs, two aligned columns ------------------------------------------------ */
  function parse(text) {   // CheckpointStore.diff(): unified diffs per file, plus "# path: …" notes
    const files = [], notes = [];
    let file = null, hunk = null;
    for (const line of text.split('\n')) {
      if (line.startsWith('--- ')) { file = { path: line.slice(4).replace(/ \(checkpoint [^)]*\)$/, ''), hunks: [] }; files.push(file); hunk = null; }
      else if (line.startsWith('+++ ')) continue;
      else if (line.startsWith('@@')) { if (!file) { file = { path: '', hunks: [] }; files.push(file); } hunk = { header: line, lines: [] }; file.hunks.push(hunk); }
      else if (line.startsWith('#')) { notes.push(line.slice(1).trim()); hunk = null; }
      else if (hunk) hunk.lines.push(line);
    }
    return { files, notes };
  }
  function cell(side, kind, text) { return el('span', text, 'ln ' + side + ' ' + kind); }
  function paintHunk(grid, hunk) {
    grid.append(el('span', hunk.header, 'hunk'));
    let dels = [], adds = [];
    const flush = () => {
      for (let i = 0; i < Math.max(dels.length, adds.length); i++) {
        grid.append(i < dels.length ? cell('ua-old', 'del', dels[i]) : cell('ua-old', 'gap', ''),
                    i < adds.length ? cell('ua-new', 'add', adds[i]) : cell('ua-new', 'gap', ''));
      }
      dels = []; adds = [];
    };
    for (const line of hunk.lines) {
      if (line.startsWith('-')) dels.push(line.slice(1));
      else if (line.startsWith('+')) adds.push(line.slice(1));
      else if (line.startsWith(' ') || line === '') { flush(); grid.append(cell('ua-old', 'ctx', line.slice(1)), cell('ua-new', 'ctx', line.slice(1))); }
      else { flush(); grid.append(el('span', line, 'note')); }
    }
    flush();
  }
  function render(row, text) {
    const { files, notes } = parse(text), when = [row.label || row.id, row.created_at ? new Date(row.created_at).toLocaleString() : ''].filter(Boolean).join(' · ');
    const blocks = files.map(file => {
      const block = el('div', null, 'ua-diff'), head = el('div', null, 'ua-diff-head'), grid = el('div', null, 'ua-diff-grid');
      head.append(el('span', file.path, 'ua-diff-file'), el('span', when, 'ua-diff-when'));
      for (const hunk of file.hunks) paintHunk(grid, hunk);
      block.append(head, grid);
      return block;
    });
    if (notes.length) {
      const block = el('div', null, 'ua-diff'), head = el('div', null, 'ua-diff-head'), grid = el('div', null, 'ua-diff-grid');
      head.append(el('span', row.id, 'ua-diff-file'), el('span', when, 'ua-diff-when'));
      for (const note of notes) grid.append(el('span', note, 'note'));
      block.append(head, grid); blocks.push(block);
    }
    return blocks;
  }
  async function loadChanges() {
    if (loadingChanges) return;
    loadingChanges = true;
    changes.replaceChildren(el('p', "Reading this session's file checkpoints…"));
    try {
      const rows = (await read('/api/checkpoints')).checkpoints || [];
      if (!rows.length) {
        changes.replaceChildren(el('p', 'No file changes recorded in this session yet. A checkpoint is taken the first time a turn writes a file; each one appears here with its changes side by side, old on the left, new on the right.'));
        return;
      }
      const out = [el('p', rows.length + ' checkpoint(s), newest first. Left: the file before that turn; right: the file now.')];
      for (const row of rows.slice(0, 12)) {
        let text;
        try { text = (await read('/api/checkpoints/' + encodeURIComponent(row.id) + '/diff')).diff || ''; }
        catch (e) { text = '# ' + row.id + ': ' + e.message; }
        out.push(...render(row, text));
      }
      changes.replaceChildren(...out);
    } catch (e) { changes.replaceChildren(el('p', e.message)); }
    finally { loadingChanges = false; }
  }

  byId('ua-tab-map').onclick = () => showTab('map');
  byId('ua-tab-changes').onclick = () => showTab('changes');
  byId('ua-close').onclick = close;
  byId('ua-refresh').onclick = () => { if (tab === 'changes') loadChanges(); else { if (frame.getAttribute('src')) frame.src = frame.getAttribute('src'); poll(); } };
  byId('ua-ask').onclick = () => {
    const input = byId('input');
    byId('studio-interactions')?.click(); window.companion?.compose?.();
    input.value = input.value ? input.value + '\n\n' + ASK : ASK;
    input.dispatchEvent(new Event('input', { bubbles: true })); input.focus();
  };
  window.addEventListener('dream:session', () => {   // another workspace: the map is that project's, so start over
    const key = window.DREAM_SESSION?.workspace || '';
    if (key === workspaceKey) return;
    workspaceKey = key; graph = null; analyzedAt = null; frame.removeAttribute('src'); frame.hidden = true;
    if (!panel.hidden) poll();
  });
  let changesTimer = null;   // a new tool card may mean a new checkpoint
  new MutationObserver(() => { if (panel.hidden || tab !== 'changes') return; clearTimeout(changesTimer); changesTimer = setTimeout(loadChanges, 1500); })
    .observe(byId('stream'), { childList: true });
  window.DreamUnderstand = { toggle, open, close, isOpen: () => !panel.hidden };
})();
