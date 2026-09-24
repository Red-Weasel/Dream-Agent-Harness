"""DREAM-108: memory is project-scoped.

Owner, 2026-09-24: "i told it to improve terrain and sea water and the memory is making it
bleed into a different repo" -- "memory should be repo specific and project specific".

Two workspaces A and B share one database and one memory folder, the way two Dream
sessions in two repos do. Everything written in A is invisible in B (and back), user-wide
items are visible in both, another project's items come only on an explicit request and
carry their project's label, and the system prompt built for B never names A's memories.
The startup migration files legacy rows by evidence only, backs up first, reports, and is
idempotent. Fixtures only: no model, no GPU, no live data.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream import config
from dream.core import system_prompt
from dream.memory import longterm, migration, project, skills
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext, bind_context
from dream.tools.memory_file_tools import (
    memory_append, memory_delete, memory_list, memory_move, memory_read, memory_str_replace,
    memory_write,
)
from dream.tools.memory_tools import forget, read_session, recall, recall_sessions, remember
from dream.tools.notes import note, read_notes
from dream.tools.task_tools import task_add, task_list, task_update


def _t(res):
    return res["content"][0]["text"]


def _bad(res):
    return bool(res.get("is_error"))


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Two sessions in two workspaces over ONE database and ONE memory folder."""
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    db = tmp_path / "data" / "dream.db"
    sides = {}
    for name, folder, sid in (("a", "Deepseek - testing", "20260923-100000-aaaa"),
                              ("b", "Orbital", "20260924-100000-bbbb")):
        ws = tmp_path / folder
        ws.mkdir()
        store = MemoryStore(db)
        store.project = project.project_key(ws)
        store.start_session(sid)
        project.register(ws)
        c = ToolContext(store=store, working=WorkingMemory(store, sid), browser=None,
                        session_id=sid, workspace=ws, tasks=TaskStore(store))
        sides[name] = SimpleNamespace(store=store, ctx=c, sid=sid, ws=ws, key=store.project)
    yield sides
    for side in sides.values():
        side.store.close()


async def call(side, tool, args):
    with bind_context(side.ctx):
        return await tool.handler(args)


# --- the key ---------------------------------------------------------------------------------


def test_one_key_per_workspace_is_the_notebooks_key(tmp_path):
    ws = tmp_path / "Deepseek - testing"
    ws.mkdir()
    key = project.project_key(ws)
    assert project.project_dir(ws).name == key
    assert re.fullmatch(r"deepseek---testing-[0-9a-f]{6}", key)
    assert project.project_key(str(ws) + "/") == key          # one workspace, one key


# --- long-term memory: remember / recall / forget ------------------------------------------


async def test_remember_in_a_is_invisible_in_b_and_back(two):
    a, b = two["a"], two["b"]
    res = await call(a, remember, {"title": "Sea water shader", "body": "Falcon sea uses 5 summed waves."})
    assert not _bad(res), _t(res)
    res = await call(b, remember, {"title": "Terrain in Blender", "body": "Orbital terrain is a displaced plane."})
    assert not _bad(res), _t(res)
    assert "Sea water shader" in _t(await call(a, recall, {"query": "sea water waves"}))
    out_b = _t(await call(b, recall, {"query": "sea water waves"}))
    assert "Sea water shader" not in out_b and "Falcon" not in out_b
    out_a = _t(await call(a, recall, {"query": "terrain displaced plane"}))
    assert "Terrain in Blender" not in out_a and "Orbital" not in out_a
    # the rows and files say whose they are
    assert a.store.get_memory("sea-water-shader")["project"] == a.key
    fm, _ = longterm.parse_markdown((config.MEMORY_DIR / "sea-water-shader.md").read_text())
    assert fm["project"] == a.key


async def test_user_wide_is_deliberate_and_visible_everywhere(two):
    a, b = two["a"], two["b"]
    res = await call(a, remember, {"title": "Owner prefers metric units",
                                   "body": "The owner wants metric units everywhere.", "scope": "user"})
    assert not _bad(res), _t(res)
    assert a.store.get_memory("owner-prefers-metric-units")["project"] == project.USER
    out = _t(await call(b, recall, {"query": "metric units"}))
    assert "Owner prefers metric units" in out and "[user-wide]" in out
    assert _bad(await call(a, remember, {"title": "x", "body": "y", "scope": "everyone"}))


async def test_explicit_cross_project_recall_is_labelled(two):
    a, b = two["a"], two["b"]
    await call(a, remember, {"title": "Sea water shader", "body": "Falcon sea uses 5 summed waves."})
    by_name = _t(await call(b, recall, {"query": "sea water waves", "project": "Deepseek - testing"}))
    assert "Sea water shader" in by_name and f"[project: {a.key}]" in by_name
    by_key = _t(await call(b, recall, {"query": "sea water waves", "project": a.key}))
    assert "Sea water shader" in by_key and f"[project: {a.key}]" in by_key
    everywhere = _t(await call(b, recall, {"query": "sea water waves", "all_projects": True}))
    assert "Sea water shader" in everywhere and f"[project: {a.key}]" in everywhere
    assert _bad(await call(b, recall, {"query": "sea", "project": "no-such-project"}))


async def test_writes_never_land_in_another_projects_memory(two):
    a, b = two["a"], two["b"]
    await call(a, remember, {"title": "Build notes", "body": "Falcon build notes."})
    # the same title in B is B's own memory, never an overwrite of A's
    res = await call(b, remember, {"title": "Build notes", "body": "Orbital build notes."})
    assert not _bad(res), _t(res)
    assert "Falcon build notes." in a.store.get_memory("build-notes")["body"]
    assert "Falcon" not in _t(await call(b, recall, {"query": "build notes"}))
    # naming A's slug from B is refused, with A's label; A's memory is untouched
    res = await call(b, remember, {"title": "Build notes", "body": "overwrite", "slug": "build-notes"})
    assert _bad(res) and a.key in _t(res)
    res = await call(b, forget, {"slug": "build-notes"})
    assert _bad(res) and a.key in _t(res)
    assert "Falcon build notes." in a.store.get_memory("build-notes")["body"]


# --- memory files: memory_list / memory_read / writes / move --------------------------------


