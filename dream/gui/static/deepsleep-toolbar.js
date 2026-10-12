/* One toolbar, existing DeepSleep handlers. Native GTK mirrors bounded DOM state. */
(() => {
  'use strict';
  const root = document.documentElement;
  const controls = document.querySelector('#dream-deepsleep .ds-controls');
  const original = controls?.parentElement;
  const header = document.querySelector('body > header');
  if (!controls || !original || !header) return;
  const selectors = {
    provider: 'select[aria-label="New terminal provider"]',
    profile: 'select[aria-label="Terminal experience"]',
    model: 'select[aria-label="New terminal model"]',
    layout: '#ds-text-layout',
    name: 'input[aria-label="New terminal name"]',
    add: 'button[aria-label="Add terminal"]',
    tile: 'button[aria-label="Tile terminals"]',
    group: 'button[aria-label="Plan a group task"]',
    connections: 'button[aria-label="Message Board: show connections and delivery activity"]',
    preview: 'button[aria-label="Preview artifacts"]',
    refresh: 'button[aria-label="Refresh workspace"]',
    characters: '#ds-character-toggle',
  };
  header.id = header.id || 'dream-shell-header';
  const host = document.createElement('div');
  host.id = 'dream-deepsleep-header';
  host.setAttribute('aria-label', 'DeepSleep workspace controls');
  header.append(host);
  const active = () => root.dataset.dreamView === 'deepsleep';
  const identity = () => ({session_id: window.DREAM_SESSION?.session_id || '', workspace: window.DREAM_SESSION?.workspace || ''});
  const bounded = (s, n) => typeof s === 'string' && s.length <= n;
  function arrange() {
    const native = root.classList.contains('dream-native');
    host.hidden = !active() || native;
    root.classList.toggle('ds-header-controls', active());
    // Move, never clone: selected values, listeners and pending operations survive.
    const destination = native ? original : host;
    if (controls.parentElement !== destination) destination.append(controls);
    original.hidden = active();
    measureHeader();
  }
  function measureHeader() {
    // The board is fixed to the viewport. Mobile navigation is above the
    // header, so its height alone is not the board's viewport offset.
    const bottom = active() && !root.classList.contains('dream-native') ? header.getBoundingClientRect().bottom : 0;
    root.style.setProperty('--ds-header-height', bottom + 'px');
  }
  const layoutObserver = new ResizeObserver(measureHeader);
  layoutObserver.observe(header);
  const navigation = document.getElementById('dream-nav');
  if (navigation) layoutObserver.observe(navigation);
  window.addEventListener('resize', measureHeader);
  function state() {
    const id = identity();
    if (!bounded(id.session_id, 256) || !bounded(id.workspace, 4096)) return null;
    const result = {...id, view: root.dataset.dreamView || '', controls: {}};
    for (const [key, selector] of Object.entries(selectors)) {
      const node = controls.querySelector(selector);
      if (!node) continue;
      const item = {label: (node.getAttribute('aria-label') || node.textContent).slice(0, 100), disabled: node.disabled === true};
      if (node.tagName === 'SELECT') {
        item.value = node.value;
        if (!bounded(item.value, 256)) continue;
        let options = [...node.options].filter(option => bounded(option.value, 256));
        if (options.length > 64) {
          const selected = options.find(option => option.value === node.value);
          options = options.slice(0, 64);
          if (selected && !options.includes(selected)) options[63] = selected;
        }
        item.options = options.map(option => ({value: option.value, label: option.textContent.slice(0, 100), disabled: option.disabled}));
      } else if (node.tagName === 'INPUT') {
        if (!bounded(node.value, 100)) continue;
        item.value = node.value;
      } else item.pressed = node.getAttribute('aria-pressed') === 'true';
      result.controls[key] = item;
    }
    return result;
  }
  function dispatch(message) {
    const id = identity();
    if (!message || typeof message !== 'object' || !active() || !id.session_id ||
        message.session_id !== id.session_id || message.workspace !== id.workspace ||
        !Object.hasOwn(selectors, message.control) ||
        Object.keys(message).some(key => !['session_id', 'workspace', 'control', 'value'].includes(key))) return false;
    const node = controls.querySelector(selectors[message.control]);
    if (!node || node.disabled) return false;
    if (node.tagName === 'SELECT') {
      if (!bounded(message.value, 256) || ![...node.options].some(option => option.value === message.value && !option.disabled)) return false;
      node.value = message.value;
      node.dispatchEvent(new Event('change', {bubbles: true}));
    } else if (node.tagName === 'INPUT') {
      if (!bounded(message.value, 100)) return false;
      node.value = message.value;
      node.dispatchEvent(new Event('input', {bubbles: true}));
    } else {
      if (Object.hasOwn(message, 'value')) return false;
      node.click();
    }
    return true;
  }
  window.DreamDeepSleepToolbar = Object.freeze({state, dispatch});
  new MutationObserver(arrange).observe(root, {attributes: true, attributeFilter: ['class', 'data-dream-view']});
  window.addEventListener('dream:session', arrange);
  arrange();
})();
