"""Effective-context inspection at the HTTP transport (DREAM-257, phase 1): one record per attempt at
_stream_uncoordinated and _post_retry, who it was for, the usage its own response reported, and nothing added to
what the provider is sent.

The "server" is an in-process stand-in for the HTTP client: no provider, model or account is called. These are mocks
of the transport, so they say nothing about a live engine's own behaviour."""
import asyncio
import copy
import json
import types

import httpx
import pytest

from dream.core import context_inspection as ci
from dream.core import system_prompt
from dream.core.backends import openai_compat
from dream.core.backends.base import Event
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.tools import context as tool_context
from dream.tools.context import bind_context

pytestmark = pytest.mark.asyncio

# What Dream's request to an OpenAI-compatible server may carry. Nothing of the inspection is among it.
PROVIDER_FIELDS = {"model", "messages", "tools", "tool_choice", "stream", "stream_options", "temperature",
                   "max_tokens", "frequency_penalty", "presence_penalty", "repetition_penalty", "reasoning_effort"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for name in (ci.ENV_TOKENIZER, ci.ENV_TOKENIZER_MODEL, "DREAM_CONTEXT_INSPECTION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ci, "JOURNAL", ci.Journal())
    monkeypatch.setattr(ci, "TOKENIZER", ci.TextTokenizer())
    monkeypatch.setattr(openai_compat, "_RETRY_BACKOFF_S", (0.0, 0.0))


def _session(session_id):
    return types.SimpleNamespace(session_id=session_id, store=None, working=None, workspace=None, runtime_meter=None)


def _sse(chunk):
    return "data: " + json.dumps(chunk, separators=(",", ":"))


USAGE = {"prompt_tokens": 11, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 5}}
LINES = [_sse({"id": "r1", "choices": [{"index": 0, "delta": {"content": "hello"}}]}),
         "", ": keep-alive",
         _sse({"id": "r1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": USAGE}),
         "data: [DONE]"]


def _reply(text="done", usage=None, **extra):
    body = {"id": "w1", "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": text}}], **extra}
    if usage is not None:
        body["usage"] = usage
    return _Response(body=body)


class _Response:
    def __init__(self, status=200, body=None, lines=(), raw=None, fail_at=None, fail_read=None):
        self.status_code, self.headers = status, {}
        self._body, self._lines, self._fail_at, self._fail_read = body, list(lines), fail_at, fail_read
        self.text = raw if raw is not None else json.dumps(body) if body is not None else ""

    def json(self):
        if self._body is None:
            raise ValueError("the body is not JSON")
        return self._body

    async def aread(self):
        if self._fail_read is not None:
            raise self._fail_read
        return self.text.encode()

    def aiter_lines(self):
        async def lines():
            for index, line in enumerate(self._lines):
                if index == self._fail_at:
                    raise httpx.ReadError("connection lost at /private/engine.sock")
                await asyncio.sleep(0)
                yield line
        return lines()


class _Stream:
    def __init__(self, outcome, exit_error=None):
        self.outcome, self.closed, self.exit_error = outcome, False, exit_error

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *exc):
        self.closed = True
        if self.exit_error is not None:
            raise self.exit_error
        return False


class _Client:
    """The two calls the backend makes of its HTTP client, answered from a queue or by a function."""

    def __init__(self, streams=(), posts=(), responder=None):
        self.streams, self.posts, self.responder = list(streams), list(posts), responder
        self.sent, self.opened, self.exit_errors = [], [], []

    def stream(self, method, url, json=None):
        self.sent.append(("stream", json))
        self.opened.append(_Stream(self.streams.pop(0), self.exit_errors.pop(0) if self.exit_errors else None))
        return self.opened[-1]

    async def post(self, url, json=None):
        self.sent.append(("post", json))
        outcome = await self.responder(json) if self.responder is not None else self.posts.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _backend(client, system="You are a fixture.", **extra):
    provider = types.SimpleNamespace(key="fixture-http", label="Fixture", multimodal=False,
                                     base_url="https://fixture.invalid/v1", api_key=lambda: "not-a-key")
    backend = OpenAICompatBackend(provider=provider, model="fixture-model", system_prompt=system, tools=[],
                                  permission_cb=None, **extra)
    backend._client = client
    backend._coordinator = lambda model=None: None       # a remote endpoint: no local lease
    return backend