async def test_memory_files_are_scoped_and_an_exact_name_needs_the_label(two):
    a, b = two["a"], two["b"]
    res = await call(a, memory_write, {"name": "sea-weave", "content": "t=590 weave is the sea."})
    assert not _bad(res), _t(res)
    await call(b, memory_write, {"name": "rocket-build", "content": "rocket/build.py is headless Blender."})
    assert "sea-weave" in _t(await call(a, memory_list, {}))
    listed_b = _t(await call(b, memory_list, {}))
    assert "sea-weave" not in listed_b and "rocket-build" in listed_b
    everywhere = _t(await call(b, memory_list, {"all_projects": True}))
    assert "sea-weave" in everywhere and f"[project: {a.key}]" in everywhere
    # an exact name of A's memory, read from B, does not silently succeed
    res = await call(b, memory_read, {"name": "sea-weave"})
    assert _bad(res) and a.key in _t(res) and "weave is the sea" not in _t(res)
    res = await call(b, memory_read, {"name": "sea-weave", "project": a.key})
    assert not _bad(res) and "weave is the sea" in _t(res) and f"[project: {a.key}]" in _t(res)
    # writes to A's memory from B need A named
    ver = _t(res).split("\n", 1)[0].split("version: ", 1)[1].split()[0]
    res = await call(b, memory_append, {"name": "sea-weave", "text": "more", "if_version": ver})
    assert _bad(res) and a.key in _t(res)
    res = await call(b, memory_str_replace, {"name": "sea-weave", "old_string": "sea", "new_string": "ocean",
                                             "if_version": ver})
    assert _bad(res) and a.key in _t(res)
    res = await call(b, memory_write, {"name": "sea-weave", "content": "x", "if_version": ver})
    assert _bad(res) and a.key in _t(res)
    res = await call(b, memory_delete, {"name": "sea-weave", "if_version": ver})
    assert _bad(res) and a.key in _t(res)
    res = await call(b, memory_append, {"name": "sea-weave", "text": "fwidth LOD did not bite",
                                        "if_version": ver, "project": a.key})
    assert not _bad(res), _t(res)
    assert a.store.get_memory("sea-weave")["project"] == a.key


async def test_memory_write_user_scope_and_move(two):
    a, b = two["a"], two["b"]
    res = await call(a, memory_write, {"name": "owner-editor", "content": "The owner uses Helix.",
                                       "scope": "user", "provenance": "stated"})
    assert not _bad(res), _t(res)
    assert "owner-editor" in _t(await call(b, memory_list, {}))
    # scope="user" on this project's own memory promotes it, by name and with its version
    await call(a, memory_write, {"name": "owner-shell", "content": "The owner uses fish."})
    assert "owner-shell" not in _t(await call(b, memory_list, {}))
    ver = _t(await call(a, memory_read, {"name": "owner-shell"})).split("\n", 1)[0].split()[1]
    res = await call(a, memory_write, {"name": "owner-shell", "content": "The owner uses fish.",
                                       "scope": "user", "if_version": ver})
    assert not _bad(res), _t(res)
    assert "owner-shell" in _t(await call(b, memory_list, {}))
    await call(a, memory_write, {"name": "falcon-camera", "content": "camera.far is adaptive."})
    res = await call(a, memory_move, {"name": "falcon-camera", "project": "Orbital"})
    assert not _bad(res), _t(res)
    assert "falcon-camera" in _t(await call(b, memory_list, {}))
    assert "falcon-camera" not in _t(await call(a, memory_list, {}))
    fm, _ = longterm.parse_markdown((config.MEMORY_DIR / "falcon-camera.md").read_text())
    assert fm["project"] == b.key


# --- tasks ------------------------------------------------------------------------------------


async def test_tasks_are_scoped_labelled_and_movable(two):
    a, b = two["a"], two["b"]
    assert not _bad(await call(a, task_add, {"title": "Falcon 9: kill t=590 sea moire weave"}))
    assert not _bad(await call(b, task_add, {"title": "Orbital: improve terrain and sea water"}))
    out_a = _t(await call(a, task_list, {}))
    assert "Falcon 9" in out_a and "Orbital" not in out_a
    out_b = _t(await call(b, task_list, {}))
    assert "Orbital" in out_b and "Falcon 9" not in out_b and "sea moire" not in out_b
    everywhere = _t(await call(b, task_list, {"all_projects": True}))
    assert "Falcon 9" in everywhere and f"[project: {a.key}]" in everywhere
    falcon = next(t for t in a.ctx.tasks.list(scope=None) if t["title"].startswith("Falcon"))
    res = await call(b, task_update, {"id": falcon["id"], "status": "done"})
    assert _bad(res) and a.key in _t(res)
    assert a.ctx.tasks.get(falcon["id"])["status"] == "open"
    res = await call(b, task_update, {"id": falcon["id"], "status": "active", "project": a.key})
    assert not _bad(res) and a.ctx.tasks.get(falcon["id"])["project"] == a.key
    # moving A's task needs its source named (gate 1, note 3); then it moves and says so
    res = await call(b, task_update, {"id": falcon["id"], "project": "Orbital"})
    assert _bad(res) and a.key in _t(res) and a.ctx.tasks.get(falcon["id"])["project"] == a.key
    res = await call(b, task_update, {"id": falcon["id"], "project": "Orbital", "from_project": a.key})
    assert not _bad(res) and a.ctx.tasks.get(falcon["id"])["project"] == b.key
    assert f"from {a.key} to {b.key}" in _t(res)
    assert "Falcon 9" in _t(await call(b, task_list, {}))
    # THREADS.md is one file for every project, so each task sits under its project
    threads = config.THREADS_FILE.read_text()
    assert b.key in threads


# --- sessions ---------------------------------------------------------------------------------


async def test_sessions_are_scoped_and_an_exact_id_needs_the_label(two):
    a, b = two["a"], two["b"]
    a.store.start_session("20260920-090000-a0a0")
    a.store.add_turn("20260920-090000-a0a0", "user", "improve the ocean swell on the droneship")
    a.store.end_session("20260920-090000-a0a0", "Falcon sea work.")
    b.store.start_session("20260921-090000-b0b0")
    b.store.add_turn("20260921-090000-b0b0", "user", "improve the terrain displacement")
    b.store.end_session("20260921-090000-b0b0", "Orbital terrain work.")
    listed = _t(await call(b, recall_sessions, {}))
    assert "20260921-090000-b0b0" in listed and "20260920-090000-a0a0" not in listed
    assert "No past session" in _t(await call(b, recall_sessions, {"query": "ocean swell droneship"}))
    hit = _t(await call(b, recall_sessions, {"query": "ocean swell droneship", "project": a.key}))
    assert "20260920-090000-a0a0" in hit and f"[project: {a.key}]" in hit
    # the listing's own locator for another project's session carries the explicit argument
    listed_a = _t(await call(b, recall_sessions, {"project": "Deepseek - testing"}))
    locators = [json.JSONDecoder().raw_decode(part)[0]
                for part in listed_a.split("Open: read_session(")[1:]]
    locator = next(loc for loc in locators if loc["id"] == "20260920-090000-a0a0")
    assert locator == {"id": "20260920-090000-a0a0", "project": a.key}
    assert all(loc.get("project") == a.key for loc in locators)
    assert "ocean swell" in _t(await call(b, read_session, locator))
    res = await call(b, read_session, {"id": "20260920-090000-a0a0"})
    assert _bad(res) and a.key in _t(res) and "ocean swell" not in _t(res)
    res = await call(b, read_session, {"id": "20260920-090000-a0a0", "all_projects": True})
    assert not _bad(res) and "ocean swell" in _t(res) and f"[project: {a.key}]" in _t(res)
    assert a.store.get_session("20260920-090000-a0a0")["project"] == a.key


# --- working notes ----------------------------------------------------------------------------


