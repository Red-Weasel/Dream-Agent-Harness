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


# --- DREAM-257: effective context ---------------------------------------------------------------------------------

@pytest.fixture
def context_server(tmp_path, monkeypatch):
    """A Studio server whose session can change under it, and an empty journal of its own."""
    from dream.core import context_inspection
    monkeypatch.setattr(context_inspection, "JOURNAL", context_inspection.Journal(max_records=2))
    for name in (context_inspection.ENV_TOKENIZER, context_inspection.ENV_TOKENIZER_MODEL, "DREAM_CONTEXT_INSPECTION"):
        monkeypatch.delenv(name, raising=False)
    session = {"session_id": "sess-A", "workspace": str(tmp_path), "model": "fixture"}
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session=lambda: session)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1",
                               headers={"X-Dream-Token": srv.token})
    return client, srv, session


def _sent_request(session_id, text="PROMPT-MARKER the request text"):
    """One recorded attempt, as the transport records it; its request id."""
    from dream.core import context_inspection
    image = "data:image/png;base64," + "A" * 2000
    payload = {"model": "fixture-model", "tools": [], "messages": [
        {"role": "system", "content": "You are a fixture."},
        {"role": "user", "content": [{"type": "text", "text": text},
                                     {"type": "image_url", "image_url": {"url": image}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "run_bash", "arguments": '{"command": "echo ARG-SECRET-VALUE"}'}}]}]}
    before = {row["request_id"] for row in context_inspection.JOURNAL.list(session_id)}
    handle = context_inspection.capture(payload, {"recipient": "lead", "origin": "lead_turn"},
                                        session_id=session_id, private_id="standalone-backend", transport="post")
    handle.response(200)
    handle.usage({"prompt_tokens": 21, "completion_tokens": 3})
    after = [row["request_id"] for row in context_inspection.JOURNAL.list(session_id)]
    return next((request_id for request_id in after if request_id not in before), handle._id)


async def test_an_empty_context_says_what_is_not_observed_and_is_never_cached(context_server):
    client, srv, session = context_server
    response = await client.get("/api/lucid/context")
    data = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert data["records"] == [] and data["session_id"] == "sess-A"
    assert "SDK or CLI is not observed" in data["empty_note"] and "send a new request" in data["empty_note"]
    assert data["coverage"]["native_sdk_or_cli"] == "not_observed" and data["harness"].startswith("dream-context-")
    # what this is, and what it is not: a bounded preview journal, with the package's version beside the inspector's
    import dream
    assert data["journal"]["kind"] == "bounded_preview_journal" and "not a copy of the request" in data["journal"]["note"]
    assert data["package"]["version"] == dream.__version__ and dream.__version__ not in data["harness"]
    assert "uncommitted" in data["package"]["note"]
    joined = " ".join(data["not_counted"])
    assert all(word in joined for word in ("reasoning_content", "tool call", "multipart", "template"))
    assert data["limits"]["bytes_per_record"] <= data["limits"]["bytes_per_session"]
    assert data["limits"]["components_per_record"] > 0
    session.pop("session_id")                         # a server with no session id answers the same, with nothing
    assert (await client.get("/api/lucid/context")).json()["records"] == []


async def test_the_context_routes_need_the_header_token_and_this_pages_origin(context_server):
    client, srv, session = context_server
    request_id = _sent_request("sess-A")
    async with httpx.AsyncClient(transport=client._transport, base_url="http://127.0.0.1") as anonymous:
        for path in ("/api/lucid/context", f"/api/lucid/context/{request_id}"):
            refused = await anonymous.get(path)
            assert refused.status_code == 401 and refused.headers["cache-control"] == "no-store"
            assert (await anonymous.get(path, params={"token": srv.token})).status_code == 401    # never from a URL
            assert (await anonymous.get(path, headers={"X-Dream-Token": "wrong"})).status_code == 401
    for path in ("/api/lucid/context", f"/api/lucid/context/{request_id}"):
        # another page's origin, an opaque one, and every fetch a browser says is not this page's own
        for headers in ({"origin": "https://unrelated.example"}, {"origin": "null"},
                        {"origin": "http://127.0.0.1:1"}, {"origin": "http://127.0.0.1.unrelated.example"},
                        {"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"},
                        {"origin": "http://127.0.0.1", "sec-fetch-site": "cross-site"}):
            crossed = await client.get(path, headers=headers)
            assert crossed.status_code == 403 and "PROMPT-MARKER" not in crossed.text
            assert crossed.headers["cache-control"] == "no-store" and "records" not in crossed.text
        for headers in ({"origin": "http://127.0.0.1"}, {"origin": "http://127.0.0.1", "sec-fetch-site": "same-origin"},
                        {"sec-fetch-site": "none"}):
            assert (await client.get(path, headers=headers)).status_code == 200
        # reading only: any other method is refused here, and that refusal is not cacheable either
        for method in ("POST", "PUT", "DELETE"):
            refused = await client.request(method, path, json={})
            assert refused.status_code == 405 and refused.headers["cache-control"] == "no-store"
            assert "PROMPT-MARKER" not in refused.text