def _payload(backend, text="hi", **extra):
    return {"model": "fixture-model", "messages": [*backend.messages, {"role": "user", "content": text}],
            "tools": [], "stream": False, "max_tokens": 32, **extra}


def _records(session="sess-A"):
    return [ci.JOURNAL.get(session, row["request_id"]) for row in ci.JOURNAL.list(session)]


# --- the streaming boundary ---------------------------------------------------------------------------------------

async def test_a_stream_attempt_is_recorded_for_its_noted_recipient_and_the_lines_pass_untouched():
    client = _Client(streams=[_Response(lines=LINES)])
    backend = _backend(client)
    payload = _payload(backend, stream=True)
    before = copy.deepcopy(payload)
    backend._inspect_note(payload, "lead", "lead_turn", window=8192, window_basis="fixture")
    got = []
    with bind_context(_session("sess-A")):
        async with backend._stream_uncoordinated(payload) as response:
            async for line in response.aiter_lines():
                got.append(line)
    assert len(got) == len(LINES) and all(a is b for a, b in zip(got, LINES))      # the very same strings
    assert client.sent == [("stream", payload)] and client.sent[0][1] is payload and payload == before
    assert client.opened[0].closed
    (record,) = _records()
    assert (record["recipient"], record["origin"], record["window"], record["window_basis"]) == (
        "lead", "lead_turn", 8192, "fixture")
    assert (record["transport"], record["status"], record["http_status"], record["body"]) == (
        "stream", "response_received", 200, "done_seen")
    assert (record["model"], record["provider"], record["attempt"], record["session"]) == (
        "fixture-model", "fixture-http", 1, "bound")
    usage = record["usage"]
    assert (usage["observed"], usage["observations"]) == (True, 1)
    assert (usage["prompt_tokens"], usage["cached_tokens"], usage["completion_tokens"]) == (11, 5, 2)
    assert [c["kind"] for c in record["components"]] == ["system_section", "message"]
    assert record["components"][0]["source"] == "unknown"        # a plain string system prompt: no source claimed


async def test_a_payload_nobody_noted_is_recorded_as_unknown_not_guessed_to_be_the_leads():
    backend = _backend(_Client(posts=[_reply(usage={"prompt_tokens": 3, "completion_tokens": 1})]))
    with bind_context(_session("sess-A")):
        await backend._post_retry(_payload(backend))
    (record,) = _records()
    assert (record["recipient"], record["origin"], record["window"], record["turn"], record["worker_id"]) == (
        "unknown", "unregistered_caller", None, None, None)
    assert (record["status"], record["body"], record["usage"]["prompt_tokens"]) == ("response_received", "json_object", 3)


async def test_each_retry_is_its_own_attempt_under_one_group_and_a_refusals_body_is_not_kept():
    client = _Client(streams=[_Response(status=503, raw="engine reloading REFUSAL-BODY-TEXT"),
                              _Response(lines=LINES)])
    backend = _backend(client)
    payload = _payload(backend, stream=True)
    backend._inspect_note(payload, "lead", "lead_turn")
    with bind_context(_session("sess-A")):
        async with backend._stream_uncoordinated(payload) as response:
            async for _line in response.aiter_lines():
                pass
    second, first = _records()
    assert len(client.sent) == 2 and all(sent is payload for _, sent in client.sent)
    assert first["group_id"] == second["group_id"] and first["request_id"] != second["request_id"]
    assert (first["attempt"], first["status"], first["http_status"]) == (1, "refused", 503)
    assert (second["attempt"], second["status"], second["http_status"]) == (2, "response_received", 200)
    assert first["usage"]["observed"] is False and first["usage"]["prompt_tokens"] is None     # never the retry's
    assert second["usage"]["prompt_tokens"] == 11
    assert "REFUSAL-BODY-TEXT" not in json.dumps([first, second])


