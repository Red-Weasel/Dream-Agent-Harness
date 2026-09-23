"""Offline tests for skill memory — the procedural-memory layer that distils a
finished trajectory into a reusable procedure. No model, no network."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import dream.config as config
from dream.memory import skills
from dream.memory.store import MemoryStore
from dream.tools import context as tool_context
from dream.tools import skill_tools
from dream.tools.context import ToolContext, set_context


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "skills.db")


def _skill(**kw) -> skills.Skill:
    base = dict(
        slug="deploy-the-docs-site",
        title="Deploy the docs site",
        when_to_use="Publishing docs after a content change.",
        steps=["Run `npm run build` in web/.", "Upload dist/ with `wrangler pages deploy`."],
        pitfalls=["A stale node_modules builds an empty dist without failing."],
        verification="`curl -sI https://docs.example.com` returns 200.",
        last_verified="2026-08-01",
    )
    base.update(kw)
    return skills.Skill(**base)


# --- format: parse/render round-trip ------------------------------------------


def test_render_parse_round_trip():
    s = _skill()
    body = skills.render_body(s)
    assert skills.parse_skill(s.slug, s.title, body) == s
    # Idempotent: re-rendering a parsed body reproduces it byte for byte.
    assert skills.render_body(skills.parse_skill(s.slug, s.title, body)) == body


def test_render_omits_empty_sections():
    s = _skill(pitfalls=[], verification="", last_verified="")
    body = skills.render_body(s)
    assert "## Pitfalls" not in body and "## Verification" not in body
    assert "Last verified" not in body
    assert skills.parse_skill(s.slug, s.title, body) == s


def test_parse_tolerates_hand_edited_list_markers():
    body = (
        "## When to use\nRestoring the DB.\n\n"
        "## Steps\n- first\n* second\n3) third\n\n"
        "## Pitfalls\n1. only one\n"
    )
    s = skills.parse_skill("restore", "Restore", body)
    assert s.steps == ["first", "second", "third"]
    assert s.pitfalls == ["only one"]


# --- save: a skill is a procedural memory --------------------------------------


def test_save_creates_procedural_memory_findable_by_recall(tmp_path):
    st = _store(tmp_path)
    mem = skills.save_skill(
        st,
        title="Deploy the docs site",
        when_to_use="Publishing docs after a content change.",
        steps=["Run `npm run build` in web/."],
        pitfalls=["Stale node_modules builds an empty dist."],
        verification="`curl -sI` returns 200.",
    )
    assert mem["kind"] == "procedural"
    assert mem["slug"] == "deploy-the-docs-site"

    hits = st.search_memories("deploy docs site", kind="procedural")
    assert [h["slug"] for h in hits] == ["deploy-the-docs-site"]
    assert "## Steps" in hits[0]["body"]


def test_save_stamps_last_verified(tmp_path):
    st = _store(tmp_path)
    mem = skills.save_skill(st, "T", "when", ["do it"], [], "check it")
    assert f"Last verified: {skills._today()}" in mem["body"]


def test_save_honours_an_explicit_slug(tmp_path):
    st = _store(tmp_path)
    skills.save_skill(st, "T", "when", ["a"], [], "", slug="pinned")
    assert st.get_memory("pinned") is not None


# --- patch: amend one section, preserve the rest --------------------------------


def test_patch_amends_one_section_and_preserves_the_others(tmp_path):
    st = _store(tmp_path)
    skills.save_skill(
        st,
        title="Deploy the docs site",
        when_to_use="Publishing docs after a content change.",
        steps=["Run `npm run build` in web/."],
        pitfalls=["Stale node_modules builds an empty dist."],
        verification="`curl -sI` returns 200.",
        slug="deploy",
    )
    mem = skills.patch_skill(st, "deploy", pitfalls=["The CDN caches index.html for 5m."])

    s = skills.parse_skill(mem["slug"], mem["title"], mem["body"])
    assert s.pitfalls == ["The CDN caches index.html for 5m."]
    # Everything else survived the amendment untouched.
    assert s.when_to_use == "Publishing docs after a content change."
    assert s.steps == ["Run `npm run build` in web/."]
    assert s.verification == "`curl -sI` returns 200."
    assert s.title == "Deploy the docs site"


def test_patch_preserves_salience_and_tags(tmp_path):
    st = _store(tmp_path)
    skills.save_skill(st, "T", "when", ["a"], [], "", slug="p")
    st.upsert_memory("procedural", "T", st.get_memory("p")["body"], slug="p",
                     tags="deploy,docs", salience=4.0)
    skills.patch_skill(st, "p", steps=["b"])
    row = st.get_memory("p")
    assert row["salience"] == 4.0 and row["tags"] == "deploy,docs"


def test_patch_restamps_last_verified(tmp_path):
    st = _store(tmp_path)
    skills.save_skill(st, "T", "when", ["a"], [], "run the suite", slug="p")
    st.upsert_memory(
        "procedural", "T",
        skills.render_body(_skill(slug="p", title="T", last_verified="2020-01-01")),
        slug="p",
    )
    assert "Last verified: 2020-01-01" in st.get_memory("p")["body"]
    # A bare patch is a re-verification: nothing amended, the marker moves.
    mem = skills.patch_skill(st, "p")
    assert f"Last verified: {skills._today()}" in mem["body"]


def test_patch_unknown_slug_returns_none(tmp_path):
    st = _store(tmp_path)
    assert skills.patch_skill(st, "nope", steps=["x"]) is None


def test_patch_refuses_a_non_procedural_memory(tmp_path):
    st = _store(tmp_path)
    st.upsert_memory("semantic", "A fact", "the user prefers bullets.", slug="fact")
    with pytest.raises(ValueError):
        skills.patch_skill(st, "fact", steps=["x"])


# --- index: progressive disclosure ---------------------------------------------


def test_index_lines_are_bounded_and_one_line_per_skill(tmp_path):
    st = _store(tmp_path)
    for i in range(5):
        skills.save_skill(
            st,
            title=f"Skill {i}",
            when_to_use="A deliberately overlong when-to-use line. " * 20,
            steps=["do it"],
            pitfalls=[],
            verification="",
        )
    lines = skills.index_lines(st)
    assert len(lines) == 5
    for ln in lines:
        assert "\n" not in ln
        assert len(ln) <= skills.INDEX_LINE_CHARS


def test_index_lines_respects_the_limit(tmp_path):
    st = _store(tmp_path)
    for i in range(4):
        skills.save_skill(st, f"Skill {i}", "when", ["do it"], [], "")
    assert len(skills.index_lines(st, limit=2)) == 2


def test_index_lines_lead_with_the_most_recently_updated(tmp_path):
    """What the limit drops matters: the cut must fall on the coldest skills.
    updated_at is second-resolution, so it's set directly rather than raced."""
    st = _store(tmp_path)
    for i in range(3):
        skills.save_skill(st, f"Skill {i}", "when", ["do it"], [], "")
        st._conn.execute(
            "UPDATE memories SET updated_at=? WHERE slug=?",
            (f"2026-0{i + 1}-01T00:00:00+00:00", f"skill-{i}"),
        )
    st._conn.commit()
    assert [ln.split(" — ")[0] for ln in skills.index_lines(st, limit=2)] == [
        "skill-2", "skill-1"
    ]