async def test_options_and_trace_on_the_context_routes_are_refused_by_the_route_itself_and_never_cached(
        context_server):
    """Final review (low priority): OPTIONS and TRACE were answered by the framework's own 405, without no-store.

    Two layers refuse here, and they promise different things. The context route answers every method that
    reaches it (405, no-store, Allow: GET; 401 without the token; 403 for another page's origin). The server's
    boundary sits in front of the route: it lets the read methods (GET/HEAD/OPTIONS) through, and refuses a
    foreign-Origin TRACE or PATCH itself, before the route is reached, with a 403 and an error-only body. That
    boundary answer carries no journal data, but it has never promised a Cache-Control header, so none is
    required of it here (and none is forbidden: the boundary may add no-store later).
    """
    client, srv, session = context_server
    request_id = _sent_request("sess-A")
    journal_data = ("PROMPT-MARKER", "ARG-SECRET-VALUE", "You are a fixture.")
    for path in ("/api/lucid/context", f"/api/lucid/context/{request_id}"):
        for method in ("OPTIONS", "TRACE", "PATCH"):
            # this page's own request reaches the route, which refuses it as read-only and not cacheable
            refused = await client.request(method, path)
            assert refused.status_code == 405 and refused.headers["cache-control"] == "no-store"
            assert refused.headers["allow"] == "GET"
            assert refused.json() == {"error": "Context inspection is read-only."}
            assert "PROMPT-MARKER" not in refused.text and "records" not in refused.text
            # another page's origin is 403 whichever layer answers, and never with anything from the journal
            crossed = await client.request(method, path, headers={"origin": "https://unrelated.example"})
            assert crossed.status_code == 403
            assert all(marker not in crossed.text for marker in journal_data)
            if method == "OPTIONS":
                # a read method for the boundary, so the route's own origin check answers: no-store, as for a read
                assert crossed.headers["cache-control"] == "no-store" and "records" not in crossed.text
            else:
                # the boundary refuses first: an error-only body with a reason, and no record, list or preview
                body = crossed.json()
                assert isinstance(body, dict) and set(body) == {"error"}
                assert isinstance(body["error"], str) and body["error"]
        # the route's token check comes first, as for a read: no token is 401 and no-store
        async with httpx.AsyncClient(transport=client._transport, base_url="http://127.0.0.1") as anonymous:
            for method in ("OPTIONS", "TRACE"):
                unknown = await anonymous.request(method, path)
                assert unknown.status_code == 401 and unknown.headers["cache-control"] == "no-store"
        assert (await client.get(path)).status_code == 200           # reading is as it was


async def test_a_failure_inside_the_context_routes_is_a_fixed_no_store_answer(context_server, monkeypatch):
    from dream.gui import lucid_routes
    client, srv, session = context_server
    _sent_request("sess-A")

    def broken(session_id, request_id=None):
        raise RuntimeError("could not read /home/owner/.secret PROMPT-MARKER")
    monkeypatch.setattr(lucid_routes, "context_view", broken)
    failed = await client.get("/api/lucid/context")
    assert failed.status_code == 500 and failed.headers["cache-control"] == "no-store"
    assert failed.json() == {"error": "Context inspection is unavailable."}


