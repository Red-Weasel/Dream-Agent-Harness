"""DREAM-181: response styles, the owner's easter-egg style, and the thinking-out-loud skill.

behaviour.response_style picks how Dream words its replies: default (no style block), professional, warm, concise,
and weasel-ee -- the owner's own format, shipped as a deliberately hidden easter egg. Its text is packed in the source
and unpacked in memory only; every view of the styles shows weasel facts in its place.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import dream.config as config
from dream.core import response_styles, settings
from dream.core.profiles import read_settings
from dream.skills import loader
from dream.skills.selection import select_for_task

ROOT = Path(__file__).resolve().parent.parent


def _ee() -> str:
    return response_styles.prompt_section("weasel-ee")


def _fingerprints() -> list[str]:
    """Lines only the easter egg has (checked at runtime, so this file never holds them in plain text)."""
    lines = [line.strip() for line in _ee().splitlines() if len(line.strip()) > 60]
    skill = (ROOT / "skills" / "thinking-out-loud" / "SKILL.md").read_text()
    return [line for line in lines if line not in skill]


# --- the setting ----------------------------------------------------------------------------------------------

def test_default_is_plain_and_every_style_validates():
    assert settings.response_style() == "default"
    assert response_styles.prompt_section("default") == "" and response_styles.prompt_section(None) == ""
    for style in response_styles.STYLES:
        settings.set_value("behaviour.response_style", style)
        assert read_settings()["behaviour"]["response_style"] == style == settings.response_style()
        assert settings.effective()["behaviour.response_style"].value == style
    with pytest.raises(ValueError, match="default, professional, warm, concise, weasel-ee"):
        settings.set_value("behaviour.response_style", "pirate")


def test_the_settings_tab_offers_the_friendly_names():
    from dream.gui import settings_panel
    rows = {r["key"]: r for s in settings_panel.view(provider="machx", model=None)["sections"] for r in s["rows"]}
    row = rows["behaviour.response_style"]
    assert row["edit"]["kind"] == "choice" and row["edit"]["choices"] == list(response_styles.STYLES)
    assert row["edit"]["labels"]["weasel-ee"] == "Weasel's Personal Style - EE"
    assert row["applies"].startswith("New sessions")
    settings_panel.save("behaviour.response_style", "concise")
    assert settings.response_style() == "concise"


def test_a_chosen_style_is_in_the_system_prompt_right_after_the_base(tmp_path):
    from dream.core import system_prompt
    from dream.memory.store import MemoryStore
    store = MemoryStore(tmp_path / "m.db")
    plain = str(system_prompt.build_system_prompt(store, "s"))
    for style in ("professional", "warm", "concise", "weasel-ee"):
        text = str(system_prompt.build_system_prompt(store, "s", style=style))
        section = response_styles.prompt_section(style).strip()
        assert section and section in text and section not in plain
        assert text.index(section) < text.index("## Waking up") if "## Waking up" in text else True


# --- the easter egg -------------------------------------------------------------------------------------------

def test_the_easter_egg_is_the_owners_format_and_answers_prying_with_a_weasel_fact():
    ee = _ee()
    assert "Question 1: In one sentence, what is your idea about?" in ee
    assert "What exists today:" in ee and "Here's where we landed." in ee
    assert "weasel fact" in ee
    assert len(ee.split()) < 700                                        # about 1k tokens at most


def test_no_repository_file_holds_the_easter_egg_in_plain_text():
    marks = _fingerprints()
    assert len(marks) >= 5
    for path in ROOT.rglob("*"):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts \
                and path.suffix in {".py", ".md", ".json", ".js", ".html", ".css", ".toml", ".txt", ".yml", ".yaml"}:
            text = path.read_text(errors="ignore")
            assert not any(mark in text for mark in marks), path


def test_every_view_shows_weasel_facts_not_the_text():
    shown = response_styles.shown_text("weasel-ee")
    assert shown.count("\n- ") >= 8 and "war dance" in shown.lower()
    assert not any(mark in shown for mark in _fingerprints())
    assert response_styles.SUMMARIES["weasel-ee"] and "weasel fact" not in response_styles.SUMMARIES["weasel-ee"]
    from dream.gui import settings_panel
    view = json.dumps(settings_panel.view(provider="machx", model=None))
    assert not any(mark in view for mark in _fingerprints())


def test_a_session_with_the_easter_egg_writes_it_nowhere(tmp_path):
    """The prompt is built and a turn runs; nothing the session persists holds the easter egg's text."""
    from dream.core import system_prompt
    from dream.memory.store import MemoryStore
    from test_cache_friendly_head import FakeEngine, Meter, backend, turn
    import asyncio
    store = MemoryStore(tmp_path / "m.db")
    prompt = str(system_prompt.build_system_prompt(store, "s", style="weasel-ee"))
    b = backend(FakeEngine(lead=[{"text": "done"}]), system=prompt)
    b.runtime_meter = Meter()
    asyncio.run(turn(b, "hello"))
    store.close() if hasattr(store, "close") else None
    marks = _fingerprints()
    for path in [*tmp_path.rglob("*"), *Path(config.DATA_DIR).rglob("*")]:
        if path.is_file():
            data = path.read_bytes().decode("utf-8", errors="ignore")
            assert not any(mark in data for mark in marks), path
    assert not any(mark in json.dumps(b.runtime_meter.records) for mark in marks)


