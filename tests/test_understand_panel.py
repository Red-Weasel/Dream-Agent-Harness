"""DREAM-087: the Understand-Anything panel -- the codebase map beside the chat, and code changes side by side.

Owner, 2026-09-23: build https://github.com/Egonex-AI/Understand-Anything into Dream; the user must be able to see the
map while continuing to work, dive into individual code sections, and see changes in code side by side. The upstream
dashboard is served by Dream itself (built once with base /ua/) and reads its data from the project's .ua/ folder
through Dream's token-checked endpoints (root paths, as the dashboard hard-codes them); a Changes tab shows this
session's checkpoint diffs as two aligned columns.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from dream.gui import understand_routes
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

pytestmark = pytest.mark.asyncio

SAMPLE = understand_routes.DASHBOARD / "public" / "knowledge-graph.json"   # the upstream plugin's own sample graph


def git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def graph_for(root: Path, commit: str | None) -> dict:
    """A small valid graph over the fixture project; one node has an absolute path, as older graphs do."""
    return {"version": "1.0", "project": {"name": "rocket", "analyzedAt": "2026-09-23T10:00:00Z",
                                         **({"gitCommitHash": commit} if commit else {})},
            "nodes": [{"id": "file:src/main.ts", "type": "file", "name": "main.ts", "filePath": "src/main.ts",
                       "summary": "Stage count and fuel.", "tags": [], "complexity": 1},
                      {"id": "file:src/util.ts", "type": "file", "name": "util.ts", "filePath": str(root / "src" / "util.ts"),
                       "summary": "Helpers.", "tags": [], "complexity": 1},
                      {"id": "fn:stages", "type": "function", "name": "stages", "filePath": "src/main.ts",
                       "summary": "The stage count.", "tags": [], "complexity": 1}],
            "edges": [{"source": "file:src/main.ts", "target": "fn:stages", "type": "contains"}],
            "layers": [{"id": "core", "name": "Core", "nodeIds": ["file:src/main.ts", "file:src/util.ts"]}],
            "tour": []}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "rocket"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.ts").write_text("export const stages = 2;\nexport const fuel = 'RP-1';\n")
    (root / "src" / "util.ts").write_text("export const clamp = (x) => x;\n")
    (root / "README.md").write_text("# Rocket\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


def write_graph(root: Path, graph: dict):
    (root / ".ua").mkdir(exist_ok=True)
    (root / ".ua" / "knowledge-graph.json").write_text(json.dumps(graph))


def server(root, **kwargs):
    return StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(root), 'model': 'fixture'},
                        **kwargs)


def client(srv, token=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                             headers={'X-Dream-Token': token if token is not None else srv.token})


# ---- the data endpoints the dashboard fetches ------------------------------------------------------------------------

async def test_status_and_graph_before_and_after_an_analysis(project):
    srv = server(project)
    async with client(srv) as c, client(srv, token='') as anon:
        before = (await c.get('/api/understand/status')).json()
        assert before['graph'] is None and isinstance(before['dashboard'], bool)
        r = await anon.get(f'/knowledge-graph.json?token={srv.token}')
        assert r.status_code == 404 and 'understand' in r.json()['error'].lower()

        write_graph(project, graph_for(project, None))
        after = (await c.get('/api/understand/status')).json()
        assert after['graph'] == {'nodes': 3, 'edges': 1, 'name': 'rocket', 'analyzedAt': '2026-09-23T10:00:00Z',
                                  'path': str(project / '.ua' / 'knowledge-graph.json')}
        # the dashboard sends the token as ?token= and fetches from the root, as upstream hard-codes it
        g = (await anon.get(f'/knowledge-graph.json?token={srv.token}')).json()
        assert [n['filePath'] for n in g['nodes']] == ['src/main.ts', 'src/util.ts', 'src/main.ts']   # relativised
        assert (await anon.get('/knowledge-graph.json')).status_code == 401
        assert (await anon.get('/knowledge-graph.json?token=wrong')).status_code == 401
        # optional files: absent is a plain 404 (the dashboard tolerates it); config has upstream's default
        assert (await c.get('/meta.json')).status_code == 404
        assert (await c.get('/diff-overlay.json')).status_code == 404
        assert (await c.get('/domain-graph.json')).status_code == 404
        assert (await c.get('/config.json')).json() == {'autoUpdate': False, 'outputLanguage': 'en'}
        (project / '.ua' / 'config.json').write_text('{"autoUpdate": true, "outputLanguage": "de"}')
        assert (await c.get('/config.json')).json() == {'autoUpdate': True, 'outputLanguage': 'de'}


async def test_file_content_serves_only_files_the_graph_names_inside_the_project(project):
    write_graph(project, graph_for(project, None))
    (project / "src" / "blob.bin").write_bytes(b"\x00\x01")
    srv = server(project)
    async with client(srv) as c:
        r = (await c.get('/file-content.json?path=src/main.ts')).json()
        content = "export const stages = 2;\nexport const fuel = 'RP-1';\n"
        assert r == {'path': 'src/main.ts', 'language': 'typescript', 'content': content,
                     'sizeBytes': len(content.encode()), 'lineCount': 3}   # lineCount as upstream counts it
        assert (await c.get('/file-content.json?path=src/util.ts')).status_code == 200   # named absolutely in the graph
        assert (await c.get('/file-content.json?path=README.md')).status_code == 404     # exists, not in the graph
        assert (await c.get('/file-content.json?path=../rocket/src/main.ts')).status_code == 400
        assert (await c.get(f'/file-content.json?path={project / "src" / "main.ts"}')).status_code == 400
        assert (await c.get('/file-content.json')).status_code == 400
    async with client(srv, token='') as anon:
        assert (await anon.get('/file-content.json?path=src/main.ts')).status_code == 401


async def test_staleness_reports_fresh_dirty_and_behind(project):
    head = git(project, "rev-parse", "HEAD")
    write_graph(project, graph_for(project, head))
    srv = server(project)
    async with client(srv) as c:
        fresh = (await c.get('/staleness.json')).json()['graphs']['knowledge']
        assert fresh['status'] == 'fresh' and fresh['graphCommitHash'] == head == fresh['headCommitHash']
        assert fresh['changedFiles'] == [] and fresh['lastAnalyzedAt'] == '2026-09-23T10:00:00Z'   # .ua/ itself does not count

        (project / "src" / "new.ts").write_text("export const x = 1;\n")
        dirty = (await c.get('/staleness.json')).json()['graphs']['knowledge']
        assert dirty['status'] == 'dirty' and dirty['changedFiles'] == ['src/new.ts'] and dirty['changedFileCount'] == 1

        git(project, "add", "."); git(project, "commit", "-q", "-m", "more")
        stale = (await c.get('/staleness.json')).json()['graphs']['knowledge']
        assert stale['status'] == 'stale' and stale['relation'] == 'behind' and stale['commitsBehind'] == 1
        assert stale['changedFiles'] == ['src/new.ts'] and stale['headCommitHash'] == git(project, "rev-parse", "HEAD")

        write_graph(project, graph_for(project, None))
        unknown = (await c.get('/staleness.json')).json()['graphs']['knowledge']
        assert unknown == {'status': 'unknown', 'reason': 'missing-graph-commit', 'lastAnalyzedAt': '2026-09-23T10:00:00Z'}


async def test_the_dashboard_bundle_is_served_under_ua_or_says_how_to_build_it(project, monkeypatch, tmp_path):
    srv = server(project)
    async with client(srv) as c:
        if understand_routes.DIST.joinpath('index.html').is_file():
            page = await c.get('/ua/')
            assert page.status_code == 200 and '/ua/assets/' in page.text
        monkeypatch.setattr(understand_routes, 'DIST', tmp_path / 'nowhere')
        missing = await c.get('/ua/')
        assert missing.status_code == 503 and 'vite build --base=/ua/' in missing.text
        assert (await c.get('/api/understand/status')).json()['dashboard'] is False


# ---- the dock -------------------------------------------------------------------------------------------------------

class FakeStore:
    def list(self, limit=None):
        return [{"id": "cp-1", "label": "turn 1: fuel change", "session": "s", "created_at": "2026-09-23T10:01:00Z",
                 "files": 1, "paths": ["src/main.ts"], "sealed": True}]

    def diff(self, cid):
        assert cid == "cp-1"
        return ("--- src/main.ts (checkpoint cp-1)\n+++ src/main.ts (now)\n@@ -1,3 +1,3 @@\n export const stages = 2;\n"
                "-export const fuel = 'RP-1';\n+export const fuel = 'methane';\n export const crew = 0;")


@pytest.mark.skipif(not SAMPLE.is_file() or not understand_routes.DIST.joinpath('index.html').is_file(),
                    reason='needs the Understand-Anything clone with a built dashboard (see understand_routes.DIST)')
async def test_the_dock_opens_beside_the_chat_and_stays_while_working(project, monkeypatch):
    from playwright.async_api import async_playwright, expect
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    srv = server(project, checkpoints=lambda: FakeStore())
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1400, 'height': 900})
            page.set_default_timeout(6000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url + '&companion=1')
            await page.locator('#dream-nav-understand').click()
            dock, main = page.locator('#dream-understand'), page.locator('#main')
            await expect(dock).to_be_visible()
            await expect(main).to_be_visible()
            assert (await dock.bounding_box())['x'] > (await main.bounding_box())['x']   # beside the chat, to its right
            await expect(dock.locator('#ua-empty')).to_be_visible()                       # no graph yet: says what to ask
            await dock.locator('#ua-ask').click()
            await expect(page.locator('#input')).to_have_value(re.compile('understand'))
            await expect(dock).to_be_visible()

            write_graph(project, json.loads(SAMPLE.read_text()))                          # the model finished: the map appears
            frame = dock.locator('#ua-frame')
            await expect(frame).to_be_visible(timeout=12000)
            assert re.search(r'/ua/\?token=', await frame.get_attribute('src'))
            dashboard = page.frame_locator('#ua-frame')                                     # the upstream React app, live
            await expect(dashboard.get_by_role('banner')).to_be_visible(timeout=12000)
            await expect(dashboard.locator('body')).to_contain_text('understand-anything')   # the sample's project name

            # a permission card pulls the view back to chat; the map must stay in sight while the owner answers
            await page.evaluate("document.getElementById('stream').insertAdjacentHTML('beforeend','<div class=\"permission-card\">card</div>')")
            await expect(dock).to_be_visible()
            await expect(main).to_be_visible()

            # this session's changes, side by side
            await dock.locator('#ua-tab-changes').click()
            grid = dock.locator('.ua-diff')
            await expect(grid).to_be_visible()
            await expect(grid.locator('.ua-old.del')).to_have_text("export const fuel = 'RP-1';")
            await expect(grid.locator('.ua-new.add')).to_have_text("export const fuel = 'methane';")
            assert await grid.locator('.ua-old.ctx').count() == 2 == await grid.locator('.ua-new.ctx').count()
            old_box, new_box = await grid.locator('.ua-old.del').bounding_box(), await grid.locator('.ua-new.add').bounding_box()
            assert abs(old_box['y'] - new_box['y']) < 1 and new_box['x'] > old_box['x']   # the same row, side by side
            await expect(grid.locator('.ua-diff-file')).to_have_text('src/main.ts')
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.skipif(not SAMPLE.is_file() or not understand_routes.DIST.joinpath('index.html').is_file(),
                    reason='needs the Understand-Anything clone with a built dashboard (see understand_routes.DIST)')
async def test_the_dashboard_header_shows_every_legend_pill_in_the_dock(project, monkeypatch):
    """Owner, 2026-09-23: at their window size the dashboard's legend strip clipped INFRA (and hid DATA and DOMAIN):
    upstream scrolls that strip with the scrollbar hidden. In the dock the pills wrap instead."""
    from playwright.async_api import async_playwright, expect
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    write_graph(project, json.loads(SAMPLE.read_text()))
    srv = server(project)
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 2554, 'height': 1338})   # the owner's screen
            page.set_default_timeout(8000)
            await page.goto(url + '&companion=1')
            await page.locator('#dream-nav-understand').click()
            header = page.frame_locator('#ua-frame').locator('header').first
            await expect(header).to_be_visible(timeout=15000)
            await page.wait_for_timeout(800)
            clipped = await header.evaluate("""h => {
              const pills = [...h.querySelectorAll('button, span, div')].filter(e => /^(Code|Config|Docs|Infra|Data|Domain)$/.test(e.textContent.trim()) && ![...e.children].some(c => /^(Code|Config|Docs|Infra|Data|Domain)$/.test(c.textContent.trim())));
              const clip = e => { let a = e.parentElement; while (a && a !== h) { const o = getComputedStyle(a).overflowX; if (o === 'auto' || o === 'hidden' || o === 'scroll') return a; a = a.parentElement; } return null; };
              // the strip that holds the pills, and the tools group to its right (upstream's header: left | strip | right)
              const strip = h.children[1].getBoundingClientRect(), right = h.children[2].getBoundingClientRect();
              return pills.map(e => { const r = e.getBoundingClientRect(); const c = clip(e); const cr = c ? c.getBoundingClientRect() : {left: 0, right: innerWidth};
                return {pill: e.textContent.trim(),
                        clipped: r.right > cr.right + 1 || r.left < cr.left - 1 || r.right > innerWidth,
                        outside_strip: r.right > strip.right + 1 || r.left < strip.left - 1,
                        over_tools: r.right > right.left && r.left < right.right && r.bottom > right.top && r.top < right.bottom}; });
            }""")
            assert [c['pill'] for c in clipped] == ['Code', 'Config', 'Docs', 'Infra', 'Data', 'Domain'], clipped
            assert not [c for c in clipped if c['clipped'] or c['outside_strip'] or c['over_tools']], clipped
            # two header rows: tabs and tools, then the strip -- and the strip itself stays at most two lines high
            # for the sample's seven layers (a wrap that stacked every group into its own row made a 228 px header)
            shape = await header.evaluate("""h => ({height: h.getBoundingClientRect().height,
              stripLines: new Set([...h.children[1].querySelectorAll('button')].map(e => Math.round(e.getBoundingClientRect().top))).size})""")
            assert shape['stripLines'] <= 2 and shape['height'] < 130, shape
            await browser.close()
    finally:
        await srv.stop()