async def test_the_list_has_no_prompt_text_and_the_detail_is_bounded_with_no_image_or_argument(context_server):
    client, srv, session = context_server
    request_id = _sent_request("sess-A")
    listed = await client.get("/api/lucid/context")
    (row,) = listed.json()["records"]
    assert (row["request_id"], row["recipient"], row["model"], row["attempt"], row["status"]) == (
        request_id, "lead", "fixture-model", 1, "response_received")
    assert row["usage"]["prompt_tokens"] == 21 and row["tokenizer_reason"] == "tokenizer_not_configured"
    assert "PROMPT-MARKER" not in listed.text and "components" not in row and "preview" not in str(row)
    detail = await client.get(f"/api/lucid/context/{request_id}", params={"path": "/etc/passwd"})
    record = detail.json()["record"]
    assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store"
    assert detail.json()["session_id"] == "sess-A" and record["request_id"] == request_id
    system, user, part, reply, call = record["components"]
    assert (system["source"], system["preview"], system["tokens"]) == ("unknown", "You are a fixture.", None)
    assert (user["content_form"], user["images"], user["tokens_reason"]) == (
        "multipart", 1, "multipart_text_counted_per_part")
    assert part["preview"].startswith("PROMPT-MARKER") and len(part["sha256"]) == 64
    # the call's arguments are in the model's context: their own entry, sized and hashed, never shown, never zero
    assert (reply["tokens"], reply["tokens_reason"]) == (None, "component_has_no_text")
    assert (call["kind"], call["name"], call["preview"], call["preview_reason"]) == (
        "tool_call", "run_bash", "", "arguments_not_shown")
    assert call["bytes"] == len('{"command": "echo ARG-SECRET-VALUE"}') and call["tokens"] is None
    assert call["tokens_reason"] == "tokenizer_not_configured"
    assert record["tools"]["tokens_reason"] == "request_has_no_tools" and record["package_version"]
    assert detail.json()["journal"]["kind"] == "bounded_preview_journal"
    for forbidden in ("A" * 64, "base64,", "ARG-SECRET-VALUE", "root:"):
        assert forbidden not in detail.text
    assert not [key for key in record if key.startswith("_")]


async def test_another_sessions_a_standalone_backends_and_an_evicted_record_are_all_404(context_server):
    client, srv, session = context_server
    mine = _sent_request("sess-A")
    theirs = _sent_request("sess-B", "OTHER-SESSION-TEXT")
    standalone = _sent_request(None, "STANDALONE-TEXT")          # no session bound: kept under a private identity
    assert [row["request_id"] for row in (await client.get("/api/lucid/context")).json()["records"]] == [mine]
    for request_id in (theirs, standalone, "0" * 32, "not-an-id", "standalone-backend"):
        missing = await client.get(f"/api/lucid/context/{request_id}")
        assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
        assert "OTHER-SESSION-TEXT" not in missing.text and "STANDALONE-TEXT" not in missing.text
    # an id is looked up in memory, never used as a path
    assert (await client.get("/api/lucid/context/..%2F..%2Fetc%2Fpasswd")).status_code in (400, 404)
    session["session_id"] = "sess-B"                               # the server now shows the other session
    assert (await client.get(f"/api/lucid/context/{theirs}")).status_code == 200
    assert (await client.get(f"/api/lucid/context/{mine}")).status_code == 404
    session["session_id"] = "standalone-backend"                   # a private identity is not a session to read
    assert (await client.get("/api/lucid/context")).json()["records"] == []
    assert (await client.get(f"/api/lucid/context/{standalone}")).status_code == 404
    session["session_id"] = "sess-A"                               # two more requests: the journal here keeps two
    newer = [_sent_request("sess-A"), _sent_request("sess-A")]
    assert (await client.get(f"/api/lucid/context/{mine}")).status_code == 404
    listed = [row["request_id"] for row in (await client.get("/api/lucid/context")).json()["records"]]
    assert listed == newer[::-1]


