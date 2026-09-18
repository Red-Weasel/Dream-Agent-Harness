/* Per-turn client measurements and provider-reported evidence, never raw content. */
(() => {
  'use strict';
  const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const count = value => number(value) && Number.isInteger(value);
  const seconds = value => number(value) ? `${value.toFixed(1)} s` : 'not observed';
  const label = value => String(value ?? '').slice(0, 120);
  const element = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const phases = {
    preparation: 'Preparation', approval: 'Approval wait', approval_wait: 'Approval wait',
    tools: 'Tool execution', tool: 'Tool execution', tool_execution: 'Tool execution',
    backend: 'Provider requests', inference: 'Provider requests',
    post_processing: 'Post-processing', postprocessing: 'Post-processing',
    verification: 'Verification', response: 'Response handling',
    recovery: 'Recovery', filing: 'Memory filing', background_handoff: 'Waiting for filing to yield'
  };
  const outcomes = {completed: 'Completed', success: 'Completed', interrupted: 'Interrupted',
    cancelled: 'Cancelled', error: 'Failed', failed: 'Failed', incomplete: 'Incomplete', length: 'Output limit', tool_budget: 'Tool limit'};
  const failureHints = {
    arguments: ['Tool arguments', 'Check the available tool schema and correct the named argument before retrying.'],
    sandbox: ['Permission or sandbox boundary', 'Inspect the selected workspace and permission status. Do not bypass containment to silence an error.'],
    dependency: ['Tool or library dependency', 'Check the required tool or library in the same execution environment that failed.'],
    workspace: ['File or workspace lookup', 'Compare the selected project path with the filesystem visible to the failing tool.'],
    preview: ['Preview or media playback', 'Inspect the saved asset, local references and browser playback support. A queued preview is not verified playback.'],
    provider_rate_limit: ['Provider rate limit', 'Check the provider retry guidance and account availability before explicitly continuing.'],
    provider_connection: ['Provider connection', 'Inspect partial results and provider status before continuing; delivery of the previous request may be uncertain.'],
    interrupted: ['Interrupted request', 'Inspect saved effects, then explicitly continue. Do not replay uncertain actions.'],
    unknown: ['Unclassified failure', 'Inspect the original error locally and identify the failing operation before retrying.']
  };
  function row(list, title, value) {
    list.append(element('dt', title), element('dd', value));
  }
  function render(data) {
    data = data && typeof data === 'object' ? data : {};
    const card = element('details', undefined, 'turn-timing');
    const outcome = outcomes[data.outcome] || label(data.outcome) || 'Outcome not reported';
    const summary = element('summary', undefined);
    summary.append(element('span', `Turn timing · ${outcome}`),
      element('span', `${seconds(data.elapsed_s)} · First answer ${seconds(data.first_text_s)}`, 'tt-summary-time'));
    card.append(summary);
    const body = element('div', undefined, 'tt-body');
    const overview = element('dl', undefined, 'tt-values');
    row(overview, 'Elapsed', seconds(data.elapsed_s));
    row(overview, 'First activity', seconds(data.first_activity_s));
    row(overview, 'First answer', seconds(data.first_text_s));
    body.append(overview);
    body.append(element('p', 'First activity includes thinking or a tool call. First answer is the first answer text.', 'tt-note'));
    const phaseRows = element('dl', undefined, 'tt-values');
    for (const [name, info] of Object.entries(data.phases || {}).slice(0, 32)) {
      if (!info || !number(info.seconds)) continue;
      row(phaseRows, phases[name] || label(name).replaceAll('_', ' '),
        `${seconds(info.seconds)}${count(info.count) ? ` · ${info.count} call${info.count === 1 ? '' : 's'}` : ''}`);
    }
    if (phaseRows.children.length) {
      body.append(element('h4', 'Measured phases'), phaseRows,
        element('p', 'Phase intervals may overlap; their sum is not elapsed time.', 'tt-note'));
    }
    const toolEvidence = data.tool_outcomes;
    if (toolEvidence && toolEvidence.scope === 'observed_tool_events') {
      const totals = element('dl', undefined, 'tt-values');
      const plural = (n, word) => count(n) ? `${n} ${word}${n === 1 ? '' : 's'}` : 'not reported';
      row(totals, 'Tool results observed', count(toolEvidence.result_events) ? String(toolEvidence.result_events) : 'not reported');
      row(totals, 'Reported tool errors', plural(toolEvidence.reported_errors, 'reported error'));
      row(totals, 'Reported tool successes', plural(toolEvidence.reported_successes, 'reported success'));
      row(totals, 'Unreported tool outcomes', plural(toolEvidence.unreported_outcomes, 'unknown outcome'));
      body.append(element('h4', 'Tool outcome evidence'), totals,
        element('p', 'Counts cover observed events; missing error flags remain unknown. This evidence does not establish task success.', 'tt-note'));
    }
    const reviews = {pass: 'Passed', needs_attention: 'Needs attention', unverified: 'Unverified', not_requested: 'Not requested', unreported: 'Not reported'};
    const diagnostic = data.failure_diagnostics;
    if (diagnostic?.schema === 1 && diagnostic.scope === 'observed_failure_signals') {
      const counts = {};
      const hints = element('dl', undefined, 'tt-values');
      for (const [code, [title, advice]] of Object.entries(failureHints)) {
        const n = diagnostic.counts?.[code];
        if (!Number.isSafeInteger(n) || n <= 0) continue;
        counts[code] = n;
        row(hints, `${title} · ${n}`, advice);
      }
      if (hints.children.length) {
        body.append(element('h4', 'Failure recovery'), hints,
          element('p', 'These are message signals, not a confirmed root cause. Counts are observed events; one incident may produce several events.', 'tt-note'));
        const download = element('button', 'Download failure diagnostics');
        download.type = 'button';
        download.addEventListener('click', () => {
          // Reconstruct an allowlisted report; never serialize the event payload.
          const report = {schema: 1, scope: 'observed_failure_signals', counts,
            root_cause_verified: false, raw_content_recorded: false};
          const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}));
          const link = document.createElement('a');
          link.href = url;
          link.download = 'dream-failure-diagnostics.json';
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        });
        body.append(download);
      }
    }
    if (data.delivery_review) {
      body.append(element('p', `Delivery review: ${reviews[data.delivery_review.status] || 'Not reported'}.`, 'tt-note'));
    }
    const cache = data.cache || {};
    const reported = count(cache.reported_requests) ? cache.reported_requests : 0;
    body.append(element('p', reported > 0 && count(cache.cached_tokens)
      ? `Cached tokens reported: ${cache.cached_tokens} across ${reported} request${reported === 1 ? '' : 's'}.`
      : 'Cached tokens: unknown. No cache usage was reported.', 'tt-cache'));
    if (count(cache.unreported_requests) && cache.unreported_requests > 0) {
      const n = cache.unreported_requests;
      body.append(element('p', `${n} request${n === 1 ? '' : 's'} did not report cache usage.`, 'tt-note'));
    }
    const requests = Array.isArray(data.requests) ? data.requests.slice(0, 64).filter(r => r && typeof r === 'object') : [];
    const fingerprints = new Map();
    for (const request of requests) {
      if (typeof request.schema_fingerprint === 'string' && /^[0-9a-f]{8,128}$/i.test(request.schema_fingerprint)) {
        fingerprints.set(request.schema_fingerprint, (fingerprints.get(request.schema_fingerprint) || 0) + 1);
      }
    }
    const same = Math.max(0, ...fingerprints.values());
    body.append(element('p', (same > 1
      ? `Tool schema reuse: the same schema was reported in ${same} retained requests. `
      : 'Tool schema reuse: no repeated schema observed in retained requests. ')
      + 'Schema reuse does not prove a prompt-cache hit.', 'tt-note'));
    if (count(data.requests_dropped) && data.requests_dropped > 0) {
      body.append(element('p', `${data.requests_dropped} earlier request details were omitted by the timing limit.`, 'tt-note'));
    }
    if (!requests.some(r => Object.keys(r.server_timings || {}).length)) {
      body.append(element('p', 'Server timings: not reported. Client latency is not server prompt-processing time.', 'tt-note'));
    }
    for (const [i, request] of requests.entries()) {
      const details = element('details', undefined, 'tt-request');
      details.append(element('summary', `Request ${count(request.index) ? request.index : i + 1} · ${seconds(request.client_elapsed_s)}`));
      const values = element('dl', undefined, 'tt-values');
      row(values, 'Client request elapsed', seconds(request.client_elapsed_s));
      row(values, 'Client first activity', seconds(request.first_activity_s));
      row(values, 'Client first answer', seconds(request.first_text_s));
      const server = request.server_timings || {};
      for (const [key, title, unit] of [
        ['prompt_ms', 'Server-reported prompt processing', 'ms'],
        ['predicted_ms', 'Server-reported generation', 'ms'],
        ['prompt_per_second', 'Server-reported prompt rate', 'tokens/s'],
        ['predicted_per_second', 'Server-reported generation rate', 'tokens/s'],
        ['prompt_n', 'Server-reported prompt tokens', 'tokens'],
        ['predicted_n', 'Server-reported generated tokens', 'tokens'],
        ['cache_n', 'Server-reported cache count', 'tokens']
      ]) if (number(server[key])) row(values, title, `${server[key]} ${unit}`);
      row(values, 'Request cached tokens', count(request.usage?.cached_tokens) ? String(request.usage.cached_tokens) : 'unknown');
      details.append(values);
      body.append(details);
    }
    card.append(body);
    return card;
  }
  function background(data) {
    if (!data || !['queued', 'running', 'completed', 'interrupted', 'dropped', 'failed'].includes(data.kind)) return null;
    const pieces = [`Optional filing ${data.kind}`];
    if (data.label) pieces.push(label(data.label));
    if (count(data.queued)) pieces.push(`${data.queued} queued`);
    if (data.reason) pieces.push(label(data.reason));
    if (data.error_type) pieces.push(label(data.error_type));
    return pieces.join(' · ');
  }
  window.DreamTurnTiming = {render, background};
})();
