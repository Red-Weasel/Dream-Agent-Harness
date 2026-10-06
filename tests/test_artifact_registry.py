"""The session's artifact registry (DREAM-233, batch A1): what a turn builds, learned from the event funnel.

Pure Python: a registry fed the events the App funnel sees (`tool_use` / `tool_result` of the file tools, `studio`
shows, workers' `agent_activity`, `turn_start` / `turn_end`), reading the files it names from disk. It publishes
small `artifact` events (metadata only) that the Chat page turns into the panel and its cards. No server, no
browser, no model."""
from __future__ import annotations

from pathlib import Path

import pytest

from dream.core.backends.base import Event
from dream.gui.artifacts import ArtifactRegistry

PAGE = "<!doctype html><html><head><title>Coffee Shop</title></head><body><h1>Beans</h1></body></html>"
SVG = "<svg xmlns='http://www.w3.org/2000/svg'><title>Logo</title><circle r='4'/></svg>"


@pytest.fixture
def ws(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def reg(ws, tmp_path):
    events: list[dict] = []
    registry = ArtifactRegistry(ws, "s1", lambda ev: events.append(ev.data), store=tmp_path / "store")
    registry.events = events   # for the tests only
    return registry


def write(reg, ws, rel, text, *, tool="write_file", tid=None, ok=True, append=False, worker=None):
    """A write the way the funnel sees it: tool_use, the real file on disk, tool_result."""
    tid = tid or f"t{len(reg.events)}"
    args = {"path": rel, "content": text}
    if append:
        args["append"] = True
    if tool in ("Write", "Edit", "MultiEdit"):
        args = {"file_path": str(ws / rel), "content": text}
    use = {"id": tid, "name": tool, "input": args}
    result = {"id": tid, "name": tool, "content": "ok" if ok else "boom", "is_error": not ok}
    if worker:
        reg.observe(Event("agent_activity", {"kind": "tool_use", "run_id": worker, "agent": "worker", "data": use}))
    else:
        reg.observe(Event("tool_use", use))
    if ok:
        target = ws / rel if not Path(rel).is_absolute() else Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with target.open("a") as f:
                f.write(text)
        else:
            target.write_text(text)
    if worker:
        reg.observe(Event("agent_activity", {"kind": "tool_result", "run_id": worker, "agent": "worker", "data": result}))
    else:
        reg.observe(Event("tool_result", result))


def opens(reg):
    return [(e["path"], e["reason"]) for e in reg.events if e.get("open")]


def test_the_first_successful_page_write_opens_and_the_versions_are_on_disk(reg, ws, tmp_path):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "coffee-shop/index.html", PAGE)
    (ev,) = reg.events
    assert ev["op"] == "upsert" and ev["open"] is True and ev["reason"] == "auto"
    assert ev["path"] == "coffee-shop/index.html" and ev["type"] == "page" and ev["version"] == 1 and ev["turn"] == 1
    assert ev["title"] == "Coffee Shop", "the page's <title>"
    a = reg.get(ev["id"])
    assert a is not None and len(a.versions) == 1
    stored = tmp_path / "store" / "s1" / ev["id"] / "v1"
    assert stored.read_text() == PAGE, "version 1's bytes under var/artifacts/<session>/<id>/"
    got = reg.read(ev["id"])
    assert got["text"] == PAGE and got["version"] == 1 and got["digest"] == a.versions[0]["digest"]
    assert reg.read(ev["id"], 2) is None and reg.read("nope") is None


def test_a_failed_write_registers_nothing_and_a_non_previewable_file_registers_nothing(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "coffee-shop/index.html", PAGE, ok=False)
    write(reg, ws, "notes.txt", "plain")
    write(reg, ws, "data.csv", "a,b\n1,2\n")
    write(reg, ws, "README.md", "# hi", ok=False)
    assert reg.events == []


def test_an_append_write_is_read_from_disk_not_from_the_tool_input(reg, ws):
    reg.observe(Event("turn_start", {}))
    head = "<!doctype html><title>Parts</title><h1>one</h1>"
    tail = "<p>two</p></html>"
    write(reg, ws, "parts.html", head)
    write(reg, ws, "parts.html", tail, append=True)
    first, second = reg.events
    assert first["version"] == 1 and second["version"] == 2 and second["op"] == "upsert"
    assert second["open"] is False, "the page is open already: a reload, not a second open"
    assert reg.read(second["id"], 2)["text"] == head + tail


