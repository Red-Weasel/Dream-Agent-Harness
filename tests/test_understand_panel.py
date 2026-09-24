"""DREAM-087: the Understand-Anything panel -- the codebase map beside the chat, and code changes side by side.

Owner, 2026-09-23: build https://github.com/Egonex-AI/Understand-Anything into Dream; the user must be able to see the
map while continuing to work, dive into individual code sections, and see changes in code side by side. The upstream
dashboard is served by Dream itself (built once with base /ua/) and reads its data from the project's .ua/ folder
through Dream's token-checked endpoints (root paths, as the dashboard hard-codes them); a Changes tab shows this
session's checkpoint diffs as two aligned columns.
"""
import hashlib
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
async def test_the_real_dashboard_shows_no_banner_for_a_fresh_map_without_git(tmp_path, monkeypatch):
    """Gate 1, finding 1: upstream's dashboard validates graphCommitHash / headCommitHash on every freshness answer and
    replaced ours with "could not be verified". Loaded for real: fresh shows no banner, an edit shows the changed-files
    banner, never the unverified one."""
    from playwright.async_api import async_playwright
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    root = tmp_path / "nogit"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.ts").write_text("export const stages = 2;\n")
    write_graph(root, json.loads(SAMPLE.read_text()))
    sha = lambda rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
    (root / ".ua" / "map-state.json").write_text(json.dumps({"version": 1, "builtAt": "2026-09-24T12:22:12Z",
                                                              "files": {"src/main.ts": sha("src/main.ts")}}))
    srv = server(root)
    url = await srv.start()
    base = url.split('?')[0].rstrip('/')
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1400, 'height': 900})

            async def banner_text():
                async with page.expect_response(lambda r: 'staleness.json' in r.url, timeout=15000) as answer:
                    await page.goto(base + '/ua/?token=' + srv.token)
                assert (await answer.value).ok
                await page.wait_for_selector('header', timeout=15000)
                await page.wait_for_timeout(1500)
                return await page.evaluate('document.body.innerText')

            text = await banner_text()
            assert 'could not be verified' not in text and 'working-tree changes' not in text, text[:600]
            (root / "src" / "main.ts").write_text("export const stages = 3;\n")
            text = await banner_text()
            assert 'working-tree changes' in text and 'could not be verified' not in text, text[:600]
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.skipif(not SAMPLE.is_file() or not understand_routes.DIST.joinpath('index.html').is_file(),
                    reason='needs the Understand-Anything clone with a built dashboard (see understand_routes.DIST)')
async def test_staleness_without_git_uses_the_file_hashes_the_skill_recorded(tmp_path, monkeypatch):
    """DREAM-107: a project without git (the owner's) showed "freshness could not be verified" forever. The understand
    skill records every mapped file's SHA-256 (.ua/map-state.json; a map from before DREAM-106: the plugin's
    fingerprints.json, hashed as text), so the dock answers from those -- fresh while every mapped file is unchanged,
    dirty naming the edited and deleted ones -- the same answer the skill's status gives for them."""
    root = tmp_path / "plain"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.ts").write_text("export const stages = 2;\n")
    (root / "src" / "util.ts").write_text("export const clamp = (x) => x;\n")
    write_graph(root, graph_for(root, None))                                     # no commit, no git: git cannot answer
    (root / ".ua" / "domain-graph.json").write_text(json.dumps({"nodes": [], "edges": []}))
    sha = lambda rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
    (root / ".ua" / "map-state.json").write_text(json.dumps({"version": 1, "builtAt": "2026-09-24T12:22:12Z",
                                                              "files": {r: sha(r) for r in ("src/main.ts", "src/util.ts")}}))
    srv = server(root)
    async with client(srv) as c:
        async def graphs():
            return (await c.get('/staleness.json')).json()['graphs']
        fresh = await graphs()
        assert fresh['knowledge']['status'] == 'fresh' == fresh['domain']['status'], fresh
        assert fresh['knowledge']['changedFiles'] == [] and fresh['knowledge']['lastAnalyzedAt'] == '2026-09-24T12:22:12Z'
        assert fresh['knowledge']['graphCommitHash'] == 'none' == fresh['knowledge']['headCommitHash']   # dashboard needs both
        (root / "src" / "main.ts").write_text("export const stages = 3;\n")
        dirty = (await graphs())['knowledge']
        assert dirty['status'] == 'dirty' and dirty['changedFiles'] == ['src/main.ts'] and dirty['changedFileCount'] == 1
        (root / "src" / "main.ts").write_text("export const stages = 2;\n")         # restored: fresh again
        assert (await graphs())['knowledge']['status'] == 'fresh'
        (root / "src" / "util.ts").unlink()
        gone = (await graphs())['knowledge']
        assert gone['status'] == 'dirty' and gone['changedFiles'] == ['src/util.ts']

        # a map from before DREAM-106: the plugin's fingerprints.json, hashed as decoded text
        (root / ".ua" / "map-state.json").unlink()
        text_sha = hashlib.sha256((root / "src" / "main.ts").read_bytes().decode("utf-8").encode("utf-8")).hexdigest()
        (root / ".ua" / "fingerprints.json").write_text(json.dumps({"files": {"src/main.ts": {"contentHash": text_sha}}}))
        assert (await graphs())['knowledge']['status'] == 'fresh'
        (root / "src" / "main.ts").write_text("export const stages = 4;\n")
        assert (await graphs())['knowledge']['changedFiles'] == ['src/main.ts']


