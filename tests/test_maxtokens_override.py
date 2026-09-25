"""Fix list #86 (DREAM-116): `/maxtokens 40000` before a turn changed nothing. The local preset's
max_tokens (16,384 on the MiMo preset) was taken before config.MAX_OUTPUT_TOKENS on every request,
so two 22-minute replies were still cut at 16,384; and the command's confirmation went to the
Terminal tab, not the chat pane the owner typed in. Now an explicit in-session ceiling wins over
the preset's for the rest of the session (still clamped to the window), the chat pane gets Dream's
note, and the model is told about the ceiling so it plans long code in chunks (#85).

FakeEngine/backend are the local-engine stand-ins of test_cache_friendly_head (no socket).
"""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import config
from dream.core import system_prompt
from dream.core.backends.openai_compat import _CTX_MARGIN
from dream.core.profiles import PROFILES, resolve_profile
from dream.core.providers import get_provider
from dream.local.settings import session_options
from dream.memory.store import MemoryStore
from test_cache_friendly_head import FakeEngine, backend, turn

PRESET = 16384
MODEL = "local-preset-model"
ROOT = Path(__file__).resolve().parent.parent
CEILING = "output ceiling"


@pytest.fixture
def ceiling(monkeypatch):
    """The session's ceiling knobs (module state), restored after each test."""
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 131072)
    return config


def _preset_backend(engine, **kw):
    """A local engine whose preset pins max_tokens, read at construction as the real launch does."""
    with session_options({"max_tokens": PRESET}, MODEL):
        return backend(engine, model=MODEL, **kw)


def _capture(seen):
    """A lead reply that records the request's max_tokens."""
    return lambda payload: seen.append(payload["max_tokens"]) or {"text": "ok"}


def _set(ceiling, n):
    """What /maxtokens <n> records."""
    ceiling.MAX_OUTPUT_TOKENS = ceiling.MAX_OUTPUT_TOKENS_OVERRIDE = n


# --- the backend: precedence ---------------------------------------------------------------------

async def test_without_an_override_the_preset_stays_the_ceiling(ceiling):
    seen = []
    b = _preset_backend(FakeEngine(lead=[_capture(seen)]))
    assert b._max_tokens(fill=1000) == PRESET
    await b.prepare_user_turn()
    await turn(b)
    assert seen == [PRESET]


async def test_an_explicit_session_ceiling_wins_over_the_preset(ceiling):
    """The defect: 40,000 asked for, 16,384 sent. n_ctx 200,000, so the window is not the limit."""
    seen = []
    b = _preset_backend(FakeEngine(lead=[_capture(seen)]))
    _set(ceiling, 40000)
    await b.prepare_user_turn()      # the Engine's per-turn step; its performance clamp must follow
    await turn(b)
    assert seen == [40000]


def test_the_turn_clamp_follows_the_override(ceiling):
    """prepare_user_turn fixes _active_performance from performance_status(); a baseline still
    built on the preset would clamp every request back to 16,384."""
    b = _preset_backend(FakeEngine())
    _set(ceiling, 40000)
    asyncio.run(b.prepare_user_turn())
    assert b._active_performance["output_tokens"] == 40000
    assert b._max_tokens(fill=1000) == 40000


def test_the_override_is_still_clamped_to_the_window(ceiling):
    b = _preset_backend(FakeEngine(), n_ctx=50_000)
    _set(ceiling, 40000)
    assert b._max_tokens(fill=20_000) == 50_000 - 20_000 - _CTX_MARGIN
    assert b._max_tokens(fill=49_900) == 1      # never below 1


def test_a_preset_without_max_tokens_is_not_affected(ceiling):
    """No preset ceiling: the configured value already applied before, and still does."""
    b = backend(FakeEngine(), model=MODEL)
    assert b._max_tokens(fill=1000) == 131072
    _set(ceiling, 40000)
    assert b._max_tokens(fill=1000) == 40000


