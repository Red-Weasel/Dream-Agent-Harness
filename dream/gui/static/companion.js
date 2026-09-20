/* The desktop owns the conversation. Studio owns the work on the canvas.
   Loaded synchronously before connect(): even the first retained show uses
   the same initialized layout and frame bridge as every subsequent show. */
if(COMPANION){
  companion = (() => {
    const body = document.body;
    let composing = false, connected = false, frameTimer, opener, questionSeq = 0;
    const icon = `<svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><path d="M8 7h7a9 9 0 0 1 0 18H8V7Z" stroke="currentColor" stroke-width="2"/><path d="M13 12h3a4 4 0 0 1 0 8h-3" stroke="currentColor" stroke-width="2"/></svg>`;
    document.querySelector('.wordmark').innerHTML = icon + '<span>Dream</span>';
    $('meta').setAttribute('aria-label', 'Session');
    document.querySelector('.status').setAttribute('role', 'status');
    $('railtoggle').textContent = 'Metrics';
    $('railtoggle').title = 'Session metrics';
    document.querySelector('header').insertAdjacentHTML('afterend', `
      <nav class="studio-nav" aria-label="Studio workspace">
        <button id="studio-interactions" class="selected" aria-controls="main" aria-expanded="true">Chat <span id="studio-count" hidden>0</span></button>
        <button id="studio-preview" aria-pressed="false" title="Studio: previews, code, design and creation">Studio</button>
        <button id="studio-skills">Skills</button>
        <button id="studio-new">New chat</button>
        <span class="nav-space"></span>
        <button id="studio-design" aria-controls="rail" aria-expanded="false">Design</button>
        <button id="studio-checkpoints" aria-controls="rail" aria-expanded="false">Checkpoints</button>
      </nav>
      <div id="studio-connection" class="studio-notice" role="status" hidden>
        <div><strong id="studio-connection-title">Connecting to Dream</strong><span id="studio-connection-detail">Keep the terminal session open. Studio will connect automatically.</span></div>
        <button id="studio-retry">Retry now</button>
      </div>
      <div id="studio-error" class="studio-notice error-notice" role="alert" hidden>
        <div><strong>Studio needs attention</strong><span id="studio-error-text"></span></div>
        <button id="studio-dismiss" aria-label="Dismiss error">×</button>
      </div>`);
    panel.insertAdjacentHTML('afterbegin', `
      <div id="studio-empty" class="studio-empty">
        <svg class="empty-canvas" viewBox="0 0 192 132" fill="none" aria-hidden="true">
          <rect x="21" y="13" width="150" height="106" rx="10"/>
          <path d="M21 36h150M37 25h3m7 0h3m7 0h3"/>
          <rect class="canvas-detail" x="37" y="52" width="54" height="51" rx="4"/>
          <path class="canvas-detail" d="M105 56h49m-49 12h35m-35 12h42"/>
          <path class="canvas-accent" d="m52 85 12-17 12 17H52Z"/>
          <path class="canvas-cursor" d="m131 88 5 23 5-8 9-3-19-12Z"/>
        </svg>
        <h1>Your Studio</h1>
        <p>Create, preview, and refine your work here. Ask Dream for a page, document, diagram, or animation.</p>
        <div class="empty-formats"><span>Pages</span><span>Diagrams</span><span>Widgets</span></div>
      </div>
      <div id="studio-loading" role="status" hidden>Loading preview…</div>
      <div id="studio-frame-error" class="studio-empty" hidden>
        <h2>Preview closed</h2><p>This page navigated away from its preview. Reopen the original to continue.</p>
        <button id="studio-reopen">Reopen preview</button>
      </div>`);
    $('artname').insertAdjacentHTML('afterend', '<select id="studio-artifacts" aria-label="Choose an artifact"><option value="">No previews yet</option></select>');
    $('artver').setAttribute('aria-label', 'Artifact version');
    $('artprev').textContent = 'Preview'; $('artcode').textContent = 'Code';
    $('artpoint').textContent = 'Point';
    $('artpoint').setAttribute('aria-pressed', 'false');
    $('arttweaks').setAttribute('aria-pressed', 'false');
    $('artclose').setAttribute('aria-label', 'Close preview');
    panel.setAttribute('aria-label', 'Artifact preview');
    artbody.setAttribute('tabindex', '0');
    artbody.setAttribute('aria-label', 'Preview canvas');
    main.setAttribute('aria-label', 'Conversation');
    main.insertAdjacentHTML('afterbegin', `
      <div class="drawer-heading"><div><strong>Conversation</strong><span id="studio-interaction-note">Messages, tool activity, and questions</span></div><button id="studio-interactions-close" aria-label="Close interactions">×</button></div>
      <div id="studio-interactions-empty" class="inspector-empty"><h2>What would you like to work on?</h2><p>Ask a question, build something, or choose a skill. Answers and tool activity appear here as Dream works.</p></div>`);
    rail.classList.add('hide');
    rail.setAttribute('aria-label', 'Studio inspector');
    rail.insertAdjacentHTML('afterbegin', '<div class="drawer-heading"><strong>Workspace details</strong><button id="studio-inspector-close" aria-label="Close workspace details">×</button></div>');
    $('tab-metrics').textContent = 'Metrics';
    $('tab-design').textContent = 'Design system';
    $('tab-undo').textContent = 'Checkpoints';
    const tabs = document.querySelector('#rail .tabs');
    tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Workspace details');
    const panes = {metrics:'railbody', undo:'undobody', design:'designbody'};
    for(const [name, id] of Object.entries(panes)){
      $('tab-' + name).setAttribute('role', 'tab');
      $('tab-' + name).setAttribute('aria-controls', id);
      $(id).setAttribute('role', 'tabpanel');
      $(id).setAttribute('aria-labelledby', 'tab-' + name);
    }
    tabs.addEventListener('keydown', e => {
      const buttons = [...tabs.querySelectorAll('button')], i = buttons.indexOf(document.activeElement);
      if(i < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      const next = e.key === 'Home' ? 0 : e.key === 'End' ? 2 : (i + (e.key === 'ArrowRight' ? 1 : 2)) % 3;
      buttons[next].click(); buttons[next].focus();
    });
    const footer = document.querySelector('footer');
    footer.insertAdjacentHTML('beforebegin', '<div class="studio-foot"><span id="studio-foot-note">Enter to send · Shift+Enter for a new line</span><button id="studio-compose" aria-controls="studio-composer" aria-expanded="false">Message Dream</button></div>');
    footer.id = 'studio-composer';
    footer.insertAdjacentHTML('afterbegin', '<div id="chat-progress" role="status" hidden>Waiting for first text…</div>');
    input.placeholder = 'Message Dream…'; input.setAttribute('aria-label', 'Message Dream');
    $('send').insertAdjacentHTML('beforebegin', '<button id="stop" hidden>Stop</button>');
    $('stop').onclick = async () => {
      $('stop').disabled = true;
      try {
        const response = await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN}, body:JSON.stringify({action:'interrupt'})});
        if(!response.ok){ const data = await response.json(); throw new Error(data.error || 'Could not stop Dream'); }
      } catch(e){ error(e.message); } finally { $('stop').disabled = false; }
    };
    // A new chat asks whether this one goes to long-term memory first (owner choice).
    $('studio-new').onclick = () => {
      if(document.getElementById('new-chat-ask')) return;
      const box = document.createElement('div'); box.id = 'new-chat-ask'; box.setAttribute('role', 'dialog');
      box.innerHTML = '<p>Save this chat to long-term memory before starting a new one?</p>'
        + '<p class="new-chat-note">Saving writes a summary and the memories worth keeping; it can take several minutes.</p>'
        + '<div><button data-new="save">Save and start new</button><button data-new="nosave">Start new without saving</button>'
        + '<button data-new="cancel">Cancel</button></div>';
      box.addEventListener('click', e => {
        const choice = e.target.closest('button')?.dataset.new; if(!choice) return;
        box.remove(); if(choice !== 'cancel') postPrompt('/new ' + choice);
      });
      document.body.appendChild(box); box.querySelector('button').focus();
    };
    $('studio-skills').onclick = () => window.DreamLibrary?.show('skills');

    document.querySelector('footer .hint').innerHTML = '<button id="read-twice" type="button" aria-pressed="false" title="Read ×2: the model reads each message you type twice (only your words, never the history)">Read ×2</button><span>Enter to send · Shift+Enter for a new line</span><span id="perf" title="Last request: prompt reading speed, generation speed, and how full the context window is"></span><span id="counts"></span>';
    document.querySelector('footer .hint').insertAdjacentHTML('afterbegin', '<button id="chat-mode" type="button" title="Shift+Tab cycles permission mode" aria-label="Cycle permission mode" aria-live="polite">Mode unavailable</button>');
    let modePending = false;
    async function permissionMode(cycle = false){
      if(modePending) return;
      modePending = true;
      const button = $('chat-mode'); button.disabled = true;
      try {
        const response = await fetch('/api/control', {method:'POST', headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN}, body:JSON.stringify({action:cycle ? 'permission_mode' : 'permission_mode_status'})});
        const data = await response.json();
        if(!response.ok) throw new Error(data.error || 'Could not read permission mode');
        const state = data.result;
        if(!Array.isArray(state?.modes) || !state.modes.includes(state.mode) || typeof state.labels?.[state.mode] !== 'string') throw new Error('Dream did not confirm its permission mode');
        button.textContent = state.labels[state.mode]; button.dataset.mode = state.mode;
        button.title = 'Shift+Tab cycles permission mode';
      } catch(e){
        button.textContent = cycle ? 'Mode unconfirmed' : 'Mode unavailable';
        delete button.dataset.mode; button.title = e.message;
        if(cycle) error(e.message);
      } finally { modePending = false; button.disabled = false; }
    }
    $('chat-mode').onclick = () => permissionMode(true);
    window.syncReadTwice?.();
    input.addEventListener('keydown', e => {
      if(e.key !== 'Tab' || !e.shiftKey || e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;
      e.preventDefault();
      if(!e.repeat) permissionMode(true);
    });
    $('mentionclear').setAttribute('aria-label', 'Clear selected element');

    function inspectorTab(which){
      for(const name of Object.keys(panes)){
        $('tab-' + name).setAttribute('aria-selected', String(name === which));
        $('tab-' + name).tabIndex = name === which ? 0 : -1;
      }
      const visible = !rail.classList.contains('hide');
      for(const [id, name] of [['studio-design','design'], ['studio-checkpoints','undo'], ['railtoggle','metrics']]){
        $(id).setAttribute('aria-expanded', String(visible && name === which));
        $(id).classList.toggle('selected', visible && name === which);
      }
    }
    function closeInspector(focus = false){
      rail.classList.add('hide'); body.classList.remove('inspector-open');
      panel.inert = false; main.inert = false;
      inspectorTab(tab);
      if(focus && opener) opener.focus();
    }
    function inspect(which, source){
      if(!rail.classList.contains('hide') && which === tab){ closeInspector(true); return; }
      opener = source;
      rail.classList.remove('hide'); body.classList.add('inspector-open');
      panel.inert = true; main.inert = true;
      showTab(which); $('tab-' + which).focus();
    }
    function drawer(open, focus = false){
      closeInspector();
      body.classList.toggle('interactions-open', open);
      $('studio-interactions').setAttribute('aria-expanded', String(open));
      $('studio-preview').setAttribute('aria-pressed', String(!open));
      window.dispatchEvent(new Event('dream:drawer'));
      if(focus) $('studio-interactions').focus();
    }
    function interactions(){
      const count = stream.querySelectorAll('.qform, .dlcard, .vcard').length;
      const questions = stream.querySelectorAll('.qform').length;
      $('studio-count').textContent = count; $('studio-count').hidden = !count;
      $('studio-interactions-empty').hidden = stream.children.length > 0;
      $('studio-interaction-note').textContent = questions ? `${questions} ${questions === 1 ? 'question form needs' : 'question forms need'} your answer` : 'Messages, tool activity, and questions';

    }
    function compose(){
      composing = true; body.classList.add('composing');
      $('studio-compose').textContent = 'Message Dream'; $('studio-compose').setAttribute('aria-expanded', 'true');
    }
    function hideCompose(){
      composing = false;
      $('studio-compose').textContent = 'Message Dream';
      $('studio-compose').setAttribute('aria-expanded', 'false');
    }
    function empty(el, title, detail){
      el.innerHTML = `<div class="inspector-empty"><h2>${esc(title)}</h2><p>${esc(detail)}</p></div>`;
    }
    function error(text){
      $('studio-error-text').textContent = text;
      $('studio-error').hidden = false;
    }
    function permissionDone(id){
      const el = document.getElementById('permission-' + id);
      if(el) el.remove();
      interactions();
    }
    function permission(data){
      if(!data?.id || document.getElementById('permission-' + data.id)) return;
      const el = document.createElement('section'); el.className = 'permission-card';
      el.id = 'permission-' + data.id; el.setAttribute('role', 'group');
      el.setAttribute('aria-label', 'Permission requested');
      el.innerHTML = `<h3>Dream needs your permission</h3><p>${esc(data.reason)}</p><details><summary>${esc(data.tool)}</summary><pre>${esc(JSON.stringify(data.input, null, 2))}</pre></details><div class="permission-actions"></div>`;
      for(const [value, label] of Object.entries(data.choices || {})){
        const button = document.createElement('button'); button.textContent = label;
        button.onclick = async () => {
          el.querySelectorAll('button').forEach(b => b.disabled = true);
          try {
            const response = await fetch('/api/permission', {method:'POST', headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN}, body:JSON.stringify({id:data.id, choice:value})});
            if(!response.ok){ const body = await response.json(); throw new Error(body.error || 'Could not send permission'); }
            permissionDone(data.id);
          } catch(e){ error(e.message); el.querySelectorAll('button').forEach(b => b.disabled = false); }
        };
        el.querySelector('.permission-actions').appendChild(button);
      }
      stream.appendChild(el); drawer(true); interactions(); scroll(true); $('stat').textContent = 'Waiting for you';
    }
    function artifactsChanged(){
      const selected = artOpen;
      const sel = $('studio-artifacts'); sel.replaceChildren();
      if(!selected) sel.add(new Option('Choose a preview', ''));
      for(const a of artifacts.values()) sel.add(new Option(a.title, a.key));
      sel.value = selected || '';
      body.classList.toggle('has-artifacts', artifacts.size > 0);
    }
    function paint(a){
      clearTimeout(frameTimer);
      $('studio-artifacts').value = artOpen;
      $('studio-frame-error').hidden = true;
      $('studio-loading').hidden = artView === 'code';
      panel.setAttribute('aria-busy', String(artView !== 'code'));
      for(const [id, active] of [['artprev', artView === 'preview'], ['artcode', artView === 'code']]) $(id).setAttribute('aria-pressed', String(active));
      $('artpoint').disabled = artView === 'code'; $('arttweaks').disabled = artView === 'code';
      $('studio-foot-note').textContent = `${a.kind.toUpperCase()} preview · v${artVerIdx + 1}`;
      if(artView !== 'code') frameTimer = setTimeout(() => {
        $('studio-loading').textContent = 'Preview is taking longer than expected. Use Preview to reload it.';
      }, 8000);
    }
    $('studio-preview').onclick = () => { drawer(false); if(artOpen && artView !== 'preview'){ artView = 'preview'; paintArtifact(); } };
    $('studio-interactions').onclick = () => { drawer(true); input.focus(); };
    $('studio-interactions-close').onclick = () => drawer(false, true);
    $('studio-design').onclick = e => inspect('design', e.currentTarget);
    $('studio-checkpoints').onclick = e => inspect('undo', e.currentTarget);
    $('railtoggle').onclick = e => inspect('metrics', e.currentTarget);
    $('studio-inspector-close').onclick = () => closeInspector(true);
    $('studio-compose').onclick = () => { if(composing) hideCompose(); else { compose(); grow(); input.focus(); } };
    $('studio-artifacts').onchange = e => { if(e.target.value) openArtifact(e.target.value); };
    $('studio-dismiss').onclick = () => { $('studio-error').hidden = true; $('studio-preview').focus(); };
    $('studio-retry').onclick = () => { connect(); };
    $('studio-reopen').onclick = () => { artView = 'preview'; paintArtifact(); };
    for(const id of ['artpoint', 'arttweaks']) $(id).addEventListener('click', () => $(id).setAttribute('aria-pressed', String($(id).classList.contains('on'))));
    document.addEventListener('keydown', e => {
      if(e.key !== 'Escape') return;
      if(!rail.classList.contains('hide')) closeInspector(true);
      else if(body.classList.contains('interactions-open')) drawer(false, true);
      else if(composing){ hideCompose(); $('studio-compose').focus(); }
    });
    inspectorTab('metrics');
    drawer(true); compose();
    // A slow first connection gets guidance too, without flashing a banner on
    // every normal startup. Reconnect never replaces the current artifact.
    setTimeout(() => { if(!connected) $('studio-connection').hidden = false; }, 1200);
    return {
      compose, empty, error, interactions, inspectorTab, artifacts: artifactsChanged,
      message(){ $('studio-interactions-empty').hidden = stream.children.length > 0; },
      permission, permissionDone,
      session(s){
        window.DREAM_SESSION = s;
        window.dispatchEvent(new Event("dream:session"));
        const workspace = String(s.workspace || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop();
        $('meta').textContent = [s.model || s.provider, workspace].filter(Boolean).join(' · ') || 'Dream workspace';
        $('meta').title = [s.workspace, s.provider, s.model, s.session_id].filter(Boolean).join('\n');
      },
      status(on){
        $('stat').textContent = on ? 'Working' : socket?.readyState === 1 ? 'Ready' : 'Connecting';
        if(on && $('chat-progress').hidden) $('chat-progress').textContent = 'Waiting for first text…';
        $('chat-progress').hidden = !on;
      },
      progress(info){
        if(!working || !info) return;
        const seconds = Math.max(0, Math.floor(info.elapsed_s || 0));
        const elapsed = seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
        const phase = stream.querySelector('.permission-card') ? 'Waiting for you' : tools.size ? 'Running tools' :
          info.state === 'waiting' ? 'Waiting for first text' : thinking ? 'Thinking' : 'Writing reply';
        $('chat-progress').textContent = `${phase} · ${elapsed}` + (info.state === 'waiting' && seconds >= 45 ? ' · Your model is still processing this request. Stop is available.' : '');
      },
      connection(on){
        connected = on; $('studio-connection').hidden = on;
        if(on) permissionMode();
        if(!on){
          $('stat').textContent = 'Reconnecting';
          $('studio-connection-title').textContent = 'Connection interrupted';
          $('studio-connection-detail').textContent = 'Your preview is kept here. Studio will reconnect automatically while the terminal is running.';
        }
      },
      loadError(el, name, retryLoad){
        empty(el, `Could not load ${name}`, 'Check the terminal connection, then try again.');
        const button = document.createElement('button'); button.textContent = 'Try again';
        button.onclick = retryLoad; el.firstElementChild.appendChild(button);
      },
      interaction(el, kind){
        interactions();
        // Questions need an answer. Other visual output opens the drawer only
        // when no preview is on the canvas; the count keeps it discoverable.
        if(kind === 'question' || !artOpen){ drawer(true); el.scrollIntoView({block:'nearest'}); }
        el.querySelectorAll('fieldset').forEach((fs, i) => {
          const legend = fs.querySelector('legend'); legend.id = `studio-q-${++questionSeq}`;
          fs.querySelectorAll('textarea, input:not([type=radio]):not([type=checkbox])').forEach(c => c.setAttribute('aria-labelledby', legend.id));
          fs.querySelectorAll('.qsvg input').forEach((c, j) => { c.hidden = false; c.setAttribute('aria-label', `Option ${j + 1}`); });
        });
      },
      openArtifact(){
        closeInspector(); body.classList.add('has-artifact'); $('studio-empty').hidden = true;
        artifactsChanged();
        if(!stream.querySelector('.qform')) drawer(false);
      },
      closeArtifact(){
        clearTimeout(frameTimer); frameReady = false;
        artbody.replaceChildren(); body.classList.remove('has-artifact');
        $('studio-empty').hidden = false; $('studio-frame-error').hidden = true; $('studio-loading').hidden = true;
        panel.setAttribute('aria-busy', 'false'); closeInspector(); artifactsChanged();
        $('studio-foot-note').textContent = 'Enter to send · Shift+Enter for a new line';
        $('studio-artifacts').focus();
      },
      paintArtifact: paint,
      frameLoaded(){ clearTimeout(frameTimer); $('studio-loading').hidden = true; $('studio-loading').textContent = 'Loading preview…'; panel.setAttribute('aria-busy', 'false'); },
      frameClosed(){ $('studio-frame-error').hidden = false; },
      submitted(){ drawer(true); input.focus(); },
    };
  })();
}
