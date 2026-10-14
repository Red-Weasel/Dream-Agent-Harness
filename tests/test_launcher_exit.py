"""2026-10-12: _run_harness's `finally` returned for every provider but MachX, and a `return` inside `finally` discards
the exception in flight -- a crash in the terminal app (or Ctrl-C's KeyboardInterrupt) ended `dream` as if the session
had closed normally, with no traceback and exit 0. The MachX unload still runs on the way out, and the crash still
reaches the caller."""
from __future__ import annotations

import types

import pytest

from dream.local import launcher


@pytest.fixture
def crashing_app(monkeypatch):
    import dream.tui.app as app_module
    stops = []

    async def no_workspace(r):
        return None

    async def searxng_up():
        return True

    class CrashingApp:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            raise RuntimeError("the terminal app crashed")

    monkeypatch.setattr(launcher, "_pick_workspace", no_workspace)
    monkeypatch.setattr(launcher.searxng, "ensure_up", searxng_up)
    monkeypatch.setattr(app_module, "App", CrashingApp)
    monkeypatch.setattr(launcher.machx, "stop", lambda: stops.append(True) or True)
    return types.SimpleNamespace(system=lambda *a, **k: None, error=lambda *a, **k: None), stops


@pytest.mark.parametrize("provider", ["anthropic", "codex", "openai"])
async def test_a_crash_in_the_app_reaches_the_caller(crashing_app, provider):
    r, stops = crashing_app
    with pytest.raises(RuntimeError, match="the terminal app crashed"):
        await launcher._run_harness(r, provider=provider, model=None, consolidate_on_exit=False, keep_hot=False)
    assert stops == []                                  # not a local session: nothing to unload


async def test_a_machx_session_unloads_and_still_reports_the_crash(crashing_app):
    r, stops = crashing_app
    with pytest.raises(RuntimeError, match="the terminal app crashed"):
        await launcher._run_harness(r, provider="machx", model=None, consolidate_on_exit=False, keep_hot=False)
    assert stops == [True]
