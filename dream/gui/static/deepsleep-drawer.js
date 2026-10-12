/* Session metadata presentation only. No execution, terminal parsing, or URLs. */
(() => {
  'use strict';
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const bounded=value=>String(value??'').slice(0,16000);
  function create({title,workspace,anchor,pane,bounds,onOpenChange}) {
    const element=el('aside',null,'ds-session-drawer');element.hidden=true;element.setAttribute('role','dialog');element.setAttribute('aria-label','Session details for '+title);element.setAttribute('aria-modal','false');
    const header=el('header',null,'ds-session-head'),brand=el('div','Dream','ds-session-brand');
    const folder=el('h2','▱ '+(workspace.split('/').filter(Boolean).at(-1)||'Workspace'));folder.title=workspace;
    const close=el('button','×');close.type='button';close.setAttribute('aria-label','Close session details');
    header.append(brand,folder,close);
    const label=el('p',title,'ds-session-title');
    const status=el('p','Loading session details…','ds-session-status');status.setAttribute('role','status');
    const body=el('div',null,'ds-session-body');
    element.append(header,label,status,body);
    const sections=new Map();let open=false,disposed=false,generation=null,sessionId='';
    for(const [key,name] of [['changes','Changes'],['outputs','Outputs'],['agents','Subagents'],['sources','Sources']]) {
      const section=el('details',null,'ds-session-section');
      const heading=el('summary',name),note=el('p',null,'ds-session-note'),list=el('div',null,'ds-session-items'),more=el('button','Show more');more.type='button';more.hidden=true;
      const about=el('details',null,'ds-session-about'),aboutLabel=el('summary','About these records'),reason=el('p');about.append(aboutLabel,reason);about.hidden=true;
      section.append(heading,note,list,more,about);body.append(section);
      const state={key,name,section,heading,note,list,more,about,reason,touched:false,limit:4,rows:new Map(),expanded:new Set(),data:null};sections.set(key,state);
      heading.addEventListener('click',()=>{state.touched=true;});
      more.onclick=()=>{state.limit=state.limit===4?100:4;renderSection(state);};
    }
    function renderSection(state) {
      const data=state.data;
      state.reason.textContent=data?.reason||'';state.about.hidden=!state.reason.textContent;
      if(!data || data.available!==true) {
        state.heading.textContent=state.name+' · unavailable';state.note.hidden=false;state.note.textContent='This session does not provide these details.';state.list.replaceChildren();state.rows.clear();state.more.hidden=true;
        if(!state.touched)state.section.open=false;
        return;
      }
      const items=Array.isArray(data.items)?data.items.slice(0,100):[];
      state.heading.textContent=state.name+' · '+(Number.isFinite(data.total)?data.total:items.length)+(state.section.dataset.stale==='true'?' · stale':'');
      if(!state.touched)state.section.open=items.length>0;
      state.note.textContent=items.length?'':'No '+state.name.toLowerCase()+' yet.';
      if(data.trimmed)state.note.textContent+=(state.note.textContent?' ':'')+'Some recorded items are omitted.';
      state.note.hidden=!state.note.textContent;
      const keep=new Set();let position=state.list.firstChild;
      for(const [index,item] of items.slice(0,state.limit).entries()) {
        const object=item&&typeof item==='object'?item:{name:item};
        const key=String(object.id??object.path??object.name??index);keep.add(key);
        let entry=state.rows.get(key);
        if(!entry) {
          const row=el('details',null,'ds-session-item'),summary=el('summary'),icon=el('span',null,'ds-session-icon'),name=el('span',null,'ds-session-item-name'),badge=el('span',null,'ds-session-item-status'),detail=el('pre');
          icon.setAttribute('aria-hidden','true');summary.append(icon,name,badge);row.append(summary,detail);row.open=state.expanded.has(key);
          entry={row,name,icon,badge,detail};state.rows.set(key,entry);
          row.addEventListener('toggle',()=>{if(!row.isConnected||state.rows.get(key)!==entry)return;if(row.open)state.expanded.add(key);else state.expanded.delete(key);});
        }
        const filename=object.path?.split(/[\\/]/).filter(Boolean).at(-1);
        entry.name.textContent=bounded(filename||object.title||object.name||'Recorded item');
        entry.name.title=bounded(object.path||object.title||object.name||'');
        entry.icon.textContent=state.key==='changes'?'Δ':state.key==='agents'?'◇':state.key==='sources'?'▱':/image|png|jpeg|svg|webp/.test(object.type||'')?'▧':/code|script/.test(object.type||'')?'⌘':'▤';
        entry.badge.textContent=bounded(object.state||'');entry.badge.hidden=!object.state;
        const detail=bounded(JSON.stringify(object,null,2));if(entry.detail.textContent!==detail)entry.detail.textContent=detail;
        if(entry.row!==position)state.list.insertBefore(entry.row,position);position=entry.row.nextSibling;
      }
      for(const [key,entry] of state.rows)if(!keep.has(key)){entry.row.remove();state.rows.delete(key);}
      state.more.hidden=items.length<=4;
      state.more.textContent=state.limit===4?(state.key==='sources'?'View all':'Show more')+' ('+items.length+')':'Show less';
      state.more.setAttribute('aria-label',state.more.textContent+' '+state.name.toLowerCase());
    }
    function place() {
      if(!open || disposed)return;
      const a=pane.getBoundingClientRect(),v=bounds.getBoundingClientRect();
      const width=Math.min(320,Math.max(180,v.width-12)),left=a.right+8+width<=v.right-6?a.right+8:Math.max(v.left+6,v.right-width-6);
      const top=Math.max(v.top+6,Math.min(a.top,v.bottom-180));
      Object.assign(element.style,{left:left+'px',top:top+'px',width:width+'px',maxHeight:Math.max(100,v.bottom-top-6)+'px'});
    }
    function setOpen(value,restore=true) {
      if(disposed||open===value)return;open=value;element.hidden=!value;onOpenChange(value);
      if(value){place();close.focus();}else if(restore&&anchor.isConnected)anchor.focus();
    }
    close.onclick=()=>setOpen(false);
    element.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setOpen(false);}});
    window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
    for(const state of sections.values())renderSection(state);
    return {element,setOpen,place,
      update(snapshot) {
        const metadata=snapshot.session, supplied=metadata&&typeof metadata==='object';
        const confirmed=!!snapshot.generation && (snapshot.available || supplied);
        const nextSession=metadata?.session_id||'';
        const changed=confirmed && (generation!==snapshot.generation || (snapshot.available && nextSession && sessionId && nextSession!==sessionId));
        if(changed){for(const state of sections.values()){state.limit=4;state.touched=false;state.about.open=false;state.expanded.clear();state.rows.clear();state.list.replaceChildren();state.data=null;}}
        if(confirmed)generation=snapshot.generation;
        if(snapshot.available && nextSession)sessionId=nextSession;
        element.dataset.stale=String(!snapshot.available);
        status.title=snapshot.reason||'';
        status.textContent=snapshot.available?'Session records · metadata only.':supplied?'Available session records.':'Session records unavailable.';
        if(!snapshot.available && [...sections.values()].some(state=>state.data?.items?.length && !metadata?.[state.key]))status.textContent+=' Previous records are stale.';
        for(const state of sections.values()) {
          const incoming=metadata?.[state.key];
          if(snapshot.available || incoming?.available===true || !state.data) {
            state.data=incoming;state.section.dataset.stale='false';
          } else state.section.dataset.stale='true';
          renderSection(state);
        }
      },
      error(message){element.dataset.stale='true';status.title=message;status.textContent='Session records could not load. Previous records may be stale.';for(const state of sections.values()){state.section.dataset.stale='true';renderSection(state);}},
      dispose(){setOpen(false,false);disposed=true;window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);element.remove();}
    };
  }
  window.DreamSessionDrawer=Object.freeze({create});
})();