def test_an_edit_of_the_open_page_makes_a_version_and_an_unchanged_write_does_not(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "p.html", PAGE)
    (ws / "p.html").write_text(PAGE.replace("Beans", "Roast"))
    reg.observe(Event("tool_use", {"id": "e1", "name": "str_replace_edit", "input": {"path": "p.html", "old_string": "Beans", "new_string": "Roast"}}))
    reg.observe(Event("tool_result", {"id": "e1", "name": "str_replace_edit", "content": "edited", "is_error": False}))
    write(reg, ws, "p.html", PAGE.replace("Beans", "Roast"))   # the same bytes again
    versions = [e["version"] for e in reg.events]
    assert versions == [1, 2], versions


def test_one_auto_open_per_turn_and_index_html_beats_another_page(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/about.html", "<title>About</title>")
    write(reg, ws, "site/index.html", "<title>Home</title>")
    write(reg, ws, "site/contact.html", "<title>Contact</title>")
    assert opens(reg) == [("site/about.html", "auto"), ("site/index.html", "auto")], \
        "the first page opens; index.html replaces it; a third page gets only a card"
    reg.observe(Event("turn_end", {}))
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/other.html", "<title>Other</title>")
    assert opens(reg)[-1] == ("site/other.html", "auto"), "a new turn gets its own auto-open"


def test_the_shallowest_index_html_wins(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/docs/index.html", "<title>Docs</title>")
    write(reg, ws, "site/index.html", "<title>Home</title>")
    write(reg, ws, "site/blog/index.html", "<title>Blog</title>")
    assert opens(reg) == [("site/docs/index.html", "auto"), ("site/index.html", "auto")]


def test_an_svg_opens_but_a_page_written_after_it_replaces_it(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "logo.svg", SVG)
    assert reg.events[-1]["type"] == "svg" and reg.events[-1]["title"] == "Logo" and reg.events[-1]["open"]
    write(reg, ws, "page.html", PAGE)
    assert opens(reg) == [("logo.svg", "auto"), ("page.html", "auto")]


def test_an_explicit_show_wins_over_every_auto_open_in_its_turn(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/index.html", "<title>Home</title>")
    write(reg, ws, "site/about.html", "<title>About</title>")
    reg.observe(Event("studio", {"op": "show", "path": "site/about.html", "title": "about.html", "content": "<title>About</title>"}))
    assert opens(reg)[-1] == ("site/about.html", "explicit")
    write(reg, ws, "site/index.html", "<title>Home v2</title>")
    assert opens(reg)[-1] == ("site/about.html", "explicit"), "a later write never takes the explicit one's place"
    assert reg.events[-1]["path"] == "site/index.html" and reg.events[-1]["open"] is False and reg.events[-1]["version"] == 2


def test_a_mirrored_show_and_an_image_show_register_nothing_new(reg, ws):
    reg.observe(Event("turn_start", {}))
    (ws / "page.html").write_text(PAGE)
    reg.observe(Event("studio", {"op": "show", "path": "page.html", "title": "page.html", "content": PAGE, "source": "mirror"}))
    reg.observe(Event("studio", {"op": "show", "kind": "image", "path": "cap.png", "title": "cap.png", "source": "mirror"}))
    assert reg.events == []


def test_a_dependency_write_reloads_the_pages_of_its_folder_and_adds_no_card(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/index.html", "<title>Home</title><link rel=stylesheet href=style.css>")
    write(reg, ws, "site/style.css", "body{background:teal}")
    write(reg, ws, "elsewhere/app.js", "console.log(1)")
    ops = [(e["op"], e["path"]) for e in reg.events]
    assert ops == [("upsert", "site/index.html"), ("reload", "site/index.html")], ops
    assert reg.events[-1]["version"] == 1 and reg.events[-1]["open"] is False


def test_a_page_outside_the_workspace_is_refused(reg, ws, tmp_path):
    outside = tmp_path / "outside.html"
    reg.observe(Event("turn_start", {}))
    write(reg, ws, str(outside), PAGE)
    assert reg.events == [] and outside.is_file()


def test_hidden_and_vendored_folders_and_big_files_are_skipped(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, ".cache/index.html", PAGE)
    write(reg, ws, "node_modules/x/index.html", PAGE)
    write(reg, ws, "uploads/page.html", PAGE)
    write(reg, ws, "big.html", "x" * (ArtifactRegistry.MAX_BYTES + 1))
    assert reg.events == []


def test_the_version_cap_keeps_the_last_fifty(reg, ws, tmp_path, monkeypatch):
    monkeypatch.setattr(ArtifactRegistry, "MAX_VERSIONS", 3)
    reg.observe(Event("turn_start", {}))
    for n in range(5):
        write(reg, ws, "p.html", f"<title>v{n}</title>")
    aid = reg.events[-1]["id"]
    assert reg.events[-1]["version"] == 5
    folder = tmp_path / "store" / "s1" / aid
    assert sorted(p.name for p in folder.iterdir()) == ["v3", "v4", "v5"]
    assert reg.read(aid, 1) is None and reg.read(aid, 5)["text"] == "<title>v4</title>"


def test_a_workers_page_gets_its_card_at_once_and_opens_at_turn_end_if_nothing_else_did(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "w/about.html", "<title>A</title>", worker="run-1")
    write(reg, ws, "w/index.html", "<title>I</title>", worker="run-1")
    assert [e["open"] for e in reg.events] == [False, False], "cards at once, no open while the turn runs"
    reg.observe(Event("turn_end", {}))
    assert opens(reg) == [("w/index.html", "worker")]


def test_a_workers_page_does_not_open_when_the_lead_opened_one(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "lead.html", "<title>L</title>")
    write(reg, ws, "w/index.html", "<title>I</title>", worker="run-1")
    reg.observe(Event("turn_end", {}))
    assert opens(reg) == [("lead.html", "auto")]


def test_the_claude_tools_spell_the_path_absolute(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "deck/index.html", PAGE, tool="Write")
    assert reg.events[-1]["path"] == "deck/index.html" and reg.events[-1]["open"]


def test_a_shell_command_that_writes_a_page_registers_it(reg, ws):
    reg.observe(Event("turn_start", {}))
    reg.observe(Event("tool_use", {"id": "b1", "name": "run_bash", "input": {"command": "python gen.py"}}))
    (ws / "gen").mkdir()
    (ws / "gen" / "index.html").write_text("<title>Generated</title>")
    (ws / "gen" / "chart.svg").write_text(SVG)
    reg.observe(Event("tool_result", {"id": "b1", "name": "run_bash", "content": "done", "is_error": False}))
    paths = sorted(e["path"] for e in reg.events)
    assert paths == ["gen/chart.svg", "gen/index.html"], paths
    assert opens(reg) == [("gen/index.html", "auto")]


def test_a_title_falls_back_to_the_first_heading_then_the_file_name(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "a.html", "<h2>  Pricing   table </h2>")
    write(reg, ws, "b.html", "<p>no title</p>")
    assert [e["title"] for e in reg.events] == ["Pricing table", "b.html"]


def test_a_new_session_starts_a_fresh_registry(ws, tmp_path):
    events = []
    session = ["s1"]
    reg = ArtifactRegistry(lambda: ws, lambda: session[0], lambda ev: events.append(ev.data), store=tmp_path / "store")
    reg.events = events
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "p.html", PAGE)
    aid = events[-1]["id"]
    session[0] = "s2"
    reg.observe(Event("turn_start", {}))
    assert reg.get(aid) is None, "the old session's artifacts are gone from memory (their files stay on disk)"
    write(reg, ws, "p.html", PAGE)
    assert events[-1]["version"] == 1 and events[-1]["turn"] == 1


def test_the_prompt_note_names_the_open_file_its_version_and_its_siblings(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "coffee-shop/index.html", PAGE + "\n" * 10)
    write(reg, ws, "coffee-shop/style.css", "body{}")
    write(reg, ws, "coffee-shop/logo.svg", SVG)
    aid = reg.events[0]["id"]
    note = reg.note(aid)
    assert note.startswith('[Dream, not from the user: the user has "Coffee Shop" open in the preview panel: '
                           'coffee-shop/index.html (v1, 10 lines). "It", "the page" and "the site" mean this file; '
                           'change it with str_replace_edit. Files beside it: logo.svg, style.css.]'), note
    assert reg.note("nope") is None


def test_observe_never_raises_on_odd_payloads(reg):
    for ev in (Event("tool_use", None), Event("tool_use", {"id": 1, "name": None, "input": "x"}),
               Event("tool_result", {"id": "zz"}), Event("studio", "text"), Event("agent_activity", {"kind": "tool_use"}),
               Event("turn_end", None), Event("artifact", {})):
        reg.observe(ev)
    assert reg.events == []


# --- the panel shows more than pages (DREAM-236): reports, images, media, documents ----------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def shell(reg, ws, files, tid="b1"):
    """A run_bash call the way the funnel sees it: tool_use, the files it left on disk, tool_result."""
    reg.observe(Event("tool_use", {"id": tid, "name": "run_bash", "input": {"command": "python make.py"}}))
    for rel, data in files.items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())
    reg.observe(Event("tool_result", {"id": tid, "name": "run_bash", "content": "done", "is_error": False}))


def test_a_markdown_write_is_a_report_titled_by_its_first_heading_with_versions_on_disk(reg, ws, tmp_path):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "findings.md", "intro line\n\n## Quarterly  findings\n\nbody\n")
    (ev,) = reg.events
    assert ev["type"] == "report" and ev["title"] == "Quarterly findings" and ev["open"] is True and ev["reason"] == "auto"
    assert ev["version"] == 1 and ev["bytes"] == len("intro line\n\n## Quarterly  findings\n\nbody\n")
    write(reg, ws, "findings.md", "# Final\n")
    assert reg.events[-1]["version"] == 2 and reg.events[-1]["title"] == "Final" and reg.events[-1]["open"] is False
    assert (tmp_path / "store" / "s1" / ev["id"] / "v1").read_text().startswith("intro line")
    assert reg.read(ev["id"], 2)["text"] == "# Final\n"
    write(reg, ws, "plain.md", "no heading here\n")
    assert reg.events[-1]["title"] == "plain.md"


def test_dreams_own_plan_and_instruction_files_are_not_artifacts(reg, ws):
    reg.observe(Event("turn_start", {}))
    for name in ("PLAN.md", "DREAM.md", "CLAUDE.md", "AGENTS.md", "docs/PLAN.md"):
        write(reg, ws, name, "# Plan\n")
    assert reg.events == []


def test_images_media_and_documents_a_shell_command_leaves_get_their_kinds(reg, ws):
    reg.observe(Event("turn_start", {}))
    shell(reg, ws, {"out/chart.png": PNG, "out/photo.JPG": PNG, "out/clip.mp4": b"\0" * 64, "out/loop.webm": b"\0" * 8,
                    "out/voice.mp3": b"\0" * 16, "out/tone.wav": b"\0" * 16, "out/deck.pptx": b"PK" + b"\0" * 30,
                    "out/paper.pdf": b"%PDF-1.7"})
    kinds = {e["path"]: e["type"] for e in reg.events}
    assert kinds == {"out/chart.png": "image", "out/photo.JPG": "image", "out/clip.mp4": "video", "out/loop.webm": "video",
                     "out/voice.mp3": "audio", "out/tone.wav": "audio", "out/deck.pptx": "document",
                     "out/paper.pdf": "document"}, kinds
    by = {e["path"]: e for e in reg.events}
    assert by["out/clip.mp4"]["bytes"] == 64 and by["out/clip.mp4"]["title"] == "clip.mp4" and by["out/clip.mp4"]["version"] == 1
    assert len(opens(reg)) == 1, "one open for the call: its best file"
    assert opens(reg)[0][0] in ("out/clip.mp4", "out/loop.webm"), "a video outranks audio, an image and a document"
    aid = by["out/chart.png"]["id"]
    got = reg.read(aid)
    assert got["type"] == "image" and got["text"] is None and got["bytes"] == len(PNG), "no copy of a binary file is kept"


def test_a_binary_file_gets_a_version_on_each_rewrite_and_none_when_untouched(reg, ws):
    reg.observe(Event("turn_start", {}))
    shell(reg, ws, {"chart.png": PNG})
    shell(reg, ws, {"chart.png": PNG + b"more"}, tid="b2")
    assert [e["version"] for e in reg.events] == [1, 2]
    reg.observe(Event("tool_use", {"id": "b3", "name": "run_bash", "input": {"command": "true"}}))
    reg.observe(Event("tool_result", {"id": "b3", "name": "run_bash", "content": "", "is_error": False}))
    assert [e["version"] for e in reg.events] == [1, 2], "a call that did not touch the file adds nothing"


def test_the_turns_winner_a_page_over_a_report_over_an_image(reg, ws):
    reg.observe(Event("turn_start", {}))
    shell(reg, ws, {"chart.png": PNG})
    write(reg, ws, "notes.md", "# Notes\n")
    write(reg, ws, "viewer.html", PAGE)
    shell(reg, ws, {"second.png": PNG}, tid="b2")
    write(reg, ws, "more.md", "# More\n")
    assert opens(reg) == [("chart.png", "auto"), ("notes.md", "auto"), ("viewer.html", "auto")], opens(reg)
    assert {e["path"] for e in reg.events} == {"chart.png", "notes.md", "viewer.html", "second.png", "more.md"}, "every one has its card"


def test_a_report_outranks_an_svg_and_an_svg_an_image(reg, ws):
    reg.observe(Event("turn_start", {}))
    shell(reg, ws, {"a.png": PNG})
    write(reg, ws, "logo.svg", SVG)
    write(reg, ws, "notes.md", "# Notes\n")
    assert opens(reg) == [("a.png", "auto"), ("logo.svg", "auto"), ("notes.md", "auto")], opens(reg)


def test_done_or_show_to_user_on_a_report_is_the_explicit_show_and_wins(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/index.html", PAGE)
    write(reg, ws, "summary.md", "# Summary\n")
    assert opens(reg) == [("site/index.html", "auto")]
    reg.observe(Event("tool_use", {"id": "d1", "name": "done", "input": {"path": "summary.md"}}))
    reg.observe(Event("tool_result", {"id": "d1", "name": "done", "content": "Delivered", "is_error": False}))
    assert opens(reg)[-1] == ("summary.md", "explicit")
    write(reg, ws, "site/index.html", PAGE.replace("Beans", "Roast"))
    assert reg.events[-1]["open"] is False, "nothing automatic opens after an explicit show"
    (ws / "deck.pptx").write_bytes(b"PK")
    reg.observe(Event("tool_use", {"id": "d2", "name": "mcp__dream__show_to_user", "input": {"path": "deck.pptx"}}))
    reg.observe(Event("tool_result", {"id": "d2", "name": "mcp__dream__show_to_user", "content": "ok", "is_error": False}))
    assert opens(reg)[-1] == ("deck.pptx", "explicit") and reg.events[-1]["type"] == "document"
    reg.observe(Event("tool_use", {"id": "d3", "name": "done", "input": {"path": "site/index.html"}}))
    reg.observe(Event("tool_result", {"id": "d3", "name": "done", "content": "ok", "is_error": False}))
    assert opens(reg)[-1] == ("deck.pptx", "explicit"), "a page's explicit show is the studio event's, not the tool result's"


def test_a_file_inside_a_built_sites_folder_is_the_sites_and_reloads_it(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "site/index.html", PAGE)
    shell(reg, ws, {"site/img/hero.png": PNG, "site/README.md": "# About\n"})
    ops = [(e["op"], e["path"]) for e in reg.events]
    assert ops == [("upsert", "site/index.html"), ("reload", "site/index.html"), ("reload", "site/index.html")], ops
    reg.observe(Event("turn_end", {}))
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "landing.html", PAGE)                      # a page at the workspace root owns no folder
    shell(reg, ws, {"chart.png": PNG}, tid="b2")
    assert reg.events[-1]["path"] == "chart.png" and reg.events[-1]["type"] == "image"


def test_a_command_that_leaves_many_files_is_an_unpack_not_a_build(reg, ws):
    reg.observe(Event("turn_start", {}))
    files = {f"repo/docs/page{n}.md": f"# {n}\n" for n in range(6)} | {f"repo/img/{n}.png": PNG for n in range(6)}
    shell(reg, ws, files | {"repo/index.html": PAGE})
    assert [e["path"] for e in reg.events] == ["repo/index.html"], "pages register as before; twelve other files are a clone"


def test_the_note_for_a_report_and_for_a_binary_file(reg, ws):
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "notes.md", "# Notes\n\nline\n")
    shell(reg, ws, {"chart.png": PNG})
    by = {e["path"]: e["id"] for e in reg.events}
    assert reg.note(by["notes.md"]) == ('[Dream, not from the user: the user has "Notes" open in the preview panel: notes.md '
                                        '(v1, 3 lines). "It" and "the report" mean this file; change it with '
                                        'str_replace_edit. Files beside it: chart.png.]')
    assert reg.note(by["chart.png"]) == ('[Dream, not from the user: the user has "chart.png" open in the preview panel: '
                                         'chart.png (v1, image, 40 bytes). "It" means this file. Files beside it: notes.md.]')