async def test_a_backend_without_a_preset_keeps_its_profile_cap(ceiling):
    """Remote style: no preset, the profile caps output (frontier: 16,384). /maxtokens raises the
    configured ceiling as before, but the override takes only the preset's place: the profile cap
    still bounds the request, the per-turn clamp and the ceiling arithmetic."""
    seen, cap = [], PROFILES["frontier"].output_tokens
    b = backend(FakeEngine(lead=[_capture(seen), _capture(seen)]), key="openai", model="remote-model",
                profile=PROFILES["frontier"])
    await b.prepare_user_turn()
    await turn(b)
    _set(ceiling, 40000)
    await b.prepare_user_turn()
    await turn(b)
    assert seen == [cap, cap]
    assert b._active_performance["output_tokens"] == cap
    assert b._max_tokens(fill=1000) == cap


# --- the command: records the override, tells the chat pane as well as the terminal -------------

def _app():
    from dream.tui.app import App
    app = App(provider="machx", model="m")
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None))
    app.renderer.console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None)
    return app


def test_the_command_records_the_override_and_notes_it_in_the_chat_pane(ceiling):
    app = _app()
    asyncio.run(app._command("/maxtokens 40000"))
    assert ceiling.MAX_OUTPUT_TOKENS_OVERRIDE == 40000 and ceiling.MAX_OUTPUT_TOKENS == 40000
    printed = app.renderer.console.file.getvalue()          # the Terminal tab, as before
    assert "40,000" in printed and "rest of this session" in printed
    notes = [e for e in app.bus.conversation.events if e["kind"] == "system"]   # what the pane renders
    assert len(notes) == 1
    assert notes[0]["data"].startswith("Dream's own note, not from you:")
    assert "40,000" in notes[0]["data"] and "every following generation this session" in notes[0]["data"]


def test_a_rejected_value_records_nothing(ceiling):
    app = _app()
    asyncio.run(app._command("/maxtokens 12"))
    assert ceiling.MAX_OUTPUT_TOKENS_OVERRIDE is None
    assert not app.bus.conversation.events


def test_the_help_says_the_rest_of_this_session_not_one_generation():
    src = (ROOT / "dream" / "tui" / "app.py").read_text()
    assert "ONE generation" not in src
    assert "/maxtokens [n]" in src and "for the rest of this session" in src


# --- #85: the model is told about the ceiling ----------------------------------------------------

def _ceiling_sentence(text):
    assert CEILING in text
    sentence = text[text.index(CEILING):][:400]
    assert "tokens" in sentence and "chunks" in sentence and "discarded" in sentence


def test_both_prompt_texts_tell_the_model_about_its_output_ceiling():
    _ceiling_sentence(system_prompt.BASE)
    _ceiling_sentence(system_prompt.COMPACT_BASE)


def _store(tmp_path, monkeypatch):
    for name, sub in (("SEMANTIC_DIR", "sem"), ("PROCEDURAL_DIR", "proc"), ("EPISODIC_DIR", "ep")):
        (tmp_path / sub).mkdir()
        monkeypatch.setattr(config, name, tmp_path / sub)
    monkeypatch.setattr(config, "IDENTITY_FILE", tmp_path / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", tmp_path / "THREADS.md")
    return MemoryStore(tmp_path / "db.sqlite")


def test_the_built_prompt_carries_the_ceiling_under_every_profile(tmp_path, monkeypatch):
    """build_system_prompt renders COMPACT_BASE for the compact profiles (lean and balanced; a local
    provider defaults to lean, and the owner's live session runs balanced) and BASE otherwise. The
    sentence must reach the local model it was written for, not only the frontier prompt."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)       # no saved runtime settings: defaults apply
    monkeypatch.delenv("DREAM_PROFILE", raising=False)
    local_default = resolve_profile(get_provider("machx"))
    assert local_default.prompt_style == "compact"
    store = _store(tmp_path, monkeypatch)
    try:
        store.start_session("s1")
        for profile in (PROFILES["balanced"], PROFILES["lean"], local_default, PROFILES["frontier"], None):
            _ceiling_sentence(system_prompt.build_system_prompt(store, "s1", stable_sections=[], profile=profile))
    finally:
        store.close()


def test_the_live_blender_reference_says_one_script_per_part():
    live = ROOT / "skills" / "blender-animation" / "references" / "live.md"
    if not live.exists():
        pytest.skip("the blender-animation skill is not installed (skills/blender-animation)")
    text = live.read_text()
    assert "one script per part" in text and "output ceiling is discarded" in text
