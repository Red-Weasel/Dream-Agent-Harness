/* Dream starter: design_canvas.jsx — several options side by side.
 *
 * Use it when presenting 2+ static variations: a grid of labeled cells, each with
 * its own note, so the user can compare and mix. Every cell carries
 * data-screen-label so a comment can name the option it is about.
 *
 *   <DesignCanvas title="Button" subtitle="6 directions" cols={3}>
 *     <Option label="A · Solid" note="matches the kit">…</Option>
 *     <Option label="B · Ghost" width={320} height={200}>…</Option>
 *   </DesignCanvas>
 */
const designCanvasStyles = {
  root: { minHeight: '100vh', boxSizing: 'border-box', padding: '40px 48px 64px', background: '#0f1115',
          color: '#e8eaf0', fontFamily: 'ui-sans-serif, system-ui, sans-serif' },
  header: { marginBottom: 28 },
  title: { margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: 0.2 },
  subtitle: { margin: '6px 0 0', fontSize: 13, color: '#9aa0ad' },
  grid: { display: 'grid', gap: 24 },
  cell: { margin: 0, display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 },
  frame: { background: '#fff', borderRadius: 10, overflow: 'hidden', boxShadow: '0 1px 0 rgba(255,255,255,.06), 0 12px 32px rgba(0,0,0,.35)',
           display: 'grid', placeItems: 'center' },
  caption: { display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12 },
  label: { fontWeight: 600, color: '#e8eaf0' },
  note: { color: '#9aa0ad' },
};

function DesignCanvas({ title, subtitle, cols = 3, gap = 24, background, children }) {
  return (
    <div data-design-canvas style={{ ...designCanvasStyles.root, ...(background ? { background } : {}) }}>
      {(title || subtitle) && (
        <header style={designCanvasStyles.header}>
          {title && <h1 style={designCanvasStyles.title}>{title}</h1>}
          {subtitle && <p style={designCanvasStyles.subtitle}>{subtitle}</p>}
        </header>
      )}
      <div style={{ ...designCanvasStyles.grid, gap, gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
        {children}
      </div>
    </div>
  );
}

function Option({ label, note, width, height = 220, background = '#fff', padding = 0, children }) {
  return (
    <figure data-screen-label={label} style={designCanvasStyles.cell}>
      <div style={{ ...designCanvasStyles.frame, width: width || '100%', height, background, padding, boxSizing: 'border-box' }}>
        {children}
      </div>
      <figcaption style={designCanvasStyles.caption}>
        <span style={designCanvasStyles.label}>{label}</span>
        {note && <span style={designCanvasStyles.note}>{note}</span>}
      </figcaption>
    </figure>
  );
}

Object.assign(window, { DesignCanvas, Option });
