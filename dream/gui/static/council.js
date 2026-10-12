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
        <section><h3>Members <span>Active work or independent review</span></h3><p class="council-help">Choose models by name and set their effort. Members can take an editing turn or provide an independent assessment. Native CLI permissions govern their own tools. Applying the roster does not run a task or load local weights.</p><div id="council-advisors"></div></section>
        <details><summary>Consultation limits</summary><div class="council-grid"><label>Concurrent advisors<input id="council-concurrency" type="number" min="1" max="32" required></label><label>Timeout in seconds<input id="council-timeout" type="number" min="0" max="3600" step="any" required></label></div></details>
        <div class="council-actions"><button type="submit" id="council-apply" class="council-primary">Apply Council</button><button type="button" id="council-reset">Use current configuration</button><span id="council-draft" class="council-help"></span></div>
      </fieldset></form>
      <section id="council-signin"><h3>Sign in <span>Provider accounts</span></h3><p class="council-help">Opens the provider's own sign-in in a separate window. You sign in there or in your browser. The Dream app displays that window; Dream does not read or save what is typed or shown in it, does not send it to agents, and checks account status separately from opening the sign-in window. What the provider's own program does with your account is up to that program. You can do this while Dream is working.</p>
        <div class="council-ask"><label for="council-login-provider">Provider<select id="council-login-provider" aria-label="Provider" aria-describedby="council-login-note"></select></label><button type="button" id="council-login-start">Open sign-in window</button></div>
        <p id="council-login-note" class="council-help"></p>
        <p id="council-login-feedback" role="status" tabindex="-1" hidden></p>
      </section>
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
  const FRONTIERS = [['anthropic', 'Claude'], ['grok', 'Grok'], ['antigravity', 'Gemini'], ['codex', 'Codex']];
  let frontierMessage = '', localMain = null;
  const frontierSelections = new Map();
  let accounts = new Map(), accountsReading = false;
  function frontierState() {
    const controls = {};
    for(const [key, label] of FRONTIERS) {
      const account = accounts.get(key);
      const signedIn = account?.status === 'signed_in';
      const accountStatus = typeof account?.status === 'string' ? account.status : 'unknown';
      const detail = typeof account?.detail === 'string' ? account.detail : 'Account status has not been checked.';
      const labelStatus = signedIn ? 'signed in' : accountStatus === 'signed_out' ? 'signed out' :
        accountStatus === 'unavailable' ? 'CLI unavailable' : 'account unverified';
      controls[key] = {on:signedIn, signed_in:signedIn, status:accountStatus, enabled:!loginBusy,
        username:signedIn && typeof account?.username === 'string' ? account.username.slice(0, 160) : '',
        title:`${label} · ${labelStatus} — ${detail}. Click to open sign-in.`};
    }
    return {controls, message:frontierMessage, loginBusy};
  }
  async function refreshAccounts() {
    if(accountsReading) return;
    accountsReading = true;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch('/api/accounts', {headers:{'X-Dream-Token':TOKEN}, cache:'no-store', signal:controller.signal});
      const data = await response.json();
      if(!response.ok || !Array.isArray(data.accounts)) throw new Error('Account status is unavailable.');
      accounts = new Map(data.accounts.filter(a => a && FRONTIERS.some(([key]) => key === a.provider)).map(a => [a.provider,a]));
    } catch {
      accounts = new Map(FRONTIERS.map(([provider]) => [provider,{status:'unknown',detail:'Account status could not be checked. Refresh or open sign-in.'}]));
    } finally { clearTimeout(timer); accountsReading = false; frontierSync(); }
  }
  function frontierSync() {
    window.dispatchEvent(new CustomEvent('dream:frontier-state', {detail:frontierState()}));
  }
  function frontierNote(text) { frontierMessage = bounded(text, 1500); frontierSync(); }
  async function toggleFrontier(key, requested) {
    if(!FRONTIERS.some(([provider]) => provider === key) || pending) return;
    // Read current server state before changing it: another surface may have changed the roster or started work.
    pending = 'council_status'; sync();
    try {
      acceptStatus(await api({action:'council_status'}));
      if(status.busy || status.main_available === false || !status.choices.some(c => c.key === key && c.available))
        throw new Error('This frontier cannot change now. Wait for the active turn or sign in and refresh Council.');
      const cfg = {...status.config, advisors:[...status.config.advisors],
        advisor_models:{...status.config.advisor_models}, advisor_efforts:{...status.config.advisor_efforts}};
      const on = cfg.orchestrator === key || cfg.advisors.includes(key);
      const enable = typeof requested === 'boolean' ? requested : !on;
      if(enable === on) return;
      let model = status.model;
      if(enable) {
        const saved = frontierSelections.get(key);
        if(!FRONTIERS.some(([provider]) => provider === cfg.orchestrator)) {
          model = saved?.model || '';
          cfg.orchestrator = key; cfg.orchestrator_effort = saved?.effort || null;
          cfg.advisors = cfg.advisors.filter(provider => provider !== key);
        } else {
          cfg.advisors.push(key);
          if(saved?.model) cfg.advisor_models[key] = saved.model;
          if(saved?.effort) cfg.advisor_efforts[key] = saved.effort;
        }
      } else if(cfg.orchestrator === key) {
        const next = cfg.advisors.find(provider => FRONTIERS.some(([frontier]) => frontier === provider) && status.choices.some(c => c.key === provider && c.available));
        if(next) {
          model = cfg.advisor_models[next] || ''; cfg.orchestrator = next;
          cfg.orchestrator_effort = cfg.advisor_efforts[next] || null;
          cfg.advisors = cfg.advisors.filter(provider => provider !== next && provider !== key);
        } else if(localMain && status.choices.some(c => c.key === localMain.provider && c.available)) {
          cfg.orchestrator = localMain.provider; model = localMain.model; cfg.orchestrator_effort = localMain.effort;
          cfg.advisors = cfg.advisors.filter(provider => provider !== key);
        } else throw new Error('Turn on another frontier before switching off the main agent.');
      } else cfg.advisors = cfg.advisors.filter(provider => provider !== key);
      for(const provider of Object.keys(cfg.advisor_models)) if(!cfg.advisors.includes(provider)) delete cfg.advisor_models[provider];
      for(const provider of Object.keys(cfg.advisor_efforts)) if(!cfg.advisors.includes(provider)) delete cfg.advisor_efforts[provider];
      pending = 'council_configure'; sync(); frontierNote('Applying frontier selection…');
      acceptStatus(await api({action:'council_configure', config:cfg, model}));
      frontierNote('Frontier selection applied.');
    } catch(error) { frontierNote(error.message); }
    finally { pending = ''; sync(); }
  }
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
    for(const level of levels) option(select, level,
      choice?.key === 'gemini' ? (level === 'on' ? 'On · high thinking' : 'Off · reduced thinking') : level);
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
    frontierSync();
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
    for(const [key] of FRONTIERS) {
      if(data.config.orchestrator === key) frontierSelections.set(key, {model:data.model, effort:data.config.orchestrator_effort});
      else if(data.config.advisors.includes(key)) frontierSelections.set(key, {model:data.config.advisor_models?.[key], effort:data.config.advisor_efforts?.[key]});
    }
    if(!localMain && !FRONTIERS.some(([key]) => key === data.config.orchestrator))
      localMain = {provider:data.config.orchestrator, model:data.model, effort:data.config.orchestrator_effort};
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
  // --- Sign in (DREAM-246). Separate from the Council state above: it has its own request, its own feedback and
  // never waits for a turn. The only thing sent is the provider's name; the session holds the fixed commands.
  const LOGIN_PROVIDERS = [['anthropic', 'Claude'], ['codex', 'ChatGPT (Codex)'], ['grok', 'Grok'], ['antigravity', 'Gemini (Antigravity)']];
  let loginInfo = null, loginBusy = false, loginRun = 0;
  for(const [key, label] of LOGIN_PROVIDERS) option($('council-login-provider'), key, label);
  function loginMessage(text, error = false) {
    const node = $('council-login-feedback'); node.hidden = false; node.textContent = bounded(text, 2000);
    node.classList.toggle('council-error', error); node.setAttribute('role', error ? 'alert' : 'status');
    if(error) node.focus();
    frontierNote(text);
  }
  async function loginApi(payload) {
    const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 15000);
    try {
      const response = await fetch('/api/control', {method:'POST', cache:'no-store', signal:abort.signal,
        headers:{'X-Dream-Token':TOKEN, 'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      let data = null;
      try { data = await response.json(); } catch { /* reported below */ }
      if(response.status === 401) throw new Error('This Studio token is no longer authorized. Reopen Studio from the running terminal.');
      // A refusal the session wrote itself says what happened. Any other answer that is not a confirmed result
      // (unreadable, an unexplained error, no result) was still sent: its outcome is unknown, and nothing is sent again.
      if(data && typeof data.error === 'string' && data.error) throw new Error(bounded(data.error, 1500));
      if(!response.ok || !data || typeof data !== 'object' || data.ok !== true || !data.result || typeof data.result !== 'object')
        throw new Error(`The session's answer could not be read${response.ok ? '' : ` (HTTP ${response.status})`}. It is not known whether a sign-in window opened; look for one before trying again.`);
      return data.result;
    } catch(error) {
      if(error.name === 'AbortError' || error instanceof TypeError) throw new Error('The session did not answer. It is not known whether a sign-in window opened; look for one before trying again.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function loginSync() {
    const key = $('council-login-provider').value, target = loginInfo?.targets?.find(t => t.key === key);
    const noDesktop = loginInfo?.desktop === false;
    $('council-login-start').disabled = loginBusy || noDesktop;
    const parts = [];
    if(target?.note) parts.push(target.note);
    // What the desktop window last reported, not a check of any account: one sign-in window is open at a time.
    if(typeof loginInfo?.open?.label === 'string') parts.push(`A sign-in window is open for ${loginInfo.open.label}. Finish or close it before opening another.`);
    if(noDesktop) parts.push('This page is not inside the Dream desktop window, so it cannot open a sign-in window.' + (target ? ` In a separate terminal of your own, run: ${target.command}` : ''));
    $('council-login-note').textContent = bounded(parts.join(' '), 600);
    frontierSync();
  }
  async function loginRefresh() {
    try { const info = await loginApi({action:'provider_login_status'}); if(Array.isArray(info.targets)) loginInfo = info; }
    catch { /* the button stays available; the session answers when it is pressed */ }
    loginSync();
  }
  async function loginStart(provider) {
    if(typeof provider === 'string' && FRONTIERS.some(([key]) => key === provider)) $('council-login-provider').value = provider;
    if($('council-login-start').disabled) return;
    const run = ++loginRun; loginBusy = true; loginSync(); loginMessage('Asking for the sign-in window…');
    try {
      let result = await loginApi({action:'provider_login', provider:$('council-login-provider').value});
      const unread = 'The session\'s answer could not be read. It is not known whether a sign-in window opened; look for one before trying again.';
      for(let tries = 0; result.status === 'queued' && tries < 30 && run === loginRun; tries++) {
        if(typeof result.id !== 'string' || typeof result.message !== 'string') throw new Error(unread);   // only this request is asked about, never "the newest"
        loginMessage(result.message);
        await new Promise(resolve => setTimeout(resolve, 1000));
        const info = await loginApi({action:'provider_login_status', id:result.id});
        if(!info.request) throw new Error('The session no longer knows this request. Look for a sign-in window before trying again.');
        result = info.request;
      }
      if(result.status === 'queued') throw new Error('The desktop window has not answered yet. Look for a sign-in window before trying again.');
      if(typeof result.message !== 'string' || !result.message) throw new Error(unread);   // never "done" for an answer that said nothing
      loginMessage(result.message, !['launched', 'closed', 'manual'].includes(result.status));
    } catch(error) { loginMessage(error.message, true); }
    finally { if(run === loginRun) { loginBusy = false; loginSync(); loginRefresh(); refreshAccounts(); } }   // the button first; then whether a window is reported open
  }
  $('council-login-provider').addEventListener('change', loginSync);
  $('council-login-start').onclick = loginStart;
  loginSync();
  let returnFocus = button;
  function open(source = button) {
    returnFocus = source;
    if(!dialog.open) dialog.showModal();
    loginRefresh(); if(!pending) refresh();
  }
  button.onclick = () => open();
  window.addEventListener('dream:council-open', event => open($(event.detail?.source) || button));
  window.DreamFrontiers = {state:frontierState, toggle:toggleFrontier, refresh:refreshAccounts, refreshCouncil:refresh, login:loginStart};
  window.addEventListener('dream:frontier-toggle', event => toggleFrontier(event.detail?.provider, event.detail?.enabled));
  window.addEventListener('dream:frontier-login', event => {
    if(FRONTIERS.some(([key]) => key === event.detail?.provider)) loginStart(event.detail.provider);
  });
  window.addEventListener('dream:session', refreshAccounts);
  window.addEventListener('dream:account-refresh', refreshAccounts);
  setInterval(refreshAccounts, 15000);
  $('council-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => returnFocus.focus());
  sync();
})();
