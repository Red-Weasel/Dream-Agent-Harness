/* Authenticated user controls. This module never touches the artifact frame or
   sends model prompts; all changes pass through the shared /api/control API. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits:0}) : 'Unavailable';
  const icon = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 5h14M3 10h14M3 15h14"/><path d="M7 2v6m6-1v6m-6-1v6" stroke-width="3"/></svg>';
  const opener = document.createElement('button');
  opener.id = 'dream-controls-open'; opener.innerHTML = icon + '<span>Controls</span>';
  opener.setAttribute('aria-haspopup', 'dialog'); opener.setAttribute('aria-controls', 'dream-controls');
  opener.title = 'Runtime, extensions and learning';
  document.querySelector('header .status').before(opener);
  const recording = document.createElement('button');
  recording.id = 'dream-recording'; recording.hidden = true;
  recording.setAttribute('aria-haspopup', 'dialog'); recording.setAttribute('aria-controls', 'dream-controls');
  opener.before(recording);
  const dialog = document.createElement('dialog');
  dialog.id = 'dream-controls'; dialog.className = 'dc-dialog';
  dialog.setAttribute('aria-labelledby', 'dc-title');
  dialog.innerHTML = `
    <div class="dc-top"><span class="dc-mark">${icon}</span><div class="dc-heading"><h2 id="dc-title">Studio controls</h2><span class="dc-subtitle">Runtime · capabilities · learning</span></div><button id="dc-close" class="dc-quiet" aria-label="Close controls">×</button></div>
    <div class="dc-tabs" role="tablist" aria-label="Studio controls">
      <button id="dc-tab-runtime" role="tab" aria-controls="dc-runtime" aria-selected="true">Runtime</button>
      <button id="dc-tab-extensions" role="tab" aria-controls="dc-extensions" aria-selected="false" tabindex="-1">Extensions</button>
      <button id="dc-tab-learn" role="tab" aria-controls="dc-learn" aria-selected="false" tabindex="-1">Learn</button>
      <button id="dc-refresh" class="dc-refresh dc-quiet" aria-label="Refresh controls" title="Refresh current panel">↻</button>
    </div>
    <div id="dc-feedback" class="dc-feedback dc-notice" role="status" hidden><span></span><button id="dc-dismiss" class="dc-quiet" aria-label="Dismiss control message">×</button></div>
    <div class="dc-scroll">
      <div id="dc-runtime" role="tabpanel" aria-labelledby="dc-tab-runtime">
        <div id="dc-runtime-state" class="dc-notice" role="status"><span>Loading session runtime…</span></div>
        <div id="dc-runtime-content" hidden>
          <section class="dc-section" id="dc-session"></section>
          <section class="dc-section" id="dc-run-budget"></section>
          <section class="dc-section" id="dc-active-settings"><h3>Active settings</h3><p class="dc-help">Settings are not reported by this session.</p></section>
          <section class="dc-section" id="dc-provider-capabilities"><h3>Reported provider capabilities</h3><p class="dc-help">Existing session metadata only. Unknown means the provider has not supplied an explicit value; configured limits are separate.</p><dl id="dc-capability-facts" class="dc-meta"></dl><p id="dc-capability-configured" class="dc-help"></p><p id="dc-capability-warnings" class="dc-help" role="status"></p></section>
          <section class="dc-section" id="dc-coordination"><h3>Local request coordination</h3><p id="dc-coordination-status" class="dc-help" role="status"></p>
            <form id="dc-reconcile-form" hidden><fieldset id="dc-reconcile-fields"><p class="dc-help">A disconnected request may still be running on the server. Check the server before clearing its uncertain state.</p><label><input type="checkbox" id="dc-reconcile-confirm" required> I verified that this request is no longer running on the server.</label><button id="dc-reconcile-submit" type="submit" disabled>Clear reconciled request</button></fieldset></form>
          </section>
          <section class="dc-section" id="dc-performance">
            <div class="dc-row"><h3 class="dc-grow">Performance mode</h3><span id="dc-performance-current" class="dc-pill"></span></div>
            <p class="dc-help">Applies to your next user turn in this session. Shorter output limits can cut answers short. More reasoning can take longer; task quality depends on the model.</p>
            <p id="dc-performance-state" class="dc-help" role="status"></p>
            <form id="dc-performance-form"><fieldset id="dc-performance-fields" disabled>
              <label>Session mode<select id="dc-performance-mode" aria-describedby="dc-performance-preview"><option value="custom">Custom · current settings</option></select></label>
              <p id="dc-performance-preview" class="dc-performance-preview" aria-live="polite"></p>
              <button class="dc-primary" type="submit">Apply to next turn</button>
            </fieldset></form>
          </section>
          <section class="dc-section">
            <div class="dc-row"><h3 class="dc-grow">Next session</h3><span class="dc-pill">Profile</span></div>
            <p class="dc-help">Saved preferences apply to new sessions. This session keeps its current profile; environment overrides still take precedence.</p>
            <form id="dc-profile-form"><fieldset id="dc-profile-fields">
              <label>Runtime profile<select id="dc-profile"><option value="auto">Auto · choose for the provider</option><option value="lean">Lean · compact, single worker</option><option value="balanced">Balanced · compact, parallel work</option><option value="frontier">Frontier · fuller context and tools</option></select></label>
              <p id="dc-profile-note" class="dc-help"></p>
              <label>Image input<select id="dc-vision"><option value="auto">Auto · provider default</option><option value="true">On · image-capable model</option><option value="false">Off · text only</option></select></label>
              <p class="dc-help">Use On only for a model that accepts images. Exact model settings and DREAM_VISION can override this preference.</p>
              <details><summary>Override limits</summary><p class="dc-help">Filled fields replace these limits; blanks remove their saved overrides. Other saved limits and exact model settings are preserved.</p>
                <div class="dc-fields">
                  <label>Context limit <span class="dc-muted">· tokens</span><input id="dc-context_limit" type="number" min="2048" step="1" placeholder="Profile default"></label>
                  <label>Output reserve <span class="dc-muted">· tokens</span><input id="dc-output_tokens" type="number" min="1" step="1" placeholder="Profile default"></label>
                  <label>Parallel workers<input id="dc-max_parallel" type="number" min="1" max="16" step="1" placeholder="Profile default"></label>
                  <label>Idle timeout <span class="dc-muted">· seconds</span><input id="dc-idle_timeout_s" type="number" min="0.1" step="any" placeholder="Profile default"></label>
                </div>
              </details>
              <div class="dc-actions"><button class="dc-primary" type="submit">Save for next session</button></div>
            </fieldset></form>
          </section>
          <section class="dc-section"><div class="dc-row"><h3 class="dc-grow">Execution scope</h3><span id="dc-scope-badge" class="dc-pill"></span></div><p id="dc-scope-status" class="dc-help"></p>
            <details id="dc-scope-details"><summary>Local red-team exercise</summary><div class="dc-danger-zone">
              <p class="dc-help">Enable a bounded exercise in one local directory. The scope expires automatically; ordinary execution policy remains in force.</p>
              <form id="dc-scope-form"><fieldset id="dc-scope-fields"><label>Target directory<input id="dc-target" required placeholder="Workspace-relative or absolute directory" autocomplete="off" spellcheck="false"></label>
                <div class="dc-fields"><label>Expires after <span class="dc-muted">· minutes</span><input id="dc-minutes" type="number" min="1" max="120" step="1" value="15" required></label></div>
                <button type="submit" class="dc-danger">Enable scoped exercise</button></fieldset></form>
              <button id="dc-scope-stop" class="dc-danger" hidden>End exercise now</button>
            </div></details>
          </section>
        </div>
      </div>
      <div id="dc-extensions" role="tabpanel" aria-labelledby="dc-tab-extensions" hidden>
        <div class="dc-row"><div class="dc-grow"><h3>Dream catalog</h3><span class="dc-subtitle">Skills, plugins, MCP, hooks and tools</span></div></div>
        <div id="dc-ext-counts" class="dc-counts"></div>
        <div class="dc-toolbar"><input id="dc-ext-search" type="search" aria-label="Search extensions" placeholder="Find a capability…" autocomplete="off"><select id="dc-ext-kind" aria-label="Extension type"><option value="all">All types</option><option value="skill">Skills</option><option value="plugin">Plugins</option><option value="mcp">MCP</option><option value="hook">Hooks</option><option value="tool">Tools</option></select></div>
        <p class="dc-help">Disabling blocks future exposure and execution. Enabling a previously unloaded package or MCP server may require a new session.</p>
        <div id="dc-ext-state" class="dc-notice" role="status"><span>Loading Dream catalog…</span></div>
        <div id="dc-ext-warnings" hidden></div><div id="dc-ext-list"></div>
        <p id="dc-usage-note" class="dc-help"></p>
      </div>
      <div id="dc-learn" role="tabpanel" aria-labelledby="dc-tab-learn" hidden>
        <div id="dc-learn-state" class="dc-notice" role="status"><span>Checking recording status…</span></div>
        <div id="dc-active" class="dc-recording" hidden><span class="dc-record-light"></span><div class="dc-grow"><strong id="dc-active-name"></strong><p id="dc-active-detail" class="dc-help"></p></div><button id="dc-record-stop" class="dc-danger">Stop recording</button></div>
        <section class="dc-section">
          <h3>Teach by demonstration</h3><p class="dc-help">Capture a region, inspect the evidence, then review a reusable skill. Recording never starts until you press Start.</p>
          <form id="dc-record-form"><fieldset id="dc-record-fields" disabled>
            <label>Demonstration name<input id="dc-record-name" maxlength="100" required placeholder="e.g. Export a design for review" autocomplete="off"></label>
            <div class="dc-fields dc-region">
              <label>X <span class="dc-muted">· px</span><input id="dc-x" type="number" min="0" step="1" placeholder="0" required></label>
              <label>Y <span class="dc-muted">· px</span><input id="dc-y" type="number" min="0" step="1" placeholder="0" required></label>
              <label>Width<input id="dc-width" type="number" min="64" max="7680" step="1" placeholder="800" required></label>
              <label>Height<input id="dc-height" type="number" min="64" max="4320" step="1" placeholder="600" required></label>
            </div>
            <div class="dc-fields"><label>Stop after <span class="dc-muted">· seconds</span><input id="dc-seconds" type="number" min="1" max="900" step="1" value="120" required></label></div>
            <div class="dc-actions"><button class="dc-primary" type="submit">Start recording</button><span class="dc-help">Selected region + visible pointer. No keyboard event log.</span></div>
          </fieldset></form>
          <details><summary>Import a recording instead</summary><p class="dc-help">Direct capture requires X11 and ffmpeg. On Wayland, use your desktop recorder and import its video. Choose a local MKV, MP4, MOV or WebM file under 500 MB.</p>
            <form id="dc-import-form"><fieldset id="dc-import-fields"><label>Local video path<input id="dc-import-path" required placeholder="/path/to/demonstration.webm" autocomplete="off" spellcheck="false"></label><label>Name <span class="dc-muted">· optional</span><input id="dc-import-name" maxlength="100" placeholder="Use file name" autocomplete="off"></label><div class="dc-actions"><button type="submit">Import video</button></div></fieldset></form>
          </details>
        </section>
        <section class="dc-section"><div class="dc-row"><h3 class="dc-grow">Demonstrations</h3><span id="dc-demo-count" class="dc-pill" hidden></span></div>
          <p id="dc-analysis-note" class="dc-help">Model analysis is optional and uses the chosen session provider. Extracting frames does not request analysis.</p>
          <div id="dc-demos"></div><section id="dc-draft" class="dc-draft" hidden aria-label="Skill draft review"></section>
        </section>
      </div>
    </div><div id="dc-pending" class="dc-pending" role="status" hidden></div>`;
  document.body.appendChild(dialog);
  // Recording controls stay visible while scrolling or visiting another tab.
  // The header badge remains available again as soon as the dialog is closed.
  byId('dc-feedback').after(byId('dc-active'));

  let tab = 'runtime', returnFocus = opener, busy = false, runtime = null, extensions = null;
  let learning = null, learningKnown = false, profileEdited = false, profileSaved = false;
  let performanceEdited = false;
  let draftId = null, queued = new Set(), installed = new Map(), demoSignature = '', loading = {};
  let pollTimer, wasRecording = false, scopeDeadline = null, revision = 0, stopping = false;
  const overrideKeys = ['context_limit','output_tokens','max_parallel','idle_timeout_s'];
  const isRecording = () => learning?.active?.status === 'recording';

  async function api(path, payload) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), payload ? 120000 : 12000);
    try {
      const response = await fetch(path, {method:payload ? 'POST' : 'GET', cache:'no-store', signal:abort.signal,
        headers:{'X-Dream-Token':TOKEN, ...(payload ? {'Content-Type':'application/json'} : {})},
        ...(payload ? {body:JSON.stringify(payload)} : {})});
      let data;
      try { data = await response.json(); } catch { throw new Error(`Studio returned an unreadable response (${response.status}). Refresh to check the current state.`); }
      if(!response.ok || data.error) {
        const message = response.status === 401 ? 'This Studio token is no longer authorized. Reopen Studio from the running terminal.' :
          response.status === 404 ? 'This feature is unavailable in this Studio session.' : data.error || `Studio request failed (${response.status}).`;
        throw new Error(String(message));
      }
      if(payload && data.ok !== true) throw new Error('Studio did not confirm this action. Refresh to check the current state.');
      return payload ? data.result : data;
    } catch(error) {
      if(error.name === 'AbortError') throw new Error('Studio took too long to respond. The action may still finish; refresh its status before trying again.');
      if(error instanceof TypeError) throw new Error('Cannot reach Studio. Keep the terminal running, then refresh to reconnect.');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function feedback(message, error = false) {
    const node = byId('dc-feedback'); node.hidden = false;
    node.classList.toggle('dc-error', error); node.setAttribute('role', error ? 'alert' : 'status');
    node.querySelector('span').textContent = message;
  }
  function state(id, message, retry) {
    const node = byId(id); node.hidden = !message; node.classList.toggle('dc-error', !!retry);
    node.setAttribute('role', retry ? 'alert' : 'status'); node.replaceChildren();
    if(!message) return;
    const span = document.createElement('span'); span.textContent = message; node.appendChild(span);
    if(retry) { const button = document.createElement('button'); button.textContent = 'Retry'; button.onclick = retry; node.appendChild(button); }
  }
  async function change(payload, label, done) {
    if(busy) return;
    busy = true; revision++; byId('dc-pending').hidden = false; byId('dc-pending').textContent = label + '…';
    byId('dc-feedback').hidden = true; syncDisabled();
    try {
      const result = await api('/api/control', payload);
      revision++;
      if(payload.action === 'learn_start' && result?.status === 'recording') {
        // A confirmed Start gets a visible Stop immediately, even when an old
        // status request is slow or the follow-up read cannot reach the server.
        learning = {...(learning || {demonstrations:[]}),active:result}; learningKnown = true; renderLearning();
      }
      // A poll begun before this action must not overwrite its confirmed state.
      const key = payload.action.startsWith('learn_') ? 'learning' : ['extension','module_trust'].includes(payload.action) ? 'extensions' : 'runtime';
      if(loading[key]) await loading[key];
      await done(result);
    }
    catch(error) { feedback(error.message, true); }
    finally { busy = false; byId('dc-pending').hidden = true; syncDisabled(); schedulePoll(); }
  }
  function syncDisabled() {
    for(const id of ['dc-profile-fields','dc-scope-fields','dc-import-fields']) byId(id).disabled = busy;
    byId('dc-performance-fields').disabled = busy || runtime?.performance?.supported !== true;
    byId('dc-reconcile-fields').disabled = busy || runtime?.coordination?.can_reconcile !== true;
    byId('dc-reconcile-submit').disabled = busy || runtime?.coordination?.can_reconcile !== true || !byId('dc-reconcile-confirm').checked;
    byId('dc-record-fields').disabled = busy || !learningKnown || isRecording();
    byId('dc-scope-form').hidden = !!runtime?.execution?.red_team;
    byId('dc-scope-stop').hidden = !runtime?.execution?.red_team;
    byId('dc-scope-stop').disabled = busy;
    byId('dc-record-stop').disabled = stopping;
    dialog.querySelectorAll('[data-mutate]').forEach(button => { button.disabled = busy || button.dataset.blocked === 'true'; });
    const install = byId('dc-draft-install');
    if(install) install.disabled = busy || !byId('dc-draft-agree').checked || installed.has(draftId);
  }
  function selectTab(name, focus = false) {
    tab = name;
    for(const key of ['runtime','extensions','learn']) {
      byId('dc-' + key).hidden = key !== name;
      byId('dc-tab-' + key).setAttribute('aria-selected', String(key === name));
      byId('dc-tab-' + key).tabIndex = key === name ? 0 : -1;
    }
    dialog.querySelector('.dc-scroll').scrollTop = 0;
    if(focus) byId('dc-tab-' + name).focus();
    refresh();
  }
  function open(name = tab, source = opener) {
    returnFocus = source;
    if(!dialog.open) dialog.showModal();
    selectTab(name, true);
    if(name !== 'runtime' && !runtime) loadRuntime();
    schedulePoll();
  }
  function refresh() { return tab === 'runtime' ? loadRuntime() : tab === 'extensions' ? loadExtensions() : loadLearning(); }
  opener.onclick = () => open(); recording.onclick = () => open('learn', recording);
  byId('dc-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { (returnFocus.hidden ? opener : returnFocus).focus(); schedulePoll(); });
  // Capture Escape before the companion's inspector/composer shortcuts. Native
  // dialog modality also keeps keyboard and pointer actions out of the canvas.
  dialog.addEventListener('keydown', event => {
    if(event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dialog.close(); }
  });
  byId('dc-dismiss').onclick = () => { byId('dc-feedback').hidden = true; byId('dc-tab-' + tab).focus(); };
  byId('dc-refresh').onclick = () => refresh();
  for(const name of ['runtime','extensions','learn']) byId('dc-tab-' + name).onclick = () => selectTab(name);
  dialog.querySelector('[role=tablist]').addEventListener('keydown', event => {
    if(event.target.getAttribute('role') !== 'tab') return;
    const names = ['runtime','extensions','learn'], at = names.indexOf(tab);
    const next = {ArrowRight:(at+1)%3,ArrowLeft:(at+2)%3,Home:0,End:2}[event.key];
    if(next !== undefined) { event.preventDefault(); selectTab(names[next], true); }
  });

  async function loadRuntime() {
    if(loading.runtime) return loading.runtime;
    loading.runtime = (async () => {
      const readRevision = revision;
      try {
        const data = await api('/api/runtime');
        if(readRevision !== revision) return;
        if(data.available === false || !data.profile) throw new Error('Runtime controls are unavailable until an engine session is running.');
        runtime = data; state('dc-runtime-state', ''); byId('dc-runtime-content').hidden = false;
        renderRuntime();
      } catch(error) { if(readRevision === revision) state('dc-runtime-state', error.message, loadRuntime); }
      finally { delete loading.runtime; }
    })();
    return loading.runtime;
  }
  function renderRuntime() {
    const r = runtime, p = r.profile, c = r.context, run = r.run;
    renderPerformance(); renderCapabilities(); renderActiveSettings(); renderRunBudget(run);
    const context = c ? `${number(c.input_tokens)} / ${number(c.window)}` : 'Not reported';
    const percent = value => Math.max(0, Math.min(100, 100 * value / c.window));
    byId('dc-session').innerHTML = `
      <div class="dc-row"><div class="dc-grow"><span class="dc-kicker">Current session · ${esc(r.provider)}</span><div class="dc-model">${esc(r.model || 'Model not reported')}</div></div><span class="dc-pill dc-on">${esc(p.name)}</span></div>
      <div class="dc-subtitle dc-path">${esc(r.workspace)}</div>
      <details id="dc-runtime-build"><summary>Dream version · ${esc(r.runtime_identity?.version || 'Not reported')}</summary>
        ${r.runtime_identity ? `<dl class="dc-meta"><dt>Process</dt><dd>${esc(r.runtime_identity.pid)}</dd><dt>Started importing Dream</dt><dd>${esc(r.runtime_identity.imported_at)}</dd><dt>Startup source</dt><dd>${esc(r.runtime_identity.startup_source_id || 'Unavailable')}</dd><dt>Selected source changed</dt><dd>${r.runtime_identity.source_changed === true ? 'Yes' : r.runtime_identity.source_changed === false ? 'No' : 'Unknown'}</dd></dl><p class="dc-help">${esc(r.runtime_identity.restart_guidance)}</p><p class="dc-help">${esc(r.runtime_identity.scope)}</p>` : '<p class="dc-help">This session does not report its running source identity.</p>'}
      </details>
      <dl class="dc-stats"><div><dt>Context input${c ? ' · est.' : ''}</dt><dd>${esc(context)}</dd></div><div><dt>Output reserve</dt><dd>${number(c?.output ?? p.output_tokens)} <small>tokens</small></dd></div><div><dt>Parallel workers</dt><dd>${number(p.max_parallel)}</dd></div></dl>
      ${c && c.window > 0 ? `<div class="dc-budget" aria-hidden="true"><span class="dc-input" style="width:${percent(c.input_tokens)}%"></span><span class="dc-output" style="width:${percent(c.output)}%"></span></div><p class="dc-help">${esc(c.method || 'Estimated context accounting')} · ${number(c.remaining)} tokens remaining after output and margin.</p>` : `<p class="dc-help">Context is owned by ${esc(r.context_owner || 'the provider')}. A live breakdown is not available.</p>`}
      ${run ? `<details><summary>Observed turn usage · ${number(run.tools)} tools · ${number(run.failures)} failures</summary><dl class="dc-meta"><dt>Input tokens</dt><dd>${number(run.prompt_tokens)}</dd><dt>Output tokens</dt><dd>${number(run.output_tokens)}</dd><dt>Cached tokens</dt><dd>${number(run.cached_tokens)}</dd></dl><p class="dc-help">Runtime observations describe execution, not verified task outcomes.</p></details>` : '<p class="dc-help">No turn usage reported yet.</p>'}`;
    if(!profileEdited && !profileSaved) {
      const saved = r.settings;
      byId('dc-profile').value = saved?.profile || p.name;
      for(const key of overrideKeys) byId('dc-' + key).value = saved?.overrides?.[key] ?? '';
      byId('dc-vision').value = typeof saved?.overrides?.vision === 'boolean' ? String(saved.overrides.vision) : 'auto';
      byId('dc-profile-note').textContent = saved ? `Saved preference: ${saved.profile || 'auto'}.` : 'Saved preferences are not reported by this session. Choose a profile to replace them.';
    }
    const scope = r.execution || {};
    scopeDeadline = typeof scope.remaining_seconds === 'number' ? Date.now() + Math.max(0, scope.remaining_seconds) * 1000 : null;
    updateScope(); syncDisabled();
    byId('dc-analysis-note').textContent = `Model analysis is optional. Analyze queues a request with ${r.provider}${r.model ? ' / ' + r.model : ''} to inspect frames and draft instructions. Extracting frames does not request analysis.`;
  }
  function renderRunBudget(run) {
    const panel = byId('dc-run-budget');
    const unlimited = run?.max_active_s === null && run?.remaining_active_s === null;
    if(!run || (!unlimited && (typeof run.remaining_active_s !== 'number' || !Number.isFinite(run.remaining_active_s)))) {
      panel.innerHTML = '<h3>Runtime budget</h3><p class="dc-help">Remaining runtime budget is not reported by this session.</p>';
      return;
    }
    const duration = value => {
      if(typeof value !== 'number' || !Number.isFinite(value) || value < 0) return 'Not reported';
      const seconds = Math.ceil(value), hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
      return (hours ? hours + 'h ' : '') + (hours || minutes ? minutes + 'm ' : '') + seconds % 60 + 's';
    };
    const state = run.finished ? 'Last turn · clock stopped' : run.approval_pending ? 'Waiting for approval · active clock paused' : 'Current turn';
    panel.innerHTML = `<h3>Runtime budget</h3><p class="dc-help">${state}</p>
      <dl class="dc-meta"><dt>Active work remaining</dt><dd>${unlimited ? 'Unlimited' : duration(run.remaining_active_s)}</dd>
      <dt>Active time used / limit</dt><dd>${duration(run.active_elapsed_s)} / ${unlimited ? 'Unlimited' : duration(run.max_active_s)}</dd>
      <dt>Approval waiting</dt><dd>${duration(run.approval_wait_s)}</dd>
      <dt>Total elapsed</dt><dd>${duration(run.elapsed_s)}</dd>
      <dt>Total wall-time limit</dt><dd>${run.max_wall_s == null ? 'Separate limit not configured' : duration(run.max_wall_s)}</dd>
      ${run.max_wall_s == null ? '' : `<dt>Wall time remaining</dt><dd>${duration(run.remaining_wall_s)}</dd>`}</dl>
      <p class="dc-help">Latest server snapshot. Approval waiting is excluded from active time. Token and tool limits are separate. Refreshes while Runtime controls are open.</p>`;
  }
  function renderCapabilities() {
    const caps = runtime?.capabilities || {};
    const labels = {tool_calling:'Tool calling', vision:'Image input', context_tokens:'Context tokens', reasoning_levels:'Reasoning levels', concurrency:'Server concurrency', cancellation:'Server cancellation'};
    byId('dc-capability-facts').innerHTML = Object.entries(labels).map(([key,label]) => {
      const fact = caps[key], known = fact?.known === true;
      const value = !known ? 'Unknown' : typeof fact.value === 'boolean' ? (fact.value ? 'Supported' : 'Unsupported') : Array.isArray(fact.value) ? (fact.value.join(', ') || 'None reported') : String(fact.value);
      return `<dt>${esc(label)}</dt><dd>${esc(value)} <small>${esc(fact?.source || 'unreported')}</small></dd>`;
    }).join('');
    byId('dc-capability-configured').textContent = 'Configured limits: ' + (Object.entries(caps.configured || {}).map(([key,value]) => key.replaceAll('_',' ') + ' ' + value).join(' · ') || 'not reported');
    byId('dc-capability-warnings').textContent = (caps.warnings || []).join(' · ');
    const coordination = runtime?.coordination || {};
    const current = coordination.request_id || '';
    const form = byId('dc-reconcile-form');
    if(form.dataset.requestId !== current || coordination.can_reconcile !== true) byId('dc-reconcile-confirm').checked = false;
    form.dataset.requestId = current;
    form.hidden = coordination.can_reconcile !== true || !current;
    byId('dc-coordination-status').textContent = coordination.enabled === true ?
      `State: ${coordination.state || 'unknown'}${current ? ' · request ' + current : ''}${coordination.waiting ? ' · waiting requests ' + coordination.waiting : ''}. ${coordination.reason || ''}` :
      coordination.reason || 'Local coordination is unavailable for this adapter.';
    syncDisabled();
  }
  function renderActiveSettings() {
    const status = runtime?.generation_settings, box = byId('dc-active-settings');
    if(status?.available !== true) {
      box.innerHTML = '<h3>Active settings</h3><p class="dc-help">' + esc(status?.reason || 'Settings are not reported by this session.') + '</p>';
      return;
    }
    const table = rows => '<dl class="dc-meta">' + (Array.isArray(rows) ? rows.slice(0,32) : []).map(row => {
      const value = row?.known !== true ? 'Unknown' : typeof row.value === 'boolean' ? (row.value ? 'On' : 'Off') :
        typeof row.value === 'number' && Number.isFinite(row.value) ? row.value.toLocaleString(undefined,{maximumFractionDigits:8}) : String(row.value ?? 'Unknown').slice(0,80);
      return `<dt>${esc(String(row.label || '').slice(0,80))}</dt><dd>${esc(value)} <small>${esc(String(row.source || '').slice(0,160))}</small></dd>`;
    }).join('') + '</dl>';
    box.innerHTML = `<h3>Active settings</h3><p class="dc-help">${esc(status.note || 'Read-only request settings. Actual admission may reduce the output allowance.')}</p>
      ${table(status.summary)}<details><summary>Sampling and session launch options</summary>${table(status.sampling)}${table(status.launch)}</details>
      <details><summary>Last prepared turn and admission</summary><h4>Last prepared turn selection</h4>${table(status.last_prepared_turn)}
      <h4>Last admission calculation</h4><p class="dc-help">This calculation may describe a refused request. It is not a live generation snapshot.</p>${table(status.last_admission)}</details>`;
  }
  byId('dc-reconcile-confirm').onchange = syncDisabled;
  byId('dc-reconcile-form').onsubmit = event => {
    event.preventDefault();
    const requestId = byId('dc-reconcile-form').dataset.requestId;
    if(!byId('dc-reconcile-confirm').checked || runtime?.coordination?.can_reconcile !== true || !requestId) return;
    change({action:'reconcile_local_request',request_id:requestId,confirmed_idle:true}, 'Reconciling local request', async () => {
      byId('dc-reconcile-confirm').checked = false;
      await loadRuntime();
      feedback('Reconciliation accepted. Current coordination status is shown above.');
    });
  };
  function performanceImpact(mode) {
    return `${number(mode.output_tokens)} output tokens · reasoning: ${mode.reasoning_effort == null ? 'provider default' : mode.reasoning_effort}`;
  }
  function renderPerformance() {
    const status = runtime?.performance, select = byId('dc-performance-mode');
    const supported = status?.supported === true && Array.isArray(status.modes);
    byId('dc-performance-current').textContent = supported ? (status.modes.find(mode => mode.name === status.current)?.label || 'Custom') : 'Unavailable';
    byId('dc-performance-state').textContent = supported ?
      `Selected for next user turn: ${performanceImpact(status.effective || {})}.${status.reasoning_supported === false ? ' Output-only modes; this model has not reported supported reasoning levels.' : ''}` :
      status?.reason || 'Performance modes are unavailable for this adapter. Generation settings remain with the provider.';
    if(!supported) { byId('dc-performance-preview').textContent = ''; return; }
    const draft = performanceEdited ? select.value : status.current;
    select.innerHTML = status.modes.map(mode => `<option value="${esc(mode.name)}">${esc(mode.label)}${mode.name === 'custom' ? ' · original session settings' : ''}</option>`).join('');
    select.value = status.modes.some(mode => mode.name === draft) ? draft : status.current;
    previewPerformance();
  }
  function previewPerformance() {
    const mode = runtime?.performance?.modes?.find(mode => mode.name === byId('dc-performance-mode').value);
    byId('dc-performance-preview').textContent = mode ? `${mode.description} ${performanceImpact(mode)}.` : '';
  }
  byId('dc-performance-mode').onchange = () => { performanceEdited = true; previewPerformance(); };
  byId('dc-performance-form').onsubmit = event => {
    event.preventDefault();
    if(runtime?.performance?.supported !== true) return;
    const mode = byId('dc-performance-mode').value;
    change({action:'performance',mode}, 'Applying performance mode', async result => {
      if(result?.supported !== true || result.current !== mode || !Array.isArray(result.modes)) throw new Error('Studio did not confirm the selected mode. Refresh to check its state.');
      runtime = {...runtime,performance:result}; performanceEdited = false; renderPerformance();
      feedback(`${result.modes.find(row => row.name === mode)?.label || mode} selected for your next user turn. The current turn keeps its settings.`);
    });
  };
  function updateScope() {
    const raw = runtime?.execution;
    const scope = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
    const check = scope.checked === false ? 'No saved check' :
      (scope.checked === true || !Object.hasOwn(scope, 'checked')) && scope.available === true ? 'Saved check passed' :
      scope.checked === true && scope.available === false ? 'Saved check unavailable' : 'Check status unreported';
    const redTeam = scope.red_team === true;
    byId('dc-scope-badge').textContent = redTeam ? 'Scoped exercise' : check;
    const remaining = scopeDeadline === null ? null : Math.max(0, Math.ceil((scopeDeadline - Date.now()) / 1000));
    const targets = Array.isArray(scope.targets) ? scope.targets.filter(target => typeof target === 'string') : [];
    const exercise = redTeam ?
      `${targets.join(', ') || 'Target not reported'} · ${remaining === null ? 'Expiry is enforced by the engine; countdown unavailable.' : remaining > 0 ? `Expires in ${Math.ceil(remaining / 60)} min.` : 'Scope expired. End the exercise to restore the default scope.'} ` :
      'No red-team exercise is enabled. ';
    const reason = typeof scope.reason === 'string' && scope.reason ? ` ${scope.reason}` : '';
    byId('dc-scope-status').textContent = exercise + `${check}.${reason} ` +
      'Saved shell check from startup, a scope change, or native-shell recovery. Commands recheck when called. ' +
      'JavaScript runtime readiness is not reported here; scripts check it when called.';
  }
  byId('dc-profile-form').addEventListener('input', () => { profileEdited = true; });
  byId('dc-profile-form').onsubmit = event => {
    event.preventDefault();
    const overrides = {...(runtime.settings?.overrides || {})};
    for(const key of overrideKeys) {
      delete overrides[key];
      const value = byId('dc-' + key).value.trim();
      if(value !== '') overrides[key] = Number(value);
    }
    delete overrides.vision;
    if(byId('dc-vision').value !== 'auto') overrides.vision = byId('dc-vision').value === 'true';
    const profile = byId('dc-profile').value;
    change({action:'profile',profile,overrides}, 'Saving profile', async () => {
      profileSaved = true; profileEdited = false;
      byId('dc-profile-note').textContent = `Saved for new sessions: ${profile}.`;
      feedback(`Saved ${profile} for new sessions. This session remains ${runtime.profile.name}.`);
    });
  };
  byId('dc-scope-form').onsubmit = event => {
    event.preventDefault();
    change({action:'red_team',enabled:true,target:byId('dc-target').value.trim(),minutes:Number(byId('dc-minutes').value)}, 'Enabling scoped exercise', async () => { await loadRuntime(); feedback('Scoped exercise enabled. The target and expiry are shown under Execution scope.'); });
  };
  byId('dc-scope-stop').onclick = () => change({action:'red_team',enabled:false}, 'Ending scoped exercise', async () => { await loadRuntime(); feedback('Scoped exercise ended.'); });

  async function loadExtensions() {
    if(loading.extensions) return loading.extensions;
    loading.extensions = (async () => {
      const readRevision = revision;
      try {
        const data = await api('/api/extensions');
        if(readRevision !== revision) return;
        if(!Array.isArray(data.extensions)) throw new Error('Studio returned an unreadable extension catalog.');
        extensions = data; state('dc-ext-state', ''); renderExtensions();
      } catch(error) { if(readRevision === revision) state('dc-ext-state', error.message, loadExtensions); }
      finally { delete loading.extensions; }
    })(); return loading.extensions;
  }
  function renderExtensions() {
    if(!extensions) return;
    const data = extensions, term = byId('dc-ext-search').value.toLowerCase().trim(), kind = byId('dc-ext-kind').value;
    const kinds = {skill:'Skills',plugin:'Plugins',mcp:'MCP',hook:'Hooks',tool:'Tools'};
    byId('dc-ext-counts').innerHTML = Object.entries(data.counts || {}).map(([k, v]) => `<span><b>${number(v.enabled)}/${number(v.total)}</b> ${esc(kinds[k] || k)} on</span>`).join('');
    const warnings = byId('dc-ext-warnings'); warnings.hidden = !data.warnings?.length;
    warnings.innerHTML = data.warnings?.length ? `<details class="dc-notice dc-warning"><summary>${data.warnings.length} catalog warning${data.warnings.length === 1 ? '' : 's'}</summary>${data.warnings.map(w => `<p>${esc(w)}</p>`).join('')}</details>` : '';
    byId('dc-usage-note').textContent = data.usage_note || 'Usage counts cover observed runtime events only. Absent history means not observed.';
    const rows = data.extensions.filter(row => (kind === 'all' || row.kind === kind) && (!term || [row.name,row.id,row.description,...(row.capabilities || [])].join(' ').toLowerCase().includes(term)));
    const list = byId('dc-ext-list');
    list.innerHTML = rows.length ? rows.map((row, index) => {
      const parent = data.extensions.find(item => item.id === row.parent);
      const untrustedModule = row.provenance === 'python-tool-module' && row.trust_state !== 'trusted';
      const blocked = untrustedModule || !!row.blocked_by || (row.kind === 'hook' && !row.enabled) || row.installed === false || (parent && !parent.enabled && !row.enabled);
      const reviewHook = row.kind === 'hook' && !row.enabled && Array.isArray(row.command) && row.command.length > 0 && row.command.every(v => typeof v === 'string') && typeof row.cwd === 'string' && row.cwd.length > 0 && Array.isArray(row.events) && row.events.length > 0 && ['gate','observe'].includes(row.mode) && typeof row.timeout_s === 'number' && Number.isFinite(row.timeout_s) && row.timeout_s > 0;
      const cli = 'dream extensions enable ' + "'" + row.id.replaceAll("'", "'\\''") + "' --trust";
      const count = Object.entries(row.usage?.counts || {}).map(([name, n]) => `${number(n)} ${name.replaceAll('_',' ')}`).join(' · ');
      const observed = row.usage?.state === 'unavailable' ? 'Usage unavailable' : count || 'No observed events';
      const note = untrustedModule ? `Python import blocked: source trust ${row.trust_state || 'unavailable'}. Review the source and approve its exact SHA-256 through the operator controls. Configured ${row.configured_enabled ? 'on' : 'off'}; enabling alone does not trust code.` :
        row.blocked_by ? `Unavailable until module ${row.blocked_by} is enabled and its source is reviewed.` :
        row.kind === 'hook' && !row.enabled ? reviewHook ? 'Review and trust this executable hook below to enable it.' : `Hook metadata is unavailable. Inspect its registration, then use: ${cli}` :
        row.installed === false ? 'Saved override only; this extension is not installed.' : parent && !parent.enabled ? `Parent ${parent.name} is off. Enable it first.` :
        typeof row.override === 'boolean' ? `Saved override: ${row.override ? 'on' : 'off'}.` : `Default: ${row.default_enabled ? 'on' : 'off'}.`;
      return `<article class="dc-extension" data-extension="${esc(row.id)}"><div class="dc-row"><div class="dc-grow"><h4>${esc(row.name)}</h4><span class="dc-subtitle">${esc(kinds[row.kind] || row.kind)}${row.portable ? ' · portable' : ''}${row.version ? ' · ' + esc(row.version) : ''}</span></div><button class="dc-switch" role="switch" aria-checked="${!!row.enabled}" aria-label="${esc(row.name)} enabled" aria-describedby="dc-ext-note-${index}" data-index="${index}" data-mutate data-blocked="${!!blocked}"><i aria-hidden="true"></i><span>${row.enabled ? 'On' : 'Off'}</span></button></div>
        ${row.description ? `<p>${esc(row.description)}</p>` : ''}<span class="dc-help" id="dc-ext-note-${index}">${esc(note)}</span>
        ${row.provenance === 'python-tool-module' ? `<div class="dc-actions"><button data-source-index="${index}" aria-expanded="false" aria-controls="dc-module-${index}">Review Python source</button></div><section id="dc-module-${index}" class="dc-module-review" aria-label="${esc(row.name)} source review" hidden></section>` : ''}
        ${reviewHook ? `<details open class="dc-hook-review"><summary>Review executable hook</summary><dl class="dc-meta"><dt>Argv</dt><dd>${esc(JSON.stringify(row.command))}</dd><dt>Directory</dt><dd>${esc(row.cwd)}</dd><dt>Events</dt><dd>${esc(row.events.join(', '))}</dd><dt>Failure mode</dt><dd>${row.mode === 'gate' ? 'Gate · before-tool failures deny execution' : 'Observe · failures are reported; execution continues'}</dd><dt>Timeout</dt><dd>${esc(row.timeout_s)} seconds</dd></dl><p class="dc-help">Trust is bound to the configured command and script fingerprint. This does not grant tool permissions.</p><label class="dc-trust-label"><input type="checkbox" data-trust-index="${index}">I trust ${esc(row.name)} to run this configured command</label></details>` : ''}
        <details><summary>Source & usage · ${esc(observed)}</summary><dl class="dc-meta"><dt>ID</dt><dd>${esc(row.id)}</dd><dt>Source</dt><dd>${esc(row.path || row.source || 'Not reported')}</dd><dt>Provenance</dt><dd>${esc(row.provenance || 'Not reported')}</dd>${row.sha256 ? `<dt>SHA-256</dt><dd>${esc(row.sha256)}</dd>` : ''}${row.trust_error ? `<dt>Trust error</dt><dd>${esc(row.trust_error)}</dd>` : ''}${row.parent ? `<dt>Parent</dt><dd>${esc(row.parent)}</dd>` : ''}${row.capabilities?.length ? `<dt>Capabilities</dt><dd>${esc(row.capabilities.join(', '))}</dd>` : ''}<dt>Observed</dt><dd>${esc(observed)}</dd>${row.usage?.last_observed_at ? `<dt>Last event</dt><dd>${esc(row.usage.last_observed_at)}</dd>` : ''}</dl></details></article>`;
    }).join('') : `<div class="dc-empty"><h4>${data.extensions.length ? 'No matching capabilities' : 'No extensions reported'}</h4><p>${data.extensions.length ? 'Try a different name or choose All types.' : 'Installed Dream skills, plugins and connected tools will appear here.'}</p></div>`;
    list.querySelectorAll('[data-index]').forEach(button => {
      const row = rows[Number(button.dataset.index)];
      const review = list.querySelector(`[data-trust-index="${button.dataset.index}"]`);
      if(review) review.onchange = () => {
        const parent = data.extensions.find(item => item.id === row.parent);
        button.dataset.blocked = String(!review.checked || row.installed === false || !!(parent && !parent.enabled));
        syncDisabled();
      };
      button.onclick = () => {
        const payload = {action:'extension',id:row.id,enabled:!row.enabled};
        if(row.kind === 'hook' && !row.enabled) {
          if(!review?.checked) return;
          payload.trusted = true;
        }
        change(payload, row.enabled ? 'Disabling extension' : 'Enabling extension', async () => {
          await loadExtensions(); feedback(`${row.name}: ${row.enabled ? 'off' : 'on'} saved.${row.enabled ? '' : ' Start a new session if this capability was not loaded.'}`);
          const replacement = [...list.querySelectorAll('[data-extension]')].find(node => node.dataset.extension === row.id);
          replacement?.querySelector('button')?.focus();
        });
      };
    });
    list.querySelectorAll('[data-source-index]').forEach(button => {
      button.onclick = () => reviewModule(rows[Number(button.dataset.sourceIndex)], byId('dc-module-' + button.dataset.sourceIndex), button);
    });
    syncDisabled();
  }
  byId('dc-ext-search').oninput = renderExtensions;
  byId('dc-ext-kind').onchange = renderExtensions;

  async function reviewModule(row, box, button) {
    if(busy || button.disabled) return;
    button.disabled = true; button.setAttribute('aria-expanded', 'true');
    box.hidden = false; box.textContent = 'Reading configured source…'; box.setAttribute('aria-busy', 'true');
    try {
      const review = await api('/api/extensions/' + encodeURIComponent(row.id) + '/source');
      if(!box.isConnected) return;
      if(review.id !== row.id || typeof review.path !== 'string' || !review.path ||
        typeof review.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(review.sha256) ||
        !Number.isInteger(review.size) || review.size < 1 || review.size > 262144 ||
        typeof review.source !== 'string' || !review.source.trim()) {
        throw new Error('Studio did not return a complete source snapshot. Reload source before approving.');
      }
      box.innerHTML = `<h4>Review before import</h4><dl class="dc-meta"><dt>File</dt><dd>${esc(review.path)}</dd><dt>SHA-256</dt><dd class="dc-source-hash">${esc(review.sha256)}</dd><dt>Size</dt><dd>${number(review.size)} bytes</dd></dl>
        <pre tabindex="0" aria-label="Python module source"></pre>
        <p class="dc-help">Approval covers this entry file. Its Python code and dependencies run as trusted host code, outside shell containment. Catalog provenance does not grant permissions.</p>
        <label class="dc-trust-label"><input type="checkbox" data-source-agree>I reviewed this source and trust the displayed SHA-256 snapshot</label>
        <div class="dc-actions"><button class="dc-primary" data-source-trust data-mutate data-blocked="true" disabled>Trust this SHA-256</button></div>
        <p class="dc-help">The configured on/off setting is preserved. Start a new session to load approved code.</p>`;
      box.querySelector('pre').textContent = review.source;
      const agree = box.querySelector('[data-source-agree]'), trust = box.querySelector('[data-source-trust]');
      agree.onchange = () => { trust.dataset.blocked = String(!agree.checked); syncDisabled(); };
      trust.onclick = () => {
        if(busy || !agree.checked || !box.isConnected) return;
        // Every attempt consumes this review. A stale hash or uncertain response
        // requires fetching source again, never silently approving newer bytes.
        agree.checked = false; agree.disabled = true; trust.dataset.blocked = 'true';
        change({action:'module_trust',id:review.id,sha256:review.sha256}, 'Approving source snapshot', async result => {
          if(result?.trusted !== true || result.id !== review.id || result.sha256 !== review.sha256) {
            throw new Error('Studio did not confirm this source hash. Refresh the catalog and review source again.');
          }
          await loadExtensions();
          feedback(`${row.name}: source snapshot trusted. The configured on/off setting was preserved. Start a new session to load approved code.`);
          const replacement = [...byId('dc-ext-list').querySelectorAll('[data-extension]')].find(node => node.dataset.extension === row.id);
          replacement?.querySelector('[data-source-index]')?.focus();
        });
      };
      syncDisabled();
      box.querySelector('pre').focus({preventScroll:true});
    } catch(error) {
      if(box.isConnected) {
        box.replaceChildren();
        const message = document.createElement('p'); message.className = 'dc-notice dc-error';
        message.setAttribute('role', 'alert'); message.textContent = error.message;
        box.appendChild(message);
      }
    } finally {
      button.disabled = false; button.textContent = 'Reload source'; box.removeAttribute('aria-busy');
    }
  }

  async function loadLearning() {
    if(loading.learning) return loading.learning;
    loading.learning = (async () => {
      const readRevision = revision;
      try {
        const data = await api('/api/learning');
        if(readRevision !== revision) return;
        if(!Array.isArray(data.demonstrations)) throw new Error('Studio returned unreadable learning status.');
        learning = data; learningKnown = true; state('dc-learn-state', ''); renderLearning();
      } catch(error) {
        if(readRevision !== revision) return;
        learningKnown = false;
        state('dc-learn-state', error.message, loadLearning);
        if(wasRecording) {
          recording.hidden = false; recording.textContent = 'Check recording'; recording.classList.add('dc-unknown'); recording.title = 'Recording status unknown. Open Learn to retry or stop.';
          byId('dc-active-name').textContent = 'Recording status unknown';
          byId('dc-active-detail').textContent = error.message + ' You can still request Stop.';
        }
        syncDisabled();
      } finally { delete loading.learning; schedulePoll(); }
    })(); return loading.learning;
  }
  function renderLearning() {
    const active = isRecording(), info = learning.active;
    wasRecording = active; recording.hidden = !active; recording.classList.remove('dc-unknown');
    recording.textContent = 'Recording'; recording.title = active ? `Recording ${info.name}. Open Learn to stop.` : '';
    byId('dc-active').hidden = !active;
    if(active) {
      byId('dc-active-name').textContent = info.name || 'Recording demonstration';
      byId('dc-active-detail').textContent = `${Array.isArray(info.region) ? info.region.join(', ') + ' px · ' : ''}Stops after ${info.maximum_seconds ?? 'the requested number of'} seconds.`;
    }
    byId('dc-demo-count').hidden = false; byId('dc-demo-count').textContent = String(learning.demonstrations.length);
    const signature = JSON.stringify([learning.demonstrations,[...queued],[...installed]]);
    if(signature !== demoSignature) { demoSignature = signature; renderDemos(); }
    syncDisabled();
  }
  function renderDemos() {
    const list = byId('dc-demos'), rows = learning.demonstrations;
    list.innerHTML = rows.length ? rows.map((row, index) => {
      const frames = Array.isArray(row.frames) ? row.frames.length : typeof row.frames === 'number' ? row.frames : null;
      const canExtract = ['recorded','ready','draft'].includes(row.status), canAnalyze = ['ready','draft'].includes(row.status), hasDraft = row.status === 'draft';
      if(hasDraft) queued.delete(row.id);
      const status = installed.has(row.id) ? 'Installed · disabled' : queued.has(row.id) ? 'Analysis queued' : row.status || 'Status unavailable';
      return `<article class="dc-demo" data-demo="${esc(row.id)}"><div class="dc-row"><h4 class="dc-grow">${esc(row.name || row.id)}</h4><span class="dc-pill">${esc(status)}</span></div><span class="dc-help">${frames === null ? 'Frame count unavailable' : `${frames} sampled frames`}${row.duration_s ? ` · ${number(row.duration_s)}s` : ''}</span>
        ${row.error ? `<p class="dc-notice dc-error">${esc(row.error)}</p>` : ''}
        <div class="dc-actions"><button data-demo-action="extract" data-index="${index}" data-mutate data-blocked="${!canExtract}">Extract frames</button><button data-demo-action="analyze" data-index="${index}" data-mutate data-blocked="${!canAnalyze || queued.has(row.id)}">Analyze with model</button><button data-demo-action="evidence" data-index="${index}" ${!canAnalyze ? 'disabled' : ''}>Annotate evidence</button><button data-demo-action="review" data-index="${index}" ${!hasDraft ? 'disabled' : ''}>Review skill draft</button></div>
        ${queued.has(row.id) ? '<p class="dc-help">Queued in the terminal session. Refresh for a draft; model analysis is not a verification result.</p>' : ''}
        ${installed.has(row.id) ? `<p class="dc-help">Installed disabled. Enable deliberately in Extensions after review.</p><div class="dc-path">${esc(installed.get(row.id))}</div>` : ''}</article>`;
    }).join('') : '<div class="dc-empty"><h4>No demonstrations yet</h4><p>Record a specific workflow or import a video. Nothing is captured or analyzed automatically.</p></div>';
    list.querySelectorAll('[data-demo-action]').forEach(button => {
      const row = rows[Number(button.dataset.index)], action = button.dataset.demoAction;
      button.onclick = () => {
        if(action === 'evidence') { editEvidence(row); return; }
        if(action === 'review') { reviewDraft(row); return; }
        change({action:'learn_' + action,id:row.id}, action === 'extract' ? 'Extracting frames' : 'Queuing model analysis', async result => {
          if(action === 'analyze') { queued.add(row.id); byId('dc-draft').hidden = true; draftId = null; feedback(`Analysis queued${typeof result?.provider === 'string' ? ' with ' + result.provider : ''}. Review the resulting draft before installing.`); }
          else feedback('Frames extracted. Analysis is optional; request it when you are ready.');
          await loadLearning();
        });
      };
    });
    syncDisabled();
  }
  let evidenceRequest = 0;
  async function editEvidence(row) {
    const request = ++evidenceRequest;
    const box = byId('dc-draft'); draftId = row.id; box.hidden = false;
    box.innerHTML = '<p role="status">Loading recording evidence…</p>';
    box.scrollIntoView({block:'nearest'});
    try {
      const path = '/api/learning/' + encodeURIComponent(row.id);
      const data = await api(path + '/evidence');
      if(draftId !== row.id || request !== evidenceRequest) return;
      let selected = null, previewURL = null, previewRequest = 0;
      box.innerHTML = `<h4>Evidence · ${esc(row.name || row.id)}</h4><p class="dc-help">Manual annotations describe this recording. Observed means you saw it; inferred means uncertain. Only check confirmation after verifying the outcome. No keyboard or mouse event log is captured.</p>
        <form id="dc-evidence-form"><fieldset id="dc-evidence-fields"><label>Application<input id="dc-evidence-app" maxlength="200"></label><label>App version<input id="dc-evidence-version" maxlength="100"></label>
        <label>Saved event<select id="dc-evidence-event"><option value="">New event</option></select></label>
        <label>Frame<select id="dc-evidence-frame" required></select></label><img id="dc-evidence-image" alt="Selected recording frame" style="max-width:100%;max-height:260px" hidden>
        <label>Time (seconds)<input id="dc-evidence-time" type="number" min="0" step="0.1" required></label>
        ${['action','target','before','after','outcome'].map(key => `<label>${key[0].toUpperCase()+key.slice(1)}<textarea id="dc-evidence-${key}" maxlength="2000" ${key === 'action' ? 'required' : ''}></textarea></label>`).join('')}
        <label>Evidence basis<select id="dc-evidence-basis"><option value="inferred">Inferred</option><option value="observed">Observed by me</option><option value="user-confirmed">User-confirmed</option></select></label>
        <label><input id="dc-evidence-confirmed" type="checkbox">I verified this outcome</label>
        <div class="dc-actions"><button type="submit">Save event</button><button type="button" id="dc-evidence-new">New event</button></div></fieldset><p id="dc-evidence-status" role="status"></p></form>`;
      const field = key => byId('dc-evidence-' + key);
      field('app').value = data.application; field('version').value = data.app_version;
      field('frame').innerHTML = data.frames.map(f => `<option value="${esc(f.file)}">${esc(f.file)} · ${f.seconds}s</option>`).join('');
      const status = message => { if(draftId === row.id && request === evidenceRequest && field('status')) field('status').textContent = message; };
      function events() {
        field('event').innerHTML = '<option value="">New event</option>' + data.events.map(e => `<option value="${esc(e.id)}">${esc(e.action)} · ${e.seconds}s${e.confirmed ? ' · confirmed' : ''}</option>`).join('');
        field('event').value = selected || '';
      }
      async function preview() {
        const serial = ++previewRequest;
        const frame = data.frames.find(f => f.file === field('frame').value);
        if(!frame) return;
        field('time').value = frame.seconds;
        field('image').hidden = true;
        try {
          const response = await fetch(path + '/' + frame.file, {headers:{'X-Dream-Token':TOKEN}, cache:'no-store'});
          if(!response.ok) throw new Error('Unable to load selected frame');
          const blob = await response.blob();
          if(draftId !== row.id || request !== evidenceRequest || serial !== previewRequest || !field('image')) return;
          if(previewURL) URL.revokeObjectURL(previewURL);
          previewURL = URL.createObjectURL(blob); field('image').src = previewURL; field('image').hidden = false;
          field('image').onload = () => { URL.revokeObjectURL(previewURL); };
        } catch(error) { status(error.message); }
      }
      function selectEvent() {
        selected = field('event').value || null;
        const event = data.events.find(e => e.id === selected);
        for(const key of ['action','target','before','after','outcome']) field(key).value = event?.[key] || '';
        field('basis').value = event?.basis || 'inferred'; field('confirmed').checked = event?.confirmed || false;
        if(event) field('frame').value = event.frame;
        preview();
        if(event) field('time').value = event.seconds;
      }
      events(); preview();
      field('frame').onchange = preview; field('event').onchange = selectEvent;
      field('new').onclick = () => { field('event').value = ''; selectEvent(); };
      field('form').onsubmit = async event => {
        event.preventDefault();
        const fields = field('fields');
        if(fields.disabled) return;
        fields.disabled = true;
        const item = {id:selected || 'event-' + crypto.randomUUID(), frame:field('frame').value,
          seconds:Number(field('time').value), basis:field('basis').value, confirmed:field('confirmed').checked};
        for(const key of ['action','target','before','after','outcome']) item[key] = field(key).value;
        const updated = data.events.filter(e => e.id !== item.id); updated.push(item);
        try {
          const saved = await api(path + '/evidence', {revision:data.revision, application:field('app').value,
            app_version:field('version').value, events:updated});
          if(draftId !== row.id || request !== evidenceRequest || !field('form')) return;
          data.revision = saved.revision; data.events = saved.events; selected = item.id; events();
          status('Saved locally. Model analysis can cite these event IDs. Existing skill drafts keep their earlier evidence until recreated.');
        } catch(error) { status(error.message); }
        finally { fields.disabled = false; }
      };
    } catch(error) { if(draftId === row.id && request === evidenceRequest) box.innerHTML = `<p role="alert">${esc(error.message)}</p>`; }
  }
  async function reviewDraft(row) {
    const request = ++evidenceRequest;
    const box = byId('dc-draft'); draftId = row.id; box.hidden = false;
    box.innerHTML = '<p role="status">Loading skill draft…</p>'; box.scrollIntoView({block:'nearest'});
    try {
      const data = await api('/api/learning/' + encodeURIComponent(row.id) + '/draft');
      if(draftId !== row.id || request !== evidenceRequest) return;
      if(typeof data.body !== 'string' || !data.body.trim()) throw new Error('Studio did not return a reviewable skill draft.');
      box.innerHTML = `<div class="dc-row"><h4 class="dc-grow">Review · ${esc(row.name || row.id)}</h4><button id="dc-draft-close" class="dc-quiet" aria-label="Close skill draft">×</button></div><p class="dc-help">These instructions are a draft. Check the evidence and inferred actions. Installation keeps the skill disabled.</p><pre tabindex="0" aria-label="Skill draft text"></pre>${data.uncertainty ? `<p class="dc-help">Uncertainty: ${esc(data.uncertainty)}</p>` : ''}<label><input id="dc-draft-agree" type="checkbox">I reviewed these instructions</label><div class="dc-actions"><button id="dc-draft-install" class="dc-primary" disabled>Install disabled skill</button></div>`;
      box.querySelector('pre').textContent = data.body;
      byId('dc-draft-agree').onchange = syncDisabled;
      byId('dc-draft-close').onclick = () => { box.hidden = true; draftId = null; byId('dc-tab-learn').focus(); };
      byId('dc-draft-install').onclick = () => change({action:'learn_install',id:row.id}, 'Installing reviewed skill', async result => {
        if(result?.enabled !== false || !result?.path) throw new Error('Studio did not confirm a disabled installation. Refresh Extensions to check its state.');
        installed.set(row.id, result.path); feedback('Skill installed disabled. Enable it explicitly from Extensions when ready.');
        await loadLearning();
      });
      box.querySelector('pre').focus(); syncDisabled();
    } catch(error) {
      if(draftId !== row.id || request !== evidenceRequest) return;
      box.innerHTML = `<p class="dc-notice dc-error" role="alert">${esc(error.message)} Installation requires a visible draft.</p><button id="dc-draft-retry">Retry draft</button>`;
      byId('dc-draft-retry').onclick = () => reviewDraft(row);
    }
  }
  byId('dc-record-form').onsubmit = event => {
    event.preventDefault();
    change({action:'learn_start',name:byId('dc-record-name').value.trim(),region:['x','y','width','height'].map(key => Number(byId('dc-' + key).value)),seconds:Number(byId('dc-seconds').value)}, 'Starting recording', async () => {
      await loadLearning(); feedback('Recording started. Use Stop recording to finish early.');
    });
  };
  byId('dc-record-stop').onclick = async () => {
    // Stop remains available during a slow import/extraction or another setting
    // request; those actions must not hold the recording controls hostage.
    if(stopping) return;
    stopping = true; revision++; syncDisabled();
    byId('dc-record-stop').textContent = 'Stopping…';
    try {
      const result = await api('/api/control', {action:'learn_stop'});
      revision++;
      if(loading.learning) await loading.learning;
      if(result?.status && result.status !== 'recording') {
        learning = {...learning,active:null}; learningKnown = true; renderLearning();
      }
      await loadLearning(); feedback('Recording stopped. Extract frames to prepare it for review.');
    } catch(error) { feedback(error.message, true); }
    finally { stopping = false; byId('dc-record-stop').textContent = 'Stop recording'; syncDisabled(); schedulePoll(); }
  };
  byId('dc-import-form').onsubmit = event => {
    event.preventDefault();
    change({action:'learn_import',path:byId('dc-import-path').value.trim(),name:byId('dc-import-name').value.trim()}, 'Importing video', async () => { await loadLearning(); feedback('Video imported. Extract frames when ready.'); });
  };
  function schedulePoll() {
    clearTimeout(pollTimer);
    // Keep a visible recording indicator even while controls are closed. These
    // are metadata reads only; never start capture or model analysis on a timer.
    pollTimer = setTimeout(async () => {
      if(!busy && (wasRecording || !document.hidden)) {
        if(dialog.open && tab === 'runtime') { updateScope(); await loadRuntime(); }
        await loadLearning();
      }
      schedulePoll();
    }, wasRecording || (dialog.open && tab === 'learn') ? 2500 : 12000);
  }
  document.addEventListener('visibilitychange', () => { if(!document.hidden) loadLearning(); });
  loadLearning();
})();
