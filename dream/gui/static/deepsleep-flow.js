/* Receipt-only, finite handoff decoration. The board owns polling and visibility. */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const accepted = new Set(['queued', 'running', 'completed']);
  let sequence = 0;
  // anchor(node, out) and path(a, b): the board's own cord ends and shape (deepsleep.js), so the lightning runs
  // along the cord itself (2026-10-10). Without them, the decoration's own port centres and curve, as before.
  function create({board, viewport, isVisible, getNodes, anchor = null, path = null}) {
    const cord = path;   // position() has a local `path` of its own
    const consumed = new Set(), active = new Map(), waiting = [];
    let primed = false, disposed = false, links = [], overflow = 0;
    const svg = document.createElementNS(NS, 'svg');
    svg.classList.add('ds-handoff-flow');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const style = document.createElement('style');
    style.textContent = `
      .ds-handoff-flow{position:absolute;inset:0;width:100%;height:100%;overflow:hidden;pointer-events:none!important;z-index:300}
      .ds-handoff-flow *{pointer-events:none!important}
      .ds-handoff-flow .ds-flow-pulse{--flow-color:#ff8a1c}
      .ds-handoff-flow .ds-flow-pulse path{fill:none;stroke-linecap:round;stroke-linejoin:round}
      /* Electricity along the cord while a message travels (the owner, 2026-10-11): the cord charges (a red glow and
         a thin orange line) while a jagged bolt -- an orange arc round a pale yellow core, with short forks -- runs
         from the sender to the receiver; a reply runs back the other way. The bolt is redrawn by script (bolt()). */
      .ds-handoff-flow .ds-flow-trail{stroke:#ff3b1f;stroke-width:6;opacity:.28}
      .ds-handoff-flow .ds-flow-packet{stroke:#ff8a1c;stroke-width:1.5;opacity:.55}
      .ds-handoff-flow .ds-flow-arc{stroke:#ff8a1c;stroke-width:4.5;opacity:.55}
      .ds-handoff-flow .ds-flow-core{stroke:#ffe46b;stroke-width:2}
      .ds-handoff-flow .ds-flow-fork{stroke:#ffd24a;stroke-width:1.2;opacity:.85}
      .ds-handoff-flow .ds-flow-pulse[data-stage=reply] .ds-flow-packet{stroke:#ffd24a}
      .ds-handoff-flow .ds-flow-label,.ds-handoff-flow .ds-flow-queue{fill:#ffcf91;font:11px system-ui,sans-serif;text-anchor:middle;paint-order:stroke;stroke:#191610;stroke-width:4px;stroke-linejoin:round}
      @media(prefers-reduced-motion:reduce){.ds-handoff-flow .ds-flow-pulse path{animation:none;stroke-dasharray:none}}
    `;
    const defs = document.createElementNS(NS, 'defs');
    const markerIds = {};
    for (const [stage,color] of [['delivery','#ff8a1c'],['reply','#ffe46b']]) {
      const marker = document.createElementNS(NS, 'marker');
      const id = markerIds[stage] = `ds-flow-arrow-${++sequence}`;
      for (const [key,value] of Object.entries({id,viewBox:'0 0 8 8',refX:7,refY:4,markerWidth:5,markerHeight:5,orient:'auto'})) marker.setAttribute(key,value);
      const arrow = document.createElementNS(NS, 'path');
      arrow.setAttribute('d','M 0 0 L 8 4 L 0 8 Z');
      arrow.setAttribute('fill',color); marker.append(arrow); defs.append(marker);
    }
    // The bolt: 0.9 s to run the cord, 0.3 s to fade, redrawn about 30 times a second while any pulse is live.
    const TRAVEL = 900, FADE = 300, FRAME = 33;
    const still = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;   // read per send, as CSS did
    let frame = 0;
    function bolt(item, now) {
      const total = item.packet.getTotalLength ? item.packet.getTotalLength() : 0;
      if (!total) return;
      const elapsed = now - item.start, progress = Math.min(1, elapsed / TRAVEL), fade = Math.min(1, Math.max(0, (elapsed - TRAVEL) / FADE));
      const head = (1 - (1 - progress) ** 2) * total, from = Math.max(0, head - Math.max(60, Math.min(220, total * .35)));
      const back = item.reverse, steps = Math.max(4, Math.round((head - from) / 14)), points = [], twin = [];
      for (let i = 0; i <= steps; i++) {
        const along = from + (head - from) * i / steps, at = back ? total - along : along;
        const p = item.packet.getPointAtLength(at), q = item.packet.getPointAtLength(Math.min(total, at + 1));
        const nx = p.y - q.y, ny = q.x - p.x, n = Math.hypot(nx, ny) || 1, taper = Math.sin(Math.PI * i / steps);
        for (const [list, reach] of [[points, 8], [twin, 10]]) {     // two arcs, each its own zigzag
          const swing = (Math.random() * 2 - 1) * reach * taper;
          list.push([p.x + nx / n * swing, p.y + ny / n * swing]);
        }
      }
      const line = list => 'M ' + list.map(([x, y]) => x.toFixed(1) + ' ' + y.toFixed(1)).join(' L ');
      item.core.setAttribute('d', line(points)); item.arc.setAttribute('d', line(twin));
      const forks = [];                    // one or two short jagged branches off the bolt
      for (let k = 0; k < 2 && points.length > 4; k++) {
        if (Math.random() < .45) continue;
        let [x, y] = points[1 + Math.floor(Math.random() * (points.length - 3))];
        const angle = Math.random() * Math.PI * 2, branch = [[x, y]];
        for (let j = 0; j < 3; j++) { x += Math.cos(angle) * 7 + (Math.random() * 2 - 1) * 4; y += Math.sin(angle) * 7 + (Math.random() * 2 - 1) * 4; branch.push([x, y]); }
        forks.push(line(branch));
      }
      item.fork.setAttribute('d', forks.join(' '));
      item.group.style.opacity = String((.75 + Math.random() * .25) * (1 - fade));
    }
    function animate(now) {
      frame = 0;
      for (const item of active.values()) if (!item.still && now - (item.drawn || 0) >= FRAME) { item.drawn = now; bolt(item, now); }
      if (active.size) frame = requestAnimationFrame(animate);
    }
    const queueLabel = document.createElementNS(NS, 'text');queueLabel.classList.add('ds-flow-queue');
    queueLabel.setAttribute('y','20');
    svg.append(style, defs, queueLabel); board.append(svg);

    function remember(id) {
      consumed.add(id);
      while (consumed.size > 2048) consumed.delete(consumed.values().next().value);
    }
    function remove(id) {
      const item = active.get(id);
      if (!item) return;
      clearTimeout(item.timer); item.group.remove(); active.delete(id);
    }
    function reset() {
      for (const id of active.keys()) remove(id);
      waiting.length = 0;overflow = 0;queueLabel.textContent = '';
      consumed.clear(); links = []; primed = false;
    }
    function visible() {
      return !disposed && !document.hidden && isVisible() &&
        viewport.getBoundingClientRect().width > 0 && viewport.getBoundingClientRect().height > 0;
    }
    function shown(element) {
      if (!element?.isConnected || element.hidden || getComputedStyle(element).visibility === 'hidden') return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }
    function port(node, direction) {
      const characterPort = direction === 'output' ? node.avatarOutputPort : node.avatarInputPort;
      return shown(node.avatar) && shown(characterPort) ? characterPort :
        direction === 'output' ? node.outputPort : node.inputPort;
    }
    function endpoints(receipt) {
      if (!receipt.source || !receipt.target || receipt.source === 'owner' || receipt.target === 'owner' || receipt.source === receipt.target) return null;
      if (!links.some(link => link.source === receipt.source && link.target === receipt.target)) return null;
      const nodes = getNodes(), source = nodes.get(receipt.source), target = nodes.get(receipt.target);
      if (!source?.element.isConnected || !target?.element.isConnected) return null;
      if (anchor) {
        const ends = [anchor(source, true), anchor(target, false)];
        return ends.every(Boolean) ? ends : null;
      }
      const ports = [port(source, 'output'), port(target, 'input')];
      if (ports.some(port => !shown(port))) return null;
      const rects = ports.map(port => port.getBoundingClientRect());
      if (rects.some(rect => !rect.width || !rect.height)) return null;
      const origin = board.getBoundingClientRect();
      if (!origin.width || !origin.height) return null;
      const sx = board.offsetWidth / origin.width, sy = board.offsetHeight / origin.height;
      return rects.map(rect => ({x:(rect.left + rect.width/2 - origin.left)*sx, y:(rect.top + rect.height/2 - origin.top)*sy}));
    }
    function position(item, points) {
      if (cord) {          // the cord's own curve; a reply's bolt runs back down it
        const d = cord(points[0], points[1]);
        item.reverse = item.stage === 'reply';
        for (const line of [item.trail, item.packet]) line.setAttribute('d', d);
        if (item.still) for (const line of [item.arc, item.core]) line.setAttribute('d', d);   // reduced motion: a still glow
        item.label.setAttribute('x',Math.max(48,Math.min(board.clientWidth-48,(points[0].x+points[1].x)/2)));
        item.label.setAttribute('y',Math.max(14,(points[0].y+points[1].y)/2-10));
        return;
      }
      const [a,b] = item.stage === 'reply' ? [...points].reverse() : points;
      const bend = Math.max(45, Math.min(180, Math.abs(b.x-a.x)*.45)) * (item.stage === 'reply' ? -1 : 1);
      const path = `M ${a.x} ${a.y} C ${a.x+bend} ${a.y}, ${b.x-bend} ${b.y}, ${b.x} ${b.y}`;
      item.trail.setAttribute('d',path); item.packet.setAttribute('d',path);
      if (item.still) { item.arc.setAttribute('d',path); item.core.setAttribute('d',path); }
      item.label.setAttribute('x',Math.max(48,Math.min(board.clientWidth-48,(a.x+b.x)/2)));
      item.label.setAttribute('y',Math.max(14,(a.y+b.y)/2-10));
    }
    function place() {
      if (disposed) return;
      if (!visible()) { reset(); return; }
      for (const [id,item] of active) {
        const points = endpoints(item.receipt);
        if (points) position(item,points); else remove(id);
      }
      drain();
    }
    function queueStatus() {
      const origin = board.getBoundingClientRect(), view = viewport.getBoundingClientRect();
      queueLabel.setAttribute('x',String((view.left+view.width/2-origin.left)*board.offsetWidth/origin.width));
      queueLabel.setAttribute('y',String((view.top+20-origin.top)*board.offsetHeight/origin.height));
      queueLabel.textContent = [waiting.length ? `${waiting.length} effects waiting` : '',
        overflow ? `${overflow} extra recorded` : ''].filter(Boolean).join(' · ');
      if (!waiting.length && !active.size) {overflow = 0;queueLabel.textContent = '';}
    }
    function drain() {
      if (!visible()) {reset();return;}
      for (let i = 0; i < waiting.length && active.size < 8;) {
        const event = waiting[i];
        if ([...active.values()].some(item => item.receipt.id === event.receipt.id)) {i++;continue;}
        waiting.splice(i,1);
        const points = endpoints(event.receipt);
        if (points) pulse(event,points);
      }
      queueStatus();
    }
    function cancel(receipt) {
      remove(`${receipt.id}:delivery`);remove(`${receipt.id}:reply`);
      for (let i=waiting.length-1;i>=0;i--)if(waiting[i].receipt.id===receipt.id)waiting.splice(i,1);
    }
    function enqueue(receipt,stage) {
      const key = `${receipt.id}:${stage}`;
      if (consumed.has(key)) return;
      remember(key);
      if (!endpoints(receipt)) return;
      if (waiting.length < 160) waiting.push({receipt,stage,key});else overflow++;
    }
    function pulse({receipt,stage,key}, points) {
      const group = document.createElementNS(NS,'g'); group.classList.add('ds-flow-pulse');
      group.dataset.stage = stage;group.dataset.receipt = receipt.id;
      const trail = document.createElementNS(NS,'path'); trail.classList.add('ds-flow-trail');
      const packet = document.createElementNS(NS,'path'); packet.classList.add('ds-flow-packet');
      packet.setAttribute('marker-end',`url(#${markerIds[stage]})`);
      const arc = document.createElementNS(NS,'path'); arc.classList.add('ds-flow-arc');
      const core = document.createElementNS(NS,'path'); core.classList.add('ds-flow-core');
      const fork = document.createElementNS(NS,'path'); fork.classList.add('ds-flow-fork');
      const label = document.createElementNS(NS,'text'); label.classList.add('ds-flow-label'); label.textContent = stage === 'reply' ? 'Reply complete' : 'Task accepted';
      group.append(trail,packet,arc,core,fork,label); svg.append(group);
      const item = {receipt,stage,group,trail,packet,arc,core,fork,label,start:performance.now(),still:still(),timer:setTimeout(() => {remove(key);drain();},2000)};
      active.set(key,item); position(item,points);
      if (!item.still && !frame) frame = requestAnimationFrame(animate);
    }
    function update(snapshot) {
      if (disposed) return;
      if (!visible()) { reset(); return; }
      if (!Array.isArray(snapshot?.receipts) || !Array.isArray(snapshot?.links)) { reset(); return; }
      links = snapshot.links;
      const receipts = snapshot.receipts.slice(-80).filter(receipt => receipt && typeof receipt.id === 'string' && receipt.id);
      if (!primed) {
        receipts.forEach(receipt => {
          remember(`${receipt.id}:delivery`);
          if (!['queued','running','sending','uncertain'].includes(receipt.state))remember(`${receipt.id}:reply`);
        }); primed = true; return;
      }
      for (const receipt of receipts) {
        if (!accepted.has(receipt.state)) cancel(receipt);
        // Unconfirmed transport may later acknowledge this same fresh delivery.
        if (receipt.state === 'sending' || receipt.state === 'uncertain') continue;
        // Consume suppressed receipts too: moving panes, adding a link or freeing
        // a slot must never replay an old accepted delivery on a later poll.
        if (!accepted.has(receipt.state)) {
          remember(`${receipt.id}:delivery`);remember(`${receipt.id}:reply`);continue;
        }
        enqueue(receipt,'delivery');
        if (receipt.state === 'completed') {
          if (typeof receipt.result === 'string' && receipt.result.trim())enqueue(receipt,'reply');
          else remember(`${receipt.id}:reply`);
        }
      }
      place();
    }
    function onVisibility() { if (document.hidden) reset(); }
    document.addEventListener('visibilitychange',onVisibility);
    function dispose() {
      if (disposed) return;
      reset(); disposed = true; svg.remove();
      document.removeEventListener('visibilitychange',onVisibility);
    }
    return {update,reset,place,dispose};
  }
  window.DreamHandoffFlow = {create};
})();
