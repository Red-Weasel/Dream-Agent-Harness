"""Verbose / error visibility. Tool results are collapsed into a run line that
only ever showed a ~160-char preview, and the full error text was stored nowhere
— so a `⚠ 2 errors` in the footer was un-inspectable. Now: errors always get a
longer preview, `/verbose` opens output up to near-full, and every error is kept
in full for `/errors` to recall.
"""

from __future__ import annotations

from types import SimpleNamespace

from dream.tui.app import App
from dream.tui.render import Renderer


# --- renderer: preview length responds to error-ness and /verbose -------------

def test_error_results_get_a_longer_preview_than_successes():
    r = Renderer()
    big = "x" * 5000
    r.tool_result("t", big, is_error=False)
    ok_len = len(r._run_note)
    r.tool_result("t", big, is_error=True)
    err_len = len(r._run_note)
    assert ok_len < 200            # success stays terse
    assert err_len > ok_len        # an error shows much more
    assert err_len < 1000          # ...but still bounded when not verbose


def test_verbose_opens_output_up():
    r = Renderer()
    big = "y" * 5000
    r.tool_result("t", big, is_error=False)
    assert len(r._run_note) < 200
    r.set_verbose(True)
    r.tool_result("t", big, is_error=False)
    assert len(r._run_note) > 3000  # verbose shows nearly everything


def test_short_content_is_never_padded():
    r = Renderer()
    r.set_verbose(True)
    r.tool_result("t", "brief", is_error=True)
    assert r._run_note == "brief"


# --- app: errors captured in full and recalled --------------------------------

def _bare_app() -> App:
    # __init__ wires renderer + state; a stub engine satisfies _command's top-level
    # `store = self.engine.store` (which /verbose and /errors never actually use).
    app = App(provider="machx", model="m")
    app.engine = SimpleNamespace(store=None)
    return app


def test_error_events_are_captured_in_full():
    app = _bare_app()
    long_err = "Traceback: " + "e" * 3000
    app._render_event(SimpleNamespace(
        kind="tool_result",
        data={"name": "mcp__dream__browse", "content": long_err, "is_error": True}))
    app._render_event(SimpleNamespace(kind="error", data="stream died: HTTP 500"))
    app._render_event(SimpleNamespace(
        kind="result", data={"is_error": True, "subtype": "loop_detected"}))

    assert len(app.session_errors) == 3
    sources = [s for s, _ in app.session_errors]
    assert sources == ["browse", "(turn error)", "(turn ended)"]
    # the full error text is retained — not truncated to a preview
    assert app.session_errors[0][1] == long_err


def test_successful_tool_results_are_not_recorded():
    app = _bare_app()
    app._render_event(SimpleNamespace(
        kind="tool_result", data={"name": "browse", "content": "fine", "is_error": False}))
    assert app.session_errors == []


def test_blank_errors_are_skipped_and_buffer_is_bounded():
    app = _bare_app()
    app._record_error("x", "")      # empty → ignored
    app._record_error("x", None)    # None → ignored
    assert app.session_errors == []
    for i in range(150):
        app._record_error("t", f"err {i}")
    assert len(app.session_errors) == 100          # bounded
    assert app.session_errors[-1] == ("t", "err 149")  # keeps the most recent


def test_verbose_command_toggles_and_syncs_the_renderer():
    app = _bare_app()
    assert app._verbose is False
    # bare toggles
    import asyncio
    asyncio.run(app._command("/verbose"))
    assert app._verbose is True and app.renderer._verbose is True
    asyncio.run(app._command("/verbose off"))
    assert app._verbose is False and app.renderer._verbose is False
    asyncio.run(app._command("/verbose on"))
    assert app._verbose is True


def test_errors_command_runs_clean_when_empty_and_populated():
    app = _bare_app()
    import asyncio
    asyncio.run(app._command("/errors"))  # empty → no crash
    app._record_error("browse", "boom")
    asyncio.run(app._command("/errors"))  # populated → no crash


def test_bare_slash_does_not_crash_the_repl():
    import asyncio
    app = _bare_app()
    assert asyncio.run(app._command("/")) is False      # was: IndexError → session teardown
    assert asyncio.run(app._command("/   ")) is False


def test_reset_session_accounting_clears_errors():
    app = _bare_app()
    app._record_error("browse", "boom")
    app._reset_session_accounting()
    assert app.session_errors == []   # /new must not carry a prior session's errors


def test_footer_counts_only_successful_writes():
    # The footer used to count file activity at tool_use (intent) — showing "+file"
    # for writes that were declined/errored/pending. Now it commits only when the
    # tool_result comes back successful, correlated by tool id.
    app = _bare_app()
    ws = str(app.workspace)

    def use(tid, path):
        app._render_event(SimpleNamespace(
            kind="tool_use",
            data={"name": "write_file", "input": {"path": f"{ws}/{path}"}, "id": tid}))

    def result(tid, name, is_error):
        app._render_event(SimpleNamespace(
            kind="tool_result",
            data={"name": name, "content": "x", "is_error": is_error, "id": tid}))

    use("t1", "ok.txt")        # will succeed
    use("t2", "declined.txt")  # will be declined
    use("t3", "pending.txt")   # no result yet
    # Nothing committed on intent alone:
    assert app.activity == {"created": [], "edited": [], "deleted": []}

    result("t1", "write_file", is_error=False)   # landed
    result("t2", "write_file", is_error=True)    # declined/errored

    assert app.activity["created"] == ["ok.txt"]         # only the real one
    assert "declined.txt" not in app.activity["created"]
    assert [n for _, n in app._recent_files] == ["ok.txt"]
    assert "t3" in app._pending_activity  # still pending, uncommitted