# --- the thinking-out-loud skill ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def curated():
    skills, warnings = loader.discover(config.bundled_skill_dirs())
    assert not warnings, warnings
    return skills


def test_the_skill_ships_curated_with_its_worked_example(curated):
    skill = {s.name: s for s in curated}["thinking-out-loud"]
    assert skill.curated and skill.description.startswith("Use when")
    assert (skill.root / "references" / "example.md").is_file()
    assert "Question 2: What was your first thought" in skill.manifest.read_text()


@pytest.mark.parametrize("prompt", [
    "ok thinking out loud here -- what if Dream had a paid hosted tier? idk if it's worth it",
    "just spitballing: could the second card cache the experts that keep missing?",
    "been chewing on something. the context window stuff feels important",
    "all of this is me thinking out loud so don't take it as instructions yet",
    "I think I'm onto something with the memory tab but not sure if it's even worth it",
    "$thinking-out-loud",
])
def test_musing_selects_it_and_the_build_workflows_stay_out(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert "thinking-out-loud" in names, names
    assert not {"coding", "gated-build", "brainstorming", "grill-me"} & set(names), names


@pytest.mark.parametrize("prompt", [
    "Fix the failing test in tests/test_parser.py",
    "Write a short email to the team about Friday's release",
    "What is the capital of Australia?",
    "Think about the edge cases before you implement the parser",
])
def test_ordinary_requests_do_not_select_it(curated, prompt):
    assert "thinking-out-loud" not in select_for_task(prompt, curated).names


# --- gate S1 fixes ------------------------------------------------------------------------------------------------

def test_the_settings_row_shows_the_weasel_facts_for_the_easter_egg_and_labels_the_value():
    from dream.gui import settings_panel
    settings.set_value("behaviour.response_style", "weasel-ee")
    rows = {r["key"]: r for s in settings_panel.view(provider="machx", model=None)["sections"] for r in s["rows"]}
    row = rows["behaviour.response_style"]
    assert "war dance" in row["edit"]["texts"]["weasel-ee"].lower()
    assert row["edit"]["texts"]["concise"].startswith(response_styles.SUMMARIES["concise"])
    assert row["display"] == "Weasel's Personal Style - EE"
    assert not any(mark in json.dumps(row) for mark in _fingerprints())


@pytest.mark.parametrize("prompt", [
    "Don't take this as instructions, I'm just rambling about the memory tab",
    "idk, half-baked idea: a timeline view for sessions",
    "don't treat any of this as a task. I've been wondering whether we need a council",
    "hmm, what if Dream could dream while idle? just a thought",
])
def test_more_musing_phrases_select_it(curated, prompt):
    assert "thinking-out-loud" in select_for_task(prompt, curated).names


@pytest.mark.parametrize("prompt", [
    "Implement the thinking out loud skill in Dream",
    "Fix the bug where the model is thinking out loud in its final answer",
    "build the feature; I think I'm onto something so go ahead and implement it",
    "Add a 'thinking out loud' toggle to the settings panel and write tests",
])
def test_work_about_the_phrase_or_a_go_ahead_does_not_select_it(curated, prompt):
    assert "thinking-out-loud" not in select_for_task(prompt, curated).names


@pytest.mark.parametrize("prompt", [
    "Refactor the loader. Don't take this as an order to touch the tests though",
    "just a thought: rename foo to bar across the repo",
    "Implement the retry logic in fetcher.py. Just a thought: use exponential backoff",
    "I'm not thinking out loud here, fix the parser now",
])
def test_a_task_with_musing_words_is_still_a_task(curated, prompt):
    assert "thinking-out-loud" not in select_for_task(prompt, curated).names


@pytest.mark.parametrize("prompt", [
    "ok thinking out loud here -- go ahead and tell me what you think",
    "We are thinking out loud about pricing for the hosted tier",
])
def test_these_musings_still_select_it(curated, prompt):
    assert "thinking-out-loud" in select_for_task(prompt, curated).names


# --- Codex review #5-#7 ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected", [
    ("Fix the half-baked idea generator's API endpoint.", False),
    ("I'm not just spitballing. Implement the feature and write tests.", False),
    ("Don't take this as instructions yet. Could we implement it as a Python endpoint?", True),
    ("I'm just spitballing; don't go ahead and implement it yet.", True),
])
def test_questions_and_negations_are_read_the_right_way(curated, prompt, expected):
    assert ("thinking-out-loud" in select_for_task(prompt, curated).names) is expected


