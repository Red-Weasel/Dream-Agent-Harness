/* The Artifacts tab (DREAM-235): the view the Studio tab was (view id 'studio', nav entry #dream-nav-studio), under
   its new name. Its home, drawn in #studio-empty, is the library of what Dream built in this project (DREAM-241, after
   the owner's examples of 2026-10-04: a library grid with a picture per item, a list with one row per item): kind
   filters, a search, Grid | List, the items grouped by day and month, newest first. An item opens the same #artpanel
   frame in the tab at full width; its "..." menu holds the other ways (beside the chat, the browser or its
   application, a download). The items are the session's artifacts as this page knows them (the registry's, what the
   model showed, widgets) joined with GET /api/artifacts (when each was built, whether its file is still there,
   earlier sessions). Presentation and navigation only: opening goes through index.html's openCard / openArtifact. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const root = document.documentElement, body = document.body, home = $('studio-empty');
  const el = (tag, cls, text) => { const n = document.createElement(tag); if(cls) n.className = cls; if(text != null) n.textContent = text; return n; };
  const icon = d => `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">${d}</svg>`;
  // The names the owner reads: the nav entry (workspace.js labels it), the classic bar's button, the empty canvas.
  $('studio-preview').textContent = 'Artifacts';
  $('studio-preview').title = 'Artifacts: everything Dream built in this project';
  home.classList.add('art-list');
  home.innerHTML = '<div class="art-head"><h1 title="Everything Dream built in this project: pages, reports, images, video, audio and documents.">Artifacts</h1>'
    + '<div class="art-filters" id="art-filters" role="group" aria-label="Kind"></div><span class="art-space"></span>'
    + '<label class="art-search"><span class="art-sr">Search artifacts</span><input id="art-search" type="search" placeholder="Search" autocomplete="off" spellcheck="false"></label>'
    + '<div class="art-view" role="group" aria-label="Layout">'
    + `<button type="button" id="art-view-grid" aria-label="Grid" title="Grid">${icon('<rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="9" y="2.5" width="4.5" height="4.5" rx="1"/><rect x="2.5" y="9" width="4.5" height="4.5" rx="1"/><rect x="9" y="9" width="4.5" height="4.5" rx="1"/>')}</button>`
    + `<button type="button" id="art-view-list" aria-label="List" title="List">${icon('<path d="M3 4h10M3 8h10M3 12h10"/>')}</button></div>`
    + '</div><div id="art-home-list"></div>';
  const head = home.querySelector('.art-head'), filters = $('art-filters'), search = $('art-search');
  const history = el('button', 'art-history', 'Media history');
  history.type = 'button'; history.title = 'Registered media outputs of this workspace';
  history.onclick = () => $('dream-output-history')?.click();
  head.append(history);
  const list = $('art-home-list');
  let library = {session: [], earlier: []}, loading = false, again = false, timer = 0;

  // What the owner chose: the kind, the words, the layout (the layout is remembered in this browser).
  const KINDS = [['all', 'All', null], ['page', 'Pages', ['html', 'svg']], ['report', 'Reports', ['report']], ['image', 'Images', ['image']],
    ['media', 'Media', ['video', 'audio']], ['document', 'Documents', ['document']]];
  const VIEW_KEY = 'dream.artifacts.view', THUMB_FRAMES = 24;   // at most this many live page thumbnails, the newest
  let kind = 'all', query = '', view = 'grid';
  try{ if(localStorage.getItem(VIEW_KEY) === 'list') view = 'list'; }catch(e){}
  for(const [id, label] of KINDS){
    const b = el('button', null); b.type = 'button'; b.dataset.kind = id;
    b.append(el('span', null, label), el('span', 'n'));
    b.onclick = () => { kind = id; render(); };
    filters.append(b);
  }
  function setView(v){
    view = v; try{ localStorage.setItem(VIEW_KEY, v); }catch(e){}
    render();
  }
  $('art-view-grid').onclick = () => setView('grid');
  $('art-view-list').onclick = () => setView('list');
  search.addEventListener('input', () => { query = search.value.trim().toLowerCase(); render(); });

  const isHome = () => root.dataset.dreamView === 'studio' && body.classList.contains('art-home');
  function when(at){
    const s = Date.now() / 1000 - at;
    if(!(at > 0)) return '';
    if(s < 90) return 'just now';
    if(s < 3600) return Math.round(s / 60) + ' min ago';
    if(s < 86400) return Math.round(s / 3600) + ' h ago';
    return new Date(at * 1000).toLocaleDateString(undefined, {year: 'numeric', month: 'short', day: 'numeric'});
  }
  // The heading an item sits under: Today, Yesterday, then its month (with the year when it is not this one).
  function group(at){
    const d = new Date(at * 1000), now = new Date(), day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((day(now) - day(d)) / 86400000);
    if(days <= 0) return 'Today';
    if(days === 1) return 'Yesterday';
    return d.toLocaleDateString(undefined, d.getFullYear() === now.getFullYear() ? {month: 'long'} : {month: 'long', year: 'numeric'});
  }
  // A row of the server's list as this page's artifact. This session's joins the one its event made (or makes it,
  // when the replayed conversation no longer holds that event). An earlier session's has its own key and joins the
  // page's artifacts only when the owner opens it: its content is that session's stored version.
  const earlier = new Map();
  function adopt(row, old){
    const key = old ? `old:${row.session}:${row.id}` : `file:${row.path}`, known = old ? earlier : artifacts;
    let a = artifacts.get(key) || known.get(key);
    if(!a){
      a = {key, title: row.title, kind: ART_KIND[row.type] || 'html', versions: []};
      if(old){ a.old = true; a.session = row.session; }
      known.set(key, a);
    }
    a.id = String(row.id); a.path = row.path; a.title = row.title || a.title; a.kind = ART_KIND[row.type] || a.kind;
    a.vn = Math.max(a.vn || 0, row.version || 0); a.at = row.at; a.bytes = row.bytes; a.size = fileSize(row.bytes || 0);
    a.exists = row.exists !== false; a.pageUrl = row.page_url || null;
    return a;
  }
  function open(a, tab){
    if(a.old && !artifacts.has(a.key)){ artifacts.set(a.key, a); companion?.artifacts(); }
    if(!tab) $('studio-interactions').click();   // to Chat: the panel beside the conversation
    openCard(a.key, tab);
  }
  const fileOf = a => a.key.startsWith('file:') ? a.key.slice(5) : a.old && a.exists ? a.path : null;
  const gone = a => !(fileOf(a) && a.exists !== false) && !!a.path;
  const net = () => { try{ return !!previewNet; }catch(e){ return false; } };   // the internet switch (index.html)
  // The desktop window never loads a page of the page server in its own view (DREAM-111: window.py's policy sends it
  // to the owner's browser), so a frame of one stays blank there: only the plain browser page frames a page.
  const framable = () => !root.classList.contains('dream-native');
  // The picture of a grid card, cheap and safe, never the artifact's content fetched for it: an image or a diagram
  // of this session is the file itself in an <img> (nothing in it runs or loads); a page is its own address in a
  // frame that runs no script (sandbox=""), scaled down -- where the view may frame it (see framable), only while
  // previews may use the network (the internet switch: a page's stylesheet or picture may come from it), and only
  // for the newest few; anything else, an earlier session's and a file that is gone are a flat tile with the
  // kind's glyph.
  function thumb(a, frames){
    const box = el('div', 'thumb'); box.setAttribute('aria-hidden', 'true');
    const file = !a.old && a.exists !== false && a.key.startsWith('file:') ? a.key.slice(5) : null;
    if(file && (a.kind === 'image' || a.kind === 'svg')){
      const img = el('img'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.draggable = false;
      if(a.kind === 'svg') img.className = 'fit';
      img.addEventListener('error', () => box.replaceChildren(glyph(a)), {once: true});
      img.src = imageHref(file, a.at || '');
      box.append(img);
    } else if(file && a.kind === 'html' && a.pageUrl && frames){
      const f = el('iframe'); f.setAttribute('sandbox', ''); f.loading = 'lazy'; f.tabIndex = -1;
      f.setAttribute('aria-hidden', 'true'); f.setAttribute('scrolling', 'no'); f.referrerPolicy = 'no-referrer';
      f.src = a.pageUrl;
      box.classList.add('page'); box.append(f);
    } else box.append(glyph(a));
    return box;
  }
  function glyph(a){ const g = el('span', 'glyph'); g.innerHTML = ART_GLYPH[a.kind] || ART_GLYPH.html; return g; }
  // The other ways to open an item, under its "..." button: what works for it, and nothing that cannot.
  function actions(a, said){
    const acts = el('div', 'acts'), file = fileOf(a);
    const beside = el('button', null, 'Open beside chat');
    beside.type = 'button'; beside.title = 'Go to Chat and open it in the panel beside the conversation';
    beside.onclick = () => open(a, false);
    acts.append(beside);
    if(file && a.exists !== false){
      if(['html', 'svg'].includes(a.kind)){
        if(a.pageUrl){
          const link = el('a', null, 'Open in browser ↗');
          link.href = a.pageUrl; link.target = '_blank'; link.rel = 'noopener noreferrer';
          acts.append(link);
        }
      } else {
        const app = el('button', null, 'Open in app');
        app.type = 'button';
        app.onclick = () => openInApp(file, (text, failed) => { said.textContent = text; said.classList.toggle('bad', !!failed); });
        acts.append(app);
      }
      const dl = el('a', null, 'Download');
      dl.href = '/api/download?path=' + encodeURIComponent(file) + '&token=' + encodeURIComponent(TOKEN);
      dl.setAttribute('download', '');
      acts.append(dl);
    }
    return acts;
  }
  const versionOf = a => a.id ? (a.vn || 1) : a.versions.length || 1;
  const stamp = a => a.at > 0 ? a.at : Date.now() / 1000;   // what this page made and the registry has not dated is of now
  const metaText = a => view === 'grid'
    ? [ART_LABEL[a.kind] || 'Page', 'v' + versionOf(a), when(a.at), a.bytes > 0 ? a.size : ''].filter(Boolean).join(' · ')
    : [ART_LABEL[a.kind] || 'Page', a.path || '', a.bytes > 0 ? a.size : ''].filter(Boolean).join(' · ');
  const sideText = a => ['v' + versionOf(a), when(a.at)].filter(Boolean).join(' · ');
  // One item: a card (grid) or a row (list). The whole of it is its Open button; "..." sits above that.
  const cards = new Map();   // key -> {sig, node}: an unchanged item keeps its node, so its picture is not loaded again
  function card(a, frames){
    const now = artOpen === a.key, label = ART_LABEL[a.kind] || 'Page';
    const sig = JSON.stringify([view, a.title, a.kind, versionOf(a), a.bytes, a.exists, a.pageUrl, !!a.old, now, a.path, frames, view === 'grid' ? a.at : 0]);
    const had = cards.get(a.key);
    if(had && had.sig === sig){ had.refresh(); return had.node; }
    const item = el('li', 'art-row' + (now ? ' now' : '') + (gone(a) ? ' gone-file' : ''));
    item.dataset.k = a.key;
    item.setAttribute('aria-label', `${a.title}, ${label}`);
    const t = el('div', 't'), meta = el('span', 'meta'), said = el('p', 'said'), more = el('details', 'art-more'), sum = el('summary');
    t.append(el('b', null, a.title), meta);
    if(gone(a)) t.append(el('span', 'gone', 'The file is no longer in the workspace.'));
    said.setAttribute('role', 'status');
    const here = el('button', 'primary'); here.type = 'button';
    here.setAttribute('aria-label', 'Open'); here.title = `Open ${a.title} here at full width`;
    here.onclick = () => open(a, true);
    sum.innerHTML = icon('<circle cx="3.5" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="12.5" cy="8" r="1.1" fill="currentColor" stroke="none"/>');
    sum.setAttribute('aria-label', `More ways to open ${a.title}`); sum.title = 'More';
    more.append(sum, actions(a, said));
    let side = null;
    if(view === 'grid'){
      const pic = thumb(a, frames);
      if(!a.old) pic.append(el('span', 'badge', 'this session'));
      item.append(pic, t, here, more, said);
    } else {
      const ic = el('span', 'ic'); ic.innerHTML = ART_GLYPH[a.kind] || ART_GLYPH.html; ic.setAttribute('aria-hidden', 'true');
      side = el('span', 'side');
      if(!a.old) t.querySelector('b').after(el('span', 'badge', 'this session'));
      item.append(ic, t, side, here, more, said);
    }
    const refresh = () => {
      meta.textContent = metaText(a); if(side) side.textContent = sideText(a);
      if(a.at > 0) meta.title = new Date(a.at * 1000).toLocaleString() + (a.path ? ' · ' + a.path : '');
    };
    refresh();
    cards.set(a.key, {sig, node: item, refresh});
    return item;
  }
  // One menu at a time; a click elsewhere or Escape shuts it, and Escape gives the focus back to its button.
  list.addEventListener('toggle', e => {
    if(e.target.matches?.('.art-more') && e.target.open) list.querySelectorAll('.art-more[open]').forEach(m => { if(m !== e.target) m.open = false; });
  }, true);
  document.addEventListener('click', e => {
    list.querySelectorAll('.art-more[open]').forEach(m => { if(!m.contains(e.target) || e.target.closest('.acts button, .acts a')) m.open = false; });
  });
  list.addEventListener('keydown', e => {
    const m = e.key === 'Escape' && e.target.closest?.('.art-more[open]');
    if(m){ e.stopPropagation(); m.open = false; m.querySelector('summary').focus(); }
  });
  // The thumbnails' scale: a page is laid out 1280 px wide and shown at the card's width.
  const fit = () => { const t = list.querySelector('.thumb'); if(t && t.clientWidth) list.style.setProperty('--art-s', (t.clientWidth / 1280).toFixed(4)); };
  new ResizeObserver(fit).observe(list);

  let drawn = '';
  function render(){
    for(const r of library.session) adopt(r, false);
    const before = library.earlier.map(r => adopt(r, true));
    const all = [...[...artifacts.values()].filter(a => !a.old && !a.streaming), ...before].sort((x, y) => stamp(y) - stamp(x));
    // The filters: every kind that exists, with how many; the chosen one stays while it is the choice.
    const of = id => { const kinds = KINDS.find(k => k[0] === id)[2]; return kinds ? all.filter(a => kinds.includes(a.kind)) : all; };
    for(const b of filters.children){
      const n = of(b.dataset.kind).length;
      b.hidden = !(b.dataset.kind === 'all' || n || b.dataset.kind === kind);
      b.setAttribute('aria-pressed', String(b.dataset.kind === kind));
      b.querySelector('.n').textContent = String(n);
      b.setAttribute('aria-label', `${b.firstChild.textContent} ${n}`);
    }
    $('art-view-grid').setAttribute('aria-pressed', String(view === 'grid'));
    $('art-view-list').setAttribute('aria-pressed', String(view === 'list'));
    head.classList.toggle('none', !all.length);
    const shown = of(kind).filter(a => !query || (a.title || '').toLowerCase().includes(query) || (a.path || '').toLowerCase().includes(query));
    if(!shown.length){
      drawn = ''; cards.clear();
      const none = el('div', 'art-none');
      if(!all.length){
        none.append(el('h2', null, 'Nothing built yet'), el('p', null, 'Ask Dream to build a page, write a report, or make an image, a video or a document. Each one opens beside the chat as it is built and is kept here with its versions.'));
      } else {
        const label = KINDS.find(k => k[0] === kind)[1], clear = el('button', 'art-clear', 'Clear');
        none.append(el('h2', null, 'Nothing matches'),
          el('p', null, query ? `No artifact${kind === 'all' ? '' : ' in ' + label} has "${search.value.trim()}" in its title or path.` : `Nothing in ${label} yet.`), clear);
        clear.type = 'button'; clear.title = 'Show every artifact';
        clear.onclick = () => { kind = 'all'; query = ''; search.value = ''; render(); search.focus({preventScroll: true}); };
      }
      list.replaceChildren(none);
      return;
    }
    // Where the keyboard is, so a redraw (the session built something) does not take it away, and the open menu.
    const at = document.activeElement, focusKey = list.contains(at) ? at.closest('.art-row')?.dataset.k : null;
    const focusWhat = focusKey ? (at.matches('.primary') ? '.primary' : at.matches('summary') ? 'summary' : null) : null;
    const menuKey = list.querySelector('.art-more[open]')?.closest('.art-row')?.dataset.k;
    let frames = net() && framable() ? THUMB_FRAMES : 0;
    const groups = [];
    for(const a of shown){
      const title = group(stamp(a));
      if(!groups.length || groups[groups.length - 1].title !== title) groups.push({title, items: []});
      const live = view === 'grid' && frames > 0 && !a.old && a.kind === 'html' && !!a.pageUrl;
      if(live) frames--;
      groups[groups.length - 1].items.push({a, node: card(a, live)});
    }
    for(const key of [...cards.keys()]) if(!shown.some(a => a.key === key)) cards.delete(key);
    // The same items in the same places: nothing is moved (a moved frame would load its page again).
    const layout = JSON.stringify([view, groups.map(g => [g.title, g.items.map(i => i.a.key)])]);
    const same = layout === drawn && groups.every(g => g.items.every(i => i.node.isConnected));
    if(!same){
      const lists = [...list.querySelectorAll('ul')];
      if(layout === drawn && lists.length === groups.length) groups.forEach((g, n) => g.items.forEach((i, k) => { const old = lists[n].children[k]; if(old !== i.node) old.replaceWith(i.node); }));
      else list.replaceChildren(...groups.map(g => {
        const box = el('section', 'art-section'), ul = el('ul', view === 'grid' ? 'art-grid' : 'art-rows');
        box.append(el('h2', null, g.title)); ul.append(...g.items.map(i => i.node)); box.append(ul);
        return box;
      }));
      drawn = layout;
    }
    const find = k => k && list.querySelector(`.art-row[data-k="${CSS.escape(k)}"]`);
    if(menuKey && find(menuKey)) find(menuKey).querySelector('.art-more').open = true;
    if(focusWhat && find(focusKey) && !list.contains(document.activeElement)) find(focusKey).querySelector(focusWhat)?.focus({preventScroll: true});
    fit();
  }
  async function refresh(){
    if(loading){ again = true; return; }
    loading = true;
    try{
      const r = await fetch('/api/artifacts', {headers: {'X-Dream-Token': TOKEN}, cache: 'no-store'});
      if(r.ok){ const j = await r.json(); library = {session: j.session || [], earlier: j.earlier || []}; }
    }catch(e){}   // the list shows what this page knows; the next change asks again
    loading = false;
    render();
    if(again){ again = false; refresh(); }
  }
  function show(on){
    body.classList.toggle('art-home', on);
    if(on){ body.classList.remove('art-full'); render(); refresh(); }
  }
  // The nav entry (and the classic bar's button) is the tab's home, whatever is open under it.
  $('studio-preview').addEventListener('click', () => show(true));
  // A close in the tab is its list again (index.html sets the class): the list is current, and the keyboard is in it.
  $('artclose').addEventListener('click', () => { if(isHome()){ render(); list.querySelector('button')?.focus({preventScroll: true}); } });
  // The session's artifacts changed (companion.js artifactsChanged): the list follows while it is showing.
  window.addEventListener('dream:artifacts', () => {
    if(!isHome()) return;
    clearTimeout(timer); timer = setTimeout(refresh, 150);
  });
  // The internet switch decides whether a page's thumbnail may load at all: the list follows it.
  $('netpill')?.addEventListener('click', () => { if(isHome()) render(); });
  // The conversation opened (a question, an approval, a sent message). Over the viewer it gets its place beside the
  // canvas back; from the list it is the Chat view, where what Dream builds next opens beside it.
  window.addEventListener('dream:drawer', () => {
    if(!body.classList.contains('interactions-open')) return;
    const listed = isHome();
    body.classList.remove('art-home', 'art-full');
    if(listed) $('studio-interactions').click();
  });
  window.addEventListener('dream:project-reset', () => { library = {session: [], earlier: []}; earlier.clear(); cards.clear(); drawn = ''; if(isHome()) refresh(); });
  // Leaving the tab forgets its full-width state; arriving in Chat brings the open artifact beside the conversation.
  new MutationObserver(() => {
    if(root.dataset.dreamView !== 'studio') body.classList.remove('art-full');
    if(root.dataset.dreamView === 'chat') panelBesideChat();
  }).observe(root, {attributes: true, attributeFilter: ['data-dream-view']});
  // The desktop window marks the page as its own a moment after it loads: the pictures follow (see framable).
  let native = !framable();
  new MutationObserver(() => { if(native !== !framable()){ native = !framable(); if(isHome()) render(); } }).observe(root, {attributes: true, attributeFilter: ['class']});
})();
