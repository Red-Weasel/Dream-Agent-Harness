/* Dream starter: animations.jsx — a timeline for video-style HTML.
 *
 * <Stage duration={8}> owns time: a requestAnimationFrame clock, a scrubber with
 * play/pause, auto-scale of a fixed canvas (1920×1080 by default) to the viewport,
 * and a remembered position (a refresh lands where you were). Compose scenes from
 * <Sprite start end> — a child that exists only inside its window — and read time
 * with useTime() (global seconds) or useSprite() ({t, local, progress}).
 * interpolate(t, [in0, in1], [out0, out1], {easing}) maps time to any value;
 * Easing has the usual curves. <FadeIn>, <FadeOut>, <SlideIn>, <Scale> are the
 * entry/exit primitives.
 *
 * Exporters and screenshots drive it through window.stageSeek(seconds),
 * window.stagePlay(), window.stagePause(), window.stageDuration.
 *
 *   <script type="text/babel" src="animations.jsx"></script>
 *   <script type="text/babel">
 *     ReactDOM.createRoot(document.getElementById('root')).render(
 *       <Stage duration={6}>
 *         <Sprite start={0} end={3}><FadeIn><h1>Hello</h1></FadeIn></Sprite>
 *         <Sprite start={2.5}><SlideIn from="bottom"><p>…</p></SlideIn></Sprite>
 *       </Stage>);
 *   </script>
 */
const { useState, useEffect, useRef, useContext, createContext, useMemo } = React;

const AnimStageContext = createContext(null);
const AnimSpriteContext = createContext(null);

const Easing = {
  linear: t => t,
  easeIn: t => t * t,
  easeOut: t => 1 - (1 - t) * (1 - t),
  easeInOut: t => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2),
  easeOutBack: t => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  easeOutElastic: t => (t === 0 || t === 1) ? t : Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * (2 * Math.PI / 3)) + 1,
};

function interpolate(t, [i0, i1], [o0, o1], { easing = Easing.linear, clamp = true } = {}) {
  let p = i1 === i0 ? 1 : (t - i0) / (i1 - i0);
  if (clamp) p = Math.max(0, Math.min(1, p));
  return o0 + (o1 - o0) * easing(p);
}

function useTime() {
  const s = useContext(AnimStageContext);
  return s ? s.t : 0;
}

function useSprite() {
  const sp = useContext(AnimSpriteContext);
  const t = useTime();
  if (!sp) return { t, local: t, progress: 0, start: 0, end: Infinity };
  const local = t - sp.start;
  const span = sp.end === Infinity ? Infinity : sp.end - sp.start;
  const progress = span === Infinity ? 0 : Math.max(0, Math.min(1, local / span));
  return { t, local, progress, start: sp.start, end: sp.end };
}

const animStageStyles = {
  root: { position: 'fixed', inset: 0, background: '#000', overflow: 'hidden' },
  canvas: { position: 'absolute', left: '50%', top: '50%', transformOrigin: 'center', overflow: 'hidden' },
  controls: { position: 'fixed', left: 12, right: 12, bottom: 10, display: 'flex', gap: 10, alignItems: 'center',
              font: '12px system-ui, sans-serif', color: '#fff', opacity: 0.85, zIndex: 2 },
  button: { all: 'unset', cursor: 'pointer', padding: '4px 10px', borderRadius: 6, background: 'rgba(255,255,255,.14)' },
  range: { flex: 1 },
};

