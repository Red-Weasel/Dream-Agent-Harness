"""Lucid Control (DREAM-183): one place for the files that shape what Dream knows and how it answers."""
import httpx
import pytest

from dream import config
from dream.core import settings
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

pytestmark = pytest.mark.asyncio


@pytest.fixture
def lucid(tmp_path, monkeypatch):
    root = tmp_path / "lucid-memory"
    root.mkdir()
    for name, attr in (("INSTRUCTIONS.md", "INSTRUCTIONS_FILE"), ("IDENTITY.md", "IDENTITY_FILE"),
                       ("THREADS.md", "THREADS_FILE"), ("MEMORY.md", "MEMORY_INDEX_FILE")):
        monkeypatch.setattr(config, attr, root / name)
    monkeypatch.setattr(config, "MEMORY_DIR", root)
    (root / "IDENTITY.md").write_text("I am Dream.\n")
    (root / "MEMORY.md").write_text("- a memory\n")
    ws = tmp_path / "rocket"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("Old instructions for another assistant.\n")
    settings.set_workspace(ws)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={"workspace": str(ws), "model": "fixture"})
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1",
                               headers={"X-Dream-Token": srv.token})
    yield client, root, ws
    settings.set_workspace(None)


async def test_the_overview_lists_both_columns_and_the_styles(lucid):
    client, root, ws = lucid
    data = (await client.get("/api/lucid")).json()
    assert [f["key"] for f in data["global"]] == ["instructions", "identity", "threads", "memory-index"]
    assert [f["key"] for f in data["project"]] == ["dream-md", "claude-md", "plan"]
    by = {f["key"]: f for f in data["global"] + data["project"]}
    assert by["identity"]["exists"] and not by["instructions"]["exists"] and not by["memory-index"]["editable"]
    assert by["claude-md"]["exists"] and not by["claude-md"]["editable"] and "DREAM.md" in by["claude-md"]["about"]
    assert data["workspace"] == str(ws)
    styles = {s["key"]: s for s in data["styles"]}
    assert list(styles) == ["default", "professional", "warm", "concise", "weasel-ee"]
    assert styles["default"]["active"] and styles["weasel-ee"]["label"] == "Weasel's Personal Style - EE"
    assert "war dance" in styles["weasel-ee"]["text"].lower()          # the red herring


async def test_read_and_save_with_the_stamp_it_was_opened_at(lucid):
    client, root, ws = lucid
    opened = (await client.get("/api/lucid/global/instructions")).json()
    assert opened["text"] == "" and opened["stamp"] is None
    saved = await client.post("/api/lucid/global/instructions", json={"text": "Short answers.", "stamp": None})
    assert saved.status_code == 200 and (root / "INSTRUCTIONS.md").read_text() == "Short answers.\n"
    stale = await client.post("/api/lucid/global/instructions", json={"text": "Other.", "stamp": None})
    assert stale.status_code == 400 and "changed since you opened it" in stale.json()["error"]
    again = (await client.get("/api/lucid/global/instructions")).json()
    ok = await client.post("/api/lucid/global/instructions", json={"text": "Other.", "stamp": again["stamp"]})
    assert ok.status_code == 200 and (root / "INSTRUCTIONS.md").read_text() == "Other.\n"
    made = await client.post("/api/lucid/project/dream-md", json={"text": "# Rocket\nUse three.js.", "stamp": None})
    assert made.status_code == 200 and (ws / "DREAM.md").read_text().startswith("# Rocket")


async def test_read_only_files_unknown_keys_and_links_are_refused(lucid):
    client, root, ws = lucid
    idx = (await client.get("/api/lucid/global/memory-index")).json()
    r = await client.post("/api/lucid/global/memory-index", json={"text": "x", "stamp": idx["stamp"]})
    assert r.status_code == 400 and "read-only" in r.json()["error"]
    claude = (await client.get("/api/lucid/project/claude-md")).json()
    assert (await client.post("/api/lucid/project/claude-md",
                              json={"text": "x", "stamp": claude["stamp"]})).status_code == 400
    for bad in ("/api/lucid/global/secrets", "/api/lucid/nowhere/instructions", "/api/lucid/global/..%2F..%2Fetc"):
        assert (await client.post(bad, json={"text": "x", "stamp": None})).status_code in (400, 404)
    outside = ws.parent / "elsewhere.md"
    outside.write_text("keep me\n")
    (ws / "PLAN.md").symlink_to(outside)
    r = await client.post("/api/lucid/project/plan", json={"text": "x", "stamp": None})
    assert r.status_code == 400 and "link" in r.json()["error"] and outside.read_text() == "keep me\n"


