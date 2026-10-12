/* Read-only presentation of Dream's recorded events. Never interprets PTY output. */
(() => {
  'use strict';
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const stringify = value => typeof value === 'string' ? value : JSON.stringify(value ?? '', null, 2);
  const empty = value => value == null || (typeof value === 'string' && !value.trim()) || (typeof value === 'object' && Object.keys(value).length === 0);
  function describe(event) {
    const d = event.data, object = d && typeof d === 'object' ? d : {};
    const labels = {user:'You',text_delta:'Assistant',thinking_delta:'Reported thinking',assistant_done:'Assistant completed',tool_use:'Tool',tool_result:'Tool result',system:'System',error:'Error',result:'Result',turn_start:'Turn started',turn_end:'Turn ended',turn_timing:'Turn timing',background_work:'Background work',council_activity:'Council activity',plan:'Plan'};
    let label = labels[event.kind] || event.kind, body = stringify(d), type = event.kind;
    if (type === 'user') body = typeof d === 'string' ? d : stringify(object.text ?? d);
    if (type === 'tool_use') { label += ': ' + (object.name || 'unnamed'); body = stringify(object.input ?? d); }
    if (type === 'tool_result') { label += object.is_error ? ' · error' : ''; body = stringify(object.content ?? d); }
    if (type === 'agent_activity') {
      label = 'Agent ' + (object.agent || 'unknown') + ' · ' + ({thinking_report:'Reported thinking',thinking_delta:'Reported thinking',tool_use:'Tool',tool_result:'Tool result'}[object.kind] || object.kind || 'activity');
      body = stringify(object.text ?? object.data ?? d);
    }
    if (body.length > 16000) body = body.slice(0,16000) + '\n… Display truncated.';
    return {label,body,type,message:['user','text_delta'].includes(type)};
  }
  function create({nodeId, token, userLabel='You', canPoll, onAvailability = () => {}, onSnapshot = () => {}, onError = () => {}}) {
    const normalizeLabel=value=>(typeof value==='string'?value.trim().slice(0,60).trim():'')||'You';
    userLabel=normalizeLabel(userLabel);
    const element = el('section',null,'ds-event-panel'); element.setAttribute('aria-label','Recorded Dream activity'); element.hidden=true;
    const status = el('p',null,'ds-event-status'); status.setAttribute('role','status');
    const statusText=el('span','Loading activity…');status.append(statusText);
    const retry = el('button','Retry activity','ds-event-retry'); retry.type='button'; retry.hidden=true;
    const list = el('div',null,'ds-event-list'); list.tabIndex=0; list.setAttribute('aria-label','Recorded activity entries');
    element.append(status,list);
    let layout='terminal', visible=false, active=false, disposed=false, timer=null, controller=null, generation=null, entries=[], staleView=null, staleScrollIntent=false;
    let available=null,busy=false;
    function getState() {return {hasContent:entries.length>0,available,busy,stale:element.dataset.stale==='true'};}
    function notify() {element.dataset.hasContent=String(entries.length>0);element.dataset.busy=String(busy);onAvailability(available,getState());}
    element.dataset.hasContent='false';element.dataset.busy='false';
    function capture() {
      const anchor=entries.find(entry=>entry.element.offsetTop+entry.element.offsetHeight>=list.scrollTop);
      return {nearBottom:list.scrollHeight-list.clientHeight-list.scrollTop<28,oldScroll:list.scrollTop,anchor,anchorOffset:anchor?anchor.element.offsetTop-list.scrollTop:0};
    }
    // Only explicit navigation invalidates the saved outage anchor. Native
    // scroll events also fire when the unavailable layout clamps the viewport.
    function deliberateScroll() {
      if(element.dataset.stale==='true'){staleView=null;staleScrollIntent=true;}
    }
    list.addEventListener('wheel',event=>{if(event.deltaY)deliberateScroll();},{passive:true});
    list.addEventListener('touchmove',deliberateScroll,{passive:true});
    list.addEventListener('keydown',event=>{
      if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(event.key) || (event.key===' ' && event.target===list)) deliberateScroll();
    });
    list.addEventListener('pointerdown',event=>{
      const bounds=list.getBoundingClientRect();
      if(event.target===list && event.clientX>=bounds.left+list.clientLeft+list.clientWidth) deliberateScroll();
    });
    function defaults(entry) { if (!entry.touched) entry.element.open = layout === 'conversation' && entry.record.message; }
    function render(snapshot) {
      if (!snapshot || typeof snapshot.available !== 'boolean' || !Array.isArray(snapshot.events) || typeof snapshot.generation !== 'string') throw Error('Unreadable activity response.');
      if(snapshot.events.some(event=>!event || typeof event.id!=='string' || typeof event.kind!=='string')) throw Error('Unreadable activity entries.');
      if(!snapshot.available) {
        if(!staleView && !staleScrollIntent)staleView=capture();
        element.dataset.stale='true'; available=false;busy=false;retry.hidden=true;retry.remove();
        status.title=snapshot.reason||'';
        statusText.textContent=/^Native CLI\b/i.test(snapshot.reason||'')?'Native activity is available in the terminal.':(snapshot.reason||'Activity is unavailable.').replace(/\s*Use (?:its|the) terminal\.?\s*$/i,'')+' Use the terminal.';
        if(entries.length)statusText.textContent+=' Previous records are stale.';
        notify();
        return;
      }
      const reset = generation !== snapshot.generation;
      const positionBefore=staleView||capture();staleView=null;staleScrollIntent=false;
      if(reset) {entries=[];list.replaceChildren();list.scrollTop=0;}
      generation=snapshot.generation;element.dataset.stale='false';available=true;retry.hidden=true;retry.remove();status.title='';
      // Lifecycle events describe readiness without adding empty transcript rows.
      const lifecycle=snapshot.events.filter(event=>event.kind==='turn_start'||event.kind==='turn_end').at(-1);
      busy=lifecycle?.kind==='turn_start';
      const {nearBottom,oldScroll,anchor,anchorOffset}=positionBefore;
      const selected=window.getSelection(); const selecting=selected && !selected.isCollapsed && list.contains(selected.anchorNode);
      const next=[], byId=new Map(entries.map(entry=>[entry.id,entry]));
      for (const event of snapshot.events.slice(-200)) {
        if (!event || typeof event.kind !== 'string') continue;
        if(['turn_start','turn_end'].includes(event.kind) && empty(event.data)) continue;
        const record=describe(event);
        // Stable source IDs survive coalescing and eviction; generation above
        // prevents expansion or stale text crossing child-session lifetimes.
        const id=String(event.id);
        let entry=byId.get(id);
        if(entry && entry.record.type!==record.type) entry=null;
        if(entry) byId.delete(id);
        else {
          const details=el('details',null,'ds-event'), summary=el('summary'), label=el('span',null,'ds-event-label'), preview=el('span',null,'ds-event-preview'), body=el('pre');
          summary.append(label,preview); details.append(summary,body);
          entry={element:details,label,preview,body,touched:false};
          summary.addEventListener('click',()=>{entry.touched=true;});
        }
        entry.id=id; entry.record=record; entry.element.dataset.kind=record.type;
        if (entry.body.textContent!==record.body) entry.body.textContent=record.body;
        entry.label.textContent=record.type==='user'?userLabel:record.label;
        entry.preview.textContent=record.body.replace(/\s+/g,' ').slice(0,100);
        defaults(entry); next.push(entry);
      }
      const kept=new Set(next);
      for(const entry of entries) if(!kept.has(entry)) entry.element.remove();
      let position=list.firstChild;
      for(const entry of next) { if(entry.element!==position) list.insertBefore(entry.element,position); position=entry.element.nextSibling; }
      entries=next;
      statusText.textContent=entries.length ? (busy?'Working…':'Recorded activity')+(snapshot.trimmed || snapshot.events.length>200 ? ' · earlier details omitted.' : '') : busy?'Working… Activity will appear here.':'No activity yet.';
      if ((nearBottom || reset) && !selecting) list.scrollTop=list.scrollHeight;
      else if(anchor && kept.has(anchor)) list.scrollTop=anchor.element.offsetTop-anchorOffset;
      else list.scrollTop=oldScroll;
      notify();
    }
    function schedule() { clearTimeout(timer); if(active && !disposed) timer=setTimeout(poll,1000); }
    async function poll() {
      clearTimeout(timer);
      if(!active || disposed || controller) return;
      if(!canPoll()) {schedule();return;}
      const current=controller=new AbortController(); const timeout=setTimeout(()=>current.abort(),8000);
      try {
        const response=await fetch('/api/deepsleep/activity?node='+encodeURIComponent(nodeId),{headers:{'X-Dream-Token':token},cache:'no-store',signal:current.signal});
        if(!response.ok) throw Error('Activity could not load ('+response.status+').');
        const snapshot=await response.json();
        if(active && !disposed && canPoll()) {render(snapshot);onSnapshot(snapshot);}
      } catch(error) {
        if(active && !disposed) {
          if(!staleView && !staleScrollIntent)staleView=capture();
          statusText.textContent=(error.name==='AbortError'?'Activity read timed out.':error.message)+(entries.length?' Previous records are stale.':'')+' Use the terminal.';status.title='';element.dataset.stale='true';available=false;busy=false;retry.hidden=false;if(!retry.isConnected)status.append(retry);notify();onError(statusText.textContent);
        }
      } finally { clearTimeout(timeout); controller=null; schedule(); }
    }
    retry.onclick=()=>poll();
    return {element,getState,
      setUserLabel(value) {userLabel=normalizeLabel(value);for(const entry of entries)if(entry.record.type==='user')entry.label.textContent=userLabel;},
      setLayout(value) {layout=value;element.dataset.layout=value;for(const entry of entries) defaults(entry);},
      setVisible(value) {visible=value;element.hidden=!value;},
      setActive(value) {if(active===value)return;active=value;if(value)poll();else{clearTimeout(timer);controller?.abort();}},
      dispose() {disposed=true;visible=false;active=false;clearTimeout(timer);controller?.abort();}
    };
  }
  window.DreamActivity=Object.freeze({create});
})();
