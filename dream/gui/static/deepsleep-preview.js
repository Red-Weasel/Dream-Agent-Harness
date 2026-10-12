/* DeepSleep's Preview (the owner, 2026-10-10): the preview panel of the Chat and Nested views (#artpanel, index.html),
   slid in beside the terminal board. A Preview button in the board's controls opens and closes it; Esc in the panel
   and the panel's own close work too. With nothing open, the panel lists what Dream built in this project
   (GET /api/artifacts, as the Artifacts tab) and the workspace's previewable files (GET /api/preview-files); a row
   opens in the panel through index.html's own openCard / openArtifact. The terminals shrink into the space left.
   Files a DeepSleep terminal builds appear as workspace files: each terminal keeps its own artifact list. */
(() => {
  'use strict';
  const root = document.documentElement;
  // The board's controls: in the board, or in the page header where deepsleep-toolbar.js moves them outside the desktop.
  const controls = document.querySelector('#dream-deepsleep .ds-controls, #dream-deepsleep-header .ds-controls');
  if (!controls || !panel) return;
  const WIDTH = 'dream.dsPreviewWidth';
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const active = () => root.dataset.dreamView === 'deepsleep';

  const toggle = el('button', 'ds-preview-toggle', 'Preview');
  toggle.type = 'button';
  toggle.setAttribute('aria-label', 'Preview artifacts');
  toggle.setAttribute('aria-pressed', 'false');
  toggle.title = 'Preview pages, reports, images and documents beside the terminals (Esc closes)';
  const after = controls.querySelector('button[aria-label="Message Board: show connections and delivery activity"]');
  if (after) after.after(toggle); else controls.append(toggle);

  // The list shown in the panel while nothing is open in it.
  const pick = el('section', null);
  pick.id = 'ds-preview-pick';
  pick.setAttribute('aria-label', 'Choose what to preview');
  const head = el('header', 'ds-pv-head');
  head.append(el('h2', null, 'Preview'));
  const search = el('input');
  search.type = 'search'; search.id = 'ds-preview-search'; search.placeholder = 'Search files';
  search.setAttribute('aria-label', 'Search what can be previewed'); search.autocomplete = 'off'; search.spellcheck = false;
  const close = el('button', 'ds-pv-close', '×');
  close.type = 'button'; close.setAttribute('aria-label', 'Close preview'); close.title = 'Close preview (Esc)';
  head.append(search, close);
  const list = el('div', 'ds-pv-list');
  list.id = 'ds-preview-list';
  pick.append(head, list);
  const edge = el('div', null);
  edge.id = 'ds-preview-edge';
  edge.tabIndex = 0;
  edge.setAttribute('role', 'separator'); edge.setAttribute('aria-orientation', 'vertical');
  edge.setAttribute('aria-label', 'Preview width: drag, or use the arrow keys');
  panel.append(pick, edge);

  let open = false, built = [], files = [], truncated = false, failed = false, loading = false, timer = 0;

  function setOpen(on) {
    if (on === open) return;
    open = on;
    root.classList.toggle('ds-preview', on);
    toggle.setAttribute('aria-pressed', String(on));
    if (on) {
      root.classList.add('ds-preview-entering');
      panel.getBoundingClientRect();
      requestAnimationFrame(() => root.classList.remove('ds-preview-entering'));
      refresh();
      if (!panel.classList.contains('open')) search.focus({preventScroll: true});
    } else if (panel.classList.contains('open')) {
      $('artclose').click();     // index.html's close: the frame and its render loop are torn down
    }
  }

  const kindLabel = {page: 'Page', svg: 'SVG', report: 'Report', image: 'Image', video: 'Video', audio: 'Audio', document: 'Document'};
  const ago = at => {
    const s = Math.max(0, Date.now() / 1000 - (at || 0));
    return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago'
      : Math.round(s / 86400) + ' d ago';
  };

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const get = url => fetch(url, {headers: {'X-Dream-Token': TOKEN}, cache: 'no-store'}).then(r => r.ok ? r.json() : Promise.reject(r.status));
      const [library, listing] = await Promise.all([get('/api/artifacts'), get('/api/preview-files')]);
      built = [...(library.session || []).map(row => ({row, old: false})), ...(library.earlier || []).map(row => ({row, old: true}))];
      files = listing.files || [];
      truncated = !!listing.truncated;
      failed = false;
    } catch (e) {
      failed = true;
    }
    loading = false;
    render();
  }

  function row(title, detail, kind, onOpen, gone) {
    const b = el('button', 'ds-pv-row');
    b.type = 'button';
    b.dataset.kind = kind;
    b.append(el('span', 'ds-pv-kind', kindLabel[kind] || kind), el('span', 'ds-pv-name', title), el('span', 'ds-pv-detail', detail));
    if (gone) { b.disabled = true; b.title = 'The file is no longer in the workspace'; }
    else b.onclick = onOpen;
    return b;
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const hit = (...words) => !q || words.some(w => String(w || '').toLowerCase().includes(q));
    list.replaceChildren();
    if (failed) { list.append(el('p', 'ds-pv-empty', 'The list could not be read from Dream. Is the session still running?')); return; }
    const shownPaths = new Set();
    const made = built.filter(({row: r}) => hit(r.title, r.path));
    if (made.length) {
      list.append(el('h3', null, 'Built by Dream'));
      for (const {row: r, old} of made.slice(0, 120)) {
        shownPaths.add(r.path);
        list.append(row(r.title || r.path, `${r.path} · ${ago(r.at)}`, r.type, () => openBuilt(r, old), r.exists === false));
      }
    }
    const more = files.filter(f => !shownPaths.has(f.path) && hit(f.path));
    if (more.length) {
      list.append(el('h3', null, 'Workspace files'));
      for (const f of more) {
        const name = f.path.split('/').pop();
        list.append(row(name, `${f.path} · ${fileSize(f.bytes || 0)} · ${ago(f.at)}`, f.type, () => openFile(f)));
      }
      if (truncated && !q) list.append(el('p', 'ds-pv-note', 'Showing the 500 newest. Search to narrow the list.'));
    }
    if (!made.length && !more.length) {
      list.append(el('p', 'ds-pv-empty', q ? 'Nothing matches.' : 'Nothing to preview yet. Pages, reports, images and documents in the workspace appear here.'));
    }
  }

  // A row of the project's library opens as the Artifacts tab opens it (artifacts.js adopt + open).
  function openBuilt(r, old) {
    const key = old ? `old:${r.session}:${r.id}` : `file:${r.path}`;
    let a = artifacts.get(key);
    if (!a) {
      a = {key, title: r.title, kind: ART_KIND[r.type] || 'html', versions: []};
      if (old) { a.old = true; a.session = r.session; }
      artifacts.set(key, a);
      companion?.artifacts();
    }
    a.id = String(r.id); a.path = r.path; a.title = r.title || a.title; a.kind = ART_KIND[r.type] || a.kind;
    a.vn = Math.max(a.vn || 0, r.version || 0); a.at = r.at; a.exists = r.exists !== false;
    openCard(key);
  }

  // Any previewable workspace file: a text kind's content from the download route, a binary kind the file itself.
  async function openFile(f) {
    const key = `file:${f.path}`;
    const known = artifacts.get(key);
    if (known && known.id) { openCard(key); return; }
    const a = known || {key, title: f.path.split('/').pop(), kind: ART_KIND[f.type] || 'html', versions: []};
    a.path = f.path; a.bytes = f.bytes; a.size = fileSize(f.bytes || 0); a.at = f.at;
    let body = String(f.at || Date.now());
    if (ART_TEXT.has(a.kind)) {
      try {
        const r = await fetch('/api/download?path=' + encodeURIComponent(f.path) + '&token=' + encodeURIComponent(TOKEN), {cache: 'no-store'});
        body = r.ok ? await r.text() : null;
      } catch (e) { body = null; }
    }
    if (body == null) { list.prepend(el('p', 'ds-pv-empty', `${f.path} could not be read.`)); return; }
    if (!known) { artifacts.set(key, a); companion?.artifacts(); }
    setVersion(a, 1, body);
    if (a.kind !== 'image') artView = 'preview';
    openArtifact(key, {chat: chatPanelMode()});
  }

  // Width: the drag edge and the arrow keys; kept in this browser only.
  const clampWidth = w => Math.round(Math.max(320, Math.min(w, window.innerWidth - 420)));
  function setWidth(w, keep) {
    root.style.setProperty('--ds-preview-w', clampWidth(w) + 'px');
    if (keep) { try { localStorage.setItem(WIDTH, String(clampWidth(w))); } catch (e) {} }
  }
  try { const saved = Number(localStorage.getItem(WIDTH)); if (saved > 0) setWidth(saved, false); } catch (e) {}
  edge.addEventListener('pointerdown', e => {
    e.preventDefault();
    edge.setPointerCapture(e.pointerId);
    root.classList.add('ds-preview-dragging');
    const move = ev => setWidth(window.innerWidth - ev.clientX, false);
    const up = ev => {
      edge.removeEventListener('pointermove', move); edge.removeEventListener('pointerup', up);
      root.classList.remove('ds-preview-dragging');
      setWidth(window.innerWidth - ev.clientX, true);
    };
    edge.addEventListener('pointermove', move); edge.addEventListener('pointerup', up);
  });
  edge.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    setWidth(panel.getBoundingClientRect().width + (e.key === 'ArrowLeft' ? 32 : -32), true);
  });

  toggle.addEventListener('click', () => setOpen(!open));
  close.addEventListener('click', () => { setOpen(false); toggle.focus({preventScroll: true}); });
  search.addEventListener('input', render);
  // Esc closes the preview, except where Esc belongs to something else: a terminal, a dialog, the panel's menu.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || e.defaultPrevented || !open || !active() || $('artmenu')?.classList.contains('open')) return;
    const at = document.activeElement;
    if (at && at.closest?.('#dream-deepsleep .ds-node, .xterm, dialog, [role=dialog]')) return;
    e.preventDefault();
    e.stopPropagation();       // one Esc closes one thing: not the conversation drawer behind the board as well
    setOpen(false);
    toggle.focus({preventScroll: true});
  }, true);   // before the page's own Esc handlers, which would otherwise take it first
  // The panel's own close leaves the list in its place; what Dream builds meanwhile joins the list.
  $('artclose').addEventListener('click', () => { if (open && active()) setTimeout(() => { refresh(); search.focus({preventScroll: true}); }, 0); });
  window.addEventListener('dream:artifacts', () => {
    if (!open || panel.classList.contains('open')) return;
    clearTimeout(timer); timer = setTimeout(refresh, 200);
  });
  window.DreamDeepSleepPreview = Object.freeze({isOpen: () => open, setOpen});
})();