async def test_the_token_and_origin_are_required(lucid):
    client, root, ws = lucid
    async with httpx.AsyncClient(transport=client._transport, base_url="http://127.0.0.1") as anonymous:
        assert (await anonymous.get("/api/lucid")).status_code == 401
    crossed = await client.post("/api/lucid/global/instructions", json={"text": "x", "stamp": None},
                                headers={"origin": "https://unrelated.example"})
    assert crossed.status_code == 403 and not (root / "INSTRUCTIONS.md").exists()


async def test_without_a_project_the_project_column_is_empty_and_saves_explain(lucid):
    client, root, ws = lucid
    settings.set_workspace(None)
    assert (await client.get("/api/lucid")).json()["project"] == []
    r = await client.post("/api/lucid/project/dream-md", json={"text": "x", "stamp": None})
    assert r.status_code == 400 and "No project is open" in r.json()["error"]


# --- the page -------------------------------------------------------------------------------------------------

@pytest.fixture
def page_server(lucid, monkeypatch):
    """The real App's control handler (settings_save) behind the Studio server, the Lucid files of `lucid`."""
    from dream.tui.app import App
    client, root, ws = lucid
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    app = App(provider="machx", workspace=ws)
    settings.set_workspace(ws)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, on_control=app._runtime_control,
                       session={"workspace": str(ws), "model": "fixture"})
    return srv, root, ws


@pytest.mark.parametrize("width", [1400, 390])
async def test_the_lucid_control_page_shows_the_board_edits_a_file_and_picks_a_style(page_server, width):
    import re
    from playwright.async_api import async_playwright, expect
    srv, root, ws = page_server
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(url.replace("/#", "/?companion=1#"))
            await expect(page.locator("#dream-nav-memory")).to_contain_text("Lucid Control")
            await page.locator("#dream-nav-memory").click()
            lucid = page.locator("#dream-memory-page")
            await expect(lucid.locator("h1")).to_have_text("Lucid Control")
            await expect(lucid.locator(".lucid-column")).to_have_count(2)
            await expect(lucid.locator(".lucid-file")).to_have_count(7)
            await expect(lucid.locator(".lucid-style")).to_have_count(5)
            overflow = await page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
            assert not overflow
            # the easter egg shows its weasel facts, never its text
            await lucid.locator(".lucid-style[data-style=weasel-ee]").click()
            await expect(lucid.locator(".lucid-style-text")).to_contain_text("war dance")
            await lucid.locator(".lucid-style-text button", has_text="Use for new sessions").click()
            await expect(lucid.locator(".library-status")).to_contain_text("from the next session")
            assert settings.response_style() == "weasel-ee"
            # a global file opens, saves at its stamp, and goes back to the board
            await lucid.locator(".lucid-file[data-key='global/instructions']").click()
            editor = lucid.locator(".lucid-editor textarea")
            await editor.fill("Plain English, please.")
            await lucid.locator(".lucid-editor button", has_text="Save").click()
            await expect(lucid.locator(".library-status")).to_contain_text("Saved")
            assert (root / "INSTRUCTIONS.md").read_text() == "Plain English, please.\n"
            await lucid.locator(".lucid-editor button", has_text="Back to Lucid Control").click()
            await expect(lucid.locator(".lucid-board")).to_be_visible()
            # a read-only file cannot be edited
            await lucid.locator(".lucid-file[data-key='project/claude-md']").click()
            await expect(lucid.locator(".lucid-editor textarea")).to_have_attribute("readonly", re.compile(".*"))
            await expect(lucid.locator(".lucid-editor button", has_text="Save")).to_have_count(0)
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()


# --- Codex review fixes ------------------------------------------------------------------------------------------