async def test_a_stream_that_dies_mid_body_is_failed_once_closed_and_never_replayed():
    client = _Client(streams=[_Response(lines=LINES, fail_at=3)])
    backend = _backend(client)
    payload = _payload(backend, stream=True)
    got = []
    with bind_context(_session("sess-A")):
        with pytest.raises(httpx.ReadError):
            async with backend._stream_uncoordinated(payload) as response:
                async for line in response.aiter_lines():
                    got.append(line)
    assert got == LINES[:3] and len(client.sent) == 1 and client.opened[0].closed
    (record,) = _records()
    assert (record["status"], record["error_type"], record["http_status"]) == ("failed", "ReadError", 200)
    assert record["usage"]["observed"] is False and record["usage"]["prompt_tokens"] is None
    assert "/private/engine.sock" not in json.dumps(record)


async def test_a_caller_that_stops_reading_and_dreams_own_cut_leave_the_status_as_it_was():
    client = _Client(streams=[_Response(lines=LINES), _Response(lines=LINES)])
    backend = _backend(client)
    with bind_context(_session("sess-A")):
        async with backend._stream_uncoordinated(_payload(backend, "first", stream=True)) as response:
            async for _line in response.aiter_lines():
                break
        reply = openai_compat._StreamedReply()
        with pytest.raises(openai_compat._ControlledStreamCut) as raised:
            async with backend._stream_uncoordinated(_payload(backend, "second", stream=True)) as response:
                async for _line in response.aiter_lines():
                    raise openai_compat._ControlledStreamCut(reply)
    assert raised.value.reply is reply                         # the cut travels on as itself
    assert all(stream.closed for stream in client.opened)
    cut, stopped = _records()
    assert (stopped["status"], stopped["body"]) == ("response_received", "ended_without_done")
    assert (cut["status"], cut["body"], cut["error_type"]) == ("response_received", "cut_by_dream", None)


# --- the non-streaming boundary -----------------------------------------------------------------------------------

async def test_usage_is_only_what_the_response_reported_missing_stays_null_and_bad_values_are_refused():
    bad = {"prompt_tokens": True, "completion_tokens": -3, "prompt_tokens_details": {"cached_tokens": 2.5}}
    backend = _backend(_Client(posts=[_reply(usage={"prompt_tokens": 40, "completion_tokens": 9}),
                                      _reply(), _reply(usage=bad)]))
    with bind_context(_session("sess-A")):
        for text in ("one", "two", "three"):
            await backend._post_retry(_payload(backend, text))
    refused, missing, counted = (r["usage"] for r in _records())
    assert (counted["prompt_tokens"], counted["completion_tokens"], counted["cached_tokens"]) == (40, 9, None)
    assert counted["cached_tokens_reason"] == "not_reported"
    assert missing["observed"] is False and missing["reason"] == "no_usage_reported_by_this_attempt"
    assert [missing[k] for k in ("prompt_tokens", "cached_tokens", "completion_tokens")] == [None, None, None]
    assert [refused[k] for k in ("prompt_tokens", "cached_tokens", "completion_tokens")] == [None, None, None]
    assert {refused[k + "_reason"] for k in ("prompt_tokens", "cached_tokens", "completion_tokens")} == {
        "not_a_non_negative_integer"}


async def test_failed_posts_are_attempts_of_one_group_and_keep_only_the_errors_type():
    client = _Client(posts=[httpx.ReadTimeout("timed out reading /private/path/engine.log")] * 3)
    backend = _backend(client)
    payload = _payload(backend)
    backend._inspect_note(payload, "lead", "lead_recovery")
    with bind_context(_session("sess-A")):
        with pytest.raises(openai_compat._RequestFailed):
            await backend._post_retry(payload)
    records = _records()
    assert sorted(r["attempt"] for r in records) == [1, 2, 3] and len({r["group_id"] for r in records}) == 1
    assert {(r["status"], r["error_type"], r["http_status"]) for r in records} == {("failed", "ReadTimeout", None)}
    assert all(r["usage"]["prompt_tokens"] is None for r in records)
    assert "/private/path" not in json.dumps(records)


