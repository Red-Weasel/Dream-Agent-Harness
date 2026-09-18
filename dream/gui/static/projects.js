/* Workspace references remain text, never executable HTML. */
(() => {
  'use strict';
  window.ProjectPanel = {
    mount(container, options = {}) {
      const token = options.token || new URLSearchParams(location.search).get('token') || '';
      const root = document.createElement('section'); root.className = 'project-panel';
      root.innerHTML = `<div class="project-top"><div><h2>Project</h2><p data-workspace></p></div><button data-refresh>Refresh</button></div>
        <p role="status" data-status></p>
        <div class="project-columns"><section><h3>Search workspace</h3><form data-search><label>Keywords<input name="query" required maxlength="256" placeholder="Function, phrase or document topic"></label><button>Search</button></form><p data-coverage></p><div data-results></div>
        <details data-source-box><summary>Source excerpt</summary><pre data-source></pre></details></section>
        <section><h3>Pins</h3><p>Only explicit pins enter project context. Constraints receive space first.</p>
        <form data-pin><label>Kind<select name="kind"><option value="constraint">Constraint</option><option value="fact">Fact</option><option value="decision">Decision</option><option value="task">Task note</option><option value="file">File reference</option></select></label><label>Label<input name="label" maxlength="120" placeholder="Optional short label"></label><label>Note or workspace-relative file path<textarea name="value" required maxlength="1000" rows="3"></textarea></label><button>Pin to project</button></form><div data-pins></div></section></div>
        <section><h3>Context preview</h3><p>Rebuilds project pins and task notes. It does not restore a model's conversation state or replay tools.</p><button data-preview>Refresh context preview</button><button data-handoff>Use handoff in chat</button><p data-loaded></p><pre data-context></pre><ul data-warnings></ul></section>
        <section><h3>Run recovery</h3><p data-recovery-message></p><div data-runs></div></section>`;
      container.append(root);
      const $ = sel => root.querySelector(sel);
      let manifest = null, context = null;
      const status = text => $('[data-status]').textContent = text;
      const safe = fn => async event => {
        event?.preventDefault(); const b = event?.currentTarget;
        if (b?.dataset.busy) return;
        if (b) b.dataset.busy = 'true';
        try { await fn(event); } catch (error) { status(error.message); }
        finally { if (b) delete b.dataset.busy; }
      };
      async function api(action, payload) {
        const response = await fetch('/api/project/' + action, {method: payload === undefined ? 'GET' : 'POST',
          headers: {'x-dream-token': token, ...(payload === undefined ? {} : {'content-type': 'application/json'})},
          ...(payload === undefined ? {} : {body: JSON.stringify(payload)})});
        if (!response.ok) { const result = await response.json(); throw Error(result.error || 'Project request failed.'); }
        return action === 'download' ? response.blob() : response.json();
      }
      function node(tag, text) { const n = document.createElement(tag); n.textContent = text; return n; }
      function button(label, fn) { const b = node('button', label); b.type = 'button'; b.onclick = safe(fn); return b; }
      async function preview() {
        context = await api('context', {});
        $('[data-context]').textContent = context.text || 'No project pins loaded.';
        $('[data-loaded]').textContent = 'Loaded: ' + (context.names.join(', ') || 'none') + ' · ' + context.text.length + '/' + context.max_chars + ' characters';
        $('[data-warnings]').replaceChildren(...context.warnings.map(w => node('li', w)));
        $('[data-handoff]').disabled = !context.text || !options.onHandoff;
      }
      function drawPins() {
        $('[data-workspace]').textContent = manifest.workspace + ' · revision ' + manifest.revision;
        $('[data-pins]').replaceChildren(...manifest.pins.map(pin => {
          const row = node('article', ''); row.className = 'project-card';
          row.append(node('strong', pin.kind + ' · ' + pin.label), node('p', pin.path || pin.text));
          if (pin.kind === 'file') row.append(button('Review source', () => openSource({path: pin.path})),
            button('Revalidate current file', async () => { manifest = await api('revalidate', {id: pin.id, expected_revision: manifest.revision}); drawPins(); await preview(); status('Pin now references the current file.'); }));
          row.append(button('Remove pin', async () => { manifest = await api('unpin', {id: pin.id, expected_revision: manifest.revision}); drawPins(); await preview(); }));
          return row;
        }));
      }
      async function openSource(hit) {
        const source = await api('source', {path: hit.path, sha256: hit.sha256, offset: hit.offset || 0});
        $('[data-source]').textContent = source.path + ' · extracted characters ' + source.offset + '\n' + source.text + (source.partial ? '\n[Partial extraction]' : '');
        $('[data-source-box]').open = true;
      }
      async function refresh() {
        manifest = await api('manifest'); drawPins(); await preview();
        const recovery = await api('recovery');
        $('[data-recovery-message]').textContent = recovery.message;
        $('[data-runs]').replaceChildren(...recovery.runs.map(run => {
          const row = node('article', ''); row.className = 'project-card';
          row.append(node('strong', run.status + ' · ' + run.goal), node('p', run.summary || ''), node('code', run.run_id),
            node('p', run.requires_reconciliation ? 'Review uncertain actions before resuming.' : 'Check the saved contract and remaining budget before resuming.'));
          if (options.onResumePreview) row.append(button('Prepare resume', () => options.onResumePreview(run)));
          return row;
        }));
        if (recovery.available && !recovery.runs.length) $('[data-runs]').append(node('p', 'No matching durable runs found.'));
        status('Project refreshed.');
      }
      $('[data-search]').onsubmit = safe(async event => {
        status('Searching visible workspace files…');
        const result = await api('search', {query: new FormData(event.currentTarget).get('query')});
        $('[data-coverage]').textContent = (result.partial ? 'Partial search. ' : '') + result.files_checked + ' files checked. ' + result.coverage;
        $('[data-results]').replaceChildren(...result.hits.map(hit => {
          const row = node('article', ''); row.className = 'project-card';
          row.append(button(hit.path + ' · ' + hit.provenance + ' ' + hit.offset + '–' + hit.end, () => openSource(hit)), node('pre', hit.snippet),
            button('Pin file', async () => { manifest = await api('pin', {kind: 'file', path: hit.path, expected_revision: manifest.revision}); drawPins(); await preview(); }),
            button('Download source', async () => {
              const blob = await api('download', {path: hit.path, sha256: hit.sha256});
              const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = hit.path.split('/').pop(); link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
            })); return row;
        }));
        status(result.hits.length + ' matches.' + (result.warnings.length ? ' ' + result.warnings.join(' ') : ''));
      });
      $('[data-pin]').onsubmit = safe(async event => {
        const element = event.currentTarget, form = new FormData(element), kind = form.get('kind');
        manifest = await api('pin', {kind, label: form.get('label') || null, [kind === 'file' ? 'path' : 'text']: form.get('value'), expected_revision: manifest.revision});
        element.reset(); drawPins(); await preview(); status('Project pin saved.');
      });
      $('[data-refresh]').onclick = safe(refresh);
      $('[data-preview]').onclick = safe(preview);
      $('[data-handoff]').onclick = safe(async () => { await preview(); if (context.text && options.onHandoff) options.onHandoff(context.text, context); });
      safe(refresh)();
      return {refresh, root};
    }
  };
})();
