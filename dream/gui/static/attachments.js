/* User-selected files belong to the draft until the server accepts the message. */
const draftAttachments = (() => {
  const footer = document.querySelector('footer');
  footer.insertAdjacentHTML('afterbegin', '<div id="attachments" aria-label="Attached files" aria-live="polite"></div>');
  document.querySelector('.composer').insertAdjacentHTML('afterbegin', '<button id="attach" type="button" aria-label="Attach files" title="Attach files · up to 8 files, 25 MB each">＋</button><input id="file-input" type="file" multiple hidden>');
  const picker = $('file-input'), list = $('attachments'), attach = $('attach');
  const key = 'dream-draft:' + TOKEN;
  let files = [];
  function save(){
    try { sessionStorage.setItem(key, JSON.stringify({text:input.value, files:files.filter(f => f.id).map(({id,name,size,path,workspace,session_id}) => ({id,name,size,path,workspace,session_id}))})); } catch(e) { /* A private browser can disable tab storage; the live draft still works. */ }
  }
  function render(){
    list.replaceChildren();
    for(const file of files){
      const chip = document.createElement('div'); chip.className = 'attachment' + (file.error ? ' failed' : '');
      const name = document.createElement('span'); name.className='attachment-name'; name.textContent=file.name;
      const state = document.createElement('span'); state.className='attachment-state';
      state.textContent = file.error || (file.id ? 'Ready' : 'Uploading…');
      chip.append(name, state);
      if(file.error && file.file){
        const retry = document.createElement('button'); retry.textContent='Retry'; retry.disabled=sending;
        retry.onclick = () => upload(file); chip.appendChild(retry);
      }
      const remove = document.createElement('button'); remove.textContent='×'; remove.setAttribute('aria-label','Remove ' + file.name); remove.disabled=sending;
      remove.onclick = () => { file.controller?.abort(); files = files.filter(f => f !== file); render(); save(); };
      chip.appendChild(remove); list.appendChild(chip);
    }
    list.hidden = files.length === 0;
    attach.disabled = sending || files.length >= 8;
    send.disabled = sending || files.some(f => !f.id);
  }
  async function upload(item){
    item.error = ''; item.controller = new AbortController(); render();
    try {
      const response = await fetch('/api/upload?name=' + encodeURIComponent(item.name), {method:'POST', headers:{'Content-Type':'application/octet-stream','X-Dream-Token':TOKEN}, body:item.file, signal:item.controller.signal});
      const data = await response.json();
      if(!response.ok) throw new Error(data.error || 'Upload failed. Retry or remove this file.');
      if(typeof data.id !== 'string' || typeof data.path !== 'string') throw new Error('Dream did not confirm the uploaded file. Retry or remove it.');
      Object.assign(item, data); item.file = null;
    } catch(e){ if(e.name !== 'AbortError') item.error = e.message || 'Upload failed. Retry or remove this file.'; }
    render(); save();
  }
  function add(selected){
    if(window.DREAM_PROJECT_SWITCHING){sysline('Wait for the project switch before attaching files.', 'error');return;}
    if(sending) return;
    for(const file of selected){
      if(files.length >= 8){ sysline('Attach at most 8 files per message. Send these first or remove a file.', 'error'); break; }
      const item = {name:file.name, size:file.size, file}; files.push(item);
      if(file.size > 25*1024*1024){ item.error='Over 25 MB. Choose a smaller file.'; item.file=null; render(); }
      else upload(item);
    }
    save();
  }
  attach.onclick = () => picker.click();
  picker.onchange = () => { add(picker.files); picker.value=''; };
  footer.addEventListener('dragover', e => { if(e.dataTransfer.types.includes('Files')) { e.preventDefault(); footer.classList.add('file-drag'); } });
  footer.addEventListener('dragleave', e => { if(!footer.contains(e.relatedTarget)) footer.classList.remove('file-drag'); });
  footer.addEventListener('drop', e => { if(e.dataTransfer.files.length){ e.preventDefault(); footer.classList.remove('file-drag'); add(e.dataTransfer.files); } });
  input.addEventListener('paste', e => { if(e.clipboardData?.files.length){ e.preventDefault(); add(e.clipboardData.files); } });
  input.addEventListener('input', save);
  try {
    const draft = JSON.parse(sessionStorage.getItem(key) || 'null');
    if(draft && typeof draft.text === 'string') input.value = draft.text;
    files = (Array.isArray(draft?.files) ? draft.files : []).filter(f => typeof f.id === 'string' && typeof f.name === 'string').slice(0,8);
  } catch(e) { /* An invalid saved draft does not block a new conversation. */ }
  grow(); render();
  function adopt(records){
    if(sending || window.DREAM_PROJECT_SWITCHING || input.readOnly || footer.inert) throw Error('Wait until the chat draft is available before adding this prompt.');
    if(!Array.isArray(records) || records.length>8) throw Error('Attach at most 8 files to a chat draft.');
    const session=window.DREAM_SESSION;
    if(!session?.workspace || !session?.session_id) throw Error('Wait for the active session before transferring files.');
    const merged=[...files], seen=new Map(files.filter(f=>f.id).map(f=>[f.id,f]));
    for(const record of records){
      if(!record || typeof record.id!=='string' || !/^[A-Za-z0-9_-]{1,256}$/.test(record.id)
        || typeof record.name!=='string' || !record.name || record.name.length>200
        || typeof record.path!=='string' || !record.path.startsWith('uploads/') || record.path.includes('\\') || record.path.split('/').includes('..')
        || !Number.isSafeInteger(record.size) || record.size<0 || record.size>25*1024*1024
        || record.workspace!==session.workspace || record.session_id!==session.session_id)
        throw Error('A reference file belongs to another session or was not confirmed. Attach it again in the current workspace.');
      const previous=seen.get(record.id);
      if(previous){
        if(previous.path!==record.path || previous.name!==record.name || previous.size!==record.size) throw Error('Conflicting attachment metadata. Keep the existing draft and attach the file again.');
        continue;
      }
      const copy={id:record.id,name:record.name,size:record.size,path:record.path,workspace:record.workspace,session_id:record.session_id};
      merged.push(copy); seen.set(copy.id,copy);
    }
    if(merged.length>8) throw Error('The chat draft would have more than 8 files. Remove a file before adding this prompt.');
    files=merged;render();save();
  }
  return {render, save, adopt, ready:() => files.every(f => f.id), ids:() => files.map(f => f.id),
    sent(ids){ files = files.filter(f => !ids.includes(f.id)); render(); save(); }};
})();