async def test_an_error_inside_a_200_and_a_body_that_is_not_json_are_said_truthfully():
    backend = _backend(_Client(posts=[_Response(body={"error": {"message": "generation failed"}}),
                                      _Response(raw="<html>not json</html>")]))
    with bind_context(_session("sess-A")):
        with pytest.raises(openai_compat._ServerGenerationError) as said:
            await backend._post_retry(_payload(backend, "one"))
        with pytest.raises(ValueError):
            await backend._post_retry(_payload(backend, "two"))
    not_json, in_body = _records()
    # a 200 that carries an error is a failed exchange, not a received response (review finding 5)
    assert (in_body["status"], in_body["http_status"], in_body["body"], in_body["error_type"]) == (
        "failed", 200, "error_in_body", type(said.value).__name__)
    assert (not_json["status"], not_json["error_type"], not_json["http_status"]) == ("failed", "ValueError", 200)
    assert "generation failed" not in json.dumps(in_body) and "<html>" not in json.dumps(not_json)


async def test_an_error_chunk_inside_a_200_stream_is_a_failed_attempt_and_the_error_travels_on():
    lines = [_sse({"error": {"message": "generation failed STREAM-ERROR-TEXT"}}), "data: [DONE]"]
    client = _Client(streams=[_Response(lines=lines)])
    backend = _backend(client)
    with bind_context(_session("sess-A")):
        with pytest.raises(openai_compat._ServerGenerationError) as said:
            async with backend._stream_uncoordinated(_payload(backend, stream=True)) as response:
                async for line in response.aiter_lines():
                    chunk = openai_compat._sse_chunk(line)
                    if isinstance(chunk, dict):
                        openai_compat._raise_server_error(chunk)       # what every caller of the stream does
    (record,) = _records()
    assert (record["status"], record["http_status"], record["error_type"]) == (
        "failed", 200, type(said.value).__name__)
    assert client.opened[0].closed and "STREAM-ERROR-TEXT" not in json.dumps(record)


async def test_a_failure_or_a_stop_while_a_refusals_body_is_read_ends_the_record_too():
    """Review finding 5: the non-200 body read was outside every handler, so the record stayed "refused"."""
    lost = httpx.ReadError("connection lost at /private/engine.sock")
    stopped = asyncio.CancelledError()
    client = _Client(streams=[_Response(status=503, fail_read=lost), _Response(status=429, fail_read=stopped)])
    backend = _backend(client)
    with bind_context(_session("sess-A")):
        with pytest.raises(httpx.ReadError) as raised:
            async with backend._stream_uncoordinated(_payload(backend, "one", stream=True)):
                raise AssertionError("a refused stream is never entered")
        assert raised.value is lost                                    # the original error, unchanged
        with pytest.raises(asyncio.CancelledError):                    # a Stop is still a Stop
            async with backend._stream_uncoordinated(_payload(backend, "two", stream=True)):
                raise AssertionError("a refused stream is never entered")
    interrupted, failed = _records()
    assert (failed["status"], failed["http_status"], failed["error_type"]) == ("failed", 503, "ReadError")
    assert (interrupted["status"], interrupted["http_status"], interrupted["error_type"]) == ("interrupted", 429, None)
    # the context closed cleanly both times: no cleanup failure is invented beside the body's
    assert [(r["cleanup_error_type"], r["phase"]) for r in (failed, interrupted)] == [(None, "body")] * 2
    assert all(stream.closed for stream in client.opened) and len(client.sent) == 2      # closed, and not retried
    assert "/private/engine.sock" not in json.dumps([interrupted, failed])


