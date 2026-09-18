"""Observed shell failures reach native callers and the HTTP tool dispatcher."""
from types import SimpleNamespace

import pytest

from dream.core import execution
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.tools import native


@pytest.fixture
def process_result(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "ctx", lambda: SimpleNamespace(workspace=tmp_path))
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("Shell-result tests must not execute a process or probe the sandbox")

    monkeypatch.setattr(execution, "run_owned", forbidden)
    monkeypatch.setattr(execution, "probe_sandbox", forbidden)

    def install(rc, output=b"fixture stdout\nfixture stderr\n", *, contained=True,
                truncated=False, timed_out=False):
        async def execute(command, context, **kwargs):
            calls.append(command)
            return execution.ProcessResult(rc, output, timed_out, truncated), contained

        monkeypatch.setattr(execution, "execute_bash", execute)
        return calls

    return install


@pytest.mark.parametrize("rc", [0, 1, 127, -15])
@pytest.mark.parametrize("contained", [True, False])
@pytest.mark.parametrize("output,truncated", [(b"fixture stdout\nfixture stderr\n", False),
                                                (b"", False), (b"partial output", True)])
async def test_registered_shell_reports_exit_without_losing_diagnostics(
        process_result, rc, contained, output, truncated):
    calls = process_result(rc, output, contained=contained, truncated=truncated)
    result = await native.run_bash.handler({"command": "fixture command"})
    text = result["content"][0]["text"]
    assert bool(result.get("is_error")) is (rc != 0)
    assert f"(exit {rc})" in text
    assert output.decode() in text
    assert ("[...truncated]" in text) is truncated
    assert text.startswith("[explicitly approved uncontained execution]") is (not contained)
    if not output:
        assert "no output" in text
    assert calls == ["fixture command"]


@pytest.mark.parametrize("rc", [0, 127])
async def test_http_dispatch_preserves_native_failure_flag(process_result, rc, monkeypatch):
    calls = process_result(rc)
    provider = SimpleNamespace(key="machx", label="Fixture", base_url="http://unused.invalid/v1",
                               multimodal=False, api_key=lambda: "fixture")
    backend = OpenAICompatBackend(provider=provider, model="fixture", system_prompt="fixture",
                                  tools=[native.run_bash], permission_cb=None)

    def no_model_request(*args, **kwargs):
        pytest.fail("Shell-result tests must not call a model")

    monkeypatch.setattr(backend, "_post_with_retry", no_model_request)
    text, failed = await backend._exec_tool("run_bash", {"command": "fixture command"})
    assert failed is (rc != 0)
    assert f"(exit {rc})" in text
    assert "fixture stdout\nfixture stderr\n" in text
    assert calls == ["fixture command"]


@pytest.mark.parametrize("rc", [0, -15])
async def test_timeout_remains_an_error_with_partial_output(process_result, rc):
    process_result(rc, b"partial output", timed_out=True, truncated=True)
    result = await native.run_bash.handler({"command": "fixture command"})
    assert result["is_error"] is True
    text = result["content"][0]["text"]
    assert "Command timed out" in text
    assert "partial output" in text and "[...truncated]" in text


@pytest.mark.parametrize("rc", [0, 127])
async def test_http_turn_emits_native_shell_failure(process_result, rc):
    from test_schema_deferral import _backend, _FakeClient, _tool_round, _text_round

    calls = process_result(rc)
    backend = _backend([native.run_bash])
    backend._client = _FakeClient([
        _tool_round("run_bash", '{"command":"fixture command"}'),
        _text_round("Observed the command result."),
    ])
    events = [event async for event in backend.ask("Inspect the fixture command result")]
    results = [event.data for event in events if event.kind == "tool_result"]
    assert len(results) == 1
    assert results[0]["is_error"] is (rc != 0)
    assert f"(exit {rc})" in results[0]["content"]
    assert "fixture stdout\nfixture stderr\n" in results[0]["content"]
    assert calls == ["fixture command"]
