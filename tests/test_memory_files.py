"""Phase 9a: markdown is the source of truth. One flat file per memory, an index
regenerated on every write, a database rebuilt from the files at boot, the old
three-directory tree migrated with nothing lost, project instructions from the
workspace, and a provenance tag on every fact line."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream import config
from dream.core import instructions, system_prompt
from dream.memory import longterm
from dream.memory.store import MemoryStore


@pytest.fixture
def mem_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setattr(config, "MEMORY_DIR", root)
    monkeypatch.setattr(config, "SEMANTIC_DIR", root / "semantic")
    monkeypatch.setattr(config, "PROCEDURAL_DIR", root / "procedural")
    monkeypatch.setattr(config, "EPISODIC_DIR", root / "episodic")
    monkeypatch.setattr(config, "MEMORY_INDEX_FILE", root / "MEMORY.md")
    monkeypatch.setattr(config, "IDENTITY_FILE", root / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", root / "THREADS.md")
    monkeypatch.setattr(config, "INSTRUCTIONS_FILE", root / "INSTRUCTIONS.md")
    root.mkdir(exist_ok=True)  # conftest already made it
    return root


def _store(tmp_path, name="a.db"):
    return MemoryStore(tmp_path / name)


# --- provenance ----------------------------------------------------------------------


def test_every_fact_line_gets_a_tag_and_structure_does_not():
    body = ("# Heading\n\nThe user likes bullets.\n- first point\n  - nested\n1. numbered\n"
            "[observed] already tagged\n```\ncode line\n```\n| a | b |\n---\n")
    out = longterm.tag_body(body, "stated")
    assert out.splitlines() == [
        "# Heading", "", "[stated] The user likes bullets.", "- [stated] first point",
        "  - [stated] nested", "1. [stated] numbered", "[observed] already tagged",
        "```", "code line", "```", "| a | b |", "---"]
    assert longterm.tag_body("x", "bogus") == "[inferred] x"


# --- one file, the index --------------------------------------------------------------


def test_a_memory_is_one_flat_file_with_the_new_frontmatter(mem_root, tmp_path):
    s = _store(tmp_path)
    mem = s.upsert_memory("semantic", "The user's editor", "[stated] The user uses Helix.",
                          slug="user-editor", description="The user's editor of choice")
    p = longterm.write_markdown(mem)
    assert p == mem_root / "user-editor.md"
    fm, body = longterm.parse_markdown(p.read_text())
    assert fm["name"] == "user-editor" and fm["type"] == "user" and fm["kind"] == "semantic"
    assert fm["description"] == "The user's editor of choice" and body == "[stated] The user uses Helix."
    # the index: one line, the title, the file, the hook
    idx = (mem_root / "MEMORY.md").read_text()
    assert "- [The user's editor](user-editor.md) — The user's editor of choice" in idx
    assert "Helix" not in idx, "the index carries pointers, never bodies"
    # a description left out is the body's first sentence, tag stripped
    mem2 = s.upsert_memory("semantic", "DB layout", "[inferred] Tables live in data/. More.",
                           slug="db-layout")
    longterm.write_markdown(mem2)
    assert "- [DB layout](db-layout.md) — Tables live in data/." in (mem_root / "MEMORY.md").read_text()
    # deleting regenerates too
    longterm.delete_markdown("user-editor")
    idx = (mem_root / "MEMORY.md").read_text()
    assert "user-editor" not in idx and "db-layout" in idx


def test_the_type_is_derived_when_not_named(tmp_path, mem_root):
    s = _store(tmp_path)
    assert s.upsert_memory("episodic", "Session", "[observed] x", slug="e")["mem_type"] == "project"
    assert s.upsert_memory("procedural", "Deploy", "steps", slug="p")["mem_type"] == "reference"
    assert s.upsert_memory("semantic", "The user prefers", "The user likes terse answers", slug="u")["mem_type"] == "user"
    assert s.upsert_memory("semantic", "API", "The endpoint version is v2 per the docs", slug="r")["mem_type"] == "reference"
    with pytest.raises(ValueError):
        s.upsert_memory("semantic", "x", "y", slug="z", mem_type="opinion")


def test_a_reserved_name_and_a_traversal_name_never_touch_the_wrong_file(mem_root, tmp_path):
    s = _store(tmp_path)
    (mem_root / "IDENTITY.md").write_text("I am Dream")
    # a traversal slug is re-slugified to a plain name inside the tree
    p = longterm.write_markdown({"slug": "../../etc/x", "kind": "semantic", "title": "t", "body": "b"})
    assert p == mem_root / "etc-x.md"
    # a reserved stem never reaches disk through the file writer (the store
    # suffixes it first; a caller that skipped the store is refused)
    with pytest.raises(ValueError):
        longterm.write_markdown({"slug": "../IDENTITY", "kind": "semantic", "title": "t", "body": "b"})
    assert (mem_root / "IDENTITY.md").read_text() == "I am Dream"
    assert "IDENTITY.md" not in {f.name for f in longterm.memory_files()}
    # and through the store, the same title lands on the suffixed name
    assert s.upsert_memory("semantic", "Identity", "b")["slug"] == "identity-2"


# --- migration --------------------------------------------------------------------------


def _old_file(d: Path, slug: str, kind: str | None, title: str, body: str, created="2026-06-01T00:00:00+00:00"):
    d.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"slug: {slug}"] + ([f"kind: {kind}"] if kind else []) + [
        f"title: {title}", "tags: ", "salience: 1.0", f"created_at: {created}", f"updated_at: {created}", "---", "", body, ""]
    (d / f"{slug}.md").write_text("\n".join(fm))


def test_the_three_directories_migrate_flat_with_nothing_lost(mem_root, tmp_path):
    sem, pro, epi = mem_root / "semantic", mem_root / "procedural", mem_root / "episodic"
    _old_file(sem, "user-home", "semantic", "The user's home", "The user lives in a house with a lab.")
    _old_file(pro, "debug-playbook", "procedural", "Debug playbook", "## Steps\n- read the log")
    _old_file(epi, "launch-day", None, "Launch day", "We shipped.", created="2026-05-02T09:00:00+00:00")
    # a collision: a name the flat tree already holds with DIFFERENT content
    _old_file(sem, "IDENTITY", "semantic", "Not the identity file", "a memory named like a reserved file")
    (mem_root / "IDENTITY.md").write_text("I am Dream")
    _old_file(pro, "user-home", "procedural", "Another user-home", "different body")

    moved = longterm.migrate_layout()
    assert moved == 5
    names = {f.name for f in longterm.memory_files()}
    assert names == {"user-home.md", "debug-playbook.md", "launch-day.md", "identity-2.md", "user-home-2.md"}
    assert (mem_root / "IDENTITY.md").read_text() == "I am Dream", "the reserved file is untouched"
    assert not list(sem.glob("*.md")) and not list(pro.glob("*.md")) and not list(epi.glob("*.md"))
    assert sem.exists(), "the old directories are left, empty — not deleted"
    launch = longterm.read_file(mem_root / "launch-day.md")
    assert launch["kind"] == "episodic" and launch["mem_type"] == "project"
    assert launch["created_at"] == "2026-05-02T09:00:00+00:00" and launch["body"] == "We shipped."
    home = longterm.read_file(mem_root / "user-home.md")
    assert home["mem_type"] == "user" and home["body"] == "The user lives in a house with a lab."
    assert longterm.read_file(mem_root / "debug-playbook.md")["mem_type"] == "reference"
    # a second run is a no-op
    assert longterm.migrate_layout() == 0
    assert {f.name for f in longterm.memory_files()} == names


def test_the_live_three_memories_migrate_with_no_loss(mem_root, tmp_path):
    """Criterion g, against copies of the real files in this repo's memory/."""
    real = Path(__file__).parent.parent / "memory"
    olds = [p for sub in ("semantic", "procedural", "episodic") for p in (real / sub).glob("*.md")]
    if not olds:
        pytest.skip("the live tree has already migrated")
    before = {}
    for p in olds:
        fm, body = longterm.parse_markdown(p.read_text())
        before[fm.get("slug") or p.stem] = (fm.get("title"), body, fm.get("created_at"))
        (mem_root / p.parent.name).mkdir(exist_ok=True)
        (mem_root / p.parent.name / p.name).write_text(p.read_text())
    s = _store(tmp_path)
    counts = longterm.sync(s)
    assert counts["moved"] == len(olds) and counts["imported"] == len(olds)
    for slug, (title, body, created) in before.items():
        row = s.get_memory(slug)
        assert row and row["title"] == title and row["body"] == body and row["created_at"] == created
        assert row["mem_type"] in ("user", "feedback", "project", "reference")