# --- the Artifacts tab's list (DREAM-235): what was built survives the process and is listed by project --------


def test_the_index_survives_a_restart_and_ids_continue(ws, tmp_path):
    events: list[dict] = []
    reg = ArtifactRegistry(ws, "s1", lambda ev: events.append(ev.data), store=tmp_path / "store")
    reg.events = events
    reg.observe(Event("turn_start", {}))
    write(reg, ws, "coffee-shop/index.html", PAGE)
    write(reg, ws, "notes.md", "# Notes\n")
    again = ArtifactRegistry(ws, "s1", lambda ev: events.append(ev.data), store=tmp_path / "store")
    again.events = events
    assert [a["path"] for a in again.snapshot()] == ["coffee-shop/index.html", "notes.md"]
    first = again.snapshot()[0]
    assert again.read(first["id"])["text"] == PAGE, "a card of the resumed session opens"
    again.observe(Event("turn_start", {}))
    write(again, ws, "third.html", PAGE)
    assert events[-1]["id"] == "a3" and events[-1]["turn"] == 2, "ids and turns go on where the session stopped"
    write(again, ws, "notes.md", "# Notes v2\n")
    assert events[-1]["version"] == 2


def test_the_library_lists_this_session_then_earlier_sessions_of_this_workspace_newest_first(ws, tmp_path):
    store = tmp_path / "store"
    events: list[dict] = []

    def session(name, workspace):
        reg = ArtifactRegistry(workspace, name, lambda ev: events.append(ev.data), store=store)
        reg.events = events
        reg.observe(Event("turn_start", {}))
        return reg

    other_ws = tmp_path / "other"
    other_ws.mkdir()
    old = session("s-old", ws)
    write(old, ws, "old/index.html", PAGE.replace("Coffee Shop", "Old Site"))
    write(old, ws, "old-notes.md", "# Old notes\n")
    write(old, ws, "old-notes.md", "# Old notes v2\n")
    foreign = session("s-foreign", other_ws)
    write(foreign, other_ws, "elsewhere.html", PAGE)
    now = session("s-now", ws)
    write(now, ws, "new.html", PAGE.replace("Coffee Shop", "New Site"))
    (ws / "old" / "index.html").unlink()
    lib = now.library()
    assert [a["title"] for a in lib["session"]] == ["New Site"]
    assert lib["session"][0]["versions"] == 1 and lib["session"][0]["exists"] is True and lib["session"][0]["at"] > 0
    assert [(a["title"], a["session"], a["versions"], a["exists"]) for a in lib["earlier"]] == [
        ("Old notes v2", "s-old", 2, True), ("Old Site", "s-old", 1, False)], lib["earlier"]
    gone = lib["earlier"][1]
    assert now.read(gone["id"], 1, session="s-old")["text"].count("Old Site") == 1, "the stored version outlives the file"
    assert now.read(gone["id"], 1, session="s-foreign") is None and now.read("a1", 1, session="../s-old") is None
    assert now.read("a1", 1, session="nope") is None


def test_a_workspace_change_in_one_session_keeps_each_workspaces_artifacts_apart(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    where = [a]
    events: list[dict] = []
    reg = ArtifactRegistry(lambda: where[0], "s1", lambda ev: events.append(ev.data), store=tmp_path / "store")
    reg.events = events
    reg.observe(Event("turn_start", {}))
    write(reg, a, "p.html", PAGE)
    where[0] = b
    reg.observe(Event("turn_start", {}))
    assert reg.snapshot() == [], "the other project's pages are not this project's"
    write(reg, b, "q.html", PAGE)
    assert events[-1]["id"] == "a2", "one numbering per session: version folders never collide"
    where[0] = a
    reg.observe(Event("turn_start", {}))
    assert [x["path"] for x in reg.snapshot()] == ["p.html"]
    assert [x["path"] for x in reg.library()["session"]] == ["p.html"] and reg.library()["earlier"] == []
