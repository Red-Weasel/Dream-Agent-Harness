"""The panel beside the chat in the desktop's real web view (DREAM-233, batch A1): the real `DreamWindow`'s
WebKitGTK view at its default size, on a private headless GTK display, with a scripted build (the real registry,
the real `write_file` handler). Proves in the engine the owner sees what tests/test_chat_artifact_panel.py proves
in Chromium: the panel to the right of the chat with the page painted (a pixel of its header), the slide from the
right, Preview | Code, close and the card, and that nothing took the focus.

The driver process and its protocol are tests/test_chat_webkit.py's (`--driver` runs that module's driver here);
this file adds the session with a registry. Skips without the GI bindings or `broadwayd`; run with the system
Python (`run-sys.sh`). No model, no engine."""
from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

if __name__ == '__main__' and sys.argv[1:] == ['--driver']:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_chat_webkit import driver
    sys.exit(driver())

import pytest  # noqa: E402

from dream.gui import preview as preview_mod  # noqa: E402
from dream.gui.artifacts import ArtifactRegistry  # noqa: E402
from dream.gui.bus import EventBus  # noqa: E402
from dream.gui.server import StudioServer  # noqa: E402
from dream.tools import context as tool_context  # noqa: E402
from dream.tools.context import ToolContext, set_context, set_studio  # noqa: E402
from test_chat_artifact_panel import COFFEE, Scripted  # noqa: E402
from test_chat_webkit import WebKit  # noqa: E402

pytestmark = pytest.mark.skipif(importlib.util.find_spec('gi') is None or shutil.which('broadwayd') is None,
                                reason='System GTK/WebKit bindings and broadwayd required')
SHOTS = Path(os.environ.get('DREAM_A1_SHOTS') or '/nonexistent')
X11 = os.environ.get('DREAM_CHAT_WEBKIT_X11')


@pytest.fixture
async def desktop_build(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    monkeypatch.setattr(preview_mod, 'display_render_node', lambda: None)
    ws = tmp_path / 'ws'
    ws.mkdir()
    prompts = []
    bus = EventBus()
    registry = ArtifactRegistry(ws, 's1', bus.publish, store=tmp_path / 'store')
    srv = StudioServer(bus, on_prompt=prompts.append, session={'workspace': str(ws), 'provider': 'test', 'model': 'fixture',
                       'session_id': 'chat-test'}, artifacts=registry, follow_model_view=False)
    scripted = Scripted(srv, registry, ws)
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id='s1', workspace=ws, emit=scripted.funnel))
    preview_mod._PREVIEW = None
    url = await srv.start()
    set_studio(srv)
    runtime = tempfile.mkdtemp(prefix='dream-wk-')
    os.chmod(runtime, 0o700)
    env = {'HOME': os.environ.get('HOME', runtime), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'XDG_RUNTIME_DIR': runtime, 'GDK_BACKEND': 'broadway', 'BROADWAY_DISPLAY': ':6', 'NO_AT_BRIDGE': '1',
           'PYTHONPATH': os.environ.get('PYTHONPATH', ''), 'PYTHONDONTWRITEBYTECODE': '1'}
    log = open(Path(runtime) / 'driver.log', 'wb')
    display = process = None
    try:
        if X11:
            env.update(GDK_BACKEND='x11', DISPLAY=X11)
        else:
            display = subprocess.Popen(['broadwayd', '--unixsocket', str(Path(runtime) / 'http.socket'), ':6'],
                                       env=env, stdout=log, stderr=log)
            for _ in range(100):
                if b'Listening on' in Path(log.name).read_bytes():
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.skip('broadwayd did not start')
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), '--driver',
                                                       env=env, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=log)
        wk = WebKit(process)
        await wk.answer()
        base, _, token = url.partition('/#token=')
        await wk.call('connect', base=base, token=token, session_id='chat-test')
        await wk.call('focus')
        await wk.wait("document.getElementById('stat')?.textContent === 'Ready' && document.documentElement.classList.contains('dream-native')")
        yield srv, wk, scripted, prompts
    except BaseException:
        log.flush()
        sys.stderr.write(Path(log.name).read_text(errors='replace')[-4000:])
        raise
    finally:
        if process is not None:
            try:
                await asyncio.wait_for(wk.call('quit'), 5)
                await asyncio.wait_for(process.wait(), 5)
            except Exception:
                process.kill()
        if display is not None:
            display.terminate(); display.wait(5)
        log.close()
        set_studio(None)
        tool_context._CTX = None
        await srv.stop()
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None
        shutil.rmtree(runtime, ignore_errors=True)


