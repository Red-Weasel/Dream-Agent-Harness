"""How much a single local generation is allowed to produce.

The cap exists so a looping model self-terminates, and at 32768 it was set
without reference to the window it runs in: on a 250k-context session that is
13% of the window, and a single `write_file` of a full HTML page — inline CSS
and JS — exceeds it. The turn then dies mid-tool-call, having written nothing,
which is the exact failure the cap was meant to prevent, dressed as a limit.

A runaway is already caught by the loop guard and the per-prompt tool budget.
So the ceiling scales with the window the model actually has, and the flat
default is only the floor for a session whose window is unknown.
"""

from __future__ import annotations

from types import SimpleNamespace

import dream.config as config
from dream.core.backends.openai_compat import _CTX_MARGIN, OpenAICompatBackend


def _backend(n_ctx=None):
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1",
                        multimodal=False, api_key=lambda: "n")
    b = OpenAICompatBackend(provider=p, model="m", system_prompt="s",
                            tools=[], permission_cb=None)
    b.n_ctx = n_ctx
    return b


def test_the_default_ceiling_is_not_a_pinhole():
    """130 KB was not enough to write one web page."""
    assert config.MAX_OUTPUT_TOKENS >= 131072


def test_a_big_window_gets_a_proportionally_big_allowance():
    """The whole complaint: a 250k-context session must be allowed to write a
    large file in one call, not be held to a fraction of its own window."""
    b = _backend(n_ctx=250_000)
    assert b._max_tokens(fill=5_000) >= 60_000


def test_the_allowance_never_exceeds_what_the_window_can_hold():
    b = _backend(n_ctx=8192)
    got = b._max_tokens(fill=6000)
    assert got <= 8192 - 6000 - _CTX_MARGIN + 1
    assert got >= 1024  # ...but never below a usable reply


def test_a_nearly_full_window_still_leaves_a_usable_floor():
    b = _backend(n_ctx=8192)
    assert b._max_tokens(fill=8000) == 1024


def test_an_unknown_window_falls_back_to_the_configured_ceiling():
    b = _backend(n_ctx=None)
    assert b._max_tokens(fill=0) == config.MAX_OUTPUT_TOKENS


def test_the_env_override_still_wins(monkeypatch):
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 4096)
    b = _backend(n_ctx=250_000)
    assert b._max_tokens(fill=0) == 4096


def test_the_truncation_message_names_the_knob_and_the_value():
    """When a call IS cut off, the message has to be actionable — the variable
    to set and what it is set to now."""
    import inspect

    from dream.core.backends import openai_compat
    src = inspect.getsource(openai_compat)
    i = src.index("A tool call was cut off at")
    window = src[i - 700:i + 300]
    assert "DREAM_MAX_TOKENS" in window
    assert "{cap:,}" in window  # the ACTUAL number in force, not a bare name
    assert "/new" in window     # and the way out when the window is the limit


# --- changing it must not cost you the session -------------------------------


def test_the_ceiling_is_read_live_so_a_command_can_change_it_mid_session():
    """The complaint behind /maxtokens: hitting the ceiling mid-build must not
    force a restart, because a restart costs the context you hit it with.
    _max_tokens must consult config at request time, not cache it."""
    b = _backend(n_ctx=None)
    before = b._max_tokens(fill=0)
    orig = config.MAX_OUTPUT_TOKENS
    try:
        config.MAX_OUTPUT_TOKENS = 999_999
        assert b._max_tokens(fill=0) == 999_999   # same object, no reconnect
        assert b._max_tokens(fill=0) != before
    finally:
        config.MAX_OUTPUT_TOKENS = orig


def test_the_maxtokens_command_exists_and_is_documented():
    from pathlib import Path
    app = (Path(__file__).parent.parent / "dream" / "tui" / "app.py").read_text()
    assert 'cmd == "maxtokens"' in app
    assert "/maxtokens" in app          # listed in the help banner
    assert "config.MAX_OUTPUT_TOKENS = n" in app   # sets the live value