async def test_the_freshness_check_reads_only_files_touched_since_the_map_and_never_a_whole_binary(tmp_path, monkeypatch):
    """The owner's pre-106 map records 952 files, 794 MB with a 280 MB browser binary: a poll must not read them. Files
    untouched since the map was built are never read; a changed one is hashed in chunks, text hashes decoded
    incrementally exactly as a whole-file decode would."""
    import os
    import time
    root = tmp_path / "big"
    (root / "tools").mkdir(parents=True)
    blob = bytes(range(256)) * 4096 + "é€😀".encode() + b"\xff\xfe" + "ü".encode()[:1]     # binary, split and bad UTF-8
    (root / "tools" / "chrome").write_bytes(blob)
    whole = hashlib.sha256(blob.decode("utf-8", "replace").encode("utf-8")).hexdigest()
    assert understand_routes._content_hash(root / "tools" / "chrome", "text") == whole          # chunked == one-shot
    old = time.time() - 3600
    os.utime(root / "tools" / "chrome", (old, old))
    reads = []
    monkeypatch.setattr(understand_routes, "_content_hash", lambda path, method: reads.append(path) or whole)
    built = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
    record = ({"tools/chrome": whole}, "text", built, understand_routes._epoch(built), None)
    result = understand_routes.content_freshness(root, record)
    assert result["status"] == "fresh" and reads == []                                          # older than the map: not read
    os.utime(root / "tools" / "chrome", None)                                                   # touched after the map
    assert understand_routes.content_freshness(root, record)["status"] == "fresh"
    assert reads == [root / "tools" / "chrome"]


async def test_an_edit_during_the_run_is_dirty_and_the_cutoff_is_trusted_only_when_safe(tmp_path):
    """Gate 1, finding 2: map-state's hashes are taken at the scan, its builtAt at assemble. The cutoff is the run's
    start (the run id `status` stamps before the scan), so a file edited between scan and assemble -- mtime before
    builtAt, content unlike the record -- is dirty. A naive or future timestamp is never trusted: everything is hashed."""
    import os
    import time
    root = tmp_path / "run"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    recorded = hashlib.sha256(b"A = 1\n").hexdigest()
    (root / "src" / "a.py").write_text("A = 2\n")                     # edited mid-run, after the scan hashed "A = 1"
    now = time.time()
    os.utime(root / "src" / "a.py", (now - 600, now - 600))          # ... ten minutes ago, before the map was assembled
    start = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now - 1800)) + ".000000Z-4242"       # status: half an hour ago
    built = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60))                        # assemble: a minute ago
    ua = root / ".ua"
    ua.mkdir()
    (ua / "map-state.json").write_text(json.dumps({"version": 1, "run": start, "builtAt": built,
                                                    "files": {"src/a.py": recorded}}))
    record = understand_routes.recorded_hashes(ua)
    assert abs(record[3] - (now - 1800)) < 2                            # the moment is the run's start, not builtAt
    assert understand_routes.content_freshness(root, record)["changedFiles"] == ["src/a.py"]
    assert understand_routes._run_epoch("20261399T999999Z-1") is None       # impossible digits: no crash, no cutoff
    for untrusted in ("2026-09-24T12:00:00", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 86400)), "not a date"):
        (ua / "map-state.json").write_text(json.dumps({"version": 1, "builtAt": untrusted, "files": {"src/a.py": recorded}}))
        assert understand_routes.content_freshness(root, understand_routes.recorded_hashes(ua))["status"] == "dirty", untrusted