# --- the database is derived ---------------------------------------------------------


def test_a_hand_edited_file_wins_on_the_next_boot_with_no_tool_call(mem_root, tmp_path):
    s = _store(tmp_path)
    mem = s.upsert_memory("semantic", "The user's editor", "[stated] The user uses Vim.", slug="user-editor")
    longterm.write_markdown(mem)
    assert longterm.sync(s) == {"moved": 0, "imported": 0, "updated": 0, "dropped": 0}
    # the user opens the file and fixes it
    p = mem_root / "user-editor.md"
    p.write_text(p.read_text().replace("Vim", "Helix"))
    s.close()
    s2 = _store(tmp_path)  # a new boot on the same database
    counts = longterm.sync(s2)
    assert counts["updated"] == 1
    assert s2.get_memory("user-editor")["body"] == "[stated] The user uses Helix."
    assert "Helix" in s2.search_memories("editor")[0]["body"]
    # a hand-written file with no row is imported; its created_at is kept
    (mem_root / "new-fact.md").write_text(
        "---\nname: new-fact\ntype: feedback\nkind: semantic\ntitle: Terse\ncreated_at: 2026-01-01T00:00:00+00:00\n---\n[stated] Terse please.\n")
    counts = longterm.sync(s2)
    assert counts["imported"] == 1
    row = s2.get_memory("new-fact")
    assert row["mem_type"] == "feedback" and row["created_at"] == "2026-01-01T00:00:00+00:00"
    # a hand-deleted file drops its row
    (mem_root / "user-editor.md").unlink()
    assert longterm.sync(s2)["dropped"] == 1 and s2.get_memory("user-editor") is None
    assert "user-editor" not in (mem_root / "MEMORY.md").read_text()
    # a malformed file costs nothing
    (mem_root / "broken.md").write_text("---\nname: \n---\n")
    (mem_root / "notes.txt").write_text("not a memory")
    longterm.sync(s2)