GEOMETRY = """(() => {
  const box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right}; };
  const panel = document.getElementById('artpanel'), frame = document.querySelector('#artbody iframe');
  return {main: box(document.getElementById('main')), panel: box(panel), split: box(document.querySelector('.split')),
          frame: frame ? box(frame) : null, panelShown: getComputedStyle(panel).display !== 'none' && panel.getBoundingClientRect().width > 0,
          view: document.documentElement.dataset.dreamView, chatArt: document.body.classList.contains('chat-art'),
          transform: getComputedStyle(panel).transform, transition: getComputedStyle(panel).transitionDuration,
          active: document.activeElement ? document.activeElement.id || document.activeElement.tagName : null,
          title: document.getElementById('artname').textContent, vn: document.getElementById('artvn').textContent,
          inner: [innerWidth, innerHeight]};
})()"""


async def test_the_build_opens_the_panel_beside_the_chat_in_the_real_window(desktop_build, tmp_path):
    srv, wk, scripted, prompts = desktop_build
    g0 = await wk.js(GEOMETRY)
    assert g0['view'] == 'chat' and not g0['panelShown'] and g0['inner'][0] >= 1000, g0
    await wk.js("""(() => { window.__slide = []; const p = document.getElementById('artpanel');
      for(const k of ['transitionstart', 'transitionend']) p.addEventListener(k, e => { if(e.target === p && e.propertyName === 'transform') __slide.push([k, getComputedStyle(p).transform]); });
      return true; })()""")
    await scripted.turn('build a landing page for a coffee shop',
                        [('write_file', {'path': 'coffee-shop/index.html', 'content': COFFEE})])
    await wk.wait("!!document.querySelector('#artbody iframe') && document.body.classList.contains('chat-art')")
    heading = await srv.ask_frame('eval', {'code': "document.getElementById('h').textContent"}, timeout=10)
    assert heading == 'Beans & Steam', heading                                                  # the page runs in the frame
    g = await wk.js(GEOMETRY)
    print('WEBKIT PANEL:', g)
    assert g['view'] == 'chat' and g['chatArt'] and g['panelShown'], g                         # the view stayed Chat
    assert g['panel']['x'] >= g['main']['r'] - 1 and g['main']['w'] >= 360, g                  # right of the chat
    assert g['frame'] and g['frame']['w'] >= 300 and g['frame']['h'] >= 300, g
    assert g['title'] == 'Coffee Shop' and g['vn'] == 'v1', g
    assert g['active'] in ('input', 'BODY', None), g                                            # nothing took the focus
    slide = await wk.wait('__slide.length >= 2 && __slide')
    assert slide[0][0] == 'transitionstart' and slide[0][1] not in ('none', 'matrix(1, 0, 0, 1, 0, 0)'), slide   # it came from the right
    assert slide[-1][0] == 'transitionend' and g['transition'] == '0.2s', (slide, g)
    # What was painted: the page's header (#6b3e26) inside the frame, and the chat's own surface left of the panel.
    fx, fy = g['frame']['x'] + 24, g['frame']['y'] + 24
    painted = (await wk.call('snapshot', path=str(Path(SHOTS if SHOTS.is_dir() else tmp_path) / 'webkit-chat-panel.png'),
                             pixels=[[fx, fy], [g['main']['x'] + 8, g['main']['y'] + g['main']['h'] / 2]]))['pixels']
    print('WEBKIT PIXELS:', painted)
    assert all(abs(a - b) <= 12 for a, b in zip(painted[0], [0x6b, 0x3e, 0x26])), painted      # the header colour
    assert painted[1] != painted[0], painted
    if SHOTS.is_dir():
        await wk.call('window_png', path=str(SHOTS / 'webkit-window-chat-panel.png'))
    # DREAM-240, in the real window: nothing above the work area, the chat is a column with the composer inside it,
    # the preview runs down beside it, and the resize edge sits between them.
    await wk.wait("document.body.classList.contains('cn-column')")
    col = await wk.js("""(() => { const r = s => { const b = document.querySelector(s).getBoundingClientRect(); return {x: b.x, y: b.y, w: b.width, h: b.height, r: b.right, b: b.bottom}; };
        return {main: r('#main'), panel: r('#artpanel'), foot: r('body>footer'), edge: r('#chat-split'), split: r('.split'),
                header: getComputedStyle(document.querySelector('body>header')).display, bar: getComputedStyle(document.getElementById('dream-workbar')).display}; })()""")
    print('WEBKIT COLUMN:', col)
    assert col['header'] == 'none' and col['bar'] == 'none' and col['split']['y'] <= 16, col
    assert abs(col['foot']['r'] - col['main']['r']) <= 1 and abs(col['panel']['b'] - col['foot']['b']) <= 1, col
    assert col['edge']['w'] == 8 and abs(col['edge']['x'] - col['main']['r']) <= 2 and col['main']['w'] >= 360 and col['panel']['w'] >= 388, col
    # Preview | Code by a real click; Code shows the file
    await wk.click('#artcode')
    await wk.wait("document.querySelector('#artbody pre')?.textContent.includes('<title>Coffee Shop</title>')")
    assert (await wk.js(GEOMETRY))['view'] == 'chat'
    await wk.click('#artprev')
    await wk.wait("!!document.querySelector('#artbody iframe')")
    # Close: the chat is back at full width and the card stays
    await wk.click('#artclose')
    await wk.wait("!document.querySelector('#artbody iframe')")
    g2 = await wk.js(GEOMETRY)
    assert not g2['panelShown'] and g2['view'] == 'chat' and abs(g2['main']['w'] - g2['split']['w']) <= 2, g2   # #main's 1 px side borders
    text = await wk.js("(document.querySelector('#stream .artcard')?.textContent || '').split(/\\s+/).join(' ').trim()")
    assert text == '</> Coffee Shop · Page · v1 · Open', text          # the kind's glyph, the title, kind, version, Open
    if SHOTS.is_dir():
        await wk.call('snapshot', path=str(SHOTS / 'webkit-chat-card.png'))
    await wk.click('#stream .artcard .go')
    await wk.wait("!!document.querySelector('#artbody iframe') && document.body.classList.contains('chat-art')")
    assert prompts == ['build a landing page for a coffee shop']