def test_index_lines_ignores_other_kinds(tmp_path):
    st = _store(tmp_path)
    st.upsert_memory("semantic", "A fact", "not a skill")
    skills.save_skill(st, "Real skill", "when", ["do it"], [], "")
    assert [ln.split(" — ")[0] for ln in skills.index_lines(st)] == ["real-skill"]


# --- load: the full body on demand ---------------------------------------------


def test_load_returns_the_full_body(tmp_path):
    st = _store(tmp_path)
    mem = skills.save_skill(
        st, "Deploy", "when", ["one", "two"], ["careful"], "check", slug="deploy"
    )
    body = skills.load_skill(st, "deploy")
    assert body == mem["body"]
    assert "## Steps" in body and "two" in body and "careful" in body


def test_load_unknown_slug_returns_none(tmp_path):
    assert skills.load_skill(_store(tmp_path), "nope") is None


# --- legacy / malformed procedural memories ------------------------------------


def test_legacy_procedural_memory_survives_index_load_and_patch(tmp_path):
    st = _store(tmp_path)
    st.upsert_memory("procedural", "Search well", "web_search then browse top results.")
    # Parsing must not crash, and the prose must not be thrown away.
    s = skills.parse_skill("search-well", "Search well", "web_search then browse top results.")
    assert s.steps == [] and s.pitfalls == []
    assert "web_search then browse" in s.when_to_use

    lines = skills.index_lines(st)
    assert len(lines) == 1 and lines[0].startswith("search-well — ")
    assert skills.load_skill(st, "search-well") == "web_search then browse top results."

    # Patching one adds structure without losing the original text.
    mem = skills.patch_skill(st, "search-well", steps=["web_search", "browse the top 3"])
    assert "web_search then browse top results." in mem["body"]
    assert "## Steps" in mem["body"]


