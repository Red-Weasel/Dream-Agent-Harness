/* Dream starter: browser_window.jsx — a browser with a tab strip and address bar.
 *
 *   <BrowserWindow url="https://acme.app/dashboard" tabs={['Dashboard', 'Billing']} width={1200} height={760}>
 *     …the web page…
 *   </BrowserWindow>
 */
const browserWindowStyles = {
  shell: { position: 'relative', display: 'inline-flex', flexDirection: 'column', borderRadius: 12, overflow: 'hidden', background: '#fff',
           boxShadow: '0 0 0 1px rgba(0,0,0,.12), 0 24px 60px rgba(0,0,0,.35)', fontFamily: 'ui-sans-serif, system-ui, sans-serif' },
  tabs: { display: 'flex', alignItems: 'flex-end', gap: 4, height: 40, padding: '8px 10px 0', boxSizing: 'border-box', background: '#dfe1e5', flex: '0 0 auto' },
  lights: { display: 'inline-flex', gap: 7, marginRight: 10, marginBottom: 10 },
  light: { width: 11, height: 11, borderRadius: '50%' },
  tab: { padding: '7px 14px', fontSize: 12, borderRadius: '8px 8px 0 0', color: '#3c4043', background: 'transparent', maxWidth: 200,
         overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  tabActive: { background: '#fff', fontWeight: 500 },
  bar: { display: 'flex', alignItems: 'center', gap: 10, height: 44, padding: '0 12px', boxSizing: 'border-box', background: '#fff',
         borderBottom: '1px solid #dadce0', flex: '0 0 auto' },
  navBtn: { color: '#5f6368', fontSize: 16, width: 22, textAlign: 'center' },
  address: { flex: 1, display: 'flex', alignItems: 'center', gap: 8, height: 30, padding: '0 12px', borderRadius: 15, background: '#f1f3f4',
             fontSize: 13, color: '#202124', overflow: 'hidden' },
  url: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  content: { position: 'relative', flex: '1 1 auto', overflow: 'auto', minHeight: 0 },
};

function BrowserWindow({ url = 'https://example.com', tabs = ['New tab'], active = 0, width = 1200, height = 760, label, style, children }) {
  const host = (() => { try { return new URL(url).host; } catch (e) { return url; } })();
  const rest = url.replace(/^https?:\/\/[^/]*/, '');
  return (
    <div data-window="browser" data-screen-label={label} style={{ ...browserWindowStyles.shell, width, height, ...style }}>
      <div style={browserWindowStyles.tabs}>
        <span style={browserWindowStyles.lights} aria-hidden="true">
          <span style={{ ...browserWindowStyles.light, background: '#ff5f57' }} />
          <span style={{ ...browserWindowStyles.light, background: '#febc2e' }} />
          <span style={{ ...browserWindowStyles.light, background: '#28c840' }} />
        </span>
        {tabs.map((t, i) => (
          <span key={i} data-tab={i === active ? 'active' : ''} style={{ ...browserWindowStyles.tab, ...(i === active ? browserWindowStyles.tabActive : {}) }}>{t}</span>
        ))}
      </div>
      <div style={browserWindowStyles.bar}>
        <span style={browserWindowStyles.navBtn} aria-hidden="true">←</span>
        <span style={browserWindowStyles.navBtn} aria-hidden="true">→</span>
        <span style={browserWindowStyles.navBtn} aria-hidden="true">⟳</span>
        <div data-address style={browserWindowStyles.address}>
          <svg width="12" height="14" viewBox="0 0 12 14" aria-hidden="true"><rect x="1" y="6" width="10" height="7.5" rx="1.5" fill="#5f6368"/><path d="M3 6V4.5a3 3 0 0 1 6 0V6" stroke="#5f6368" strokeWidth="1.6" fill="none"/></svg>
          <span style={browserWindowStyles.url}><b style={{ fontWeight: 500 }}>{host}</b><span style={{ color: '#5f6368' }}>{rest}</span></span>
        </div>
      </div>
      <div data-screen style={browserWindowStyles.content}>{children}</div>
    </div>
  );
}

Object.assign(window, { BrowserWindow });
