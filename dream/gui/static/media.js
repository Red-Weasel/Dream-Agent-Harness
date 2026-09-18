/* Create uses the same durable media service as CLI/model tools. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const token = new URLSearchParams(location.search).get('token') || '';
  const opener = document.createElement('button');
  opener.id = 'dream-create-open'; opener.textContent = 'Create';
  opener.setAttribute('aria-haspopup', 'dialog'); opener.setAttribute('aria-controls', 'dream-create');
  document.querySelector('header .status').before(opener);
  const panel = document.createElement('dialog'); panel.id = 'dream-create';
  panel.setAttribute('aria-labelledby', 'mc-heading');
  panel.innerHTML = `
    <div class="mc-top"><div><span class="mc-eyebrow">DREAM / CREATE</span><h2 id="mc-heading">Create an animation</h2></div><div class="mc-row"><button id="mc-guided-open" aria-controls="mc-guided" aria-expanded="false">Guided tasks</button><button id="mc-close" aria-label="Close Create">×</button></div></div>
    <div id="mc-feedback" role="status" hidden></div>
    <section id="mc-guided" aria-label="Guided tasks" hidden></section>
    <div class="mc-layout">
      <aside class="mc-library"><h3>Projects</h3><form id="mc-new"><label for="mc-name">New project</label><input id="mc-name" placeholder="Software showcase" required maxlength="200"><button type="submit" class="mc-primary">Start animation</button></form><div id="mc-projects"></div><div class="mc-note">Starts with three 5-second scenes. Edit the words, preview, then export. Runs locally without a generation model.</div></aside>
      <main class="mc-main"><div id="mc-empty"><span class="mc-eyebrow">START HERE</span><h3>Give your idea a little motion.</h3><p>Name your project to start a 15-second animation. Change the text and add screenshots, then save and play the preview.</p><p class="mc-note">The starter uses landscape 720p at 24 frames per second. You can change the size before export.</p></div>
        <div id="mc-editor" hidden>
          <div class="mc-project-heading"><div><h3 id="mc-title"></h3><span id="mc-revision"></span><p id="mc-summary" class="mc-note"></p></div><button id="mc-save" class="mc-primary">Save & preview</button></div>
          <div class="mc-preview"><iframe id="mc-frame" title="Animation preview" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe></div>
          <div class="mc-row mc-export"><label>Format<select id="mc-format"><option value="mp4">MP4 video</option><option value="webm">WebM video</option><option value="html">Website animation · HTML</option><option value="png">Still image · PNG</option></select></label><label>Canvas<select id="mc-canvas"><option value="1920,1080">Landscape · 1080p</option><option value="1080,1920">Portrait · 1080p</option><option value="1080,1080">Square · 1080p</option><option value="1280,720">Landscape · 720p · starter</option><option value="3840,2160">Landscape · 4K</option></select></label><button id="mc-render" class="mc-primary">Export MP4</button></div><p id="mc-format-help" class="mc-note"></p><p id="mc-renderer-status" class="mc-note" role="status"></p>
          <div class="mc-row"><h3 class="mc-grow">Scenes</h3><button id="mc-add">＋ Add scene</button></div><div id="mc-scenes" aria-label="Scene list"></div>
          <form id="mc-scene-form"><div class="mc-row"><span id="mc-scene-number" class="mc-eyebrow"></span><button id="mc-up" type="button">Move earlier</button><button id="mc-delete" type="button">Remove</button></div><label>Title<input id="mc-scene-title" maxlength="4000"></label><label>Body<textarea id="mc-scene-body" rows="3" maxlength="4000"></textarea></label><div class="mc-row"><label>Scene length in seconds<input id="mc-duration" type="number" min="0.1" max="600" step="0.1"></label><label>Text entrance<select id="mc-motion"><option value="slide">Slide</option><option value="fade">Fade</option><option value="zoom">Zoom</option><option value="none">Still</option></select></label></div><details id="mc-style"><summary>Scene colors</summary><div class="mc-row"><label>Background<input id="mc-background" type="color"></label><label>Accent<input id="mc-accent" type="color"></label></div></details><p class="mc-note">Scene length sets how long this scene stays on screen. Entrance controls how its text appears.</p><label>Scene image or clip<select id="mc-scene-asset"><option value="">Text only</option></select></label></form>
          <details class="mc-advanced"><summary>Composition source & revision history</summary><p>Edit the complete scene document or restore a prior saved revision as a new revision.</p><textarea id="mc-source" rows="12" spellcheck="false" aria-label="Composition JSON"></textarea><button id="mc-source-apply">Apply source</button><div class="mc-row"><label>Revision<input id="mc-history-number" type="number" min="1" value="1"></label><button id="mc-history-load">Load revision into editor</button></div></details>
        </div>
      </main>
      <aside class="mc-assets"><section><h3>Assets</h3><label class="mc-file">Import images, clips or audio<input id="mc-upload" type="file" accept="image/*,video/*,audio/*,.html,.blend" multiple></label><div id="mc-asset-list"></div><label>Soundtrack<select id="mc-audio"><option value="">No soundtrack</option></select></label></section>
      <details id="mc-subscriptions"><summary>Create images or clips with a subscription</summary><p class="mc-note">Open your signed-in service, use the saved prompt and references, then import the result. Your existing plan’s limits apply.</p><label>Service<select id="mc-provider"><option value="chatgpt">ChatGPT</option><option value="gemini">Gemini</option><option value="grok">Grok</option><option value="claude">Claude · coded visuals</option></select></label><label>Prompt<textarea id="mc-prompt" rows="4" placeholder="Describe the image or video…"></textarea></label><button id="mc-handoff">Prepare prompt & service link</button></details>
      <details><summary>Local generation · ComfyUI</summary><p class="mc-note">Advanced setup for AI-generated images or video. Requires a running ComfyUI server, installed models and an API workflow. Server availability is not checked here. Ordinary animation and export above do not need ComfyUI.</p><label>Server<input id="mc-backend" value="http://127.0.0.1:8188"></label><label>API workflow JSON<textarea id="mc-workflow" rows="6" spellcheck="false"></textarea></label><label class="mc-check"><input type="checkbox" id="mc-resource">I checked GPU memory, temperature and active workloads.</label><label class="mc-check"><input type="checkbox" id="mc-reviewed">I reviewed this local workflow and its model requirements.</label><button id="mc-generate">Run local workflow</button></details>
      <details><summary>Blender workspace</summary><p class="mc-note">Open a workspace .blend file in installed Blender. Import its rendered result here.</p><label>Project file<input id="mc-blend-path" placeholder="scenes/demo.blend"></label><p id="mc-blender-status" class="mc-note"></p><button id="mc-blender">Open in Blender</button></details>
      <section><div class="mc-row"><h3 class="mc-grow">Jobs</h3><button id="mc-refresh" title="Refresh jobs" aria-label="Refresh jobs">↻</button></div><div id="mc-jobs"></div><details><summary>Job recovery</summary><p>Use after an interrupted Dream worker. Running workers cannot be recovered.</p><button id="mc-recover">Recover stopped workers</button></details></section></aside>
    </div>`;
  document.body.append(panel);
  let project = null, assets = [], selected = 0, dirty = false, timer = null, previewSequence = 0, jobsSignature = '', rendererStatus = null;
  const notice = (text, error=false) => { $('mc-feedback').hidden = !text; $('mc-feedback').textContent = text; $('mc-feedback').classList.toggle('mc-error', error); };
  const safe = fn => async event => {
    if(event)event.preventDefault();
    const control = event?.currentTarget;
    if(control?.dataset.busy)return;
    if(control)control.dataset.busy='true';
    try { await fn(event); } catch(error) { notice(error.message, true); }
    finally { if(control)delete control.dataset.busy; }
  };
  async function api(action, payload) {
    const response = await fetch('/api/media/' + action, {method: payload === undefined ? 'GET':'POST', headers:{'x-dream-token':token, ...(payload === undefined?{}:{'content-type':'application/json'})}, ...(payload === undefined?{}:{body:JSON.stringify(payload)})});
    let result;
    try { result = await response.json(); } catch { throw Error('Dream returned an unreadable media response. Check the connection and try again.'); }
    if(!response.ok)throw Error(result.error || 'Media request failed'); return result;
  }
  function button(text, action) { const b = document.createElement('button'); b.textContent = text; b.onclick = safe(action); return b; }
  function requireProject() { if(!project)throw Error('Create or select a project first.'); }
  function mark() { dirty = true; $('mc-revision').textContent = 'Revision ' + project.revision + ' · unsaved changes'; updateCompositionControls(); }
  function updateExport() {
    const format=$('mc-format').value;
    const descriptions={mp4:'MP4 works in most players and sharing apps. Video exports render each frame locally; longer scenes and larger canvases take more time.',webm:'WebM is a video format for websites. Encoding can take longer than MP4.',html:'HTML is a portable animation you can open in a browser or embed in a website. No video encoding is needed.',png:'PNG exports one still frame near the beginning. Choose MP4 or HTML to keep the motion.'};
    $('mc-format-help').textContent=descriptions[format];
    $('mc-render').textContent='Export '+format.toUpperCase();
    const needsVideo=['mp4','webm'].includes(format);
    const missing=rendererStatus?['ffmpeg','ffprobe'].filter(name=>!rendererStatus[name]):[];
    $('mc-render').disabled=needsVideo&&missing.length>0;
    $('mc-renderer-status').textContent=missing.length&&needsVideo?'Video export needs '+missing.join(' and ')+'. Install the missing FFmpeg tools, or select HTML.':format==='html'?'HTML export is local and needs no generation service.':rendererStatus?'Uses local Chromium'+(needsVideo?' and FFmpeg':'')+'. Browser setup is checked when exporting.':'Checking local export tools…';
  }
  function updateCompositionControls() {
    const c=project.composition, value=c.width+','+c.height;
    const canvas=$('mc-canvas');
    canvas.querySelector('option[data-custom]')?.remove();
    if(!Array.from(canvas.options).some(option=>option.value===value)){
      const option=new Option('Custom · '+c.width+' × '+c.height,value);option.dataset.custom='true';canvas.add(option);
    }
    canvas.value=value;
    $('mc-summary').textContent=c.scenes.length+' scenes · '+Number(c.scenes.reduce((n,s)=>n+s.duration,0).toFixed(1))+' seconds · '+c.width+' × '+c.height+' · '+c.fps+' fps';
  }
  function syncScene() {
    if(!project)return;
    const s=project.composition.scenes[selected];
    s.title=$('mc-scene-title').value;s.body=$('mc-scene-body').value;s.duration=Number($('mc-duration').value);s.animation=$('mc-motion').value;s.background=$('mc-background').value;s.accent=$('mc-accent').value;
    const id=$('mc-scene-asset').value;
    if(id){s.asset_id=id;s.asset_type=assets.find(a=>a.id===id)?.mime.startsWith('video/')?'video':'image';}else{delete s.asset_id;delete s.asset_type;}
  }
  function source(){ $('mc-source').value=JSON.stringify(project.composition,null,2); }
  function drawScene() {
    const scenes=project.composition.scenes,s=scenes[selected];$('mc-scenes').replaceChildren();
    scenes.forEach((scene,index)=>{const b=button(String(index+1).padStart(2,'0')+'  '+(scene.title||'Untitled')+' · '+scene.duration+'s',()=>{syncScene();selected=index;drawScene();});b.classList.toggle('selected',index===selected);b.setAttribute('aria-pressed',String(index===selected));$('mc-scenes').append(b);});
    $('mc-scene-number').textContent='SCENE '+(selected+1);$('mc-scene-title').value=s.title||'';$('mc-scene-body').value=s.body||'';$('mc-duration').value=s.duration;$('mc-motion').value=s.animation||'fade';$('mc-background').value=s.background||'#09252d';$('mc-accent').value=s.accent||'#92e3cf';$('mc-scene-asset').value=s.asset_id||'';
    $('mc-delete').disabled=scenes.length===1;$('mc-up').disabled=selected===0;source();
  }
  function drawAssets() {
    $('mc-asset-list').replaceChildren();$('mc-scene-asset').replaceChildren(new Option('Text only',''));$('mc-audio').replaceChildren(new Option('No soundtrack',''));
    assets.forEach(a=>{
      const row=document.createElement('div');row.className='mc-asset';
      const link=document.createElement('a');link.textContent=a.name;link.href='/api/media/assets/'+encodeURIComponent(a.id)+'?token='+encodeURIComponent(token);link.target='_blank';link.rel='noopener';row.append(link);
      const small=document.createElement('small');small.textContent=(a.size/1048576).toFixed(1)+' MiB · '+a.mime;row.append(small);$('mc-asset-list').append(row);
      if(a.mime.startsWith('image/')||a.mime.startsWith('video/'))$('mc-scene-asset').add(new Option(a.name,a.id));
      if(a.mime.startsWith('audio/'))$('mc-audio').add(new Option(a.name,a.id));
    });
    $('mc-audio').value=project?.composition.audio_asset_id||'';
  }
  async function refreshProjects(){const result=await api('projects');$('mc-projects').replaceChildren();result.projects.forEach(p=>{const b=button(p.title,()=>load(p.id));b.classList.toggle('selected',p.id===project?.id);$('mc-projects').append(b);});}
  async function preview(){const seq=++previewSequence, c=project.composition;const result=await api('preview',{project_id:project.id});if(seq!==previewSequence)return;$('mc-frame').parentElement.style.aspectRatio=c.width+' / '+c.height;$('mc-frame').parentElement.style.width='min(100%, '+(34*c.width/c.height)+'vh)';const csp=`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; media-src data:; font-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">`;$('mc-frame').srcdoc=result.html.replace('<meta charset="utf-8">','<meta charset="utf-8">'+csp);}
  async function load(id){if(dirty)await save();const result=await api('get',{project_id:id});project=result.project;assets=result.assets;selected=0;dirty=false;$('mc-empty').hidden=true;$('mc-editor').hidden=false;$('mc-title').textContent=project.title;$('mc-revision').textContent='Revision '+project.revision;updateCompositionControls();drawAssets();drawScene();await refreshProjects();await preview();await jobs();}
  async function save(){requireProject();syncScene();project=await api('save',{project_id:project.id,composition:project.composition,expected_revision:project.revision});dirty=false;$('mc-revision').textContent='Revision '+project.revision;drawScene();updateCompositionControls();await preview();notice('Project saved. Press Play in the preview to watch it.');}
  async function upload(file, jobId){requireProject();const q=new URLSearchParams({project_id:project.id,filename:file.name});if(jobId)q.set('job_id',jobId);const response=await fetch('/api/media/upload?'+q,{method:'POST',headers:{'x-dream-token':token},body:file});const result=await response.json();if(!response.ok)throw Error(result.error);const updated=await api('get',{project_id:project.id});assets=updated.assets;syncScene();drawAssets();drawScene();await jobs();notice('Imported '+file.name);}
  async function jobs(){
    const projectId=project?.id;
    const result=await api('jobs',projectId?{project_id:projectId}:{});
    if(projectId!==project?.id)return;
    if(project && result.jobs.some(j=>(j.result?.asset_ids||[]).some(id=>!assets.some(a=>a.id===id)))){
      const updated=await api('get',{project_id:projectId});
      if(projectId!==project?.id)return;
      syncScene();assets=updated.assets;drawAssets();drawScene();
    }
    const signature=JSON.stringify(result);
    if(signature===jobsSignature)return;
    const focused=document.activeElement, focusedJob=focused?.closest('.mc-job');
    if(focusedJob && ['INPUT','TEXTAREA'].includes(focused.tagName) && result.jobs.some(j=>j.id===focusedJob.dataset.jobId && j.status===focusedJob.dataset.status))return;
    jobsSignature=signature;
    $('mc-jobs').replaceChildren();result.jobs.forEach(j=>{
    const row=document.createElement('article');row.className='mc-job';row.dataset.status=j.status;row.dataset.jobId=j.id;
    const title=document.createElement('strong');
    const states={queued:'Queued',running:'Working',succeeded:'Ready',failed:'Failed',awaiting_user:'Waiting for your result',unknown:'Outcome needs checking',interrupted:'Interrupted',cancel_requested:'Stopping observation',cancelled:'Cancelled'};
    const kind=j.kind==='render'?(j.request.format||'').toUpperCase()+' export':j.kind==='handoff'?'Subscription generation':j.kind==='open_external'?'Blender':'Local generation';
    title.textContent=kind+' · '+(states[j.status]||j.status);row.append(title);
    if(j.kind==='render'){const revision=document.createElement('p');revision.textContent='Saved revision '+j.request.revision;row.append(revision);}
    const next={failed:'Read the error below, fix the setting or dependency, then start a new export.',interrupted:'The worker stopped before completion. You can start a new export.',unknown:'ComfyUI may still be running. Check its queue before creating another generation request.',awaiting_user:'Generate in the service, download the result, then import it here.',queued:'Waiting for the workspace media worker. You can keep editing.'};
    if(next[j.status]){const hint=document.createElement('p');hint.textContent=next[j.status];row.append(hint);}
    if(j.status==='running'){const progress=document.createElement('progress');progress.max=1;progress.value=j.progress||0;row.append(progress);}
    if(j.error){const note=document.createElement('p');note.textContent=j.error;row.append(note);}
    if(j.request.url&&j.status==='awaiting_user'){const link=document.createElement('a');link.href=j.request.url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Open '+j.request.provider+' ↗';row.append(link);const prompt=document.createElement('textarea');prompt.value=j.request.prompt;prompt.readOnly=true;prompt.setAttribute('aria-label','Prepared generation prompt');row.append(prompt);row.append(button('Copy prompt',async()=>{try{await navigator.clipboard.writeText(j.request.prompt);notice('Prompt copied.');}catch{prompt.select();notice('Select and copy the prepared prompt.');}}));}
    if(j.status==='awaiting_user'){const label=document.createElement('label');label.textContent='Import finished result';const file=document.createElement('input');file.type='file';file.accept='image/*,video/*';file.onchange=safe(async()=>{if(file.files[0])await upload(file.files[0],j.id);});label.append(file);row.append(label);}
    if(['queued','running','awaiting_user'].includes(j.status))row.append(button('Cancel',async()=>{await api('cancel',{job_id:j.id});await jobs();}));
    if(j.status==='unknown')row.append(button('Reconcile',async()=>{await api('reconcile',{job_id:j.id});await jobs();}));
    (j.result?.asset_ids||[]).forEach(id=>{const a=document.createElement('a');a.textContent=j.kind==='render'?'Download '+(j.request.format||'output').toUpperCase()+' ↗':'Open output ↗';a.href='/api/media/assets/'+encodeURIComponent(id)+'?token='+encodeURIComponent(token);a.target='_blank';a.rel='noopener';row.append(a);});$('mc-jobs').append(row);
  });}
  opener.onclick=safe(async()=>{panel.showModal();updateExport();await refreshProjects();const status=await api('status');rendererStatus=status.renderer;$('mc-blender').disabled=!status.blender.available;$('mc-blender-status').textContent=status.blender.available?'Blender is installed. Choose an existing workspace file.':'Blender is not installed. Import rendered images or clips through Assets instead.';updateExport();await jobs();clearInterval(timer);timer=setInterval(()=>jobs().catch(e=>notice(e.message,true)),3000);});
  $('mc-close').onclick=()=>panel.close();panel.addEventListener('close',()=>{clearInterval(timer);opener.focus();});
  $('mc-new').onsubmit=safe(async()=>{if(dirty)await save();const p=await api('create',{title:$('mc-name').value});await load(p.id);$('mc-name').value='';});
  $('mc-save').onclick=safe(save);
  $('mc-scene-form').onsubmit=e=>e.preventDefault();$('mc-scene-form').oninput=()=>{syncScene();mark();source();};
  $('mc-add').onclick=safe(()=>{requireProject();syncScene();project.composition.scenes.push({id:'scene-'+Date.now(),duration:5,title:'Your next idea',body:'',animation:'slide',background:'#09252d',accent:'#92e3cf'});selected=project.composition.scenes.length-1;mark();drawScene();});
  $('mc-delete').onclick=safe(()=>{project.composition.scenes.splice(selected,1);selected=Math.max(0,selected-1);mark();drawScene();});
  $('mc-up').onclick=safe(()=>{syncScene();const s=project.composition.scenes;[s[selected-1],s[selected]]=[s[selected],s[selected-1]];selected--;mark();drawScene();});
  $('mc-canvas').onchange=()=>{const [w,h]=$('mc-canvas').value.split(',').map(Number);project.composition.width=w;project.composition.height=h;mark();source();};
  $('mc-audio').onchange=safe(()=>{requireProject();if($('mc-audio').value)project.composition.audio_asset_id=$('mc-audio').value;else delete project.composition.audio_asset_id;mark();source();});
  $('mc-source-apply').onclick=safe(async()=>{requireProject();const result=await api('validate',{composition:JSON.parse($('mc-source').value)});project.composition=result.composition;selected=0;mark();drawAssets();drawScene();notice('Source applied to the draft. Save to update the preview.');});
  $('mc-history-load').onclick=safe(async()=>{requireProject();const result=await api('get',{project_id:project.id,revision:Number($('mc-history-number').value)});project.composition=result.project.composition;selected=0;mark();drawAssets();drawScene();notice('Prior revision loaded. Save to create a new revision.');});
  $('mc-format').onchange=updateExport;
  $('mc-render').onclick=safe(async()=>{requireProject();if(dirty)await save();await api('render',{project_id:project.id,format:$('mc-format').value,idempotency_key:crypto.randomUUID()});await jobs();notice('Export submitted. Follow its progress in Jobs; download appears when the file is ready.');});
  $('mc-upload').onchange=safe(async()=>{for(const file of $('mc-upload').files)await upload(file);$('mc-upload').value='';});
  $('mc-handoff').onclick=safe(async()=>{requireProject();await api('handoff',{project_id:project.id,provider:$('mc-provider').value,prompt:$('mc-prompt').value,asset_ids:assets.filter(a=>a.mime.startsWith('image/')).map(a=>a.id),idempotency_key:crypto.randomUUID()});await jobs();notice('Prompt saved. Open the service from Jobs, generate there, then import the result.');});
  $('mc-generate').onclick=safe(async()=>{requireProject();await api('generate',{project_id:project.id,workflow:JSON.parse($('mc-workflow').value),base_url:$('mc-backend').value,resource_confirmed:$('mc-resource').checked,workflow_confirmed:$('mc-reviewed').checked,idempotency_key:crypto.randomUUID()});await jobs();});
  $('mc-blender').onclick=safe(async()=>{requireProject();await api('open_external',{project_id:project.id,path:$('mc-blend-path').value,idempotency_key:crypto.randomUUID()});await jobs();});
  $('mc-refresh').onclick=safe(jobs);$('mc-recover').onclick=safe(async()=>{await api('recover',{});await jobs();notice('Stopped jobs reconciled. Unknown generation jobs require backend inspection.');});
})();