async def test_notes_follow_their_session_and_cross_only_on_request(two):
    a, b = two["a"], two["b"]
    assert not _bad(await call(a, note, {"text": "sea octave fade did not bite at t=590"}))
    assert not _bad(await call(b, note, {"text": "terrain uses a displace modifier"}))
    assert "sea octave fade" not in _t(await call(b, read_notes, {}))
    assert "sea octave fade" not in _t(await call(b, read_notes, {"query": "octave"}))
    hit = _t(await call(b, read_notes, {"query": "octave", "all_projects": True}))
    assert "sea octave fade" in hit and f"[project: {a.key}]" in hit
    rows = a.store._conn.execute("SELECT note, project FROM working_notes").fetchall()
    assert {r["note"][:4]: r["project"] for r in rows} == {"sea ": a.key, "terr": b.key}


# --- the system prompt and the other automatic injections --------------------------------------


async def test_the_system_prompt_for_b_never_names_as_memories(two):
    a, b = two["a"], two["b"]
    await call(a, remember, {"title": "Falcon sea shader", "body": "Five summed waves at grazing angle."})
    await call(a, task_add, {"title": "Falcon 9: kill t=590 sea moire weave"})
    skills.save_skill(a.store, title="Isolate a Falcon frame artifact", when_to_use="a Falcon frame has a band",
                      steps=["ablate by removeFromParent"], verification="re-capture t=590")
    a.store.end_session(a.sid, "Falcon droneship sea polish session.")
    await call(b, remember, {"title": "Orbital terrain", "body": "Displaced plane with erosion."})
    await call(a, remember, {"title": "Owner likes short answers", "body": "Keep replies short.", "scope": "user"})
    text_b = system_prompt.build_system_prompt(b.store, "20260924-120000-cccc", workspace=b.ws)
    for leak in ("Falcon sea shader", "Falcon 9: kill", "Isolate a Falcon frame artifact",
                 "Falcon droneship sea polish session", "Five summed waves"):
        assert leak not in text_b, leak
    assert "Orbital terrain" in text_b and "Owner likes short answers" in text_b
    text_a = system_prompt.build_system_prompt(a.store, "20260923-120000-dddd", workspace=a.ws)
    assert "Falcon sea shader" in text_a and "Falcon 9: kill" in text_a
    assert "Isolate a Falcon frame artifact" in text_a and "Orbital terrain" not in text_a
    assert "Falcon droneship sea polish session" in text_a


def test_skills_are_scoped_and_another_projects_skill_is_not_loaded(two):
    a, b = two["a"], two["b"]
    mem = skills.save_skill(a.store, title="Falcon frame bisect", when_to_use="band in a frame",
                            steps=["ablate"], verification="diff")
    assert mem["project"] == a.key
    assert not any("falcon-frame-bisect" in ln for ln in skills.index_lines(b.store))
    assert skills.load_skill(b.store, "falcon-frame-bisect") is None
    with pytest.raises(ValueError, match=a.key):
        skills.patch_skill(b.store, "falcon-frame-bisect", steps=["overwrite"])
    assert any("falcon-frame-bisect" in ln for ln in skills.index_lines(a.store))


def test_the_memory_index_file_labels_each_line_with_its_project(two):
    a, b = two["a"], two["b"]
    a.store.upsert_memory("semantic", "Falcon camera", "far plane", persist_markdown=True)
    b.store.upsert_memory("semantic", "Orbital rig", "shake root", persist_markdown=True)
    longterm.write_index()
    text = config.MEMORY_INDEX_FILE.read_text()
    assert f"[project: {a.key}]" in text and f"[project: {b.key}]" in text
    lines_b = longterm.index_lines(scope=(b.key, project.USER))
    assert any("Orbital rig" in ln for ln in lines_b)
    assert not any("Falcon camera" in ln for ln in lines_b)


def test_a_hand_edited_project_line_moves_the_memory_on_the_next_boot(two):
    a, b = two["a"], two["b"]
    a.store.upsert_memory("semantic", "Falcon camera", "far plane", persist_markdown=True)
    path = config.MEMORY_DIR / "falcon-camera.md"
    path.write_text(path.read_text().replace(f"project: {a.key}", f"project: {b.key}"))
    longterm.sync(a.store)
    assert a.store.get_memory("falcon-camera")["project"] == b.key
    longterm.sync(a.store)                     # files win, and a second boot changes nothing
    assert a.store.get_memory("falcon-camera")["project"] == b.key


# --- consolidation --------------------------------------------------------------------------


class _Backend:
    def __init__(self, text):
        self.text, self.prompt = text, None

    async def ask(self, prompt):
        from dream.core.backends.base import Event
        self.prompt = prompt
        yield Event("assistant_done", self.text)


async def test_consolidation_stays_in_its_project(two):
    from dream.core.engine import Engine
    a, b = two["a"], two["b"]
    # an older session of A that died with an unconsolidated note
    a.store.start_session("20260922-080000-a1a1")
    a.store.add_note("20260922-080000-a1a1", "A-ONLY stray: sea foam is too bright")
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    a.store._conn.execute("UPDATE sessions SET started_at=? WHERE id=?", (old, "20260922-080000-a1a1"))
    a.store._conn.execute("UPDATE working_notes SET ts=?", (old,))
    a.store._conn.commit()
    engine = Engine(workspace=b.ws)
    engine.store, engine.session_id = b.store, b.sid
    engine.backend = _Backend("done.\nSUMMARY: terrain session recap")
    engine._tool_context = b.ctx
    summary = await engine.consolidate()
    assert summary == "terrain session recap"
    assert "A-ONLY stray" not in engine.backend.prompt
    episode = b.store.get_memory(f"session-{b.sid}")
    assert episode["project"] == b.key
    assert a.store.stray_notes("nobody", scope=(a.key,))    # still waiting for A's own dream


# --- the migration -----------------------------------------------------------------------------


def _pre_dream108_db():
    """A database as Dream left it before DREAM-108: today's schema (FTS index and its
    triggers included) with the project columns and their indexes dropped again."""
    store = MemoryStore(config.DB_PATH)
    TaskStore(store)
    store.close()
    con = sqlite3.connect(config.DB_PATH)
    con.execute("DROP INDEX idx_memories_project")
    con.execute("DROP INDEX idx_sessions_project")
    for table in ("sessions", "memories", "working_notes", "tasks"):
        con.execute(f"ALTER TABLE {table} DROP COLUMN project")
    return con


def _transcript(sid, records):
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with (config.SESSIONS_DIR / f"{sid}.jsonl").open("w", encoding="utf-8") as fh:
        for role, tool, content in records:
            fh.write(json.dumps({"ts": "2026-09-20T10:00:00+00:00", "role": role, "tool": tool,
                                 "content": content}) + "\n")