async def test_a_failure_while_the_response_is_closed_is_recorded_and_still_raised():
    """Review finding 5: cleanup that raises after a body that read fine, or on top of a body that did not."""
    broke = RuntimeError("close failed at /private/engine.sock")
    stopped = asyncio.CancelledError()
    after_failure = OSError("close failed too")
    client = _Client(streams=[_Response(lines=LINES), _Response(lines=LINES), _Response(lines=LINES, fail_at=1),
                              _Response(status=503, raw="busy")])
    client.exit_errors = [broke, stopped, after_failure, RuntimeError("close failed after a refusal")]
    backend = _backend(client)

    async def read(text):
        async with backend._stream_uncoordinated(_payload(backend, text, stream=True)) as response:
            return [line async for line in response.aiter_lines()]

    with bind_context(_session("sess-A")):
        with pytest.raises(RuntimeError) as raised:
            await read("one")
        assert raised.value is broke
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await read("two")
        assert cancelled.value is stopped
        with pytest.raises(OSError) as replaced:
            await read("three")
        assert replaced.value is after_failure and isinstance(replaced.value.__context__, httpx.ReadError)
        with pytest.raises(RuntimeError, match="after a refusal"):
            await read("four")
    refused, third, second, first = _records()
    assert all(stream.closed for stream in client.opened)
    # the body was read to [DONE] and usage observed; closing then failed: said, with what was observed kept
    assert (first["status"], first["error_type"], first["cleanup_error_type"], first["phase"], first["body"]) == (
        "failed", "RuntimeError", "RuntimeError", "cleanup", "done_seen")
    assert first["usage"]["prompt_tokens"] == 11
    assert (second["status"], second["cleanup_error_type"], second["error_type"]) == (
        "interrupted", "CancelledError", None)
    # the body had already failed: its own error stays the record's, the cleanup's is beside it
    assert (third["status"], third["error_type"], third["cleanup_error_type"]) == ("failed", "ReadError", "OSError")
    assert (refused["status"], refused["http_status"], refused["error_type"]) == ("failed", 503, "RuntimeError")
    # the refusal's body read fine; it was the close that failed, and the record says it was the close
    assert (refused["cleanup_error_type"], refused["phase"]) == ("RuntimeError", "cleanup")
    assert "/private/engine.sock" not in json.dumps([first, second, third, refused])


async def test_a_refusal_whose_body_read_and_whose_close_both_fail_keeps_the_stop_and_says_both():
    """Final review finding 3: a Stop while a 503's body was read, followed by a close that raised, was recorded as
    failed/OSError in phase body with no cleanup error: the Stop was lost from the record. What propagates is
    unchanged: the close's exception, with the body's as its context."""
    stopped = asyncio.CancelledError()
    lost = httpx.ReadError("connection lost at /private/engine.sock")
    close_one, close_two = OSError("close failed at /private/engine.sock"), OSError("close failed too")
    client = _Client(streams=[_Response(status=503, fail_read=stopped), _Response(status=503, fail_read=lost)])
    client.exit_errors = [close_one, close_two]
    backend = _backend(client)
    payloads = [_payload(backend, "one", stream=True), _payload(backend, "two", stream=True)]
    before = copy.deepcopy(payloads)
    with bind_context(_session("sess-A")):
        with pytest.raises(OSError) as raised:
            async with backend._stream_uncoordinated(payloads[0]):
                raise AssertionError("a refused stream is never entered")
        # exactly what left before this was recorded: the close's error, the Stop it interrupted as its context
        assert raised.value is close_one and isinstance(raised.value.__context__, asyncio.CancelledError)
        with pytest.raises(OSError) as raised:
            async with backend._stream_uncoordinated(payloads[1]):
                raise AssertionError("a refused stream is never entered")
        assert raised.value is close_two and raised.value.__context__ is lost
    assert payloads == before and [sent for _kind, sent in client.sent] == payloads
    assert all(sent is payload for (_kind, sent), payload in zip(client.sent, payloads))
    assert len(client.opened) == 2 and all(stream.closed for stream in client.opened)   # closed once, never retried
    read_failed, interrupted = _records()
    # the Stop is still the record's status; the close's failure is beside it, as the cleanup's own
    assert (interrupted["status"], interrupted["http_status"], interrupted["error_type"]) == ("interrupted", 503, None)
    assert (interrupted["cleanup_error_type"], interrupted["phase"]) == ("OSError", "cleanup")
    # the read's own error stays the record's error; the close's does not replace it
    assert (read_failed["status"], read_failed["http_status"], read_failed["error_type"]) == (
        "failed", 503, "ReadError")
    assert (read_failed["cleanup_error_type"], read_failed["phase"]) == ("OSError", "cleanup")
    listed = ci.JOURNAL.list("sess-A")
    assert [(row["status"], row["cleanup_error_type"]) for row in listed] == [("failed", "OSError"),
                                                                             ("interrupted", "OSError")]
    assert all(r["usage"]["observed"] is False for r in (interrupted, read_failed))
    assert "/private/engine.sock" not in json.dumps([interrupted, read_failed])