def test_import_markdown_is_the_old_name_for_sync(mem_root, tmp_path):
    (mem_root / "x.md").write_text("---\nname: x\nkind: semantic\ntitle: X\n---\nbody\n")
    assert longterm.import_markdown(_store(tmp_path)) == 1


# --- the wake context and project instructions -------------------------------------


def test_the_index_is_loaded_into_the_wake_context(mem_root, tmp_path):
    s = _store(tmp_path)
    longterm.write_markdown(s.upsert_memory("semantic", "The user's editor", "[stated] Helix.",
                                            slug="user-editor", description="what the user edits with"))
    text = system_prompt.build_system_prompt(s, "sess")
    assert "**Memory index**" in text and "- [The user's editor](user-editor.md) — what the user edits with" in text
    assert "[stated]" in text and "[observed]" in text and "[inferred]" in text, "the provenance rule is in the prompt"
    assert "never narrate" in text


def test_project_instructions_come_from_the_workspace(tmp_path, mem_root):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert instructions.project_instructions(ws) == ""
    assert instructions.project_instructions(None) == ""
    (ws / "DREAM.md").write_text("Run tests with make test.\n")
    (ws / "CLAUDE.md").write_text("Never touch migrations.\n")
    sec = instructions.project_instructions(ws)
    assert sec.index("### DREAM.md") < sec.index("### CLAUDE.md")
    assert "make test" in sec and "Never touch migrations" in sec
    # in the prompt: after the user's instructions, before the wake context
    instructions.save("Bullets by default.")
    s = _store(tmp_path)
    text = system_prompt.build_system_prompt(s, "sess", workspace=ws)
    assert text.index("## The user's instructions") < text.index("## This project's instructions") < text.index("## Waking up")
    # a huge file is cut, and says so
    (ws / "DREAM.md").write_text("x" * (config.PROJECT_INSTRUCTIONS_MAX + 500))
    sec = instructions.project_instructions(ws)
    assert "[truncated: DREAM.md is" in sec and len(sec) < config.PROJECT_INSTRUCTIONS_MAX + 2000


