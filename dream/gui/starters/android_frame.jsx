/* Dream starter: android_frame.jsx — an Android phone bezel.
 *
 *   <AndroidFrame time="12:30" dark nav="gesture" keyboard label="02 Feed">…</AndroidFrame>
 *
 * 412×915 CSS px screen by default. Punch-hole camera, status bar, content that
 * scrolls, a gesture pill (nav="gesture") or three-button bar (nav="buttons"),
 * and an optional keyboard.
 */
const androidFrameStyles = {
  shell: { position: 'relative', borderRadius: 40, background: '#202124', padding: 10, display: 'inline-block',
           boxShadow: '0 30px 60px rgba(0,0,0,.45), inset 0 0 0 2px #3c4043' },
  screen: { position: 'relative', borderRadius: 32, overflow: 'hidden', fontFamily: 'Roboto, "Google Sans", system-ui, sans-serif' },
  camera: { position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', width: 14, height: 14, borderRadius: '50%', background: '#000', zIndex: 3, boxShadow: 'inset 0 0 0 3px #111' },
  status: { position: 'absolute', top: 0, left: 0, right: 0, height: 40, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 24px', boxSizing: 'border-box', fontSize: 14, fontWeight: 500, zIndex: 2 },
  content: { position: 'absolute', top: 40, left: 0, right: 0, bottom: 0, overflow: 'auto' },
  gesture: { position: 'absolute', bottom: 8, left: '50%', transform: 'translateX(-50%)', width: 120, height: 4, borderRadius: 2, zIndex: 3 },
  buttons: { position: 'absolute', bottom: 0, left: 0, right: 0, height: 48, display: 'flex', justifyContent: 'space-around', alignItems: 'center', zIndex: 3 },
  keyboard: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: '8px 6px 56px', boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 8, zIndex: 2 },
  row: { display: 'flex', justifyContent: 'center', gap: 5 },
  key: { width: 34, height: 44, borderRadius: 6, display: 'grid', placeItems: 'center', fontSize: 18 },
};

function AndroidStatusBar({ time = '12:30', dark = false }) {
  const color = dark ? '#fff' : '#1f1f1f';
  return (
    <div style={{ ...androidFrameStyles.status, color }}>
      <span>{time}</span>
      <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }} aria-hidden="true">
        <svg width="16" height="14" viewBox="0 0 16 14"><path d="M0 4a11 11 0 0 1 16 0l-1.8 1.8a8.5 8.5 0 0 0-12.4 0zM3 7a7 7 0 0 1 10 0l-1.8 1.8a4.5 4.5 0 0 0-6.4 0zM8 13.5 5.9 11.4a3 3 0 0 1 4.2 0z" fill={color}/></svg>
        <svg width="14" height="14" viewBox="0 0 14 14"><path d="M14 0v14H0z" fill={color}/></svg>
        <svg width="10" height="16" viewBox="0 0 10 16"><rect x="3" y="0" width="4" height="2" fill={color}/><rect x="0" y="2" width="10" height="14" rx="1.5" fill={color}/></svg>
      </span>
    </div>
  );
}

function AndroidKeyboard({ dark = false }) {
  const rows = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm'];
  const bg = dark ? '#1f1f1f' : '#e8eaed', key = dark ? '#3c4043' : '#fff', fg = dark ? '#fff' : '#1f1f1f';
  return (
    <div data-android-keyboard style={{ ...androidFrameStyles.keyboard, background: bg, color: fg }}>
      {rows.map((r, i) => (
        <div key={i} style={androidFrameStyles.row}>
          {r.split('').map(k => <span key={k} style={{ ...androidFrameStyles.key, background: key }}>{k}</span>)}
        </div>
      ))}
      <div style={androidFrameStyles.row}><span style={{ ...androidFrameStyles.key, width: 200, background: key, fontSize: 14 }}>space</span></div>
    </div>
  );
}

function AndroidFrame({ width = 412, height = 915, time = '12:30', dark = false, nav = 'gesture', keyboard = false,
                        background, label, style, children }) {
  const screenBg = background || (dark ? '#121212' : '#fff');
  const fg = dark ? '#fff' : '#1f1f1f';
  return (
    <div data-device="android" data-screen-label={label} style={{ ...androidFrameStyles.shell, ...style }}>
      <div style={{ ...androidFrameStyles.screen, width, height, background: screenBg }}>
        <div style={androidFrameStyles.camera} />
        <AndroidStatusBar time={time} dark={dark} />
        <div data-screen style={androidFrameStyles.content}>{children}</div>
        {keyboard && <AndroidKeyboard dark={dark} />}
        {nav === 'buttons'
          ? <div data-nav="buttons" style={{ ...androidFrameStyles.buttons, color: fg }}>
              <span aria-label="back">◁</span><span aria-label="home">○</span><span aria-label="recents">□</span>
            </div>
          : <div data-nav="gesture" style={{ ...androidFrameStyles.gesture, background: fg }} />}
      </div>
    </div>
  );
}

Object.assign(window, { AndroidFrame, AndroidStatusBar, AndroidKeyboard });