async def test_a_stop_while_the_request_is_out_is_interrupted_and_still_cancels():
    async def never(_payload):
        await asyncio.Event().wait()

    backend = _backend(_Client(responder=never))

    async def run():
        with bind_context(_session("sess-A")):
            await backend._post_retry(_payload(backend))

    task = asyncio.ensure_future(run())
    await asyncio.sleep(0.05)
    assert _records()[0]["status"] == "attempted"            # recorded as it left, before any response
    await backend.interrupt()
    with pytest.raises(asyncio.CancelledError):
        await task
    (record,) = _records()
    assert (record["status"], record["phase"], record["http_status"]) == ("interrupted", "before_response", None)


# --- whose request it is ------------------------------------------------------------------------------------------

async def test_the_session_is_the_bound_one_and_never_the_process_wide_fallback(monkeypatch):
    # another session's legacy global context is lying around: a request with nothing bound must not land there
    monkeypatch.setattr(tool_context, "_CTX", _session("sess-GLOBAL"))
    backend = _backend(_Client(responder=lambda payload: _async(_reply(usage={"prompt_tokens": 1}))))
    await backend._post_retry(_payload(backend, "standalone"))
    with bind_context(_session("sess-A")):
        await backend._post_retry(_payload(backend, "for A"))
    with bind_context(_session("sess-B")):
        await backend._post_retry(_payload(backend, "for B"))
    assert ci.JOURNAL.list("sess-GLOBAL") == []
    (a,), (b,) = _records("sess-A"), _records("sess-B")
    assert a["components"][-1]["preview"] == "for A" and b["components"][-1]["preview"] == "for B"
    private = ci.JOURNAL._stores["private"]
    assert list(private) == [backend._inspect_private] and len(private[backend._inspect_private]) == 1
    (standalone,) = private[backend._inspect_private].values()
    assert standalone["session"] == "not_bound"
    assert ci.JOURNAL.list(backend._inspect_private) == []                     # no reader can ask for it
    assert ci.JOURNAL.get(backend._inspect_private, standalone["request_id"]) is None
    assert ci.JOURNAL.get("sess-A", standalone["request_id"]) is None


async def _async(value):
    return value


def _worker_backend(responder, **extra):
    backend = _backend(_Client(responder=responder), **extra)
    backend._subagents = {"explorer": types.SimpleNamespace(tool_names=(), prompt="You explore.",
                                                            description="Explores the workspace.")}
    backend._role_model = lambda name: None          # no routed model
    backend._lane_ctx = lambda: 32_768               # the engine says a lane holds this much
    backend.n_ctx = 100_000                          # the lead's own window is another figure
    return backend


def _answering(counts, delays=None):
    async def answer(payload):
        task = payload["messages"][1]["content"]
        await asyncio.sleep((delays or {}).get(task, 0))
        return _reply("report " + task, usage={"prompt_tokens": counts[task], "completion_tokens": 1})
    return answer