async def test_threads_is_read_only_because_the_task_list_writes_it(lucid):
    client, root, ws = lucid
    data = (await client.get("/api/lucid")).json()
    threads = next(f for f in data["global"] if f["key"] == "threads")
    assert not threads["editable"] and "task" in threads["about"]
    r = await client.post("/api/lucid/global/threads", json={"text": "x", "stamp": None})
    assert r.status_code == 400 and "read-only" in r.json()["error"]


def test_two_saves_at_the_same_stamp_cannot_both_win(lucid):
    import concurrent.futures
    from dream.gui import lucid_routes
    client, root, ws = lucid
    stamp = lucid_routes.read("global", "instructions")["stamp"]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda i: _try_save(lucid_routes, f"edit {i}", stamp), range(8)))
    assert results.count("saved") == 1 and results.count("refused") == 7


def _try_save(module, text, stamp):
    try:
        module.save("global", "instructions", {"text": text, "stamp": stamp})
        return "saved"
    except ValueError:
        return "refused"


def test_a_read_returns_text_and_stamp_from_the_same_file(lucid):
    from dream.gui import lucid_routes
    client, root, ws = lucid
    (root / "INSTRUCTIONS.md").write_text("A\n")
    first = lucid_routes.read("global", "instructions")
    import hashlib
    assert first["text"] == "A\n" and first["stamp"] == hashlib.sha256(b"A\n").hexdigest()[:32]


def test_a_folder_swapped_for_a_link_after_the_check_is_not_followed(lucid, monkeypatch):
    """The save checks and writes through the folder descriptor it opened: replacing the folder by a link to somewhere
    else between the version check and the write leaves the other place untouched."""
    import os
    from dream.gui import lucid_routes
    client, root, ws = lucid
    elsewhere = ws.parent / "elsewhere"
    elsewhere.mkdir()
    real_check = lucid_routes._snapshot_at
    moved = ws.parent / "rocket-moved"

    def check_then_swap(folder, path):
        result = real_check(folder, path)
        if not moved.exists():
            os.rename(ws, moved)
            os.symlink(elsewhere, ws)
        return result
    monkeypatch.setattr(lucid_routes, "_snapshot_at", check_then_swap)
    try:
        lucid_routes.save("project", "plan", {"text": "plan", "stamp": None})
    finally:
        os.unlink(ws)
        os.rename(moved, ws)
    assert not (elsewhere / "PLAN.md").exists() and (ws / "PLAN.md").read_text() == "plan\n"


async def test_a_folder_where_a_file_belongs_marks_only_that_card(lucid):
    client, root, ws = lucid
    (ws / "DREAM.md").mkdir()
    data = (await client.get("/api/lucid")).json()
    card = next(f for f in data["project"] if f["key"] == "dream-md")
    assert card["problem"] and "folder" in card["problem"] and not card["editable"]
    assert next(f for f in data["project"] if f["key"] == "plan")["problem"] is None
    r = await client.post("/api/lucid/project/dream-md", json={"text": "x", "stamp": None})
    assert r.status_code == 400 and "folder" in r.json()["error"]


async def test_a_file_over_the_limit_is_never_shown_in_part(lucid):
    """Gate S3 round 2: a cut copy saved back would drop the rest of the file."""
    client, root, ws = lucid
    big = "line of plan text\n" * 20000 + "TAIL-MARKER\n"
    (ws / "PLAN.md").write_text(big)
    card = next(f for f in (await client.get("/api/lucid")).json()["project"] if f["key"] == "plan")
    assert card["problem"] and "too large" in card["problem"] and not card["editable"]
    assert (await client.get("/api/lucid/project/plan")).status_code == 400
    r = await client.post("/api/lucid/project/plan", json={"text": big[:-200], "stamp": None})
    assert r.status_code == 400 and (ws / "PLAN.md").read_text() == big


async def test_the_limit_counts_the_newline_the_save_adds(lucid):
    client, root, ws = lucid
    r = await client.post("/api/lucid/global/instructions", json={"text": "b" * 256000, "stamp": None})
    assert r.status_code == 400 and "too long" in r.json()["error"] and not (root / "INSTRUCTIONS.md").exists()
    ok = await client.post("/api/lucid/global/instructions", json={"text": "b" * 255999, "stamp": None})
    assert ok.status_code == 200 and (await client.get("/api/lucid/global/instructions")).status_code == 200


