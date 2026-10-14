/* Shared slash discovery for the Chat and Nested Dream composers. */
(() => {
  'use strict';
  const selector = '#input, #nd-compose';
  const menu = document.createElement('section');
  menu.id = 'dream-command-menu';
  menu.hidden = true;
  menu.setAttribute('aria-label', 'Commands');
  const heading = document.createElement('div');
  heading.className = 'dcm-heading';
  heading.textContent = 'Commands';
  const status = document.createElement('div');
  status.className = 'dcm-status';
  status.setAttribute('role', 'status');
  const list = document.createElement('div');
  list.id = 'dream-command-options';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', 'Available commands');
  const foot = document.createElement('div');
  foot.className = 'dcm-foot';
  foot.textContent = '↑ ↓ to browse · Enter to choose · Esc to close';
  menu.append(heading, status, list, foot);
  document.body.append(menu);
  let input = null, commands = null, matches = [], selected = 0, request = null, generation = 0;
  let failure = '', suppressed = false, committed = null;
  function query() {
    if (!input || !input.value.startsWith('/') || input.value.includes('\n')) return null;
    // Fully written executable commands belong to the composer's existing
    // submit/error path. Discovery must not turn Enter into another selection.
    // /handoff (and the older /fresh) is the Chat composer's Handoff control.
    if (/^\/(?:login(?:\s|$)|(?:handoff|fresh)\s*$|(?:skill|plugin)\s)/i.test(input.value)) return null;
    return input.value.slice(1).trim().toLocaleLowerCase();
  }
  function close() {
    menu.hidden = true;
    if (input) {
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }
  }
  function position() {
    if (!input || menu.hidden) return;
    const rect = input.getBoundingClientRect();
    const width = Math.min(540, window.innerWidth - 24);
    menu.style.width = width + 'px';
    menu.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px';
    const above = rect.top - 12;
    const below = window.innerHeight - rect.bottom - 12;
    menu.style.maxHeight = Math.max(100, Math.min(400, Math.max(above, below))) + 'px';
    menu.style.top = above >= Math.min(300, below) ? 'auto' : (rect.bottom + 6) + 'px';
    menu.style.bottom = above >= Math.min(300, below) ? (window.innerHeight - rect.top + 6) + 'px' : 'auto';
  }
  function highlight() {
    [...list.children].forEach((option, i) => option.setAttribute('aria-selected', String(i === selected)));
    const option = list.children[selected];
    if (option) {
      input.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({block: 'nearest'});
    } else input.removeAttribute('aria-activedescendant');
  }
  function choose(command) {
    if (!input || !command) return;
    const target = input;
    suppressed = true;
    close();
    const action = command.action;
    if (action === 'settings' || action === 'config') {
      window.dispatchEvent(new CustomEvent('dream:settings-open', {detail: {source: target.id}}));
    } else if (action === 'council') {
      window.dispatchEvent(new CustomEvent('dream:council-open', {detail: {source: target.id}}));
    } else if (action === 'navigate' && ['deepsleep', 'skills', 'settings'].includes(command.view)) {
      window.dispatchEvent(new CustomEvent('dream:navigate', {detail: {view: command.view, session_id: window.DREAM_SESSION?.session_id}}));
    } else if (action === 'deepsleep') {
      window.dispatchEvent(new CustomEvent('dream:navigate', {detail: {view: 'deepsleep', session_id: window.DREAM_SESSION?.session_id}}));
    } else if (typeof command.insert === 'string' && command.insert.startsWith('/')) {
      committed = {target, prefix: command.insert};
      target.value = command.insert;
      target.dispatchEvent(new Event('input', {bubbles: true}));
      target.focus();
      target.setSelectionRange(target.value.length, target.value.length);
    }
  }
  function render() {
    const q = query();
    if (q === null || suppressed || document.activeElement !== input) { close(); return; }
    menu.hidden = false;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-haspopup', 'listbox');
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-controls', list.id);
    input.setAttribute('aria-autocomplete', 'list');
    matches = (commands || []).filter(c => `${c.name} ${c.kind} ${c.description}`.toLocaleLowerCase().includes(q)).slice(0, 80);
    selected = Math.min(selected, Math.max(0, matches.length - 1));
    list.replaceChildren();
    status.textContent = failure || (!commands ? 'Loading commands…' : matches.length ? `${matches.length} commands · Choose one, then add your request.` : 'No matching commands. Try a skill name, plugin, login, or settings.');
    if (failure) {
      const retry = document.createElement('button');
      retry.type = 'button'; retry.className = 'dcm-retry'; retry.textContent = 'Retry';
      retry.addEventListener('click', () => { input.focus(); failure = ''; load(); });
      status.append(' ', retry);
    }
    matches.forEach((command, index) => {
      const option = document.createElement('div');
      option.id = `dream-command-option-${index}`;
      option.className = 'dcm-option';
      option.setAttribute('role', 'option');
      const title = document.createElement('div'); title.className = 'dcm-title';
      const name = document.createElement('span'); name.textContent = '/' + command.name.replace(/^\//, '');
      const kind = document.createElement('span'); kind.className = 'dcm-kind'; kind.textContent = command.kind;
      title.append(name, kind);
      const description = document.createElement('div'); description.className = 'dcm-description'; description.textContent = command.description;
      option.append(title, description);
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => choose(command));
      list.append(option);
    });
    position(); highlight();
  }
  async function load() {
    if (request) { render(); return; }
    const version = generation;
    const controller = new AbortController();
    request = controller;
    const timer = setTimeout(() => controller.abort(), 8000);
    render();
    try {
      const response = await fetch('/api/commands', {headers: {'X-Dream-Token': TOKEN}, cache: 'no-store', signal: controller.signal});
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.commands)) throw new Error('Unavailable');
      if (version !== generation) return;
      commands = data.commands.filter(c => c && typeof c.name === 'string' && typeof c.description === 'string' && typeof c.kind === 'string');
      failure = '';
    } catch (error) {
      if (version === generation) failure = 'Commands could not load. Your message is still here.';
    } finally {
      clearTimeout(timer);
      if (request === controller) request = null;
      if (version === generation) render();
    }
  }
  function update(target) {
    if (!target.matches?.(selector)) return;
    if (input !== target) close();
    input = target;
    if (committed?.target === target && target.value.startsWith(committed.prefix)) { close(); return; }
    committed = null;
    suppressed = false;
    selected = 0;
    if (query() === null) { close(); return; }
    if (!commands && !failure) load(); else render();
  }
  document.addEventListener('input', event => {
    if (suppressed && event.target === input && !event.isTrusted) return;
    update(event.target);
  });
  document.addEventListener('focusin', event => { if (event.target !== input) update(event.target); });
  document.addEventListener('keydown', event => {
    if (menu.hidden || event.target !== input || event.isComposing) return;
    // Nothing to choose: unknown slash text reaches the normal command handler -- and, while the list is still
    // loading, slash text with a space in it (usually a message; one word may yet name a command, so it waits; a
    // phrase that would match a command's description once loaded still goes as a message). Not when it failed to
    // load: the draft stays, as the status says.
    if (event.key === 'Enter' && !event.shiftKey && !matches.length && query() &&
        (commands || (!failure && /\s/.test(query())))) {
      close();
      return;
    }
    if (['ArrowDown', 'ArrowUp', 'Escape'].includes(event.key) || (event.key === 'Enter' && !event.shiftKey)) {
      event.preventDefault(); event.stopImmediatePropagation();
      if (event.key === 'Escape') { suppressed = true; close(); }
      else if (event.key === 'Enter') choose(matches[selected]);
      else if (matches.length) { selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length; highlight(); }
    }
  }, true);
  document.addEventListener('pointerdown', event => { if (!menu.contains(event.target) && event.target !== input) close(); });
  document.addEventListener('focusin', event => { if (event.target !== input && !menu.contains(event.target)) close(); });
  window.addEventListener('resize', position);
  document.addEventListener('scroll', position, true);
  window.addEventListener('dream:session', () => { generation++; commands = null; failure = ''; request?.abort(); request = null; close(); });
  window.addEventListener('dream:navigate', close);
})();