HEADER = """(() => {
  const box = e => { const b = e.getBoundingClientRect(); return {w: b.width, h: b.height, x: b.x, r: b.right, clipped: e.scrollWidth > e.clientWidth + 1}; };
  const at = id => box(document.getElementById(id));
  return {name: at('artname'), more: at('artmore'), ext: at('artext'), refresh: at('artrefresh'), menu: at('artmenu'),
          seg: [...document.querySelectorAll('#artpanel .seg button')].map(box), bar: box(document.querySelector('#artpanel .artbar')),
          inner: [innerWidth, innerHeight]};
})()"""


async def test_the_header_its_menu_a_report_and_the_artifacts_tab_in_the_real_window(desktop_build, tmp_path):
    """DREAM-235 / DREAM-236 in the engine the owner sees: the header's title has its room and the secondary actions
    are the menu (a container query), a report renders, and the Artifacts tab lists what was built and opens it at
    full width."""
    srv, wk, scripted, prompts = desktop_build
    await scripted.turn('build a landing page for a coffee shop',
                        [('write_file', {'path': 'coffee-shop/index.html', 'content': COFFEE})])
    await wk.wait("!!document.querySelector('#artbody iframe') && document.body.classList.contains('chat-art')")
    await wk.wait("document.getElementById('artext').style.display !== 'none'")
    # the slide has ended: the header is where a click will find it
    await wk.wait("['none', 'matrix(1, 0, 0, 1, 0, 0)'].includes(getComputedStyle(document.getElementById('artpanel')).transform)")
    h = await wk.js(HEADER)
    print('WEBKIT HEADER:', h)
    assert h['bar']['x'] >= 0 and h['bar']['r'] <= h['inner'][0] + 1, h
    assert h['name']['w'] >= 200 and not h['name']['clipped'], h                               # the title in full
    assert len(h['seg']) == 2 and all(b['w'] > 0 and not b['clipped'] for b in h['seg']), h   # Preview | Code whole
    assert h['bar']['h'] <= 52 and 0 < h['ext']['h'] <= 34, h                                  # one row, one line
    assert h['more']['w'] > 0 and h['refresh']['w'] == 0, h                                    # the actions are the menu
    await wk.click('#artmore')
    await wk.wait("document.getElementById('artmenu').classList.contains('open')")
    h = await wk.js(HEADER)
    assert h['refresh']['w'] > 0 and h['menu']['x'] >= 0 and h['menu']['r'] <= h['inner'][0], h
    if SHOTS.is_dir():
        await wk.call('snapshot', path=str(SHOTS / 'webkit-header-menu.png'))
    await wk.click('#artmore')
    await wk.wait("!document.getElementById('artmenu').classList.contains('open')")
    # a report: its Markdown rendered in the panel, no frame
    await scripted.turn('write it up', [('write_file', {'path': 'notes.md', 'content': '# Field notes\n\n| a | b |\n|---|---|\n| 1 | 2 |\n'})])
    await wk.wait("document.querySelector('#artbody .artdoc h1')?.textContent === 'Field notes'")
    assert await wk.js("[document.querySelectorAll('#artbody .artdoc td').length, document.querySelectorAll('#artbody iframe').length]") == [2, 0]
    if SHOTS.is_dir():
        await wk.call('snapshot', path=str(SHOTS / 'webkit-report.png'))
    # the Artifacts tab: what the sidebar's entry dispatches (DreamWindow._dispatch_view)
    await wk.js("(window.dispatchEvent(new CustomEvent('dream:navigate', {detail: {view: 'studio', session_id: 'chat-test'}})), true)")
    await wk.wait("document.querySelectorAll('#art-home-list .art-row').length === 2")
    home = await wk.js("""(() => { const r = id => document.getElementById(id).getBoundingClientRect();
        return {view: document.documentElement.dataset.dreamView, home: r('studio-empty').width, body: r('artbody').width, main: r('main').width,
                titles: [...document.querySelectorAll('#art-home-list .art-row b')].map(b => b.textContent)}; })()""")
    assert home['view'] == 'studio' and home['home'] > 0 and home['body'] == 0 and home['main'] == 0, home
    assert home['titles'] == ['Field notes', 'Coffee Shop'], home                              # newest first
    if SHOTS.is_dir():
        await wk.call('snapshot', path=str(SHOTS / 'webkit-artifacts-tab.png'))
    await wk.click('#art-home-list .art-row .primary')                                          # Open: the tab's viewer
    await wk.wait("!document.body.classList.contains('art-home') && document.querySelector('#artbody .artdoc h1')?.textContent === 'Field notes'")
    g = await wk.js(GEOMETRY)
    assert g['view'] == 'studio' and not g['chatArt'] and g['main']['w'] == 0 and g['panel']['w'] >= g['split']['w'] - 4, g


