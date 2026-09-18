/* Dream starter: <deck-stage> — the shell for any slide presentation.
 *
 * Put each slide as a direct <section> child. The component letterboxes a fixed
 * 1920×1080 canvas onto black and scales it to fit any viewport, handles keyboard
 * and tap navigation, shows a "3 / 12" counter, remembers the current slide in
 * localStorage (a refresh lands where you were), tags every slide with
 * data-screen-label ("01 Title") so comments can name a slide, posts
 * {slideIndexChanged: N} to the parent on init and on every change (speaker notes
 * key off it), and prints one page per slide.
 *
 * Exporters set the `noscale` attribute to drop the scaling and get raw 1920×1080
 * slides; window.goToSlide(i) is the global they call.
 *
 *   <deck-stage>
 *     <section><h1>Title</h1></section>
 *     <section data-screen-label="02 Agenda">…</section>
 *   </deck-stage>
 *   <script src="deck_stage.js"></script>
 */
(function () {
  const W = 1920, H = 1080;

  class DeckStage extends HTMLElement {
    constructor() {
      super();
      this.index = 0;
      this._root = this.attachShadow({ mode: 'open' });
      this._root.innerHTML = `
        <style>
          :host { display: block; position: fixed; inset: 0; background: #000; overflow: hidden; }
          :host([noscale]) { position: static; background: #fff; overflow: visible; }
          .stage { position: absolute; left: 50%; top: 50%; width: ${W}px; height: ${H}px;
                   transform: translate(-50%, -50%) scale(var(--deck-scale, 1));
                   transform-origin: center; background: #fff; overflow: hidden; }
          :host([noscale]) .stage { position: static; transform: none; }
          ::slotted(section) { position: absolute; inset: 0; width: ${W}px; height: ${H}px;
                               box-sizing: border-box; display: none; overflow: hidden; }
          ::slotted(section[data-active]) { display: block; }
          .nav { position: fixed; right: 14px; bottom: 10px; z-index: 2; display: flex; gap: 8px;
                 align-items: center; font: 13px/1 system-ui, sans-serif; color: #fff;
                 opacity: .75; user-select: none; }
          .nav button { all: unset; cursor: pointer; padding: 4px 8px; border-radius: 6px;
                        background: rgba(255,255,255,.12); }
          .nav button:hover { background: rgba(255,255,255,.28); }
          :host([noscale]) .nav { display: none; }
          @media print {
            :host { position: static; background: #fff; overflow: visible; }
            .stage { position: static; transform: none; width: ${W}px; height: auto; overflow: visible; }
            ::slotted(section) { display: block !important; position: relative; inset: auto;
                                 page-break-after: always; break-after: page; page-break-inside: avoid; }
            .nav { display: none; }
          }
        </style>
        <div class="stage" part="stage"><slot></slot></div>
        <div class="nav" part="nav">
          <button id="prev" aria-label="previous slide">‹</button>
          <span id="count"></span>
          <button id="next" aria-label="next slide">›</button>
        </div>`;
    }

    connectedCallback() {
      this._slides = Array.from(this.children).filter(el => el.tagName === 'SECTION');
      this._slides.forEach((s, i) => {
        if (!s.hasAttribute('data-screen-label')) {
          const h = s.querySelector('h1, h2, h3');
          const title = (h && h.textContent.trim()) || `Slide ${i + 1}`;
          s.setAttribute('data-screen-label', `${String(i + 1).padStart(2, '0')} ${title}`);
        }
        s.setAttribute('data-om-validate', '');
      });
      // print: one page per slide, at the canvas size
      if (!document.getElementById('deck-stage-print')) {
        const st = document.createElement('style');
        st.id = 'deck-stage-print';
        st.textContent = `@page { size: ${W}px ${H}px; margin: 0; }
          @media print { html, body { margin: 0; background: #fff; } }`;
        document.head.appendChild(st);
      }
      this._key = `deck-stage:${location.pathname}`;
      let start = 0;
      try { start = parseInt(localStorage.getItem(this._key) || '0', 10) || 0; } catch (e) {}
      const fromHash = /^#(\d+)$/.exec(location.hash);
      if (fromHash) start = parseInt(fromHash[1], 10) - 1;

      this._root.getElementById('prev').onclick = e => { e.stopPropagation(); this.prev(); };
      this._root.getElementById('next').onclick = e => { e.stopPropagation(); this.next(); };
      this._root.querySelector('.stage').addEventListener('click', e => {
        // tap: right half forward, left half back — unless something interactive was hit
        if (e.target.closest && e.target.closest('a, button, input, textarea, select, [data-no-nav]')) return;
        const r = this.getBoundingClientRect();
        (e.clientX - r.left) > r.width / 2 ? this.next() : this.prev();
      });
      this._onKey = e => {
        if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
        const k = e.key;
        if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'PageDown' || k === ' ') { e.preventDefault(); this.next(); }
        else if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'PageUp') { e.preventDefault(); this.prev(); }
        else if (k === 'Home') { e.preventDefault(); this.goTo(0); }
        else if (k === 'End') { e.preventDefault(); this.goTo(this._slides.length - 1); }
      };
      window.addEventListener('keydown', this._onKey);
      this._onResize = () => this._fit();
      window.addEventListener('resize', this._onResize);
      this._fit();
      this.goTo(start, true);
      window.goToSlide = i => this.goTo(i);
    }

    disconnectedCallback() {
      window.removeEventListener('keydown', this._onKey);
      window.removeEventListener('resize', this._onResize);
    }

    get length() { return this._slides.length; }

    _fit() {
      if (this.hasAttribute('noscale')) { this.style.removeProperty('--deck-scale'); return; }
      const s = Math.min(window.innerWidth / W, window.innerHeight / H);
      this.style.setProperty('--deck-scale', String(s));
      this.scale = s;
    }

    goTo(i, silent) {
      const n = this._slides.length;
      if (!n) return;
      i = Math.max(0, Math.min(n - 1, i | 0));
      this._slides.forEach((s, j) => j === i ? s.setAttribute('data-active', '') : s.removeAttribute('data-active'));
      this.index = i;
      this._root.getElementById('count').textContent = `${i + 1} / ${n}`;
      try { localStorage.setItem(this._key, String(i)); } catch (e) {}
      try { history.replaceState(null, '', `#${i + 1}`); } catch (e) {}
      try { window.parent.postMessage({ slideIndexChanged: i }, '*'); } catch (e) {}
      if (!silent) this.dispatchEvent(new CustomEvent('slidechange', { detail: { index: i } }));
    }
    next() { this.goTo(this.index + 1); }
    prev() { this.goTo(this.index - 1); }
  }

  if (!customElements.get('deck-stage')) customElements.define('deck-stage', DeckStage);
})();