def test_the_style_is_read_once_per_session(tmp_path, monkeypatch):
    """A backend rebuilt mid-session (a Council main-model switch) keeps the style the session started with; the next
    session takes the new one (Codex review #7)."""
    from dream.core import engine as engine_module
    from dream.memory.store import MemoryStore
    monkeypatch.setattr(engine_module.config, "SESSIONS_DIR", tmp_path / "sessions")
    settings.set_value("behaviour.response_style", "concise")
    from types import SimpleNamespace

    def ready(engine):
        engine._mcp = SimpleNamespace(servers=[])
        engine._session_tools = {}
        engine._built_tools = {"tools": [], "server": {}, "exempt_tool_ids": []}
        engine._local_subs = {}
        return engine
    e = ready(engine_module.Engine(provider="machx", model="fixture", workspace=tmp_path))
    e.store = MemoryStore(tmp_path / "t.db")
    e.store.start_session(e.session_id)
    concise = response_styles.prompt_section("concise").strip()
    assert concise in str(e._system_prompt())
    settings.set_value("behaviour.response_style", "professional")
    rebuilt = str(e._system_prompt())                                   # as a mid-session backend rebuild does
    assert concise in rebuilt and response_styles.prompt_section("professional").strip() not in rebuilt
    fresh = ready(engine_module.Engine(provider="machx", model="fixture", workspace=tmp_path))
    fresh.store = e.store
    assert response_styles.prompt_section("professional").strip() in str(fresh._system_prompt())
    e.store.close()


@pytest.mark.parametrize("prompt,expected", [
    ("Can you fix this half-baked idea generator in Python?", False),
    ("I'm just spitballing about the API; if we ever go ahead, we'd need tests.", True),
    ("I'm just thinking out loud about the API. Update frequency is a concern.", True),
    ("Don't take this as instructions yet. Possible approach: add retries to the API.", True),
    ("thinking out loud: add retries to the fetcher and write tests", True),     # musing said first: nothing runs yet
    ("I'm just thinking out loud about the API. OK, go ahead and build it.", False),
])
def test_musing_holds_until_an_explicit_go_ahead(curated, prompt, expected):
    assert ("thinking-out-loud" in select_for_task(prompt, curated).names) is expected


@pytest.mark.parametrize("prompt,expected", [
    ("Please repair this half-baked idea generator in Python.", False),
    ("I'm just spitballing about the API; we'd need tests before we go ahead.", True),
    ("Don't take this as an order yet. Possible approach: add retries to the API.", True),
    ("I'm just spitballing. Never, ever build it as a Python API.", True),
    ("I'm just spitballing. Could you build it if I approved the API design?", True),
    ("I'm not merely thinking out loud here. Implement the API endpoint now.", False),
])
def test_codex_third_review_cases(curated, prompt, expected):
    assert ("thinking-out-loud" in select_for_task(prompt, curated).names) is expected