@pytest.mark.parametrize("width", [1400, 390])
async def test_the_effective_context_panel_reads_on_refresh_only_and_shows_everything_as_text(page_server, width):
    import asyncio
    import json as _json
    import re
    from playwright.async_api import async_playwright, expect
    srv, root, ws = page_server
    first, second = "a" * 32, "b" * 32
    unsafe = '<img src=x onerror="window.__xss=1">'

    def row(request_id, attempt):
        return {"request_id": request_id, "group_id": "c" * 32, "attempt": attempt, "created_at": 1790000000.0,
                "transport": "stream", "provider": "fixture", "turn": 1, "recipient": unsafe, "origin": "lead_turn",
                "model": '"><script>window.__xss=1</script>', "worker_id": None, "agent": None, "window": None,
                "window_basis": "not_known", "status": "response_received", "phase": "body", "http_status": 200,
                "error_type": None, "body": "done_seen"}

    def record(request_id):
        return {**row(request_id, 2 if request_id == first else 1),
                "usage": {"observed": True, "observations": 1, "reason": None, "prompt_tokens": 1234,
                          "prompt_tokens_reason": None, "cached_tokens": None, "cached_tokens_reason": "not_reported",
                          "completion_tokens": 7, "completion_tokens_reason": None},
                "tokenizer": {"method": None, "reason": "tokenizer_not_configured", "digest": None,
                              "bound_model": None},
                "request": {"messages": 2, "tools": 0, "images": 0, "bytes": 400, "components_omitted": 0},
                "components": [{"kind": "system_section", "message_index": 0, "role": "system", "source": unsafe,
                                "detail": {"file": "DREAM.md"}, "start": 0, "end": 40, "sha256": "d" * 64,
                                "bytes": 40, "chars": 40, "tokens": None, "tokens_reason": "tokenizer_not_configured",
                                "preview": "<script>window.__xss=2</script> SECTION-PREVIEW " + request_id[:4],
                                "preview_truncated": True, "preview_omitted": False}]}

    calls, state = [], {"fail": False, "hold": None}

    async def context(route):
        url = route.request.url
        calls.append(url)
        if state["hold"] is not None:
            await state["hold"].wait()
        if state["fail"]:
            await route.fulfill(status=500, content_type="application/json",
                                body=_json.dumps({"error": "Context inspection is unavailable."}))
        elif url.endswith("/api/lucid/context"):
            await route.fulfill(status=200, content_type="application/json", body=_json.dumps({
                "session_id": "sess-A", "records": [row(first, 2), row(second, 1)], "empty_note": None,
                "coverage": {"note": "COVERAGE-NOTE"}, "caveats": ["CAVEAT-ONE"]}))
        else:
            await route.fulfill(status=200, content_type="application/json", body=_json.dumps({
                "session_id": "sess-A", "record": record(url.rsplit("/", 1)[1]), "caveats": ["CAVEAT-ONE"]}))

    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": width, "height": 900})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.route(re.compile(r"/api/lucid/context(/[0-9a-f]{32})?$"), context)
            await page.goto(url.replace("/#", "/?companion=1#"))
            await page.locator("#dream-nav-memory").click()
            lucid = page.locator("#dream-memory-page")
            panel = lucid.locator(".lucid-context")
            await expect(panel.locator("h2")).to_have_text("Effective context")
            # below the two columns, before the response styles
            order = await page.evaluate("[...document.querySelector('.lucid-board').children].map(n =>"
                                        " n.classList.contains('lucid-column') ? 'column' :"
                                        " n.classList.contains('lucid-context') ? 'context' :"
                                        " n.querySelector('.lucid-style-grid') ? 'styles' : 'other')")
            assert order == ["column", "column", "context", "styles"]
            await expect(lucid.locator(".lucid-file")).to_have_count(7)
            await page.wait_for_timeout(400)
            assert calls == []                                    # nothing is read until the owner asks
            await panel.locator(".lucid-context-refresh").click()
            await expect(panel.locator("select option")).to_have_count(2)
            await expect(panel.locator("select option").first).to_contain_text("<img src=x")
            await expect(panel.locator("select option").first).to_contain_text("attempt 2")
            await expect(panel).to_contain_text("Prompt tokens the server reported for this attempt: 1,234")
            await expect(panel).to_contain_text("unknown (the server did not report it)")
            await expect(panel).to_contain_text("no local tokenizer is configured")
            await expect(panel).to_contain_text("SECTION-PREVIEW aaaa")
            await expect(panel).to_contain_text("d" * 64)
            await expect(panel).to_contain_text("truncated and redacted")
            await expect(panel).to_contain_text("CAVEAT-ONE")
            await expect(panel.locator("[role=status]")).to_contain_text("COVERAGE-NOTE")
            # every string from the server is text: nothing became an element and nothing ran
            await expect(panel.locator("img, script, iframe")).to_have_count(0)
            assert await page.evaluate("window.__xss === undefined")
            assert not await page.evaluate(
                "document.documentElement.scrollWidth > document.documentElement.clientWidth")
            assert len(calls) == 2 and calls[1].endswith(first)
            await panel.locator("select").select_option(second)
            await expect(panel).to_contain_text("SECTION-PREVIEW bbbb")
            assert len(calls) == 3 and calls[2].endswith(second)
            await page.wait_for_timeout(400)
            assert len(calls) == 3                                # and it does not poll afterwards
            # an error is shown in the panel and the board stays
            state["fail"] = True
            await panel.locator(".lucid-context-refresh").click()
            await expect(panel.locator("[role=status]")).to_contain_text("Context inspection is unavailable.")
            await expect(panel.locator("[role=status]")).to_have_attribute("data-error", "true")
            await expect(lucid.locator(".lucid-column")).to_have_count(2)
            await expect(lucid.locator(".library-status")).not_to_contain_text("unavailable")
            state["fail"] = False
            # an answer that arrives after the owner opened an editor is dropped: the editor stays
            state["hold"] = asyncio.Event()
            await panel.locator(".lucid-context-refresh").click()
            await lucid.locator(".lucid-file[data-key='global/instructions']").click()
            editor = lucid.locator(".lucid-editor textarea")
            await editor.fill("KEEP THIS")
            state["hold"].set()
            await page.wait_for_timeout(400)
            await expect(editor).to_have_value("KEEP THIS")
            await expect(lucid.locator(".lucid-context")).to_have_count(0)
            assert errors == []
            # an answer for a session that has since changed is dropped too
            async def accept(dialog):
                await dialog.accept()
            page.on("dialog", accept)
            await lucid.locator(".lucid-editor button", has_text="Back to Lucid Control").click()
            await expect(panel.locator("h2")).to_have_text("Effective context")
            state["hold"] = asyncio.Event()
            await panel.locator(".lucid-context-refresh").click()
            await expect(panel.locator("[role=status]")).to_contain_text("Reading")
            await page.evaluate("window.dispatchEvent(new CustomEvent('dream:session'))")
            state["hold"].set()
            await page.wait_for_timeout(400)
            await expect(lucid.locator(".lucid-context select")).to_have_count(0)
            await expect(lucid.locator(".lucid-context details")).to_have_count(0)
            await browser.close()
    finally:
        await srv.stop()