def test_a_save_keeps_the_files_permissions_writes_every_byte_and_never_hangs_on_a_pipe(lucid, monkeypatch):
    import os
    from dream.gui import lucid_routes
    client, root, ws = lucid
    (root / "INSTRUCTIONS.md").write_text("private\n")
    os.chmod(root / "INSTRUCTIONS.md", 0o600)
    stamp = lucid_routes.read("global", "instructions")["stamp"]
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, bytes(data)[:3]))   # short writes every time
    lucid_routes.save("global", "instructions", {"text": "still private, and long enough", "stamp": stamp})
    monkeypatch.setattr(os, "write", real_write)
    assert (root / "INSTRUCTIONS.md").read_text() == "still private, and long enough\n"
    assert (os.stat(root / "INSTRUCTIONS.md").st_mode & 0o777) == 0o600
    os.mkfifo(ws / "PLAN.md")
    card = next(f for f in lucid_routes.overview()["project"] if f["key"] == "plan")
    assert card["problem"] and not card["editable"]                     # returned at once, no hang


def test_an_unreadable_file_marks_only_its_card(lucid, monkeypatch):
    from dream.gui import lucid_routes
    client, root, ws = lucid
    real = lucid_routes._snapshot

    def denied(path, root_):
        if path.name == "DREAM.md":
            raise PermissionError(13, "Permission denied")
        return real(path, root_)
    monkeypatch.setattr(lucid_routes, "_snapshot", denied)
    data = lucid_routes.overview()
    assert next(f for f in data["project"] if f["key"] == "dream-md")["problem"]
    assert next(f for f in data["project"] if f["key"] == "plan")["problem"] is None


async def test_typing_during_a_save_stays_unsaved_and_a_late_board_never_replaces_the_editor(page_server):
    """Codex re-review #6, #7: the page never drops an edit it has not saved."""
    import asyncio
    from playwright.async_api import async_playwright, expect
    srv, root, ws = page_server
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(5000)
            await page.goto(url.replace("/#", "/?companion=1#"))
            await page.locator("#dream-nav-memory").click()
            lucid = page.locator("#dream-memory-page")
            await expect(lucid.locator(".lucid-file")).to_have_count(7)
            gate = asyncio.Event()

            async def slow(route):
                if route.request.method == "POST":
                    await gate.wait()
                await route.continue_()
            await page.route("**/api/lucid/global/instructions", slow)
            await lucid.locator(".lucid-file[data-key='global/instructions']").click()
            editor = lucid.locator(".lucid-editor textarea")
            await editor.fill("A")
            await lucid.locator(".lucid-editor button", has_text="Save").click()
            await editor.fill("AB")                                      # typed while the save is in flight
            gate.set()
            await expect(lucid.locator(".library-status")).to_contain_text("Saved")
            dialogs = []

            async def dismiss(dialog):
                dialogs.append(dialog.message)
                await dialog.dismiss()
            page.on("dialog", dismiss)
            await lucid.locator(".lucid-editor button", has_text="Back to Lucid Control").click()
            await expect(editor).to_have_value("AB")                     # the unsaved B asked first, and stayed
            assert dialogs and "unsaved" in dialogs[0]
            # a board answer that arrives after an editor opened does not replace it
            await page.unroute("**/api/lucid/global/instructions")
            late = asyncio.Event()

            async def slow_board(route):
                await late.wait()
                await route.continue_()
            await page.route("**/api/lucid", slow_board)
            await lucid.locator("button", has_text="Refresh").click()
            late.set()
            await page.wait_for_timeout(400)
            await expect(editor).to_have_value("AB")
            await browser.close()
    finally:
        await srv.stop()


def test_new_files_get_owner_only_globally_and_ordinary_modes_in_the_project(lucid):
    import os
    from dream.gui import lucid_routes
    client, root, ws = lucid
    lucid_routes.save("global", "instructions", {"text": "mine", "stamp": None})
    lucid_routes.save("project", "plan", {"text": "plan", "stamp": None})
    assert (os.stat(root / "INSTRUCTIONS.md").st_mode & 0o777) == 0o600
    umask = os.umask(0o022); os.umask(umask)
    assert (os.stat(ws / "PLAN.md").st_mode & 0o777) == 0o666 & ~umask


