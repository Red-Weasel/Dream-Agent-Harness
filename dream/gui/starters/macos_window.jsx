/* Dream starter: macos_window.jsx — desktop window chrome with traffic lights.
 *
 *   <MacosWindow title="Settings" width={960} height={600} toolbar={<Toolbar/>} label="03 Settings">…</MacosWindow>
 */
const macosWindowStyles = {
  shell: { position: 'relative', display: 'inline-flex', flexDirection: 'column', borderRadius: 12, overflow: 'hidden',
           background: '#fff', boxShadow: '0 0 0 1px rgba(0,0,0,.12), 0 24px 60px rgba(0,0,0,.35)',
           fontFamily: '-apple-system, "SF Pro Text", system-ui, sans-serif' },
  titlebar: { position: 'relative', height: 52, display: 'flex', alignItems: 'center', padding: '0 12px', boxSizing: 'border-box',
              background: 'linear-gradient(#ececec, #dcdcdc)', borderBottom: '1px solid #b8b8b8', flex: '0 0 auto' },
  lights: { display: 'inline-flex', gap: 8 },
  light: { width: 12, height: 12, borderRadius: '50%', boxShadow: 'inset 0 0 0 .5px rgba(0,0,0,.2)' },
  title: { position: 'absolute', left: 0, right: 0, textAlign: 'center', fontSize: 13, fontWeight: 600, color: '#3a3a3a', pointerEvents: 'none' },
  toolbar: { marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' },
  content: { position: 'relative', flex: '1 1 auto', overflow: 'auto', minHeight: 0 },
};

function MacosWindow({ title = 'Untitled', width = 960, height = 600, dark = false, toolbar, label, style, children }) {
  const bar = dark ? { background: 'linear-gradient(#3a3a3c, #2c2c2e)', borderBottom: '1px solid #1a1a1a' } : {};
  return (
    <div data-window="macos" data-screen-label={label} style={{ ...macosWindowStyles.shell, width, height, background: dark ? '#1e1e1e' : '#fff', ...style }}>
      <div style={{ ...macosWindowStyles.titlebar, ...bar }}>
        <span style={macosWindowStyles.lights} aria-hidden="true">
          <span style={{ ...macosWindowStyles.light, background: '#ff5f57' }} />
          <span style={{ ...macosWindowStyles.light, background: '#febc2e' }} />
          <span style={{ ...macosWindowStyles.light, background: '#28c840' }} />
        </span>
        <span data-title style={{ ...macosWindowStyles.title, color: dark ? '#e5e5e5' : '#3a3a3a' }}>{title}</span>
        {toolbar && <span style={macosWindowStyles.toolbar}>{toolbar}</span>}
      </div>
      <div data-screen style={macosWindowStyles.content}>{children}</div>
    </div>
  );
}

Object.assign(window, { MacosWindow });
