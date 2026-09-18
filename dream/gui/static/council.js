/* Explicit Council controls on the authenticated Studio lifecycle API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const button = document.createElement('button');
  button.id = 'dream-council-open'; button.textContent = 'Council';
  button.setAttribute('aria-haspopup', 'dialog'); button.setAttribute('aria-controls', 'dream-council');
  document.querySelector('header .status').before(button);
  const dialog = document.createElement('dialog');
  dialog.id = 'dream-council'; dialog.setAttribute('aria-labelledby', 'council-title');
  dialog.innerHTML = `
    <div class="council-top"><div><h2 id="council-title">Council</h2><p>Choose your main agent. Bring Council members in to work or review.</p></div><button id="council-close" aria-label="Close Council">×</button></div>
    <div class="council-bar"><span id="council-session-state" role="status">Status not loaded</span><button id="council-refresh">Refresh Council</button><button id="council-stop" hidden>Stop active operation</button></div>
    <p id="council-feedback" role="status" hidden></p>
    <p id="council-main-warning" class="council-error" role="status" hidden></p>
    <div class="council-scroll">
      <form id="council-config-form"><fieldset id="council-fields" disabled>
        <section><h3>Main agent <span>Orchestrator</span></h3><p class="council-help">Changes apply between turns. Dream keeps this conversation and workspace; provider-private sessions do not transfer.</p>
          <div class="council-grid"><label>Main provider<select id="council-main"></select></label><label>Main model<select id="council-main-model" aria-label="Main model"></select></label></div>
          <p id="council-main-note" class="council-help"></p>
          <label>Main effort<select id="council-main-effort" aria-label="Main effort" aria-describedby="council-effort-note"></select></label><p id="council-effort-note" class="council-help"></p>
        </section>
        <section><h3>Members <span>Active work or independent review</span></h3><p class="council-help">Choose models by name and set their effort. Members can take an editing turn or provide a read-only review. Applying the roster does not run a task or load local weights.</p><div id="council-advisors"></div></section>
        <details><summary>Consultation limits</summary><div class="council-grid"><label>Concurrent advisors<input id="council-concurrency" type="number" min="1" max="32" required></label><label>Timeout in seconds<input id="council-timeout" type="number" min="0" max="3600" step="any" required></label></div></details>
        <div class="council-actions"><button type="submit" id="council-apply" class="council-primary">Apply Council</button><button type="button" id="council-reset">Use current configuration</button><span id="council-draft" class="council-help"></span></div>
      </fieldset></form>
      <section><h3>Bring members in</h3><p class="council-help">Assign work gives members tools in this workspace using your current permission mode. A team relay runs members in order, then returns to your main agent. Review requests are read-only and can run together.</p>
        <p id="council-legal-note" class="council-help" hidden>Legal review is enabled and requires sources. Ask in the conversation to use Council tools with source evidence. This panel cannot submit legal reviews.</p>
        <label>Task or review question<textarea aria-label="Question for advisors" id="council-question" rows="3" maxlength="8000" placeholder="Polish the saved animation, verify playback, and report the changes."></textarea></label>
        <div class="council-ask"><label>Selected member<select id="council-target"></select></label><button id="council-ask-one">Review with selected member</button><button id="council-ask-all" class="council-primary">Review with council</button></div>
        <div class="council-actions"><button id="council-work-one" class="council-primary">Assign to selected member</button><button id="council-work-all">Run team relay</button></div><p class="council-help">Editing members take turns; simultaneous resident editing sessions are not yet supported. Local members attach to an existing server.</p>
        <div id="council-results" aria-label="Advisor answers"></div>
      </section>
    </div>`;
  document.body.append(dialog);
  let status = null, pending = '', dirty = false, stopping = false, uncertain = false;
  const rows = new Map();
  const bounded = (value, limit = 24000) => String(value ?? '').slice(0, limit);
  function message(text, error = false) {
    const node = $('council-feedback'); node.hidden = false; node.textContent = bounded(text, 2000);
    node.classList.toggle('council-error', error); node.setAttribute('role', error ? 'alert' : 'status');
  }
  async function api(payload) {
    const abort = new AbortController();
    const seconds = payload.action === 'council_ask' ? Math.max(120, Number(status?.config?.timeout_seconds || 120) * Math.max(1, status?.config?.advisors?.length || 1) + 30) : 120;
    const timer = payload.action === 'council_work' ? null : setTimeout(() => abort.abort(), seconds * 1000);
    try {
      const response = await fetch('/api/control', {method:'POST', cache:'no-store', signal:abort.signal,
        headers:{'X-Dream-Token':TOKEN, 'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      let data;
      try { data = await response.json(); } catch { uncertain = true; throw new Error('Unreadable response. Refresh Council to check the operation before retrying.'); }
      if(!response.ok || data.error) throw new Error(response.status === 401 ? 'This Studio token is no longer authorized. Reopen Studio from the running terminal.' : bounded(data.error || `Request failed (${response.status}).`, 1500));
      if(data.ok !== true || !data.result) { uncertain = true; throw new Error('The action was not confirmed. Refresh Council before retrying.'); }
      return data.result;
    } catch(error) {
      if(error.name === 'AbortError' || error instanceof TypeError) {
        uncertain = true;
        throw new Error('Connection interrupted or timed out. The operation may still be running. Refresh Council before retrying.');
      }
      throw error;
    } finally { clearTimeout(timer); }
  }
  function option(select, value, label, disabled = false) {
    const node = document.createElement('option'); node.value = value; node.textContent = bounded(label, 200); node.disabled = disabled; select.append(node);
  }
  const customModels = new Map();
  function modelValue(select) {
    return select.value === '__custom__' ? customModels.get(select).value.trim() : select.value;
  }
  function modelOptions(select, choice, selected = '') {
    let custom = customModels.get(select);
    if(!custom) {
      custom = document.createElement('input'); custom.maxLength = 256; custom.autocomplete = 'off'; custom.spellcheck = false;
      custom.setAttribute('aria-label', (select.getAttribute('aria-label') || 'Main model') + ' custom ID');
      custom.placeholder = 'Custom model ID'; select.parentElement.after(custom); customModels.set(select, custom);
      select.addEventListener('change', () => { custom.hidden = select.value !== '__custom__'; });
    }
    select.replaceChildren(); option(select, '', 'Provider default');
    const models = Array.isArray(choice?.models) ? choice.models : [];
    for(const model of models) option(select, model.id, model.label || model.id);
    if(selected && !models.some(m => m.id === selected)) option(select, selected, selected + ' · configured');
    option(select, '__custom__', 'Custom model…'); select.value = selected || ''; custom.value = ''; custom.hidden = true;
  }
  function modelEfforts(choice, select) {
    const model = choice?.models?.find(m => m.id === modelValue(select));
    return model && Array.isArray(model.efforts) ? {...choice, efforts:model.efforts} : choice;
  }
  function effortOptions(select, choice, selected = '') {
    select.replaceChildren(); option(select, '', 'Provider default');
    const levels = Array.isArray(choice?.efforts) ? choice.efforts : [];
    for(const level of levels) option(select, level, level);
    // Retain a previously applied value when metadata no longer reports it.
    // The owner can explicitly reset it to default instead of losing it silently.
    if(selected && !levels.includes(selected)) option(select, selected, selected + ' · previously configured');
    select.value = selected || ''; select.disabled = !levels.length && !selected;
  }
  function sync() {
    const unavailable = status?.main_available === false;
    const blocked = !status || !!pending || !!status.busy || uncertain || unavailable;
    $('council-fields').disabled = !status || !!pending;
    $('council-apply').disabled = blocked;
    $('council-reset').disabled = !status || !!pending;
    $('council-refresh').disabled = !!pending;
    $('council-stop').hidden = pending === 'council_configure' || unavailable || !(status?.busy || ['council_ask', 'council_work'].includes(pending) || uncertain);
    $('council-stop').disabled = stopping;
    const askBlocked = blocked || dirty || !$('council-question').value.trim() || !status.config.advisors.length || !!status.config.legal_review;
    $('council-legal-note').hidden = !status?.config.legal_review;
    $('council-ask-one').disabled = askBlocked || !$('council-target').value;
    $('council-ask-all').disabled = askBlocked;
    const workBlocked = blocked || dirty || !$('council-question').value.trim() || !status?.config?.advisors?.length;
    $('council-work-one').disabled = workBlocked || !$('council-target').value;
    $('council-work-all').disabled = workBlocked;
    $('council-draft').textContent = dirty ? 'Draft changes. Apply before asking.' : '';
    $('council-session-state').textContent = pending ? (pending === 'council_status' ? 'Refreshing…' : pending === 'council_ask' ? 'Consulting advisors…' : pending === 'council_work' ? 'Council members working…' : 'Applying Council…') : unavailable ? 'Main agent disconnected' : uncertain ? 'Refresh to check operation' : status?.busy ? 'An operation is active. Refresh after it finishes.' : status ? 'Ready between turns' : 'Status unavailable';
    $('council-main-warning').hidden = !unavailable && !status?.warning;
    $('council-main-warning').textContent = bounded(status?.warning || (unavailable ? 'The main agent is unavailable. Start a new Dream application before continuing.' : ''), 2000);
    dialog.setAttribute('aria-busy', String(!!pending));
  }
  function renderDraft() {
    const cfg = status.config;
    $('council-main').replaceChildren(); for(const row of rows.values()) customModels.delete(row.model); rows.clear(); $('council-advisors').replaceChildren();
    for(const choice of status.choices) {
      option($('council-main'), choice.key, choice.label + (choice.available ? '' : ' · unavailable'), !choice.available && choice.key !== cfg.orchestrator);
      const row = document.createElement('div'); row.className = 'council-advisor';
      const toggle = document.createElement('input'); toggle.type = 'checkbox'; toggle.checked = cfg.advisors.includes(choice.key);
      toggle.setAttribute('aria-label', `Enable ${choice.label}`); toggle.disabled = !choice.available && !toggle.checked;
      const label = document.createElement('label'); label.className = 'council-check'; label.append(toggle, document.createTextNode(bounded(choice.label, 100)));
      const note = document.createElement('p'); note.className = 'council-help'; note.textContent = bounded(choice.note, 300);
      const identity = document.createElement('div'); identity.append(label, note);
      const modelLabel = document.createElement('label'); modelLabel.textContent = 'Member model';
      const model = document.createElement('select');
      model.setAttribute('aria-label', `${choice.label} member model`); modelLabel.append(model); modelOptions(model, choice, cfg.advisor_models?.[choice.key]);
      const effortLabel = document.createElement('label'); effortLabel.textContent = 'Member effort';
      const effort = document.createElement('select'); effort.setAttribute('aria-label', `${choice.label} member effort`);
      effortOptions(effort, modelEfforts(choice, model), cfg.advisor_efforts?.[choice.key]); effortLabel.append(effort);
      const effortNote = document.createElement('p'); effortNote.className = 'council-help'; effortNote.textContent = bounded(choice.effort_note, 300);
      const settings = document.createElement('div'); settings.className = 'council-advisor-settings'; settings.append(modelLabel, customModels.get(model), effortLabel, effortNote);
      row.append(identity, settings); $('council-advisors').append(row);
      rows.set(choice.key, {toggle, model, effort, row, choice});
      model.addEventListener('change', () => { effortOptions(effort, modelEfforts(choice, model)); dirty = true; updateMain(); sync(); });
    }
    $('council-main').value = cfg.orchestrator;
    modelOptions($('council-main-model'), status.choices.find(c => c.key === cfg.orchestrator), status.model);
    effortOptions($('council-main-effort'), modelEfforts(status.choices.find(c => c.key === cfg.orchestrator), $('council-main-model')), cfg.orchestrator_effort);
    $('council-concurrency').value = cfg.max_concurrency;
    $('council-timeout').value = cfg.timeout_seconds;
    dirty = false; updateMain();
  }
  function updateMain() {
    const main = $('council-main').value;
    const choice = status.choices.find(c => c.key === main);
    $('council-main-note').textContent = bounded([choice?.note, choice?.model_note].filter(Boolean).join(' '), 600);
    $('council-effort-note').textContent = bounded(status.choices.find(c => c.key === main)?.effort_note, 300);
    for(const row of rows.values()) {
      row.toggle.disabled = !row.choice.available && !row.toggle.checked;
      row.model.disabled = !row.toggle.checked;
      customModels.get(row.model).disabled = !row.toggle.checked;
      row.effort.disabled = !row.toggle.checked || (!modelEfforts(row.choice, row.model)?.efforts?.length && !row.effort.value);
    }
  }
  function acceptStatus(data, replaceDraft = false) {
    if(!data.config || !Array.isArray(data.config.advisors) || !Array.isArray(data.choices) || typeof data.busy !== 'boolean') {
      uncertain = true; throw new Error('Council status is incomplete. Refresh before making changes.');
    }
    status = data; uncertain = false;
    if(!dirty || replaceDraft) renderDraft();
    const previous = $('council-target').value; $('council-target').replaceChildren();
    for(const key of data.config.advisors) {
      const choice = data.choices.find(c => c.key === key), model = data.config.advisor_models?.[key];
      const name = choice?.models?.find(m => m.id === model)?.label || model;
      option($('council-target'), key, (choice?.label || key) + (name ? ' · ' + name : ' · provider default'));
    }
    if(data.config.advisors.includes(previous)) $('council-target').value = previous;
  }
  async function refresh() {
    if(pending) return;
    pending = 'council_status'; sync();
    try { acceptStatus(await api({action:'council_status'})); message(dirty ? 'Status refreshed. Your draft changes are preserved.' : 'Current Council loaded.'); }
    catch(error) { message(error.message, true); }
    finally { pending = ''; sync(); }
  }
  $('council-config-form').addEventListener('input', () => { dirty = true; updateMain(); sync(); });
  $('council-main').addEventListener('change', () => {
    modelOptions($('council-main-model'), status.choices.find(c => c.key === $('council-main').value));
    effortOptions($('council-main-effort'), status.choices.find(c => c.key === $('council-main').value));
    dirty = true; updateMain(); sync();
  });
  $('council-main-model').addEventListener('change', () => {
    effortOptions($('council-main-effort'), modelEfforts(status.choices.find(c => c.key === $('council-main').value), $('council-main-model')));
    dirty = true; sync();
  });
  $('council-question').addEventListener('input', sync);
  $('council-target').addEventListener('change', sync);
  $('council-config-form').onsubmit = async event => {
    event.preventDefault(); if($('council-apply').disabled) return;
    const advisors = [], advisor_models = {}, advisor_efforts = {};
    for(const [key, row] of rows) if(row.toggle.checked) {
      advisors.push(key);
      if(modelValue(row.model)) advisor_models[key] = modelValue(row.model);
      if(row.effort.value) advisor_efforts[key] = row.effort.value;
    }
    const config = {...status.config, orchestrator:$('council-main').value, advisors, advisor_models,
      orchestrator_effort:$('council-main-effort').value || null, advisor_efforts,
      max_concurrency:Number($('council-concurrency').value), timeout_seconds:Number($('council-timeout').value)};
    const model = modelValue($('council-main-model'));
    pending = 'council_configure'; sync(); message('Applying Council. Waiting for the session to confirm…');
    try { acceptStatus(await api({action:'council_configure', config, model}), true); message('Council applied. The next turn uses this main agent and roster.'); }
    catch(error) { message(error.message, true); }
    finally { pending = ''; sync(); }
  };
  async function ask(one) {
    if($(one ? 'council-ask-one' : 'council-ask-all').disabled) return;
    const payload = {action:'council_ask', question:$('council-question').value.trim()};
    if(one) payload.advisor = $('council-target').value;
    pending = 'council_ask'; sync(); message('Consulting advisors. Stop is available while they work.');
    try {
      const result = await api(payload);
      if(!Array.isArray(result.results)) throw new Error('No readable advisor results were returned. Check the conversation.');
      $('council-results').replaceChildren();
      for(const answer of result.results.slice(0, 16)) {
        const article = document.createElement('article'); article.className = 'council-answer';
        const heading = document.createElement('h4'); heading.textContent = bounded(answer.label || answer.advisor, 100) + (answer.model ? ' · ' + bounded(answer.model, 150) : '');
        const body = document.createElement('pre'); body.tabIndex = 0; body.textContent = bounded(answer.answer || answer.error || 'No answer returned.');
        article.append(heading, body);
        if(answer.error) { article.classList.add('council-error'); if(answer.answer) { const error = document.createElement('p'); error.textContent = bounded(answer.error, 2000); article.append(error); } }
        $('council-results').append(article);
      }
      message('Consultation finished. Each answer and any reported failure appears below.');
    } catch(error) { message(error.message, true); }
    finally { pending = ''; sync(); }
  }
  async function work(one) {
    if($(one ? 'council-work-one' : 'council-work-all').disabled) return;
    const payload = {action:'council_work', question:$('council-question').value.trim()};
    if(one) payload.advisor = $('council-target').value;
    pending = 'council_work'; sync(); message('Council work started. Follow the conversation for tools, changes and approvals.');
    try {
      const result = await api(payload);
      if(!Array.isArray(result.completed) || result.main_restored !== true) {
        uncertain = true; throw new Error('Work completion or main restoration was not confirmed. Refresh Council and inspect the conversation before retrying.');
      }
      message('Council work turns finished; control returned to your main agent. Review the conversation and changed files.');
    } catch(error) { uncertain = true; message(error.message + ' Refresh Council before another task.', true); }
    finally { pending = ''; sync(); }
  }
  $('council-work-one').onclick = () => work(true);
  $('council-work-all').onclick = () => work(false);
  $('council-ask-one').onclick = () => ask(true);
  $('council-ask-all').onclick = () => ask(false);
  $('council-refresh').onclick = refresh;
  $('council-reset').onclick = () => { renderDraft(); sync(); message('Current applied configuration restored.'); };
  $('council-stop').onclick = async () => {
    if(stopping || $('council-stop').hidden) return; stopping = true; sync();
    try {
      const result = await api({action:'interrupt'});
      if(result.interrupted === true) message('Stop requested. Wait for the operation to finish, then refresh Council.');
      else if(result.interrupted === false) message('No cancellable operation was found. Refresh Council to check the current session.');
      else message('Cancellation was not confirmed. Refresh Council to check the current session.', true);
    }
    catch(error) { message(error.message, true); }
    finally { stopping = false; sync(); }
  };
  button.onclick = () => { dialog.showModal(); if(!pending) refresh(); };
  $('council-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => button.focus());
  sync();
})();