def test_the_style_source_names_the_project_when_the_project_sets_it(lucid, tmp_path):
    import json as _json
    from dream.gui import lucid_routes
    client, root, ws = lucid
    assert lucid_routes.overview()["style_source"] == "default"
    (ws / ".dream").mkdir(exist_ok=True)
    (ws / ".dream" / "settings.json").write_text(_json.dumps({"behaviour": {"response_style": "concise"}}))
    data = lucid_routes.overview()
    assert data["style_source"] == "project"
    assert next(s for s in data["styles"] if s["key"] == "concise")["active"]


# --- DREAM-185: memories grouped by project ---------------------------------------------------------------------

def _mem(root, slug, title, project, updated):
    (root / f"{slug}.md").write_text(f"---\nname: {slug}\ntitle: {title}\nkind: semantic\nproject: {project}\n"
                                     f"created_at: 2026-09-2{updated}T10:00:00+00:00\n---\n{title}.\n")


async def test_the_memory_list_carries_each_memorys_project_and_dates(lucid):
    from dream.memory import project as project_memory
    client, root, ws = lucid
    here = project_memory.register(ws)
    other_ws = ws.parent / "falcon"
    other_ws.mkdir()
    other = project_memory.register(other_ws)
    _mem(root, "rocket-colours", "Rocket colours", here, 1)
    _mem(root, "falcon-notes", "Falcon notes", other, 2)
    _mem(root, "plain-english", "Plain English", "user", 3)
    items = {i["name"]: i for i in (await client.get("/api/memory")).json()["items"] if i["scope"] == "global"}
    assert items["rocket-colours"]["current"] and items["rocket-colours"]["project_label"] == "rocket"
    assert items["falcon-notes"]["project_label"] == "falcon" and not items["falcon-notes"]["current"]
    assert items["plain-english"]["project_label"] == "Everywhere" and items["plain-english"]["created"] == "2026-09-23"


async def test_the_memories_column_groups_this_project_first(page_server):
    from playwright.async_api import async_playwright, expect
    from dream.memory import project as project_memory
    srv, root, ws = page_server
    here = project_memory.register(ws)
    other_ws = ws.parent / "falcon"
    other_ws.mkdir(exist_ok=True)
    _mem(root, "falcon-notes", "Falcon notes", project_memory.register(other_ws), 2)
    _mem(root, "rocket-colours", "Rocket colours", here, 1)
    _mem(root, "plain-english", "Plain English", "user", 3)
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(5000)
            await page.goto(url.replace("/#", "/?companion=1#"))
            await page.locator("#dream-nav-memory").click()
            groups = page.locator("#dream-memory-page .lucid-group")
            await expect(groups.first).to_contain_text("This project · rocket")
            assert [t.split(" (")[0] for t in await groups.all_inner_texts()][:3] == [
                "THIS PROJECT · ROCKET", "EVERYWHERE", "FALCON"] or \
                [t.split(" (")[0].lower() for t in await groups.all_inner_texts()][:3] == [
                "this project · rocket", "everywhere", "falcon"]
            await page.get_by_label("Filter lucid control").select_option("current")
            await expect(page.locator("#dream-memory-page .memory-row")).to_have_count(1)
            await browser.close()
    finally:
        await srv.stop()


async def test_same_named_project_folders_stay_apart_and_a_number_title_is_text(lucid):
    """Gate 185 findings: groups key on the project, not its folder name; a YAML number title reaches the page as text."""
    from dream.memory import project as project_memory
    client, root, ws = lucid
    a, b = ws.parent / "one" / "site", ws.parent / "two" / "site"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    ka, kb = project_memory.register(a), project_memory.register(b)
    assert ka != kb
    _mem(root, "site-a", "Site A", ka, 1)
    _mem(root, "site-b", "Site B", kb, 2)
    (root / "numbered.md").write_text("---\nname: numbered\ntitle: 2026\nkind: semantic\nproject: user\n---\nx\n")
    items = {i["name"]: i for i in (await client.get("/api/memory")).json()["items"] if i["scope"] == "global"}
    assert items["site-a"]["project"] != items["site-b"]["project"]
    assert items["numbered"]["title"] == "2026"