async def test_workers_side_by_side_keep_their_own_identity_window_and_usage():
    # the first worker's reply comes back last: its usage must still be its own
    backend = _worker_backend(_answering({"task-one": 101, "task-two": 202}, {"task-one": 0.05}))

    async def run(task):
        with bind_context(_session("sess-A")):
            return await backend._run_subagent("explorer", task)

    results = await asyncio.gather(run("task-one"), run("task-two"))
    assert results == [("report task-one", False), ("report task-two", False)]
    by_task = {r["components"][-1]["preview"]: r for r in _records()}
    assert set(by_task) == {"task-one", "task-two"}
    one, two = by_task["task-one"], by_task["task-two"]
    assert (one["usage"]["prompt_tokens"], two["usage"]["prompt_tokens"]) == (101, 202)
    for record in (one, two):
        assert (record["recipient"], record["origin"], record["agent"]) == ("worker", "subagent", "explorer")
        assert ci.REQUEST_ID.fullmatch(record["worker_id"])
        # the window this worker was sized to in its own loop: its lane's, not the lead's
        assert (record["window"], record["window_basis"]) == (32_768, "engine_lane_slot_ctx")
        assert record["window"] != backend._window() == 100_000
        assert [c["preview"] for c in record["components"]][0] == "You explore."
        # the role's prompt is labelled where the adapter assembles it (review finding 13), not left unknown
        assert (record["components"][0]["source"], record["components"][0]["detail"]) == (
            "subagent_role_prompt", {"agent": "explorer"})
    # and what was sent is the same text, in the same JSON, as the plain string would have been
    for _kind, sent in backend._client.sent:
        assert sent["messages"][0] == {"role": "system", "content": "You explore."}
        assert json.dumps(sent["messages"][0]) == json.dumps({"role": "system", "content": "You explore."})
    assert one["worker_id"] != two["worker_id"] and one["group_id"] != two["group_id"]
    assert backend._inspect_notes == {}                              # a note lives only while its request is out
    for _kind, sent in backend._client.sent:
        assert set(sent) <= PROVIDER_FIELDS


async def test_workers_of_two_sessions_never_meet_in_one_journal():
    first = _worker_backend(_answering({"task-one": 111}, {"task-one": 0.03}))
    second = _worker_backend(_answering({"task-two": 222}))

    async def run(backend, session, task):
        with bind_context(_session(session)):
            return await backend._run_subagent("explorer", task)

    await asyncio.gather(run(first, "sess-A", "task-one"), run(second, "sess-B", "task-two"))
    (a,), (b,) = _records("sess-A"), _records("sess-B")
    assert (a["usage"]["prompt_tokens"], a["components"][-1]["preview"]) == (111, "task-one")
    assert (b["usage"]["prompt_tokens"], b["components"][-1]["preview"]) == (222, "task-two")
    assert ci.JOURNAL.get("sess-A", b["request_id"]) is None and ci.JOURNAL.get("sess-B", a["request_id"]) is None


async def test_a_refused_stream_and_its_plain_fallback_are_two_attempts_of_one_worker_request():
    backend = _worker_backend(_answering({"task-one": 77}), worker_streaming=True)
    backend._client.streams = [_Response(status=400, raw='{"error":{"message":"REFUSAL-BODY-TEXT"}}')]
    with bind_context(_session("sess-A")):
        result = await backend._run_subagent("explorer", "task-one")
    assert result == ("report task-one", False) and backend.worker_streaming is False
    fallback, streamed = _records()
    assert streamed["group_id"] == fallback["group_id"] and streamed["worker_id"] == fallback["worker_id"]
    assert (streamed["attempt"], streamed["transport"], streamed["status"], streamed["http_status"]) == (
        1, "stream", "refused", 400)
    assert (fallback["attempt"], fallback["transport"], fallback["status"]) == (2, "post", "response_received")
    assert streamed["usage"]["prompt_tokens"] is None and fallback["usage"]["prompt_tokens"] == 77
    assert "REFUSAL-BODY-TEXT" not in json.dumps([fallback, streamed])
    (kind_one, first_sent), (kind_two, second_sent) = backend._client.sent
    assert (kind_one, kind_two) == ("stream", "post") and first_sent["stream"] is True
    assert set(first_sent) <= PROVIDER_FIELDS and set(second_sent) <= PROVIDER_FIELDS
    assert backend._inspect_notes == {}