def _legacy_memory(slug, title, body, source):
    (config.MEMORY_DIR / f"{slug}.md").write_text(
        f"---\nname: {slug}\ndescription: \ntype: project\nkind: semantic\ntitle: {title}\ntags: \n"
        f"salience: 1.0\ncreated_at: 2026-09-20T10:00:00+00:00\nupdated_at: 2026-09-20T10:00:00+00:00\n---\n\n"
        f"{body}\n", encoding="utf-8")
    return (slug, "semantic", title, body, source)


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """A pre-DREAM-108 database, transcripts, memory files and two project markers."""
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "data" / "sessions")
    falcon, cryp = tmp_path / "Deepseek - testing", tmp_path / "Orbital"
    falcon.mkdir()
    cryp.mkdir()
    project.register(falcon)
    project.register(cryp)
    kf, kc = project.project_key(falcon), project.project_key(cryp)
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = _pre_dream108_db()
    sessions = [
        ("20260920-080000-f001", "polish the sea", "2026-09-20T08:00:00+00:00", "2026-09-20T12:00:00+00:00"),
        ("20260921-080000-c002", "map orbital", "2026-09-21T08:00:00+00:00", "2026-09-21T12:00:00+00:00"),
        ("20260922-080000-c003", "media", "2026-09-22T08:00:00+00:00", "2026-09-22T09:00:00+00:00"),
        ("20260922-100000-x004", "mixed", "2026-09-22T10:00:00+00:00", "2026-09-22T11:00:00+00:00"),
        ("20260922-120000-x005", "no transcript", "2026-09-22T12:00:00+00:00", "2026-09-22T13:00:00+00:00"),
        ("20260922-140000-x006", "elsewhere", "2026-09-22T14:00:00+00:00", "2026-09-22T15:00:00+00:00"),
    ]
    con.executemany("INSERT INTO sessions(id, title, started_at, ended_at) VALUES (?,?,?,?)", sessions)
    _transcript("20260920-080000-f001", [
        ("user", None, "fix the sea weave"),
        ("tool_use", "read_file", json.dumps({"path": f"{falcon}/falcon9/droneship.js"})),
        ("tool_use", "read_file", json.dumps({"path": f"{falcon}/falcon9/main.js"})),
        ("tool_result", "run_bash", f"(exit 0)\n{falcon}/review/t590.png"),
    ])
    _transcript("20260921-080000-c002", [
        ("tool_result", "project_note",
         f"Project memory updated: {config.MEMORY_DIR}/projects/{kc}/PROJECT.md"),
    ])
    _transcript("20260922-080000-c003", [   # a transcript keeps 2,000 chars: the JSON is cut
        ("tool_result", "media_read",
         json.dumps({"workspace": str(cryp), "providers": {"chatgpt": {"url": "https://x"}}})[:-12]),
    ])
    _transcript("20260922-100000-x004", [
        ("tool_use", "read_file", json.dumps({"path": f"{falcon}/a.js"})),
        ("tool_use", "read_file", json.dumps({"path": f"{falcon}/b.js"})),
        ("tool_use", "read_file", json.dumps({"path": f"{cryp}/rocket/build.py"})),
        ("tool_use", "read_file", json.dumps({"path": f"{cryp}/rocket/x.py"})),
    ])
    _transcript("20260922-140000-x006", [
        ("tool_use", "read_file", json.dumps({"path": str(tmp_path / "Payback" / "build_rocket.py")})),
    ])
    mems = [
        _legacy_memory("falcon-sea", "Falcon sea", "The sea is five summed waves.", "20260920-080000-f001"),
        _legacy_memory("orbital-rig", "Rig", "Shake root parents the camera.", "20260921-080000-c002"),
        _legacy_memory("blender-note", "Blender note", "Orbital builds rocket/build.py headless.", None),
        _legacy_memory("two-projects", "Two", f"Orbital and {falcon} share a sea shader.", None),
        _legacy_memory("disagree", "Disagree", "Orbital terrain idea.", "20260920-080000-f001"),
        _legacy_memory("orphan", "Orphan", "Something with no evidence at all.", None),
    ]
    con.executemany(
        "INSERT INTO memories(slug, kind, title, body, source_session, created_at, updated_at) "
        "VALUES (?,?,?,?,?,'2026-09-20T10:00:00+00:00','2026-09-20T10:00:00+00:00')", mems)
    con.executemany("INSERT INTO working_notes(session_id, note, ts) VALUES (?,?,?)", [
        ("20260920-080000-f001", "octave fade did not bite", "2026-09-20T09:00:00+00:00"),
        ("20260922-120000-x005", "orphan note", "2026-09-22T12:30:00+00:00"),
    ])
    con.executemany("INSERT INTO tasks(title, status, notes, created_at, updated_at) VALUES (?,?,?,?,?)", [
        ("Orbital: continue the launch animation", "open", "", "2026-09-23T10:00:00+00:00",
         "2026-09-23T10:00:00+00:00"),
        ("Falcon 9: kill t=590 sea weave", "open", "droneship.js", "2026-09-20T11:59:00+00:00",
         "2026-09-20T11:59:00+00:00"),
        ("Misc: something", "open", "", "2026-08-01T10:00:00+00:00", "2026-08-01T10:00:00+00:00"),
    ])
    con.commit()
    con.close()
    return SimpleNamespace(falcon=falcon, cryp=cryp, kf=kf, kc=kc)


def _owner(table, key_col, key):
    con = sqlite3.connect(config.DB_PATH)
    try:
        return con.execute(f"SELECT project FROM {table} WHERE {key_col}=?", (key,)).fetchone()[0]
    finally:
        con.close()


def test_the_migration_files_legacy_items_by_evidence_only(legacy):
    store = MemoryStore(config.DB_PATH)
    longterm.sync(store)
    result = migration.run(store)
    assert result.applied and result.backup and result.report
    kf, kc, U = legacy.kf, legacy.kc, project.UNASSIGNED
    assert {sid: _owner("sessions", "id", sid) for sid in (
        "20260920-080000-f001", "20260921-080000-c002", "20260922-080000-c003",
        "20260922-100000-x004", "20260922-120000-x005", "20260922-140000-x006")} == {
        "20260920-080000-f001": kf,       # three paths under its workspace
        "20260921-080000-c002": kc,       # a project_note result names the key
        "20260922-080000-c003": kc,       # a tool result recorded the workspace
        "20260922-100000-x004": U,        # paths under two workspaces: not guessed
        "20260922-120000-x005": U,        # no transcript
        "20260922-140000-x006": U,        # only an unknown folder
    }
    assert {slug: store.get_memory(slug)["project"] for slug in (
        "falcon-sea", "orbital-rig", "blender-note", "two-projects", "disagree", "orphan")} == {
        "falcon-sea": kf,                 # its source session
        "orbital-rig": kc,               # its source session
        "blender-note": kc,               # no session; its text names one project
        "two-projects": U,                # names two projects
        "disagree": U,                    # session says Falcon, text says Orbital
        "orphan": U,                      # no evidence
    }
    for slug in ("falcon-sea", "two-projects"):
        fm, _ = longterm.parse_markdown((config.MEMORY_DIR / f"{slug}.md").read_text())
        assert fm["project"] == store.get_memory(slug)["project"]
    notes = dict(store._conn.execute("SELECT note, project FROM working_notes").fetchall())
    assert notes == {"octave fade did not bite": kf, "orphan note": U}
    tasks = {r["title"].split(":")[0]: r["project"] for r in store._conn.execute("SELECT * FROM tasks")}
    assert tasks == {"Orbital": kc, "Falcon 9": kf, "Misc": U}
    report = result.report.read_text()
    assert result.report.name.startswith("memory-migration-") and result.report.parent == config.DATA_DIR
    for needle in ("20260922-100000-x004", "two-projects", "Misc: something", "unassigned",
                   "project_note", "source session", "created while"):
        assert needle in report, needle
    # the backup holds what was there before
    assert (result.backup / "dream.db").is_file()
    before = (result.backup / "memory" / "falcon-sea.md").read_text()
    assert "project:" not in before
    con = sqlite3.connect(result.backup / "dream.db")
    assert con.execute("SELECT COUNT(*) FROM memories WHERE project != ''").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 6
    con.close()
    store.close()


