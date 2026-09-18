/* Display only. This module never requests model reasoning or dispatches tools. */
(() => {
  'use strict';
  const MAX_TEXT = 65536, MAX_TOOL = 20000, MAX_ITEMS = 100;
  const modes = new Set(['compact', 'detailed']);
  const node = (tag, text, className) => {
    const el = document.createElement(tag); el.textContent = text ?? '';
    if(className) el.className = className;
    return el;
  };
  const stringify = value => typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2);
  function mount(stream) {
    let mode = 'detailed';
    try { const saved = localStorage.getItem('dream.feed.detail'); if(modes.has(saved)) mode = saved; } catch {}
    const toolbar = node('div', '', 'feed-toolbar');
    const label = node('label', 'Feed detail '), select = document.createElement('select');
    select.id = 'feed-detail'; select.setAttribute('aria-label', 'Feed detail');
    select.append(new Option('Detailed', 'detailed'), new Option('Compact', 'compact')); select.value = mode;
    label.append(select); toolbar.append(label, node('span', 'Only activity reported by the agent is shown.', 'feed-note'));
    stream.before(toolbar);
    const workers = new Map(), textState = new WeakMap();
    function applyMode() {
      stream.dataset.feedDetail = mode;
      stream.querySelectorAll('details[data-feed-auto]').forEach(el => { el.open = mode === 'detailed'; });
    }
    select.onchange = () => { mode = select.value; try { localStorage.setItem('dream.feed.detail', mode); } catch {} applyMode(); };
    applyMode();
    function detail(className, title) {
      const el = node('details', '', className); el.dataset.feedAuto = 'true'; el.open = mode === 'detailed';
      el.append(node('summary', title)); return el;
    }
    function appendText(el, delta, limit = MAX_TEXT) {
      const state = textState.get(el) || {text:'', total:0};
      const value = String(delta ?? ''); state.total += value.length;
      state.text += value.slice(0, Math.max(0, limit - state.text.length));
      textState.set(el, state);
      el.textContent = state.text + (state.total > limit ? `\n[Display truncated at ${limit.toLocaleString()} characters; ${state.total.toLocaleString()} received.]` : '');
    }
    function thinking(parent) {
      const el = detail('think', 'Thinking · reported by the model');
      el.append(node('div', '')); parent.append(el); return el;
    }
    function inspectable(parent, label, raw, preview = false) {
      const value = stringify(raw), retained = value.slice(0, MAX_TOOL), truncated = value.length > MAX_TOOL;
      if(preview) {
        const excerpt = retained.split('\n').slice(0, 10).join('\n').slice(0, 800);
        parent.append(node('pre', excerpt || '[Empty tool result]', 'feed-preview'));
      }
      const button = node('button', `${label} · ${value.length.toLocaleString()} ${preview ? 'returned characters' : 'request characters'}${truncated ? ' · display limited to 20,000' : ''}`, 'feed-inspect');
      button.type = 'button'; button.setAttribute('aria-expanded', 'false');
      button.title = preview ? 'Size of the returned tool text, before display truncation.' : 'Size of the tool arguments (such as the file path), not the amount of file content read.';
      const pre = node('pre', retained + (truncated ? '\n[Display truncated. The full payload is not retained in this view.]' : ''), 'feed-full');
      pre.hidden = true;
      button.onclick = () => { pre.hidden = !pre.hidden; button.setAttribute('aria-expanded', String(!pre.hidden)); };
      parent.append(button, pre);
    }
    function tool(parent, data) {
      const el = detail('tool', '');
      const summary = el.querySelector('summary');
      const args = stringify(data.input ?? {});
      summary.append(node('span', String(data.name || 'tool'), 'tname'), node('span', args.slice(0, 110) + (args.length > 110 ? '…' : ''), 'targ'), node('span', 'Running', 'badge'));
      inspectable(el, 'Inspect arguments', args);
      parent.append(el); return el;
    }
    function result(el, data) {
      el.classList.toggle('err', !!data.is_error);
      if(data.is_error && mode === 'compact') el.open = true;
      el.querySelector('.badge').textContent = data.is_error ? 'Failed' : 'Completed';
      inspectable(el, 'Inspect result', data.content ?? '', true);
    }
    function reset() { workers.clear(); stream.querySelectorAll('.agent-card,.agent-feed-limit').forEach(el => el.remove()); }
    function activity(data) {
      if(!data || typeof data.run_id !== 'string' || !data.run_id || data.run_id.length > 200) return;
      let worker = workers.get(data.run_id);
      if(!worker) {
        if(workers.size >= 32) {
          const finished = [...workers].find(([,value]) => value.terminal);
          if(finished) { finished[1].card.remove(); workers.delete(finished[0]); }
          if(!stream.querySelector('.agent-feed-limit')) stream.append(node('p', 'Agent activity display is limited to 32 workers; older completed activity or additional concurrent workers may be omitted.', 'sys agent-feed-limit'));
          if(!finished) return;
        }
        const card = detail('agent-card', ''); card.dataset.runId = data.run_id;
        const summary = card.querySelector('summary');
        summary.append(node('strong', String(data.agent || data.name || 'Agent').slice(0, 160), 'agent-name'), node('span', 'Activity reported', 'agent-state'));
        const body = node('div', '', 'agent-body'), statusDetail = node('p', '', 'agent-status-detail');
        statusDetail.hidden = true; body.append(statusDetail); card.append(body); stream.append(card);
        worker = {card, body, statusDetail, tools:new Map(), thinking:null, answer:null, count:0}; workers.set(data.run_id, worker);
      }
      const event = data.kind || data.event || data.phase;
      if(event === 'status') {
        const allowed = {queued:'Queued', request:'Request started · awaiting response', running:'Running', complete:'Completed', completed:'Completed', failed:'Failed', interrupted:'Interrupted', unknown:'Unknown'};
        const label = allowed[data.status];
        if(label) worker.card.querySelector('.agent-state').textContent = label;
        if(typeof data.text === 'string') { worker.statusDetail.textContent = data.text.slice(0, 1000); worker.statusDetail.hidden = !data.text; }
        if(mode === 'compact' && ['failed','interrupted','unknown'].includes(data.status)) worker.card.open = true;
        if(['complete','completed','failed','interrupted','unknown'].includes(data.status)) {
          for(const el of worker.tools.values()) { el.querySelector('.badge').textContent = data.status === 'interrupted' ? 'Interrupted' : 'No result reported'; }
          worker.tools.clear(); worker.thinking = null; worker.answer = null; worker.terminal = true;
        }
        return;
      }
      // Bound one worker's rendered records, including repeated request cycles.
      if(worker.count >= MAX_ITEMS && !['tool_result','response'].includes(event)
          && !(event === 'thinking_delta' && worker.thinking) && !(event === 'text_delta' && worker.answer)) {
        if(!worker.body.querySelector('.agent-item-limit')) worker.body.append(node('p', 'Further activity records omitted after 100 items.', 'agent-item-limit'));
        return;
      }
      if(event === 'thinking_delta' || event === 'thinking_report') {
        if(!worker.thinking) { if(worker.count++ >= MAX_ITEMS) return; worker.thinking = thinking(worker.body); }
        if(event === 'thinking_report') worker.thinking.querySelector('summary').textContent = 'Thinking · reported after response';
        appendText(worker.thinking.querySelector('div'), data.text ?? data.delta ?? data.data);
      } else if(event === 'text_delta') {
        worker.thinking = null;
        if(!worker.answer) { if(worker.count++ >= MAX_ITEMS) return; worker.answer = node('pre', '', 'agent-answer'); worker.body.append(worker.answer); }
        appendText(worker.answer, data.text ?? data.delta ?? data.data);
      } else if(event === 'tool_use') {
        worker.thinking = null; worker.answer = null; worker.count++;
        const payload = data.tool || data.data || data;
        const el = tool(worker.body, payload);
        el.querySelector('.badge').textContent = 'Requested';
        if(payload.id) worker.tools.set(payload.id, el);
      } else if(event === 'tool_result') {
        const payload = data.tool || data.data || data, el = worker.tools.get(payload.id);
        if(el) {
          worker.tools.delete(payload.id); result(el, payload);
          if(payload.is_error && mode === 'compact') worker.card.open = true;
          if(data.status === 'interrupted') el.querySelector('.badge').textContent = 'Interrupted';
        }
      } else if(event === 'response') {
        worker.card.querySelector('.agent-state').textContent = 'Response received';
      } else if(event === 'request') {
        worker.thinking = null; worker.answer = null; worker.count++;
        worker.card.querySelector('.agent-state').textContent = 'Request started · awaiting response';
        worker.body.append(node('p', 'Request ' + String(data.request ?? data.request_index ?? '').slice(0, 30), 'agent-request'));
      }
    }
    return {thinking, appendText, tool, result, activity, reset};
  }
  window.DreamFeed = {mount};
})();