function Stage({ duration = 10, fps = 60, width = 1920, height = 1080, autoplay = true, loop = true,
                 background = '#000', controls = true, children }) {
  const key = 'stage:' + location.pathname;
  const [t, setT] = useState(() => {
    try { const v = parseFloat(localStorage.getItem(key)); return isFinite(v) ? Math.min(duration, v) : 0; }
    catch (e) { return 0; }
  });
  const [playing, setPlaying] = useState(autoplay);
  const [scale, setScale] = useState(1);
  const tRef = useRef(t); tRef.current = t;

  useEffect(() => {
    const fit = () => setScale(Math.min(window.innerWidth / width, window.innerHeight / height));
    fit(); window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [width, height]);

  useEffect(() => {
    if (!playing) return;
    let raf, last = performance.now();
    const tick = now => {
      const dt = (now - last) / 1000; last = now;
      let n = tRef.current + dt;
      if (n >= duration) { if (loop) n = n % duration; else { n = duration; setPlaying(false); } }
      setT(n);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, duration, loop]);

  useEffect(() => { try { localStorage.setItem(key, String(t)); } catch (e) {} }, [t]);

  useEffect(() => {
    window.stageSeek = s => { setPlaying(false); setT(Math.max(0, Math.min(duration, +s || 0))); };
    window.stagePlay = () => setPlaying(true);
    window.stagePause = () => setPlaying(false);
    window.stageDuration = duration;
    return () => { delete window.stageSeek; delete window.stagePlay; delete window.stagePause; };
  }, [duration]);

  const ctx = useMemo(() => ({ t, duration, width, height, playing }), [t, duration, width, height, playing]);
  return (
    <AnimStageContext.Provider value={ctx}>
      <div style={animStageStyles.root}>
        <div data-stage style={{ ...animStageStyles.canvas, width, height, background,
                                 transform: `translate(-50%, -50%) scale(${scale})` }}>
          {children}
        </div>
        {controls && (
          <div data-stage-controls style={animStageStyles.controls}>
            <button style={animStageStyles.button} onClick={() => setPlaying(p => !p)} aria-label={playing ? 'pause' : 'play'}>
              {playing ? '❚❚' : '▶'}
            </button>
            <input type="range" min={0} max={duration} step={1 / fps} value={t} style={animStageStyles.range}
                   onChange={e => { setPlaying(false); setT(parseFloat(e.target.value)); }} />
            <span>{t.toFixed(2)}s / {duration}s</span>
          </div>
        )}
      </div>
    </AnimStageContext.Provider>
  );
}

function Sprite({ start = 0, end = Infinity, style, children }) {
  const t = useTime();
  if (t < start || t >= end) return null;
  return (
    <AnimSpriteContext.Provider value={{ start, end }}>
      <div data-sprite style={{ position: 'absolute', inset: 0, ...style }}>{children}</div>
    </AnimSpriteContext.Provider>
  );
}

function FadeIn({ duration = 0.5, style, children }) {
  const { local } = useSprite();
  const opacity = interpolate(local, [0, duration], [0, 1], { easing: Easing.easeOut });
  return <div style={{ opacity, ...style }}>{children}</div>;
}

function FadeOut({ duration = 0.5, style, children }) {
  const { t, end } = useSprite();
  const opacity = end === Infinity ? 1 : interpolate(t, [end - duration, end], [1, 0], { easing: Easing.easeIn });
  return <div style={{ opacity, ...style }}>{children}</div>;
}

function SlideIn({ from = 'left', distance = 240, duration = 0.6, easing = Easing.easeOut, style, children }) {
  const { local } = useSprite();
  const p = interpolate(local, [0, duration], [1, 0], { easing });
  const axis = (from === 'left' || from === 'right') ? 'X' : 'Y';
  const sign = (from === 'left' || from === 'top') ? -1 : 1;
  return <div style={{ transform: `translate${axis}(${(sign * distance * p).toFixed(2)}px)`, ...style }}>{children}</div>;
}

function Scale({ from = 0.8, duration = 0.5, easing = Easing.easeOutBack, style, children }) {
  const { local } = useSprite();
  const s = interpolate(local, [0, duration], [from, 1], { easing });
  return <div style={{ transform: `scale(${s.toFixed(4)})`, transformOrigin: 'center', ...style }}>{children}</div>;
}

Object.assign(window, { Stage, Sprite, FadeIn, FadeOut, SlideIn, Scale, useTime, useSprite, Easing, interpolate });