def test_near_cap_note(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_FILE_MAX", 100)
    assert longterm.near_cap("x" * 50) == ""
    assert "near the 100 cap" in longterm.near_cap("x" * 85)
    assert "over the 100 cap" in longterm.near_cap("x" * 150) and "don't shave" in longterm.near_cap("x" * 150)


# --- Gate 9a ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_memory_named_like_a_reserved_file_is_suffixed_not_dropped(mem_root, tmp_path):
    """Gate 9a blocking finding: remember(title="Identity") wrote identity.md, which
    the index excludes as reserved, and the next boot dropped the row while the
    tool had reported success. Every writer now lands on identity-2."""
    from dream.tools import context as tool_context
    from dream.tools.context import ToolContext, set_context
    from dream.tools.memory_tools import remember

    s = _store(tmp_path)
    set_context(ToolContext(store=s, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=None))
    try:
        (mem_root / "IDENTITY.md").write_text("I am Dream")
        for title in ("Identity", "Memory", "threads", "INSTRUCTIONS"):
            res = await remember.handler({"title": title, "body": "a fact"})
            assert not res.get("is_error") and f"slug: {title.lower()}-2)" in res["content"][0]["text"], title
            assert (mem_root / f"{title.lower()}-2.md").exists()
        assert (mem_root / "IDENTITY.md").read_text() == "I am Dream"
        idx = (mem_root / "MEMORY.md").read_text()
        assert "identity-2.md" in idx and "memory-2.md" in idx
        # an explicit slug goes the same way; the update lands in place
        res = await remember.handler({"title": "x", "body": "b", "slug": "identity"})
        assert "slug: identity-2)" in res["content"][0]["text"]
        assert len(list(mem_root.glob("identity*.md"))) == 1
        # survives the boot that used to drop it
        s.close()
        s2 = _store(tmp_path)
        assert longterm.sync(s2)["dropped"] == 0
        assert s2.get_memory("identity-2")["body"] == "[stated] b"
        assert s2.get_memory("identity") is None
        # the last line of defense: a writer that bypassed the store is refused
        with pytest.raises(ValueError):
            longterm.write_markdown({"slug": "identity", "kind": "semantic", "title": "t", "body": "b"})
        longterm.delete_markdown("IDENTITY")
        longterm.delete_markdown("identity")
        assert (mem_root / "IDENTITY.md").read_text() == "I am Dream"
    finally:
        tool_context._CTX = None


def test_gate9a_observations_hand_files_are_stable_and_the_index_escapes(mem_root, tmp_path):
    s = _store(tmp_path)
    # a hand-written file without the canonical lines is imported ONCE and then stable
    (mem_root / "hand.md").write_text("---\nname: something-else\ntitle: Hand\n---\nA fact by hand.\n")
    assert longterm.sync(s)["imported"] == 1
    row = s.get_memory("hand")
    assert row and row["body"] == "A fact by hand.", "the stem is the name, not the name: line"
    first = row["updated_at"]
    for _ in range(3):
        assert longterm.sync(s) == {"moved": 0, "imported": 0, "updated": 0, "dropped": 0}
    assert s.get_memory("hand")["updated_at"] == first
    # an edit is still seen
    (mem_root / "hand.md").write_text("---\ntitle: Hand\n---\nA fact by hand, fixed.\n")
    assert longterm.sync(s)["updated"] == 1 and "fixed" in s.get_memory("hand")["body"]
    # an empty file is not a memory; a reserved-stem file is never one
    (mem_root / "empty.md").write_text("")
    (mem_root / "hollow.md").write_text("---\ntitle: Hollow\ntype: user\n---\n\n")
    (mem_root / "identity.md").write_text("---\ntitle: nope\n---\nnot a memory\n")
    assert longterm.sync(s)["imported"] == 0
    assert s.get_memory("empty") is None and s.get_memory("hollow") is None and s.get_memory("identity") is None
    # brackets in a title do not break the index link
    longterm.write_markdown(s.upsert_memory("semantic", "Weird ] title [x]", "[stated] b", slug="weird"))
    assert "- [Weird \\] title \\[x\\]](weird.md)" in (mem_root / "MEMORY.md").read_text()