async def test_the_effective_context_panel_never_shows_one_sessions_context_under_another(page_server):
    """Review findings 6 and 11: the server's session identity is compared with the one this page was showing when
    the request began; a session change is an epoch (A -> B -> A is still a change), clears what is shown at once and
    drops a late answer whether it succeeded or failed; and an open editor is never replaced."""
    import asyncio
    import json as _json
    import re
    from playwright.async_api import async_playwright, expect
    srv, root, ws = page_server
    first, second = "a" * 32, "b" * 32
    state = {"session": "sess-A", "hold": None, "fail": False, "held": []}
    calls = []

    def row(request_id):
        return {"request_id": request_id, "group_id": "c" * 32, "attempt": 1, "created_at": 1790000000.0,
                "transport": "post", "provider": "fixture", "turn": 1, "recipient": "lead", "origin": "lead_turn",
                "model": "fixture-model", "worker_id": None, "agent": None, "window": None, "window_basis": "not_known",
                "status": "response_received", "phase": "body", "http_status": 200, "error_type": None,
                "body": "json_object", "package_version": "9.9.9-fixture", "harness": "fixture-inspector/2"}

    def record(request_id, session):
        text = {"sha256": "d" * 64, "bytes": 40, "chars": 40, "tokens": None,
                "tokens_reason": "tokenizer_not_configured"}
        return {**row(request_id), "reduced": ["previews_dropped"], "work": {"skipped": {"work_budget_exhausted": 2}},
                "usage": {"observed": False, "observations": 0, "reason": "no_usage_reported_by_this_attempt",
                          "prompt_tokens": None, "cached_tokens": None, "completion_tokens": None},
                "tokenizer": {"method": None, "reason": "tokenizer_not_configured", "digest": None,
                              "bound_model": None, "engine_exact": False},
                "request": {"messages": 9, "tools": 1, "images": 0, "bytes": 400, "complete": True,
                            "messages_omitted": 4, "messages_not_digested": 0},
                "tools": {"count": 1, "sha256": "e" * 64, "bytes": 90, "tokens": None,
                          "tokens_reason": "tokenizer_not_configured", "tokens_scope": "tools_array_json_text_only",
                          "listed_omitted": 0, "listed": [{
                              "index": 0, "name": "<b>run_bash</b>", "sha256": "f" * 64, "bytes": 90, "tokens": 17,
                              "tokens_reason": None, "tokens_scope": "schema_json_text_only",
                              "parameters": ["command"], "parameters_omitted": 0,
                              "preview": "SCHEMA-PREVIEW <script>window.__xss=3</script>",
                              "preview_truncated": False}]},
                "components": [
                    {"kind": "message", "message_index": 3, "role": "assistant", "source": "conversation",
                     "content_form": "none", "images": 0, "other_parts": 0, "tool_call_count": 1,
                     "reasoning_chars": 120, "tokens_scope": "content_text_only", "sha256": None, "bytes": None,
                     "chars": None, "tokens": None, "tokens_reason": "component_has_no_text",
                     "message_sha256": "9" * 64, "preview": "", "preview_truncated": False,
                     "preview_omitted": True, "preview_reason": "no_text"},
                    {"kind": "tool_call", "message_index": 3, "role": "assistant", "source": "conversation",
                     "call_index": 0, "name": "run_bash", "representation": "function_arguments_string_as_sent",
                     "tokens_scope": "arguments_text_only", **text, "tokens": 4321, "tokens_reason": None,
                     "preview": "", "preview_truncated": True, "preview_omitted": True,
                     "preview_reason": "arguments_not_shown"},
                    {"kind": "message", "message_index": 4, "role": "user", "source": "conversation",
                     "content_form": "text", "images": 0, "other_parts": 0, "tool_call_count": 0,
                     "reasoning_chars": 0, "tokens_scope": "content_text_only", **text,
                     "preview": "PREVIEW-OF-" + session + "-" + request_id[:4], "preview_truncated": False,
                     "preview_omitted": False, "preview_reason": None}]}

    async def context(route):
        url = route.request.url
        calls.append(url)
        session = state["session"]                   # the session the server is showing as the request arrives
        if state["hold"] is not None:
            state["held"].append(url)
            await state["hold"].wait()
        if state["fail"]:
            await route.fulfill(status=500, content_type="application/json",
                                body=_json.dumps({"error": "LATE-ERROR-TEXT"}))
        elif url.endswith("/api/lucid/context"):
            await route.fulfill(status=200, content_type="application/json", body=_json.dumps({
                "session_id": session, "records": [row(first), row(second)], "empty_note": None,
                "coverage": {"note": "COVERAGE-NOTE"}, "caveats": [], "not_counted": ["NOT-COUNTED-ONE"]}))
        else:
            await route.fulfill(status=200, content_type="application/json", body=_json.dumps({
                "session_id": session, "record": record(url.rsplit("/", 1)[1], session), "caveats": [],
                "not_counted": ["NOT-COUNTED-ONE"]}))

    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.route(re.compile(r"/api/lucid/context(/[0-9a-f]{32})?$"), context)
            await page.goto(url.replace("/#", "/?companion=1#"))
            await page.locator("#dream-nav-memory").click()
            lucid = page.locator("#dream-memory-page")
            panel = lucid.locator(".lucid-context")
            status = panel.locator("[role=status]")
            await expect(panel).to_contain_text("bounded preview journal")
            await expect(panel).to_contain_text("not a copy of the request")

            async def client_session(session_id, announce=True):
                await page.evaluate(
                    "([id, announce]) => { window.DREAM_SESSION = {...(window.DREAM_SESSION || {}), session_id: id};"
                    " if (announce) window.dispatchEvent(new CustomEvent('dream:session')); }",
                    [session_id, announce])

            await client_session("sess-A")
            # 1. the server already shows B, and this page has not heard: B's list is not shown under A
            state["session"] = "sess-B"
            await panel.locator(".lucid-context-refresh").click()
            await expect(status).to_contain_text("session changed")
            await expect(status).to_have_attribute("data-error", "true")
            await expect(panel.locator("select")).to_have_count(0)
            await expect(panel.locator("details")).to_have_count(0)
            await expect(panel).not_to_contain_text("PREVIEW-OF-")
            # 2. both agree on A: the list and a detail show, with the schema and argument counts and their labels
            state["session"] = "sess-A"
            await panel.locator(".lucid-context-refresh").click()
            await expect(panel).to_contain_text("PREVIEW-OF-sess-A-aaaa")
            await expect(panel).to_contain_text("tool call · run_bash · arguments")
            await expect(panel).to_contain_text("of the call's arguments text alone")
            await expect(panel).to_contain_text("4,321")
            await expect(panel).to_contain_text("Arguments are never shown here")
            await expect(panel).to_contain_text("120 characters of reasoning_content (not counted)")
            await expect(panel).to_contain_text("tool schemas · 1")
            await expect(panel).to_contain_text("<b>run_bash</b>")
            await expect(panel).to_contain_text("canonical JSON text")
            await expect(panel).to_contain_text("SCHEMA-PREVIEW")
            await expect(panel).to_contain_text("NOT-COUNTED-ONE")
            await expect(panel).to_contain_text("9.9.9-fixture")
            await expect(panel).to_contain_text("hashed and sized only, not token-counted")
            await expect(panel).to_contain_text("previews dropped")
            await expect(panel).not_to_contain_text("counted but not listed")
            await expect(panel.locator("b, script, img")).to_have_count(0)
            assert await page.evaluate("window.__xss === undefined")
            # 3. a detail picked while the server moved to B without telling this page: B's record is not shown
            state["session"] = "sess-B"
            await panel.locator("select").select_option(second)
            await expect(status).to_contain_text("session changed")
            await expect(panel).not_to_contain_text("PREVIEW-OF-")
            await expect(panel.locator("select")).to_have_count(0)
            # 4. out-of-order details: the earlier choice answers last and is dropped
            state["session"] = "sess-A"
            await panel.locator(".lucid-context-refresh").click()
            await expect(panel).to_contain_text("PREVIEW-OF-sess-A-aaaa")
            gate = asyncio.Event()

            async def staged(route):
                if route.request.url.endswith(second):
                    await gate.wait()
                await context(route)
            await page.unroute(re.compile(r"/api/lucid/context(/[0-9a-f]{32})?$"))
            await page.route(re.compile(r"/api/lucid/context(/[0-9a-f]{32})?$"), staged)
            await panel.locator("select").select_option(second)
            await expect(panel).not_to_contain_text("PREVIEW-OF-sess-A-aaaa")      # the old preview went at once
            await panel.locator("select").select_option(first)
            await expect(panel).to_contain_text("PREVIEW-OF-sess-A-aaaa")
            gate.set()
            await page.wait_for_timeout(400)
            await expect(panel).to_contain_text("PREVIEW-OF-sess-A-aaaa")
            await expect(panel).not_to_contain_text("PREVIEW-OF-sess-A-bbbb")
            await page.unroute(re.compile(r"/api/lucid/context(/[0-9a-f]{32})?$"))
            await page.route(re.compile(r"/api/lucid/context(/[0-9a-f]{32})?$"), context)
            # 5. A -> B -> A while a list is in flight: the ids match again, the epoch does not; cleared at once
            state["hold"] = asyncio.Event()
            await panel.locator(".lucid-context-refresh").click()
            await expect(status).to_contain_text("Reading")
            await client_session("sess-B")
            await expect(panel.locator("select")).to_have_count(0)
            await expect(panel.locator("details")).to_have_count(0)
            await expect(status).to_contain_text("Not read yet")
            await client_session("sess-A")
            state["hold"].set()
            await page.wait_for_timeout(400)
            await expect(panel.locator("select")).to_have_count(0)
            await expect(panel).not_to_contain_text("PREVIEW-OF-")
            await expect(status).to_contain_text("Not read yet")
            # 6. a new session in the same workspace, and a late FAILURE: the stale error is dropped too
            state["hold"], state["fail"] = asyncio.Event(), True
            await panel.locator(".lucid-context-refresh").click()
            await expect(status).to_contain_text("Reading")
            await client_session("sess-C")
            state["hold"].set()
            await page.wait_for_timeout(400)
            await expect(status).not_to_contain_text("LATE-ERROR-TEXT")
            await expect(status).to_have_attribute("data-error", "false")
            state["hold"], state["fail"] = None, False
            # 7. an editor with unsaved text is never replaced by a late answer or by a session change
            state["session"] = "sess-C"
            state["hold"] = asyncio.Event()
            await panel.locator(".lucid-context-refresh").click()
            await lucid.locator(".lucid-file[data-key='global/instructions']").click()
            editor = lucid.locator(".lucid-editor textarea")
            await editor.fill("UNSAVED TEXT")
            await client_session("sess-D")
            state["hold"].set()
            await page.wait_for_timeout(400)
            await expect(editor).to_have_value("UNSAVED TEXT")
            await expect(lucid.locator(".lucid-context")).to_have_count(0)
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()