async def test_a_recorded_path_outside_the_project_or_not_a_regular_file_is_never_read(tmp_path, monkeypatch):
    """Gate 1, finding 3: a crafted record could name ../x, an absolute path, a symlink out, a FIFO or /dev/zero -- a
    content oracle for outside files and a hang. Such entries count as changed and are never opened."""
    import os
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("outside\n")
    (root / "src" / "link").symlink_to(tmp_path / "secret.txt")
    os.mkfifo(root / "src" / "pipe")
    secret = hashlib.sha256(b"outside\n").hexdigest()
    reads = []
    real = understand_routes._content_hash
    monkeypatch.setattr(understand_routes, "_content_hash", lambda path, method: reads.append(str(path)) or real(path, method))
    files = {"../secret.txt": secret, str(tmp_path / "secret.txt"): secret, "src/link": secret, "src/pipe": "x",
             "/dev/zero": "x"}
    result = understand_routes.content_freshness(root, (files, "bytes", None, None, None))
    assert result["status"] == "dirty" and sorted(result["changedFiles"]) == sorted(files)
    assert reads == []


async def test_recorded_hashes_win_over_git_for_a_map_that_has_them(project):
    """With the skill's record present, a commit that changes no mapped content does not make the map stale, and an
    uncommitted edit makes it dirty -- content decides, as it does for the skill's status."""
    head = git(project, "rev-parse", "HEAD")
    write_graph(project, graph_for(project, head))
    sha = lambda rel: hashlib.sha256((project / rel).read_bytes()).hexdigest()
    (project / ".ua" / "map-state.json").write_text(json.dumps({"version": 1, "builtAt": "2026-09-24T12:00:00Z",
                                                                 "files": {r: sha(r) for r in ("src/main.ts", "src/util.ts")}}))
    git(project, "commit", "-q", "--allow-empty", "-m", "nothing mapped changed")
    srv = server(project)
    async with client(srv) as c:
        assert (await c.get('/staleness.json')).json()['graphs']['knowledge']['status'] == 'fresh'
        (project / "src" / "util.ts").write_text("export const clamp = (x) => Math.max(0, x);\n")
        dirty = (await c.get('/staleness.json')).json()['graphs']['knowledge']
        assert dirty['status'] == 'dirty' and dirty['changedFiles'] == ['src/util.ts']


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

            # DREAM-107: side by side by default; the expand toggle widens the dock and keeps the chat beside it
            narrow = (await dock.bounding_box())['width']
            toggle = dock.locator('#ua-wide')
            await expect(toggle).to_have_attribute('aria-pressed', 'false')
            await toggle.click()
            await expect(toggle).to_have_attribute('aria-pressed', 'true')
            wide = (await dock.bounding_box())['width']
            assert wide > narrow + 200, (narrow, wide)
            await expect(main).to_be_visible()
            assert (await main.bounding_box())['width'] > 150                                # the chat keeps a column
            await toggle.click()
            await expect(toggle).to_have_attribute('aria-pressed', 'false')
            assert abs((await dock.bounding_box())['width'] - narrow) < 2

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