def test_empty_body_does_not_crash_the_parser(tmp_path):
    st = _store(tmp_path)
    st.upsert_memory("procedural", "Blank", "")
    assert skills.parse_skill("blank", "Blank", "") == skills.Skill(slug="blank", title="Blank")
    # With nothing to describe it, the index falls back to the title.
    assert skills.index_lines(st) == ["blank — Blank"]


def test_headings_only_body_does_not_crash(tmp_path):
    s = skills.parse_skill("h", "H", "## Steps\n\n## Pitfalls\n\n## Verification\n")
    assert s == skills.Skill(slug="h", title="H")


# --- the tool layer -------------------------------------------------------------


@pytest.fixture
def tool_env(tmp_path, monkeypatch):
    """A store plus a redirected markdown mirror, installed as the tool context."""
    monkeypatch.setattr(config, "PROCEDURAL_DIR", tmp_path / "proc")
    monkeypatch.setattr(config, "SEMANTIC_DIR", tmp_path / "sem")
    monkeypatch.setattr(config, "EPISODIC_DIR", tmp_path / "ep")
    for d in (config.PROCEDURAL_DIR, config.SEMANTIC_DIR, config.EPISODIC_DIR):
        d.mkdir()
    st = _store(tmp_path)
    set_context(
        ToolContext(
            store=st,
            working=SimpleNamespace(),
            browser=SimpleNamespace(),
            session_id="test-session",
            workspace=tmp_path,
        )
    )
    yield st
    tool_context._CTX = None


def _text(result) -> str:
    return result["content"][0]["text"]


async def test_skill_save_tool_writes_memory_and_mirror(tool_env):
    result = await skill_tools.skill_save.handler(
        {
            "title": "Deploy the docs site",
            "when_to_use": "Publishing docs after a content change.",
            "steps": ["Run `npm run build`.", "Deploy dist/."],
            "pitfalls": ["Stale node_modules."],
            "verification": "`curl -sI` returns 200.",
        }
    )
    assert not result.get("is_error")
    assert "deploy-the-docs-site" in _text(result)
    assert tool_env.get_memory("deploy-the-docs-site")["kind"] == "procedural"
    mirrored = config.MEMORY_DIR / "deploy-the-docs-site.md"  # Phase 9: flat
    assert mirrored.exists() and "## Steps" in mirrored.read_text(encoding="utf-8")
    # The session that learned it is recorded.
    assert tool_env.get_memory("deploy-the-docs-site")["source_session"] == "test-session"


async def test_skill_save_accepts_newline_separated_steps(tool_env):
    """Local models emit a newline block where the schema declares an array."""
    result = await skill_tools.skill_save.handler(
        {"title": "T", "when_to_use": "w", "steps": "first\nsecond", "verification": "v"}
    )
    assert not result.get("is_error")
    s = skills.parse_skill("t", "T", tool_env.get_memory("t")["body"])
    assert s.steps == ["first", "second"]


async def test_skill_save_requires_steps(tool_env):
    result = await skill_tools.skill_save.handler(
        {"title": "T", "when_to_use": "w", "steps": [], "verification": "v"}
    )
    assert result.get("is_error") is True
    assert tool_env.all_memories(kind="procedural") == []


