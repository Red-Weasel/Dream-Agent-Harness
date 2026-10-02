/* Nested Dream (DREAM-188, ADR-068): a view over the session's own events. The orchestrator is the lead; the workers
   are its task sub-agents on an OpenAI-compatible engine, one lane per agent_activity run_id, in spawn order. The
   state is rebuilt from the chat's dream:event hook, live and on history replay: nothing is fetched twice, and no
   lane exists that an event did not name. Display only: nothing here runs a tool or answers a permission.
   The All-agents drawer (nested-drawer.js, DREAM-195) and the layout (nested-layout.js, DREAM-196) read this state
   through window.DreamNested; the layout and the pins are kept under localStorage dream.nested.* when storage allows. */
(() => {
  'use strict';
  if(!COMPANION) return;
  const root=document.documentElement, $=id=>document.getElementById(id);
  const el=(tag,text,attrs={})=>{const n=document.createElement(tag);if(text!==null)n.textContent=text;for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);return n;};
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const WORD={working:'Working',verifying:'Verifying',needs:'Needs you',queued:'Queued',pausing:'Pausing',paused:'Paused',stopping:'Stopping',done:'Done',failed:'Failed',stopped:'Stopped',unknown:'Outcome unknown'};
  // agent_activity status -> lane state. queued: Queued (pewter, DREAM-191); running: Working (green); completed: Done
  // (pewter); failed: Failed (red); unknown: Outcome unknown (pewter: it ended, and whether it finished is not known --
  // never Failed); interrupted: Stopped (red). Anything else (`waiting` for another request on the engine) leaves the
  // state as it was; the row's text is the lane's headline either way.
  const STATE={queued:'queued',running:'working',pausing:'pausing',paused:'paused',stopping:'stopping',completed:'done',complete:'done',failed:'failed',unknown:'unknown',interrupted:'stopped'};
  // P5 (DREAM-192): pausing (pewter word, still running to its round boundary), paused (pewter) and stopping are live
  // states; only a queued or a working worker has a pause to offer.
  const FINAL=new Set(['done','failed','stopped','unknown']), PAUSABLE=new Set(['queued','working']);
  // The engine's lanes (DREAM-191): {served, busy, queued} from hello.lanes and then from every `lanes` event.
  // served >= 1, busy <= served, queued a count when present: anything else is a malformed event, ignored (never read as 0).
  const lanesOf=v=>{ if(!v||typeof v!=='object') return null;
    const served=count(v.served), busy=count(v.busy), queued=v.queued==null?0:count(v.queued);
    return served!==null&&served>=1&&busy!==null&&busy<=served&&queued!==null?{served,busy,queued}:null; };
  // Providers whose sessions report no worker activity (ADR-068): the coding CLIs emit none. The labels hello.provider
  // carries are the backends' own: cli_agent.py CodexAdapter.label, grok_adapter.py GrokAdapter.label,
  // gemini_adapter.py GeminiAdapter.label (not the providers table's).
  const SILENT=new Set(['ChatGPT · Codex','xAI · Grok','Gemini · CLI']);
  // A Claude-led session (anthropic.py provider_label): the Claude SDK runs its sub-agents, shown as read-only cards
  // whose run ids are "claude-<the lead's call id>" (DREAM-212), and its local helpers (delegate_local, DREAM-213) are
  // Dream's own workers, full cards. The backend refuses every control on the SDK's workers in these words
  // (WORKER_REFUSAL, WORKERS_REFUSAL); both backends word a tool call whose result never came as UNKNOWN_TOOL.
  const CLAUDE='Claude · Anthropic', sdk=l=>l.id.startsWith('claude-');
  const SDK_ONE='This worker runs inside the Claude SDK; Dream cannot stop, pause or message it.';
  const SDK_ALL='These workers run inside the Claude SDK; Dream cannot stop, pause or message them.';
  const EMPTY_CLAUDE="Claude's sub-agents appear here as read-only cards once one runs, and its local helpers (delegate_local) as full cards you can pause, stop and message.";
  const UNKNOWN_TOOL='Tool observation interrupted; outcome is unknown.';
  const PAUSE_ALL='Pause every queued or working worker at its next round boundary';
  const PAUSE_ALL_CLAUDE="Pause every queued or working local worker at its next round boundary; Claude's own sub-agents keep running.";
  const READ_AGAIN='\n\n[Read the request above again:]\n';
  const RESUMED='Resumed by you.';   // the P5 contract's resume row: for a worker still waiting for a lane it starts nothing
  const MAX_LANES=64, MAX_ROWS=100, MAX_LOG=400, MAX_ROUNDS=200, MAX_TEXT=65536, MAX_MESSAGES=80, TAIL=700;
  const grow=(s,add)=>s.length>=MAX_TEXT?s:(s+String(add??'')).slice(0,MAX_TEXT);
  const tail=(s,n)=>s.length>n?'…'+s.slice(s.length-n):s;
  const count=v=>Number.isInteger(v)&&v>=0?v:null;
  const preview=v=>{const t=typeof v==='string'?v:JSON.stringify(v??{});return t.length>120?t.slice(0,120)+'…':t;};
  const PIN='<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M6 2.5h4l-.5 4 2 2.5h-7l2-2.5zM8 9v4.5"/></svg>';

  // --- what the owner keeps between visits: sizes, side and pins under dream.nested.*; never required, and every
  //     read and write is guarded, so the page is the same with storage refused or throwing --------------------------
  const store={
    get(key,fallback){try{const v=localStorage.getItem('dream.nested.'+key);return v==null?fallback:JSON.parse(v);}catch(e){return fallback;}},
    set(key,value){try{localStorage.setItem('dream.nested.'+key,JSON.stringify(value));}catch(e){}},
    remove(key){try{localStorage.removeItem('dream.nested.'+key);}catch(e){}},
    clear(){try{for(const k of Object.keys(localStorage)) if(k.startsWith('dream.nested.')) localStorage.removeItem(k);}catch(e){}}};
  const LAYOUT={orchW:'orch-w',pinSplit:'pin-split',topH:'top-h',side:'side'};
  const number=(v,lo,hi)=>typeof v==='number'&&Number.isFinite(v)&&v>=lo&&v<=hi?v:null;
  const loadLayout=()=>({orchW:number(store.get('orch-w',null),0,100000),pinSplit:number(store.get('pin-split',null),25,75),
                         topH:number(store.get('top-h',null),30,80),side:store.get('side','left')==='right'?'right':'left'});
  let L=loadLayout();
  function setLayout(key,value){
    if(!(key in LAYOUT)) return false;
    if(key==='side'){ L.side=value==='right'?'right':'left'; store.set('side',L.side); return true; }
    const v=key==='orchW'?number(value,0,100000):key==='pinSplit'?number(value,25,75):number(value,30,80);
    L[key]=v; if(v==null) store.remove(LAYOUT[key]); else store.set(LAYOUT[key],v); return true;
  }
  // The owner's presentation choices (Customize, DREAM-200): text size, which pips show, the card width, thinking on cards.
  const PREFS={fz:'fz',pips:'pips',cardw:'cardw',cardThink:'card-think'};
  const loadPrefs=()=>({fz:number(store.get('fz',1),0.5,2)??1,pips:store.get('pips','all')==='active'?'active':'all',
                        cardw:number(store.get('cardw',320),200,600)??320,cardThink:store.get('card-think',true)!==false});
  let P=loadPrefs();
  function setPref(key,value){
    if(!(key in PREFS)) return false;
    const v=key==='fz'?number(value,0.5,2):key==='cardw'?number(value,200,600):key==='pips'?(value==='active'?'active':'all'):value!==false;
    if(v==null) return false;
    P[key]=v; store.set(PREFS[key],v); applyPrefs(); schedule(); return true;
  }

  // --- the state, rebuilt from events ------------------------------------------------------------------------------
  // pins: the two windows' run ids, kept per session; a new worker takes an empty slot, the owner pins and unpins.
  // pinsSaved: the owner has chosen (a saved record, or a pin/unpin in this page); then a replayed lane never takes a
  // slot by itself, an empty slot stays the owner's, and only a live new worker fills one.
  const fresh=()=>({lanes:new Map(),order:[],pins:[null,null],pinsSaved:false,session:'',provider:'',engine:null,steeringTarget:null,planTitle:'',lastUser:'',
                    messages:[],current:null,tools:new Map(),turn:false,turnNo:0,open:new Set(),asks:new Map()});
  let S=fresh(), focusAfter=null;
  // P7 (DREAM-194): the permission requests waiting for the owner, in arrival order, by id. A worker's names its run
  // (run_id, agent) and shows that worker as Needs you until permission_done; the lead's own names neither. They are not
  // in the replayed history: a (re)connecting view is sent each waiting request again after it. `seen` keeps when this
  // page first saw each one, so a reconnect does not restart its waiting clock (a reload does: the payload has no time).
  const seen=new Map();
  let askSeq=0, stackNote='';
  const whole=new Set();   // requests whose whole reason the owner opened (a short window shows its first two lines)
  let moreShown=null;      // the request whose toggle was just used: shown after the render moves it
  function ask(d){
    if(!d||typeof d.id!=='string'||!d.id||S.asks.has(d.id)) return;
    const run=typeof d.run_id==='string'&&d.run_id?cap(d.run_id,100):null;
    const choices=d.choices&&typeof d.choices==='object'&&!Array.isArray(d.choices)?Object.entries(d.choices).map(([v,t])=>[v,String(t??v)]):[];
    if(!seen.has(d.id)){ seen.set(d.id,Date.now()); if(seen.size>200) seen.delete(seen.keys().next().value); }
    S.asks.set(d.id,{k:++askSeq,id:d.id,run,agent:run&&typeof d.agent==='string'?cap(d.agent,100):'',tool:String(d.tool||'tool').split('__').pop().slice(0,150),
                     args:preview(d.input),reason:String(d.reason??'').slice(0,4000),choices,at:seen.get(d.id),busy:false,answered:'',closed:false,note:''});
    stackNote='';
  }
  const asksOf=l=>[...S.asks.values()].filter(a=>a.run===l.id);
  // What a lane shows: Needs you while a request of its own waits, unless it has already finished.
  const stateOf=l=>!FINAL.has(l.state)&&asksOf(l).length?'needs':l.state;
  const waited=at=>{ const m=Math.floor((Date.now()-at)/60000); return m<1?'waiting <1m':`waiting ${m}m`; };
  const clockHTML=l=>{ const a=stateOf(l)==='needs'?asksOf(l)[0]:null; return a?`<span class="nd-wait nd-num" data-since="${a.at}">${waited(a.at)}</span>`:''; };
  const loadPins=()=>{const v=S.session?store.get('pins.'+S.session,null):null;const ok=Array.isArray(v)&&v.length===2&&v.every(x=>x===null||typeof x==='string');S.pinsSaved=ok;return ok?v.slice():[null,null];};
  const savePins=()=>{ S.pinsSaved=true; if(S.session) store.set('pins.'+S.session,S.pins); };
  // After a replay: a saved id the replay never claimed (a trimmed history) gives its window to the first unpinned worker.
  function settlePins(){
    let changed=false;
    for(let i=0;i<2;i++){ const id=S.pins[i]; if(id&&!S.lanes.has(id)){ S.pins[i]=null; const next=S.order.find(l=>!S.pins.includes(l.id)); if(next) S.pins[i]=next.id; changed=true; } }
    if(changed){ savePins(); schedule(); }
  }
  function resetLayout(){ store.clear(); L=loadLayout(); P=loadPrefs(); applyPrefs(); S.pins=[null,null]; S.pinsSaved=false; for(const l of S.order){ const i=S.pins.indexOf(null); if(i<0) break; S.pins[i]=l.id; } schedule(); }
  // The transcript of one run, in order and bounded: what the drawer's detail shows in full.
  function entry(l,e){ if(l.log.length>=MAX_LOG){ l.log.shift(); l.omitted++; } l.log.push(e); return e; }
  // One round per request_index: its text and reasoning, streamed piece by piece (DREAM-190) or whole (the plain path).
  // A row that names no request joins the round that is open, and a response row adopts an unnumbered open round.
  function roundFor(l,index){
    let r=null; const last=l.rounds[l.rounds.length-1];
    if(index!==null){ r=l.rounds.find(x=>x.index===index)||null; if(!r&&last&&last.live&&last.index===null){ last.index=index; r=last; } }
    else if(last&&last.live) r=last;
    if(!r){ if(l.rounds.length>=MAX_ROUNDS) l.rounds.shift(); r={index,text:'',thinking:'',live:true,textEntry:null,thinkEntry:null}; l.rounds.push(r); }
    return r;
  }
  const written=l=>l.rounds.map(r=>r.text).filter(Boolean).join('\n\n');    // what the worker has written, every round
  const thinking=l=>l.rounds.map(r=>r.thinking).filter(Boolean).join('\n\n');
  // What agent_activity.py keeps for the replay, mirrored live so both name the same lanes: a row needs a run and an
  // agent, and either is cut at 100 characters with the same mark.
  const cap=(s,n)=>s.length>n?s.slice(0,n)+'\n[Display truncated]':s;
  function lane(d,replay){
    if(!d||typeof d.run_id!=='string'||!d.run_id||typeof d.agent!=='string'||!d.agent) return;
    const id=cap(d.run_id,100);
    let l=S.lanes.get(id);
    if(!l){
      if(S.lanes.size>=MAX_LANES) return;
      l={id,n:S.lanes.size+1,agent:cap(d.agent,100),state:'working',text:'',rounds:[],rows:[],tools:new Map(),
         log:[],omitted:0,request:'',model:'',context:null,usage:null,duration:null,busy:false,note:'',seq:0,queued:false,pending:0,msgs:0,turn:S.turnNo};
      S.lanes.set(id,l); S.order.push(l);
      // An empty window takes a new worker -- and that is kept -- unless this is a replay under the owner's saved pins.
      if(!(replay&&S.pinsSaved)){ const slot=S.pins.indexOf(null); if(slot>=0){ S.pins[slot]=l.id; if(!replay) savePins(); } }
    }
    // Facts a row may carry from the first request on (DREAM-189): shown when present, never guessed.
    if(typeof d.model==='string'&&d.model) l.model=d.model.slice(0,160);
    const c=d.context;
    if(c&&typeof c==='object'&&count(c.used)!==null&&count(c.window)!==null&&c.window>0) l.context={used:c.used,window:c.window};
    const kind=d.kind, index=count(d.request_index), request=index===null?'Request':'Request '+index;
    if(kind==='status'){
      if(d.status==='message_received'||d.status==='message_undelivered'){   // P6: an owner's message taken, or not
        const text=typeof d.text==='string'?d.text.slice(0,1000):'';
        l.pending=Math.max(0,l.pending-1); l.msgs++; if(text) l.text=text;
        entry(l,{kind:'message',text}); return;
      }
      let st=STATE[d.status];
      // A queued worker is still waiting for an engine lane until a running row other than the owner's resume starts it.
      if(st==='queued') l.queued=true; else if(st==='working'&&l.queued){ if(d.text===RESUMED) st='queued'; else l.queued=false; }
      if(st){ l.state=st; l.note=''; l.seq++; }   // the server's word on the worker outdates a refusal shown on it, and a control answer still in flight
      if(typeof d.text==='string'&&d.text) l.text=d.text.slice(0,1000);
      entry(l,{kind:'status',state:st||l.state,text:typeof d.text==='string'?d.text.slice(0,1000):''});
      if(st&&FINAL.has(st)){
        l.pending=0;   // nothing waits for a worker that has ended: its undelivered rows came before this one
        for(const r of l.tools.values()) if(r.badge==='Requested') r.badge=st==='stopped'?'Interrupted':'No result reported';
        l.tools.clear();
      }
    } else if(kind==='request'){ l.queued=false; roundFor(l,index).live=true; l.request=request+' · waiting for the reply'; entry(l,{kind:'request',text:l.request}); }
    else if(kind==='response'){
      l.request=request+' · reply received';
      if(count(d.duration_ms)!==null) l.duration=d.duration_ms;
      const u=d.usage;
      l.usage=u&&typeof u==='object'?{prompt:count(u.prompt_tokens),completion:count(u.completion_tokens)}:null;
      entry(l,{kind:'response',text:l.request});
      // The row's whole text settles the round: kept when it is what streamed, else it replaces it; never shown twice.
      // The server caps that text ("[Display truncated]") while the deltas were the whole reply: a capped prefix of
      // what streamed keeps the streamed words.
      const r=roundFor(l,index), t=typeof d.text==='string'?d.text:'', mark='\n[Display truncated]';
      const capped=t.endsWith(mark)&&r.text.startsWith(t.slice(0,-mark.length));
      if(t&&t!==r.text&&!capped) r.text=t.slice(0,MAX_TEXT);
      if(r.text){ if(r.textEntry) r.textEntry.text=r.text; else r.textEntry=entry(l,{kind:'text',text:r.text}); }
      r.live=false;
    }
    else if(kind==='thinking_report'){
      if(typeof d.text==='string'&&d.text){ const r=roundFor(l,index); r.thinking=grow(r.thinking?r.thinking+'\n\n':'',d.text); r.thinkEntry=null; entry(l,{kind:'thinking',text:d.text.slice(0,MAX_TEXT)}); }
    }
    else if(kind==='thinking_delta'){
      const t=d.text??d.delta;
      if(typeof t==='string'&&t){ const r=roundFor(l,index); r.thinking=grow(r.thinking,t); if(!r.thinkEntry) r.thinkEntry=entry(l,{kind:'thinking',text:''}); r.thinkEntry.text=r.thinking; }
    }
    else if(kind==='text_delta'){
      const t=d.text??d.delta;
      if(typeof t==='string'&&t){ const r=roundFor(l,index); r.text=grow(r.text,t); if(!r.textEntry) r.textEntry=entry(l,{kind:'text',text:''}); r.textEntry.text=r.text; }
    }
    else if(kind==='tool_use'){
      if(l.rows.length>=MAX_ROWS) return;
      const t=d.data||{}, row={id:String(t.id||''),name:String(t.name||'tool').slice(0,150),args:preview(t.input),badge:'Requested',out:''};
      l.rows.push(row); if(row.id) l.tools.set(row.id,row); entry(l,{kind:'tool',row});
    }
    else if(kind==='tool_result'){
      const t=d.data||{}, row=l.tools.get(String(t.id||''));
      if(row){ const out=String(t.content??''); row.badge=out===UNKNOWN_TOOL?WORD.unknown:t.is_error?'Failed':'Returned'; row.out=out.slice(0,2000); l.tools.delete(row.id); }
    }
  }
  const pinned=()=>S.pins.map(id=>id?S.lanes.get(id)||null:null);
  function pin(id,slot){
    if(!S.lanes.has(id)) return false;
    const was=S.pins.indexOf(id);
    const at=slot===0||slot===1?slot:was>=0?was:S.pins.indexOf(null)>=0?S.pins.indexOf(null):1;
    if(was>=0) S.pins[was]=null;
    S.pins[at]=id; savePins(); schedule(); return true;
  }
  function unpin(id){ const i=S.pins.indexOf(id); if(i<0) return false; S.pins[i]=null; savePins(); schedule(); return true; }
  // The owner's words, once: a guided task arrives wrapped, and a "Read ×2" message is stored doubled.
  function said(d){const w=d&&typeof d==='object'?d:null;const s=String((w?w.text:d)||'');const i=s.indexOf(READ_AGAIN);return i>=0?s.slice(0,i):s;}
  function message(role,extra={}){if(S.messages.length>=MAX_MESSAGES)S.messages.shift();const m={role,text:'',thinking:'',...extra};S.messages.push(m);return m;}
  function onEvent(m,replay){
    const d=m.data;
    switch(m.kind){
      case 'hello': S.provider=String(d?.provider||''); S.engine=lanesOf(d?.lanes); S.steeringTarget=d?.steering_target||null; S.session=String(d?.session_id||''); if(!S.order.length) S.pins=loadPins(); break;
      case 'history':
        S=Object.assign(fresh(),{provider:S.provider,engine:S.engine,steeringTarget:S.steeringTarget,session:S.session,open:S.open}); S.pins=loadPins();
        setTimeout(settlePins,0);   // the chat replays the retained events synchronously after this one
        break;
      case 'lanes': { const v=lanesOf(d); if(!v) return; S.engine=v; break; }   // not in the history: hello carries the current count
      case 'steering_ready': S.steeringTarget=d||null; break;
      case 'user': { const text=said(d); S.lastUser=text; S.current=null; message('you',{text}); break; }
      case 'turn_start': S.turn=true; S.turnNo++; break;   // a new turn: the last one's failures leave the stack
      case 'text_delta': S.turn=true; if(!S.current) S.current=message('dream'); S.current.text=grow(S.current.text,d); break;
      case 'thinking_delta': S.turn=true; if(!S.current) S.current=message('dream'); S.current.thinking=grow(S.current.thinking,d); break;
      case 'tool_use': {
        S.turn=true; S.current=null;
        const t=d||{}, row=message('tool',{id:String(t.id||''),name:String(t.name||'tool').split('__').pop().slice(0,150),args:preview(t.input),badge:'Running'});
        if(row.id) S.tools.set(row.id,row);
        break;
      }
      case 'tool_result': { const t=d||{}, row=S.tools.get(String(t.id||'')); if(row){ row.badge=t.is_error?'Failed':'Completed'; S.tools.delete(row.id); } break; }
      case 'assistant_done': S.current=null; break;
      case 'plan': S.planTitle=String(d?.title||'').slice(0,300); break;
      case 'result': case 'turn_end': case 'error':
        S.turn=false; S.current=null;
        if(m.kind==='turn_end') S.steeringTarget=null;   // like the chat: a steer target dies with its turn
        for(const r of S.tools.values()) if(r.badge==='Running') r.badge=m.kind==='turn_end'&&d?.interrupted?'Interrupted':'No result returned';
        S.tools.clear();
        // A worker still live when its turn ends has no report coming; a later terminal row still overrides this.
        for(const l of S.order) if(!FINAL.has(l.state)){
          l.state='stopped'; l.text='The turn ended without a final report from this worker'; l.note=''; l.pending=0;
          for(const r of l.tools.values()) if(r.badge==='Requested') r.badge='No result reported';
          l.tools.clear(); entry(l,{kind:'status',state:'stopped',text:l.text});
        }
        break;
      case 'agent_activity': lane(d,replay); break;
      case 'permission': ask(d); break;
      case 'permission_done': {
        const a=S.asks.get(String(d?.id??'')); if(!a) return;
        S.asks.delete(a.id); seen.delete(a.id);
        if(a.note) stackNote=a.note;   // a refusal the owner has not read yet outlives its item
        break;
      }
      default: return;
    }
    schedule();
  }
  window.addEventListener('dream:event',e=>{try{onEvent(e.detail.m,!!e.detail.replay);}catch(err){console.error(err);}});

  // --- the page ------------------------------------------------------------------------------------------------------
  const page=el('section',null,{id:'dream-nested-page',class:'nd','aria-label':'Nested Dream'});page.hidden=true;
  page.innerHTML=`
    <div class="nd-top" role="region" aria-label="Workers at a glance">
      <span class="nd-title">Nested Dream</span>
      <div class="nd-pips" id="nd-pips" aria-label="Every worker's state"></div>
      <div class="nd-attn" id="nd-attn" aria-live="polite"></div>
      <button type="button" class="nd-pill" id="nd-drawer-btn" data-fk="drawer-btn" aria-expanded="false" aria-controls="nd-drawer">All agents</button>
      <button type="button" class="nd-pill" id="nd-pause-all" data-fk="pause-all" disabled title="${PAUSE_ALL}">Pause all</button><span class="nd-sr" id="nd-pause-why"></span>
      <span class="nd-note nd-ctl-note" id="nd-ctl-note" role="status" aria-live="polite"></span><div class="nd-sr" id="nd-live" role="alert"></div>
      <span class="nd-spacer"></span>
      <div class="nd-grp" role="group" aria-label="Worker cards">
        <button type="button" class="nd-iconbtn" id="nd-prev" data-fk="prev" aria-label="Scroll the worker cards left" title="Scroll left">‹</button>
        <button type="button" class="nd-iconbtn" id="nd-next" data-fk="next" aria-label="Scroll the worker cards right" title="Scroll right">›</button>
        <button type="button" class="nd-pill" id="nd-auto" data-fk="auto" aria-pressed="false" aria-label="Auto-scroll the worker cards" title="Auto-scroll: the cards drift by and hold still under the pointer or the focus">Auto-scroll</button>
        <button type="button" class="nd-pill" id="nd-reset" data-fk="reset" title="Back to the default sizes, side, pins and Customize choices">Reset layout</button>
        <button type="button" class="nd-pill" id="nd-custom-btn" data-fk="custom-btn" aria-expanded="false" aria-controls="nd-custom">Customize</button>
      </div>
      <span class="nd-count nd-num" id="nd-count"></span>
      <span class="nd-lanes nd-num" id="nd-lanes" title="The engine's lanes: how many of this session's workers can run at once"></span>
      <span class="nd-capw" id="nd-cap" hidden><span class="nd-cap-l" id="nd-cap-l">Agents</span><span class="nd-seg" id="nd-cap-grp" role="radiogroup" aria-label="Agents per reply" aria-describedby="nd-cap-hint" title="Takes effect from the orchestrator's next reply. Workers beyond the engine's lanes queue."></span><span class="nd-sr" id="nd-cap-hint">Takes effect from the orchestrator's next reply. Workers beyond the engine's lanes queue.</span><span class="nd-note nd-cap-note" id="nd-cap-note" role="status" aria-live="polite"></span></span>
    </div>
    <div class="nd-app">
      <aside class="nd-orch" aria-label="Orchestrator">
        <div class="nd-orch-body" id="nd-orch-body">
        <div class="nd-hero"><img src="/assets/dream-eclipse.png" alt="" width="1664" height="936"><button type="button" class="nd-iconbtn nd-move" id="nd-side" data-fk="side" aria-label="Move the orchestrator to the other side" title="Move to the other side">⇄</button></div>
        <div class="nd-goal-wrap" id="nd-goal-wrap"></div>
        <section class="nd-needs-sec" id="nd-needs-sec" aria-labelledby="nd-needs-h"><h2 class="nd-sec" id="nd-needs-h" tabindex="-1">Needs you <span class="nd-c nd-num" id="nd-needs-c"></span></h2><div class="nd-stack" id="nd-stack"><div class="nd-stack-first" id="nd-stack-first"></div><div class="nd-stack-rest" id="nd-stack-rest"></div></div><p class="nd-note nd-stack-note" id="nd-stack-note" role="status" aria-live="polite"></p></section>
        <div class="nd-orch-scroll" id="nd-orch-scroll"><h2 class="nd-sec">Conversation</h2><div class="nd-convo" id="nd-convo"></div></div>
        </div>
        <div class="nd-composer">
          <div class="nd-box">
            <label class="nd-sr" for="nd-compose">Message the orchestrator</label>
            <textarea id="nd-compose" rows="2" placeholder="Message the orchestrator"></textarea>
            <div class="nd-row"><span class="nd-hint" id="nd-hint"></span><button type="button" class="nd-send" id="nd-send">Send</button></div>
          </div>
          <p class="nd-status" id="nd-status" role="status" aria-live="polite"></p>
        </div>
      </aside>
      <div class="nd-handle nd-h-orch" id="nd-h-orch" role="separator" aria-orientation="vertical" aria-label="Orchestrator width" tabindex="0" data-fk="h-orch"></div>
      <div class="nd-main" id="nd-agents" role="tabpanel" aria-labelledby="nd-tab-agents">
        <div class="nd-pinned" id="nd-pinned"><div class="nd-slot" data-slot-box="0"></div><div class="nd-handle" id="nd-h-pin" role="separator" aria-orientation="vertical" aria-label="Width of the first pinned window" tabindex="0" data-fk="h-pin" hidden></div><div class="nd-slot" data-slot-box="1"></div></div>
        <div class="nd-handle nd-h-h" id="nd-h-top" role="separator" aria-orientation="horizontal" aria-label="Height of the pinned windows" tabindex="0" data-fk="h-top"></div>
        <section class="nd-belt" aria-label="Other workers"><div class="nd-track" id="nd-track"></div></section>
      </div>
      <aside class="nd-drawer" id="nd-drawer" aria-label="All agents" aria-hidden="true" inert></aside>
      <div class="nd-custom" id="nd-custom" role="dialog" aria-labelledby="nd-custom-h" hidden></div>
    </div>`;
  document.querySelector('.split').append(page);
  function applyPrefs(){ page.style.setProperty('--nd-fz',String(P.fz)); page.style.setProperty('--nd-card-w',P.cardw+'px'); }
  // At a short height the column's body scrolls above its composer, which is outside the scroll box (nested.css): the
  // first waiting request is brought into view when it becomes the first, and again on later renders unless the owner
  // scrolled the body in the last few seconds -- the layout alone never leaves a request's choices out of sight, and
  // typing in the composer never moves them (a textarea inside the scroll box scrolled it to its caret: gate P7 r4).
  // The owner has not scrolled the column when the page opens (a start at 0 held every reveal for its first 4 s), and
  // the column's scroll position moving with the layout just after a resize (its content now fits, say) is not the owner.
  const col=$('nd-orch-body'); let firstAsk=null, ownerAt=-Infinity, autoTop=-1, resizedAt=-Infinity;
  col.addEventListener('scroll',()=>{ if(Math.abs(col.scrollTop-autoTop)>1&&performance.now()-resizedAt>500) ownerAt=performance.now(); },{passive:true});
  new ResizeObserver(()=>schedule()).observe($('nd-stack-first'));   // its size moves with the column and the window: fit the stack again
  // A window that only got shorter moves the composer, not the first item: fit and reveal on every resize.
  addEventListener('resize',()=>{ resizedAt=performance.now(); schedule(); });
  function revealFirst(){
    const f=$('nd-stack-first').querySelector('.nd-ask[data-ask]'), key=f?f.dataset.ask:null, changed=key!==firstAsk; firstAsk=key;
    if(!f||(!changed&&performance.now()-ownerAt<4000)) return;
    const c=col.getBoundingClientRect(), b=f.getBoundingClientRect(), foot=page.querySelector('.nd-composer').getBoundingClientRect();
    const over=b.bottom-Math.min(c.bottom,foot.top);
    if(over>0){ col.scrollTop+=Math.min(over,Math.max(0,b.top-c.top)); autoTop=col.scrollTop; }
  }
  applyPrefs();

  // --- rendering, all of it from S ---------------------------------------------------------------------------------
  const pipHTML=(l,button)=>{const st=stateOf(l), label=`${l.n} ${esc(l.agent)}: ${WORD[st]}`;
    const q=l.queued&&st!=='queued'&&!FINAL.has(st)?' nd-q':'';   // pausing or stopping while it waits for a lane: pewter; red once stopped
    return button?`<button type="button" class="nd-pip nd-${st}${q}" data-run-id="${esc(l.id)}" data-fk="pip-${l.n}" aria-label="${label}" title="${label}">${l.n}</button>`
                 :`<span class="nd-pip nd-${st}${q}" aria-hidden="true">${l.n}</span>`;};
  function top(){
    const v=S.order, shown=P.pips==='active'?v.filter(l=>!['done','queued','paused'].includes(stateOf(l))):v;
    $('nd-pips').innerHTML=shown.map(l=>pipHTML(l,true)).join('');
    const nn=v.filter(l=>stateOf(l)==='needs').length, nf=v.filter(l=>l.state==='failed'||l.state==='stopped').length;
    $('nd-attn').innerHTML=(nn?`<button type="button" class="nd-needs" data-cycle="needs" data-fk="attn-needs"><b>${nn}</b> ${nn===1?'needs':'need'} you</button>`:'')
      +(nf?`<button type="button" class="nd-fail" data-cycle="failed" data-fk="attn-fail"><b>${nf}</b> failed</button>`:'');
    $('nd-count').textContent=v.length?`${v.length} worker${v.length===1?'':'s'}`:'';
    // Pause all as the backend answers it (anthropic.py worker_control): it pauses Dream's own workers -- on Claude, the
    // local helpers' -- never the SDK's; with none of Dream's own live and one of the SDK's live, it is refused in the
    // SDK's words, said here.
    const pa=$('nd-pause-all'), own=S.order.filter(l=>!sdk(l)), why=$('nd-pause-why');
    const sdkOnly=!own.some(l=>!FINAL.has(l.state)&&l.state!=='stopping')&&S.order.some(l=>sdk(l)&&!FINAL.has(l.state));
    pa.disabled=pauseAllBusy||!own.some(l=>PAUSABLE.has(l.state)); if(pauseAllBusy) pa.setAttribute('aria-busy','true'); else pa.removeAttribute('aria-busy');
    const title=sdkOnly?SDK_ALL:S.provider===CLAUDE?PAUSE_ALL_CLAUDE:PAUSE_ALL;   // on Claude it never reaches the SDK's workers
    if(pa.title!==title){ pa.title=title; why.textContent=sdkOnly?SDK_ALL:'';
      if(sdkOnly) pa.setAttribute('aria-describedby','nd-pause-why'); else pa.removeAttribute('aria-describedby'); }
    $('nd-ctl-note').textContent=ctlNote;
    $('nd-cap').hidden=!workerCap.shown; const grp=$('nd-cap-grp'); grp.innerHTML=capHTML();
    const hint=capHint(); if(grp.title!==hint){ grp.title=hint; $('nd-cap-hint').textContent=hint; }
    $('nd-lanes').title=S.provider==='MachX'?"The engine's lanes: how many of this session's workers can run at once"
      :S.provider===CLAUDE?"The local engine's lanes: how many of Claude's local helpers can run at once":"Parallel workers: how many of this session's workers run at once";
    if(workerCap.busy) grp.setAttribute('aria-busy','true'); else grp.removeAttribute('aria-busy');
    $('nd-cap-note').textContent=workerCap.note;
    const e=S.engine;
    $('nd-lanes').textContent=e?`lanes: ${e.busy} of ${e.served} busy${e.queued?` · ${e.queued} queued`:''}`:'';
  }
  const facts=l=>{const out=[];
    if(l.model) out.push(`<span class="nd-md" title="Model">${esc(l.model)}</span>`);
    if(l.context) out.push(`<span class="nd-ctx nd-num" title="Context used">${Math.round(100*l.context.used/l.context.window)}% · ${l.context.used.toLocaleString()}/${l.context.window.toLocaleString()}</span>`);
    if(l.usage&&l.usage.completion!==null&&l.duration>0) out.push(`<span class="nd-rate nd-num" title="Reply speed">${(l.usage.completion/(l.duration/1000)).toFixed(1)} tok/s</span>`);
    return out.join('');};
  const rowsHTML=l=>l.rows.length?`<div class="nd-tools">${l.rows.map(r=>`<div class="nd-tool"><span class="nd-tool-name">${esc(r.name)}</span><span class="nd-tool-args">${esc(r.args)}</span><span class="nd-badge">${esc(r.badge)}</span>${r.out?`<span class="nd-tool-out">${esc(tail(r.out,200))}</span>`:''}</div>`).join('')}</div>`:'';
  // Collapsed, the summary shows the latest line of the thinking (owner: streaming text visible when collapsed).
  const thinkHTML=(text,key)=>text?`<details class="nd-think" data-think="${esc(key)}"${S.open.has(key)?' open':''}><summary><span class="nd-think-label">Thinking</span><span class="nd-tail">${esc(tail(text,90))}</span></summary><p class="nd-full">${esc(text)}</p></details>`:'';
  const emptyWinHTML=slot=>`<section class="nd-win nd-win-empty" data-slot="${slot}" aria-label="Pinned window ${slot+1}: empty"><p class="nd-muted">Empty window. Pin a worker here with a card's pin, by dragging a card, or from All agents; the next worker the orchestrator starts also lands here.</p></section>`;
  // A pinned window keeps its frame and its composer (P6) while its worker stays in it, so a draft, its caret and an
  // input method's composition survive the events that re-render the header and the body around them.
  function win(box,l,slot){
    if(!l){ box.innerHTML=emptyWinHTML(slot); return; }
    let w=box.firstElementChild;
    if(!w||w.dataset.runId!==l.id){
      box.innerHTML=`<section class="nd-win" data-slot="${slot}" data-run-id="${esc(l.id)}" tabindex="-1"><div class="nd-whead"></div><div class="nd-wbody" data-body="${esc(l.id)}"></div></section>`;
      w=box.firstElementChild; w.append(sayBox(l,'w').node);
    }
    const out=written(l), st=stateOf(l);
    w.className=`nd-win nd-${st}`; w.setAttribute('aria-label',`Pinned window ${slot+1}: ${l.n} ${l.agent}, ${WORD[st]}`);
    w.querySelector('.nd-whead').innerHTML=`<div class="nd-wrow">${pipHTML(l,false)}<span class="nd-nm">${esc(l.agent)}</span>${facts(l)}<span class="nd-rt"><span class="nd-state">${WORD[st]}</span>${clockHTML(l)}${ctlHTML(l,'w')}<button type="button" class="nd-iconbtn" data-unpin="${esc(l.id)}" data-fk="unpin-${slot}" aria-pressed="true" aria-label="Unpin ${l.n} ${esc(l.agent)}" title="Unpin">${PIN}</button></span></div>
        <p class="nd-headline">${esc(l.text)}</p>${l.request?`<p class="nd-request">${esc(l.request)}</p>`:''}${noteHTML(l)}`;
    w.querySelector('.nd-wbody').innerHTML=`${rowsHTML(l)}${thinkHTML(thinking(l),'w:'+l.id)}${out?`<p class="nd-stream">${esc(out)}</p>`:''}`;
    syncSay(sayBox(l,'w'),l);
  }
  function cardText(l){
    const out=written(l), th=thinking(l);
    if(out) return `<span class="nd-out">${esc(tail(out,TAIL))}</span>`;
    if(th&&P.cardThink) return `<span class="nd-th">${esc(tail(th,TAIL))}</span>`;
    const r=l.rows[l.rows.length-1];
    if(r) return `<span class="nd-out">${esc(r.name)} · ${esc(r.badge)}${r.out?' · '+esc(tail(r.out,200)):''}</span>`;
    return l.request?`<span class="nd-out">${esc(l.request)}</span>`:'';   // the headline already carries the status text (a queued reason, once)
  }
  const cardHTML=l=>{ const st=stateOf(l); return `<article class="nd-card nd-${st}" data-run-id="${esc(l.id)}" tabindex="0" draggable="true" data-fk="card-${l.n}" aria-label="${l.n} ${esc(l.agent)}, ${WORD[st]}">
      <div class="nd-chead"><div class="nd-wrow">${pipHTML(l,false)}<span class="nd-nm">${esc(l.agent)}</span>${facts(l)}<span class="nd-rt"><span class="nd-state">${WORD[st]}</span>${clockHTML(l)}</span></div><p class="nd-headline">${esc(l.text)}</p>${noteHTML(l)}</div>
      <div class="nd-cbody">${cardText(l)}</div>
      <div class="nd-cfoot"><span class="nd-cleft">${l.context?`<span class="nd-num">${Math.round(100*l.context.used/l.context.window)}% of context</span>`:''}${ctlHTML(l,'c')}</span><button type="button" class="nd-iconbtn" data-pin="${esc(l.id)}" data-fk="pin-${l.n}" aria-label="Pin ${l.n} ${esc(l.agent)} to a window" title="Pin to a window">${PIN}</button></div>
    </article>`; };
  function emptyHTML(){
    if(SILENT.has(S.provider)) return `<div class="nd-empty" role="status"><p class="nd-provider-note">This provider does not report worker activity.</p><p>Nested Dream shows the task sub-agents of an OpenAI-compatible engine (MachX, OpenAI or xAI) and of a Claude-led session; the coding CLIs run theirs out of sight.</p><p>Next: start a session on one of those to see workers here.</p></div>`;
    if(S.provider===CLAUDE) return `<div class="nd-empty" role="status"><h2>No workers yet</h2><p>${EMPTY_CLAUDE}</p><p>Next: give it a goal in the box on the left.</p></div>`;
    return `<div class="nd-empty" role="status"><h2>No workers yet</h2><p>Workers appear here when the orchestrator delegates with its task tool during a turn.</p><p>Next: give it a goal in the box on the left.</p></div>`;
  }
  // The two windows render into their own boxes on either side of ONE resize edge that is never rebuilt: a pointer
  // drag holds its capture on that node while events keep re-rendering the windows.
  function lanes(){
    const v=S.order, box=$('nd-pinned'), slots=box.querySelectorAll('.nd-slot'), hPin=$('nd-h-pin'), track=$('nd-track');
    if(!v.length){ slots[0].innerHTML=emptyHTML(); slots[1].innerHTML=''; hPin.hidden=true; track.innerHTML=''; return; }
    hPin.hidden=false;
    const scroll=new Map();
    box.querySelectorAll('.nd-wbody').forEach(b=>scroll.set(b.dataset.body,b.scrollHeight-b.scrollTop-b.clientHeight<48?-1:b.scrollTop));
    const p=pinned();
    win(slots[0],p[0],0); win(slots[1],p[1],1);
    box.querySelectorAll('.nd-wbody').forEach(b=>{const s=scroll.get(b.dataset.body);b.scrollTop=s===undefined||s===-1?b.scrollHeight:s;});
    const rest=v.filter(l=>!S.pins.includes(l.id)), left=track.scrollLeft;
    track.innerHTML=rest.length?rest.map(cardHTML).join(''):'<p class="nd-muted nd-belt-note">Every worker is pinned above. From the third worker on, they appear here.</p>';
    track.scrollLeft=left;
  }
  // A long goal reads as normal text cut after four lines; the reader's Show all / Show less choice survives re-renders
  // and resets for a new goal (the owner's 2026-09-30 screenshots: a long prompt filled the column as a bold heading).
  let goalOpen=false, goalSeen='';
  function orch(){
    const goal=S.planTitle||S.lastUser;
    if(goal!==goalSeen){goalSeen=goal;goalOpen=false;}
    const long=Boolean(goal)&&(goal.length>280||goal.split('\n').length>4);
    $('nd-goal-wrap').innerHTML=goal?`<h1 class="nd-goal${long&&!goalOpen?' nd-goal-cut':''}" id="nd-goal">${esc(goal)}</h1>${long?`<button type="button" class="nd-pill nd-goal-more" aria-controls="nd-goal" aria-expanded="${goalOpen}">${goalOpen?'Show less':'Show all'}</button>`:''}`
      :'<h1 class="nd-goal nd-goal-empty">No goal yet</h1><p class="nd-muted">Nothing has been sent to the orchestrator in this session. Send it a message below to begin.</p>';
    const sc=$('nd-orch-scroll'), stick=sc.scrollHeight-sc.scrollTop-sc.clientHeight<48, st=sc.scrollTop;
    $('nd-convo').innerHTML=S.messages.map((m,i)=>m.role==='you'?`<div class="nd-msg nd-you"><div class="nd-by"><b>You</b></div><p class="nd-text">${esc(m.text)}</p></div>`
      :m.role==='tool'?`<div class="nd-msg nd-tool-msg"><span class="nd-tool-name">${esc(m.name)}</span><span class="nd-tool-args">${esc(m.args)}</span><span class="nd-badge">${esc(m.badge)}</span></div>`
      :`<div class="nd-msg nd-dream"><div class="nd-by"><b>Orchestrator</b></div>${thinkHTML(m.thinking,'m:'+i)}${m.text?`<p class="nd-stream">${esc(m.text)}</p>`:''}</div>`).join('')
      ||'<p class="nd-muted">Nothing said yet.</p>';
    sc.scrollTop=stick?sc.scrollHeight:st;
    needs();
    $('nd-send').textContent=S.turn?'Steer':'Send';
    $('nd-hint').textContent=S.turn?'A turn is running: this goes to the orchestrator at its next model request':'Enter to send · Shift+Enter for a new line';
  }
  // The Needs-you stack (DREAM-194), above the conversation and outside its scroll so a waiting request is never
  // scrolled away: every waiting request -- whose it is, the tool and the reason, then the choices the request itself
  // offers, numbered in its order -- and after them every failed or stopped worker with its retry.
  function askHTML(a,where){
    const l=a.run?S.lanes.get(a.run)||null:null, key=`${where}-${a.k}`, off=a.busy||!!a.answered||a.closed;
    const name=a.run?(l?`${l.n} ${l.agent}`:a.agent||'worker'):'Orchestrator';
    const who=a.run?`${l?`<span class="nd-pip nd-${stateOf(l)}" aria-hidden="true">${l.n}</span>`:''}<b>${esc(l?l.agent:a.agent||'worker')}</b>`:'<b>Orchestrator</b>';
    return `<div class="nd-ask${off?' nd-ask-done':''}" role="group" aria-label="${esc(name)} asks to use ${esc(a.tool)}" data-ask="${esc(a.id)}"${a.busy?' aria-busy="true"':''}>
      <div class="nd-who">${who}<span class="nd-wait nd-num" data-since="${a.at}">${waited(a.at)}</span></div>
      <p class="nd-ask-tool"><span class="nd-tool-name">${esc(a.tool)}</span><span class="nd-tool-args">${esc(a.args)}</span></p>${a.reason?`<p class="nd-ask-reason${whole.has(a.id)?' nd-whole':''}" id="nd-why-${key}">${esc(a.reason)}</p>`:''}
      <div class="nd-opts">${a.choices.map(([v,t],i)=>`<button type="button" class="nd-opt" data-ans="${esc(a.id)}" data-choice="${esc(v)}" data-fk="ans-${key}-${i}"${i<9?` aria-keyshortcuts="${i+1}"`:''}${off?' disabled':''}><span class="nd-k nd-num" aria-hidden="true">${i+1}</span>${esc(t)}</button>`).join('')}</div>
      ${a.answered?`<p class="nd-muted">Answered: ${esc(a.answered)}</p>`:''}${a.note?`<p class="nd-note">${esc(a.note)}</p>`:''}${a.reason&&where==='s'?`<button type="button" class="nd-pill nd-more" data-more="${esc(a.id)}" data-fk="more-${key}" aria-controls="nd-why-${key}" aria-expanded="${whole.has(a.id)}" hidden>${whole.has(a.id)?'Show less':'Show the whole reason'}</button>`:''}</div>`;
  }
  const failHTML=l=>`<div class="nd-ask nd-ask-fail" role="group" aria-label="${l.n} ${esc(l.agent)}: ${WORD[l.state]}"><div class="nd-who"><span class="nd-pip nd-${l.state}" aria-hidden="true">${l.n}</span><b>${esc(l.agent)}</b><span class="nd-state">${WORD[l.state]}</span></div>${l.text?`<p class="nd-ask-reason">${esc(l.text)}</p>`:''}${retryHTML(l,'s')}</div>`;
  // A question form (questions_v2, ask_user_input, visual_check) is answered in Chat: the stack points to every form the
  // chat shows open -- who asked, the form's title, its first question's first line -- and follows the chat as they come
  // and go (an answered form leaves the chat, and the stack with it).
  const stream=$('stream'), openForms=()=>stream?[...stream.querySelectorAll('form.qform')]:[];
  function formHTML(f,i){
    const title=(f.querySelector('.qtitle')?.textContent||'Questions').trim(), qs=f.querySelectorAll('fieldset.q');
    const first=(qs[0]?.querySelector('legend')?.textContent||'').trim().split('\n')[0];
    return `<div class="nd-ask nd-ask-form" role="group" aria-label="Orchestrator asks: ${esc(title)}"><div class="nd-who"><b>Orchestrator</b><span class="nd-state">${qs.length} question${qs.length===1?'':'s'} in Chat</span></div>`
      +`<p class="nd-ask-tool">${esc(title)}</p>${first?`<p class="nd-ask-reason">${esc(first)}</p>`:''}<button type="button" class="nd-pill nd-open-form" data-form="${i}" data-fk="form-${i}">Open Chat</button></div>`;
  }
  if(stream) new MutationObserver(ms=>{ if(ms.some(m=>[...m.addedNodes,...m.removedNodes].some(n=>n.nodeType===1&&n.matches('form.qform')))) schedule(); }).observe(stream,{childList:true});
  function needs(){
    const asks=[...S.asks.values()], forms=openForms(), fails=S.order.filter(l=>(l.state==='failed'||l.state==='stopped')&&l.turn===S.turnNo);
    const items=[...asks.map(a=>askHTML(a,'s')),...forms.map(formHTML),...fails.map(failHTML)], n=items.length, rest=$('nd-stack-rest'), top=rest.scrollTop;
    $('nd-needs-c').textContent=n?String(n):''; $('nd-needs-sec').classList.toggle('nd-quiet',!n);   // nothing waits: one line, none at a short height
    $('nd-stack-first').innerHTML=n?items[0]:'<p class="nd-muted">Nothing is waiting on you.</p>';
    rest.innerHTML=items.slice(1).join(''); rest.scrollTop=top;
    $('nd-stack-note').textContent=stackNote;
    fitNeeds();
  }
  // A short window shows a request's reason in two lines (nested.css): its toggle shows when that cut something, or to
  // close it again. The section gives way down to what it holds besides the rest box -- its heading, its first item and
  // its note -- and about one item of the rest box (6rem, or all of it when less), so the rest box shrinks and scrolls
  // before the conversation loses its room (flexbox alone counts the rest box's content in the section's minimum) and
  // never hides its items, which are in the Tab order. A column too short even for that scrolls, as before.
  const SHORT=matchMedia('(max-height:800px)'), REST_FLOOR=96;
  function fitNeeds(){
    for(const m of $('nd-stack').querySelectorAll('.nd-more')){ const r=$(m.getAttribute('aria-controls'));
      m.hidden=!r||!(r.classList.contains('nd-whole')?SHORT.matches:r.scrollHeight>r.clientHeight+1); }
    const sec=$('nd-needs-sec'), rest=$('nd-stack-rest'); sec.style.minHeight='';
    if(rest.offsetHeight) sec.style.minHeight=`${sec.offsetHeight-rest.offsetHeight+Math.min(rest.offsetHeight,REST_FLOOR)}px`;
  }
  function keep(fn){const a=document.activeElement,k=a&&a.dataset?a.dataset.fk:null;fn();if(k){const n=page.querySelector(`[data-fk="${k}"]`);if(n&&n!==document.activeElement)n.focus({preventScroll:true});}}
  const listeners=new Set();
  let raf=0, dirty=false;
  function render(){
    raf=0; dirty=false;
    keep(()=>{top();orch();lanes();});
    page.querySelectorAll('details.nd-think').forEach(d=>{d.ontoggle=()=>{if(d.open)S.open.add(d.dataset.think);else S.open.delete(d.dataset.think);};});
    for(const fn of listeners){ try{ fn(); }catch(e){ console.error(e); } }
    const plan=$('nd-plan'); if(plan&&plan.parentElement!==col) col.append(plan);   // nested-plan.js mounts it by the composer: into the scroll box
    // A pin, an unpin, an answer or a control moved or disabled the focused control: after every view re-rendered, and
    // only when the focus was lost (the owner may have moved on, say to a text box).
    if(focusAfter){ const f=focusAfter; focusAfter=null; const a=document.activeElement;
      if(!a||a===document.body){ const n=typeof f==='function'?f():page.querySelector(f); if(n) n.focus({preventScroll:true}); } }
    revealFirst();
    if(moreShown){ page.querySelector(`#nd-stack [data-more="${CSS.escape(moreShown)}"]`)?.scrollIntoView({block:'nearest'}); moreShown=null; }
  }
  function schedule(){ if(page.hidden){dirty=true;return;} if(!raf) raf=requestAnimationFrame(render); }
  // --- P5 controls (DREAM-192): Stop, Pause/Resume and Pause all over /api/control. A lane shows the server's answer
  // only: its state changes when the response says so (the status rows then say more), a refusal is shown verbatim
  // on that lane, and its controls are off while a request is in flight and once the worker has finished.
  const CTL_STATE={stopping:'stopping',pausing:'pausing',paused:'paused',running:'working'};
  const NET='Could not reach Dream. Reconnect and try again.';
  let pauseAllBusy=false, ctlNote='';
  async function api(path,body){
    let r;
    try{ r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN},body:JSON.stringify(body)}); }
    catch{ throw Error(NET); }
    const data=await r.json().catch(()=>({}));
    if(!r.ok) throw Object.assign(Error(typeof data.error==='string'&&data.error?data.error:`Dream refused it (HTTP ${r.status}).`),{status:r.status});
    return data;
  }
  const post=(action,extra)=>api('/api/control',{action,...extra}).then(d=>d.result);
  // An answer describes the worker when its request left: a status row that arrived meanwhile is newer (the real server
  // sends a Stop's terminal row before the response), so a lane that finished or got a row since keeps what it has.
  // A worker still waiting for an engine lane stays Queued when it is resumed.
  const settle=(res,seqs)=>{ const l=res&&typeof res==='object'?S.lanes.get(String(res.run_id)):null, st=l&&CTL_STATE[res.state];
    if(st&&!FINAL.has(l.state)&&seqs.get(l)===l.seq){ l.state=st==='working'&&l.queued?'queued':st; l.note=''; } };
  // The keyboard's place after an action: back on the control when the disabled spell lost it (never when the owner
  // moved on, say to the composer), or on its lane when the control ends disabled (a Stop that is now Stopping).
  const laneFocus=(id,where)=>{ const k=CSS.escape(id);
    return where==='d'?$('nd-pin-btn')||$('nd-back'):page.querySelector(`.nd-${where==='c'?'card':'win'}[data-run-id="${k}"]`)||page.querySelector(`.nd-pips [data-run-id="${k}"]`); };
  async function control(id,action){
    const l=S.lanes.get(id); if(!l||l.busy||FINAL.has(l.state)||sdk(l)) return;   // the SDK's worker: never a request
    const a=document.activeElement, fk=a&&page.contains(a)&&a.dataset.fk?a.dataset.fk:null, where=fk&&(fk.match(/^ctl-([wcd])-/)||[])[1];
    const seqs=new Map([[l,l.seq]]);
    l.busy=true; l.note=''; schedule();
    try{ settle(await post(action,{run_id:id}),seqs); } catch(e){ l.note=e.message; announce(e.message); }
    finally{ l.busy=false;
      if(fk) focusAfter=()=>{ const n=page.querySelector(`[data-fk="${CSS.escape(fk)}"]`); return n&&!n.disabled?n:where?laneFocus(id,where):null; };
      schedule(); }
  }
  async function pauseAll(){
    if(pauseAllBusy) return;
    const a=document.activeElement, mine=!!a&&a.id==='nd-pause-all', seqs=new Map(S.order.map(x=>[x,x.seq]));
    pauseAllBusy=true; ctlNote=''; schedule();
    try{ const res=await post('agents_pause_all',{}); for(const r of Array.isArray(res?.runs)?res.runs:[]) settle(r,seqs); }
    catch(e){ ctlNote=e.message; }
    finally{ pauseAllBusy=false; if(mine) focusAfter=()=>{ const b=$('nd-pause-all'); return b.disabled?$('nd-drawer-btn'):b; }; schedule(); }
  }
  // P10 (DREAM-197): the owner's cap on the workers one reply may start, nested.max_workers (3/7/11/15, shown with the
  // orchestrator as 4/8/12/16 agents). Read from settings_get each time the view opens and when the Controls dialog
  // (its Settings tab) closes, written with settings_save; the checked choice is only ever the server's answer, and a
  // read begun before a save's answer is dropped. A Dream without the setting shows no control; a failed read -- a
  // refused request, or a settings file Dream cannot use -- shows the four choices off and Dream's reason.
  const CAP='nested.max_workers', CAP_CHOICES=[3,7,11,15];   // the setting's choices (NESTED_EVENTS.md P10)
  const workerCap={shown:false,value:null,choices:[],labels:{},busy:false,note:''};
  let capSaved=0;   // the saves answered so far
  // What paces the workers of a reply here: the engine's lanes (MachX), Parallel workers (OpenAI, xAI), or nothing of
  // Dream's (the coding CLIs run their own sub-agents; only the OpenAI-compatible backend applies it). On Claude it binds
  // the local helpers: each delegate_local call's helper reads nested.max_workers as it connects (local_helpers.py, the
  // helper's apply_worker_cap); the sub-agents the SDK runs take no limit from Dream.
  const capHint=()=>S.provider===CLAUDE?"Applies to Claude's local helpers (delegate_local) from its next call; Claude's own sub-agents are the SDK's."
    :SILENT.has(S.provider)?'Applies to MachX and the OpenAI and xAI APIs; this provider runs its own sub-agents.'
    :`Takes effect from the orchestrator's next reply. ${S.provider==='MachX'?"Workers beyond the engine's lanes queue."
      :S.engine&&S.engine.served===1?'One runs at a time; the rest queue.':'Parallel workers sets how many run at once; the rest queue.'}`;
  function capView(v){
    if(v&&v.ok===false) throw new Error(typeof v.error==='string'&&v.error?v.error:'Dream could not read its settings.');
    let row=null; for(const s of Array.isArray(v?.sections)?v.sections:[]) for(const r of Array.isArray(s?.rows)?s.rows:[]) if(r&&r.key===CAP) row=r;
    if(!row){ workerCap.shown=false; return; }
    const ch=Array.isArray(row.edit?.choices)?row.edit.choices.filter(n=>count(n)!==null):[];
    workerCap.shown=ch.length>0; workerCap.choices=ch; workerCap.labels=row.edit?.labels&&typeof row.edit.labels==='object'?row.edit.labels:{};
    workerCap.value=ch.includes(row.value)?row.value:null;
  }
  async function capRead(){
    const at=capSaved;
    try{ const v=await post('settings_get',{}); if(at!==capSaved) return; capView(v); workerCap.note=''; }
    catch(e){ if(at!==capSaved) return; workerCap.shown=true; workerCap.value=null; workerCap.note=e.message;
      if(!workerCap.choices.length) workerCap.choices=CAP_CHOICES.slice(); }
    schedule();
  }
  // Any choice saves, the checked one too: the view may be behind the file (another window, /settings in a terminal).
  async function capSave(v){
    if(workerCap.busy||workerCap.value===null||!workerCap.choices.includes(v)) return;
    workerCap.busy=true; workerCap.note=''; schedule();
    try{ const res=await post('settings_save',{key:CAP,value:v,scope:'global'}); capSaved++; capView(res?.settings); }
    catch(e){ capSaved++; workerCap.note=e.message; }   // said once, by the note (a polite live region)
    finally{ workerCap.busy=false; schedule(); }
  }
  function capHTML(){
    const off=workerCap.value===null, first=off?0:workerCap.choices.indexOf(workerCap.value);
    return workerCap.choices.map((n,i)=>{ const on=n===workerCap.value, name=String(workerCap.labels[String(n)]||`${n+1} agents`);
      return `<button type="button" role="radio" aria-checked="${on}" aria-label="${esc(name)}" data-cap="${n}" data-fk="cap-${n}" tabindex="${i===first?0:-1}"${off?' disabled':''}>${n+1}</button>`; }).join('');
  }
  // Refusals are shown on their lane and said once through one live region that is never re-rendered.
  function announce(text){ const r=$('nd-live'); r.textContent=''; requestAnimationFrame(()=>{ r.textContent=text; }); }
  // The same two buttons in a window's header, a card's foot and the drawer's detail (`where` keeps their focus keys apart).
  function ctlHTML(l,where){
    if(sdk(l)){   // the SDK's worker: both off, saying why to the pointer (title) and to a screen reader (described by)
      const why=`nd-ro-${where}-${l.n}`, b=(cls,fk,label)=>`<button type="button" class="nd-pill nd-ctl ${cls}" data-fk="ctl-${where}-${fk}-${l.n}" disabled aria-describedby="${why}" title="${SDK_ONE}">${label}</button>`;
      return `<span class="nd-ctls" role="group" aria-label="Controls for ${l.n} ${esc(l.agent)}">${b('nd-ctl-pause','p','Pause')}${b('nd-ctl-stop','s','Stop')}<span class="nd-sr" id="${why}">${SDK_ONE}</span></span>`;
    }
    const done=FINAL.has(l.state), off=l.busy||done||l.state==='stopping', resume=l.state==='pausing'||l.state==='paused', busy=l.busy?' aria-busy="true"':'';
    const b=(cls,act,fk,label,title,no)=>`<button type="button" class="nd-pill nd-ctl ${cls}" data-ctl="${act}" data-run="${esc(l.id)}" data-fk="ctl-${where}-${fk}-${l.n}"${off||no?' disabled':''}${busy} title="${done?'This worker has finished':title}">${label}</button>`;
    return `<span class="nd-ctls" role="group" aria-label="Controls for ${l.n} ${esc(l.agent)}">`   // Pause only where Pause all would pause too
      +b('nd-ctl-pause',resume?'agent_resume':'agent_pause','p',resume?'Resume':'Pause',resume?'Resume this worker':'Pause this worker at its next round boundary',!resume&&!PAUSABLE.has(l.state))
      +b('nd-ctl-stop','agent_stop','s','Stop','Stop this worker now')+'</span>';
  }
  const noteHTML=l=>l.note?`<p class="nd-note">${esc(l.note)}</p>`:'';
  // Answering a request (DREAM-194): POST /api/permission {id, choice} with the session token. A 200 marks the item
  // answered until permission_done closes it; a refusal shows verbatim on the item (a 409, no longer waiting, also
  // closes its choices), or under the stack when permission_done closed the item meanwhile. Answered, the keyboard
  // moves to the next open request in the same place (else the stack's heading, or Back in the drawer); refused, it stays.
  const nextAsk=where=>page.querySelector(`${where==='d'?'#nd-detail':'#nd-stack'} .nd-ask[data-ask]:not(.nd-ask-done) .nd-opt`)||$(where==='d'?'nd-back':'nd-needs-h');
  async function answer(id,choice){
    const a=S.asks.get(id), pick=a&&a.choices.find(([v])=>v===choice); if(!pick||a.busy||a.answered||a.closed) return;
    const act=document.activeElement, box=act&&page.contains(act)?act.closest('.nd-ask[data-ask]'):null, mine=!!box&&box.dataset.ask===id;
    const fk=mine?act.dataset.fk:null, where=mine&&box.closest('#nd-drawer')?'d':'s';
    a.busy=true; a.note=''; stackNote=''; schedule();
    let err=null;
    try{ await api('/api/permission',{id,choice}); }catch(e){ err=e; }
    const cur=S.asks.get(id);      // permission_done or a reconnect may have closed or rebuilt it meanwhile
    if(cur){ cur.busy=false; if(!err) cur.answered=pick[1]; else { cur.note=err.message; if(err.status===409) cur.closed=true; } }
    else if(err) stackNote=err.message;
    if(err) announce(err.message);
    if(mine) focusAfter=err&&cur&&!cur.closed&&fk?`[data-fk="${CSS.escape(fk)}"]`:()=>nextAsk(where);
    schedule();
  }
  // A failed or stopped worker: the owner asks the orchestrator to run it again through the composer's own path (a steer
  // while a turn runs, a message between turns), in words that say which worker and what ended it.
  const retryText=l=>`Please retry the ${l.agent} worker; it ${l.state==='stopped'?'was stopped':'failed'}${l.text?`: ${l.text.slice(0,300)}`:'.'}`;
  const retried=new Set();   // run ids whose retry the orchestrator has taken: said, and not sent again
  const retryHTML=(l,where)=>`${retried.has(l.id)?'<span class="nd-retried">Retry asked</span>':''}<button type="button" class="nd-pill nd-retry" data-retry="${esc(l.id)}" data-fk="retry-${where}-${l.n}"${sending||retried.has(l.id)?' disabled':''}>Ask the orchestrator to retry ${esc(l.agent)}</button>`;
  // P6 (DREAM-193): a message to one worker, from its pinned window or the drawer's detail. Each composer is built once
  // per lane and place and kept across renders and reconnects (by run id). It POSTs agent_message {run_id, text} with
  // the token; the answer's `pending` shows as "N waiting" until the worker's message rows count it down (less the rows
  // that arrived while it was on its way, which it already counted; nothing once the worker has ended); a refusal is
  // said verbatim and the draft stays; the box is off (read-only, so the focus and the draft stay) on a finished or
  // stopping worker; the limit shows as the owner types, counted in code points as the server counts.
  const LIMIT=4000, chars=s=>[...s].length;
  const boxes=new Map();   // `${where}:${run}` -> {node, run, busy, note}
  let boxSeq=0;
  function sayBox(l,where){
    const key=where+':'+l.id; let b=boxes.get(key);
    if(!b){
      const k=++boxSeq, node=el('div',null,{class:'nd-say','data-say':key});
      node.innerHTML=`<label class="nd-sr" for="nd-say-${k}"></label><textarea id="nd-say-${k}" rows="1" data-fk="say-${k}"></textarea><div class="nd-say-row"><span class="nd-say-wait nd-num"></span><span class="nd-say-count nd-num"></span><button type="button" class="nd-pill nd-say-send">Send</button></div><p class="nd-say-note" id="nd-say-note-${k}"></p>`;
      b={node,run:l.id,busy:false,note:''}; boxes.set(key,b);
    }
    return b;
  }
  const sayOff=l=>FINAL.has(l.state)||l.state==='stopping'||sdk(l);   // the SDK's worker takes no message (DREAM-212)
  function syncSay(b,l){
    const n=b.node, ta=n.querySelector('textarea'), send=n.querySelector('.nd-say-send'), cnt=n.querySelector('.nd-say-count'), note=n.querySelector('.nd-say-note');
    const off=sayOff(l), ro=sdk(l), name=`${l.n} ${l.agent}`, text=ta.value.trim(), c=chars(text), over=c>LIMIT;
    n.querySelector('label').textContent=`Message ${name}`;
    ta.placeholder=ro?'Read-only':off?(l.state==='stopping'?'This worker is stopping':'This worker has finished'):`Message ${l.agent}`;
    ta.readOnly=off||b.busy; if(off) ta.setAttribute('aria-disabled','true'); else ta.removeAttribute('aria-disabled');
    if(ro) ta.setAttribute('aria-describedby',note.id); else ta.removeAttribute('aria-describedby');   // the reason, below the box
    send.disabled=off||b.busy||!text||over; send.setAttribute('aria-label',`Send to ${name}`);
    if(b.busy) send.setAttribute('aria-busy','true'); else send.removeAttribute('aria-busy');
    cnt.textContent=text?`${c.toLocaleString()} / ${LIMIT.toLocaleString()}${over?' · too long to send':''}`:''; cnt.classList.toggle('nd-over',over);
    n.querySelector('.nd-say-wait').textContent=l.pending?`${l.pending} waiting`:'';
    note.textContent=ro?SDK_ONE:off&&ta.value?`This worker ${l.state==='stopping'?'is stopping':'has finished'}; your draft stays here. Message the orchestrator instead.`:b.note;
    n.classList.toggle('nd-off',off); n.classList.toggle('nd-ro',ro);
  }
  async function say(b){
    const l=S.lanes.get(b.run), ta=b.node.querySelector('textarea'), text=ta.value.trim();
    if(!l||b.busy||!text||sayOff(l)||chars(text)>LIMIT) return;
    if(document.activeElement===b.node.querySelector('.nd-say-send')) ta.focus({preventScroll:true});   // Send turns off now: the focus stays in the box, not on the page's keys
    const m0=l.msgs;
    b.busy=true; b.note=''; syncSay(b,l);
    try{ const res=await post('agent_message',{run_id:l.id,text}), cur=S.lanes.get(b.run), p=count(res?.pending); ta.value='';
      // Rows that came while this was on its way were counted by `pending` too. A reconnect meanwhile rebuilds the lane
      // from the retained history, which may hold fewer of its rows: never a reason to count more than the answer.
      if(cur&&p!==null&&!FINAL.has(cur.state)) cur.pending=Math.max(0,p-Math.max(0,cur.msgs-m0)); }
    catch(e){ b.note=e.message; announce(e.message); }
    finally{ b.busy=false; const cur=S.lanes.get(b.run); if(cur) syncSay(b,cur); schedule(); }
  }
  // A pip, a card, or Enter on a focused card opens that worker in the All-agents drawer; without it, a pip scrolls to the worker.
  const drawerOpen=id=>{ if(window.DreamNestedDrawer){ window.DreamNestedDrawer.open(id); return; }
    const key=CSS.escape(id), target=page.querySelector(`.nd-win[data-run-id="${key}"],.nd-card[data-run-id="${key}"]`);
    if(target){ target.scrollIntoView({block:'nearest',inline:'nearest'}); target.focus({preventScroll:true}); } };
  page.addEventListener('click',e=>{
    const cycle=e.target.closest('.nd-attn [data-cycle]'); if(cycle){ window.DreamNestedDrawer?.cycle(cycle.dataset.cycle); return; }
    const ctl=e.target.closest('[data-ctl]'); if(ctl){ if(!ctl.disabled) control(ctl.dataset.run,ctl.dataset.ctl); return; }
    const ans=e.target.closest('[data-ans]'); if(ans){ if(!ans.disabled) answer(ans.dataset.ans,ans.dataset.choice); return; }
    const cb=e.target.closest('[data-cap]'); if(cb){ if(!cb.disabled) capSave(+cb.dataset.cap); return; }
    const more=e.target.closest('[data-more]'); if(more){ const id=more.dataset.more; if(!whole.delete(id)) whole.add(id); moreShown=id; schedule(); return; }
    const sb=e.target.closest('.nd-say-send'); if(sb){ const b=boxes.get(sb.closest('.nd-say').dataset.say); if(b&&!sb.disabled) say(b); return; }
    const rt=e.target.closest('[data-retry]'); if(rt){ const l=S.lanes.get(rt.dataset.retry); if(!rt.disabled&&l&&(l.state==='failed'||l.state==='stopped')&&!retried.has(l.id)) submit(retryText(l)).then(ok=>{ if(ok){ retried.add(l.id); schedule(); } }); return; }
    const of=e.target.closest('[data-form]'); if(of){ const f=openForms()[+of.dataset.form]; if(f){ $('dream-nav-chat')?.click();   // to the chat, on that form
        requestAnimationFrame(()=>{ f.scrollIntoView({block:'center'}); f.querySelector('input,textarea,select,button')?.focus({preventScroll:true}); }); } return; }
    if(e.target.closest('#nd-pause-all')){ pauseAll(); return; }
    const pinBtn=e.target.closest('[data-pin]'); if(pinBtn){ const id=pinBtn.dataset.pin; if(pin(id)) focusAfter=`[data-unpin="${CSS.escape(id)}"]`; return; }
    const unpinBtn=e.target.closest('[data-unpin]'); if(unpinBtn){ const id=unpinBtn.dataset.unpin; if(unpin(id)) focusAfter=`[data-pin="${CSS.escape(id)}"]`; return; }
    const p=e.target.closest('.nd-pips .nd-pip'); if(p){ drawerOpen(p.dataset.runId||''); return; }
    const c=e.target.closest('.nd-card[data-run-id]'); if(c){ drawerOpen(c.dataset.runId); }
  });
  page.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&e.target.classList?.contains('nd-card')&&e.target.dataset.runId){ e.preventDefault(); drawerOpen(e.target.dataset.runId); return; }
    const move={ArrowRight:1,ArrowDown:1,ArrowLeft:-1,ArrowUp:-1}[e.key], rb=move&&e.target.matches?.('[data-cap]')?e.target:null;
    if(rb){ e.preventDefault(); e.stopPropagation(); if(workerCap.busy||workerCap.value===null) return;
      const i=workerCap.choices.indexOf(+rb.dataset.cap), n=workerCap.choices[(i+move+workerCap.choices.length)%workerCap.choices.length];
      page.querySelector(`[data-cap="${n}"]`)?.focus(); capSave(n); return; }
    const say_=e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.target.matches?.('.nd-say textarea')?boxes.get(e.target.closest('.nd-say').dataset.say):null;   // Enter sends, Shift+Enter is a new line
    if(say_){ e.preventDefault(); if(!e.repeat) say(say_); return; }   // a held Enter sends once: a refused draft stays in the box
    // 1-9 answer the focused Needs-you request with its Nth choice (the drawer's digits leave these to it).
    const box=/^[1-9]$/.test(e.key)&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.repeat&&!e.isComposing&&e.target.closest?e.target.closest('.nd-ask[data-ask]'):null;
    if(box){ e.preventDefault(); e.stopPropagation(); const b=box.querySelectorAll('.nd-opt')[+e.key-1]; if(b&&!b.disabled) answer(b.dataset.ans,b.dataset.choice); }
  });
  page.addEventListener('input',e=>{ const n=e.target.closest&&e.target.closest('.nd-say'), b=n&&boxes.get(n.dataset.say), l=b&&S.lanes.get(b.run); if(l) syncSay(b,l); });   // the count as the owner types
  // What the drawer and layout modules read and call (never the state itself).
  window.DreamNested={WORD,esc,tail,written,thinking,facts,order:()=>S.order,lane:id=>S.lanes.get(id)||null,pins:()=>S.pins.slice(),pin,unpin,controlsHTML:ctlHTML,noteHTML,control,
    stateOf,clockHTML,asksHTML:(l,where)=>asksOf(l).map(a=>askHTML(a,where)).join(''),retryHTML,sayNode:(l,where)=>sayBox(l,where).node,syncSay:(l,where)=>syncSay(sayBox(l,where),l),
    layout:()=>({...L}),setLayout,resetLayout,prefs:()=>({...P}),setPref,session:()=>S.session,provider:()=>S.provider,silent:()=>SILENT.has(S.provider),visible:()=>!page.hidden,
    claude:()=>S.provider===CLAUDE,readOnly:sdk,
    subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn);}};

  // --- the composer: the same /api/prompt the chat uses; while a turn runs it steers, between turns it sends ----------
  $('nd-goal-wrap').addEventListener('click',e=>{const b=e.target.closest('.nd-goal-more');if(!b)return;
    goalOpen=!goalOpen;$('nd-goal').classList.toggle('nd-goal-cut',!goalOpen);
    b.textContent=goalOpen?'Show less':'Show all';b.setAttribute('aria-expanded',String(goalOpen));});
  const compose=$('nd-compose'), sendBtn=$('nd-send'), status=$('nd-status');
  const RECEIPT={pending:'Saved; it goes to the orchestrator with its next model request.',
    included:"Added to the orchestrator's next model request; not sent yet.",
    submitted:'Sent with a model request. This does not confirm that the orchestrator followed it.',
    retained:'Retained without a confirmed submission; inspect the saved session before sending it again.',
    queued_after_turn:'This agent does not take live steering; queued after the turn.'};
  let sending=false;
  const drafts=new Map();   // one steering receipt id per text, kept across a failed POST: a retry never delivers twice
  async function submit(given){   // the composer's words, or a retry's (DREAM-194)
    const own=given===undefined, text=(own?compose.value:given).trim(); if(!text||sending) return;
    sending=true; sendBtn.disabled=true; status.textContent=''; delete status.dataset.error; schedule();
    const steer=S.turn, body={prompt:text,attachments:[]};
    if(steer){ let d=drafts.get(text); if(!d){ d={id:crypto.randomUUID().replaceAll('-',''),target:S.steeringTarget}; drafts.set(text,d); } body.delivery='steer'; body.steering_id=d.id; body.steering_target=d.target; }
    else if(document.getElementById('read-twice')?.getAttribute('aria-pressed')==='true') body.read_twice=true;   // the chat's Read x2 toggle; a queued send only
    try{
      const r=await fetch('/api/prompt',{method:'POST',headers:{'Content-Type':'application/json','X-Dream-Token':TOKEN},body:JSON.stringify(body)});
      const data=await r.json().catch(()=>({}));
      if(!r.ok) throw Error(data.error||`Could not send (HTTP ${r.status})`);
      if(own) compose.value='';
      drafts.delete(text);
      status.textContent=steer?(RECEIPT[data.steering?.status]||'Sent to the orchestrator for its next model request.'):'Sent to the orchestrator.';
      return true;
    }catch(e){ status.textContent=e.message||'Could not reach Dream. Reconnect and send it again.'; status.dataset.error='true'; return false; }
    finally{ sending=false; sendBtn.disabled=false; schedule(); }
  }
  sendBtn.onclick=()=>submit();
  // The waiting clocks move between events too (whole minutes: a 5 s tick is close enough).
  setInterval(()=>{ if(!page.hidden) page.querySelectorAll('[data-since]').forEach(n=>{ n.textContent=waited(+n.dataset.since); }); },5000);
  compose.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();submit();}});

  new MutationObserver(()=>{const on=root.dataset.dreamView==='nested';if(on===!page.hidden)return;page.hidden=!on;if(on){render();capRead();}else window.DreamNestedDrawer?.close();}).observe(root,{attributes:true,attributeFilter:['data-dream-view']});
  document.addEventListener('close',e=>{ if(e.target?.id==='dream-controls'&&!page.hidden) capRead(); },true);   // its Settings tab may have saved the limit
})();