async def test_the_nested_views_right_hand_pane_in_the_real_window(desktop_build, tmp_path):
    """DREAM-243 in WebKitGTK: what Dream builds opens in the Nested view's Preview pane, over the workers' area and
    under their bar, the orchestrator column untouched; Map lays the Understand dock over the same area; a tool call
    shows as "Writing" in the column while the model writes it (DREAM-242)."""
    from dream.core.backends.base import Event
    srv, wk, scripted, prompts = desktop_build
    await wk.js("(() => { document.getElementById('dream-nav-nested').click(); return true; })()")
    await wk.wait("document.documentElement.dataset.dreamView === 'nested' && !!document.getElementById('nd-pane-preview')")
    geo = """(() => { const r = s => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect();
        return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom), shown: getComputedStyle(e).display !== 'none' && b.width > 0}; };
      return {orch: r('.nd-orch'), top: r('.nd-top'), panel: r('#artpanel'), dock: r('#dream-understand'), split: r('.split'),
              pane: document.documentElement.dataset.ndPane, view: document.documentElement.dataset.dreamView,
              tabs: [...document.querySelectorAll('.nd-top [role=tab]')].map(t => t.textContent)}; })()"""
    g0 = await wk.js(geo)
    assert g0['tabs'] == ['Workers', 'Preview', 'Map'] and g0['pane'] == 'workers', g0
    scripted.funnel(Event('turn_start', {}))
    scripted.funnel(Event('tool_preview', '<tool_call><function=run_bash><parameter=command>ls the-sea-module'))
    await wk.wait("document.querySelector('#nd-convo details.nd-writing .nd-tail')?.textContent.includes('the-sea-module')")
    scripted.funnel(Event('turn_end', {}))
    await scripted.turn('build a landing page for a coffee shop',
                        [('write_file', {'path': 'coffee-shop/index.html', 'content': COFFEE})])
    await wk.wait("document.documentElement.dataset.ndPane === 'preview' && !!document.querySelector('#artbody iframe')")
    heading = await srv.ask_frame('eval', {'code': "document.getElementById('h').textContent"}, timeout=10)
    assert heading == 'Beans & Steam', heading                                                  # the page runs in the pane
    await asyncio.sleep(0.4)                                                                    # the panel's slide has ended
    g = await wk.js(geo)
    print('WEBKIT NESTED PANE:', g)
    assert g['view'] == 'nested' and g['orch'] == g0['orch'], (g0, g)                            # the column is untouched
    assert g['panel']['shown'] and abs(g['panel']['x'] - g['top']['x']) <= 1 and abs(g['panel']['w'] - g['top']['w']) <= 1, g
    assert abs(g['panel']['y'] - g['top']['b']) <= 1 and abs(g['panel']['b'] - g['split']['b']) <= 2, g
    if SHOTS.is_dir():
        await wk.call('snapshot', path=str(SHOTS / 'webkit-nested-preview.png'))
    await wk.click('#nd-pane-map')
    await wk.wait("document.documentElement.classList.contains('understand-open')")
    m = await wk.js(geo)
    assert m['pane'] == 'map' and m['orch'] == g0['orch'], (g0, m)                               # the map does not squeeze the view
    assert m['dock']['shown'] and abs(m['dock']['x'] - m['top']['x']) <= 1 and abs(m['dock']['y'] - m['top']['b']) <= 1, m
    assert not m['panel']['shown'], m
    if SHOTS.is_dir():
        await wk.call('snapshot', path=str(SHOTS / 'webkit-nested-map.png'))
    await wk.click('#nd-pane-workers')
    await wk.wait("document.documentElement.dataset.ndPane === 'workers' && !document.documentElement.classList.contains('understand-open')")