async def test_skill_patch_tool_amends_and_remirrors(tool_env):
    await skill_tools.skill_save.handler(
        {"title": "Deploy", "when_to_use": "w", "steps": ["a"], "verification": "v"}
    )
    result = await skill_tools.skill_patch.handler(
        {"slug": "deploy", "pitfalls": ["CDN caches for 5m"]}
    )
    assert not result.get("is_error")
    body = tool_env.get_memory("deploy")["body"]
    assert "CDN caches for 5m" in body and "## Steps" in body
    mirrored = (config.MEMORY_DIR / "deploy.md").read_text(encoding="utf-8")  # Phase 9: flat
    assert "CDN caches for 5m" in mirrored


async def test_skill_patch_unknown_slug_errs(tool_env):
    result = await skill_tools.skill_patch.handler({"slug": "ghost", "steps": ["x"]})
    assert result.get("is_error") is True
    assert "ghost" in _text(result)


async def test_skill_list_and_load_tools(tool_env):
    await skill_tools.skill_save.handler(
        {"title": "Deploy", "when_to_use": "Publishing docs.", "steps": ["a"],
         "verification": "v"}
    )
    listed = _text(await skill_tools.skill_list.handler({}))
    assert "deploy — Publishing docs." in listed

    loaded = _text(await skill_tools.skill_load.handler({"slug": "deploy"}))
    assert "## Steps" in loaded and "a" in loaded

    missing = await skill_tools.skill_load.handler({"slug": "ghost"})
    assert missing.get("is_error") is True


async def test_skill_load_points_an_installed_skill_name_at_skill_open(tool_env, monkeypatch):
    """2026-09-23, live session: search_skills listed 'understand-dashboard', then skill_load(slug=...) said only
    "No skill with slug" and pointed at skill_list -- which lists the owner's LEARNED skills, not installed ones."""
    from types import SimpleNamespace
    from dream.tools import installed_skill_tools
    monkeypatch.setattr(installed_skill_tools, "_by_name",
                        lambda n: SimpleNamespace(name=n) if n == "understand-dashboard" else None)
    hit = await skill_tools.skill_load.handler({"slug": "understand-dashboard"})
    assert hit.get("is_error") is True
    assert 'skill_open(name="understand-dashboard")' in _text(hit)
    miss = await skill_tools.skill_load.handler({"slug": "ghost"})
    assert miss.get("is_error") is True
    assert "skill_list" in _text(miss) and "skill_open" in _text(miss)


async def test_skill_list_when_empty(tool_env):
    result = await skill_tools.skill_list.handler({})
    assert not result.get("is_error")
    assert "No skills" in _text(result)


def test_skill_tools_export():
    assert [t.name for t in skill_tools.SKILL_TOOLS] == [
        "skill_save", "skill_patch", "skill_list", "skill_load"
    ]


# --- integrator hardening (findings from the Phase B gate) -------------------


def test_multi_line_steps_survive_a_round_trip():
    """A step worth writing down carries the exact command, so it spans lines.
    Splitting per line renumbered the continuation lines as steps of their own:
    a 2-step skill became a 5-step skill on the first parse."""
    s = skills.Skill(
        slug="deploy", title="Deploy",
        when_to_use="When shipping.",
        steps=["Run the build:\n```bash\nnpm run build\n```", "Deploy it."],
        pitfalls=["Restarting first hides the cause.\nRead the logs before you do."],
        verification="curl /health returns 200",
        last_verified="2026-08-01",
    )
    body = skills.render_body(s)
    back = skills.parse_skill("deploy", "Deploy", body)
    assert back.steps == s.steps
    assert back.pitfalls == s.pitfalls
    assert skills.render_body(back) == body  # stable under repeated patching


async def test_patch_refuses_to_wipe_a_section_with_an_empty_list(tool_env):
    await skill_tools.skill_save.handler({
        "title": "Wipe me", "when_to_use": "never",
        "steps": ["a", "b"], "verification": "check",
    })
    slug = skills.index_lines(tool_env)[0].split(" —")[0].strip()
    res = await skill_tools.skill_patch.handler({"slug": slug, "steps": []})
    assert res.get("is_error")
    assert "Steps" in tool_env.get_memory(slug)["body"]  # the procedure survived
