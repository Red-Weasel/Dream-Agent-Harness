/* DeepSleep: independently connected terminals on a persistent workflow board. */
(() => {
  'use strict';
  const root = document.documentElement;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (text, title, fn) => { const b = el('button', text); b.type = 'button'; b.setAttribute('aria-label', title || text); b.title = title || text; b.onclick = fn; return b; };
  const select = label => { const n = el('select'); n.setAttribute('aria-label', label); return n; };
  const page = el('section', null, 'ds-page'); page.id = 'dream-deepsleep'; page.setAttribute('aria-label', 'DeepSleep terminal workspace'); page.hidden = true;
  const toolbar = el('header', null, 'ds-toolbar');
  const brand = el('div', null, 'ds-brand'); brand.append(el('span', 'DEEPSLEEP', 'ds-eyebrow'), el('h1', 'Your models, connected.'));
  const controls = el('div', null, 'ds-controls');
  const provider = select('New terminal provider'), model = select('New terminal model'), profile = select('Terminal experience');
  for (const [value, label] of [['native', 'Native CLI'], ['dream', 'Dream workflow']]) { const option = el('option', label); option.value = value; profile.append(option); }
  const terminalName = el('input', null, 'ds-terminal-name'); terminalName.type = 'text'; terminalName.maxLength = 100;
  terminalName.placeholder = 'Terminal name (optional)'; terminalName.setAttribute('aria-label', 'New terminal name');
  terminalName.title = 'Name this terminal before opening it, for example Claude · Research';
  const add = button('+ Terminal', 'Add terminal', () => createNode()); add.className = 'ds-primary';
  const tileButton = button('▦ Tile', 'Tile terminals', () => tileNodes());
  const refreshButton = button('Refresh', 'Refresh workspace', () => refresh());
  const groupButton = button('Group task', 'Plan a group task', () => { groupPanel.hidden = !groupPanel.hidden; if (!groupPanel.hidden) groupPrompt.focus(); });
  const connectionsButton = button('Message Board', 'Message Board: show connections and delivery activity', () => setChannelOpen(activity.hidden));
  const characterToggle = button('Characters', 'Characters', () => setSkin(skin === 'characters' ? 'terminal' : 'characters', true));
  characterToggle.id = 'ds-character-toggle'; characterToggle.dataset.action = 'character-skin'; characterToggle.setAttribute('aria-pressed','false');
  characterToggle.title = 'Change terminal appearance only; sessions and input stay the same';
  const textLayoutSelect = select('Text layout'); textLayoutSelect.id='ds-text-layout';
  for(const [value,label] of [['terminal','Terminal'],['conversation','Conversation'],['activity','Activity']]) {const option=el('option',label);option.value=value;textLayoutSelect.append(option);}
  textLayoutSelect.onchange=()=>setTextLayout(textLayoutSelect.value,true);
  controls.append(provider, profile, model, terminalName, add, tileButton, groupButton, connectionsButton, textLayoutSelect, characterToggle, refreshButton); toolbar.append(brand, controls);
  const feedback = el('p', 'Native CLI gives you provider commands. Dream workflow terminals can exchange linked tasks.', 'ds-feedback'); feedback.setAttribute('role', 'status');
  const groupPanel = el('section', null, 'ds-group'); groupPanel.hidden = true;
  const targets = select('Research terminals'); targets.multiple = true;
  const synthesis = select('Synthesis terminal');
  const groupPrompt = el('textarea'); groupPrompt.rows = 2; groupPrompt.placeholder = 'What should the group work on?'; groupPrompt.setAttribute('aria-label', 'Group task prompt');
  const groupRun = button('Run group', 'Run group task', () => runGroup()); groupRun.className = 'ds-primary';
  const targetLabel = el('label', 'Send to'); targetLabel.append(targets);
  const synthesisLabel = el('label', 'Bring results together in'); synthesisLabel.append(synthesis);
  groupPanel.append(targetLabel, synthesisLabel, groupPrompt, groupRun, button('Close', 'Close group task', () => { groupPanel.hidden = true; groupButton.focus(); }));
  const viewport = el('div', null, 'ds-viewport'); viewport.setAttribute('aria-label', 'Terminal board'); viewport.tabIndex = 0;
  const board = el('div', null, 'ds-board');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.classList.add('ds-wires'); svg.setAttribute('aria-hidden', 'true');
  const defs = document.createElementNS(svg.namespaceURI, 'defs'); const marker = document.createElementNS(svg.namespaceURI, 'marker');
  for (const [k,v] of Object.entries({id:'ds-arrow',viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'7',markerHeight:'7',orient:'auto-start-reverse'})) marker.setAttribute(k,v);
  const arrow = document.createElementNS(svg.namespaceURI, 'path'); arrow.setAttribute('d','M 0 0 L 10 5 L 0 10 z'); marker.append(arrow); defs.append(marker); svg.append(defs); board.append(svg); viewport.append(board);
  const empty = el('div', null, 'ds-empty'); empty.append(el('span', '✦', 'ds-empty-mark'), el('h2', 'Give every model a seat.'), el('p', 'Add a terminal for Claude, Gemini, Grok, Codex, or your local model. Work separately, connect two sessions, or give the group a shared task.')); board.append(empty);
  const activity = el('aside', null, 'ds-activity'); activity.hidden = true;
  const linksList = el('div', null, 'ds-links-list'), receipts = el('div', null, 'ds-receipts');
  const channelHead=el('header',null,'ds-channel-head');
  const channelMove=button('⠿','Move board channel; arrow keys move, Home resets',null);channelMove.className='ds-channel-move';
  channelHead.append(channelMove,el('h2','Message Board'),button('Close','Close board channel',()=>setChannelOpen(false)));
  const channelAbout=el('p','The project board: what you and every terminal post, no link needed. @name also gives that terminal the message as a task. Messages over a link are marked Direct.','ds-channel-about');
  const channelEmpty=el('p','No messages yet. Mention a Workflow terminal to start.','ds-channel-empty');
  const channelHistory=el('div',null,'ds-channel-history');channelHistory.tabIndex=0;channelHistory.setAttribute('aria-label','Board conversation history');
  const connectionDetails=el('details',null,'ds-channel-connections');connectionDetails.append(el('summary','Connections and delivery details'),linksList,receipts);
  const channelComposer=el('div',null,'ds-channel-composer');
  const channelSuggestions=el('div',null,'ds-channel-suggestions');channelSuggestions.hidden=true;channelSuggestions.setAttribute('aria-label','Terminal mention suggestions');
  const channelPrompt=el('textarea');channelPrompt.rows=3;channelPrompt.placeholder='Post to the board, or @name a terminal…';channelPrompt.setAttribute('aria-label','Board channel message');
  const channelStatus=el('p','No @: posts to the board for every terminal. @name also sends it to that terminal as a task.','ds-channel-status');channelStatus.setAttribute('role','status');
  const channelSend=button('Send','Send board channel message',()=>sendChannel());channelSend.className='ds-primary';channelSend.disabled=true;
  channelComposer.append(channelSuggestions,channelPrompt,channelStatus,channelSend);activity.append(channelHead,channelAbout,channelEmpty,channelHistory,connectionDetails,channelComposer);
  const channelGrip=button('◣','Resize message board; arrow keys resize, Home resets',null);channelGrip.className='ds-channel-size';activity.append(channelGrip);
  channelPrompt.addEventListener('input',mentionSuggestions);
  channelPrompt.addEventListener('keydown',event=>{if(event.key==='ArrowDown'&&!channelSuggestions.hidden){event.preventDefault();channelSuggestions.querySelector('button:not(:disabled)')?.focus();}if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&!event.isComposing){event.preventDefault();sendChannel();}if(event.key==='Escape'&&!channelSuggestions.hidden){event.preventDefault();event.stopPropagation();channelSuggestions.hidden=true;}});
  moveChannel();
  const forwarding = el('input'); forwarding.type = 'checkbox'; forwarding.id = 'ds-forward';
  const forwardLabel = el('label', null, 'ds-forward-label'); forwardLabel.append(forwarding, el('span', 'Automatically forward completed replies on new links'));
  connectionDetails.insertBefore(forwardLabel, linksList);
  page.append(toolbar, feedback, groupPanel, viewport, activity); document.body.append(page);
  const companions = el('div',null,'ds-companions'); board.append(companions);
  const nodes = new Map(); let snapshot = {nodes:[],links:[],receipts:[],providers:[]}, pending = false, uncertain = false, reading = false, epoch = 0, source = null, readAgain = false, tiling = false, maximized = null;
  const characters = ['penguin','fox','wolf','robot','rabbit'];
  const defaultCharacter = {codex:'penguin',anthropic:'robot',antigravity:'fox',gemini:'fox',grok:'wolf',machx:'rabbit'};
  let handoffFlow=null;
  let channelPosition=null,channelPositionKey=null,channelSize=null,channelAttempt=null,channelSupported=false,channelWorkspace=null,channelFollow=true,channelRestoreScroll=null;
  const channelDrafts=new Map();
  const channelRows=new Map(),channelMentions=new Map();
  let skin = 'terminal', textLayout='terminal', displayName='You', appearanceWorkspace = null, openSessionNode=null;
  function appearanceKey(kind, node) { return 'dream.deepsleep.' + kind + ':' + JSON.stringify(node ? [node.data.workspace || snapshot.workspace || 'default',node.data.id] : snapshot.workspace || 'default'); }
  function readAppearance(key) { try { return localStorage.getItem(key); } catch { return null; } }
  function saveAppearance(key, value) { try { localStorage.setItem(key,value); } catch { message('Appearance changed for this view. Browser storage is unavailable, so it may reset after reload.'); } }
  function setSkin(value, persist = false) {
    skin = value === 'characters' ? 'characters' : 'terminal';
    page.dataset.skin = skin; characterToggle.setAttribute('aria-pressed',String(skin === 'characters'));
    if (persist) saveAppearance(appearanceKey('skin'),skin);
    for(const node of nodes.values())placeAvatar(node);
    // Floating characters never reserve terminal cells.
  }
  function raiseNode(node) {
    // Keep panes below decorative/flow overlays and the existing drawers.
    const ordered=[...nodes.values()].filter(n=>n!==node).sort((a,b)=>(Number(a.element.style.zIndex)||0)-(Number(b.element.style.zIndex)||0));
    ordered.push(node);ordered.forEach((n,i)=>n.element.style.zIndex=String(Math.min(190,i+1)));
  }
  function avatarBounds(node) {
    return {width:node.avatarAnchor.offsetWidth||84,height:node.avatarAnchor.offsetHeight||40,
      boardWidth:Math.max(viewport.clientWidth,board.clientWidth),boardHeight:Math.max(viewport.clientHeight,board.clientHeight)};
  }
  function setAvatarSize(node,value,persist=false) {
    node.avatarSize=['small','medium','large'].includes(value)?value:'medium';
    node.avatarSizeKey=appearanceKey('avatar-size',node);
    node.avatarSizeSelect.value=node.avatarSize;
    if(node.avatarAnchor)node.avatarAnchor.dataset.size=node.avatarSize;
    if(persist)saveAppearance(node.avatarSizeKey,node.avatarSize);
    if(node.avatarAnchor)placeAvatar(node);
  }
  function placeAvatar(node) {
    if(!node.avatar)return;
    if(node.avatarSizeKey!==appearanceKey('avatar-size',node))setAvatarSize(node,readAppearance(appearanceKey('avatar-size',node)));
    const key=appearanceKey('avatar-position',node);
    if(node.avatarPositionKey!==key) {
      node.avatarPositionKey=key;node.avatarPosition=null;
      try {const value=JSON.parse(readAppearance(key));if(value&&Number.isFinite(value.x)&&Number.isFinite(value.y)&&value.x>=0&&value.y>=0&&value.x<=1000000&&value.y<=1000000)node.avatarPosition={x:value.x,y:value.y};} catch {}
    }
    if(skin!=='characters')node.cancelAvatarDrag?.();
    const origin=board.getBoundingClientRect(),title=node.title.getBoundingClientRect(),head=node.head.getBoundingClientRect();
    const sx=origin.width?board.offsetWidth/origin.width:1,sy=origin.height?board.offsetHeight/origin.height:1;
    const titleRange=document.createRange();titleRange.selectNodeContents(node.title);
    // The header's free strip runs from the title's text to its first button (2026-10-10: the In pill sits in it,
    // and steps aside while a character, which has its own In and Out, is shown there).
    const controls=[...node.headEnd.children].find(element=>element.offsetWidth>0)?.getBoundingClientRect();
    const freeRight=controls?controls.left-8:title.right;
    const spareHeader=(freeRight-titleRange.getBoundingClientRect().right)*sx;
    node.avatarAnchor.hidden=skin!=='characters'||!!(maximized&&maximized!==node);
    node.avatar.hidden=node.avatarAnchor.hidden;
    const b=avatarBounds(node);
    const desired=node.avatarPosition||{x:(freeRight-origin.left)*sx-b.width-4,
      y:(head.top-origin.top)*sy+Math.min((head.height*sy-b.height)/2,head.height*sy-b.height)};
    // Automatic characters stay clear of title text, buttons and terminal rows.
    // At a tight board edge the existing header ports remain available instead.
    if(!node.avatarPosition&&(spareHeader<b.width+8||desired.y<0))node.avatarAnchor.hidden=node.avatar.hidden=true;
    node.element.classList.toggle('ds-character-shown',!node.avatarAnchor.hidden);
    node.avatarAnchor.classList.toggle('ds-avatar-auto',!node.avatarPosition);
    const x=Math.max(0,Math.min(desired.x,b.boardWidth-b.width)),y=Math.max(0,Math.min(desired.y,b.boardHeight-b.height));
    Object.assign(node.avatarAnchor.style,{left:x+'px',top:y+'px'});
    node.avatar.title=(node.data.title||node.data.provider)+' character — drag anywhere; arrow keys move, Home resets';
    wires();handoffFlow?.place();
  }
  function resetAvatar(node) {
    node.cancelAvatarDrag?.();node.avatarPosition=null;
    saveAppearance(appearanceKey('avatar-position',node),'');placeAvatar(node);
  }
  function setChannelOpen(value) {
    if(!value&&!activity.hidden){channelFollow=channelHistory.scrollHeight-channelHistory.scrollTop-channelHistory.clientHeight<32;channelRestoreScroll=channelHistory.scrollTop;}
    activity.hidden=!value;connectionsButton.setAttribute('aria-expanded',String(value));
    if(value){positionChannel();if(channelFollow)channelHistory.scrollTop=channelHistory.scrollHeight;else if(channelRestoreScroll!==null)channelHistory.scrollTop=channelRestoreScroll;channelRestoreScroll=null;channelPrompt.focus();}else connectionsButton.focus();
  }
  function positionChannel() {
    const key=appearanceKey('channel-position');
    if(channelPositionKey!==key){channelPositionKey=key;channelPosition=null;channelSize=null;try{const value=JSON.parse(readAppearance(key));if(Number.isFinite(value?.x)&&Number.isFinite(value?.y))channelPosition=value;}catch{}
      try{const value=JSON.parse(readAppearance(appearanceKey('channel-size')));if(Number.isFinite(value?.w)&&Number.isFinite(value?.h))channelSize=value;}catch{}}
    const width=Math.min(channelSize?Math.max(260,channelSize.w):440,Math.max(260,page.clientWidth-16));
    const x=Math.max(8,Math.min(channelPosition?.x??page.clientWidth-width-8,page.clientWidth-width-8));
    const fixed=channelHead.offsetHeight+channelAbout.offsetHeight+(channelEmpty.hidden?0:channelEmpty.offsetHeight)+channelComposer.offsetHeight+80;
    const fits=connectionDetails.open?fixed/.72:fixed+connectionDetails.offsetHeight,minimum=Math.max(420,fits);
    const tall=channelSize?Math.max(fits,channelSize.h):minimum;   // a chosen height, never below what the window needs
    const y=Math.max(viewport.offsetTop+8,Math.min(channelPosition?.y??viewport.offsetTop+8,page.clientHeight-tall-8));
    const room=page.clientHeight-y-8;
    activity.style.overflowY=room<tall?'auto':'hidden';
    Object.assign(activity.style,{left:x+'px',right:'auto',top:y+'px',width:width+'px',height:Math.max(180,channelSize?Math.min(room,tall):room)+'px',maxHeight:'none'});
  }
  function moveChannel() {
    let drag=null;
    const save=()=>saveAppearance(appearanceKey('channel-position'),JSON.stringify(channelPosition));
    channelMove.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();channelMove.focus();drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:parseFloat(activity.style.left),top:parseFloat(activity.style.top),previous:channelPosition};channelMove.setPointerCapture(event.pointerId);});
    channelMove.addEventListener('pointermove',event=>{if(!drag)return;channelPosition={x:drag.left+event.clientX-drag.x,y:drag.top+event.clientY-drag.y};positionChannel();});
    channelMove.addEventListener('pointerup',event=>{if(!drag)return;drag=null;if(channelMove.hasPointerCapture(event.pointerId))channelMove.releasePointerCapture(event.pointerId);save();});
    const cancel=()=>{if(!drag)return;channelPosition=drag.previous;drag=null;positionChannel();};
    channelMove.addEventListener('pointercancel',cancel);channelMove.addEventListener('lostpointercapture',cancel);
    channelMove.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&drag){event.preventDefault();event.stopPropagation();cancel();return;}
      if(event.key==='Home'){event.preventDefault();channelPosition=null;save();positionChannel();return;}
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();const step=event.shiftKey?40:10;
      channelPosition={x:parseFloat(activity.style.left)+(event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0),y:parseFloat(activity.style.top)+(event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0)};positionChannel();save();
    });
    // The corner grip: its left edge and its bottom edge follow the pointer; the right edge stays where it is.
    let sizing=null;
    const box=()=>({x:parseFloat(activity.style.left),y:parseFloat(activity.style.top),w:parseFloat(activity.style.width),h:parseFloat(activity.style.height)});
    const resize=(from,w,h)=>{w=Math.max(260,Math.min(w,from.x+from.w-8));channelSize={w,h};channelPosition={x:from.x+from.w-w,y:from.y};positionChannel();};
    const keep=()=>{const now=box();channelSize={w:now.w,h:now.h};save();saveAppearance(appearanceKey('channel-size'),JSON.stringify(channelSize));};
    channelGrip.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();channelGrip.focus();sizing={x:event.clientX,y:event.clientY,from:box(),position:channelPosition,size:channelSize};channelGrip.setPointerCapture(event.pointerId);});
    channelGrip.addEventListener('pointermove',event=>{if(sizing)resize(sizing.from,sizing.from.w-(event.clientX-sizing.x),sizing.from.h+event.clientY-sizing.y);});
    channelGrip.addEventListener('pointerup',event=>{if(!sizing)return;sizing=null;if(channelGrip.hasPointerCapture(event.pointerId))channelGrip.releasePointerCapture(event.pointerId);keep();});
    const cancelSize=()=>{if(!sizing)return;channelPosition=sizing.position;channelSize=sizing.size;sizing=null;positionChannel();};
    channelGrip.addEventListener('pointercancel',cancelSize);channelGrip.addEventListener('lostpointercapture',cancelSize);
    channelGrip.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&sizing){event.preventDefault();event.stopPropagation();cancelSize();return;}
      if(event.key==='Home'){event.preventDefault();const from=box();channelSize=null;channelPosition=channelPosition&&{x:from.x+from.w-Math.min(440,Math.max(260,page.clientWidth-16)),y:from.y};positionChannel();save();saveAppearance(appearanceKey('channel-size'),'null');return;}
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();const step=event.shiftKey?40:10,from=box();
      resize(from,from.w+(event.key==='ArrowLeft'?step:event.key==='ArrowRight'?-step:0),from.h+(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0));keep();
    });
  }
  function channelRecipients() {
    const selected=new Set();let broadcast=false;
    for(const match of channelPrompt.value.matchAll(/@("(?:\\.|[^"\\])*"|[^\s@]+)/g)) {
      if(match.index>0&&!/[\s([{]/.test(channelPrompt.value[match.index-1]))continue;
      let label;try{label=match[1][0]==='"'?JSON.parse(match[1]):match[1];}catch{return {error:'Choose a terminal from the @ suggestions.'};}
      if(label.toLowerCase()==='everyone') {broadcast=true;for(const n of nodes.values())if(automatic(n.data)&&linked(n.data)&&n.data.state==='ready')selected.add(n.data.id);continue;}
      const picked=channelMentions.get(match[0]);
      const candidates=picked?[nodes.get(picked)].filter(Boolean):[...nodes.values()].filter(n=>receiverLabel(n.data).toLowerCase()===label.toLowerCase());
      if(candidates.length!==1)return {error:candidates.length?'That name belongs to more than one terminal. Choose an exact terminal from @ suggestions.':'Unknown terminal '+match[0]+'. Choose a terminal from @ suggestions.'};
      const node=candidates[0];
      if(!automatic(node.data)||!linked(node.data))return {error:receiverLabel(node.data)+' is a native CLI: it takes no task from here. Post without @ (it reads the board), or type in its terminal.'};
      if(node.data.state!=='ready')return {error:receiverLabel(node.data)+' is not ready. Reconnect its terminal before sending.'};
      selected.add(node.data.id);
    }
    if(!selected.size)return {error:broadcast?'No ready Workflow terminals are available for @everyone.':'Mention @terminalname or @everyone to choose who receives this message.'};
    return {targets:[...selected],broadcast};
  }
  function syncChannelWorkspace(workspace) {
    if(channelWorkspace===workspace)return;
    if(channelWorkspace!==null)channelDrafts.set(channelWorkspace,{text:channelPrompt.value,mentions:[...channelMentions],attempt:channelAttempt,follow:activity.hidden?channelFollow:channelHistory.scrollHeight-channelHistory.scrollTop-channelHistory.clientHeight<32,scroll:activity.hidden?(channelRestoreScroll??channelHistory.scrollTop):channelHistory.scrollTop});
    channelWorkspace=workspace;const draft=channelDrafts.get(workspace);
    channelPrompt.value=draft?.text||'';channelMentions.clear();for(const [token,id] of draft?.mentions||[])channelMentions.set(token,id);
    channelAttempt=draft?.attempt||null;channelFollow=draft?.follow??true;channelRestoreScroll=draft?.scroll||0;
    channelSuggestions.hidden=true;channelStatus.textContent=draft?.text?'Draft restored for this workspace.':'No @: posts to the board for every terminal. @name also sends it to that terminal as a task.';
    channelHistory.replaceChildren();channelRows.clear();
  }
  function mentionSuggestions() {
    const tokens=new Set([...channelPrompt.value.matchAll(/@("(?:\\.|[^"\\])*"|[^\s@]+)/g)].filter(match=>match.index===0||/[\s([{]/.test(channelPrompt.value[match.index-1])).map(match=>match[0]));
    for(const token of channelMentions.keys())if(!tokens.has(token))channelMentions.delete(token);
    if(channelAttempt&&channelAttempt.prompt!==channelPrompt.value.trim())channelAttempt=null;
    const tail=channelPrompt.value.slice(0,channelPrompt.selectionStart).match(/@([^@\n]*)$/);
    channelSuggestions.replaceChildren();channelSuggestions.hidden=!tail;
    if(!tail || (tail.index>0&&!/[\s([{]/.test(channelPrompt.value[tail.index-1]))){channelSuggestions.hidden=true;return;}
    const query=tail[1].replace(/^"/,'').toLowerCase();
    const choices=[{label:'everyone',id:null,detail:'All ready Workflow terminals'},...[...nodes.values()].map(n=>({label:receiverLabel(n.data).toLowerCase()==='everyone'?receiverLabel(n.data)+' · '+n.data.id.slice(-6):receiverLabel(n.data),id:n.data.id,detail:automatic(n.data)?n.data.state:'Native CLI · reads the board',disabled:!automatic(n.data)||!linked(n.data)||n.data.state!=='ready'}))].filter(choice=>choice.label.toLowerCase().includes(query));
    if(!choices.length&&/\s/.test(query)){channelSuggestions.hidden=true;return;}
    for(const choice of choices) {
      const item=button('@'+choice.label+' · '+choice.detail,'Mention '+choice.label,()=>{
        const token='@'+(/^[\w.-]+$/.test(choice.label)?choice.label:JSON.stringify(choice.label));
        const start=channelPrompt.selectionStart-tail[0].length,end=channelPrompt.selectionStart;
        channelPrompt.setRangeText(token+' ',start,end,'end');if(choice.id)channelMentions.set(token,choice.id);
        channelSuggestions.hidden=true;channelPrompt.focus();channelStatus.textContent=choice.id?'Recipient: '+choice.label:'Broadcast to all ready Workflow terminals when you Send.';
      });
      item.disabled=!!choice.disabled;channelSuggestions.append(item);
    }
    if(!choices.length)channelSuggestions.append(el('p','No matching terminal.'));
  }
  // An @ at a word's start (an email address is not one): the same test channelRecipients makes.
  const mentions=text=>[...text.matchAll(/@("(?:\\.|[^"\\])*"|[^\s@]+)/g)].some(match=>match.index===0||/[\s([{]/.test(text[match.index-1]));
  async function sendChannel() {
    const prompt=channelPrompt.value.trim();if(!prompt){channelStatus.textContent='Write a message: no @ posts it to the board, @name also sends it to that terminal.';channelPrompt.focus();return;}
    if(!mentions(prompt)) {   // the project's open board (2026-10-10): every terminal can read it; no task is sent
      channelSend.disabled=true;
      if(await mutate({action:'board_post',text:prompt},'Posted to the board.')) {
        if(channelPrompt.value.trim()===prompt){channelPrompt.value='';channelMentions.clear();}
        channelAttempt=null;channelSuggestions.hidden=true;channelStatus.textContent='Posted to the board. Every terminal can read it.';
      } else channelStatus.textContent=feedback.textContent;
      channelSend.disabled=pending||uncertain||!channelSupported;return;
    }
    const recipients=channelAttempt?.prompt===prompt?{targets:channelAttempt.targets}:channelRecipients();if(recipients.error){channelStatus.textContent=recipients.error;return;}
    const signature=JSON.stringify([prompt,recipients.targets]);
    if(channelAttempt?.signature!==signature){const bytes=crypto.getRandomValues(new Uint8Array(16));channelAttempt={signature,prompt,targets:[...recipients.targets],id:'channel-'+Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('')};}
    channelStatus.textContent='Sending to '+recipients.targets.map(id=>nodes.has(id)?receiverLabel(nodes.get(id).data):id).join(', ')+'…';
    channelSend.disabled=true;
    if(await mutate({action:'channel_send',id:channelAttempt.id,targets:recipients.targets,prompt},'Channel message recorded. Check each receiver’s delivery state.')) {
      if(channelPrompt.value.trim()===prompt){channelPrompt.value='';channelMentions.clear();}
      channelAttempt=null;channelSuggestions.hidden=true;channelStatus.textContent='Message recorded. Delivery states appear above.';
    } else channelStatus.textContent=feedback.textContent;
    channelSend.disabled=pending||uncertain||!channelSupported;
  }
  function renderChannel() {
    const follow=activity.hidden?channelFollow:channelHistory.scrollHeight-channelHistory.scrollTop-channelHistory.clientHeight<32;
    const selected=window.getSelection(),selecting=selected&&!selected.isCollapsed&&channelHistory.contains(selected.anchorNode);
    const next=new Set();let position=channelHistory.firstChild;
    const append=(key,label,text,state,kind='')=>{
      next.add(key);let row=channelRows.get(key);
      if(!row){const element=el('article',null,'ds-channel-message'),heading=el('div',null,'ds-channel-author'),body=el('p'),status=el('span',null,'ds-channel-state');element.append(heading,body,status);row={element,heading,body,status};channelRows.set(key,row);}
      row.element.classList.toggle('ds-channel-request',kind==='request');row.element.classList.toggle('ds-channel-board',kind==='board');row.element.classList.toggle('ds-channel-direct',kind==='direct');row.element.dataset.state=state||'recorded';
      if(row.heading.textContent!==label)row.heading.textContent=label;const body=String(text||'').slice(0,4000);if(row.body.textContent!==body)row.body.textContent=body;row.body.hidden=!text;if(row.status.textContent!==(state||'recorded'))row.status.textContent=state||'recorded';
      if(row.element!==position)channelHistory.insertBefore(row.element,position);position=row.element.nextSibling;
    };
    const label=id=>nodes.has(id)?receiverLabel(nodes.get(id).data):id||'Unknown terminal';
    const author=post=>post.author==='owner'?(displayName||'You'):post.node&&nodes.has(post.node)?label(post.node):'Session '+String(post.author||'').slice(-6);
    const entries=[...(snapshot.channel||[]).map(item=>({at:item.at||0,item})),...(snapshot.board?.posts||[]).map(post=>({at:post.at||0,post}))];
    entries.sort((a,b)=>a.at-b.at);   // stable: equal times keep the server's own order
    for(const {item,post} of entries) {
      if(post){append('board:'+post.id,'Board · '+author(post)+(post.kind==='help'?' · asks for help':'')+(post.reply_to?' · reply to #'+post.reply_to:''),post.text,'#'+post.id,'board');continue;}
      const owner=item.source==='owner',direct=item.direct===true||!owner;
      const route=label(item.source)+' → '+(item.targets||[]).map(id=>label(id)).join(', ');
      append(item.id+':request',direct?'Direct · '+route:'You → '+(item.targets||[]).map(id=>'@'+label(id)).join(', '),item.text,item.state,direct?'direct':'request');
      for(const receipt of item.receipts||[])append(item.id+':'+receipt.id,(direct?'Direct · ':'')+label(receipt.target),receipt.error||receipt.result||(receipt.state==='prepared'?'Prepared handoff. Review it in the receiver’s Tools.':''),receipt.state+(receipt.result_truncated?' · response shortened':''),direct?'direct':'');
      if(item.trimmed)append(item.id+':trimmed','History note','This entry was shortened for display. Review the terminal for the complete record.','shortened');
    }
    if(snapshot.board?.error)append('board:error','Board · unavailable',snapshot.board.error,'error','board');
    for(const [key,row] of channelRows)if(!next.has(key)){row.element.remove();channelRows.delete(key);}
    channelEmpty.textContent=channelSupported?'No messages yet. Post to the board, or @mention a terminal.':'Board messaging is unavailable. Refresh after updating Dream; terminal handoffs remain available.';
    channelEmpty.hidden=next.size>0;
    channelFollow=follow;
    if(!activity.hidden){if(follow&&!selecting)channelHistory.scrollTop=channelHistory.scrollHeight;else if(channelRestoreScroll!==null)channelHistory.scrollTop=channelRestoreScroll;channelRestoreScroll=null;}
    channelSend.disabled=pending||uncertain||!channelSupported;
  }
  function revealAvatar(node) {
    setSkin('characters',true);
    node.avatarPosition={x:node.layout.x+20,y:node.layout.y+45};
    saveAppearance(appearanceKey('avatar-position',node),JSON.stringify(node.avatarPosition));
    placeAvatar(node);node.avatar.scrollIntoView({block:'nearest',inline:'nearest'});node.avatar.focus();
  }
  async function renameTerminal(node) {
    const title=node.nameInput.value.trim();
    if(!title || title.length>100){node.nameStatus.textContent='Enter a terminal name with 1–100 characters.';node.nameInput.focus();return;}
    if(node.nameSaving)return;
    node.nameSaving=true;node.nameSave.disabled=true;node.nameStatus.textContent='Saving terminal name…';
    const saved=await mutate({action:'rename',node:node.data.id,title},'Terminal name saved.');
    node.nameSaving=false;node.nameSave.disabled=pending||uncertain;
    node.nameStatus.textContent=saved?'Terminal name saved.':feedback.textContent;
    if(saved && node.nameInput.value.trim()===title){node.nameDirty=false;node.nameInput.value=node.data.title;}
  }
  function updateTerminalName(node,item) {
    const previous=node.data.title||node.data.provider,next=item.title||item.provider;
    if(previous!==next) {
      node.element.setAttribute('aria-label',next);
      const templates=['Move {}; use arrow keys','Sign in to {}','Collapse {} to header','Expand {} terminal','Expand {} terminal; new output','Close {}','Maximize {}','Restore {}','Session details for {}','Link to {}','Link from {}','Terminal {}','Follow latest output in {}','Reconnect {}','Restart {}','Prompt {}','Send to {}','Copy draft for {}','Agents in {}','Resize {}; use arrow keys','Move {} character; arrow keys move, Home resets','Link to {} character','Link from {} character','Recorded activity for {}','Terminal tools for {}','Handoff receiver for {}','Send selected text from {}','Prepared handoffs for {}','Files in {}','File explorer for {}','Voice controls in {}','Character for {}','Character size for {}','Reset character position for {}','Place character for {}','Model in {}','Effort in {}','Permissions in {}','Remote control in {}','Changes in {}'];
      const labels=new Map(templates.map(template=>[template.replace('{}',previous),template.replace('{}',next)]));
      for(const container of [node.element,node.avatarAnchor,node.sessionDrawer?.element].filter(Boolean)) {
        for(const control of [container,...container.querySelectorAll('[aria-label],[title]')]) {
          for(const attribute of ['aria-label','title']) {
            const value=control.getAttribute(attribute);
            if(labels.has(value))control.setAttribute(attribute,labels.get(value));
          }
        }
      }
      const label=node.sessionDrawer?.element.querySelector('.ds-session-title');if(label)label.textContent=next;
    }
    if(!node.nameDirty)node.nameInput.value=next;
  }
  function movableAvatar(node) {
    const handle=node.avatar;let gesture=null;
    const persist=()=>saveAppearance(appearanceKey('avatar-position',node),JSON.stringify(node.avatarPosition));
    const cancel=()=>{if(!gesture)return;const old=gesture;gesture=null;node.avatarPosition=old.position;if(handle.hasPointerCapture(old.pointer))handle.releasePointerCapture(old.pointer);handle.classList.remove('ds-avatar-dragging');placeAvatar(node);};
    node.cancelAvatarDrag=cancel;
    handle.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;event.preventDefault();event.stopPropagation();handle.focus();
      gesture={pointer:event.pointerId,x:event.clientX+viewport.scrollLeft,y:event.clientY+viewport.scrollTop,
        left:parseFloat(node.avatarAnchor.style.left)||0,top:parseFloat(node.avatarAnchor.style.top)||0,position:node.avatarPosition?{...node.avatarPosition}:null};
      handle.setPointerCapture(event.pointerId);handle.classList.add('ds-avatar-dragging');
    });
    handle.addEventListener('pointermove',event=>{
      if(!gesture||gesture.pointer!==event.pointerId)return;
      const b=avatarBounds(node);
      node.avatarPosition={x:Math.max(0,Math.min(b.boardWidth-b.width,gesture.left+event.clientX+viewport.scrollLeft-gesture.x)),
        y:Math.max(0,Math.min(b.boardHeight-b.height,gesture.top+event.clientY+viewport.scrollTop-gesture.y))};placeAvatar(node);
    });
    handle.addEventListener('pointerup',event=>{
      if(!gesture||gesture.pointer!==event.pointerId)return;gesture=null;handle.classList.remove('ds-avatar-dragging');
      if(handle.hasPointerCapture(event.pointerId))handle.releasePointerCapture(event.pointerId);
      if(node.avatarPosition)persist();
    });
    handle.addEventListener('pointercancel',cancel);handle.addEventListener('lostpointercapture',cancel);
    handle.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&gesture){event.preventDefault();event.stopPropagation();cancel();return;}
      if(event.key==='Home'){event.preventDefault();event.stopPropagation();resetAvatar(node);return;}
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();event.stopPropagation();const step=event.shiftKey?40:10,b=avatarBounds(node);
      node.avatarPosition={x:Math.max(0,Math.min(b.boardWidth-b.width,(parseFloat(node.avatarAnchor.style.left)||0)+(event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0))),
        y:Math.max(0,Math.min(b.boardHeight-b.height,(parseFloat(node.avatarAnchor.style.top)||0)+(event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0)))};
      placeAvatar(node);persist();
    });
  }
  function setDisplayName(value,persist=false) {
    displayName=(typeof value==='string'?value.trim().slice(0,60).trim():'')||'You';
    for(const node of nodes.values()) {
      if(node.displayNameInput)node.displayNameInput.value=displayName;
      node.activityView?.setUserLabel(displayName);
    }
    if(persist)saveAppearance(appearanceKey('display-name'),displayName);
  }
  function setTextLayout(value,persist=false) {
    textLayout=['conversation','activity'].includes(value)?value:'terminal'; textLayoutSelect.value=textLayout;
    for(const node of nodes.values()) updateTextLayout(node);
    if(persist)saveAppearance(appearanceKey('text-layout'),textLayout);
  }
  function updateTextLayout(node) {
    if(!node.content)return;
    const structured=textLayout!=='terminal', showActivity=structured || !!node.activityOpen;
    const hasEvents=showActivity && node.activityHasContent===true;
    // Capture scrollback before DOM sizing changes can move xterm's viewport.
    if(node.terminal && !native(node.data) && !node.followOutput && node.historyRow==null) node.historyRow=node.terminal.buffer.active.viewportY;
    node.element.dataset.textLayout=textLayout;
    node.activityToggle.textContent=node.activityOpen?'Hide activity':'Activity';
    node.activityToggle.hidden=structured; node.activityToggle.setAttribute('aria-expanded',String(showActivity));
    node.terminalToggle.hidden=!hasEvents;
    const terminalVisible=!hasEvents || node.activityAvailable!==true || node.terminalOpen!==false;
    node.terminalHost.hidden=!terminalVisible;
    node.terminalToggle.textContent=terminalVisible?'Hide terminal':'Show terminal';
    node.terminalToggle.disabled=node.activityAvailable!==true;
    node.terminalToggle.setAttribute('aria-expanded',String(terminalVisible));
    node.content.classList.toggle('ds-with-events',hasEvents);
    node.content.classList.toggle('ds-events-empty',showActivity && !hasEvents);
    node.content.classList.toggle('ds-events-unavailable',node.activityAvailable===false);
    node.activityView?.setLayout(textLayout);
    node.activityView?.setVisible(showActivity && live() && !node.collapsed);
    node.activityView?.setActive(((!node.collapsed && showActivity) || !!node.sessionOpen) && live());
    if(node.activityFallback)node.activityFallback.hidden=!showActivity;
    if(node.element.isConnected)layout(node,node.layout);
    else fit(node);
  }
  function setCharacter(node, choice, persist = false) {
    const selected = characters.includes(choice) ? choice : defaultCharacter[node.data.provider] || 'rabbit';
    node.character = selected; node.characterSelect.value = selected; node.element.dataset.character = selected;
    node.appearanceKey = appearanceKey('avatar',node);
    node.avatarFallback.hidden = true; node.avatarImage.hidden = false;
    node.avatarImage.alt = selected[0].toUpperCase() + selected.slice(1) + ' character';
    node.avatarImage.src = '/assets/avatars/' + selected + '.png';
    if (persist) saveAppearance(appearanceKey('avatar',node),selected);
  }
  const live = () => root.dataset.dreamView === 'deepsleep';
  handoffFlow=window.DreamHandoffFlow?.create({board,viewport,isVisible:()=>live()&&!maximized,getNodes:()=>nodes,anchor:(node,out)=>cordEnd(node,out),path:(a,b)=>cordPath(a,b)});
  function message(text, bad = false) { feedback.textContent = text; feedback.dataset.error = String(bad); }
  async function api(payload) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('/api/deepsleep', {method: payload ? 'POST' : 'GET', headers: {'X-Dream-Token': TOKEN, ...(payload ? {'Content-Type':'application/json'} : {})}, cache: 'no-store', signal: controller.signal, ...(payload ? {body:JSON.stringify(payload)} : {})});
      const body = await response.json();
      if (!response.ok || body.ok === false) { const error = new Error(typeof body.error === 'string' ? body.error : 'The workspace request was refused.'); error.known = true; throw error; }
      return body.result || body;
    } catch (error) { if (error.name === 'AbortError') throw new Error('The request timed out. Refresh to check its result before trying again.'); throw error; }
    finally { clearTimeout(timer); }
  }
  function busy(value) { pending = value; const locked = value || tiling; tileButton.disabled = locked || uncertain || nodes.size < 2; add.disabled = locked || uncertain || !provider.value; groupRun.disabled = locked || uncertain; for (const n of nodes.values()) { n.close.disabled = locked || uncertain; if(n.nameSave)n.nameSave.disabled=locked||uncertain||!!n.nameSaving; } channelSend.disabled=locked||uncertain||!channelSupported; }
  async function mutate(payload, success) {
    if (pending || uncertain) { message(uncertain ? 'Refresh to check the previous request before making another change.' : 'Wait for the current workspace change to finish.', true); return false; }
    busy(true); epoch++;
    try { const data = await api(payload); if (Array.isArray(data.nodes)) reconcile(data); else { const refreshed = await refresh(); if (refreshed === false) { message('Change accepted, but the board could not refresh. Use Refresh to see its result.', true); return true; } } if (success) message(success); return true; }
    catch (error) { if (!error.known) uncertain = true; message(error.message || 'Workspace request failed. Refresh to check the current state.', true); return false; }
    finally { busy(false); }
  }
  async function refresh() {
    if (reading) { readAgain = true; return; }
    reading = true; const readEpoch = epoch; refreshButton.disabled = true;
    try { const data = await api(); if (!Array.isArray(data.nodes) || !Array.isArray(data.links)) throw new Error('The workspace returned an unreadable state.'); if (readEpoch === epoch) { reconcile(data); uncertain = false; busy(pending); } return true; }
    catch (error) { handoffFlow?.reset();channelStatus.textContent='Channel could not refresh. Displayed messages may be stale. Use Refresh to retry.';message(error.message || 'The workspace could not load. Use Refresh to try again.', true); return false; }
    finally { reading = false; refreshButton.disabled = false; if (readAgain) { readAgain = false; refresh(); } }
  }
  function receiverLabel(data) {
    const title=data.title||data.provider||data.id;
    const peers=[...nodes.values()].map(node=>node.data).filter(item=>(item.title||item.provider||item.id)===title);
    if(peers.length<2)return title;
    let length=6;
    while(peers.some(item=>item.id!==data.id&&item.id.slice(-length)===data.id.slice(-length)))length+=4;
    return title+' · '+data.id.slice(-length);
  }
  function replaceOptions(control, values, selectedValue) {
    const selected = control.multiple ? new Set([...control.selectedOptions].map(o => o.value)) : null;
    control.replaceChildren();
    for (const [value,label] of values) { const option = el('option', label); option.value = value; if (selected) option.selected = selected.has(value); control.append(option); }
    if (!selected && values.some(([v]) => v === selectedValue)) control.value = selectedValue;
  }
  function models() {
    const choice = snapshot.providers.find(p => p.key === provider.value);
    replaceOptions(model, [['','Provider default'], ...(choice?.models || []).map(m => typeof m === 'string' ? [m,m] : [m.id || m.value || m.key, m.label || m.name || m.id])], model.value);
  }
  function profiles(reset = false) { const local = provider.value === 'machx'; profile.disabled = local; if (reset || local) profile.value = local ? 'dream' : 'native'; }
  provider.onchange = () => { models(); profiles(true); };
  async function createNode() {
    const title = terminalName.value.trim();
    if (await mutate({action:'create',provider:provider.value,model:model.value,profile:profile.value,...(title && {title})}, 'Terminal created. Use Sign in if the provider asks for authentication.')) {
      if (terminalName.value.trim() === title) terminalName.value = '';
      const last = [...nodes.values()].at(-1); last?.element.scrollIntoView({block:'nearest', inline:'nearest'}); last?.terminal?.focus();
    }
  }
  async function runGroup() {
    const chosen = [...targets.selectedOptions].map(o => o.value), prompt = groupPrompt.value.trim();
    if (!chosen.length || !synthesis.value || !prompt) { message('Choose research terminals, a synthesis terminal, and a task.', true); return; }
    if (chosen.includes(synthesis.value)) { message('Choose a separate synthesis terminal to collect the research.', true); return; }
    if (await mutate({action:'group',targets:chosen,synthesis_target:synthesis.value,prompt}, 'Group task sent. Delivery activity shows each handoff.')) { if (groupPrompt.value.trim() === prompt) groupPrompt.value = ''; activity.hidden = false; }
  }
  const historyLine = (node,fallback=0) => node.historyMarker ? (node.historyMarker.isDisposed?0:node.historyMarker.line) : (node.historyRow??fallback);
  function captureHistory(node) {
    const buffer=node.terminal.buffer.active;
    node.historyRow=buffer.viewportY;
    node.historyMarker?.dispose();node.historyMarker=null;
    if(buffer.type==='normal')node.historyMarker=node.terminal.registerMarker(buffer.viewportY-buffer.baseY-buffer.cursorY);
  }
  function fit(node) {
    if (!node.fit || !live() || !node.element.isConnected) return;
    if(node.fitPending || node.writing>0){node.fitWanted=true;return;}
    if(node.fitFrame)return;
    node.fitFrame = requestAnimationFrame(() => {
      node.fitFrame = null;
      if (!live() || node.collapsed || !node.element.isConnected || !node.terminalHost.clientWidth || !node.terminalHost.clientHeight) return;
      if(node.writing>0){node.fitWanted=true;return;}
      node.fitWanted=false;node.fitting = true;
      const terminal=node.terminal, buffer=terminal.buffer.active;
      const historyRow=node.historyRow ?? buffer.viewportY;
      // xterm can shift the cursor below its tracked line when wrapping rows
      // below a prompt. Preserve that physical line before Dream redraws it.
      const marker=!native(node.data) && buffer.type==='normal' ? terminal.registerMarker(0) : null;
      const cursorX=buffer.cursorX;
      const finish=()=>{
        try {
          if(node.followOutput)terminal.scrollToBottom();
          else if(!native(node.data)) {
            terminal.scrollToLine(historyLine(node,historyRow));
            requestAnimationFrame(()=>{if(node.element.isConnected&&!node.followOutput){node.fitting=true;terminal.scrollToLine(historyLine(node,historyRow));node.fitting=false;}});
          }
          const size=`${terminal.cols}:${terminal.rows}`;
          if(node.socket?.readyState===WebSocket.OPEN&&node.transport==='ready'&&node.lastSize!==size) {
            node.socket.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows}));node.lastSize=size;
          }
        } finally {marker?.dispose();node.fitPending=false;node.fitting=false;const queued=node.fitOutput||[];node.fitOutput=[];for(const text of queued)output(node,text);if(node.fitWanted)fit(node);}
      };
      const resize=()=>{
        try {
          node.fit.fit();
          const row=marker && !marker.isDisposed ? marker.line-terminal.buffer.active.baseY : -1;
          if(row>=0 && row<terminal.rows && row!==terminal.buffer.active.cursorY) {
            node.fitPending=true;
            terminal.write(`\x1b[${row+1};${Math.min(cursorX,terminal.cols-1)+1}H`,finish);
          } else finish();
        } catch(error) {marker?.dispose();node.fitPending=false;node.fitting=false;throw error;}
      };
      const proposed=node.fit.proposeDimensions();
      const changing=proposed && (proposed.cols!==terminal.cols || proposed.rows!==terminal.rows);
      const promptRow=node.promptMarker && !node.promptMarker.isDisposed && buffer.type==='normal' ? node.promptMarker.line-buffer.baseY : -1;
      if(changing && inputReady(node) && promptRow>=0 && promptRow<=buffer.cursorY && promptRow<terminal.rows) {
        // Dream reports only the active, unsent prompt region. Remove its
        // transient paint before reflow; PTY resize redraws the retained draft.
        node.fitPending=true;
        terminal.write(`\x1b[${promptRow+1};1H\x1b[J\x1b[${buffer.cursorY+1};${cursorX+1}H`,resize);
      } else resize();

    });
  }
  function output(node, text) {
    // Until structured approval state exists, any new PTY output must remain
    // visible: it may contain an interactive permission prompt.
    if(text && node.collapsed && !node.collapsedUnread){node.collapsedUnread=true;node.collapsedNotice.hidden=false;node.collapseButton.setAttribute('aria-label','Expand '+(node.data.title||node.data.provider)+' terminal; new output');node.collapseButton.title='New output received. Expand to review input and approval requests.';}
    if(text && !node.collapsed && node.terminalHost.hidden){node.terminalOpen=true;updateTextLayout(node);node.note.textContent='Terminal reopened for new output. Review input and approval requests here.';}
    if (native(node.data)) { node.terminal.write(text); return; }
    if(node.fitPending){(node.fitOutput ||= []).push(text);return;}
    node.writing++;
    node.terminal.write(text, () => {
      node.writing--;
      if (!node.element.isConnected) return;
      if (node.followOutput) node.terminal.scrollToBottom();
      else if(node.historyRow!=null&&!node.scrollIntent&&!node.pointerScrolling)node.terminal.scrollToLine(historyLine(node));
      node.latest.hidden = node.followOutput;
      if(!node.writing&&node.fitWanted)fit(node);
    });
  }
  function followOutput(node) {
    node.followOutput=true;node.writing=0;
    const atBottom=()=>node.terminal.buffer.active.viewportY>=node.terminal.buffer.active.baseY;
    const capture=()=>{node.followOutput=atBottom()&&!node.terminal.hasSelection();if(node.followOutput){node.historyRow=null;node.historyMarker?.dispose();node.historyMarker=null;}else captureHistory(node);node.latest.hidden=node.followOutput;};
    let settleTimer=null;
    const settle=()=>{clearTimeout(settleTimer);settleTimer=setTimeout(()=>{if(!node.element.isConnected)return;if(!node.pointerScrolling){if(node.writing||node.fitting){settle();return;}capture();node.scrollIntent=false;}},250);};
    node.terminal.onScroll(()=>{
      if(node.fitting || node.writing)return;
      if(node.scrollIntent || node.pointerScrolling){capture();settle();}
    });
    const scrollIntent=()=>{node.followOutput=false;captureHistory(node);node.scrollIntent=true;node.latest.hidden=false;settle();};
    node.terminalHost.addEventListener('wheel',scrollIntent,{passive:true,capture:true});
    node.terminalHost.addEventListener('touchmove',scrollIntent,{passive:true,capture:true});
    node.terminalHost.addEventListener('pointerdown',event=>{
      const viewport=event.target.closest('.xterm-viewport');
      const gutter=viewport && event.clientX>=viewport.getBoundingClientRect().left+viewport.clientLeft+viewport.clientWidth;
      if(gutter || event.target.closest('.scrollbar')){node.pointerScrolling=true;scrollIntent();}
    },{capture:true});
    // xterm clears selection during resize; that is not a new reading position.
    const selection=node.terminal.onSelectionChange(()=>{if(node.fitting||node.fitPending)return;if(node.terminal.hasSelection()){node.followOutput=false;captureHistory(node);node.latest.hidden=false;}else capture();});
    const pointerFinish=()=>{if(!node.pointerScrolling)return;node.pointerScrolling=false;settle();};
    window.addEventListener('pointerup',pointerFinish);window.addEventListener('pointercancel',pointerFinish);
    node.terminalHost.addEventListener('keydown',event=>{if(event.shiftKey&&['PageUp','PageDown','Home','End'].includes(event.key))scrollIntent();},{capture:true});
    node.disposeScrollTracking=()=>{selection.dispose();clearTimeout(settleTimer);node.historyMarker?.dispose();window.removeEventListener('pointerup',pointerFinish);window.removeEventListener('pointercancel',pointerFinish);};
  }
  const blockedInputNote = 'Terminal is not ready; input was not sent. Reconnect or restart the process.';
  const inputReady = node => node.socket?.readyState === WebSocket.OPEN && node.transport === 'ready' && ['starting','ready'].includes(node.data.state);
  function connect(node) {
    if (node.socket && [WebSocket.OPEN,WebSocket.CONNECTING].includes(node.socket.readyState)) return;
    if (!node.terminal) return;
    node.processState = null; node.transport = 'connecting'; node.lastSize = null; updateConnection(node); node.reconnect.disabled = true;
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/deepsleep/${encodeURIComponent(node.data.id)}`); node.socket = socket;
    socket.onopen = () => { if (node.socket !== socket) return; socket.send(JSON.stringify({token:TOKEN})); node.transport = 'authorizing'; updateConnection(node); };
    socket.onmessage = event => {
      if (node.socket !== socket) return;
      let data; try { data = JSON.parse(event.data); } catch { node.note.textContent = 'Unreadable terminal event.'; return; }
      if (data.type === 'output' && typeof data.data === 'string') output(node, data.data);
      else if (data.type === 'state' && typeof data.state === 'string') { node.transport = 'ready'; node.processState = data.state; node.data.state = data.state; updateConnection(node); fit(node); }
      else if (data.type === 'error') { node.note.textContent = typeof data.message === 'string' ? data.message : 'Terminal connection error.'; }
    };
    socket.onerror = () => { if (node.socket !== socket) return; node.note.textContent = 'Terminal connection failed. Reconnect when ready; input is not replayed.'; };
    socket.onclose = () => { if (node.socket !== socket) return; node.transport = 'disconnected'; updateConnection(node); };
  }
  function updateConnection(node) {
    const state = node.data.state || 'unknown', stopped = ['failed','exited'].includes(state);
    if(stopped){node.promptMarker?.dispose();node.promptMarker=null;}
    node.state.textContent = native(node.data) && state === 'ready' ? 'Running' : state;
    node.state.title = 'Process state. Account access is checked separately.';
    node.element.dataset.state = state;
    const transport = {connecting:'Connecting…',authorizing:'Checking access…',ready:'Transport ready',disconnected:'Disconnected'}[node.transport] || 'Not connected';
    node.connection.textContent = stopped ? `Process ${state} · ${transport.toLowerCase()}` : transport;
    node.connection.title = 'Terminal transport only; not provider sign-in status.';
    node.status.hidden = !native(node.data) && node.transport === 'ready' && !stopped;
    node.reconnect.disabled = ['connecting','authorizing','ready'].includes(node.transport);
    node.reconnect.hidden = node.transport === 'ready';
    node.restart.hidden = !stopped;
    if (inputReady(node) && node.inputBlockedNote) {
      if (node.note.textContent === blockedInputNote) node.note.textContent = '';
      node.inputBlockedNote = false;
    }
  }
  function setTools(node, open) {
    node.element.classList.toggle('ds-compact', !open);
    node.drawer.hidden = !open;
    node.toolsButton.setAttribute('aria-expanded', String(open));
    node.toolsButton.textContent = open ? 'Tools ▴' : 'Tools ▾';
    if (!open) abortDictation(node);
    layout(node, node.layout);
  }
  function setCollapsed(node,value) {
    if(pending || uncertain || tiling || node.gestureActive || !!node.collapsed===value)return;
    if(value && node.maximized)maximize(node);
    if(!value && node.collapsedUnread)node.terminalOpen=true;
    node.collapsed=value;node.element.classList.toggle('ds-collapsed',value);
    node.collapsedUnread=false;node.collapsedNotice.hidden=true;
    node.collapseButton.textContent=value?'▾':'▴';
    node.collapseButton.setAttribute('aria-expanded',String(!value));
    node.collapseButton.setAttribute('aria-label',(value?'Expand ':'Collapse ')+(node.data.title||node.data.provider)+(value?' terminal':' to header'));
    node.collapseButton.title=node.collapseButton.getAttribute('aria-label');
    if(value)abortDictation(node);
    updateTextLayout(node);node.collapseButton.focus();
  }
  function maximize(node) {
    if (tiling || pending || uncertain) return;
    if(node.collapsed)setCollapsed(node,false);
    if (maximized && maximized !== node) maximize(maximized);
    if (maximized === node) {
      maximized = null; node.maximized = false; page.classList.remove('ds-has-maximized'); node.element.classList.remove('ds-maximized');
      layout(node, node.restoreLayout); node.maximizeButton.textContent = '⛶'; node.maximizeButton.setAttribute('aria-label', 'Maximize ' + node.data.title);
    } else {
      node.restoreLayout = {...node.layout}; node.maximized = true; maximized = node;
      page.classList.add('ds-has-maximized'); node.element.classList.add('ds-maximized');
      node.maximizeButton.textContent = '⤢'; node.maximizeButton.setAttribute('aria-label', 'Restore ' + node.data.title);
      viewport.scrollTo(0,0); layout(node,{x:6,y:6,width:viewport.clientWidth-12,height:viewport.clientHeight-12});
    }
    node.maximizeButton.title = node.maximizeButton.getAttribute('aria-label'); node.terminal?.focus();
  }
  async function tileNodes() {
    if (tiling || pending || uncertain || nodes.size < 2) return;
    if (maximized) maximize(maximized);
    const list = [...nodes.values()];
    if (list.some(node => node.editing)) { message('Finish moving the current terminal before tiling.', true); return; }
    tiling = true; busy(false); activity.hidden = true; groupPanel.hidden = true;
    const gap = 6, columns = Math.min(list.length, Math.max(1, Math.min(3, Math.floor((viewport.clientWidth-gap)/366))));
    const rows = Math.ceil(list.length / columns);
    const width = Math.floor((viewport.clientWidth-gap*(columns+1))/columns), height = Math.floor((viewport.clientHeight-gap*(rows+1))/rows);
    for (const node of list) { node.editing = true; setTools(node,false); }
    let completed=0, rowY=gap, rowHeight=0;
    try {
      for (let i=0; i<list.length; i++) {
        const node=list[i];
        if(i>0&&i%columns===0){rowY+=rowHeight+gap;rowHeight=0;}
        layout(node,{x:gap+(i%columns)*(width+gap),y:rowY,width,height:Math.max(300,height)});
        rowHeight=Math.max(rowHeight,node.layout.height);
        if (!await mutate({action:'layout',node:node.data.id,...node.layout})) break;
        completed++;
      }
      if (completed===list.length) message('Terminals tiled. Drag or resize any pane to adjust it.');
    } finally {
      for (const node of list) { node.editing=false; const saved=snapshot.nodes.find(item=>item.id===node.data.id); if(saved)layout(node,saved.layout); }
      tiling=false; busy(false); viewport.scrollTo(0,0);
    }
  }
  function layout(node, values) {
    const fallback = {x:40,y:40,width:600,height:470};
    const numbers = {};
    for (const key of ['x','y','width','height']) numbers[key] = Number.isFinite(values?.[key]) ? values[key] : fallback[key];
    numbers.x = Math.max(0, numbers.x); numbers.y = Math.max(0, numbers.y); numbers.width = Math.max(340, numbers.width); numbers.height = Math.max(300, numbers.height);
    // Keep the controls reachable when narrow cards wrap their footer rows.
    node.element.style.width = numbers.width + 'px';
    const controlsHeight = [...node.element.children].filter(child => !child.classList.contains('ds-content') && !child.classList.contains('ds-resize') && !child.classList.contains('ds-avatar-slot')).reduce((sum, child) => sum + child.getBoundingClientRect().height, 0);
    const statusHeight=node.content?.querySelector('.ds-event-panel:not([hidden]) .ds-event-status')?.getBoundingClientRect().height||0;
    const contentMinimum=node.content?.classList.contains('ds-with-events') ? Math.max(260,190+statusHeight+(node.terminalToggle.hidden?0:node.terminalToggle.getBoundingClientRect().height)) : Math.max(82,78+statusHeight);
    if(!node.collapsed)numbers.height = Math.max(numbers.height, Math.ceil(controlsHeight + contentMinimum));
    node.layout = numbers;
    const shownWidth=node.collapsed?Math.min(numbers.width,Math.max(340,Math.min(420,viewport.clientWidth-12))):numbers.width;
    Object.assign(node.element.style, {left:numbers.x+'px',top:numbers.y+'px',width:shownWidth+'px',height:(node.collapsed?44:numbers.height)+'px'});
    dimensions(); wires(); fit(node); node.sessionDrawer?.place();
  }
  function dimensions() { positionChannel(); let w = viewport.clientWidth, h = viewport.clientHeight; for (const n of maximized ? [maximized] : nodes.values()) { w = Math.max(w,n.layout.x+n.element.offsetWidth+6); h = Math.max(h,n.layout.y+n.element.offsetHeight+6); } board.style.width = w+'px'; board.style.height = h+'px'; for(const node of nodes.values())placeAvatar(node);handoffFlow?.place(); }
  async function saveLayout(node, previous) {
    node.queuedLayout = {desired:{...node.layout}, previous};
    node.editing = true;
    if(node.savingLayout)return;
    node.savingLayout=true;
    try {
      while(node.queuedLayout){
        while(pending&&!uncertain)await new Promise(resolve=>setTimeout(resolve,20));
        const queued=node.queuedLayout;node.queuedLayout=null;
        const ok=await mutate({action:'layout',node:node.data.id,...queued.desired});
        if(!ok){node.queuedLayout=null;if(!node.gestureActive)layout(node,queued.previous);break;}
        if(!node.queuedLayout&&!node.gestureActive){const saved=snapshot.nodes.find(n=>n.id===node.data.id);if(saved?.layout)layout(node,saved.layout);}
      }
    } finally {node.editing=!!node.gestureActive;node.savingLayout=false;}
  }
  function movable(node, handle, resize) {
    let gesture = null;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || uncertain || tiling || node.maximized) return;
      event.preventDefault(); raiseNode(node); node.editing = true; node.gestureActive = true;
      gesture = {x:event.clientX,y:event.clientY,initial:{...node.layout},pointer:event.pointerId}; handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      if (!gesture) return;
      const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
      layout(node, {...gesture.initial, ...(resize ? {width:gesture.initial.width+dx,height:gesture.initial.height+dy} : {x:gesture.initial.x+dx,y:gesture.initial.y+dy})});
    });
    const finish = event => { if (!gesture) return; const old = gesture.initial; gesture = null; node.gestureActive = false; if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId); saveLayout(node, old); };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', () => { if (!gesture) return; layout(node,gesture.initial); gesture = null; node.gestureActive = false; node.editing = !!node.savingLayout; });
    handle.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key) || uncertain || tiling || node.maximized) return;
      event.preventDefault(); const old = {...node.layout}, step = event.shiftKey ? 40 : 10;
      const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
      const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
      layout(node,{...old,...(resize ? {width:old.width+dx,height:old.height+dy} : {x:old.x+dx,y:old.y+dy})}); saveLayout(node,old);
    });
  }
  const native = data => data.profile === 'native';
  const linked = data => !!data && data.capabilities?.linked !== false;
  const automatic = data => !!data && !native(data) && data.capabilities?.automatic !== false;
  function abortDictation(node) { if (node.recognition) { try { node.recognition.abort(); } catch { /* Already stopped. */ } } }
  // The terminal's own prompt is the one place to type (the owner, 2026-10-10: no draft box). A shortcut, a file path
  // or dictation goes there as a paste would, and nothing is submitted.
  function stage(node, text) {
    if (!node.terminal || !inputReady(node)) { node.note.textContent = `The terminal is not connected yet; type ${text} in it when it is. Nothing was sent.`; return; }
    node.terminal.paste(text); node.terminal.focus();
    node.note.textContent = 'Added to the terminal’s prompt. Nothing has been sent.';
  }
  async function prepareCommand(node, command) {
    if (native(node.data)) { stage(node, command); return; }
    try {
      if (!navigator.clipboard?.writeText) throw Error('clipboard unavailable');
      await navigator.clipboard.writeText(command);
      node.note.textContent = `${command} copied · paste in the terminal.`;
    } catch {
      node.note.textContent = `Type ${command} in the terminal.`;
    }
    node.terminal?.focus();
  }
  function fileReference(node, result) {
    const workspace = node.data.workspace || snapshot.workspace;
    if (!workspace || result.workspace !== workspace || typeof result.path !== 'string'
        || result.path.startsWith('/') || result.path.split('/').some(p => p === '..' || p === '.')) {
      throw Error('The file does not belong to this terminal’s launch workspace. Refresh and try again.');
    }
    return JSON.stringify(workspace.replace(/\/$/, '') + '/' + result.path);
  }
  const TERMINAL_BUTTONS = [[/\[S\] Send/g,'s'],[/\[E\] Edit/g,'e'],[/\[ Optimizer: (?:on|off) \]/g,'\x0f'],[/\[ Skip next: (?:on|off) \]/g,'\x1d']];
  async function tileControl(node, action) {
    const response = await fetch('/api/deepsleep/control', {method:'POST', headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN},
      body:JSON.stringify({node:node.data.id, action})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || 'The terminal did not answer.');
    return data.result;
  }
  function showMode(node, state) {
    const label = state?.labels?.[state?.mode];
    node.modeChip.textContent = label || 'Mode unavailable';
    if (label) node.modeChip.dataset.mode = state.mode; else delete node.modeChip.dataset.mode;
  }
  async function cycleMode(node) {
    node.modeChip.disabled = true;
    try { showMode(node, await tileControl(node, 'permission_mode')); node.modeReadAt = Date.now(); }
    catch (error) { node.note.textContent = error.message; }
    finally { node.modeChip.disabled = false; }
  }
  function showTile(node, state) {
    showMode(node, state);
    const on = state?.read_twice === true;
    node.readTwiceChip.setAttribute('aria-pressed', String(on)); node.readTwiceChip.classList.toggle('on', on);
    node.ctxInfo.textContent = typeof state?.context === 'string' ? state.context : '';
  }
  // Shift+Tab in the terminal changes the mode too, and its context grows: read the row again at most every
  // 15 seconds while the board refreshes, in one call (tile_status).
  async function readMode(node) {
    if (!node.modeChip || node.modeReading || node.data.state !== 'ready' || Date.now() - (node.modeReadAt || 0) < 15000) return;
    node.modeReading = true;
    try { showTile(node, await tileControl(node, 'tile_status')); } catch { showTile(node, null); }
    finally { node.modeReading = false; node.modeReadAt = Date.now(); }
  }
  async function toggleReadTwice(node) {
    node.readTwiceChip.disabled = true;
    try {
      const on = (await tileControl(node, 'read_twice'))?.read_twice === true;
      node.readTwiceChip.setAttribute('aria-pressed', String(on)); node.readTwiceChip.classList.toggle('on', on);
      node.note.textContent = on ? 'Read ×2 on: what you type in this terminal is read twice.' : 'Read ×2 off.';
    } catch (error) { node.note.textContent = error.message; }
    finally { node.readTwiceChip.disabled = false; }
  }
  async function handoff(node) {
    node.handoffButton.disabled = true;
    try { await tileControl(node, 'fresh_start'); node.note.textContent = 'Handoff done: this terminal now goes on from a short summary.'; }
    catch (error) { node.note.textContent = error.message; }
    finally { node.handoffButton.disabled = false; }
  }
  // Presets: the Chat tab's own route (/api/presets); each tells the other through the dream:presets event.
  let presetState = null, presetLoad = null;
  async function presetCall(body) {
    const response = await fetch('/api/presets', body ? {method:'POST', headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN}, body:JSON.stringify(body)}
                                                : {headers:{'X-Dream-Token':TOKEN}, cache:'no-store'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || 'Presets are unavailable.');
    return data;
  }
  function loadPresets() {
    presetLoad ||= presetCall().then(data => { presetState = data; }, error => { presetState = {error: error.message, presets: []}; })
      .finally(() => { presetLoad = null; for (const node of nodes.values()) drawPreset(node); });
    return presetLoad;
  }
  function drawPreset(node) {
    if (!node.presetSelect) return;
    if (!presetState) { loadPresets(); return; }
    const list = Array.isArray(presetState.presets) ? presetState.presets : [];
    replaceOptions(node.presetSelect, list.map(p => [p.name, p.name]), presetState.active);
    const cost = list.find(p => p.name === node.presetSelect.value)?.cost;
    node.presetCost.textContent = presetState.error || (cost ? `${cost.skills} skills · ${cost.tools} tools · ~${(cost.tokens / 1000).toFixed(1)}K tokens` : '');
    node.presetCost.title = node.presetSelect.title = node.presetCost.textContent;
    node.presetSelect.disabled = !list.length;
  }
  async function usePreset(node) {
    try {
      await presetCall({op:'use', preset:node.presetSelect.value, sha256:presetState?.sha256});
      node.note.textContent = `Skill preset ${node.presetSelect.value} applies to new sessions.`;
      window.dispatchEvent(new CustomEvent('dream:presets', {detail:'deepsleep'}));
    } catch (error) { node.note.textContent = error.message; }
    presetState = null; loadPresets();
  }
  window.addEventListener('dream:presets', event => { if (event.detail !== 'deepsleep') { presetState = null; loadPresets(); } });
  function footer(node) {
    const tools = el('div', null, 'ds-footer-tools');
    const drawer = node.drawer = el('div', null, 'ds-tools-drawer');
    const repoBar = el('div', null, 'ds-repo-bar');
    const toggle = node.toolsButton = button('Tools ▾', 'Terminal tools for ' + (node.data.title || node.data.provider), () => setTools(node, node.drawer.hidden));
    toggle.setAttribute('aria-expanded', 'false');
    const repo = el('div', null, 'ds-repo');
    const workspace = node.data.workspace || snapshot.workspace || '';
    repo.setAttribute('aria-label', 'Launch workspace'); repo.textContent = '⌁ ' + (workspace.split('/').filter(Boolean).at(-1) || 'Workspace'); repo.title = 'Launch workspace: ' + (workspace || 'Current project');
    node.repo = repo;
    const actions = el('div', null, 'ds-footer-actions');
    node.commandButtons = [];
    for (const [key, label] of [['model','Model'], ['effort','Effort'], ['permissions','Permissions'], ['remote','Remote control'], ...(!native(node.data) ? [['changes','Changes']] : [])]) {
      const shortcut = button(label, `${label} in ${node.data.title || node.data.provider}`, () => {
        const command = node.data.capabilities?.commands?.[key];
        if (command) prepareCommand(node, command);
      });
      shortcut.dataset.command = key;
      node.commandButtons.push(shortcut); actions.append(shortcut);
    }
    const filePicker = el('input'); filePicker.type = 'file'; filePicker.multiple = true; filePicker.hidden = true;
    const attach = button('＋', 'Reference attached files in the prompt', () => filePicker.click());
    attach.title = 'Attach up to 8 files, 25 MB each. Their paths go into the terminal’s prompt.';
    filePicker.onchange = async () => {
      const files = [...filePicker.files]; filePicker.value = '';
      if (files.length > 8) { node.note.textContent = 'Choose at most 8 files at a time. Nothing was uploaded.'; return; }
      if (files.some(file => file.size > 25 * 1024 * 1024)) { node.note.textContent = 'Each file must be 25 MB or smaller. Nothing was uploaded.'; return; }
      attach.disabled = true;
      try {
        for (const file of files) {
          node.note.textContent = `Attaching ${file.name}…`;
          const response = await fetch('/api/deepsleep/upload?node=' + encodeURIComponent(node.data.id) + '&name=' + encodeURIComponent(file.name), {method:'POST', headers:{'Content-Type':'application/octet-stream','X-Dream-Token':TOKEN}, body:file});
          const result = await response.json();
          if (!response.ok || typeof result.path !== 'string') throw Error(result.error || 'Upload failed. Nothing was added.');
          stage(node, fileReference(node, result));
          node.note.textContent = 'File reference added. Provider attachment and image handling depend on the native CLI.';
        }
      } catch (error) { node.note.textContent = error.message || 'Upload failed. Nothing was added.'; }
      finally { attach.disabled = false; }
    };
    const explorer = el('section', null, 'ds-file-explorer'); explorer.hidden = true; explorer.setAttribute('aria-label', 'Files in ' + (node.data.title || node.data.provider));
    const explorerBody = el('div', null, 'ds-file-list');
    const explorerPath = el('span', 'Workspace', 'ds-file-path');
    const fileButton = button('Files', 'File explorer for ' + (node.data.title || node.data.provider), () => { explorer.hidden = !explorer.hidden; fileButton.setAttribute('aria-expanded', String(!explorer.hidden)); if (!explorer.hidden) browse(''); });
    fileButton.setAttribute('aria-expanded', 'false');
    let fileEpoch = 0;
    async function browse(path) {
      const requestEpoch = ++fileEpoch;
      explorerBody.replaceChildren(el('p', 'Loading files…'));
      try {
        const response = await fetch('/api/deepsleep/files', {method:'POST',headers:{'X-Dream-Token':TOKEN,'Content-Type':'application/json'},body:JSON.stringify({node:node.data.id,path})});
        const data = await response.json();
        if (!response.ok || !Array.isArray(data.entries)) throw Error(data.error || 'Files could not load.');
        if (requestEpoch !== fileEpoch) return;
        explorerPath.textContent = data.path || 'Workspace';
        explorerBody.replaceChildren();
        if (data.path) explorerBody.append(button('↑ Workspace', 'Return to workspace root', () => browse('')));
        for (const entry of data.entries) {
          const item = button((entry.directory ? '▸ ' : '') + entry.name, (entry.directory ? 'Open folder ' : 'Add file ') + entry.name, () => {
            if (entry.directory) browse(entry.path);
            else { try { stage(node, fileReference(node, {workspace:data.workspace,path:entry.path})); explorer.hidden = true; fileButton.setAttribute('aria-expanded','false'); } catch(error) { node.note.textContent = error.message; } }
          });
          explorerBody.append(item);
        }
        if (!data.entries.length) explorerBody.append(el('p', 'This folder is empty.'));
        if (data.truncated) explorerBody.append(el('p', 'This listing is limited. Open a subfolder to browse further.'));
      } catch(error) {
        if (requestEpoch !== fileEpoch) return;
        explorerBody.replaceChildren(el('p', error.message || 'Files could not load.'), button('Retry', 'Retry loading files', () => browse(path)));
      }
    }
    const explorerHead = el('div', null, 'ds-file-head'); explorerHead.append(explorerPath, button('×', 'Close file explorer', () => { explorer.hidden = true; fileButton.setAttribute('aria-expanded','false'); fileButton.focus(); })); explorer.append(explorerHead, explorerBody);
    explorer.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); explorer.hidden = true; fileButton.setAttribute('aria-expanded','false'); fileButton.focus(); } });
    const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = button('Mic', node.data.capabilities?.commands?.voice ? 'Voice controls in ' + (node.data.title || node.data.provider) : 'Dictate into the prompt', () => {
      if (node.data.capabilities?.commands?.voice) { prepareCommand(node, node.data.capabilities.commands.voice); return; }
      if (!Speech) return;
      if (node.recognition) { node.recognition.stop(); return; }
      const recognition = new Speech(); node.recognition = recognition; recognition.interimResults = false;
      mic.setAttribute('aria-pressed','true'); mic.textContent = 'Stop mic';
      node.note.textContent = 'Listening through your browser’s speech service. Words go into the terminal’s prompt; nothing is sent.';
      recognition.onresult = event => { for (let i = event.resultIndex; i < event.results.length; i++) if (event.results[i].isFinal) stage(node, event.results[i][0].transcript); };
      recognition.onerror = event => { node.note.textContent = 'Dictation unavailable: ' + (event.error || 'microphone error') + '.'; };
      recognition.onend = () => { node.recognition = null; mic.textContent = 'Mic'; mic.setAttribute('aria-pressed','false'); };
      try { recognition.start(); } catch { recognition.onend(); node.note.textContent = 'Microphone could not start.'; }
    });
    mic.disabled = !Speech && !node.data.capabilities?.commands?.voice;
    mic.title = node.data.capabilities?.commands?.voice ? 'Prepare the provider voice command' : Speech ? 'Dictate using your browser speech service; may require microphone permission and network access' : 'Microphone input is unavailable in this browser and provider';
    mic.setAttribute('aria-pressed','false');
    actions.append(attach, fileButton, mic, filePicker);
    const launch = node.launchInfo = el('span', '', 'ds-launch-info');
    const folder = document.createElementNS(svg.namespaceURI, 'svg');
    folder.setAttribute('viewBox','0 0 20 20'); folder.setAttribute('aria-hidden','true');
    const outline = document.createElementNS(svg.namespaceURI, 'path');
    outline.setAttribute('d','M2 5V4h6l2 2h8v10H2V5Z'); folder.append(outline);
    repo.classList.add('ds-repo-badge');
    repo.replaceChildren(folder,el('span',workspace.split('/').filter(Boolean).at(-1) || 'Workspace'));
    const effort = node.commandButtons.find(item => item.dataset.command === 'effort');
    effort.className = 'ds-effort';
    const toolsRepo=el('div',null,'ds-tools-repo');toolsRepo.append(toggle,repo);
    node.sessionTag=el('span','Terminal '+node.data.id.slice(-6),'ds-session-id');node.sessionTag.title='Terminal ID: '+node.data.id;
    const barStart=el('div',null,'ds-bar-side ds-bar-start'),barEnd=el('div',null,'ds-bar-side ds-bar-end');
    barStart.append(toolsRepo);barEnd.append(node.sessionTag,effort,node.status);
    repoBar.append(barStart,node.outputPort,barEnd);
    node.permissionsInfo=el('span','','ds-permissions-info');
    drawer.append(launch,actions,explorer);
    tools.append(repoBar);
    // The Chat tab's own controls, under the bar beside the repo (the owner, 2026-10-10): this terminal's permission
    // mode and Handoff, through DeepSleep's relay to it, and the skill preset new sessions start with (Dream-wide,
    // as the Chat tab's Preset), with what it costs. A native CLI keeps its own, in its terminal.
    if (!native(node.data)) {
      const title = node.data.title || node.data.provider, row = node.chatRow = el('div', null, 'ds-chat-row');
      node.modeChip = button('Mode…', 'Cycle permission mode in ' + title, () => cycleMode(node)); node.modeChip.className = 'ds-chip ds-mode-chip';
      node.modeChip.title = 'This terminal’s permission mode; click for the next one (Shift+Tab in the terminal does the same)';
      node.ctxInfo = el('span', '', 'ds-ctx-info'); node.ctxInfo.title = 'This terminal’s context: used of its window';
      node.readTwiceChip = button('Read ×2', 'Read ×2 in ' + title, () => toggleReadTwice(node)); node.readTwiceChip.className = 'ds-chip ds-read-chip';
      node.readTwiceChip.setAttribute('aria-pressed', 'false');
      node.readTwiceChip.title = 'Read ×2: the model reads each message you type in this terminal twice (only your words, never the history)';
      node.handoffButton = button('Handoff', 'Handoff in ' + title, () => handoff(node)); node.handoffButton.className = 'ds-chip';
      node.handoffButton.title = 'Rewrite this terminal’s conversation as one short handoff now; between turns only (/handoff does the same)';
      const pick = el('label', 'Preset', 'ds-preset-pick'); node.presetSelect = select('Skill preset for new sessions'); pick.append(node.presetSelect);
      pick.title = 'Skill preset for new sessions, as on the Chat tab: the skills and tools a new session starts with. Running sessions keep theirs.';
      node.presetCost = el('span', '', 'ds-preset-cost');
      node.presetSelect.onchange = () => usePreset(node);
      // Three groups that wrap whole on a narrow tile: this terminal's state, its actions, the preset (2026-10-11).
      const group = (...items) => { const g = el('div', null, 'ds-chip-group'); g.append(...items); return g; };
      row.append(group(node.modeChip, node.ctxInfo), group(node.readTwiceChip, node.handoffButton), group(pick, node.presetCost));
      tools.append(row);
      drawPreset(node);
    }
    tools.append(drawer);
    drawer.hidden = true;
    const characterLabel = el('label', 'Character', 'ds-character-choice');
    node.characterSelect = select('Character for ' + (node.data.title || node.data.provider));
    for (const name of characters) { const option = el('option',name[0].toUpperCase()+name.slice(1)); option.value=name; node.characterSelect.append(option); }
    node.characterSelect.onchange = () => setCharacter(node,node.characterSelect.value,true);
    node.avatarSizeSelect=select('Character size for '+(node.data.title||node.data.provider));
    for(const [value,label] of [['small','Small'],['medium','Medium'],['large','Large']]){const option=el('option',label);option.value=value;node.avatarSizeSelect.append(option);}
    node.avatarSizeSelect.onchange=()=>setAvatarSize(node,node.avatarSizeSelect.value,true);
    characterLabel.append(node.characterSelect,el('span','Size'),node.avatarSizeSelect,button('Place character','Place character for '+(node.data.title||node.data.provider),()=>revealAvatar(node)),button('Reset position','Reset character position for '+(node.data.title||node.data.provider),()=>resetAvatar(node)));
    const terminalLabel=el('label','Terminal name','ds-display-name');
    node.nameInput=el('input');node.nameInput.type='text';node.nameInput.maxLength=100;node.nameInput.value=node.data.title||node.data.provider;
    node.nameInput.title='Name only this terminal. Its running session and connections stay the same.';
    node.nameInput.addEventListener('input',()=>{node.nameDirty=true;node.nameStatus.textContent='';});
    node.nameInput.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();event.stopPropagation();renameTerminal(node);}});
    terminalLabel.append(node.nameInput);
    node.nameSave=button('Save name','Save terminal name',()=>renameTerminal(node));
    node.nameStatus=el('span','','ds-name-status');node.nameStatus.setAttribute('role','status');
    const terminalNameRow=el('div',null,'ds-terminal-name-row');terminalNameRow.append(terminalLabel,node.nameSave,node.nameStatus);
    const nameLabel=el('label','Your message label (all terminals)','ds-display-name');
    node.displayNameInput=el('input');node.displayNameInput.type='text';node.displayNameInput.maxLength=60;
    node.displayNameInput.value=displayName;node.displayNameInput.placeholder='You';node.displayNameInput.autocomplete='off';
    node.displayNameInput.title='Display label for your messages in this workspace. Session identity and message content stay unchanged.';
    const saveName=()=>setDisplayName(node.displayNameInput.value,true);
    node.displayNameInput.addEventListener('change',saveName);
    node.displayNameInput.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();event.stopPropagation();saveName();}});
    nameLabel.append(node.displayNameInput);
    const appearanceControls=el('div',null,'ds-appearance-controls');appearanceControls.append(terminalNameRow,characterLabel,nameLabel);drawer.append(appearanceControls);

    return tools;
  }
  function updateFooter(node) {
    const commands = node.data.capabilities?.commands || {};
    for (const item of node.commandButtons) {
      const command = commands[item.dataset.command]; item.disabled = !command; item.hidden = !command;
      item.title = command ? (native(node.data) ? `Prepare ${command} in your draft; paste into the terminal when ready` : `Copy ${command} for terminal paste; does not submit`) : 'This provider has no verified shortcut for this control';
      if (!native(node.data) && item.dataset.command === 'remote') {
        item.disabled = true; item.hidden=true;
        item.title = 'Remote control is not available for Dream workflow';
      }
      if (item.dataset.command === 'changes' && command) item.title = `Show files changed in this session: copy ${command}, paste into terminal`;
      if (item.dataset.command === 'effort') {
        item.textContent = 'Effort ↗';
        item.title += ' Launch effort is not a live setting.';
      }
    }
    const values = [node.data.initial_effort && `effort ${node.data.initial_effort}`, node.data.initial_permissions && `permissions ${node.data.initial_permissions}`].filter(Boolean);
    node.launchInfo.textContent = values.length ? 'At launch: ' + values.join(' · ') : '';
    node.launchInfo.hidden = !values.length;
    if (node.permissionsInfo) {
      node.permissionsInfo.textContent = '';
      node.permissionsInfo.title = 'Launch permissions only. The terminal shows current permission changes.';
    }
  }
  function addNode(data) {
    const node = {data,layout:{x:0,y:0,width:600,height:470},editing:false}; nodes.set(data.id,node);
    const card = node.element = el('article',null,'ds-node'); card.dataset.node = data.id; card.classList.toggle('ds-native-pane',native(data)); card.classList.add('ds-compact'); card.setAttribute('aria-label',data.title || data.provider);
    const head = node.head = el('div',null,'ds-node-head');
    const move = button('⠿','Move '+(data.title || data.provider)+'; use arrow keys',null); move.className='ds-move'; movable(node,move,false);
    const title = node.title = el('h2',data.title || data.provider); const state = node.state = el('span',data.state || 'starting','ds-state');
    const login = button('Sign in','Sign in to '+(data.title || data.provider),() => { if (window.DreamFrontiers?.login) window.DreamFrontiers.login(node.data.provider); else node.note.textContent = 'Sign-in controls are unavailable. Refresh Dream to reconnect them.'; });
    login.className='ds-login';
    node.collapseButton=button('▴','Collapse '+(data.title||data.provider)+' to header',()=>setCollapsed(node,!node.collapsed));node.collapseButton.setAttribute('aria-expanded','true');
    node.collapsedNotice=el('span','New output','ds-collapsed-output');node.collapsedNotice.hidden=true;node.collapsedNotice.setAttribute('role','status');
    node.close = button('×','Close '+(data.title || data.provider),async () => { await mutate({action:'close',node:data.id},'Terminal closed.'); });
    if (['local','openai','machx'].includes(data.provider)) { login.textContent='Local'; login.disabled=true; login.title='Local models do not need a frontier account sign-in.'; }
    node.maximizeButton = button('⛶', 'Maximize ' + (data.title || data.provider), () => maximize(node));
    node.sessionButton=button('▤','Session details for '+(data.title||data.provider),()=>node.sessionDrawer?.setOpen(!node.sessionOpen));
    node.sessionButton.setAttribute('aria-expanded','false');node.sessionButton.disabled=!window.DreamSessionDrawer?.create;
    // The top and bottom bars are a left side, a pill in the middle (In / Out) and a right side (2026-10-10).
    const headStart=el('div',null,'ds-bar-side ds-bar-start'),headEnd=el('div',null,'ds-bar-side ds-bar-end');
    headStart.append(move,title);headEnd.append(state,node.collapsedNotice,node.sessionButton,login,node.collapseButton,node.maximizeButton,node.close);
    head.append(headStart,headEnd);node.headEnd=headEnd;
    const ports = node.ports = el('div',null,'ds-ports'); ports.hidden=true;
    const inputPort = button('● Input','Link to '+(data.title || data.provider),async () => {
      if (!source) { message('Choose Output on the sending terminal first.'); return; }
      if (source === data.id) { message('Choose a different terminal as the receiver.',true); return; }
      const manual=!automatic(nodes.get(source)?.data)||!automatic(node.data);
      if (await mutate({action:'link',source,target:data.id,auto_forward:manual?false:forwarding.checked},manual?'Linked. A native CLI sends and reads direct messages with its link tools (links, link_send, link_inbox).':'Terminals connected.')) { source = null; markSource(); }
    });
    const outputPort = button('Output ●','Link from '+(data.title || data.provider),() => { source = source === data.id ? null : data.id; markSource(); message(source ? 'Now choose Input on the receiving terminal. Escape cancels.' : 'Connection selection cancelled.'); });
    node.inputPort = inputPort; node.outputPort = outputPort;
    ports.append(inputPort,el('span',(native(data) ? 'Native CLI · ' : '') + (data.model || 'Provider default'),'ds-model'),outputPort);
    for (const port of [inputPort,outputPort]) { port.disabled = !linked(data); if (port.disabled) port.title = 'Links are unavailable for this terminal.'; }
    // In: a wide pill in the middle of the top bar; Out: the same in the middle of the bottom bar (footer, below).
    {inputPort.textContent='In';outputPort.textContent='Out';inputPort.classList.add('ds-port-pill');outputPort.classList.add('ds-port-pill');head.insertBefore(inputPort,headEnd);}
    const terminalHost = node.terminalHost = el('div',null,'ds-terminal'); terminalHost.setAttribute('aria-label','Terminal '+(data.title || data.provider));
    node.latest = button('↓ Latest', 'Follow latest output in '+(data.title || data.provider), () => { node.followOutput = true;node.historyRow=null;node.historyMarker?.dispose();node.historyMarker=null; node.terminal?.scrollToBottom(); node.latest.hidden = true; node.terminal?.focus(); });
    node.latest.className = 'ds-latest'; node.latest.hidden = true; terminalHost.append(node.latest);
    const status = node.status = el('div',null,'ds-terminal-status'); node.connection=el('span','Not connected'); node.reconnect=button('Reconnect','Reconnect '+(data.title || data.provider),()=>connect(node)); node.restart=button('Restart','Restart '+(data.title || data.provider),async()=>{node.promptMarker?.dispose();node.promptMarker=null;if(await mutate({action:'restart',node:data.id},'Terminal restarted.')){node.socket?.close();node.socket=null;connect(node);}}); node.restart.hidden=!['exited','failed'].includes(data.state); status.append(node.connection,node.reconnect,node.restart);
    node.agents=el('ul',null,'ds-agents'); node.agents.setAttribute('aria-label','Agents in '+(data.title || data.provider));
    node.note=el('p','','ds-node-note'); node.note.setAttribute('role','status');
    const resize=button('◢','Resize '+(data.title || data.provider)+'; use arrow keys',null); resize.className='ds-resize'; movable(node,resize,true);
    const tools = footer(node);
    node.avatarAnchor=el('div',null,'ds-avatar-anchor');node.avatarAnchor.dataset.avatarNode=data.id;
    const avatar = node.avatar = button('', 'Move '+(data.title||data.provider)+' character; arrow keys move, Home resets',null);avatar.className='ds-avatar-slot';avatar.dataset.avatarNode=data.id;movableAvatar(node);
    node.avatarImage = el('img'); node.avatarImage.draggable = false;
    node.avatarFallback = el('span','','ds-avatar-fallback'); node.avatarFallback.hidden = true;
    node.avatarImage.onerror = () => { node.avatarImage.hidden=true; node.avatarFallback.hidden=false; node.avatarFallback.textContent=node.character[0].toUpperCase(); node.avatarFallback.setAttribute('aria-label',node.character+' character image unavailable'); };
    node.avatarImage.onload = () => { node.avatarImage.hidden=false; node.avatarFallback.hidden=true; };
    node.avatarInputPort=button('In','Link to '+(data.title||data.provider)+' character',()=>inputPort.click());
    node.avatarOutputPort=button('Out','Link from '+(data.title||data.provider)+' character',()=>outputPort.click());
    for(const [port,original] of [[node.avatarInputPort,inputPort],[node.avatarOutputPort,outputPort]]){port.className='ds-avatar-port';port.disabled=original.disabled;port.title=original.title;}
    avatar.append(node.avatarImage,node.avatarFallback);
    node.avatarAnchor.append(node.avatarInputPort,avatar,node.avatarOutputPort);companions.append(node.avatarAnchor);
    setAvatarSize(node,readAppearance(appearanceKey('avatar-size',node)));
    setCharacter(node,readAppearance(appearanceKey('avatar',node)));
    const content=node.content=el('div',null,'ds-content');content.id='ds-content-'+data.id;node.collapseButton.setAttribute('aria-controls',content.id);
    node.terminalToggle=button('Hide terminal','Toggle terminal input and approvals',()=>{node.terminalOpen=node.terminalHost.hidden;updateTextLayout(node);if(!node.terminalHost.hidden)node.terminal?.focus();});
    node.terminalToggle.className='ds-terminal-toggle';
    node.activityToggle=button('Activity','Recorded activity for '+(data.title||data.provider),()=>{node.activityOpen=!node.activityOpen;updateTextLayout(node);});
    node.drawer.append(node.activityToggle);
    if(window.DreamSessionDrawer?.create) {
      node.sessionDrawer=window.DreamSessionDrawer.create({title:data.title||data.provider,workspace:data.workspace||snapshot.workspace||'',anchor:node.sessionButton,pane:card,bounds:viewport,
        onOpenChange:value=>{if(value && openSessionNode && openSessionNode!==node)openSessionNode.sessionDrawer.setOpen(false,false);node.sessionOpen=value;openSessionNode=value?node:null;node.sessionButton.setAttribute('aria-expanded',String(value));updateTextLayout(node);}});
      page.append(node.sessionDrawer.element);
    }
    if(window.DreamActivity?.create) {
      node.activityView=window.DreamActivity.create({nodeId:data.id,token:TOKEN,userLabel:displayName,
        canPoll:()=>{const r=content.getBoundingClientRect(),v=viewport.getBoundingClientRect();return live()&&!document.hidden&&(node.sessionOpen||(!node.collapsed&&(!maximized||maximized===node)&&r.bottom>v.top&&r.top<v.bottom&&r.right>v.left&&r.left<v.right));},
        onAvailability:(value,state)=>{node.activityAvailable=value;node.activityHasContent=state?.hasContent===true;updateTextLayout(node);},
        onSnapshot:value=>{node.sessionDrawer?.update(value);const id=value.session?.session_id;if(id){node.sessionTag.textContent='Session '+id.slice(-6);node.sessionTag.title='Session ID: '+id;}},onError:text=>node.sessionDrawer?.error(text)});
      content.append(node.activityView.element);
    } else {node.activityAvailable=false;node.activityFallback=el('p','Structured activity renderer is unavailable. Use the terminal.','ds-event-status');content.append(node.activityFallback);}
    content.append(node.terminalToggle,terminalHost);
    card.append(head,ports,content);
    if(native(data)) card.append(tools); else card.append(status,tools);
    card.append(node.agents,node.note,resize); updateFooter(node); board.append(card);
    card.addEventListener('pointerdown',()=>{raiseNode(node);});
    if(typeof window.Terminal==='function' && window.FitAddon?.FitAddon) {
      node.terminal=new window.Terminal({cursorBlink:true,fontSize:native(data)?12:13,fontFamily:'ui-monospace, SFMono-Regular, monospace',scrollback:5000,theme:{background:'#11100e',foreground:'#f2ede3',cursor:'#e9aa62',selectionBackground:'#a8783c66',...(!native(data)&&{magenta:'#d79558',brightMagenta:'#efbb77',blue:'#c8aa77',brightBlue:'#e7c68b'})},allowProposedApi:!native(data)});
      node.fit=new window.FitAddon.FitAddon(); node.terminal.loadAddon(node.fit); node.terminal.open(terminalHost);
      if(!native(data))node.promptProtocol=node.terminal.parser.registerOscHandler(777,payload=>{
        if(!payload.startsWith('dream-prompt;'))return false;
        node.promptMarker?.dispose();node.promptMarker=null;
        const value=payload.slice('dream-prompt;'.length);
        if(/^(0|[1-9]\d{0,3})$/.test(value) && Number(value)<node.terminal.rows && node.terminal.buffer.active.type==='normal')
          node.promptMarker=node.terminal.registerMarker(-Number(value));
        return true;
      });
      if (!native(data)) followOutput(node);
      const sendInput=text=>{if(inputReady(node))node.socket.send(JSON.stringify({type:'input',data:text}));else{node.inputBlockedNote=true;node.note.textContent=blockedInputNote;}};
      node.terminal.onData(sendInput);
      if(!native(data)) {
        // Dream's own buttons in its terminal text (the owner, 2026-10-11): the rewrite's Send and Edit, and the
        // prompt optimizer's two switches on the prompt box's line. A click sends that button's key, as if typed.
        // Read from the clicked cell, not xterm's link hover: Dream redraws those rows every second (8 times a
        // second during a turn), and each redraw drops a hovered link.
        const buttonAt=event=>{
          const t=node.terminal, screen=terminalHost.querySelector('.xterm-screen'), r=screen?.getBoundingClientRect();
          if(!r||!r.width||!r.height)return null;
          const col=Math.floor((event.clientX-r.left)/(r.width/t.cols)), row=Math.floor((event.clientY-r.top)/(r.height/t.rows));
          const line=t.buffer.active.getLine(t.buffer.active.viewportY+row)?.translateToString(true)||'';
          for(const [pattern,key] of TERMINAL_BUTTONS) for(const found of line.matchAll(pattern))
            if(col>=found.index&&col<found.index+found[0].length)return key;
          return null;
        };
        terminalHost.addEventListener('click',event=>{
          const key=!node.terminal.hasSelection()&&buttonAt(event);
          if(key){event.preventDefault();sendInput(key);}
        });
        terminalHost.addEventListener('mousemove',event=>{
          const screen=terminalHost.querySelector('.xterm-screen');
          if(screen)screen.style.cursor=buttonAt(event)?'pointer':'';
        });
        terminalHost.addEventListener('compositionstart',()=>{node.composing=true;},true);
        terminalHost.addEventListener('compositionend',()=>{node.composing=false;},true);
        node.terminal.attachCustomKeyEventHandler(event=>{
          if(native(node.data)||event.key!=='Enter'||!event.shiftKey||event.ctrlKey||event.altKey||event.metaKey||event.isComposing||event.keyCode===229||node.composing)return true;
          event.preventDefault();event.stopPropagation();
          // Preserve a distinct modified key through PTY transport. A bare LF/CR
          // could accept an approval prompt instead of inserting a draft newline.
          if(event.type==='keydown')sendInput('\x1b[13;2u');
          return false;
        });
      }
      node.observer=new ResizeObserver(()=>fit(node)); node.observer.observe(terminalHost); connect(node);
    } else { node.note.textContent='Terminal renderer unavailable. Refresh Dream after its assets finish loading.'; node.reconnect.disabled=true; }
    updateTextLayout(node); layout(node,data.layout); return node;
  }
  function markSource() { for(const [id,node] of nodes) {node.element.dataset.linking=String(source===id);node.avatarOutputPort?.setAttribute('aria-pressed',String(source===id));} }
  // Where a cord ends (2026-10-10): a shown character's port; else Out in the middle of the bottom bar and In in the
  // middle of the top bar. A collapsed tile hides its bottom bar, so its Out leaves from the bottom of its top bar.
  function cordEnd(node,out){
    const shown=element=>{if(!element?.isConnected||element.hidden||getComputedStyle(element).visibility==='hidden')return false;const r=element.getBoundingClientRect();return r.width>0&&r.height>0;};
    const avatarPort=out?node.avatarOutputPort:node.avatarInputPort;
    let element=out?node.outputPort:node.inputPort,side=out?'bottom':'top',edge=false;
    if(shown(node.avatar)&&shown(avatarPort)){element=avatarPort;side=out?'right':'left';}
    else if(!shown(element)){if(!out||!node.collapsed||!shown(node.head))return null;element=node.head;edge=true;}
    const r=element.getBoundingClientRect(),origin=board.getBoundingClientRect();if(!origin.width||!origin.height)return null;
    // A pill's cord leaves from its bottom edge and lands on its top edge, so the arrow never covers its label.
    const y=edge||side==='bottom'?r.bottom:side==='top'?r.top:r.top+r.height/2;
    return {x:(r.left+r.width/2-origin.left)*board.offsetWidth/origin.width,y:(y-origin.top)*board.offsetHeight/origin.height,side,character:side==='left'||side==='right'};
  }
  // A cord leaves Out downward (a character's sideways) and enters In from above (a character's from the left).
  function cordPath(a,b){
    const across=end=>end.side==='left'||end.side==='right';
    const reach=across(a)&&across(b)?Math.max(70,Math.abs(b.x-a.x)*.45):Math.max(60,Math.hypot(b.x-a.x,b.y-a.y)*.4);
    const c1=across(a)?[a.x+reach,a.y]:[a.x,a.y+reach],c2=across(b)?[b.x-reach,b.y]:[b.x,b.y-reach];
    return `M ${a.x} ${a.y} C ${c1[0]} ${c1[1]}, ${c2[0]} ${c2[1]}, ${b.x} ${b.y}`;
  }
  function wires() {
    svg.querySelectorAll('.ds-wire').forEach(n=>n.remove());
    let characterAnchors=false;
    for(const link of snapshot.links) {
      const a=nodes.get(link.source),b=nodes.get(link.target);if(!a||!b)continue;
      const path=document.createElementNS(svg.namespaceURI,'path'); path.classList.add('ds-wire'); if(link.delivery==='manual'||!automatic(a.data)||!automatic(b.data))path.classList.add('ds-wire-manual');
      const from=cordEnd(a,true),to=cordEnd(b,false);characterAnchors ||= !!(from?.character||to?.character);if(!from||!to)continue;
      path.setAttribute('d',cordPath(from,to)); path.setAttribute('marker-end','url(#ds-arrow)');svg.append(path);
    }
    svg.classList.toggle('ds-character-wires',characterAnchors);
  }
  function reconcile(data) {
    if(data.workspace && data.workspace!==snapshot.workspace){handoffFlow?.reset();for(const node of nodes.values())node.cancelAvatarDrag?.();}
    syncChannelWorkspace(data.workspace||snapshot.workspace||'');
    channelSupported=Array.isArray(data.channel);
    snapshot={channel:data.channel||[],board:data.board||{posts:[],error:''},workspace:data.workspace||snapshot.workspace||'',nodes:data.nodes,links:data.links,handoffs:data.handoffs||[],receipts:data.receipts||[],groups:data.groups||[],providers:data.providers||snapshot.providers};
    if (appearanceWorkspace !== snapshot.workspace) { appearanceWorkspace = snapshot.workspace; setSkin(readAppearance(appearanceKey('skin'))); setTextLayout(readAppearance(appearanceKey('text-layout'))); setDisplayName(readAppearance(appearanceKey('display-name'))); }
    replaceOptions(provider,snapshot.providers.map(p=>[p.key,p.label]),provider.value); models(); profiles(); add.disabled=pending||uncertain||!provider.value;
    const ids=new Set(snapshot.nodes.map(n=>n.id));
    for(const [id,node] of nodes) if(!ids.has(id)){abortDictation(node);node.sessionDrawer?.dispose();node.activityView?.dispose();node.socket?.close();node.observer?.disconnect();node.disposeScrollTracking?.();node.promptProtocol?.dispose();node.promptMarker?.dispose();node.terminal?.dispose();node.cancelAvatarDrag?.();node.avatarAnchor?.remove();node.element.remove();nodes.delete(id);if(maximized===node){maximized=null;page.classList.remove('ds-has-maximized');}if(source===id)source=null;}
    for(const raw of snapshot.nodes) {
      const node=nodes.get(raw.id)||addNode(raw);
      // A connected authenticated socket owns process state; REST snapshots
      // still carry layout and metadata, but may predate its latest event.
      const item=node.transport==='ready'&&node.socket?.readyState===WebSocket.OPEN&&node.processState ? {...raw,state:node.processState} : raw;
      updateTerminalName(node,item);node.data=item;node.title.textContent=item.title||item.provider;updateConnection(node);
      if (node.appearanceKey !== appearanceKey('avatar',node)) setCharacter(node,readAppearance(appearanceKey('avatar',node)));
      updateFooter(node); if(item.error)node.note.textContent=item.error;
      node.agents.replaceChildren();for(const agent of item.agents||[]){const li=el('li',typeof agent==='string'?agent:`${agent.name||agent.id||'Agent'} · ${agent.state||agent.status||'working'}`);node.agents.append(li);}node.agents.hidden=!node.agents.children.length;
      if(!node.editing&&!node.maximized)layout(node,item.layout);
    }
    tileButton.disabled=pending||tiling||uncertain||nodes.size<2; empty.hidden=nodes.size>0; markSource();dimensions();wires();
    for(const node of nodes.values())readMode(node);
    const choices=snapshot.nodes.filter(automatic).map(n=>[n.id,receiverLabel(n)]);replaceOptions(targets,choices);replaceOptions(synthesis,choices,synthesis.value);
    linksList.replaceChildren();
    if(!snapshot.links.length)linksList.append(el('p','No connections yet. Choose Output on a terminal, then Input on its receiver.'));
    for(const link of snapshot.links){const row=el('div',null,'ds-link-row');row.append(el('span',`${nodes.get(link.source)?.title.textContent||link.source} → ${nodes.get(link.target)?.title.textContent||link.target}${!automatic(nodes.get(link.target)?.data)?' · native receiver: link inbox':!automatic(nodes.get(link.source)?.data)?' · native sender: link tools':link.auto_forward?' · automatic':''}`),button('Unlink','Unlink '+link.id,()=>mutate({action:'unlink',id:link.id},'Connection removed.')));linksList.append(row);}
    receipts.replaceChildren();
    if(!snapshot.receipts.length)receipts.append(el('p','No deliveries yet. Prompts and linked handoffs will appear here.'));
    for(const group of snapshot.groups||[]) receipts.append(el('p',`Group · ${group.state} · ${group.error||`${group.messages?.length||0} researchers → ${nodes.get(group.synthesis_target)?.title.textContent||group.synthesis_target}`}`));
    for(const receipt of snapshot.receipts.slice(-30).reverse()) {const route=receipt.target ? `${nodes.get(receipt.source)?.title.textContent||receipt.source||'You'} → ${nodes.get(receipt.target)?.title.textContent||receipt.target} · ` : '';receipts.append(el('p',`${route}${receipt.state||receipt.status||'recorded'} · ${receipt.error||receipt.routing_error||receipt.message||receipt.summary||receipt.id||'Delivery recorded'}`));
      if(receipt.result){const answer=el('details');answer.append(el('summary','Read response'+(receipt.result_truncated?' (truncated)':'')),el('pre',receipt.result));receipts.append(answer);}}
    renderChannel();handoffFlow?.update(snapshot);
  }
  function view() {page.hidden=!live();if(!live()){handoffFlow?.reset();openSessionNode?.sessionDrawer?.setOpen(false,false);for(const node of nodes.values())node.cancelAvatarDrag?.();}for(const node of nodes.values())updateTextLayout(node);if(live()){refresh();requestAnimationFrame(()=>{dimensions();for(const node of nodes.values())fit(node);});}else{for(const node of nodes.values())abortDictation(node);}}
  new MutationObserver(view).observe(root,{attributes:true,attributeFilter:['data-dream-view']});
  window.addEventListener('resize',()=>{if(maximized)layout(maximized,{x:6,y:6,width:viewport.clientWidth-12,height:viewport.clientHeight-12});dimensions();for(const node of nodes.values())fit(node);});
  new ResizeObserver(() => { if(maximized)layout(maximized,{x:6,y:6,width:viewport.clientWidth-12,height:viewport.clientHeight-12}); else dimensions(); }).observe(viewport);
  page.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(source){source=null;markSource();message('Connection selection cancelled.');}else if(!activity.hidden){setChannelOpen(false);}else if(!groupPanel.hidden){groupPanel.hidden=true;groupButton.focus();}});
  setInterval(()=>{if(live()&&!pending)refresh();},4000);
  window.addEventListener('beforeunload',()=>{handoffFlow?.dispose();for(const n of nodes.values()){abortDictation(n);n.sessionDrawer?.dispose();n.activityView?.dispose();n.socket?.close();}});
  view();
})();
