/* Dream starter: ios_frame.jsx — an iPhone bezel with a real status bar.
 *
 *   <IosFrame time="9:41" dark keyboard label="01 Home">…screen content…</IosFrame>
 *
 * The screen is 393×852 CSS px by default (the 6.1" class). Content scrolls
 * inside the screen; the Dynamic Island, status bar, home indicator, and an
 * optional keyboard are drawn for you. Hit targets in your content should be
 * at least 44px.
 */
const iosFrameStyles = {
  shell: { position: 'relative', borderRadius: 60, background: '#1c1c1e', padding: 12, boxSizing: 'content-box',
           boxShadow: '0 30px 60px rgba(0,0,0,.45), inset 0 0 0 2px #3a3a3c', display: 'inline-block' },
  screen: { position: 'relative', borderRadius: 48, overflow: 'hidden', fontFamily: '-apple-system, "SF Pro Text", system-ui, sans-serif' },
  island: { position: 'absolute', top: 11, left: '50%', transform: 'translateX(-50%)', width: 126, height: 37, borderRadius: 20, background: '#000', zIndex: 3 },
  status: { position: 'absolute', top: 0, left: 0, right: 0, height: 54, display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', padding: '14px 28px 0', boxSizing: 'border-box', fontSize: 16, fontWeight: 600, zIndex: 2 },
  content: { position: 'absolute', top: 54, left: 0, right: 0, bottom: 0, overflow: 'auto' },
  home: { position: 'absolute', bottom: 8, left: '50%', transform: 'translateX(-50%)', width: 134, height: 5, borderRadius: 3, zIndex: 3 },
  keyboard: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: '8px 4px 40px', boxSizing: 'border-box',
              display: 'flex', flexDirection: 'column', gap: 10, zIndex: 2 },
  row: { display: 'flex', justifyContent: 'center', gap: 6 },
  key: { width: 32, height: 42, borderRadius: 5, display: 'grid', placeItems: 'center', fontSize: 20, fontWeight: 400,
         boxShadow: '0 1px 0 rgba(0,0,0,.35)' },
};

function IosStatusIcons({ color }) {
  return (
    <span style={{ display: 'inline-flex', gap: 7, alignItems: 'center' }} aria-hidden="true">
      <svg width="18" height="12" viewBox="0 0 18 12"><rect x="0" y="8" width="3" height="4" fill={color}/><rect x="5" y="6" width="3" height="6" fill={color}/><rect x="10" y="3" width="3" height="9" fill={color}/><rect x="15" y="0" width="3" height="12" fill={color}/></svg>
      <svg width="16" height="12" viewBox="0 0 16 12"><path d="M8 11.5 4.6 8.2a4.8 4.8 0 0 1 6.8 0zM2.4 6a8 8 0 0 1 11.2 0l-1.6 1.6a5.8 5.8 0 0 0-8 0zM0 3.6a11.3 11.3 0 0 1 16 0l-1.6 1.6a9 9 0 0 0-12.8 0z" fill={color}/></svg>
      <svg width="27" height="13" viewBox="0 0 27 13"><rect x=".5" y=".5" width="22" height="12" rx="3.5" stroke={color} fill="none" opacity=".4"/><rect x="2" y="2" width="19" height="9" rx="2" fill={color}/><path d="M24.5 4.5v4a2 2 0 0 0 0-4z" fill={color} opacity=".4"/></svg>
    </span>
  );
}

function IosStatusBar({ time = '9:41', dark = false }) {
  const color = dark ? '#fff' : '#000';
  return (
    <div style={{ ...iosFrameStyles.status, color }}>
      <span>{time}</span>
      <IosStatusIcons color={color} />
    </div>
  );
}

function IosKeyboard({ dark = false }) {
  const rows = ['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM'];
  const bg = dark ? '#2b2b2d' : '#d1d3d9', key = dark ? '#5a5a5e' : '#fff', fg = dark ? '#fff' : '#000';
  return (
    <div data-ios-keyboard style={{ ...iosFrameStyles.keyboard, background: bg, color: fg }}>
      {rows.map((r, i) => (
        <div key={i} style={iosFrameStyles.row}>
          {r.split('').map(k => <span key={k} style={{ ...iosFrameStyles.key, background: key }}>{k}</span>)}
        </div>
      ))}
      <div style={iosFrameStyles.row}><span style={{ ...iosFrameStyles.key, width: 190, background: key, fontSize: 15 }}>space</span></div>
    </div>
  );
}

function IosFrame({ width = 393, height = 852, time = '9:41', dark = false, keyboard = false, island = true,
                    background, label, style, children }) {
  const screenBg = background || (dark ? '#000' : '#fff');
  return (
    <div data-device="ios" data-screen-label={label} style={{ ...iosFrameStyles.shell, ...style }}>
      <div style={{ ...iosFrameStyles.screen, width, height, background: screenBg }}>
        {island && <div style={iosFrameStyles.island} />}
        <IosStatusBar time={time} dark={dark} />
        <div data-screen style={iosFrameStyles.content}>{children}</div>
        {keyboard && <IosKeyboard dark={dark} />}
        <div style={{ ...iosFrameStyles.home, background: dark ? '#fff' : '#000' }} />
      </div>
    </div>
  );
}

Object.assign(window, { IosFrame, IosStatusBar, IosKeyboard });