def test_the_migration_is_idempotent_and_loses_nothing(legacy):
    store = MemoryStore(config.DB_PATH)
    longterm.sync(store)
    counts = {t: store._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("sessions", "memories", "working_notes", "turns")}
    first = migration.run(store)
    assert first.applied
    after = {r["slug"]: dict(r) for r in store._conn.execute("SELECT slug, project, body FROM memories")}
    second = migration.run(store)
    assert not second.applied and second.backup is None and second.report is None
    assert after == {r["slug"]: dict(r) for r in store._conn.execute("SELECT slug, project, body FROM memories")}
    assert counts == {t: store._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("sessions", "memories", "working_notes", "turns")}
    assert len(list((config.DATA_DIR / "backups").iterdir())) == 1
    # a reboot re-reads the files and keeps every assignment
    longterm.sync(store)
    assert after == {r["slug"]: dict(r) for r in store._conn.execute("SELECT slug, project, body FROM memories")}
    store.close()


def test_a_dry_run_changes_nothing(legacy, tmp_path):
    store = MemoryStore(config.DB_PATH)
    longterm.sync(store)
    files = {p.name: p.read_text() for p in config.MEMORY_DIR.glob("*.md")}
    out = tmp_path / "dry.md"
    result = migration.run(store, dry_run=True, report_path=out)
    assert not result.applied and result.backup is None and out.is_file()
    assert "20260922-100000-x004" in out.read_text()
    assert files == {p.name: p.read_text() for p in config.MEMORY_DIR.glob("*.md")}
    assert store._conn.execute("SELECT COUNT(*) FROM memories WHERE project=''").fetchone()[0] == 6
    assert not (config.DATA_DIR / "backups").exists()
    store.close()


def test_the_backups_keep_the_last_few_and_nothing_else_is_pruned(legacy):
    backups = config.DATA_DIR / "backups"
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"):
        (backups / f"{migration.BACKUP_PREFIX}{stamp}").mkdir(parents=True)
    (backups / "memory-wipe-20260716-190420").mkdir()
    store = MemoryStore(config.DB_PATH)
    longterm.sync(store)
    result = migration.run(store)
    kept = sorted(p.name for p in backups.iterdir() if p.name.startswith(migration.BACKUP_PREFIX))
    assert len(kept) == migration.KEEP_BACKUPS and result.backup.name == kept[-1]
    assert (backups / "memory-wipe-20260716-190420").is_dir()
    store.close()


async def test_unassigned_items_are_hidden_listable_and_reassignable(legacy):
    store = MemoryStore(config.DB_PATH)
    longterm.sync(store)
    migration.run(store)
    store.project = legacy.kc
    store.start_session("20260924-090000-cccc")
    side = SimpleNamespace(ctx=ToolContext(store=store, working=WorkingMemory(store, "20260924-090000-cccc"),
                                           browser=None, session_id="20260924-090000-cccc",
                                           workspace=legacy.cryp, tasks=TaskStore(store)))
    assert "orphan" not in _t(await call(side, memory_list, {}))
    assert "Something with no evidence" not in _t(await call(side, recall, {"query": "evidence"}))
    listed = _t(await call(side, memory_list, {"all_projects": True}))
    assert "orphan" in listed and "[unassigned]" in listed
    assert "Misc: something" not in _t(await call(side, task_list, {}))
    assert "[unassigned]" in _t(await call(side, task_list, {"all_projects": True}))
    assert not _bad(await call(side, memory_move, {"name": "orphan", "project": legacy.kc}))
    assert "orphan" in _t(await call(side, memory_list, {}))
    misc = next(t for t in side.ctx.tasks.list(scope=None) if t["title"].startswith("Misc"))
    assert not _bad(await call(side, task_update, {"id": misc["id"], "project": "Orbital"}))
    assert "Misc: something" in _t(await call(side, task_list, {}))
    res = await call(side, memory_move, {"session": "20260922-100000-x004", "project": legacy.kc})
    assert not _bad(res), _t(res)
    assert "20260922-100000-x004" in _t(await call(side, recall_sessions, {}))
    store.close()


def test_the_engine_migrates_then_opens_a_scoped_session(legacy):
    from dream.core.engine import Engine
    engine = Engine(workspace=legacy.cryp)
    engine._open_memory()
    try:
        assert engine.store.project == legacy.kc == engine.project
        assert engine.store.get_session(engine.session_id)["project"] == legacy.kc
        assert _owner("sessions", "id", "20260920-080000-f001") == legacy.kf
        (backup,) = list((config.DATA_DIR / "backups").iterdir())
        con = sqlite3.connect(backup / "dream.db")
        assert "project" not in {r[1] for r in con.execute("PRAGMA table_info(memories)")}
        con.close()
        assert "project:" not in (backup / "memory" / "falcon-sea.md").read_text()
        assert list(config.DATA_DIR.glob("memory-migration-*.md"))
        assert (project.project_dir(legacy.cryp) / ".workspace").read_text() == str(legacy.cryp.resolve())
    finally:
        engine.store.close()


# --- gate 1 notes (DREAM-108 re-check) -------------------------------------------------------

_TWO_STARTS = r'''
import sys
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
print("ready", flush=True)
store = MemoryStore(sys.argv[1])
TaskStore(store)
store.close()
print("done", flush=True)
'''