LEAD_LINES = [_sse({"id": "r1", "choices": [{"index": 0, "delta": {"content": "hello"}}]}),
              _sse({"id": "r1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": USAGE}),
              "data: [DONE]"]


async def test_a_lead_turn_is_recorded_as_the_leads_with_its_prompts_sources():
    client = _Client(streams=[_Response(lines=LEAD_LINES)])
    backend = _backend(client, system=system_prompt.sourced("You are a fixture.", "base"))
    backend.n_ctx = 50_000
    with bind_context(_session("sess-A")):
        events = [event async for event in backend.ask("hello there")]
    assert any(event.kind == "result" for event in events)
    (record,) = _records()
    assert (record["recipient"], record["origin"], record["turn"], record["transport"]) == (
        "lead", "lead_turn", 1, "stream")
    assert (record["window"], record["window_basis"]) == (backend._window(), "backend_or_reported_context")
    assert record["usage"]["prompt_tokens"] == 11 and record["usage"]["cached_tokens"] == 5
    head, user = record["components"]
    assert (head["kind"], head["source"], head["preview"]) == ("system_section", "base", "You are a fixture.")
    assert user["preview"].startswith("hello there")
    (_kind, sent), = client.sent
    assert not [key for key in sent if "inspect" in key or "recipient" in key] and "worker_id" not in sent
    assert backend._inspect_notes == {}


async def test_a_backend_built_for_an_advisor_records_an_advisor_and_no_session_turn():
    client = _Client(streams=[_Response(lines=LEAD_LINES)])
    backend = _backend(client, system=system_prompt.sourced("You advise.", "advisor_persona"))
    backend.inspect_role = ("advisor", "council_http_consultation")
    with bind_context(_session("sess-A")):
        [event async for event in backend.ask("what do you think?")]
    (record,) = _records()
    assert (record["recipient"], record["origin"], record["turn"]) == ("advisor", "council_http_consultation", None)
    assert record["components"][0]["source"] == "advisor_persona"


async def test_the_http_advisor_is_built_as_an_advisor(monkeypatch):
    from dream.core import moe
    made = []

    class Advisor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            made.append(self)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def ask(self, prompt):
            yield Event("assistant_done", "an answer")

    monkeypatch.setattr(moe, "OpenAICompatBackend", Advisor)
    monkeypatch.setattr(moe, "resolve_profile", lambda provider, model=None: None)
    monkeypatch.setattr(moe, "_context_meter", lambda provider, meter=None: None)
    provider = types.SimpleNamespace(key="fixture-http", label="Fixture", default_model="fixture-model", kind="openai")
    assert await moe._consult_openai(provider, "a question") == "an answer"
    assert made[0].inspect_role == ("advisor", "council_http_consultation")
    assert "inspect_role" not in made[0].kwargs                  # said beside the backend, not through its constructor
    # the built-in persona is labelled at construction (review finding 13): the same characters, a known source
    persona = made[0].kwargs["system_prompt"]
    assert persona == moe.ADVISOR_SYSTEM and json.dumps(persona) == json.dumps(moe.ADVISOR_SYSTEM)
    assert ci.spans_of(persona) == [(0, len(moe.ADVISOR_SYSTEM), "advisor_persona", {"origin": "dream_builtin"})]
    # a persona a caller supplies in its place is said to be the caller's, not Dream's
    assert await moe._consult_openai(provider, "a question", system="Be brief.") == "an answer"
    supplied = made[1].kwargs["system_prompt"]
    assert supplied == "Be brief." and ci.spans_of(supplied)[0][2:] == ("caller_supplied_system",
                                                                      {"origin": "consult_advisor"})