@pytest.mark.parametrize("missing", ["every table", "tasks only"])
def test_two_first_starts_at_once_both_open_the_old_database(legacy, missing):
    """Note 2: two Dream starts on a pre-108 database both find no `project` column and both
    ALTER TABLE; the second used to crash with "duplicate column name: project". A held write
    lock makes both reach their ALTER before either runs it -- in MemoryStore (every table
    missing) or in TaskStore (only the tasks table missing)."""
    import os
    import subprocess
    import sys
    import time

    if missing == "tasks only":
        MemoryStore(config.DB_PATH).close()       # sessions, memories, notes get theirs now
    repo = Path(__file__).resolve().parents[1]
    hold = sqlite3.connect(config.DB_PATH)
    hold.execute("BEGIN IMMEDIATE")
    env = dict(os.environ, PYTHONPATH=str(repo))
    starts = [subprocess.Popen([sys.executable, "-c", _TWO_STARTS, str(config.DB_PATH)], cwd=repo, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for start in starts:
            assert start.stdout.readline().strip() == "ready"
        time.sleep(1.5)            # both are past their column check, waiting on the lock
    finally:
        hold.rollback()
        hold.close()
    outs = [start.communicate(timeout=60) for start in starts]
    assert [start.returncode for start in starts] == [0, 0], outs
    con = sqlite3.connect(config.DB_PATH)
    for table in ("sessions", "memories", "working_notes", "tasks"):
        assert "project" in {r[1] for r in con.execute(f"PRAGMA table_info({table})")}, table
    con.close()


async def test_moving_another_projects_item_needs_its_source_named(two):
    """Note 3: a move needs only a destination for this project's, user-wide and unassigned
    items; another project's item needs its source named, and every reply says from where to where."""
    a, b = two["a"], two["b"]
    await call(a, memory_write, {"name": "falcon-sea", "content": "Five summed waves at a grazing angle."})
    await call(a, task_add, {"title": "Falcon 9: kill the sea weave"})
    tid = next(t["id"] for t in a.ctx.tasks.list(scope=None) if t["title"].startswith("Falcon"))
    a.store.start_session("20260920-090000-a0a0")
    a.store.add_note("20260920-090000-a0a0", "falcon sea note")
    for tool, args in ((memory_move, {"name": "falcon-sea", "project": "Orbital"}),
                       (memory_move, {"session": "20260920-090000-a0a0", "project": "Orbital"}),
                       (task_update, {"id": tid, "project": "Orbital"}),
                       (task_update, {"id": tid, "project": "user"}),
                       (memory_move, {"name": "falcon-sea", "project": "Orbital", "from_project": "Orbital"}),
                       (task_update, {"id": tid, "project": "Orbital", "from_project": b.key})):
        res = await call(b, tool, args)
        assert _bad(res) and a.key in _t(res), (tool.name, args, _t(res))
    assert a.store.get_memory("falcon-sea")["project"] == a.key
    assert a.store.get_session("20260920-090000-a0a0")["project"] == a.key
    assert a.ctx.tasks.get(tid)["project"] == a.key
    res = await call(b, memory_move, {"name": "falcon-sea", "project": "Orbital",
                                      "from_project": "Deepseek - testing"})
    assert not _bad(res) and f"from {a.key} to {b.key}" in _t(res), _t(res)
    res = await call(b, memory_move, {"session": "20260920-090000-a0a0", "project": "Orbital",
                                      "from_project": a.key})
    assert not _bad(res) and f"from {a.key} to {b.key}" in _t(res), _t(res)
    res = await call(b, task_update, {"id": tid, "project": "Orbital", "from_project": a.key})
    assert not _bad(res) and f"from {a.key} to {b.key}" in _t(res), _t(res)
    assert b.ctx.tasks.get(tid)["project"] == b.key
    # this project's, user-wide and unassigned items need no source; the reply still says both ends
    await call(b, memory_write, {"name": "rig-note", "content": "Shake root parents the camera."})
    res = await call(b, memory_move, {"name": "rig-note", "project": "user"})
    assert not _bad(res) and f"from {b.key} to user" in _t(res), _t(res)
    res = await call(a, memory_move, {"name": "rig-note", "project": "Deepseek - testing"})
    assert not _bad(res) and f"from user to {a.key}" in _t(res), _t(res)
    a.store.upsert_memory("semantic", "Loose", "No evidence placed this.", slug="loose",
                          project=project.UNASSIGNED, persist_markdown=True)
    res = await call(b, memory_move, {"name": "loose", "project": "Orbital"})
    assert not _bad(res) and f"from unassigned to {b.key}" in _t(res), _t(res)
    await call(b, task_add, {"title": "Orbital: terrain"})
    mine = next(t["id"] for t in b.ctx.tasks.list(scope=None) if t["title"] == "Orbital: terrain")
    res = await call(b, task_update, {"id": mine, "project": "user"})
    assert not _bad(res) and f"from {b.key} to user" in _t(res), _t(res)
    # unassigned is never a destination, memory_write included
    res = await call(b, memory_write, {"name": "orphan-fact", "content": "x", "project": "unassigned"})
    assert _bad(res) and not (config.MEMORY_DIR / "orphan-fact.md").exists()


def test_after_the_migration_a_hand_written_memory_joins_the_session_that_starts(legacy):
    """Note 4: once the one-time migration has run, a memory file the owner writes by hand with
    no project line belongs to the project of the session that starts, and gets the line; a
    filed memory an older Dream rewrote without its line keeps its project; a line naming a key
    no workspace has is kept and named in the boot log. The migration does not run again."""
    from dream.core.engine import Engine
    first = Engine(workspace=legacy.falcon)
    first._open_memory()
    first.store.close()
    reports = sorted(config.DATA_DIR.glob("memory-migration-*.md"))
    backups = sorted((config.DATA_DIR / "backups").iterdir())
    hand = config.MEMORY_DIR / "handmade-fact.md"
    hand.write_text("---\nname: handmade-fact\ntitle: Handmade fact\nkind: semantic\n---\n\n"
                    "The owner hand-wrote this fact.\n", encoding="utf-8")
    rewritten = config.MEMORY_DIR / "falcon-sea.md"
    rewritten.write_text("\n".join(line for line in rewritten.read_text().splitlines()
                                   if not line.startswith("project:")) + "\n", encoding="utf-8")
    nosuch = config.MEMORY_DIR / "orphan.md"
    nosuch.write_text(nosuch.read_text().replace("project: unassigned", "project: nosuch-123456"))
    second = Engine(workspace=legacy.cryp)
    second._open_memory()
    try:
        store = second.store
        assert store.get_memory("handmade-fact")["project"] == legacy.kc
        assert f"project: {legacy.kc}" in hand.read_text()
        assert store.get_memory("falcon-sea")["project"] == legacy.kf
        assert f"project: {legacy.kf}" in rewritten.read_text()
        assert store.get_memory("orphan")["project"] == "nosuch-123456"
        assert "project: nosuch-123456" in nosuch.read_text()
        log = (config.LOG_DIR / "cli.log").read_text()
        assert "nosuch-123456" in log and "orphan" in log
        assert "handmade-fact" in log
        assert not second.memory_migration.applied
        assert sorted(config.DATA_DIR.glob("memory-migration-*.md")) == reports
        assert sorted((config.DATA_DIR / "backups").iterdir()) == backups
    finally:
        second.store.close()


def test_re_check_notes_latin1_file_old_dreams_write_and_folder_name_line(legacy):
    """Re-check notes: a hand-written file that is not UTF-8 no longer stops the session start
    (its row takes the project; the file stays byte for byte); a memory an older Dream wrote
    after the migration joins its source session's project, not the starting one's; a project
    line naming a project by its folder resolves to that key and the line is rewritten; a line
    that names no project stays hidden and is logged."""
    from dream.core.engine import Engine
    first = Engine(workspace=legacy.falcon)
    first._open_memory()
    first.store.close()
    latin = config.MEMORY_DIR / "latin-fact.md"
    raw = "---\nname: latin-fact\ntitle: Latin fact\n---\n\nCaf\xe9 au lait.\n".encode("latin-1")
    latin.write_bytes(raw)
    folder = config.MEMORY_DIR / "folder-named.md"
    folder.write_text("---\nname: folder-named\ntitle: Folder named\nproject: Orbital\n---\n\n"
                      "Named by its folder.\n", encoding="utf-8")
    nowhere = config.MEMORY_DIR / "nowhere.md"
    nowhere.write_text("---\nname: nowhere\ntitle: Nowhere\nproject: Nowhere At All\n---\n\n"
                       "Names no project.\n", encoding="utf-8")
    old = _legacy_memory("old-write", "Old write", "Written by the old Dream.", "20260920-080000-f001")
    con = sqlite3.connect(config.DB_PATH)
    con.execute("INSERT INTO memories(slug, kind, title, body, source_session, created_at, updated_at) "
                "VALUES (?,?,?,?,?,'2026-09-24T10:00:00+00:00','2026-09-24T10:00:00+00:00')", old)
    con.commit()
    con.close()
    second = Engine(workspace=legacy.cryp)
    second._open_memory()
    try:
        store = second.store
        assert store.get_memory("latin-fact")["project"] == legacy.kc
        assert latin.read_bytes() == raw
        assert store.get_memory("old-write")["project"] == legacy.kf
        assert f"project: {legacy.kf}" in (config.MEMORY_DIR / "old-write.md").read_text()
        assert store.get_memory("folder-named")["project"] == legacy.kc
        assert f"project: {legacy.kc}" in folder.read_text()
        assert store.get_memory("nowhere")["project"] == ""
        assert "project: Nowhere At All" in nowhere.read_text()
        log = (config.LOG_DIR / "cli.log").read_text()
        assert "latin-fact" in log and "not written" in log
        assert "'Nowhere At All' is not a project key" in log
    finally:
        second.store.close()


def _boot(workspace):
    from dream.core.engine import Engine
    engine = Engine(workspace=workspace)
    engine._open_memory()
    return engine


def test_gate3_odd_project_lines_never_stop_the_boot_and_aliases_resolve(legacy, tmp_path):
    """Follow-up gate, findings 1 and 2. A line `project.resolve` could not handle (a ~user form
    naming no user, a symlink loop) raised RuntimeError out of sync and stopped every boot; now it
    stays hidden and is logged. A line shaped like a key but naming a folder or an alias ('global',
    'orbital') was kept as a project no workspace has; now it resolves the way the tools resolve."""
    _boot(legacy.falcon).store.close()
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    lines = {"tilde-user": "~nosuchuser_zz/Orbital", "loop-path": f"{loop}/x", "tilde-name": "~orbital",
             "alias-global": "global", "alias-userwide": "user-wide", "alias-folder": "orbital"}
    for slug, line in lines.items():
        (config.MEMORY_DIR / f"{slug}.md").write_text(
            f"---\nname: {slug}\ntitle: {slug}\nproject: {line}\n---\n\nBody of {slug}.\n", encoding="utf-8")
    expected = {"tilde-user": "", "loop-path": "", "tilde-name": legacy.kc,
                "alias-global": project.USER, "alias-userwide": project.USER, "alias-folder": legacy.kc}
    for _ in range(2):                                  # a second boot changes nothing further
        engine = _boot(legacy.cryp)
        try:
            assert {slug: engine.store.get_memory(slug)["project"] for slug in lines} == expected
        finally:
            engine.store.close()
        for slug, owner in expected.items():
            text = (config.MEMORY_DIR / f"{slug}.md").read_text()
            assert f"project: {owner or lines[slug]}\n" in text, slug
    log = (config.LOG_DIR / "cli.log").read_text()
    assert "tilde-user.md" in log and "loop-path.md" in log and "alias-global.md" in log


def test_gate3_hand_written_files_with_any_name_or_line_ending_are_filed(legacy):
    """Follow-up gate, findings 3 and 4. A hand-written file whose name is not a slug ('My Notes.md',
    'sea_water.md', 'Upper.md') was never adopted: adoption rebuilt its path from the slug. And a
    project line written into a CRLF file turned every line ending into LF."""
    _boot(legacy.falcon).store.close()
    for name in ("My Notes", "sea_water", "Upper"):
        (config.MEMORY_DIR / f"{name}.md").write_text(f"---\ntitle: {name}\n---\n\nNotes in {name}.\n",
                                                      encoding="utf-8")
    crlf_adopt = config.MEMORY_DIR / "crlf-adopt.md"
    crlf_adopt.write_bytes(b"---\r\ntitle: CRLF adopt\r\n---\r\n\r\nNo project line.\r\n")
    crlf_line = config.MEMORY_DIR / "crlf-line.md"
    crlf_line.write_bytes(b"---\r\ntitle: CRLF line\r\nproject: Orbital\r\n---\r\n\r\nA folder-name line.\r\n")
    engine = _boot(legacy.cryp)
    try:
        store = engine.store
        for name, slug in (("My Notes", "my-notes"), ("sea_water", "sea-water"), ("Upper", "upper")):
            assert store.get_memory(slug)["project"] == legacy.kc, slug
            assert f"project: {legacy.kc}\n" in (config.MEMORY_DIR / f"{name}.md").read_text(), name
        for path, slug in ((crlf_adopt, "crlf-adopt"), (crlf_line, "crlf-line")):
            assert store.get_memory(slug)["project"] == legacy.kc, slug
            data = path.read_bytes()
            assert f"project: {legacy.kc}\r\n".encode() in data, slug
            assert b"\n" not in data.replace(b"\r\n", b""), slug          # every line ending still CRLF
    finally:
        engine.store.close()


def test_gate3_recheck_bare_cr_files_keep_their_frontmatter_and_ambiguity_is_named(legacy, tmp_path):
    """Follow-up gate re-check, findings 1 and 2. The CRLF fix split lines on "\\n" only, while Dream
    reads a memory with universal newlines and splitlines(): a bare-CR file looked like it had no
    frontmatter, so a new one went in front and the real one (title, kind, the project line) became
    body text. And a key-shaped line matching two projects was logged as naming no project at all."""
    _boot(legacy.falcon).store.close()
    cr_only = config.MEMORY_DIR / "cr-only.md"
    cr_only.write_bytes(b"---\rtitle: CR only\rkind: procedural\r---\r\rBare CR endings.\r")
    cr_line = config.MEMORY_DIR / "cr-only-line.md"
    cr_line.write_bytes(b"---\rtitle: CR line\rkind: procedural\rproject: Orbital\r---\r\rA folder-name line.\r")
    fence = config.MEMORY_DIR / "fence-cr.md"
    fence.write_bytes(b"---\r\ntitle: Fence CR\r\nkind: procedural\r\n---\r\rThe fence ends in a bare CR.\r\n")
    for parent in ("a", "b"):
        (tmp_path / parent / "shared").mkdir(parents=True)
        project.register(tmp_path / parent / "shared")
    (config.MEMORY_DIR / "ambiguous.md").write_text(
        "---\ntitle: Ambiguous\nproject: shared\n---\n\nTwo folders named shared.\n", encoding="utf-8")
    engine = _boot(legacy.cryp)
    try:
        store = engine.store
        for path, slug, title in ((cr_only, "cr-only", "CR only"), (cr_line, "cr-only-line", "CR line"),
                                  (fence, "fence-cr", "Fence CR")):
            row = store.get_memory(slug)
            assert row["project"] == legacy.kc, slug
            assert (row["title"], row["kind"]) == (title, "procedural"), slug
            fm, body = longterm.parse_markdown(path.read_text(encoding="utf-8"))   # universal newlines
            assert fm["project"] == legacy.kc and fm["title"] == title and not body.startswith("---"), slug
        assert b"\n" not in cr_only.read_bytes() and b"\n" not in cr_line.read_bytes()   # still bare CR
        assert store.get_memory("ambiguous")["project"] == "shared"
        log = (config.LOG_DIR / "cli.log").read_text()
        assert "matches several projects" in log
    finally:
        engine.store.close()


def test_gate3_recheck_a_project_line_that_would_change_the_memory_is_refused(tmp_path):
    """The write is checked before it lands: if placing the line would change anything but the
    project as Dream reads the file back, nothing is written and ValueError says why."""
    path = tmp_path / "sep.md"
    raw = "No frontmatter, and a line separator inside the body.\n"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        longterm.set_project_line(path, "user")
    assert path.read_text(encoding="utf-8") == raw
    ok = tmp_path / "ok.md"
    ok.write_text("---\ntitle: Ok\nproject: user\nproject: unassigned\n---\n\nBody.\n", encoding="utf-8")
    longterm.set_project_line(ok, "user")       # every project line agrees afterwards
    fm, body = longterm.parse_markdown(ok.read_text())
    assert fm == {"title": "Ok", "project": "user"} and body == "Body."


def test_gate3_pass_notes_bom_file_and_non_utf8_file_name(legacy):
    """Follow-up gate PASS notes. A leading UTF-8 byte-order mark hid the whole frontmatter from
    parse_markdown, so a hand-written `project:` line (and the title) was ignored and the file was
    adopted into whichever project started. And a memory file whose NAME is not valid UTF-8 crashed
    write_index at every boot (fix list #73)."""
    import os
    _boot(legacy.falcon).store.close()
    bom = config.MEMORY_DIR / "bom-note.md"
    bom.write_bytes("﻿---\ntitle: BOM note\nproject: Deepseek - testing\n---\n\nWritten with a BOM.\n"
                    .encode("utf-8"))
    bad = os.fsencode(str(config.MEMORY_DIR)) + b"/bad-\xff-name.md"
    with open(bad, "wb") as fh:
        fh.write(b"---\ntitle: Bad name\n---\n\nA name that is not UTF-8.\n")
    bare = config.MEMORY_DIR / "bom-bare.md"          # a BOM and no frontmatter: adopted, block after the BOM
    bare.write_bytes("﻿Just a hand-written line.\n".encode("utf-8"))
    engine = _boot(legacy.cryp)                     # used to raise UnicodeEncodeError in write_index
    try:
        assert engine.store.get_memory("bom-bare")["project"] == legacy.kc
        assert bare.read_bytes() == f"﻿---\nproject: {legacy.kc}\n---\n\nJust a hand-written line.\n".encode()
        row = engine.store.get_memory("bom-note")
        assert row["project"] == legacy.kf and row["title"] == "BOM note"
        data = bom.read_bytes()
        assert data.startswith("﻿---\n".encode()) and f"project: {legacy.kf}\n".encode() in data
        assert os.path.exists(bad)                  # left alone, and named in the boot log
        log = (config.LOG_DIR / "cli.log").read_text(errors="replace")
        assert "not valid UTF-8" in log
    finally:
        engine.store.close()


def test_gate3_a_non_utf8_legacy_file_does_not_cut_the_migration_short(legacy):
    """Follow-up gate, note: the migration's file step caught only OSError, so a legacy file that is
    not UTF-8 raised out of the run after the rows were committed -- no report, no mark, and the
    files after it got no project line."""
    raw = "---\ntitle: Latin legacy\n---\n\nCaf\xe9 au lait.\n".encode("latin-1")
    (config.MEMORY_DIR / "latin-legacy.md").write_bytes(raw)
    store = MemoryStore(config.DB_PATH)
    try:
        longterm.sync(store)
        result = migration.run(store)
        assert result.applied and result.report.is_file() and store.scope_migrated()
        assert (config.MEMORY_DIR / "latin-legacy.md").read_bytes() == raw
        assert any("latin-legacy.md" in e for e in result.errors), result.errors
        for slug in ("orphan", "two-projects"):          # sorted after it: they still get their line
            fm, _ = longterm.parse_markdown((config.MEMORY_DIR / f"{slug}.md").read_text())
            assert fm["project"] == store.get_memory(slug)["project"]
    finally:
        store.close()


async def test_skill_save_over_another_projects_skill_is_refused_cleanly(two):
    """Note 7: skill_save with another project's slug raised an uncaught ScopeError."""
    from dream.tools import skill_tools
    a, b = two["a"], two["b"]
    skills.save_skill(a.store, title="Falcon frame bisect", when_to_use="a band in a frame",
                      steps=["ablate by removeFromParent"], verification="re-capture t=590")
    res = await call(b, skill_tools.skill_save, {"title": "Mine", "when_to_use": "x", "steps": ["overwrite"],
                                                 "verification": "v", "slug": "falcon-frame-bisect"})
    assert _bad(res) and a.key in _t(res), _t(res)
    mem = a.store.get_memory("falcon-frame-bisect")
    assert mem["project"] == a.key and "removeFromParent" in mem["body"]


class _BagOfWords:
    """A stand-in embedder (no model): each word lands in one of 64 buckets."""

    def available(self):
        return True

    def embed(self, text):
        import hashlib

        import numpy as np
        v = np.zeros(64, dtype="float32")
        for word in str(text).lower().split():
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1
        n = np.linalg.norm(v)
        return v / n if n else v


def _vector_stores(tmp_path):
    db = tmp_path / "vectors.db"
    a = MemoryStore(db, embedder=_BagOfWords())
    a.project = "proj-a"
    b = MemoryStore(db, embedder=_BagOfWords())
    b.project = "proj-b"
    a.upsert_memory("semantic", "Falcon sea weave", "the ocean shader weave band at a grazing angle",
                    slug="a-sea-weave")
    return a, b


def test_a_link_never_brings_another_projects_memory_into_recall(tmp_path):
    """Note 1: B's memory links to A's slug; B's recall follows its links, never into A."""
    a, b = _vector_stores(tmp_path)
    b.upsert_memory("semantic", "Rocket terrain", "terrain for the rocket launch site; see [[a-sea-weave]]",
                    slug="b-terrain")
    assert ("b-terrain", "a-sea-weave") in {tuple(r) for r in b._conn.execute(
        "SELECT from_slug, to_slug FROM memory_links")}
    hits = b.search_memories("rocket terrain launch", limit=5)
    assert [h["slug"] for h in hits] == ["b-terrain"]
    assert "a-sea-weave" in [h["slug"] for h in b.search_memories("rocket terrain launch", scope=None)]
    a.close()
    b.close()


def test_vector_recall_never_reaches_another_projects_memory(tmp_path):
    """Note 1: words only A's memory has; B's keyword search cannot see A's row, so only the
    vector path could reach it -- and the vector path is scoped too."""
    a, b = _vector_stores(tmp_path)
    b.upsert_memory("semantic", "Rocket terrain", "displaced plane for the launch site", slug="b-terrain")
    b.upsert_memory("semantic", "Owner likes dark themes", "the user prefers dark themes", slug="u-dark",
                    project=project.USER)
    hits = [h["slug"] for h in b.search_memories("grazing angle band", limit=5)]
    assert "a-sea-weave" not in hits and hits, hits
    assert "a-sea-weave" in [h["slug"] for h in b.search_memories("grazing angle band", scope=None)]
    assert "u-dark" in [h["slug"] for h in b.search_memories("dark themes", limit=5)]
    a.close()
    b.close()
