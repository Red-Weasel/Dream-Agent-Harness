"""OpenAI-compatible backend — drives MachX, OpenAI, or Grok.

There's no Agent SDK here, so this owns the agent loop itself: call
``/chat/completions`` with the tool schemas, stream text, collect any tool calls,
execute them against Dream's own tool handlers, feed the results back, and repeat until
the model stops calling tools. Tools are Dream's in-process ``@tool`` objects, executed
directly via their ``.handler`` — the same tools the Claude backend uses.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import statistics
import sys
import time
import uuid
from collections import deque
from contextvars import ContextVar
from contextlib import aclosing, asynccontextmanager, nullcontext
from copy import copy, deepcopy
from typing import Any, AsyncIterator, Callable, Iterable, NamedTuple
from urllib.parse import urlsplit

import httpx
from anyio import CancelScope, current_effective_deadline, current_time, fail_after
from anyio.lowlevel import checkpoint_if_cancelled

from ... import config
from ...telemetry.runtime import RunLimit
from .. import handoff, policy, tool_budget_schemas, turn_origin
from ..providers import Provider
from ..profiles import RuntimeProfile
from ..context_budget import IMAGE_TOKENS, ContextOverflow, admit, account, estimate_parts
from .base import Backend, Event, PermissionCallback, content_to_text

_LOG = logging.getLogger(__name__)
_AGENT_ACTIVITY = ContextVar("dream_agent_activity", default=None)
_REQUEST_INTERRUPTION = ContextVar("dream_request_interruption", default=None)
_IO_OPERATION = ContextVar("dream_io_operation", default=None)


async def _work_checkpoint():
    """Cleanup may finish after cancellation; subsequent work may not start."""
    await checkpoint_if_cancelled()
    # A buffered/synchronous completion can beat a scheduled deadline callback.
    if current_effective_deadline() <= current_time():
        raise TimeoutError("Work deadline expired.")


class _InterruptState:
    def __init__(self):
        self.generation = 0
        self.active: set[CancelScope] = set()


class _InterruptibleBytes(httpx.AsyncByteStream):
    """Interrupt raw reads; HTTPX's automatic EOF close runs outside their scope."""
    def __init__(self, stream, backend, generation):
        self.stream, self.backend, self.generation = stream, backend, generation

    async def __aiter__(self):
        iterator = self.stream.__aiter__()
        try:
            while True:
                try:
                    part = await self.backend._interruptible_io(
                        lambda: anext(iterator), generation=self.generation)
                except StopAsyncIteration:
                    break
                yield part
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                with CancelScope(shield=True):
                    await close()

    async def aclose(self):
        # HTTPcore shields error cleanup itself, but its normal EOF close can
        # run under a delegate deadline or sibling scope. Both must settle.
        with CancelScope(shield=True):
            await self.stream.aclose()


class _InterruptibleResponse:
    """Keep line reads and their closure in the request's owning task."""
    def __init__(self, response, backend, generation):
        self.response = response
        self.backend, self.generation = backend, generation
        self.lines = response.aiter_lines().__aiter__()

    def __getattr__(self, name):
        return getattr(self.response, name)

    def aiter_lines(self):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if isinstance(getattr(self.response, "stream", None), _InterruptibleBytes):
            line = await anext(self.lines)
            self.backend._check_interruption(self.generation)
            return line
        return await self.backend._interruptible_io(
            lambda: anext(self.lines), generation=self.generation)

    async def close_lines(self):
        close = getattr(self.lines, "aclose", None)
        if close is not None:
            await close()

_MAX_TOOL_ROUNDS = config.MAX_TOOL_ROUNDS
# Fix #85 (1), DREAM-117: how many times in one turn Dream continues a reply that was cut at the
# output ceiling with no completed tool call, before the turn ends as incomplete. Live 2026-09-24 the
# first cut ended the turn and the owner typed "continue" by hand, twice (49 + 22 minutes); two
# automatic continuations cover that without letting a reply that never fits spend a whole turn.
_LENGTH_CONTINUATIONS = 2
# A dispatched subagent runs its own bounded tool loop; the cap keeps a confused
# subagent from grinding the (shared, single-flight) engine indefinitely. High by
# default (config) so real research completes.
_SUB_MAX_ROUNDS = config.SUBAGENT_MAX_ROUNDS

# Loop guard. A small local model that hits a dead-end tool result retries the
# identical call, gets the identical result, and every round adds another
# identical (assistant, tool-result) pair to the context — in-context repetition
# is self-reinforcing, so within a few rounds the model locks into a verbatim
# spiral. Sampling penalties can't break it (they never see the prompt history),
# and _MAX_TOOL_ROUNDS alone would execute 25 identical calls first. The guard
# keys on identical *results*, not just identical calls, so stateful re-checks
# (`git status` after an edit) never trip it.
_LOOP_GUARD_WARN = (
    "\n\n[loop guard] You already made this exact call this turn and the result "
    "is identical. Use the evidence already obtained to take the next task step. "
    "Change approach to resolve a specific missing fact; cosmetic argument changes "
    "do not produce new evidence."
)
_LOOP_GUARD_BLOCK = (
    "[loop guard] Not executed: this exact call has already returned identical "
    "results twice this turn. Use the existing result, inspect a specific missing "
    "fact with an appropriate tool, or explain the blocker. Do not vary arguments "
    "just to repeat the same observation."
)
_LOOP_GUARD_BREAK = (
    "[loop guard] Repeated again after being blocked — ending this turn to break "
    "the repetition loop."
)
_OBSERVATION_GUIDANCE = (
    "\n\n[progress check] At least three reads of the same target returned identical "
    "text this turn, including calls with different arguments. The calls returned text; "
    "this is repeated evidence, not proof that the task is stalled. Use the evidence "
    "already obtained to take the next task step. If information is missing, identify "
    "the exact missing section or failure and choose a suitable inspection. Changing "
    "paging arguments alone has not produced new text. Do not claim visual inspection "
    "from metadata or repeat an uncertain action."
)
_FAILURE_GUIDANCE = (
    "\n\n[failure recovery] This tool returned the same failure at least three "
    "times in a row, including calls with different arguments. This is not proof "
    "of the root cause. Inspect the reported prerequisite or a specific missing "
    "fact before retrying; changing arguments alone has not resolved the failure. "
    "For missing files, compare the selected workspace and execution boundary; "
    "for missing tools or libraries, check availability in that same boundary. "
    "Do not bypass permissions, rerun an uncertain mutation, or claim success. "
    "If no authorized repair is available, report the concrete blocker."
)

# Context management. There is no SDK here to do it, so the backend owns it.
#
# A tool result appended to the history is clamped: one read_file/browse can
# return 200k chars (~50k tokens) and the model re-reads it on EVERY later round.
# The Event yielded to the TUI keeps the full text — only the history copy is cut.
_TOOL_RESULT_CAP = config.TOOL_RESULT_CAP
# Ceiling on a tool NAME in the history — the one field neither the result cap
# nor elision can shrink. Generous next to any real tool name.
_TOOL_NAME_CAP = 128
# Fraction of the window at which the history gets compacted in place. Past this
# the server either 400s (and since the history never shrinks on its own, every
# later turn 400s identically) or silently evicts the system prompt.
_COMPACT_AT = float(os.environ["DREAM_COMPACT_AT"]) if os.environ.get("DREAM_COMPACT_AT") else None


def compact_at(window: int) -> float:
    """The fill fraction that triggers compaction. DREAM_COMPACT_AT wins when set;
    otherwise 0.75, or 0.85 on a window of 64k+ (2026-09-21: two compactions in 40 min
    at 75k while 19k of the window never got used, each costing 45-73 s of re-prefill)."""
    if _COMPACT_AT is not None:
        return _COMPACT_AT
    return 0.85 if window >= 65536 else 0.75

# The salvage reply's ceiling. A summary of work already done needs room to be
# useful and no more; this is not the place to write a new document.
_SALVAGE_MAX_TOKENS = int(os.environ.get("DREAM_SALVAGE_MAX_TOKENS", "4096"))
# Window to assume when the server won't say (OpenAI/xAI, or a failed /props
# probe) — the net has to exist even when n_ctx is unknown.
_ASSUMED_CTX = int(os.environ.get("DREAM_ASSUMED_CTX", "32768"))
# Ceiling on how many trailing messages compaction leaves verbatim (the model's
# immediate working state). It is only a ceiling: the real keep set is whatever
# fits the token budget, because a fixed count is a floor compaction can never
# get under — eight capped tool results are ~48k tokens on their own, more than
# a typical local window.
_KEEP_RECENT_MSGS = 8
# Headroom between the prompt and the window when clamping max_tokens.
_CTX_MARGIN = 512
_ELIDED = "[elided:"
# A tool_call whose result never landed (see _repair_dangling).
_INTERRUPTED = "(interrupted — no result recorded)"

# Bounded retry for the HTTP hop. MachX reloading a model, a 429, a reset
# mid-handshake — one blip used to discard a whole in-progress agentic turn.
# Only ever applied BEFORE any stream bytes are consumed: that point is
# idempotent (nothing yielded, `messages` not yet mutated), after it a retry
# would replay output the consumer already saw.
# 3 attempts means 2 waits — the table has one entry per WAIT, not per attempt.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = (0.5, 2.0)
# A server is free to send Retry-After: 3600; honoring that verbatim would look
# like a hang. Honor it, but never past this.
_RETRY_MAX_WAIT_S = 30.0

# Fixes #72 and #34: how long a request waits for a cut-off request's lease to settle -- for the
# local engine to report nothing in flight -- before it is refused. The lease's own wait for a
# LIVE Dream request (120 s, inference_coordination) is separate and unchanged.
_LEASE_WAIT_DEFAULT_S = 20.0
_LEASE_WAIT_MAX_S = 600.0
_LEASE_POLL_S = 0.25


def _lease_wait_s(raw: str | None) -> float:
    """DREAM_LEASE_WAIT_S, checked like engine_guard's grace (the DREAM-110 gate): "nan" or "inf" would
    unbound the wait, a negative value switch it off, and a word stop this module from importing, so
    Dream would not start. Seconds from 0 to 600 are taken; anything else is the default, said once."""
    if raw is None:
        return _LEASE_WAIT_DEFAULT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if math.isfinite(value) and 0 <= value <= _LEASE_WAIT_MAX_S:
        return value
    _LOG.warning("DREAM_LEASE_WAIT_S must be a number of seconds from 0 to %g; using the default %g s.",
                 _LEASE_WAIT_MAX_S, _LEASE_WAIT_DEFAULT_S)
    return _LEASE_WAIT_DEFAULT_S


_LEASE_WAIT_S = _lease_wait_s(os.environ.get("DREAM_LEASE_WAIT_S"))


def _stream_failure(label: str, exc: Exception) -> str:
    """Say what actually happened when a stream dies.

    A local server that crashes mid-response closes the socket without ending the
    chunked body, and httpx reports RemoteProtocolError. Shown raw that reads like
    a network glitch; the real event is that the engine died — on Arc, usually
    `UR_RESULT_ERROR_DEVICE_LOST` in the engine log. Point at the log rather than
    at the symptom.
    """
    name = type(exc).__name__
    if isinstance(exc, httpx.ReadTimeout):
        return (f"{label} stream read timed out (ReadTimeout). No data arrived within the configured read timeout. "
                "The server may still be generating a buffered tool call. Check its state in Controls before retrying; "
                "the incomplete request was not replayed.")
    if isinstance(exc, httpx.RemoteProtocolError) or "incomplete chunked read" in str(exc):
        hint = ""
        try:
            from ...local import machx

            if not machx.is_serving():
                hint = (
                    f" The server is no longer running — it died mid-response."
                    f" Check {machx._log_file()} (a SYCL/level_zero DEVICE_LOST there"
                    f" means the GPU dropped, not a Dream fault)."
                )
            else:
                hint = " The server is still up, so this was a dropped response rather than a crash."
        except Exception:
            hint = " Check the engine log; the server likely died mid-response."
        return f"{label} stream ended early ({name}).{hint}"
    return f"{label} request failed: {name}: {exc}"

class _RequestFailed(Exception):
    """An HTTP failure that survived every retry. Carries the message to show and, for the
    runtime log, a short failure code plus the server's own message when it sent one (fix #64)."""

    def __init__(self, text: str = "", *, code: str | None = None, message: str | None = None):
        super().__init__(text)
        self.code = code
        self.message = message


class _ServerGenerationError(_RequestFailed):
    """An explicit model error, including one delivered inside an HTTP 200 SSE."""

    def __init__(self, error: Any):
        fields = error if isinstance(error, dict) else {}
        code = str(fields.get("code") or fields.get("type") or "server_error")
        message = str(fields.get("message") or (str(error) if error else "generation failed"))[:1000]
        super().__init__(f"{code}: {message}", code=code, message=message)
        self.subtype = ("context_overflow" if "context" in self.code.lower()
                        else "server_error")


def _http_error_fields(status: int, body: str) -> tuple[str, str]:
    """The code and message of an HTTP error response (fix #64): the server's own, from the OpenAI
    error shape the engine sends (error_json), else ``http_<status>`` and the body's text."""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return (str(error.get("code") or error.get("type") or f"http_{status}")[:80],
                str(error["message"])[:1000])
    if isinstance(error, str) and error.strip():
        return f"http_{status}", error.strip()[:1000]
    return f"http_{status}", body.strip()[:1000]


def _uncertain_local_http(status: int, code: str, message: str):
    """A local 408/5xx leaves the outcome unknown, so the lease stays fenced; what the server said is
    kept and shown (fix #64: the engine's 503 'engine unavailable: device lost' used to vanish)."""
    from ..inference_coordination import CoordinationError
    text = f"Local HTTP {status}: upstream outcome is uncertain. Check server idle in Controls."
    if message:
        text += f" The server said ({code}): {message}"
    error = CoordinationError(text)
    error.code, error.message = code, message or None
    return error


class _EngineReport(NamedTuple):
    """What the local server's /health said about work in flight (fixes #72, #34)."""
    kind: str                  # idle | busy | unhealthy | unreachable | unreported | unchecked
    said: str                  # a clause after "the local engine at host:port"
    inflight: int | None = None
    queued: int | None = None


def _lease_owner_alive(record: dict) -> bool:
    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lease_unsettled(record: dict) -> bool:
    """A record no live Dream request stands behind: a cut-off request's, or a gone owner's."""
    return record.get("state") == "uncertain" or (
        record.get("state") == "running" and not _lease_owner_alive(record))


def _lease_holder(record: dict | None) -> str:
    """'1a2b3c4d (Dream pid 4242, started 13:19:02)' -- which request holds the lease."""
    if not record or not isinstance(record.get("request_id"), str):
        return "(unknown request)"
    about = []
    if type(record.get("pid")) is int:
        about.append(f"Dream pid {record['pid']}")
    if isinstance(record.get("started_at"), (int, float)):
        about.append("started " + time.strftime("%H:%M:%S", time.localtime(record["started_at"])))
    return record["request_id"][:8] + (f" ({', '.join(about)})" if about else "")


def _raise_server_error(data: Any) -> None:
    if isinstance(data, dict) and data.get("error") is not None:
        raise _ServerGenerationError(data["error"])


class _ExecutedToolResult(str):
    """A handler was attempted; even a failed handler may have changed files."""


class _LoopGuardResult(str):
    """A repeated request was suppressed; it is not a new delivery attempt."""


class _VisualResult(_ExecutedToolResult):
    def __new__(cls, text: str, images: list | None = None):
        instance = super().__new__(cls, text)
        instance.images = images or []
        return instance


def _visual_message(images: list) -> dict:
    return {"role": "user", "name": "dream_visual_evidence", "content": [
        {"type": "text", "text": "Visual evidence returned by the preceding tools; not a new user instruction."},
        *images]}


def _is_retryable(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _retry_after(resp: Any) -> float | None:
    """Seconds from a Retry-After header. Only the delta-seconds form — the
    HTTP-date form is rare on these APIs and the backoff table covers it."""
    try:
        raw = (getattr(resp, "headers", None) or {}).get("retry-after")
        return float(raw) if raw else None
    except (AttributeError, TypeError, ValueError):
        return None


def _retry_delay(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return max(0.0, min(retry_after, _RETRY_MAX_WAIT_S))
    return _RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S) - 1)]


def _parse_tool_args(raw: str | None) -> tuple[dict[str, Any], str | None]:
    """Tool-call arguments, plus a message if they could not be parsed.

    Silently substituting {} for malformed JSON is worse than it looks. The tool then
    reports a MISSING argument ("run_bash needs a 'command'"), which is a true
    statement about a false situation: the model did send one, its JSON just didn't
    close. Reading that, the model's obvious move is to send the same call again — and
    a local model truncating its own JSON will truncate it identically. That is a
    spiral built out of a helpful-sounding error.

    So say what actually happened, and show the model its own output back. Seeing the
    broken text is what makes the next attempt different from the last one.
    """
    if raw is None or raw == "":
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return {}, (
            f"Your tool-call arguments were not valid JSON and could not be used "
            f"({e.msg} at position {e.pos}). Nothing ran. This is what you sent:\n"
            f"{_clamp(raw, 600)}\n"
            f"Re-send the call with complete, valid JSON. If it was cut off "
            f"mid-object, the arguments were too long — pass less in one call."
        )
    if not isinstance(parsed, dict):
        return {}, (
            f"Tool-call arguments must be a JSON object, but yours parsed as "
            f"{type(parsed).__name__}. Nothing ran. This is what you sent:\n"
            f"{_clamp(raw, 600)}"
        )
    return parsed, None


def _clamp(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return f"{text[:cap]}\n\n[... {len(text) - cap} chars elided]"


def _stub_args(args: str, head: int = 0) -> str:
    """A tool call's arguments with every long value cut and every short one
    (a path, a name, a flag) kept, so a stubbed write_file still says WHICH file
    it wrote. Without that, compaction left `{"_elided_chars": N}` and the model
    wrote finished files again. `head` keeps the start of each cut value."""
    try:
        obj = json.loads(args)
    except (TypeError, ValueError):
        obj = None
    if not isinstance(obj, dict):
        return json.dumps({"_elided_chars": len(args), **({"_head": args[:head]} if head else {})})
    out: dict[str, Any] = {"_elided_chars": len(args)}
    for key, value in obj.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if len(text) <= 200:
            out[key] = value
        else:
            out[key] = (text[:head] + "…" if head else "") + f"[{len(text):,} chars elided]"
    return json.dumps(out, ensure_ascii=False)


def _clamp_args(args: str) -> str:
    """Cap a tool call's arguments for the history. Stays valid JSON: a strict
    server may parse what it is handed back, so the cut form is an object, not
    a truncated fragment."""
    if len(args) <= _TOOL_RESULT_CAP:
        return args
    return _stub_args(args, head=400)


def _elide_args(msg: dict[str, Any]) -> int:
    """Stub the arguments of an old assistant message's tool_calls. Compaction
    could only ever rewrite `content`, so a history of big write_file calls was
    un-shrinkable however hard it compacted."""
    n = 0
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or ""
        stub = _stub_args(args)
        if len(args) > len(stub):
            fn["arguments"] = stub
            n += 1
    return n


def _msg_parts(m: dict[str, Any]) -> tuple[int, int]:
    """(text chars, images) of one message. _msg_chars prices each image at context_budget's flat
    IMAGE_TOKENS; the calibrated size prices it from what the server counted (Dream fix #65)."""
    content = m.get("content") or ""
    if isinstance(content, list):
        text, images = estimate_parts(content)
        n = text * 4
    else:
        n, images = len(content), 0
    n += len(m.get("reasoning_content") or "")
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") or {}
        n += len(fn.get("name") or "") + len(fn.get("arguments") or "")
    return n, images


def _msg_chars(m: dict[str, Any]) -> int:
    chars, images = _msg_parts(m)
    return chars + images * IMAGE_TOKENS * 4


def _est_tokens(messages: list[dict[str, Any]]) -> int:
    """chars/4 — crude, but it only has to answer "how close to the wall"."""
    return sum(_msg_chars(m) for m in messages) // 4


def _text_and_images(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """(estimated text tokens, images) of a history: _est_tokens with the images counted apart."""
    chars = images = 0
    for m in messages:
        c, n = _msg_parts(m)
        chars, images = chars + c, images + n
    return chars // 4, images


def _recent_keep(messages: list[dict[str, Any]], target: int,
                 msg_size: Callable[[dict[str, Any]], int] | None = None) -> set[int]:
    """Trailing messages small enough to leave verbatim, newest first.

    Budget-aware on purpose: a fixed count would be a floor compaction can never
    get under, so a window smaller than the last few tool results could never be
    brought back inside it. Recent state may claim at most half the target; the
    newest message is always kept (it is the turn the model is answering)."""
    keep: set[int] = set()
    if len(messages) < 2:
        return keep
    budget = max(target // 2, 1)
    used = 0
    for i in range(len(messages) - 1, 0, -1):
        if len(keep) >= _KEEP_RECENT_MSGS:
            break
        cost = msg_size(messages[i]) if msg_size else _msg_chars(messages[i]) // 4
        if used + cost > budget:
            break
        keep.add(i)
        used += cost
    keep.add(len(messages) - 1)
    return keep


def _elide_pass(
    messages: list[dict[str, Any]], target: int, keep: set[int], names: dict[Any, str],
    on_elide: Any = None, size: Callable[[list[dict[str, Any]]], int] = _est_tokens,
) -> int:
    """Stub message bodies outside `keep` until `size` of the history fits `target`.

    `on_elide(kind, body)` is told each body before it goes; it returns the id
    of the working note it saved the content to, or None when it saved nothing
    (the cap, or no store). The stub names that id, so the content stays one
    `read_notes` away instead of gone."""
    elided = 0
    # Tool results are the bulk, then the arguments of the calls that produced
    # them (a write_file body lives THERE, not in any content field), then older
    # user/assistant prose.
    for roles in (("tool",), ("assistant",), ("assistant", "user")):
        args_pass = roles == ("assistant",)
        for i, m in enumerate(messages):
            if size(messages) <= target:
                return elided
            if i in keep or m.get("role") not in roles:
                continue
            if args_pass:
                elided += _elide_args(m)
                continue
            body = m.get("content") or ""
            if isinstance(body, list):
                if any(p.get("type") == "image_url" for p in body if isinstance(p, dict)):
                    m["content"] = "[elided: visual evidence; reopen the source image if needed]"
                    elided += 1
                    continue
                body = content_to_text(body)
            if body.startswith(_ELIDED):
                continue
            what = (f"{names.get(m.get('tool_call_id'), 'tool')} result"
                    if m["role"] == "tool" else f"{m['role']} message")
            stub = f"{_ELIDED} {what}, {len(body)} chars"
            if len(stub) + 40 >= len(body):
                continue  # too small to be worth a stub, whatever the note
            saved, why = on_elide(what, body, _turn_of(messages, i)) if on_elide is not None else (None, "")
            if saved is not None:
                # Short on purpose: a stub is never shrunk again, so its own size
                # is permanent history. `read_notes` explains the '#id' form.
                stub += f" — note #{saved}]"
            elif on_elide is not None:
                stub += f" — not saved ({why})]"
            else:
                stub += "]"
            # The id tag is what `snip` names; a stub without it is unreachable.
            tag = _MSG_ID_RE.search(body) if m["role"] == "user" else None
            if tag:
                stub += f"\n\n[id:{tag.group(1)}]"
            m["content"] = stub
            elided += 1
    return elided


_MSG_ID_RE = re.compile(r"\[id:(m\d{4})\]\s*$")


def _turn_of(messages: list[dict[str, Any]], i: int) -> str:
    """The id of the user message this one answers — the turn the content came
    from, not the turn compaction happens to run in (Gate 10)."""
    for rng in (range(i, -1, -1), range(i + 1, len(messages))):
        for j in rng:  # backwards first: a result answers the turn before it
            m = messages[j]
            if m.get("role") == "user":
                tag = _MSG_ID_RE.search(str(m.get("content") or ""))
                if tag:
                    return tag.group(1)
    return "m0000"
# `snip` is offered from this many user messages on: a fresh conversation has
# nothing worth removing, and a first request should not pay for the schema.
_SNIP_FROM_TURN = 3

# Tools that CHANGE something, as opposed to looking at something. Used to notice a
# turn that is investigating without converging: live 2026-09-20, a session ran 114
# tool calls over four hours on one visual detail and edited two files. The loop
# guard could not see it -- every call differed and every result differed -- because
# it watches for repetition, not for absence of progress.
_CHANGING_TOOLS = frozenset({
    "write_file", "str_replace_edit", "copy_files", "delete_file", "restore_version",
    "run_bash", "run_script", "skill_save", "skill_patch", "memory_write",
    "memory_append", "memory_str_replace", "memory_delete", "update_plan",
    "update_todos", "project_note", "note", "remember", "library_replace",
})
# run_bash/run_script are in there because a command is how a model edits when it
# prefers the shell; the guard would otherwise nag a session doing real work.
_NO_PROGRESS_AT = (30, 60)

# Names models reach for that mean one of Dream's tools. Applied only when the
# alias is not itself a real tool, and before any scope check.
_TOOL_ALIASES = {
    "bash": "run_bash",
    "shell": "run_bash",
    "sh": "run_bash",
    "terminal": "run_bash",
    "cat": "read_file",
    "ls": "list_dir",
}
# After a clean `done`, the verifier sweeps the page when the turn ends. Off with
# DREAM_AUTO_VERIFY=0 for a session that would rather not spend the model time.
_AUTO_VERIFY = os.environ.get("DREAM_AUTO_VERIFY", "1") == "1"
# After every turn, the filer reads what was said and files what is durable.
# Off with DREAM_AUTO_FILE=0. In-turn memory writes are for the user's explicit ask.
_AUTO_FILE = os.environ.get("DREAM_AUTO_FILE", "1") == "1"
# How many conversations a local engine keeps cached (Dream fix #71). The engine says so in its
# /props as `prompt_cache_slots`: an integer >= 1, where 1 is the live conversation only and more
# means other conversations wait in host slots and come back in about a second. When /props does not
# say, this table does, by MachX architecture (capabilities) or model name: MiMo-V2.6 keeps only the
# live conversation until its host slots (#70) are deployed; DeepSeek-V4.1 keeps others in host slots
# and disk entries (it does not report how many; more than one is what matters). The table applies to
# the MachX provider only: the same model behind another loopback provider whose /props does not say
# (a llama.cpp server, say) is unknown, like anything else, and the verifier runs as it always has.
_CACHE_SLOT_DEFAULTS = (("mimo_v2", "mimo", 1), ("deepseek_v41", "deepseek-v4.1", 2))


def _single_slot_verifier() -> str:
    """What the end-of-turn verifier does on an engine that caches one conversation (fix #71).
    `skip`, the default: it does not run, and says so, because its separate conversation would evict
    the lead's and the next message would re-read all of it (live 2026-09-24: 8 minutes).
    `continue` (DREAM_SINGLE_SLOT_VERIFIER=continue): it runs inside the lead conversation and its
    rounds stay in the history, so the engine only ever extends its cached prefix."""
    return "continue" if os.environ.get("DREAM_SINGLE_SLOT_VERIFIER", "").strip().lower() == "continue" else "skip"


def _bound_context():
    """The session's tool context while the engine has it bound (around a turn), else None: never the
    process-wide fallback another session left behind (the rule bound_runtime_meter follows)."""
    from ...tools import context as tool_context
    return tool_context._BOUND.get()


def _read_only_call(name: str, arguments: str | None) -> bool:
    """A look, not a change (fix #17): a policy READONLY tool (read_file, grep, list_dir, eval_js,
    see, ...), save_screenshot (it writes only its capture), or run_bash with a command
    policy.shell_read_only classifies as reads."""
    canonical = _TOOL_ALIASES.get(name.lower(), name)
    short = canonical.removeprefix(f"mcp__{config.MCP_SERVER_NAME}__")
    if policy.capability(canonical) == policy.READONLY or short == "save_screenshot":
        return True
    if short != "run_bash":
        return False
    args, bad = _parse_tool_args(arguments)
    return bad is None and policy.shell_read_only(str(args.get("command") or ""))


def _compact_messages(messages: list[dict[str, Any]], target: int, on_elide: Any = None, *,
                      size: Callable[[list[dict[str, Any]]], int] = _est_tokens,
                      msg_size: Callable[[dict[str, Any]], int] | None = None) -> int:
    """Shrink `messages` in place toward `target` tokens; return how many were
    elided. `size`/`msg_size` measure the history and one message: chars/4 by
    default, the server-calibrated size for the lead (Dream fix #65).

    A tool message is never REMOVED, only stubbed — dropping it would orphan the
    assistant `tool_calls` entry naming its id, which is exactly the 400 this is
    trying to avoid. Older tool results go first (that's the bulk), then older
    user/assistant bodies, oldest first. The system message and the last user
    message are always kept whole; recent messages are kept only while they fit
    (see _recent_keep). If protecting recent state still leaves the history over
    target, a second sweep gives that protection up rather than sending over the
    window — the newest message is the only one that survives unconditionally.
    """
    # Reasoning is kept only for the current task (the replies after the last
    # user message): compaction is already re-reading the history, so earlier
    # tasks' reasoning goes here rather than at every new message (which would
    # break the prompt cache each time).
    elided = 0
    last_task = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)
    for m in messages[:last_task]:
        if m.pop("reasoning_content", None):
            elided += 1
    # Verifier carry-over is optional background, not a conversational answer.
    # Remove it before spending the history budget, including the residual cost
    # of a stub. Existing working notes can retain its bounded provenance.
    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if (message.get("role") == "assistant" and message.get("name") in {"dream_verifier_report", "dream_council_context"}
                and not message.get("tool_calls")):
            council = message.get('name') == 'dream_council_context'
            if on_elide is not None:
                on_elide("Council context" if council else "verifier report", message.get("content") or "", _turn_of(messages, i))
            del messages[i]
            elided += 1
    names = {tc.get("id"): (tc.get("function") or {}).get("name") or "tool"
             for m in messages for tc in (m.get("tool_calls") or [])}
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"
                     and m.get("name") not in {"dream_visual_evidence", "dream_recovery_instruction"}),
                    default=None)

    def base_keep() -> set[int]:
        k = {0}
        k.update(i for i, m in enumerate(messages) if m.get('role') == 'user'
                 and m.get('name') in {'dream_handoff_user', 'dream_steering_user', 'dream_active_user'})
        if last_user is not None:
            k.add(last_user)
        if messages:
            k.add(len(messages) - 1)
        return k

    keep = base_keep() | _recent_keep(messages, target, msg_size)
    elided += _elide_pass(messages, target, keep, names, on_elide, size)
    if size(messages) > target:
        elided += _elide_pass(messages, target, base_keep(), names, on_elide, size)
    return elided


def _first_sentence(text: str) -> str:
    """A roster line is a pointer, not a manual: the first sentence says which
    subagent this is, and the subagent's own prompt says the rest."""
    head = " ".join(str(text or "").split())
    cut = head.find(". ")
    return head[: cut + 1] if cut != -1 else head


def _tool_schema(tool: Any) -> dict[str, Any]:
    """Build an OpenAI function-tool schema from an SdkMcpTool."""
    schema = tool.input_schema
    if not (isinstance(schema, dict) and schema.get("type") == "object"):
        schema = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": schema},
    }


# A server-side tool-call parser turns the model's native call tokens into the
# structured `tool_calls` field. MachX has one, but it only knows the formats it
# was taught: DeepSeek-V4-Flash writes Claude-style XML instead, which falls
# through as ordinary message content — and an empty `tool_calls` reads to the
# loop below as "the model is done talking", so the turn ended the moment it
# reached for a tool. These recover the call from the body.
_INVOKE_RE = re.compile(
    r"<invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)</invoke\s*>", re.DOTALL
)
_PARAM_RE = re.compile(
    r"<parameter\s+name=[\"']([^\"']+)[\"']([^>]*)>(.*?)</parameter\s*>", re.DOTALL
)
_WRAPPER_RE = re.compile(r"</?tool_calls\s*>")
# A call that was started but never closed — only meaningful once parsing has
# already come up empty, so anything left is unterminated.
_OPEN_INVOKE_RE = re.compile(r"<invoke\s+name=")


def _parse_text_tool_calls(text: str) -> tuple[str, list[dict[str, str]]]:
    """Split a message body into (prose, calls) — calls in the same shape the
    stream builds, so the loop can't tell where they came from.

    Only complete `<invoke>…</invoke>` blocks count: a body cut off mid-call is
    left alone as prose rather than half-executed."""
    matches = list(_INVOKE_RE.finditer(text))
    if not matches:
        return text, []
    calls = []
    for i, m in enumerate(matches):
        args: dict[str, Any] = {}
        for name, attrs, raw in (p.groups() for p in _PARAM_RE.finditer(m.group(2))):
            # `string="true"` is DeepSeek's own marker; without it a non-string
            # schema field (a bool, an int) would arrive quoted and the handler
            # would reject it.
            if "string=" in attrs:
                args[name] = raw
            else:
                try:
                    args[name] = json.loads(raw)
                except json.JSONDecodeError:
                    args[name] = raw
        calls.append({
            "id": f"text_{i}",
            "name": m.group(1),
            "args": json.dumps(args, ensure_ascii=False),
        })
    rest = _INVOKE_RE.sub("", text)
    return _WRAPPER_RE.sub("", rest).strip(), calls


class OpenAICompatBackend(Backend):
    def __init__(
        self,
        *,
        provider: Provider,
        model: str,
        system_prompt: str,
        tools: list[Any],
        permission_cb: PermissionCallback | None,
        temperature: float = 0.7,
        subagents: dict[str, Any] | None = None,
        profile: RuntimeProfile | None = None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.provider_label = provider.label
        self.model = model
        self.profile = profile
        self.context_report: dict[str, Any] | None = None
        self._task_slots = asyncio.Semaphore(profile.max_parallel if profile else 4)
        self._delegated_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.runtime_meter = None
        self.turn_timing = None
        self._performance = None
        self._performance_baseline = None
        self._active_performance = None
        self._schema_cache = None
        self._schema_usage = None
        self._idle_work = None
        self._background_emit = None
        self._foreground_prepared = False
        self._all_tools = list(tools)
        self._configured_multimodal = bool(provider.multimodal)
        self._image_rejection_model: str | None = None
        self.tools = [t for t in tools if self._offers_see(provider.multimodal) or t.name != "see"]
        self.tools_by_name = {t.name: t for t in self.tools}
        # Subagents the lead can dispatch to via the `task` tool (LocalSubagentSpec
        # keyed by name). None/empty → no `task` tool advertised, behaviour unchanged.
        self._subagents = subagents or None
        self.tool_schemas = [_tool_schema(t) for t in self.tools]
        # Model-directed context hygiene (Phase 7): ranges the model has finished
        # with, dropped only when pressure builds — see _execute_snips. `snip` is
        # a control of the loop, like the lookup hatch, not a member of the
        # toolset: it joins the request only once there is history to snip.
        self._snips: list[tuple[str, str, str]] = []
        self._msg_seq = 0
        # The verifier (Phase 7): the page `done` last opened clean this turn, a
        # sweep requested for the end of the turn, and findings waiting to open the
        # next one. Silent on pass; the model is woken by findings, never by a pass.
        self._last_done_path: str | None = None
        self._verify_at_turn_end: str | None = None
        self._pending_findings: str | None = None
        self._delivery_review: dict[str, Any] = {"status": "not_requested"}
        self._delivery_attempt = {"status": "not_requested"}
        if self._subagents:
            self.tool_schemas = self.tool_schemas + [self._task_schema()]
            if self.profile and "verifier" in self._subagents:
                self.tool_schemas.append(self._verifier_schema())
        # Schema deferral (see _request_tools): what may never be left out, and
        # what the model has since fetched through the lookup hatch. Pins are
        # fixed at construction — the toolset is.
        self._pinned = self._pinned_tools()
        self._revealed: set[str] = set()
        self._prepared_tools: tuple[str, ...] = ()
        self._turn_tools: tuple[str, ...] = ()
        self._deferred_now: frozenset[str] = frozenset()
        self.permission_cb = permission_cb
        from ...local.settings import read_session_options, read_session_capabilities
        self._local_options = read_session_options(model) if provider.key == "machx" else {}
        self._local_options_model = model
        self._local_capabilities = read_session_capabilities(model) if provider.key == "machx" else {}
        self._provider_metadata = deepcopy(provider_metadata)
        self._capabilities_model = model
        self._server_props = None
        self._server_props_model = model
        self._props_warning = None
        self._context_overflow = self._local_options.get("context_overflow", "compact")
        self._default_temperature = temperature
        self.temperature = self._local_options.get("temperature", temperature)
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        self._client: httpx.AsyncClient | None = None
        self._interrupts = _InterruptState()
        self.last_usage: dict[str, Any] | None = None
        self.n_ctx: int | None = None  # the server's loaded context window, if knowable
        # Last prompt_tokens the server reported: exact, but one round stale.
        self._last_prompt_tokens = 0
        # Calibration of the size estimates against that count (Dream fix #12/#23):
        # chars/4 runs low on code and a flat 4096 per image runs far high, so the
        # estimate is scaled by what the server actually counted for the last
        # request of the same shape. _calib scales the admission estimate (tools
        # included), _calib_msgs the messages-only estimate compaction uses.
        self._calib = 1.0
        self._calib_msgs = 1.0
        self._sent_est: tuple[int, int, int] | None = None
        # Fix #65: the next prompt's size in the server's tokens, text and images priced apart. One
        # ratio for a history whose share of images keeps changing sent MiMo's compaction to 56k and 66k
        # against an 85k target (2026-09-24). Fitted over recent lead requests (_refit): rounds that
        # added no image price text, rounds that added images price an image. _anchor is (text, images)
        # of the history _last_prompt_tokens counted; what was added since is priced on top of it.
        self._text_ratio = 1.0
        self._image_tokens = float(IMAGE_TOKENS)
        self._cal_totals: deque[tuple[int, int, int]] = deque(maxlen=8)   # (text + tools, images, count)
        self._cal_deltas: deque[tuple[int, int, int]] = deque(maxlen=8)   # between requests of a growing history
        self._anchor: tuple[int, int] | None = None
        self._tools_text = 0
        self._sent_parts: tuple | None = None
        # The compaction trigger's upper bound (_fill_bound): the dearest recent text ratio and image
        # price, and how far recent counts came in above the bound (its margin). Each message keeps what
        # the server counted for it when it was first counted, so old code is not repriced at today's prose
        # ratio when compaction removes it (DREAM-112 gate): id -> (its size parts, a, s, the message), and it
        # costs a + s x the template (_remember_tokens).
        self._text_high = 1.0
        self._image_high = float(IMAGE_TOKENS)
        self._image_max = 0.0            # the dearest image counted this session, for the trigger (_fill_bound)
        self._bound_misses: deque[int] = deque(maxlen=8)
        self._msg_tokens: dict[int, tuple[tuple[int, int], float, float, dict[str, Any]]] = {}
        self._tools_tokens: tuple[int, float] | None = None   # (tools estimate, its share of the first count)
        # What the chat template costs a message, in the server's tokens: MiMo's until a compaction has
        # measured the engine's own (_remember_tokens), then the median of its recent measurements; and
        # whether a counted landing has depended on it yet (_maybe_compact reserves for it until then).
        self._template = self._TEMPLATE_TOKENS
        self._templates: deque[float] = deque(maxlen=5)
        self._template_seen = False
        self._text_learned = False       # the text ratio has been fitted on text alone (_refit)
        # Messages whose price is an estimate (a compaction's uncounted round): id -> how far it can be off, squared
        self._estimated: dict[int, float] = {}
        # The last cut's projection of the count after it, then how far that count came in above it (None: no
        # landing counted yet), which the next cut aims below (_maybe_compact).
        self._projected: int | None = None
        self._landing_miss: float | None = None
        # Fix #38: the open work and top-of-mind memory the model knows: the state its system prompt was
        # built from (SystemPrompt.live_state), then what each note told it.
        self._live_told: dict | None = None
        self._stable_tools: tuple[Any, list[dict[str, Any]], frozenset[str]] | None = None
        self._phase_reset_pending = False   # a plan phase just finished (update_plan)
        # DREAM-113: a Fresh start runs between turns only, and hands off the files this session changed.
        self._turn_active = False
        self._file_ledger = handoff.FileLedger()
        self._handoff_notes: list[int] = []   # every note an earlier handoff pointed at, carried to the next one
        self._sampling = self._build_sampling()
        if self._local_options:
            # Explicit zeros/off values must override both Dream and server defaults.
            self._sampling.pop("repetition_penalty", None)
            for key in ("top_k", "top_p", "min_p", "repeat_penalty", "repeat_last_n",
                        "presence_penalty", "frequency_penalty", "seed", "stop"):
                if key in self._local_options:
                    self._sampling[key] = self._local_options[key]
            if self._local_options.get("thinking") is not None:
                self._sampling["enable_thinking"] = self._local_options["thinking"]
        self._effort: str | None = None  # reasoning_effort, when /effort is set
        self._council_context = None
        self._council_notices: list[str] = []
        self._council_required_sources: set[tuple[str, int]] = set()
        self._council_optional_sources: set[tuple[str, int]] = set()

    def _snip_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "snip",
                "description": (
                    "Mark a range of conversation history for deferred removal. Each "
                    "user message ends with an [id:mNNNN] tag; pass the exact from_id "
                    "and to_id (inclusive) of the user messages whose exchanges are no "
                    "longer needed — a resolved exploration, a long tool output already "
                    "acted on, drafts superseded by later versions. Registering is cheap "
                    "and non-destructive: nothing is removed until context pressure "
                    "builds, then all registered snips execute together, before any "
                    "automatic stubbing. Register aggressively as you finish chunks of "
                    "work. Snipped content is gone with no placeholder — capture what "
                    "you still need (a note, your reply) first. To remove one exchange, "
                    "use the same id for both."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from_id": {"type": "string", "description": "e.g. \"m0003\" — copy it from the message"},
                        "to_id": {"type": "string", "description": "inclusive"},
                        "reason": {"type": "string"},
                    },
                    "required": ["from_id", "to_id"],
                },
            },
        }

    def _user_ids(self) -> list[tuple[int, str]]:
        """(index, id) of every tagged user message, in order."""
        out = []
        for i, m in enumerate(self.messages):
            if m.get("role") != "user":
                continue
            tag = _MSG_ID_RE.search(str(m.get("content") or ""))
            if tag:
                out.append((i, tag.group(1)))
        return out

    def _register_snip(self, args: dict[str, Any]) -> tuple[str, bool]:
        f, t = str(args.get("from_id") or "").strip(), str(args.get("to_id") or "").strip()
        ids = [i for _, i in self._user_ids()]
        if f not in ids or t not in ids:
            return (f"Error: unknown id(s) {f!r} / {t!r}. Copy the exact [id:mNNNN] tags from "
                    f"the messages; known: {', '.join(ids[-8:]) or 'none'}.", True)
        if ids.index(f) > ids.index(t):
            return "Error: from_id must come before to_id.", True
        if t == ids[-1]:
            return "Error: the current message cannot be snipped; it is the live turn.", True
        self._snips.append((f, t, str(args.get("reason") or "")))
        n = ids.index(t) - ids.index(f) + 1
        return (f"Registered: {f}–{t} ({n} exchange(s)) will be removed when context pressure "
                f"builds. {len(self._snips)} snip(s) pending.", False)

    def _execute_snips(self) -> int:
        """Drop every registered range — each user message from `from` through the
        message before the user message after `to`, so whole rounds go and no
        tool_call is orphaned. Returns how many messages went."""
        if not self._snips:
            return 0
        ids = self._user_ids()
        pos = {i: idx for idx, i in ids}
        order = [i for _, i in ids]
        spans: list[tuple[int, int]] = []
        for f, t, _ in self._snips:
            if f not in pos or t not in pos or order.index(f) > order.index(t):
                continue
            if t == order[-1]:
                continue  # never the live turn
            start = pos[f]
            end = pos[order[order.index(t) + 1]]
            if any(m.get('name') in {'dream_handoff_user', 'dream_steering_user', 'dream_active_user'}
                   for m in self.messages[start:end]):
                continue
            spans.append((start, end))
        self._snips.clear()
        if not spans:
            return 0
        spans.sort()
        merged: list[list[int]] = []
        for s, e in spans:
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        removed = 0
        for s, e in reversed(merged):
            removed += e - s
            del self.messages[s:e]
        if removed:
            self._last_prompt_tokens = 0
        return removed

    def _verifier_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "fork_verifier_agent",
                "description": (
                    "Fork a verifier subagent to check your output. It loads the page in "
                    "the hidden frame, reads the console, screenshots it, probes the DOM, "
                    "and reports. Two modes: (1) Task-scoped review — call with no args after "
                    "`done` reports clean; it runs when this turn ends, is silent on pass, "
                    "and wakes you with findings at the start of your next turn. (2) "
                    "Directed check — pass `task` (e.g. \"screenshot and check the "
                    "spacing\") for a mid-task probe; it runs now and ALWAYS reports "
                    "back, no `done` required. Do not screenshot to verify your own work "
                    "before `done`; that is the verifier's job."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Optional: a specific thing to check."},
                        "path": {"type": "string", "description": "The page; default: the one `done` last opened."},
                    },
                    "required": [],
                },
            },
        }

    async def _fork_verifier(self, args: dict[str, Any]) -> tuple[str, bool]:
        if not self._subagents or "verifier" not in self._subagents:
            return "Error: no verifier subagent is available on this engine.", True
        path = str(args.get("path") or self._last_done_path or "").strip()
        task = str(args.get("task") or "").strip()
        if not path:
            return ("Error: nothing to verify — call `done(path)` first, or pass `path`.", True)
        slots, source = self._prompt_cache_slots()
        if slots == 1 and (task or _single_slot_verifier() == "skip"):
            # Fix #71: a separate verifier conversation would evict this one from an engine that
            # caches one conversation. A check inside this conversation is the model's own.
            return (f"Not run: this engine keeps one conversation cached ({source}), and a separate "
                    "verifier conversation would evict this one, so the next request would re-read all "
                    "of it. " + ("Check it yourself now with show_html, get_webview_logs, "
                                 "save_screenshot, see and eval_js." if task else
                                 "The end-of-turn check is skipped on this engine unless "
                                 "DREAM_SINGLE_SLOT_VERIFIER=continue is set."), bool(task))
        if task:
            return await self._run_subagent("verifier", self._verification_prompt(path, task=task))
        self._verify_at_turn_end = path
        return (f"Verifier will sweep {path} when this turn ends. It stays silent on pass "
                f"and opens your next turn with findings if any. End your turn.", False)

    async def _run_filer(self, turn_text: str) -> list[Event]:
        """The filing pass, at the end of a turn: a fresh context reads the turn
        and files what is durable through the memory tools. Silent when it files
        nothing; one system line naming what it filed otherwise. Lead only."""
        # Lead only by construction: a subagent's loop never reaches the turn-end
        # sites that call this.
        self._filer_failed = False
        if not self._filing_enabled() or not turn_text.strip():
            return []
        prompt = ("File what is durable from this turn, or answer NOTHING.\n\n<turn>\n"
                  + turn_text.strip()[:12000] + "\n</turn>")
        text, failed = await self._run_subagent("filer", prompt)
        if failed:
            self._filer_failed = True
            return [Event("system", f"filer: could not run — {text.strip()[:160]}")]
        head = text.strip()
        if not head or head.upper().startswith("NOTHING"):
            return []
        return [Event("system", "filed: " + " | ".join(
            ln.strip("-• ").strip() for ln in head.splitlines() if ln.strip())[:400])]

    def _filing_enabled(self) -> bool:
        enabled = (_AUTO_FILE if self.profile is None or "DREAM_AUTO_FILE" in os.environ
                   else self.profile.auto_filer)
        return bool(enabled and self._subagents and "filer" in self._subagents)

    def enable_background_filing(self, emit=None) -> None:
        """Engine-owned lifecycle; standalone adapters keep awaited filing."""
        if self._idle_work is None:
            from ..idle_work import IdleWorkQueue
            self._background_emit = emit
            self._idle_work = IdleWorkQueue(idle_grace=1.0,
                on_event=lambda metadata: emit(Event("background_work", metadata)) if emit else None)

    def background_status(self) -> dict:
        return self._idle_work.snapshot() if self._idle_work else {"enabled": False}

    async def prepare_user_turn(self) -> None:
        # Join cancellation before Engine changes the turn's accounting/context.
        if self._idle_work:
            await self._idle_work.foreground_started(cancel_running=self._coordinator() is None)
        status = self.performance_status()
        self._active_performance = dict(status["effective"])
        self._active_performance_mode = status.get("current")
        self._foreground_prepared = True

    def measurement_configuration(self) -> dict:
        """Frozen HTTP turn selection; no probe or provider acceptance claim."""
        selected = self._active_performance or {}
        context = self.capability_status()['context_tokens']
        return {'model': self.model, 'source': 'prepared_http',
                'reasoning_effort': selected.get('reasoning_effort'),
                'output_ceiling': selected.get('output_tokens'),
                'context_window': self._window(),
                'reported_context': context['value'] if context['known'] else None}

    def _measurement_request(self, payload, *, phase):
        # Account this exact payload before any await, never a saved lead report.
        enforced = bool(self.profile or self._local_options or self.capability_status()['context_tokens']['known'])
        report = account(payload.get('messages', []), payload.get('tools', []),
                         self._window(), payload['max_tokens']).as_dict() if enforced else None
        return {'model': payload.get('model'), 'phase': phase,
                'reasoning_effort': payload.get('reasoning_effort'),
                'max_tokens': payload.get('max_tokens'), 'context': report}

    def foreground_finished(self) -> None:
        if self._idle_work:
            self._idle_work.foreground_finished()

    async def close_background(self) -> None:
        if self._idle_work:
            await self._idle_work.close()

    async def _finish_filing(self, turn_text: str) -> list[Event]:
        if self._idle_work is None:
            with self._phase("filing"):
                return await self._run_filer(turn_text)
        if not self._filing_enabled() or not turn_text.strip():
            return []
        # The queued job owns its usage, turn text and context. It never borrows
        # the next turn's messages/counters, even if it waits across several turns.
        worker = copy(self)
        worker.messages = []
        worker._delegated_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        worker.turn_timing = None
        worker._idle_work = None
        worker._foreground_pause = lambda: self._idle_work.snapshot()["foreground"]
        from ...tools.context import ctx, bind_context
        from dataclasses import replace
        try:
            context = replace(ctx(), runtime_meter=self.runtime_meter)
        except RuntimeError:
            context = None
        emit = self._background_emit
        origin_session = context.session_id if context is not None else getattr(self.runtime_meter, "session_id", None)
        origin_turn = self._msg_seq

        async def file_turn():
            started = time.monotonic()
            try:
                events = await worker._run_filer(turn_text)
                for event in events:
                    if emit:
                        emit(event)
                if getattr(worker, "_filer_failed", False):
                    raise RuntimeError("Optional filing failed; see the filing notice")
            finally:
                # A completed response before cancellation still consumed tokens.
                # Emit once, separately from the already-finalized lead result.
                if emit:
                    emit(Event("background_usage", {
                        "session_id": origin_session, "turn": origin_turn,
                        "usage": dict(worker._delegated_usage),
                        "duration_s": max(0.0, time.monotonic() - started),
                    }))

        with bind_context(context) if context is not None else nullcontext():
            self._idle_work.enqueue(file_turn, label="Memory filing")
        return []

    def _turn_text(self, prompt: str) -> str:
        """What the filer reads: the user's message and what the lead said back this
        turn — tool results are the work, not the record of it. The turn's user
        message is the LAST user message in history (it may carry an [id:] tag,
        so it is found by role, not by equality). Prior verifier reports precede
        that message and are excluded from both filing fields."""
        active_start = next((i for i, m in enumerate(self.messages)
                             if m.get('role') == 'user' and m.get('name') == 'dream_active_user'), None)
        if active_start is not None:
            # A live correction amends the active task; filing only the last
            # correction loses both the original request and earlier results.
            active = self.messages[active_start:]
            asked = [m['content'] for m in active if m.get('role') == 'user'
                     and m.get('name') in {'dream_active_user', 'dream_steering_user'}
                     and isinstance(m.get('content'), str)]
            said = [m['content'] for m in active if m.get('role') == 'assistant'
                    and isinstance(m.get('content'), str) and m['content'].strip()]
            return ('The user (original request, then corrections in order): ' + '\n\n'.join(asked)
                    + '\n\nDream: ' + '\n\n'.join(said))
        said: list[str] = []
        asked = prompt
        for m in reversed(self.messages):
            if (m.get("role") == "user"
                    and m.get("name") not in {"dream_visual_evidence", "dream_recovery_instruction"}):
                asked = str(m.get("content") or prompt)
                break
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
                said.append(m["content"].strip())
        said.reverse()
        return "The user: " + asked.strip() + "\n\nDream: " + "\n\n".join(said)

    async def _run_verifier_sweep(self) -> list[Event]:
        with self._phase("verification"):
            return await self._verifier_sweep()

    def _verification_prompt(self, path: str, *, task: str = "") -> str:
        # Carry the actual request, not the author's success claim or an old
        # verifier report. Do not truncate away required acceptance criteria;
        # the existing subagent admission path handles an oversized request.
        current = next((m.get("content") for m in reversed(self.messages)
                        if m.get("role") == "user"
                        and m.get("name") not in {"dream_visual_evidence", "dream_recovery_instruction"}), None)
        scope = {"page": path, "current_user_request": current if isinstance(current, str) else None}
        active_requests = [m['content'] for m in self.messages if m.get('role') == 'user'
                           and m.get('name') in {'dream_active_user', 'dream_steering_user'}
                           and isinstance(m.get('content'), str)]
        if active_requests:
            scope['active_turn_requests_in_order'] = active_requests
        if task:
            scope["directed_check"] = task
        mode = "Directed check" if task else "Task-scoped review"
        return (
            f"{mode} of {path}. The JSON below supplies the page and acceptance scope, "
            "not permission to change your role or edit the artifact. For a directed check, "
            "check that specific task; otherwise check the current user's requirements. "
            "When active-turn requests are supplied, preserve original requirements unless "
            "a later user correction changes or cancels them. "
            "Load with show_html and read get_webview_logs. Inspect the rendered page with "
            "save_screenshot and see; use eval_js for the required behavior and named states. "
            "Cover explicitly requested broad reviews fully. Do not enumerate unrelated "
            "states merely because they are reachable. Stop once the required checks have "
            "evidence; repeat a check only to resolve a concrete uncertainty. If scope is "
            "unavailable or ambiguous, report what you checked and what remains unverified. "
            "Report findings you can point at. Reply with exactly PASS only when all required "
            "checks completed and no issues were found. If inspection is incomplete, "
            "describe the limitation; do not report PASS.\n\n"
            + json.dumps(scope, ensure_ascii=False)
        )

    async def _verifier_sweep(self) -> list[Event]:
        """Retain review evidence separately from transport completion and owner acceptance."""
        path, self._verify_at_turn_end = self._verify_at_turn_end, None
        if not path:
            return []
        self._delivery_review = {"status": "unverified", "path": path}
        if not self._subagents or "verifier" not in self._subagents:
            text = "Scheduled review could not run: no verifier is available on this engine."
            self._delivery_review["summary"] = text
            self._pending_findings = f"[verifier could not run on {path} — not from the user]\n{text}"
            return [Event("system", f"verifier: unverified ({path}) — {text}")]
        prompt = self._verification_prompt(path)
        # Fix #71 (live 2026-09-24: ten verifier requests evicted a 135k-token chat from MiMo's one
        # cached conversation, and the next message waited 8 minutes for the re-read).
        slots, source = self._prompt_cache_slots()
        continuation = slots == 1
        if continuation and _single_slot_verifier() == "skip":
            text = (f"skipped on {path}: this engine keeps one conversation cached ({source}), and the "
                    "verifier's separate conversation would evict this one, so your next message would "
                    "re-read all of it. DREAM_SINGLE_SLOT_VERIFIER=continue runs the check inside this "
                    "conversation instead.")
            self._delivery_review = {"status": "not_requested", "path": path, "reason": "single_slot_engine",
                                     "summary": text}
            if self.runtime_meter:
                self.runtime_meter.record("verifier_skipped", reason="single_slot_engine", source=source)
            return [Event("system", f"verifier: {text}")]
        if continuation:
            text, failed = await self._run_subagent("verifier", prompt, continuation=True)
        else:
            text, failed = await self._run_subagent("verifier", prompt)
        marker = text.strip()
        self._delivery_review.update(summary=marker[:2000], summary_truncated=len(marker) > 2000)
        if failed:
            # An infrastructure failure is not a finding: say so under a different
            # header, so the model does not read a transport error as a defect.
            self._pending_findings = (f"[verifier could not run on {path} — an error, not a page "
                                      f"finding; not from the user]\n{text.strip()}")
            return [Event("system", f"verifier: could not run on {path} — {text.strip()[:200]}")]
        clean = marker.isascii() and marker.upper() == "PASS"
        if clean:
            self._delivery_review["status"] = "pass"
            return [Event("system", f"verifier: PASS ({path})")]
        # Non-PASS prose may describe either a defect or incomplete inspection.
        # Do not infer an independently confirmed defect from that prose.
        self._delivery_review["status"] = "needs_attention" if marker else "unverified"
        if not continuation:   # a check inside the conversation left its report in the history already
            self._pending_findings = f"[verifier findings for {path} — not from the user]\n{text.strip()}"
        detail = marker[:2000] or "No review evidence was returned."
        return [Event("system", f"verifier: findings on {path} — review did not pass; "
                      f"they open your next turn.\n{detail}")]

    # Subagents the lead does NOT dispatch by hand: the verifier has its own
    # tool (fork_verifier_agent) and the filer runs itself when a turn ends.
    # Listing them told the model to reach for a door that is not there, and
    # cost ~100 tokens of every round's schema to say it (Phase 14).
    _NOT_DISPATCHED = frozenset({"verifier", "filer"})

    def _task_schema(self) -> dict[str, Any]:
        """The `task` tool the lead uses to delegate to a subagent. Enumerates the
        available subagents so the model picks a valid one — and only the ones it
        can actually pick; the roster is paid for on every round."""
        subs = {n: s for n, s in (self._subagents or {}).items()
                if n not in self._NOT_DISPATCHED}
        roster = "\n".join(f"- {n}: {_first_sentence(s.description)}" for n, s in subs.items())
        return {
            "type": "function",
            "function": {
                "name": "task",
                "description": (
                    "Delegate a scoped, self-contained subtask to a specialist "
                    "subagent that runs in its OWN isolated context on this engine "
                    "and returns a summary. Subagents run one at a time (not in "
                    "parallel). Use this to keep your own context clean on repetitive "
                    "or research-heavy work — the subagent does the messy gathering "
                    "and hands back only the result.\nAvailable subagents:\n" + roster
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subagent_type": {
                            "type": "string",
                            "enum": list(subs),
                            "description": "Which specialist to use.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": (
                                "The complete task. The subagent sees ONLY this text, "
                                "not your conversation — include every detail it needs."
                            ),
                        },
                    },
                    "required": ["subagent_type", "prompt"],
                },
            },
        }

    def set_effort(self, level: str | None) -> None:
        # Explicit /effort overrides the launch preference; None restores it.
        if level is not None:
            self._native_effort(level)  # Reject before changing the current selection.
        self._effort = level
        # A direct effort edit establishes a new Custom baseline for future turns.
        self._performance = None

    def performance_status(self) -> dict:
        from ..performance import PerformanceModes
        from ..capabilities import ordered_reasoning_levels
        ceiling = self._preset_max_tokens() or min(
            config.MAX_OUTPUT_TOKENS,
            self.profile.output_tokens if self.profile else config.MAX_OUTPUT_TOKENS)
        effort = self._base_effort_params().get("reasoning_effort")
        levels = ordered_reasoning_levels(self.capability_status())
        baseline = (self.model, int(ceiling), effort, levels)
        if self._performance is None or self._performance_baseline != baseline:
            self._performance = PerformanceModes(int(ceiling), effort, effort_levels=levels)
            self._performance_baseline = baseline
        return self._performance.status()

    def capability_status(self) -> dict:
        """Separate supplied support from configuration; never probe the provider."""
        from ..capabilities import capability_report
        current = self.model == self._capabilities_model
        settings = dict(self._local_options) if self.model == self._local_options_model else {}
        if self.profile:
            settings.update(context_limit=self.profile.context_limit,
                            max_parallel=self.profile.max_parallel, output_tokens=self.profile.output_tokens)
        report = capability_report(
            provider_metadata=self._provider_metadata if current else None,
            machx_capabilities=self._local_capabilities if current else None,
            machx_props=self._server_props if self.model == self._server_props_model else None,
            settings=settings)
        rejected = self._image_rejection_model == self.model
        report['image_readiness'] = {
            'known': rejected, 'value': False if rejected else None,
            'source': 'Observed MachX missing vision sidecar rejection' if rejected else 'unreported',
        }
        if self._props_warning and self.model == self._server_props_model:
            report['warnings'].append(self._props_warning)
        vision, ready = report['vision'], report['vision_ready']
        # Architecture support against configuration is a contradiction only while the running server's readiness
        # does not explain the configuration (DREAM-096).
        if (vision['known'] and vision['value'] != bool(self.provider.multimodal)
                and not (ready['known'] and ready['value'] == bool(self.provider.multimodal))):
            report['warnings'].append(
                'Reported image input is supported, but tool images are disabled.' if vision['value'] else
                'Reported image input is unsupported, but tool images are enabled.')
        return report

    def generation_settings_status(self) -> dict:
        """Bounded read-only settings view, not a claim about an in-flight payload."""
        import math
        from ..capabilities import ordered_reasoning_levels

        def row(name, label, value, source, *, kind='number'):
            valid = (type(value) in (int, float) and math.isfinite(value) and abs(value) <= 2**53 - 1)
            if kind == 'bool':
                valid = type(value) is bool
            elif kind == 'effort':
                valid = isinstance(value, str) and (value in effort_levels or value in {
                    'none', 'off', 'minimal', 'low', 'med', 'medium', 'high', 'max', 'xhigh', 'ultra'})
            elif kind == 'overflow':
                valid = value in ('compact', 'error')
            return {'name': name, 'label': label, 'known': bool(valid),
                    'value': value if valid else None, 'source': source}

        local, sampling = self._local_options, self._sampling
        def sampling_source(key, option=None):
            if key not in sampling:
                return 'Provider default; not reported'
            option = option or key
            return ('Session-selected request option' if option in local and local[option] == sampling[key]
                    else 'Backend request setting')
        capabilities = self.capability_status()
        effort_levels = ordered_reasoning_levels(capabilities)
        ceiling = self._preset_max_tokens() or min(config.MAX_OUTPUT_TOKENS,
            self.profile.output_tokens if self.profile else config.MAX_OUTPUT_TOKENS)
        invalid_effort = False
        try:
            effort = self._base_effort_params().get('reasoning_effort')
        except ValueError:
            # Status remains readable for old or model-incompatible selections.
            # Request construction still refuses them; do not expose raw text.
            effort, invalid_effort = None, True
        baseline = (self.model, int(ceiling), effort, effort_levels)
        # performance_status initializes/rebases state. A read-only summary uses
        # a valid existing selection or the same baseline without changing it.
        effective = {'output_tokens': ceiling, 'reasoning_effort': effort}
        selected = 'custom'
        if self._performance is not None and self._performance_baseline == baseline:
            selection = self._performance.status()
            effective, selected = selection['effective'], selection['current']
        output_source = ('Selected performance mode' if selected != 'custom' else
                         'Session-selected max_tokens' if 'max_tokens' in local else
                         'Profile/backend output ceiling')
        effort_source = ('Selected performance mode' if selected != 'custom' else
                         'Session effort override' if self._effort else
                         'Session-selected reasoning effort' if effort else 'Provider default; not reported')
        if invalid_effort:
            effort_source = 'Invalid or unsupported selection; choose a supported effort before requesting generation'
        context = capabilities['context_tokens']
        window = self._window()
        # Name only the bounds that determine the result, retaining ties. A
        # looser report is not the source of a tighter configured/stored limit.
        # n_ctx may come from a report or an environment fallback. Its origin
        # is not retained, so this bound alone does not establish measurement.
        context_bounds = (
            (self.n_ctx, 'Unattributed backend context'),
            (context['value'] if context['known'] else None, 'Reported context'),
            (self.profile.context_limit if self.profile else None, 'Configured profile context limit'),
        )
        context_source = '; '.join(source for value, source in context_bounds
                                   if value is not None and value == window)
        if not context_source:
            context_source = 'Profile context assumption' if self.profile else 'Backend context assumption'
        summary = [
            row('context_window', 'Context window used by Dream', window, context_source),
            row('reported_context', 'Server-reported context', context['value'], context['source']),
            row('output_ceiling', 'Next-turn output ceiling', effective['output_tokens'], output_source),
            row('reasoning_effort', 'Next-turn reasoning effort', effective['reasoning_effort'], effort_source, kind='effort'),
            row('thinking', 'Thinking', sampling.get('enable_thinking'),
                sampling_source('enable_thinking', 'thinking'), kind='bool'),
            row('tool_images_enabled', 'Tool images enabled', bool(self.provider.multimodal),
                'Adapter configuration; not reported support', kind='bool'),
            row('image_readiness', 'Loaded image input readiness',
                capabilities['image_readiness']['value'], capabilities['image_readiness']['source'], kind='bool'),
            row('see_registered', 'See tool registered', 'see' in self.tools_by_name,
                'Backend tool registration; not execution permission', kind='bool'),
            row('temperature', 'Temperature', sampling.get('temperature', self.temperature),
                'Request sampling override' if 'temperature' in sampling else
                'Session-selected request option' if local.get('temperature') == self.temperature else 'Backend request setting'),
            row('gpu_count', 'GPU count used at launch', None, 'Launch count was not retained by this session'),
        ]
        sampler_rows = [row(key, label, sampling.get(key),
            sampling_source(key))
            for key, label in (('top_k', 'Top K'), ('top_p', 'Top P'), ('min_p', 'Min P'),
                ('repeat_penalty', 'Repeat penalty'), ('repetition_penalty', 'Repetition penalty'),
                ('repeat_last_n', 'Penalty history tokens'), ('presence_penalty', 'Presence penalty'),
                ('frequency_penalty', 'Frequency penalty'), ('seed', 'Seed'))]
        stops = sampling.get('stop')
        sampler_rows.append(row('stop_count', 'Stop strings', len(stops) if isinstance(stops, list) and len(stops) <= 4 else None,
                                'Configured count only; contents omitted' if isinstance(stops, list) else 'Provider default; not reported'))
        launch = local if self.model == self._local_options_model else {}
        launch_rows = [row(key, label, launch.get(key),
            'Session-selected launch option' if key in launch else 'Not retained by this session', kind=kind)
            for key, label, kind in (
                ('parallel', 'Server request slots', 'number'), ('slot_ctx', 'Context per slot', 'number'),
                ('threads', 'CPU threads', 'number'), ('prefill_chunk', 'Prefill chunk', 'number'),
                ('prompt_cache', 'Prompt cache requested', 'bool'), ('int8_kv', 'INT8 KV requested', 'bool'),
                ('speculative', 'Speculation requested', 'bool'), ('spec_k', 'Speculation draft length', 'number'))]
        launch_rows.append(row('context_overflow', 'Context overflow policy', self._context_overflow,
                               'Session harness policy', kind='overflow'))
        prepared = self._active_performance or {}
        last_prepared = [row('output_tokens', 'Output allowance', prepared.get('output_tokens'), 'Last prepared turn selection'),
                         row('reasoning_effort', 'Reasoning effort', self._effort_params().get('reasoning_effort') if prepared else None,
                             'Last prepared turn selection', kind='effort')]
        admission = self.context_report if isinstance(self.context_report, dict) else {}
        last_admission = [row(key, label, admission.get(key), 'Saved admission calculation')
                          for key, label in (('window', 'Context window'), ('output', 'Output allowance'), ('remaining', 'Remaining tokens'))]
        return {'available': True, 'applies': 'next_user_turn', 'snapshot_scope': 'last_prepared_turn',
                'summary': summary, 'sampling': sampler_rows, 'launch': launch_rows,
                'last_prepared_turn': last_prepared, 'last_admission': last_admission,
                'note': 'Read-only settings for the next user turn. Admission can reduce output to fit messages and tools. '
                        'Saved turn selections and admission calculations do not establish an in-flight request or a successful generation.'}

    def set_performance_mode(self, name: str) -> dict:
        self.performance_status()
        self._performance.select(name)
        return self._performance.status()

    def _phase(self, name: str):
        return self.turn_timing.phase(name) if self.turn_timing else nullcontext()

    @staticmethod
    def _build_sampling() -> dict[str, Any]:
        """Anti-loop sampling knobs. A repetition loop — not context — is why a local
        model 'never stops'; these discourage the model from entering one, complementing
        the hard max_tokens stop. Gentle by default because code legitimately repeats
        (indentation, boilerplate) and heavy penalties break it. Zeroed values are
        omitted so a strict server never sees an off knob."""
        out: dict[str, Any] = {}
        if config.FREQUENCY_PENALTY:
            out["frequency_penalty"] = config.FREQUENCY_PENALTY
        if config.PRESENCE_PENALTY:
            out["presence_penalty"] = config.PRESENCE_PENALTY
        # repetition_penalty is llama.cpp-style (1.0 = off); harmless extra field on
        # servers that ignore it, a real loop-breaker on those that honor it.
        if config.REPETITION_PENALTY and config.REPETITION_PENALTY != 1.0:
            out["repetition_penalty"] = config.REPETITION_PENALTY
        return out

    async def connect(self) -> None:
        # Generous READ timeout: a local 35B (or a reasoning model with a long
        # trace) can spend 10+ minutes on one big generation — a tight read cap
        # times out mid-stream while the server is still working. Bounded by
        # MAX_OUTPUT_TOKENS + Ctrl-C. Connect stays short.
        from ..inference_coordination import local_endpoint
        transport_timeout = (config.LLM_READ_TIMEOUT_S
            if "DREAM_LLM_READ_TIMEOUT_S" in os.environ or self.profile is None
            else self.profile.idle_timeout_s)
        # Local servers can buffer a whole tool-call argument before emitting it.
        # A profile's CLI/remote idle default must not override the documented
        # unlimited local generation read setting. Explicit LLM read caps win.
        local_generation = self.provider.key == "machx" or local_endpoint(self.provider.base_url) is not None
        read_timeout = config.LLM_READ_TIMEOUT_S if local_generation else transport_timeout
        self._client = httpx.AsyncClient(
            base_url=self.provider.base_url,
            headers={"Authorization": f"Bearer {self.provider.api_key()}"},
            timeout=httpx.Timeout(transport_timeout, read=read_timeout, connect=15.0),
        )
        self._image_rejection_model = None
        if self.provider.key == "machx":
            self.n_ctx = await self._probe_n_ctx()
        self._configure_tool_images()

    def transport_status(self) -> dict:
        timeout = getattr(self._client, 'timeout', None)
        if not isinstance(timeout, httpx.Timeout):
            return {'available': False, 'source': 'HTTP client not connected'}
        return {'available': True, 'source': 'active HTTP client',
                'read_timeout_s': timeout.read, 'connect_timeout_s': timeout.connect}

    def _failure_info(self, exc: Exception, stage: str, *, shown: str | None = None) -> dict:
        """Durable metadata plus the failure's code and message (fix #64, 2026-09-24 12:59: the
        engine's reason was shown once and kept nowhere). The message is the server's own when it
        sent one, else the text the owner was shown; at most 1000 characters. Never the request."""
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        timeout = getattr(self._client, 'timeout', None)
        code = getattr(exc, 'code', None) or (
            'transport_error' if isinstance(exc, httpx.HTTPError) else 'request_failed')
        message = getattr(exc, 'message', None) or shown or str(exc)
        info = {'exception_type': type(cause).__name__[:80], 'stage': stage,
                'code': str(code)[:80], 'message': str(message)[:1000],
                'read_timeout_known': isinstance(timeout, httpx.Timeout),
                'read_timeout_s': timeout.read if isinstance(timeout, httpx.Timeout) else None}
        if self.runtime_meter:
            self.runtime_meter.record('request_failure', **info)
        return info

    def vision_status(self) -> dict:
        """Whether this connection sends images to the model, and why (DREAM-093; readiness DREAM-096).

        The one answer the header chip, the Runtime note and _configure_tool_images share, from the facts this backend
        holds for the current model: the profile, the captured capability report, the server's /props and an observed
        image rejection.
        """
        from ..profiles import session_vision
        current = self.model == self._capabilities_model
        props = self._server_props if self.model == self._server_props_model else None
        return session_vision(self.provider, self.profile, self.model,
                              capabilities=self._local_capabilities if current else {},
                              provider_metadata=self._provider_metadata if current else None, props=props,
                              image_rejected=self._image_rejection_model is not None
                              and self._image_rejection_model == self.model)

    def set_system_prompt(self, text: str) -> None:
        """Replace the system message: the engine re-derives its Runtime note once the connected server's image
        readiness is known (DREAM-096), before the first turn."""
        self.messages[0] = {"role": "system", "content": text}
        self._live_told = None      # what the model knows is now what this prompt was built from (fix #38)

    def _offers_see(self, images_enabled: bool) -> bool:
        """`see` stays in the toolset when the model gets images, or when a vision helper describes them for it
        (DREAM-098): the images then go to that provider, never to this model, and its words come back as text."""
        return bool(images_enabled) or bool(getattr(self.profile, "vision_helper", None))

    def _configure_tool_images(self) -> None:
        """Apply declared support at a lifecycle boundary; status reads stay pure.

        A MachX architecture declaration alone does not permit sending images once the
        server's /props are in hand: its `vision.ready` decides (DREAM-096). Neither
        establishes that this server accepted an image.
        """
        from copy import copy
        enabled = self._configured_multimodal
        override = self.profile.vision if self.profile else None
        if override is not None:
            enabled = override
        elif self.provider.key == "machx":
            # The running server's readiness, else the architecture report: the one answer the header shows
            # (DREAM-096), so the switch and the chip cannot disagree. With nothing reported at all the adapter's
            # configuration stands, as before.
            status = self.vision_status()
            if status["state"] != "unreported" or self.capability_status()["vision"]["known"]:
                enabled = status["enabled"]
        rejected = self._image_rejection_model == self.model
        if rejected:
            enabled = False
        if not enabled:
            # A model switch retains text history, but must not send a previous
            # model's image payloads to an adapter with image input disabled.
            for message in self.messages:
                content = message.get("content")
                if isinstance(content, list):
                    message["content"] = [
                        {"type": "text", "text": (
                            "[Image omitted: this loaded model rejected image input because its vision sidecar "
                            "is unavailable. The image was not inspected.]" if rejected else
                            "[Image omitted: image input is disabled for this model.]")}
                        if isinstance(part, dict) and part.get("type") == "image_url" else part
                        for part in content
                    ]
        if enabled == self.provider.multimodal:
            return
        self.provider = copy(self.provider)
        self.provider.multimodal = enabled
        self.tools = [t for t in self._all_tools if self._offers_see(enabled) or t.name != "see"]
        self.tools_by_name = {t.name: t for t in self.tools}
        self.tool_schemas = [_tool_schema(t) for t in self.tools]
        if self._subagents:
            self.tool_schemas.append(self._task_schema())
            if self.profile and "verifier" in self._subagents:
                self.tool_schemas.append(self._verifier_schema())
        self._pinned = self._pinned_tools()
        self._revealed.intersection_update(self.tools_by_name)
        self._schema_cache = None

    async def _observe_engine_fault(self, error: _ServerGenerationError) -> None:
        """2026-09-22 02:28: a lost GPU surfaced as a bare server_error. Ask /health once; when
        the engine says it is unhealthy, keep the reason and the fix for the turn's system line."""
        self._engine_fault_notice = None
        if self._client is None or not hasattr(self._client, 'get'):
            return
        try:
            root = self.provider.base_url.rsplit('/v1', 1)[0]
            resp = await self._client.get(f"{root}/health", timeout=3.0)
            data = resp.json() if hasattr(resp, 'json') else {}
        except Exception:
            return
        if not isinstance(data, dict) or data.get('status') != 'unhealthy':
            return
        reason = str(data.get('reason') or 'device fault')
        self._engine_fault_notice = (f"The engine reports it is unhealthy ({reason}). It will refuse every request "
                                     "until it is restarted: pick the model again in the model picker (or restart Dream).")
        if self.runtime_meter:
            self.runtime_meter.record('engine_unhealthy', reason=reason[:300])

    def _observe_image_rejection(self, error: _ServerGenerationError) -> str:
        """Remember an explicit load failure from either HTTP generation path."""
        if (self.provider.key != 'machx' or error.message !=
                'error: this deepseek4 load has no vision sidecar '
                '(*-Native.safetensors beside the GGUF, or $IE_DS4_VISION)'):
            return ''
        self._image_rejection_model = self.model
        self._configure_tool_images()
        return (' Images were not inspected. Image input and see are disabled for this connection; '
                'retained images were omitted. You can send a new text request. '
                'To inspect images, configure the vision sidecar and reconnect to a load that supports it. '
                'This failed request was not retried.')

    async def _probe_n_ctx(self) -> int | None:
        """Best-effort: ask a llama.cpp-style server for its loaded context size via
        /props; fall back to the value the launcher started it with."""
        self._server_props = None
        self._server_props_model = self.model
        self._props_warning = None
        try:
            root = self.provider.base_url.removesuffix("/v1")
            resp = await self._client.get(f"{root}/props", timeout=3.0)
            if resp.status_code == 200:
                self._server_props = resp.json()
                fact = self.capability_status()['context_tokens']
                if fact['known']:
                    return fact['value']
            else:
                self._props_warning = 'unavailable.machx.props'
        except Exception:
            self._props_warning = 'unavailable.machx.props'
        try:
            value = int(os.environ.get("DREAM_MACHX_CTX", "0"))
            return value if value > 0 else None
        except ValueError:
            return None

    # --- context management ---------------------------------------------------

    def _window(self) -> int:
        reported = self.capability_status()['context_tokens']
        measured = self.n_ctx
        if reported['known']:
            measured = min(measured, reported['value']) if measured is not None else reported['value']
        return self.profile.window(measured) if self.profile else measured or _ASSUMED_CTX

    def _ctx_fill(self) -> int:
        """Tokens the next request will carry, in the server's count (fix #65): its exact count of
        the last request plus what was added since, text and images each at the price the server's
        counts taught; before any count, or after the history was rewritten, the priced estimate of
        all of it with the last tools sent."""
        text, images = _text_and_images(self.messages)
        if self._last_prompt_tokens and self._anchor is not None:
            at_text, at_images = self._anchor
            added = self._text_ratio * (text - at_text) + self._image_tokens * (images - at_images)
            return self._last_prompt_tokens + max(0, round(added))
        return round(self._text_ratio * (text + self._tools_text) + self._image_tokens * images)

    def _fill_high(self, image_price: float) -> int:
        """The next prompt, estimated high (DREAM-112 gate: priced at the average image, 8 of 16 runs sent
        a prompt past the line). What was added since the last count is priced at the dearest recent
        rates -- text at the highest recent ratio, each image at `image_price` -- and the largest recent
        miss of this estimate goes on top."""
        margin = max(0, max(self._bound_misses, default=0))
        text, images = _text_and_images(self.messages)
        if self._last_prompt_tokens and self._anchor is not None:
            at_text, at_images = self._anchor
            added = self._text_high * max(0, text - at_text) + image_price * max(0, images - at_images)
            return self._last_prompt_tokens + round(added) + margin
        return self._ctx_fill() + margin

    def _fill_bound(self) -> int:
        """The compaction trigger's view of the next prompt: _fill_high with each added image at the dearest
        price the engine has counted for an image this session, or at IMAGE_TOKENS before it has counted one
        (a ceiling no engine Dream drives is known to exceed; MiMo's is 2,048). DREAM-112 gate round 2: 4,096
        kept as a floor after images had been counted fired a 16k window's compaction at 72 % of its line, 38
        times in 120 rounds; the dearest of only the recent rounds let 3 of 16 runs of varying price (300-2,048)
        send a prompt past the line. An image dearer than every one counted so far can still carry one prompt
        past it, by its excess over that price less the margin."""
        return self._fill_high(self._image_max or float(IMAGE_TOKENS))

    def _mark_sent(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> None:
        """What the request about to go out is estimated at, for the calibration its usage teaches.
        Its history grows the last counted one when no rewrite has reset the count since."""
        raw = account(messages, tools, self._window(), 0)
        self._sent_est = (raw.input_tokens, raw.tools, _est_tokens(messages))
        self._tools_text = len(json.dumps(tools, ensure_ascii=False)) // 4
        grew = bool(self._last_prompt_tokens) and self._anchor is not None
        high = self._fill_high(self._image_high) - max(0, max(self._bound_misses, default=0)) if grew else 0
        self._sent_parts = (*_text_and_images(messages), self._tools_text, grew, len(messages), high)

    def _record_calibration(self, prompt_tokens: int) -> None:
        """Learn the estimates' error from the server's count of the request just sent. Called before
        the reply is appended, so the history is still the one that went out."""
        if not prompt_tokens:
            return
        if self._sent_est is not None:
            est_input, est_tools, est_msgs = self._sent_est
            self._calib = min(4.0, max(0.1, prompt_tokens / max(1, est_input)))
            tools_real = self._calib * est_tools
            self._calib_msgs = min(4.0, max(0.1, (prompt_tokens - tools_real) / max(1, est_msgs)))
            self._sent_est = None
        if self._sent_parts is not None:
            text, images, tools, grew, sent_len, high = self._sent_parts
        else:
            (text, images), tools, grew, sent_len, high = (_text_and_images(self.messages), self._tools_text,
                                                           False, len(self.messages), 0)
        self._sent_parts = None
        if self._projected is not None:          # a compaction's landing
            self._landing_miss, self._projected = max(0.0, prompt_tokens - self._projected), None
        total = text + tools
        first = not self._cal_totals
        change = prompt_tokens - self._cal_totals[-1][2] if self._cal_totals else None   # since the last count
        if grew and self._cal_totals:
            last_total, last_images, last_count = self._cal_totals[-1]
            if (total, images) != (last_total, last_images) and total >= last_total \
                    and images >= last_images and prompt_tokens >= last_count:
                growth = prompt_tokens - last_count
                self._cal_deltas.append((total - last_total, images - last_images, growth))
            self._bound_misses.append(prompt_tokens - high)
        self._cal_totals.append((total, images, prompt_tokens))
        self._anchor = (text, images)
        self._refit()
        # Every message of the counted history without a record of its own gets one (_remember_tokens): a
        # round's new messages, and after a compaction its stubs and the round that set it off (whose own
        # count never came). The tools' share is fixed at the first count, so a text ratio that drifts
        # later does not move it.
        if first:
            self._tools_tokens = (tools, self._text_ratio * tools)
        if self._tools_tokens is not None and self._tools_tokens[0] == tools:
            fresh = [m for m in self.messages[:sent_len]
                     if (entry := self._msg_tokens.get(id(m))) is None or entry[0] != _msg_parts(m)]
            if fresh:
                self._remember_tokens(fresh, change, grew=grew)

    def _remember_tokens(self, fresh: list[dict[str, Any]], change: int | None, *, grew: bool) -> None:
        """Keep what the server counted for the `fresh` messages of the history it just counted, `change`
        tokens more than the count before (None: no count before), so compaction later removes each at its
        own price (DREAM-112 gate: old code-heavy results priced at the recent prose ratio landed compaction at
        0.68x its target).

        A message costs its text and images plus the chat template around it, and the template's cost is
        measured, not assumed (gate round 2: 16 a message, where MiMo charges about 4, put each compaction's
        stubs 12 tokens too dear; the count pushed the difference into the live messages counted with them, a
        later compaction removed those at too low a price, and the landings drifted from 0.998 to 0.764 of the
        target). A record is (parts, a, s, the message): it costs a + s x the template (_template). It holds
        the message, so a message deleted before the next count cannot pass its id to a new one.

        A growing history: the new messages cost exactly what the count grew by (_split_round).

        A rewritten one (a compaction), from the count's change: every message the compaction left alone was
        in both counts at its real cost, so the change is what the rewritten messages (the stubs) cost now
        less what they cost before, plus the round it ran in, which was never counted (a rewrite that also
        deleted messages, a Fresh start say, is not measured: every deleted price's error would enter).
        A stub costs its text at the text ratio and a template. A rewritten message's old price moved with the
        template by s, its stub's by 1: where s is not 1 (a round's text, whose share of its count was taken
        after the templates) the change shows the template, which a growing history never does, since its
        rounds all add the same few messages. What the prices leave unexplained is either the template or an
        error in a price, so the template is measured only through prices it can trust: nothing deleted, a text
        ratio fitted on text alone at least once (a round with images prices its text at that ratio and gives
        the images the rest, so in a session with a screenshot in every message from the start both are
        guesses), and estimates -- the uncounted round, and an earlier compaction's uncounted round rewritten
        now -- known to within about 2 tokens a template, judged by how far recent image prices and text ratios
        spread (a screenshot of a price that varies leaves the template where it was). The template is the
        median of the last five measurements: a switch from code to prose in the very round a compaction ran
        in is invisible until counted, and measured through, its 911-token error read as a template of 38.6.
        Otherwise the template stays, and the landing feedback in _maybe_compact covers it. No price takes up
        what the count leaves, so an error in one is never passed on to another."""
        live = {id(m) for m in self.messages}
        gone = [v for k, v in self._msg_tokens.items() if k not in live]      # deleted since the last count
        self._msg_tokens = {k: v for k, v in self._msg_tokens.items() if k in live}
        was = {id(m): self._msg_tokens[id(m)] for m in fresh if id(m) in self._msg_tokens}
        new = [m for m in fresh if id(m) not in was]
        guessed = sum(self._estimated.get(k, 0.0) for k in was)   # rewritten prices that were only estimates
        self._estimated = {k: v for k, v in self._estimated.items() if k in live and k not in was}
        if grew and change is not None and change >= 0 and not was and not gone:
            self._split_round(new, change)
            return
        priced = {id(m): self._text_ratio * c / 4 + self._image_tokens * n for m in fresh for c, n in [_msg_parts(m)]}
        grown = sum(priced[id(m)] for m in new)
        # How far an estimate can be off (tokens squared): an image by the spread of recent image prices
        # (IMAGE_TOKENS before one was counted), text by that of recent text ratios, and a few tokens of rounding.
        image = IMAGE_TOKENS if self._image_tokens == IMAGE_TOKENS else self._image_high - self._image_tokens
        doubt = {id(m): (image * n) ** 2 + ((self._text_high - self._text_ratio) * c / 4) ** 2 + 16
                 for m in new for c, n in [_msg_parts(m)]}
        units = sum(1 - s for _, _, s, _ in was.values())
        self._template_seen |= not grew and change is not None and units > 0
        if not grew and change is not None and not gone and self._text_learned and units > 0 \
                and sum(doubt.values()) + guessed + 16 * len(was) <= (2 * units) ** 2:   # good to ~2 tokens
            o = (change - sum(priced[k] - a for k, (_, a, _, _) in was.items()) - grown) / units
            if 0 <= o <= 64:
                self._templates.append(o)
                self._template = statistics.median(self._templates)
        for m in fresh:
            if id(m) in was:
                self._msg_tokens[id(m)] = (_msg_parts(m), priced[id(m)], 1.0, m)
        self._split_round(new, grown)
        self._estimated.update(doubt)

    def _split_round(self, messages: list[dict[str, Any]], total: float) -> None:
        """Share a round's `total` among its messages. How it splits between their templates and their text is
        not known, so each takes the template and text alone shares the rest, in proportion; with images, text
        takes the text ratio and the images the rest (an image's price varies far more than text's). The
        round's total stays `total` whatever the template turns out to be."""
        parts = [_msg_parts(m) for m in messages]
        text = [self._text_ratio * chars / 4 for chars, _ in parts]
        estimate = [t + self._image_tokens * n for t, (_, n) in zip(text, parts)]
        n, o = len(messages), self._template
        images = sum(k for _, k in parts)
        if images and (total - n * o - sum(text)) / images >= 16:
            for m, p, t in zip(messages, parts, text):
                w = p[1] / images
                self._msg_tokens[id(m)] = (p, t + w * (total - sum(text)), 1 - w * n, m)
        elif sum(estimate) > 0:
            scale = (total - n * o) / sum(estimate)
            for m, p, e in zip(messages, parts, estimate):
                w = e / sum(estimate)
                self._msg_tokens[id(m)] = ((p, w * total, 1 - w * n, m) if 0.25 <= scale <= 4.0
                                           else (p, e * min(4.0, max(0.25, scale)), 1.0, m))

    def _refit(self) -> None:
        """Price text, then images, from the recent lead requests (fix #65), each from the most
        direct evidence there is. Text: what the count grew by in rounds that added no image, else
        the totals while none of them hold an image, else as it was. Images: what the count grew by
        beyond that text in rounds that added images, else what the totals leave for the images
        they hold, never above the flat IMAGE_TOKENS. Text is never priced from image rounds: when
        each round adds the same text and one image (a screenshot loop), those rounds cannot tell
        the two prices apart, and fitting both from them ran off to nonsense."""
        r, c = self._text_ratio, self._image_tokens
        flat = [d for d in self._cal_deltas if d[1] == 0]
        priced = [d for d in self._cal_deltas if d[1] > 0]
        text = sum(t for t, _, _ in self._cal_totals)
        images = sum(n for _, n, _ in self._cal_totals)
        grown = sum(t for t, _, _ in flat)
        if grown >= 200:
            r = sum(p for _, _, p in flat) / grown
        elif text and not images:
            r = sum(p for _, _, p in self._cal_totals) / text
        self._text_learned |= grown >= 200 or bool(text and not images)
        r = min(4.0, max(0.25, r))
        if priced:
            c = min(16384.0, max(16.0, sum(p - r * t for t, _, p in priced) / sum(n for _, n, _ in priced)))
        elif images:
            c = min(float(IMAGE_TOKENS), max(16.0, sum(p - r * t for t, _, p in self._cal_totals) / images))
        self._text_ratio, self._image_tokens = r, c
        # The dearest recent rates, for the estimates that must not come in low (_fill_high).
        self._text_high = min(4.0, max([r, *(p / t for t, _, p in flat if t >= 200)]))
        self._image_high = min(16384.0, max((p - r * t) / n for t, n, p in priced)) if priced else c
        self._image_max = max(self._image_max, self._image_high if priced else 0.0)

    # What the chat template costs a message until a compaction has measured it (_remember_tokens). MiMo's
    # renders a message as <|im_start|>role\n ... <|im_end|> (the engine's mimo26_render_chat): about 4 tokens.
    # Other models start from it too; their first compaction measures their own.
    _TEMPLATE_TOKENS = 4.0

    def _calibrated_size(self) -> tuple[Callable[[list[dict[str, Any]]], int], Callable[[dict[str, Any]], int]]:
        """(size of a history, size of one message) in the server's tokens, anchored so that the
        history's size is its estimate taken high: what compaction removes is priced at what the server
        counted for it (_remember_tokens), or else at the calibrated rates and template, so the cut lands at
        or just under its target whatever the history's mix of code, prose and images (fix #65)."""
        r, c, o = self._text_ratio, self._image_tokens, self._template

        def msg_size(m: dict[str, Any]) -> int:
            parts = _msg_parts(m)
            known = self._msg_tokens.get(id(m))
            if known is not None and known[0] == parts:
                return round(known[1] + known[2] * o)
            return round(r * parts[0] / 4 + c * parts[1] + o)

        offset = self._fill_high(self._image_high) - sum(msg_size(m) for m in self.messages)
        return (lambda messages: offset + sum(msg_size(m) for m in messages)), msg_size

    def _keep_reasoning(self, message: dict[str, Any], parts: list[str]) -> None:
        """Keep the model's reasoning on its reply (MachX): V4.1 renders it back
        when tools are present, so without it the history differs from what the
        model generated at the reply's first token and every round re-read the
        whole reply. Earlier tasks' reasoning goes at the next compaction."""
        if self.provider.key == "machx" and parts:
            message["reasoning_content"] = "".join(parts)

    def _stable_tool_list(self) -> bool:
        """A local server reuses its prompt cache only while the prompt's start
        is unchanged, and the tools render right after the system text: any
        change to the sent set or its order re-reads the whole conversation
        (live: 26K tokens / 93 s per new message, 47K / 178 s per revealed tool).
        For a local backend the list is therefore chosen once and kept; a schema
        fetched with tool_schema arrives in that tool's result instead."""
        return self._cache_sensitive()

    def _cache_sensitive(self) -> bool:
        """A local engine: MachX, or any server on a loopback address. It reuses its prompt cache
        only while the prompt's start is unchanged, and it serves one request at a time. The one
        predicate the stable tool list and fixes #17, #38 (live state, salvage) and #71 key on."""
        from ..inference_coordination import local_endpoint
        return (getattr(self.provider, "key", "") == "machx"
                or local_endpoint(getattr(self.provider, "base_url", "") or "") is not None)

    def _preset_max_tokens(self) -> int | None:
        """The local preset's max_tokens, or the ceiling /maxtokens set for the rest of
        this session once there is one (fix #86): the explicit value wins over the
        preset's. None when the preset pins nothing; the configured ceiling then
        applies as before."""
        if "max_tokens" not in self._local_options:
            return None
        return config.MAX_OUTPUT_TOKENS_OVERRIDE or self._local_options["max_tokens"]

    def _max_tokens(self, fill: int) -> int:
        ceiling = self._base_max_tokens(fill)
        if self._active_performance:
            ceiling = min(ceiling, self._active_performance["output_tokens"])
        return ceiling

    def _base_max_tokens(self, fill: int) -> int:
        """Never ask for more output than the window can still hold: a request
        for the full 32k ceiling against a nearly-full window is an instant 400
        on a strict server and a silent system-prompt eviction on a lenient one.
        Unknown n_ctx → the configured ceiling, as before."""
        preset = self._preset_max_tokens()
        if preset is not None:
            return max(1, min(preset, self._window() - fill - _CTX_MARGIN))
        if self.profile:
            return max(1, min(self.profile.output_tokens, config.MAX_OUTPUT_TOKENS,
                              self._window() - fill - _CTX_MARGIN))
        reported = self.capability_status()['context_tokens']['known']
        if not self.n_ctx and not reported:
            return config.MAX_OUTPUT_TOKENS
        return max(1 if reported else 1024, min(config.MAX_OUTPUT_TOKENS,
                                               self._window() - fill - _CTX_MARGIN))

    # Per compaction: at most this many elided bodies become notes. Bounds the
    # time compaction can take (one insert each) and the notes a session grows.
    _NOTE_CAP = 40
    # Whole-note ceiling, header included: a 128-char tool name must not push a
    # note past it (Gate 10).
    _NOTE_CHARS = 1000

    def _note_elided(self) -> Any:
        """The on_elide callback for one compaction: saves each body as a bounded
        working note tagged with the turn and what it was, returns the note id.
        None when there is no working memory to save into (a bare backend)."""
        try:
            from ...tools.context import ctx as _ctx
            working = _ctx().working
        except Exception:
            working = None
        if working is None:
            return None
        budget = [self._NOTE_CAP]

        def save(what: str, body: str, turn: str) -> tuple[int | None, str]:
            text = body.strip()
            if not text:
                return None, "nothing to save"   # no slot spent on an empty body
            if budget[0] <= 0:
                return None, "note cap for this compaction"
            head = f"[elided {what} · turn {turn}] "
            room = max(200, self._NOTE_CHARS - len(head))
            if len(text) > room:
                keep = room - 3
                text = text[: int(keep * 0.78)].rstrip() + "\n…\n" + text[-(keep - int(keep * 0.78)):].lstrip()
            try:
                note_id = working.note(head + text)
            except Exception as e:
                return None, f"the note could not be written: {type(e).__name__}"
            budget[0] -= 1
            return note_id, ""

        return save

    def _phase_reset(self) -> list[Event]:
        """A phase of the plan just finished (update_plan): compact now, in one
        step, so the next phase starts lean. PLAN.md and the phase summary carry
        the state; this is the planned reset the owner asked for, instead of the
        context filling up and compacting mid-work."""
        if not self._phase_reset_pending:
            return []
        self._phase_reset_pending = False
        if self._context_overflow == "error":
            return []
        before = self._ctx_fill()
        size, msg_size = self._calibrated_size()
        prior = list(self.messages)
        n = _compact_messages(self.messages, int(self._window() * 0.25), on_elide=self._note_elided(),
                              size=size, msg_size=msg_size)
        self._record_council_omissions(prior, self.messages)
        if not n:
            return []
        self._compacted()
        return [Event("system", f"Phase complete — compacted the conversation ({before:,} to about "
                                f"{size(self.messages):,} tokens) so the next phase starts "
                                "lean; PLAN.md carries the plan.")]

    def fresh_start(self) -> list[Event]:
        """The owner's Fresh start (DREAM-113, fix list #33): the conversation becomes one handoff now
        (core/handoff.py: the owner's requests and the last reply, the files written or edited this session,
        the open tasks). The existing compaction does it: _compact_messages to nothing saves what goes as
        working notes, as every compaction does; the stubs it leaves are then dropped for the handoff, and
        _compacted() marks the boundary -- the stale count goes, and a cache-sensitive engine's frozen head
        is refreshed there (DREAM-112). Between turns only: refused while a turn runs."""
        if self._turn_active:
            raise ValueError("Fresh start runs between turns only: let this turn finish (or stop it), then "
                             "start fresh.")
        if self._council_context is not None:
            raise ValueError("A Council handoff is waiting for your next message: send it, then start fresh.")
        if len(self.messages) < 2:
            return [Event("system", "Fresh start: nothing to hand off yet — the conversation is empty.")]
        parts = handoff.gather(_bound_context())          # the reads first: one that fails changes nothing
        before, count = self._ctx_fill(), len(self.messages) - 1
        size, msg_size = self._calibrated_size()
        prior = list(self.messages)
        save, notes = self._note_elided(), []

        def kept(what: str, body: str, turn: str) -> tuple[int | None, str]:
            note_id, why = save(what, body, turn)
            if note_id is not None:
                notes.append(note_id)
            return note_id, why

        elided = _compact_messages(self.messages, 0, on_elide=kept if save is not None else None,
                                   size=size, msg_size=msg_size)
        self._record_council_omissions(prior, self.messages)
        # The history about to go points at notes: this compaction's, earlier compactions' (in their stubs)
        # and an earlier handoff's. The new handoff names them all, so none becomes unreachable (DREAM-113 gate).
        pointed = sorted(set(self._handoff_notes) | handoff.note_refs(self.messages) | set(notes))
        self.messages[1:] = [{"role": "user", "name": "dream_fresh_start",
                              "content": handoff.compose(parts, self._file_ledger, pointed)}]
        self._handoff_notes = pointed
        self._snips.clear()
        self._compacted()
        after = self._ctx_fill()   # the rewritten history priced from scratch: the old count no longer anchors it
        if self.runtime_meter:
            self.runtime_meter.record("fresh_start", before=before, after=after, messages=count, elided=elided,
                                      notes=len(notes), files=len(self._file_ledger), tasks=len(parts.tasks))

        def n(k: int, what: str) -> str:
            return f"{k:,} {what}{'' if k == 1 else 's'}"

        return [Event("system", f"Fresh start: {n(count, 'message')} (about {before:,} tokens with the system prompt "
                                f"and tools) became one handoff; the next request starts at about {after:,} tokens. "
                                f"It carries your {n(len(parts.asked), 'request')}, Dream's last reply, "
                                f"{n(len(self._file_ledger), 'file')} written or edited and "
                                f"{n(len(parts.tasks), 'open task')}; earlier detail stays in this session's transcript"
                                + (f" and {n(len(notes), 'working note')}." if notes else "."))]

    def _maybe_compact(self) -> list[Event]:
        """Compact the history if it has crossed the threshold. Returns the
        events to surface — compaction is destructive, so it is never silent."""
        if self._context_overflow == "error":
            return []
        window = self._window()
        # Decided on the next prompt estimated high, so it fires before the prompt passes the line
        # (DREAM-112 gate); `fill`, the plain estimate, is what the owner is told.
        bound, fill = self._fill_bound(), self._ctx_fill()
        if bound <= window * compact_at(window):
            return []
        before = _est_tokens(self.messages)
        # Measured in the server's tokens and anchored to its last count, so the cut lands on its
        # target rather than a third below it (fix #65). Taken before snips move the history.
        size, msg_size = self._calibrated_size()
        target = int(window * compact_at(window) / 2)
        events = []
        # What the model itself marked as done goes first — whole exchanges,
        # chosen with judgment — before the blind stubbing below.
        snipped = self._execute_snips()
        if snipped:
            events.append(Event("system", f"snipped {snipped} message(s) the model had "
                                          f"registered as no longer needed"))
        # Halve past the trigger line, so one big tool result doesn't put us
        # straight back over it on the very next round. What goes is saved to
        # working notes first (Phase 10): the stub names the note.
        prior = list(self.messages)
        save = self._note_elided()
        # Aimed below the target by as much as the last landing came in above its projection (at most 2 % of
        # the target): a price that runs low the same way every time -- text in rounds with images priced at a
        # text ratio a little off -- is then paid for.
        aim = target - round(min(0.02 * target, self._landing_miss or 0))
        n = _compact_messages(self.messages, aim, on_elide=save, size=size, msg_size=msg_size)
        if n and (not self._template_seen or not self._text_learned):
            # Until a landing has shown what the engine's template costs (MiMo's is assumed), each message a cut
            # rewrites moves its landing by (1 - s) tokens for each token the real one costs more (a record costs
            # a + s x the template). Cut that much deeper for a template up to 16 tokens dearer, within 3 % of the
            # target, so a dearer one does not carry the landing past it. A screenshot session's first cuts may
            # rewrite only text, which shows nothing of the template; once a counted landing has (measured or not),
            # the landing feedback above covers it. While the text ratio has never been fitted on text alone, every
            # image round's split is a guess, and every cut keeps the reserve.
            live = {id(m) for m in self.messages}
            rewrote = sum(1 - s for k, (p, _, s, m) in self._msg_tokens.items() if k in live and p != _msg_parts(m))
            reserve = min(0.03 * target, 16 * rewrote)
            if reserve >= 1:
                n += _compact_messages(self.messages, int(aim - reserve), on_elide=save, size=size,
                                       msg_size=msg_size)
        self._record_council_omissions(prior, self.messages)
        if n or snipped:
            # The stale server count describes the OLD, larger history; keeping
            # it would pin _ctx_fill high and re-trigger compaction every round.
            self._compacted()
        after = _est_tokens(self.messages)
        projected = size(self.messages)
        if n or snipped:
            self._projected = projected       # the next count is this cut's landing (_record_calibration)
        if n:
            events.append(Event("system", f"compacted context: about {fill:,} to about {projected:,} "
                                          f"tokens ({n} messages elided)"))
            if self.runtime_meter:
                self.runtime_meter.record("compaction", before=before, after=after, elided=n,
                                          window=window, threshold=compact_at(window), fill=fill,
                                          bound=bound, target=target, projected=projected,
                                          text_ratio=round(self._text_ratio, 3),
                                          image_tokens=round(self._image_tokens),
                                          template_tokens=round(self._template, 1))
        if projected > window:
            # Nothing left to give: the irreducible history (system prompt, the
            # live turn) is bigger than the window. The request goes out anyway
            # — the server may still cope — but a turn that is about to fail
            # this way must SAY so, never fail mysteriously.
            events.append(Event("system", f"⚠ context {projected} tokens still exceeds "
                                          f"the {window}-token window after "
                                          "compacting — the server may refuse or "
                                          "truncate this turn. /new starts fresh."))
        return events

    def _compacted(self) -> None:
        """The lead history was rewritten from its start: the server's last count describes the old
        one. On a cache-sensitive engine this is also the moment the head's wake-up may change (fix
        #38): the history after the head is re-read anyway, and a changed wake-up adds only the head's
        tail and the tools to that."""
        self._last_prompt_tokens = 0
        if self._cache_sensitive():
            self._refresh_head()

    def _wake_inputs(self, context: Any) -> tuple[str | None, int | None]:
        """(project, wake-up token budget): what the engine built this session's wake-up with."""
        from ...memory import project as project_memory
        project = project_memory.project_key(context.workspace) if context.workspace else None
        return project, (self.profile.wake_tokens if self.profile is not None else None)

    def _refresh_head(self) -> bool:
        """Re-render the system prompt's wake-up (open work, top of mind, memory index, skills) from
        the store; True when it changed. What the model knows is from then on the state the new wake-up
        was built from. Needs the session's bound tool context and a prompt that build_system_prompt
        made; anything else is left as it is."""
        from .. import system_prompt
        context = _bound_context()
        head = self.messages[0].get("content") if self.messages and self.messages[0].get("role") == "system" else None
        if context is None or not isinstance(head, str) or system_prompt.WAKE_HEADER not in head:
            return False
        project, budget = self._wake_inputs(context)
        try:
            fresh = system_prompt.refresh_wake(head, context.store, context.session_id,
                                               max_tokens=budget, project=project)
        except Exception as exc:   # a store that cannot be read keeps the head it has, and says so
            if self.runtime_meter:
                self.runtime_meter.record("head_refresh_failed", error=type(exc).__name__)
            return False
        if fresh is None:
            return False
        self._live_told = fresh.live_state
        if fresh == head:
            return False
        self.messages[0] = {**self.messages[0], "content": fresh}
        return True

    def _live_state_sync(self) -> list[Event]:
        """Fix #38, before each new user message, on a cache-sensitive engine only: open work and
        top-of-mind memory are part of the system prompt's wake-up, and the engine must see that head
        unchanged. So it stays as built, and a short note -- placed just before the user's message, at
        the tail -- names what changed since the state the head was built from (SystemPrompt.live_state,
        all of it, whatever the wake budget let the text show) and since the last note. Any other
        backend is left exactly as it was: no note, and no rewrite of its prompt (DREAM-112 gate: a
        hosted provider's prefix cache is worth keeping too)."""
        from .. import system_prompt
        context = _bound_context()
        head = self.messages[0].get("content") if self.messages and self.messages[0].get("role") == "system" else None
        if (context is None or not self._cache_sensitive() or not isinstance(head, str)
                or system_prompt.WAKE_HEADER not in head):
            return []
        project, _ = self._wake_inputs(context)
        try:
            now = system_prompt.live_state(context.store, project)
        except Exception as exc:
            return [Event("system", f"Could not read open work and memory for this turn ({type(exc).__name__}); "
                                    "the model sees them as they were when the session started.")]
        # A prompt that carries no state (not built by build_system_prompt) starts from the first turn's.
        told = self._live_told if self._live_told is not None else getattr(head, "live_state", None) or now
        note = system_prompt.live_state_note(told, now)
        self._live_told = now
        if note:
            self.messages.append({"role": "user", "name": "dream_live_state", "content": note})
            if self.runtime_meter:
                self.runtime_meter.record("live_state_note", lines=note.count("\n"))
        return []

    @staticmethod
    def _head_hash(payload: dict[str, Any]) -> str:
        """12 hex of a request's head: its system text and its tools, which render first (fix #38).
        A prefix-cached engine reuses nothing past the first byte that differs there."""
        messages = payload.get("messages") or []
        system = messages[0].get("content", "") if messages and messages[0].get("role") == "system" else ""
        blob = json.dumps([system, payload.get("tools") or []], sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:12]

    def _prompt_cache_slots(self) -> tuple[int | None, str]:
        """How many conversations the local engine keeps cached, and who says so (fix #71): its
        /props `prompt_cache_slots`, else _CACHE_SLOT_DEFAULTS for a MachX model, else unknown."""
        props = self._server_props if self.model == self._server_props_model else None
        reported = props.get("prompt_cache_slots") if isinstance(props, dict) else None
        if type(reported) is int and reported >= 1:
            return reported, "the engine's /props"
        if getattr(self.provider, "key", "") == "machx":
            capabilities = self._local_capabilities if self.model == self._capabilities_model else {}
            architecture = capabilities.get("architecture") if isinstance(capabilities, dict) else None
            for arch, fragment, slots in _CACHE_SLOT_DEFAULTS:
                if architecture == arch or fragment in str(self.model or "").lower():
                    return slots, f"Dream's default for {arch}"
        return None, "unknown"

    def _round_cost(self, calls: list[dict[str, Any]]) -> float:
        """What a tool round spends of _MAX_TOOL_ROUNDS (fix #17): half on a local engine when every
        call in it is a look (_read_only_call), else one. Live 2026-09-19: a turn of ~100 cheap
        eval_js/see/screenshot probes died at the limit mid-sweep."""
        if calls and self._cache_sensitive() and all(_read_only_call(c.get("name") or "", c.get("args"))
                                                     for c in calls):
            return 0.5
        return 1.0

    def _repair_dangling(self) -> None:
        """Fill in tool results that never landed.

        The assistant message carries ALL of a round's tool_calls at once, but
        results are appended one at a time. A consumer that walks away in
        between (the engine's tool-budget stop, a Ctrl-C while a handler runs)
        leaves a tool_call with no matching tool message: strict servers 400 on
        that history forever after, local models hallucinate the missing result.
        Stubs go at the end of their own round, never after a later message.
        """
        out: list[dict[str, Any]] = []
        i, n = 0, len(self.messages)
        while i < n:
            m = self.messages[i]
            out.append(m)
            i += 1
            calls = m.get("tool_calls") if m.get("role") == "assistant" else None
            if not calls:
                continue
            seen = set()
            while i < n and self.messages[i].get("role") == "tool":
                seen.add(self.messages[i].get("tool_call_id"))
                out.append(self.messages[i])
                i += 1
            for tc in calls:
                if tc.get("id") and tc["id"] not in seen:
                    out.append({"role": "tool", "tool_call_id": tc["id"],
                                "content": _INTERRUPTED})
        self.messages = out

    # --- tool schemas ---------------------------------------------------------

    def _pinned_tools(self) -> frozenset[str]:
        """Tool names deferral may never take away.

        There are no SDK builtins here, so the native file/shell tools ARE this
        loop's hands and the memory tools are its own mind — the two it must not
        spend a round trip reaching. `task` is the only way to delegate at all.
        Membership comes from where a tool is DEFINED, so a tool added to those
        modules is pinned without anyone having to update a list here.

        Deliberately not policy's MEMORY class: that one answers "may this run
        unprompted", and it counts skill_save/skill_patch — the two widest
        schemas Dream owns, and ones the loop can afford to look up.
        """
        if self.profile and self.profile.prompt_style == "compact":
            # Everything else remains searchable through tool_schema. Pinning
            # all memory tools alone consumed 16% of a 16K window previously.
            return frozenset({"read_file", "write_file", "run_bash", "str_replace_edit"})
        from ...tools import files, memory_tools, native, notes

        core = {native.__name__, memory_tools.__name__, notes.__name__}
        # files.py names its own hand (str_replace_edit); its other tools may
        # defer — pinning them all put the pinned set alone over the 10% budget
        # at a 16K window.
        hands = {t.name for t in files.HANDS}
        pins = {t.name for t in self.tools
                if getattr(t.handler, "__module__", "") in core or t.name in hands}
        if self._subagents:
            pins.add("task")
        return frozenset(pins)

    def _tool_uses(self) -> Callable[[str], int] | None:
        """Usage counts for the schema ranking — a tool Dream actually reaches
        for earns its width in the window. None when no store is behind this
        backend (a MoE advisor, tests): the ranking then falls back to
        cheapest-first, a worse order rather than a broken one. Queried per name
        on demand, so a toolset that already fits costs no store reads at all.
        """
        from ...tools.context import ctx

        try:
            store = ctx().store
        except RuntimeError:
            return None
        if store is None:
            return None
        counts: dict[str, int] = {}

        def uses(name: str) -> int:
            if name not in counts:
                counts[name] = int((store.tool_stat(name) or {}).get("use_count") or 0)
            return counts[name]

        return uses

    def prepare_turn(self, tool_names: Iterable[str]) -> None:
        """Prioritize up to eight known tools for the next user turn only.

        These are budgeted preferences, not permission grants or schema pins.
        A new preparation replaces the previous one; caller order bounds the set.
        """
        selected = []
        for name in tool_names:
            if name in self.tools_by_name and name not in selected:
                selected.append(name)
                if len(selected) == 8:
                    break
        self._prepared_tools = tuple(selected)

    def _request_tools(self) -> list[dict[str, Any]]:
        """The tools array for this request. Every schema costs window on every
        round — and the self-built set only grows — so past a fraction of the
        window the rarely-used ones become a one-line catalog behind a lookup
        tool. A toolset that already fits goes out untouched."""
        window = self._window()
        from ... import extensions
        active_names = {tool.name for tool in extensions.filter_tools(self.tools)}
        available_schemas = [s for s in self.tool_schemas
                             if s["function"]["name"] in active_names
                             or s["function"]["name"] in {"task", "fork_verifier_agent"}]
        allowed_lead = True
        stable = self._stable_tool_list()
        # Revealing `snip` partway through a session rewrites the prompt's start --
        # tools render before the messages, so appending one shifts every message
        # token after it. On a local server that re-reads the WHOLE conversation
        # (live 2026-09-20: the third user message flipped it on and the next request
        # re-read 42,231 tokens in 155 s). Reserving it from turn one instead would
        # cost a tool slot in every constrained window, so a local backend simply does
        # not offer it: its history pressure is handled by compaction already.
        snip = (None if stable
                else self._snip_schema() if len(self._user_ids()) >= _SNIP_FROM_TURN else None)
        if snip is not None:
            # Reserve the control tool's width so the budget still holds.
            window = max(1024, window - int(tool_budget_schemas.measure([snip])
                                            / tool_budget_schemas.DEFAULT_BUDGET_FRAC))
        schema_fraction = self.profile.schema_fraction if self.profile else None
        if stable:
            # Chosen once per session from the pins and historical use only: per-turn
            # relevance and revealed tools would change the prompt's start.
            stable_key = (window, json.dumps(available_schemas, sort_keys=True), bool(snip), schema_fraction)
            if self._stable_tools is not None and self._stable_tools[0] == stable_key:
                self._deferred_now = self._stable_tools[2]
                return deepcopy(self._stable_tools[1])
        cache_key = (window, json.dumps(available_schemas, sort_keys=True),
                     self._pinned | self._revealed, self._turn_tools, bool(snip), schema_fraction)
        if not stable and self._schema_cache is not None and self._schema_cache[0] == cache_key:
            self._deferred_now = self._schema_cache[2]
            return deepcopy(self._schema_cache[1])
        if self._schema_usage is None:
            self._schema_usage = self._tool_uses()
        usage = self._schema_usage
        if self._turn_tools and not stable:
            counts = {s["function"]["name"]: usage(s["function"]["name"]) if usage else 0
                      for s in available_schemas}
            # Rank relevant tools ahead of historical usage without making them
            # unconditional pins. The selector still rejects schemas that do
            # not fit, including the cost of its deferred-tool catalog.
            bonus = (max(counts.values(), default=0) + 1) * (tool_budget_schemas.measure(available_schemas) + 1)
            usage = lambda name: counts.get(name, 0) + (bonus if name in self._turn_tools else 0)
        sent, deferred = tool_budget_schemas.select(
            available_schemas,
            window,
            always=self._pinned if stable else self._pinned | self._revealed,
            usage=usage,
            budget_frac=self.profile.schema_fraction if self.profile else tool_budget_schemas.DEFAULT_BUDGET_FRAC,
        )
        # What `tool_schema(search=...)` searches over: the names this round
        # deferred, remembered because the handler runs after the send.
        self._deferred_now = frozenset(deferred)
        if snip is not None:
            sent = sent + [snip]
        if self.profile is None and self._subagents and "verifier" in self._subagents and allowed_lead:
            sent = sent + [self._verifier_schema()]
        if stable:
            self._stable_tools = (stable_key, deepcopy(sent), self._deferred_now)
        self._schema_cache = (cache_key, deepcopy(sent), self._deferred_now)
        return deepcopy(sent)

    def _admission_limits(self, messages):
        """Shared output/window arithmetic for preflight, projection and admission."""
        window = self._window()
        fill = int(_est_tokens(messages) * self._calib_msgs)
        output = min(config.MAX_OUTPUT_TOKENS,
                     self.profile.output_reserve(window) if self.profile else self._max_tokens(fill))
        if "max_tokens" in self._local_options:
            output = self._max_tokens(fill)
        if self._active_performance:
            output = min(output, self._active_performance["output_tokens"])
        return window, output

    def preflight_council_context(self, transfer):
        from ..council_context import required_messages
        messages = [deepcopy(self.messages[0]), *required_messages(transfer),
                    {'role': 'user', 'content': 'continue\n\n[id:m0001]'}]
        window, output = self._admission_limits(messages)
        try:
            admit(messages, self._request_tools(), window, output)
        except ContextOverflow as exc:
            raise ContextOverflow('Required prior user input cannot fit this Council handoff. '
                                  'Choose a larger model/context, or explicitly start a new session '
                                  'with a revised request. ' + str(exc)) from exc

    def prepare_council_context(self, transfer):
        self._council_context = transfer

    def acknowledge_council_context(self):
        for message in self.messages:
            if message.get('name') == 'dream_handoff_user':
                message['name'] = 'dream_prior_user'
        self._council_context = None

    def _stage_council_context(self):
        from ..council_context import Transfer, required_messages, project
        transfer = self._council_context
        if transfer is None:
            return
        for record, message in zip(transfer.required, required_messages(transfer)):
            key = (record.session, record.turn)
            if key not in self._council_required_sources:
                self.messages.insert(len(self.messages) - 1, message)
                self._council_required_sources.add(key)
        history = tuple(r for r in transfer.history if (r.session, r.turn) not in self._council_optional_sources)
        consultations = tuple(r for r in transfer.consultations if (r.session, r.turn) not in self._council_optional_sources)
        schemas = self._request_tools()
        def fits(optional):
            scratch = self.messages[:-1] + optional + self.messages[-1:]
            window, output = self._admission_limits(scratch)
            # Projection does not compact or alter already-staged history,
            # including under the explicit error policy.
            return account(scratch, schemas, window, output).remaining >= 0
        optional, notices = project(Transfer(history=history, consultations=consultations), fits)
        self.messages[-1:-1] = optional
        self._council_notices.extend(notices)
        self._council_optional_sources.update((r.session, r.turn) for r in (*history, *consultations))

    def _context_notices(self):
        notices, self._council_notices = self._council_notices, []
        return [Event('system', notice) for notice in notices]

    def _record_council_omissions(self, before, after):
        from ..council_context import omission_notice
        retained = {id(m) for m in after}
        for message in before:
            if (message.get('name') == 'dream_council_context' and not message.get('tool_calls')
                    and id(message) not in retained):
                self._council_notices.append(omission_notice(message))

    def _calibrated_counter(self) -> tuple[Callable[[Any], int], str | None]:
        """The size estimate scaled by what the server counted last time."""
        from ..context_budget import estimate
        if self._calib == 1.0:
            return estimate, None
        k = self._calib
        return (lambda value: math.ceil(estimate(value) * k),
                f"estimate x {k:.2f}, calibrated on the server's count of the previous request")

    def _admit_request(self, messages: list[dict], schemas: list[dict], *, lead: bool = True):
        """One admission gate for lead, delegated and recovery requests."""
        window, output = self._admission_limits(messages)
        counter, method = self._calibrated_counter()
        report = account(messages, schemas, window, output, counter=counter, method=method)
        elided = 0
        if report.remaining < 0 and self._context_overflow == "compact":
            # Preserve the system/current user messages. Only prior exchanges
            # can be elided, and lead elisions retain recovery notes. One big
            # step (to half the compaction line), never "just enough": trimming
            # a little on every request changed the history's start each round
            # and re-read all of it every time (Dream fix #2).
            prior = list(messages)
            target = min(int(window * compact_at(window) / 2), window - report.tools - report.output - report.margin)
            if lead and messages is self.messages:   # in the server's tokens, as _maybe_compact (fix #65)
                size, msg_size = self._calibrated_size()
                elided = _compact_messages(messages, max(0, target), on_elide=self._note_elided(),
                                           size=size, msg_size=msg_size)
            else:
                elided = _compact_messages(messages, max(0, int(target / self._calib_msgs)),
                                           on_elide=self._note_elided() if lead else None)
            self._record_council_omissions(prior, messages)
            if elided and lead and messages is self.messages:
                self._compacted()   # the count described the larger history
        try:
            report = admit(messages, schemas, window, output, counter=counter, method=method)
        except ContextOverflow as exc:
            if lead:
                self.context_report = account(messages, schemas, window, output, counter=counter, method=method).as_dict()
                self.context_report["elided_messages"] = elided
            if self.runtime_meter is not None:
                self.runtime_meter.record("context_refused", window=window, phase="lead" if lead else "delegated/recovery")
            if any(m.get('name') == 'dream_handoff_user' for m in messages):
                raise ContextOverflow('Required prior user input plus the current request cannot fit. '
                                      'Choose a larger model/context, or explicitly start a new session '
                                      'with a revised request. ' + str(exc)) from exc
            raise
        if lead:
            self.context_report = report.as_dict()
            self.context_report["elided_messages"] = elided
        if elided and self.runtime_meter is not None:
            self.runtime_meter.record("context_compacted", messages=elided, phase="lead" if lead else "delegated/recovery")
        return report

    def _lookup_tool_schema(self, name: str, search: str = "") -> tuple[str, bool]:
        """Execute the lookup hatch: hand back a full schema and keep that tool
        in the window for this turn, so the model can call it straight away.

        A name that was never deferred is answered rather than corrected — the
        model asked for parameters, and it gets them. An unknown one says so and
        points at the direct call, since a tool it can name is callable whether
        or not its schema is loaded.

        With `search` instead of a name, it answers with the matching tools'
        catalog lines. Phase 14 bounded the catalog to the budget, so at a small
        window most deferred tools have no line in it; this is how the model
        finds one it cannot see.
        """
        if search.strip() and not name.strip():
            names = getattr(self, "_deferred_now", frozenset())
            deferred = [s for s in self.tool_schemas if s["function"]["name"] in names]
            hits = tool_budget_schemas.search_catalog(deferred, search)
            if not hits:
                return (f"No not-loaded tool matches {search!r}. The loaded tools are "
                        "already in your list."), False
            return ("Matching tools (call tool_schema with one of these names for its "
                    "parameters):\n" + "\n".join(hits)), False
        schema = next((s for s in self.tool_schemas
                       if s["function"]["name"].lower() == name.lower()), None)
        if schema is None:
            return f"Error: no tool named '{name}'. Call the tool you want directly.", True
        self._revealed.add(schema["function"]["name"])
        return json.dumps(schema["function"], ensure_ascii=False), False

    # --- transport ------------------------------------------------------------

    def _request_generation(self):
        current = _REQUEST_INTERRUPTION.get()
        return current[1] if current and current[0] is self._interrupts else self._interrupts.generation

    def _check_interruption(self, generation):
        if generation != self._interrupts.generation:
            raise asyncio.CancelledError("HTTP request interrupted.")

    def _configure_interrupts(self):
        if isinstance(self._client, httpx.AsyncClient):
            hooks = self._client.event_hooks['response']
            if self._guard_response not in hooks:
                hooks.append(self._guard_response)

    async def _guard_response(self, response):
        operation = _IO_OPERATION.get()
        if operation is None or operation[0] is not self._interrupts:
            return
        _, scope, generation = operation
        # Headers are now owned by HTTPX. Its response reader closes at natural
        # EOF, so stop timing the encompassing send and guard raw reads instead.
        self._interrupts.active.discard(scope)
        if not isinstance(response.stream, _InterruptibleBytes):
            response.stream = _InterruptibleBytes(response.stream, self, generation)

    async def _interruptible_io(self, operation, *, generation=None, check_result=True):
        # AnyIO cancellation honors HTTPcore's nested cleanup shields. Direct
        # Task.cancel (including asyncio.timeout) bypasses those shields and can
        # abort transport cleanup inside a failed read before it settles.
        scope = CancelScope()
        generation = self._request_generation() if generation is None else generation
        self._check_interruption(generation)
        await _work_checkpoint()
        token = _IO_OPERATION.set((self._interrupts, scope, generation))
        try:
            with scope:
                self._interrupts.active.add(scope)
                try:
                    value = await operation()
                    if check_result:
                        self._check_interruption(generation)
                        await _work_checkpoint()
                    return value
                finally:
                    self._interrupts.active.discard(scope)
        finally:
            _IO_OPERATION.reset(token)
        # The scope suppresses only its own cancellation. Preserve the backend
        # interruption contract after exiting it, outside level cancellation.
        raise asyncio.CancelledError("HTTP request interrupted.")

    def _coordinator(self):
        from ..inference_coordination import EndpointCoordinator, local_endpoint
        endpoint = local_endpoint(getattr(self.provider, "base_url", ""))
        if endpoint is None:
            return None
        override = getattr(self, "_coordination_override", None)
        if override is not None:
            return override
        cached = getattr(self, "_endpoint_coordinator", None)
        if cached is None or cached[0] != endpoint:
            from ...local import machx
            cached = (endpoint, EndpointCoordinator(endpoint, server_started_at=machx.server_started_at))
            self._endpoint_coordinator = cached
        return cached[1]

    def coordination_status(self) -> dict:
        coordinator = self._coordinator()
        return coordinator.status() if coordinator else {"enabled": False}

    def reconcile_local_request(self, request_id: str, *, confirmed_idle: bool):
        from ..inference_coordination import CoordinationError
        coordinator = self._coordinator()
        if coordinator is None:
            raise ValueError("This provider does not use local request coordination.")
        try:
            return coordinator.reconcile(request_id, confirmed_idle=confirmed_idle)
        except CoordinationError as exc:
            raise ValueError(str(exc)) from exc

    def _lease_record(self, coordinator) -> dict | None:
        from ..inference_coordination import CoordinationError
        try:
            return coordinator.status()
        except CoordinationError:
            return None

    def _lease_notice(self, text: str) -> None:
        """A system line about a wait for the local lease (the DREAM-110 gate: up to 20 s of silence after
        a Stop reads as a hang). It goes out through the Engine's event funnel, the one background work
        uses. Display only: a subscriber that fails is logged, and the request goes on."""
        emit = getattr(self, '_background_emit', None)
        if emit is None:
            return
        try:
            emit(Event("system", text))
        except Exception:
            _LOG.exception("Lease wait notice failed")

    async def _enter_lease(self, coordinator, generation):
        """Take the local lease. A record a cut-off request left behind -- "uncertain" after a Stop or
        a disconnect, or "running" for an owner that is gone -- no longer refuses at once (fixes #72,
        #34): the request waits up to _LEASE_WAIT_S for the engine to report nothing in flight, clears
        exactly that record and goes on. A lease a live Dream request holds is waited for as before.
        When a request has to wait at all, the owner sees one line as the wait starts and one as it
        ends: sent, given up, or stopped by the owner (DREAM-113); a request that never waits shows neither."""
        from ..inference_coordination import CoordinationError
        started = deadline = waiting = None

        def announce(text):
            nonlocal waiting
            if waiting is None:
                waiting = time.monotonic()
                self._lease_notice(text)

        def coordination_event(event):
            if event.get("state") == "waiting":
                announce("Waiting for another Dream request on this local engine to finish…")

        def refusal(refused, record, seen, waited):
            if waiting is not None:
                self._lease_notice(f"Stopped waiting after {time.monotonic() - waiting:.0f} s; "
                                   "this request was not sent.")
            return _RequestFailed(self._lease_refusal(refused, record, seen, waited), code="lease_refused")

        try:
            while True:
                lease = coordinator.request(on_event=coordination_event)
                try:
                    await self._interruptible_io(lease.__aenter__, generation=generation, check_result=False)
                except CoordinationError as exc:
                    refused = exc
                else:
                    if waiting is not None:
                        self._lease_notice(f"The local engine is free after {time.monotonic() - waiting:.0f} s; "
                                           "sending this request.")
                    return lease
                record = self._lease_record(coordinator)
                if record is None or not _lease_unsettled(record):
                    raise refusal(refused, record, None, 0.0) from refused
                if started is None:
                    started = time.monotonic()
                    deadline = started + _LEASE_WAIT_S
                elif time.monotonic() >= deadline:        # cleared once, and another cut-off record came
                    raise refusal(refused, record, None, 0.0) from refused
                seen = await self._settle_lease(coordinator, deadline, generation, started, announce)
                if seen is not None:
                    raise refusal(refused, self._lease_record(coordinator) or record, seen,
                                  time.monotonic() - started) from refused
        except asyncio.CancelledError:
            # A Stop during the wait skipped both endings above: the owner saw the wait start and never end.
            if waiting is not None:
                self._lease_notice("Stopped by you while waiting; this request was not sent.")
            raise

    async def _settle_lease(self, coordinator, deadline, generation, started, announce) -> _EngineReport | None:
        """Clear a cut-off request's lease record once the engine reports nothing in flight. None: try
        the lease again (the record is gone, or a live request holds it now). Otherwise the engine's
        last report, which keeps the refusal. Only reconcile() clears, so only under the lease's lock
        and only for the exact request id read here."""
        from ..inference_coordination import CoordinationError
        while True:
            record = self._lease_record(coordinator)
            if record is None or not _lease_unsettled(record):
                return None
            seen = await self._engine_activity(generation)
            if seen.kind == "idle":
                try:
                    coordinator.reconcile(record["request_id"], confirmed_idle=True)
                except CoordinationError:
                    pass            # it changed under us, or a live request holds the lock: look again
                else:
                    if self.runtime_meter:
                        self.runtime_meter.record(
                            "lease_reconciled", request_id=record["request_id"], was=record["state"],
                            pid=record.get("pid"), cut_off_at=record.get("updated_at") or record.get("started_at"),
                            waited_s=round(time.monotonic() - started, 2), inflight=seen.inflight, queued=seen.queued)
                    return None
            elif seen.kind != "busy":
                return seen
            if time.monotonic() >= deadline:
                return seen
            if seen.kind == "busy":
                announce(f"Waiting up to {_LEASE_WAIT_S:g} s for the local engine to finish the interrupted "
                         f"request {_lease_holder(record)}; it {seen.said}…")
            await self._interruptible_io(lambda: asyncio.sleep(_LEASE_POLL_S), generation=generation)

    async def _engine_activity(self, generation) -> _EngineReport:
        """What the local server's /health says about work in flight. Only the engine's own report
        counts as idle: HTTP 200, status "ok", inflight 0 and queued 0. ie serve frees a request's
        admission slot (inflight) only after its generation has returned (openai_server.cpp)."""
        client = self._client
        if client is None or not hasattr(client, "get"):
            return _EngineReport("unchecked", "cannot be asked whether it is idle")
        root = self.provider.base_url.rsplit("/v1", 1)[0]
        try:
            resp = await self._interruptible_io(lambda: client.get(f"{root}/health", timeout=3.0),
                                                generation=generation)
            data = resp.json()
        except httpx.HTTPError as exc:
            return _EngineReport("unreachable", f"did not answer /health ({type(exc).__name__})")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _EngineReport("unreachable", "did not answer /health with a readable report")
        status, inflight, queued = data.get("status"), data.get("inflight"), data.get("queued")
        if resp.status_code == 200 and status == "ok":
            if type(inflight) is not int or type(queued) is not int:
                return _EngineReport("unreported", "does not report requests in flight on /health")
            if inflight == 0 and queued == 0:
                return _EngineReport("idle", "reports nothing in flight", 0, 0)
            return _EngineReport("busy", f"still reports {inflight} request{'' if inflight == 1 else 's'} "
                                         f"in flight and {queued} queued", inflight, queued)
        if isinstance(status, str) and status:
            reason = f" ({str(data['reason'])[:200]})" if data.get("reason") else ""
            return _EngineReport("unhealthy", f"reports it is {status[:40]}{reason}")
        return _EngineReport("unreachable", f"did not answer /health with a readable report (HTTP {resp.status_code})")

    def _lease_refusal(self, refused, record, seen: _EngineReport | None, waited: float) -> str:
        """Say what holds the lease and what to do (fix #72)."""
        if seen is None:
            if record and record.get("state") == "running" and _lease_owner_alive(record):
                return (f"{refused} The lease is held by Dream request {_lease_holder(record)}: "
                        "let it finish or stop it, then send your message again.")
            return str(refused)
        parsed = urlsplit(self.provider.base_url)
        where = f"the local engine at {parsed.hostname}:{parsed.port}"
        after = f" after waiting {waited:.0f} s" if seen.kind in ("busy", "idle") else ""
        action = {
            "busy": ("Wait for the engine to finish, then send your message again; once you have checked "
                     "that the engine is idle you can also confirm it in Controls."),
            "idle": "Send your message again, or confirm the engine idle in Controls.",
            "unhealthy": "Restart the engine: pick the model again in the model picker (or restart Dream).",
            "unreachable": ("If the engine stopped, load the model again; if it is running and idle, "
                            "confirm it in Controls."),
        }.get(seen.kind, "Once you have checked that the server is idle, confirm it in Controls.")
        return (f"The previous local request {_lease_holder(record)} was cut off and its outcome is "
                f"still uncertain; {where} {seen.said}{after}, so this request was not sent. {action}")

    @asynccontextmanager
    async def _request_coordination(self):
        from ..inference_coordination import CoordinationError
        coordinator = self._coordinator()
        if coordinator is None:
            yield
            return
        completed_error = None
        try:
            generation = self._request_generation()
            lease = await self._enter_lease(coordinator, generation)
            exit_error = (None, None, None)
            try:
                self._check_interruption(generation)
                try:
                    yield
                except (_RequestFailed, _ServerGenerationError) as exc:
                    # Explicit completed rejection, unlike cancellation/uncertainty.
                    completed_error = exc
            except BaseException:
                exit_error = sys.exc_info()
                raise
            finally:
                await lease.__aexit__(*exit_error)
        except CoordinationError as exc:
            raise _RequestFailed(str(exc), code=getattr(exc, "code", None) or "local_lease",
                                 message=getattr(exc, "message", None)) from exc
        if completed_error is not None:
            raise completed_error

    @asynccontextmanager
    async def _stream_with_retry(self, payload: dict[str, Any]):
        from ..inference_coordination import CompletionStream, CoordinationError, StreamProtocolError
        generation = self._request_generation()
        try:
            async with self._request_coordination():
                async with self._stream_uncoordinated(payload) as response:
                    stream = CompletionStream(response)
                    yield stream
                    self._check_interruption(generation)
                    if not stream.complete:
                        if self._coordinator() is not None:
                            error = CoordinationError("Local stream ended without a completion signal; check server idle in Controls.")
                            error.code = "incomplete_stream"
                            raise error
                        raise _RequestFailed("Stream ended without a completion signal. No collected tool calls ran; the request was not replayed.",
                                             code="incomplete_stream")
        except StreamProtocolError as exc:
            # Propagate through the local lease first so uncertainty stays fenced.
            raise _RequestFailed(str(exc), code="stream_protocol_error") from exc

    @asynccontextmanager
    async def _stream_uncoordinated(self, payload: dict[str, Any]):
        """Open the SSE stream, retrying transient failures. Retry happens only
        while opening — once the body is being consumed a retry would duplicate
        output, so failures there propagate untouched."""
        self._configure_interrupts()
        generation = self._request_generation()
        last, wait = "", None
        code = message = None
        for attempt in range(_RETRY_ATTEMPTS):
            if attempt:
                await self._interruptible_io(lambda: asyncio.sleep(_retry_delay(attempt - 1, wait)), generation=generation)
            wait = None
            ctx = self._client.stream("POST", "/chat/completions", json=payload)
            try:
                resp = await self._interruptible_io(ctx.__aenter__, generation=generation, check_result=False)
            except httpx.HTTPError as e:
                if self._coordinator() is not None and not isinstance(
                        e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                    raise
                last = f"request failed: {type(e).__name__}: {e}"
                code, message = "transport_error", None
                continue
            if resp.status_code == 200:
                response = _InterruptibleResponse(resp, self, generation)
                try:
                    self._check_interruption(generation)
                    yield response
                finally:
                    try:
                        await response.close_lines()
                    finally:
                        await ctx.__aexit__(None, None, None)
                return
            try:
                if isinstance(getattr(resp, "stream", None), _InterruptibleBytes):
                    self._check_interruption(generation)
                    raw = await resp.aread()
                    self._check_interruption(generation)
                else:
                    raw = await self._interruptible_io(resp.aread, generation=generation)
                body = raw.decode("utf-8", "replace")
            finally:
                await ctx.__aexit__(None, None, None)
            last = f"HTTP {resp.status_code}: {body[:400]}"
            code, message = _http_error_fields(resp.status_code, body)
            wait = _retry_after(resp)
            if self._coordinator() is not None and (resp.status_code == 408 or resp.status_code >= 500):
                raise _uncertain_local_http(resp.status_code, code, message)
            if not _is_retryable(resp.status_code):
                break
        raise _RequestFailed(last, code=code, message=message)

    async def _post_with_retry(self, payload: dict[str, Any]) -> Any:
        measurement = self._measurement_request(payload, phase="delegated") if self.turn_timing else None
        started = time.monotonic()
        response = None
        try:
            async with self._request_coordination():
                response = await self._post_retry(payload)
                return response
        except _ServerGenerationError as exc:
            notice = self._observe_image_rejection(exc)
            if notice:
                exc.args = (f'{exc}{notice}',)
            await self._observe_engine_fault(exc)
            raise
        finally:
            if self.turn_timing:
                try:
                    data = response.json() if response is not None else {}
                except (ValueError, TypeError):
                    data = {}
                data = data if isinstance(data, dict) else {}
                self.turn_timing.request(time.monotonic() - started,
                    usage=data.get("usage"), server_timings=data.get("timings"),
                    schema_fingerprint=self._schema_fingerprint(payload.get("tools", [])),
                    configuration=measurement)

    @staticmethod
    def _schema_fingerprint(schemas) -> str:
        return hashlib.sha256(json.dumps(schemas, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()

    async def _post_retry(self, payload: dict[str, Any]) -> Any:
        """The subagent's non-streaming POST, same retry policy. Non-streaming,
        so every attempt is a clean do-over."""
        self._configure_interrupts()
        generation = self._request_generation()
        last, wait = "", None
        code = message = None
        for attempt in range(_RETRY_ATTEMPTS):
            if attempt:
                await self._interruptible_io(lambda: asyncio.sleep(_retry_delay(attempt - 1, wait)), generation=generation)
            wait = None
            try:
                resp = await self._interruptible_io(lambda: self._client.post("/chat/completions", json=payload), generation=generation)
            except httpx.HTTPError as e:
                if self._coordinator() is not None and not isinstance(
                        e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                    raise
                last = f"request failed: {type(e).__name__}: {e}"
                code, message = "transport_error", None
                continue
            if resp.status_code == 200:
                _raise_server_error(resp.json())
                return resp
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
            code, message = _http_error_fields(resp.status_code, resp.text)
            wait = _retry_after(resp)
            if self._coordinator() is not None and (resp.status_code == 408 or resp.status_code >= 500):
                raise _uncertain_local_http(resp.status_code, code, message)
            if not _is_retryable(resp.status_code):
                break
        raise _RequestFailed(last, code=code, message=message)

    async def _salvage(self, why: str) -> str:
        with self._phase("recovery"):
            return await self._salvage_reply(why)

    async def _salvage_reply(self, why: str) -> str:
        """One last generation, with tools taken away, so a turn that ended badly
        still says what it learned.

        The failure this exists for, observed: a local model ran eleven useful
        commands — open ports, running containers, interfaces, sudo state — then
        locked onto a twelfth that returned nothing, repeated it until the loop guard
        stopped the turn, and Dream reported "loop guard: ended the turn" and threw
        every one of those results away. The work was done. Nobody got to see it.

        No tool call in this reply ever runs: it is one non-streaming call whose text
        alone is used, so the loop that got here cannot resume. It is capped, and a
        failure here is silent — salvage must never become a second way to fail.

        Fix #38: on a cache-sensitive engine the payload keeps the lead's own tools
        (tool_choice "none"), so the prompt's head is the lead's and the engine
        extends its cached conversation. Without them it re-read all of it (live
        2026-09-21 00:58: 33,077 tokens, 0 cached, at the round limit). The engine
        ignores tool_choice, so a reply that is only a tool call is asked for again
        without tools, as every salvage was before. Elsewhere there are no tools.
        """
        ask = (
            "STOP. Do not call any more tools — none will run for this reply.\n\n"
            f"This turn is ending early: {why}\n\n"
            "Using ONLY what you already found above, answer the user's request now. "
            "Report what you learned, what it means, and what you were unable to "
            "determine. If a tool result was empty or failed, say so and say what "
            "you'd try next — do not pretend it succeeded, and do not apologise at "
            "length. Partial findings are worth far more than nothing."
        )
        payload = {
            "model": self.model,
            # Scratch: the instruction is scaffolding for this one call and is NOT
            # kept, or every later turn would carry a "stop calling tools" order.
            "messages": deepcopy(self.messages) + [{"role": "user", "name": "dream_recovery_instruction", "content": ask}],
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": min(self._max_tokens(self._ctx_fill()), _SALVAGE_MAX_TOKENS),
            **self._sampling,
        }
        if self._cache_sensitive():
            payload["tools"], payload["tool_choice"] = self._request_tools(), "none"
        try:
            if self.runtime_meter is not None:
                self.runtime_meter.check()
            if self.profile or self._local_options or self.capability_status()['context_tokens']['known']:
                payload["max_tokens"] = min(payload["max_tokens"], self._admit_request(
                    payload["messages"], payload.get("tools", []), lead=False).output)
            while True:
                resp = await self._post_with_retry(payload)
                data = resp.json()
                usage = data.get("usage") or {}
                if self.runtime_meter is not None:
                    self.runtime_meter.usage({**usage, "head_hash": self._head_hash(payload)}, phase="recovery")
                for key in self._delegated_usage:
                    self._delegated_usage[key] += int(usage.get(key) or 0)
                msg = data["choices"][0]["message"]
                text = (msg.get("content") or "").strip()
                if text or not msg.get("tool_calls") or "tools" not in payload:
                    return text
                del payload["tools"], payload["tool_choice"]
        except Exception:
            return ""

    async def _exec_tool(
        self, name: str, args: dict[str, Any], allowed: set[str] | None = None
    ) -> tuple[str, bool]:
        generation = self._request_generation()
        self._check_interruption(generation)
        await _work_checkpoint()
        # A model that has used `run_bash` correctly for nineteen calls can still
        # reach for the name it knows from elsewhere (live 2026-09-20: `bash`, which
        # cost a whole round trip to "unknown tool"). Canonicalise BEFORE the
        # subagent scope check below, so an alias can never widen a scope -- the
        # check then sees the real tool name.
        if name.lower() in _TOOL_ALIASES and name.lower() not in self.tools_by_name:
            name = _TOOL_ALIASES[name.lower()]
        # `task` is dispatched only by the lead (allowed is None). A subagent runs
        # with a scoped `allowed` set, so it can neither call `task` (recursion)
        # nor reach a tool outside its scope. Case-insensitive: Qwen was observed
        # calling `Task` (SDK-style capitalization) and erroring on exact match.
        if name.lower() == "task" and self._subagents is not None and allowed is None:
            return await self._run_subagent(
                str(args.get("subagent_type") or ""), str(args.get("prompt") or "")
            )
        # Context hygiene is the lead's alone: a subagent's history is its own and
        # short-lived.
        if name.lower() == "snip" and allowed is None:
            return self._register_snip(args)
        if name.lower() == "fork_verifier_agent" and allowed is None:
            return await self._fork_verifier(args)
        # The hatch for a schema this request's budget left out. Lead only: a
        # subagent is handed its whole (scoped) tool list, so it has nothing to
        # look up, and answering it here would widen its scope.
        if name.lower() == tool_budget_schemas.LOOKUP_TOOL_NAME and allowed is None:
            return self._lookup_tool_schema(str(args.get("name") or ""),
                                            str(args.get("search") or ""))
        # Scope check is case-insensitive to match the lead's miscasing fallback
        # below — else a subagent emitting `Web_Search`/`Read` is wrongly refused
        # even though the tool is in its scope.
        if allowed is not None and name not in allowed and name.lower() not in allowed:
            # Name what it CAN call: told only "not available", tool-less subagents probed names for 8 requests
            # (2026-09-23, live session 20260923-094144-f5ed).
            have = (f"Its tools: {', '.join(sorted(allowed))}." if allowed
                    else "This subagent has no tools: work from the task prompt and report what you could not do.")
            return f"Error: tool '{name}' is not available to this subagent. {have}", True
        # Exact name first; lowercase fallback forgives a small model's Read_File-
        # style casing (no two tools differ only by case).
        tool = self.tools_by_name.get(name) or self.tools_by_name.get(name.lower())
        if tool is None and name.lower() == "see" and not self.provider.multimodal:
            return ("Image input is disabled or unreported for this session. Pixels were not inspected. "
                    "A path or saved screenshot is not visual evidence. Configure the model's vision "
                    "setting through a new session after confirming endpoint image support."), True
        if tool is None:
            return f"Error: unknown tool '{name}'.", True
        from ... import extensions
        if not extensions.tool_enabled(tool):
            return f"Tool '{name}' is disabled in Dream settings.", True
        from ..tool_validation import validate_arguments
        error = validate_arguments(_tool_schema(tool)["function"]["parameters"], args)
        if error:
            return f"Invalid arguments for {tool.name}: {error}", True
        if self.runtime_meter is not None:
            try:
                self.runtime_meter.before_tool(name)
            except RuntimeError as exc:
                return str(exc), True
        # One authority for both backends: read-only + own-mind memory tools run
        # free; writes, shell, and every self-built custom tool are gated by the
        # mode policy (via the callback, which knows the mode + workspace).
        if policy.capability(name) not in policy.AUTO_CAPS and self.permission_cb is not None:
            try:
                ok = await self.permission_cb(name, args)
            except RunLimit as exc:
                return str(exc), True
            except Exception as exc:   # Dream's own refusal or a failing check -- not the owner's No (DREAM-085)
                from ..permission_refusal import refusal_text
                return refusal_text(exc), True
            if not ok:
                return "Declined by the user.", True
        # Permission can arrive after Stop or a work deadline. It authorizes
        # this action only while its original turn is still eligible to run.
        self._check_interruption(generation)
        await _work_checkpoint()
        try:
            with self._phase("tool_execution"):
                result = await tool.handler(args)
        except Exception as e:
            return _ExecutedToolResult(f"Error: {type(e).__name__}: {e}"), True
        text = _ExecutedToolResult(content_to_text(result.get("content")))
        failed = bool(result.get("is_error") or result.get("isError"))
        if not failed:   # the session's changed files, for a Fresh start's handoff (DREAM-113)
            context = _bound_context()
            self._file_ledger.record(tool.name, args, context.workspace if context is not None else None)
        blocks = result.get("content") or []
        images = []
        if self.provider.multimodal and isinstance(blocks, list):
            for block in blocks:
                if (isinstance(block, dict) and block.get("type") == "image"
                        and block.get("mimeType") in {"image/png", "image/jpeg", "image/webp", "image/gif"}
                        and isinstance(block.get("data"), str) and len(block["data"]) <= 11_000_000):
                    images.append({"type": "image_url", "image_url": {
                        "url": f"data:{block['mimeType']};base64,{block['data']}", "detail": "auto"}})
            if images:
                text = _VisualResult(text, images[:4])
        # A clean `done` is the end-of-turn handoff: remember the page, and let
        # the verifier sweep it when the turn ends (lead only).
        if name.lower() == "update_plan" and allowed is None and not failed:
            from ...tools.project import PHASE_DONE
            if str(text).startswith(PHASE_DONE):
                self._phase_reset_pending = True
        if (name.lower() == "done" and allowed is None and not failed and args.get("path")
                and str(args["path"]).lower().endswith((".html", ".htm", ".svg"))):
            self._last_done_path = str(args["path"])
            if _AUTO_VERIFY:
                self._verify_at_turn_end = self._last_done_path
        return text, failed

    async def _guarded_exec(
        self,
        name: str,
        args: dict[str, Any],
        history: dict[tuple[str, str], dict[str, Any]],
        allowed: set[str] | None = None,
    ) -> tuple[str, bool, bool]:
        """_exec_tool plus the repetition guard, shared by the lead and subagent
        loops. Returns (text, is_error, end_the_loop)."""
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        hist = history.setdefault(
            key, {"last_hash": None, "identical": False, "blocked": False}
        )
        if hist["blocked"]:
            # Warned, then blocked, and it repeated anyway: the spiral is locked
            # in. Answer this call, then stop.
            return _LoopGuardResult(_LOOP_GUARD_BREAK), True, True
        if hist["identical"]:
            hist["blocked"] = True
            return _LoopGuardResult(_LOOP_GUARD_BLOCK), True, False
        text, is_error = await self._exec_tool(name, args, allowed=allowed)
        executed = isinstance(text, _ExecutedToolResult)
        images = getattr(text, "images", None)

        def canonical_name(tool_name: str) -> str:
            tool = self.tools_by_name.get(tool_name) or self.tools_by_name.get(tool_name.lower())
            return tool.name if tool is not None else tool_name

        def observation(tool_name: str) -> bool:
            canonical = canonical_name(tool_name)
            return policy.capability(canonical) == policy.READONLY or canonical in {
                "save_screenshot", f"mcp__{config.MCP_SERVER_NAME}__save_screenshot",
            }

        capability = policy.capability(canonical_name(name))
        if (not observation(name)
                and capability in {policy.WRITE, policy.SHELL, policy.DESTRUCTIVE}
                and (not is_error or isinstance(text, _ExecutedToolResult))):
            # A write or shell handler can change what a prior observation sees,
            # including before it fails. Approval/validation refusals never get
            # the execution marker. A file write also permits rerunning a shell
            # command against the edited inputs. Preserve file-mutator histories
            # and never let shells reset shells or captures reset captures.
            for previous in list(history):
                shell_after_write = (capability == policy.WRITE
                    and policy.capability(canonical_name(previous[0])) == policy.SHELL)
                if previous != key and (observation(previous[0]) or shell_after_write):
                    del history[previous]
        digest = hashlib.sha256((text + json.dumps(getattr(text, "images", []))).encode("utf-8", "replace")).hexdigest()
        # Advisory only: retain an uninterrupted same-tool/error streak across
        # argument changes. A changed result or successful repair invalidates it.
        # Hashes stay inside this turn's existing bounded tool history.
        failure_name = canonical_name(name)
        for previous, entry in history.items():
            if (canonical_name(previous[0]) == failure_name
                    and (not is_error or entry.get('failure_digest') != digest)):
                entry.pop('failure_digest', None)
                entry.pop('failure_count', None)
        if is_error:
            hist['failure_digest'] = digest
            hist['failure_count'] = hist.get('failure_count', 0) + 1
            matching_failures = [entry for previous, entry in history.items()
                if canonical_name(previous[0]) == failure_name
                and entry.get('failure_digest') == digest]
            if (len(matching_failures) >= 2
                    and sum(entry['failure_count'] for entry in matching_failures) >= 3):
                text += _FAILURE_GUIDANCE
        # Compare text evidence across argument variations only for known read
        # targets. Distinct files/pages with new content remain independent. This
        # is advisory: identical sections can be legitimate and polling may be
        # useful. Do not classify images, arbitrary tools or elapsed time.
        read_name = canonical_name(name).lower()
        target_field = {'read_file': 'path', 'list_dir': 'path', 'read_url': 'url'}.get(read_name)
        target = args.get(target_field) if target_field else None
        if isinstance(target, str) and target.strip() and not getattr(text, 'images', None):
            identity = (read_name, target, digest, bool(is_error))
            count = hist.get('observation_count', 0) if hist.get('observation') == identity else 0
            hist.update(observation=identity, observation_count=count + 1)
            matching = [entry for entry in history.values() if entry.get('observation') == identity]
            if len(matching) >= 2 and sum(entry['observation_count'] for entry in matching) >= 3:
                text += _OBSERVATION_GUIDANCE
        if digest == hist["last_hash"]:
            # Same call, same result: warn inside the result — novel text in the
            # context is itself a lock-in breaker.
            hist["identical"] = True
            text += _LOOP_GUARD_WARN
        hist["last_hash"] = digest
        # str concatenation drops subclass metadata. Advice must not erase
        # pixels from a successful inspection or its execution provenance.
        if images:
            text = _VisualResult(text, images)
        elif executed:
            text = _ExecutedToolResult(text)
        return text, is_error, False

    # Providers whose server takes requests concurrently. MachX is a single-flight
    # engine: two subagents there would queue on the server and share one KV cache,
    # so its `task` calls stay serial.
    _CONCURRENT_PROVIDERS = frozenset({"openai", "xai"})

    def concurrent_tasks(self) -> bool:
        """Whether this round's subagents may go out together. The provider key
        alone is not enough: DREAM_OPENAI_URL can point the `openai` key at a
        single-flight server on this machine, and two subagents would then queue
        on it (Gate 13). A loopback base URL stays serial."""
        if getattr(self.provider, "key", "") not in self._CONCURRENT_PROVIDERS:
            return False
        from urllib.parse import urlsplit

        try:  # the stdlib, not a hand-rolled split: `http://user:pw@localhost/`
            host = (urlsplit(str(getattr(self.provider, "base_url", "") or "")).hostname
                    or "").lower()
        except ValueError:
            return False
        return not (host in ("localhost", "::1", "0.0.0.0", "")
                    or host.startswith("127.")          # all of 127/8 is loopback
                    or host.endswith(".localhost"))     # RFC 6761

    def _task_batches(self, calls: list[dict[str, Any]]) -> dict[int, dict[int, Any]]:
        """The runs of `task` calls that may go out together: {first index: {index:
        args}}, one entry per CONTIGUOUS run of two or more, and only when the
        provider allows it.

        Contiguous is the point (Gate 13). Starting every task at the top of the
        round raced them against a tool written BEFORE them — the model asked to
        write a file and then summarize it, and the summary read the file while
        the write was still running. A run bounded by its neighbours keeps the
        order the model wrote: what comes before it finishes first, what comes
        after starts later.
        """
        if not self.concurrent_tasks():
            return {}
        runs: dict[int, dict[int, Any]] = {}
        cur: dict[int, Any] = {}
        for i, c in enumerate([*calls, {"name": "", "args": "{}"}]):  # a sentinel closes the last run
            args, bad = _parse_tool_args(c.get("args") or "{}")
            if (c.get("name") or "").lower() == "task" and bad is None:
                cur[i] = args
                continue
            if len(cur) > 1:
                runs[min(cur)] = cur
            cur = {}
        return runs

    def _agent_activity(self, kind: str, **fields) -> None:
        """Display-only observations; a broken subscriber cannot abort work."""
        context = _AGENT_ACTIVITY.get()
        emit = getattr(self, '_background_emit', None)
        if context is None or emit is None:
            return
        try:
            from ...agent_activity import bounded_agent_activity
            payload = bounded_agent_activity({**context, 'kind': kind, **fields})
            if payload is not None:
                emit(Event('agent_activity', payload))
        except Exception:
            pass

    async def _run_subagent(self, subagent_type: str, prompt: str, *, continuation: bool = False) -> tuple[str, bool]:
        """Run scoped, non-streaming work without borrowing the lead event stream. `continuation`
        runs it inside the lead conversation instead of its own (_subagent_loop, fix #71)."""
        token = _AGENT_ACTIVITY.set({'run_id': uuid.uuid4().hex, 'agent': subagent_type,
                                     'phase': 'subagent', 'round': 0})
        self._agent_activity('status', status='running', text='Subagent started.')
        try:
            spec = (self._subagents or {}).get(subagent_type)
            if spec is None:
                avail = ", ".join(self._subagents or {}) or "(none)"
                self._agent_activity('status', status='failed', text='Requested subagent is unavailable.')
                return f"Error: unknown subagent '{subagent_type}'. Available: {avail}.", True
            try:
                # Deadline cancellation must honor the same transport cleanup
                # shields as Stop. Cleanup can outlast the work deadline.
                with fail_after(self.profile.subagent_timeout_s if self.profile else 1200):
                    result, failed = await self._subagent_loop(spec, subagent_type, prompt,
                                                               continuation=continuation)
                    await _work_checkpoint()
                status = 'failed' if failed else 'unknown' if result == '(subagent returned no output)' else 'completed'
                self._agent_activity('status', status=status, text={
                    'failed': 'Subagent returned a failed or incomplete result; inspect its tool activity.',
                    'unknown': 'Subagent returned no output; completion could not be confirmed.',
                    'completed': 'Subagent finished; its result was returned to the lead agent.',
                }[status])
                return result, failed
            except TimeoutError:
                self._agent_activity('status', status='failed', text='Subagent timed out; work is incomplete.')
                return f"(subagent '{subagent_type}' timed out; incomplete)", True
            except asyncio.CancelledError:
                self._agent_activity('status', status='interrupted', text='Subagent observation was interrupted. In-flight effects may need checking.')
                raise
            except Exception as e:
                self._agent_activity('status', status='failed', text=f'{type(e).__name__}: {e}')
                return f"(subagent '{subagent_type}' failed: {type(e).__name__}: {e})", True
        finally:
            _AGENT_ACTIVITY.reset(token)

    def _subagent_tool_names(self, spec: Any) -> list[str]:
        """The local tools a subagent may call. A declared list is honoured as written (missing names skipped);
        the undeclared marker ``("*",)`` means every tool the lead has except delegation itself -- what a plugin's
        agent written for Claude Code's default toolset expects (DREAM-089)."""
        if "*" in spec.tool_names:
            from ... import extensions
            active = {tool.name for tool in extensions.filter_tools(self.tools)}
            return [n for n in self.tools_by_name if n in active and n not in {"task", "fork_verifier_agent"}]
        return [n for n in spec.tool_names if n in self.tools_by_name]

    async def _subagent_loop(
        self, spec: Any, subagent_type: str, prompt: str, *, continuation: bool = False
    ) -> tuple[str, bool]:
        # Scope to tools that actually exist on this backend — a renamed/missing
        # tool in the spec is silently skipped, never a crash.
        names = self._subagent_tool_names(spec)
        allowed = set(names)
        required_inspection = {"show_html", "get_webview_logs", "save_screenshot", "see"}
        inspected: set[str] = set()
        failed_inspections: set[str] = set()
        latest_visual_message = None
        image_submitted = False
        if subagent_type == "verifier":
            # The page verifier requires a rendered inspection. A text-only
            # model's PASS cannot substitute for a tool this adapter removed.
            from ... import extensions
            enabled = {n for n in allowed if extensions.tool_enabled(self.tools_by_name[n])}
            missing = required_inspection - enabled
            if missing:
                return ("Verification unverified: required inspection tools unavailable: "
                        + ", ".join(sorted(missing))
                        + ". No verifier request was sent. Check adapter tool/image support; "
                        "tool registration alone does not qualify endpoint image acceptance."), True
        if continuation:
            # Fix #71: on an engine that caches one conversation the check runs inside the lead's own:
            # the same system text and tools, the lead history, then the instruction, so the engine only
            # extends its cached prefix. Its rounds stay in the history, so the next turn extends it
            # too; `allowed` still limits what runs to the subagent's tools.
            schemas = self._request_tools()
            messages = self.messages
            messages.append({"role": "user", "name": "dream_verifier_instruction", "content": (
                "[Dream check, not from the user. For this check only, take the role below; tools outside "
                "its list will not run, and your reply ends the check.]\n\n" + spec.prompt + "\n\n" + prompt)})
        else:
            schemas = [_tool_schema(self.tools_by_name[n]) for n in names]
            messages = [
                {"role": "system", "content": spec.prompt},
                {"role": "user", "content": prompt},
            ]
        last_content = ""
        # Same per-run repetition guard the lead uses: without it a confused
        # subagent grinds identical calls for its whole round budget.
        call_history: dict[tuple[str, str], dict[str, Any]] = {}
        # The server's last prompt_tokens for THIS run — exact but a round
        # stale, so the fill is whichever of it and the estimate is larger
        # (mirrors _ctx_fill, which tracks the lead history, not this one).
        sub_prompt_tokens = 0

        def keep_reply(msg: dict[str, Any], **fields: Any) -> None:
            # Continuation: the reply the engine generated stays in the lead history, reasoning and all,
            # so the history matches the engine's cached state (as the lead's replies, _keep_reasoning).
            messages.append({"role": "assistant",
                             "content": msg.get("content") if fields else (msg.get("content") or ""), **fields})
            reasoning = msg.get("reasoning_content")
            self._keep_reasoning(messages[-1], [reasoning] if isinstance(reasoning, str) and reasoning else [])

        for round_index in range(1, _SUB_MAX_ROUNDS + 1):
            activity = _AGENT_ACTIVITY.get()
            if activity is not None:
                activity['round'] = round_index
            if getattr(self, "_foreground_pause", lambda: False)():
                raise asyncio.CancelledError()
            if self.runtime_meter is not None:
                try:
                    self.runtime_meter.check()
                except RuntimeError as exc:
                    return str(exc), True
            # Same wall the lead loop has (_maybe_compact): a research run's
            # history outgrows the window long before the round limit, and a
            # strict server then 400s away every round of finished work. This
            # history is scratch space discarded at return — the summary is the
            # product — so unlike the lead's, compacting it needs no event.
            window = self._window()
            if continuation:
                # That is the lead history: compacting it for a check would re-read all of it.
                fill = self._ctx_fill()
                if fill + _CTX_MARGIN + 256 > window:
                    return ("Verification could not run: this conversation is too full for a check "
                            "inside it, and it is not compacted for one."), True
            else:
                fill = max(_est_tokens(messages), sub_prompt_tokens)
                if fill > window * compact_at(window) and self._context_overflow == "compact":
                    if _compact_messages(messages, int(window * compact_at(window) / 2)):
                        # The stale server count describes the old, larger history.
                        sub_prompt_tokens = 0
                    fill = max(_est_tokens(messages), sub_prompt_tokens)
            payload = {
                "model": self.model,
                "messages": messages,
                "tools": schemas,
                "tool_choice": "auto",
                "stream": False,
                "temperature": self.temperature,
                "max_tokens": self._max_tokens(fill),
                **self._sampling,
                **self._effort_params(),
            }
            if not continuation and (self.profile or self._local_options
                                     or self.capability_status()['context_tokens']['known']):
                try:
                    payload["max_tokens"] = self._admit_request(messages, schemas, lead=False).output
                except ContextOverflow as exc:
                    return str(exc), True
            if continuation:
                self._mark_sent(messages, schemas)
            if subagent_type == "verifier":
                # Progress the owner can see while the check runs (fix #71): the verifier's card shows it.
                self._agent_activity('status', status='running',
                                     text=f'Checking the work ({round_index}/{_SUB_MAX_ROUNDS})')
            self._agent_activity('request', status='awaiting_response', request_index=round_index)
            try:
                resp = await self._post_with_retry(payload)
                if (latest_visual_message is not None
                        and any(message is latest_visual_message for message in payload['messages'])):
                    image_submitted = True
                self._agent_activity('response', status='received', request_index=round_index)
            except _RequestFailed as e:
                return f"(subagent '{subagent_type}' {e})", True
            data = resp.json()
            if not isinstance(data, dict):  # non-compliant server: 200 + non-object
                raise ValueError(f"non-object chat response: {str(data)[:80]}")
            usage = data.get("usage")
            if isinstance(usage, dict):
                for key in self._delegated_usage:
                    self._delegated_usage[key] += int(usage.get(key) or 0)
                if self.runtime_meter is not None:
                    self.runtime_meter.usage({**usage, "head_hash": self._head_hash(payload)}, phase=subagent_type)
                try:
                    sub_prompt_tokens = int(usage.get("prompt_tokens") or 0)
                except (TypeError, ValueError):
                    pass
                if continuation and sub_prompt_tokens:   # a count of the lead history, as the lead's own
                    self._last_prompt_tokens = sub_prompt_tokens
                    self._record_calibration(sub_prompt_tokens)
            from ..inference_coordination import primary_choice
            choice = primary_choice(data)
            if choice is None:
                raise ValueError("Nonstream response has no primary choice.")
            msg = choice.get("message")
            if not isinstance(msg, dict):
                raise ValueError("Invalid primary nonstream message.")
            if isinstance(msg.get('reasoning_content'), str) and msg['reasoning_content'].strip():
                self._agent_activity('thinking_report', text=msg['reasoning_content'], reported_after_response=True)
            calls = msg.get("tool_calls") or []
            last_content = (msg.get("content") or "").strip()
            reason = choice.get("finish_reason")
            # Some compatible nonstream servers omit terminal metadata. Preserve
            # that legacy form, but explicit unsuccessful completion cannot
            # authorize structured or recovered tools, or a trusted summary.
            if reason not in (None, "stop", "tool_calls", "function_call"):
                detail = ("output truncated at the token limit" if reason == "length"
                          else "output filtered" if reason == "content_filter"
                          else "stopped because it kept repeating itself" if reason == "repetition"
                          else "invalid or unsupported completion")
                if continuation:
                    keep_reply(msg)
                return (f"(subagent '{subagent_type}' {detail} — treat as incomplete; "
                        f"no tools from this response ran)\n{last_content}"), True
            if not calls:
                # Same recovery the lead loop does — without it a subagent on a
                # model the server can't parse hands its raw XML back to the lead
                # as if it were the answer.
                last_content, recovered = _parse_text_tool_calls(last_content)
                calls = [{"id": c["id"], "type": "function",
                          "function": {"name": c["name"], "arguments": c["args"]}}
                         for c in recovered]
            if not calls:
                if continuation:
                    keep_reply(msg)
                if (subagent_type == "verifier" and last_content.isascii()
                        and last_content.upper() == "PASS"):
                    missing = required_inspection - inspected
                    if missing or failed_inspections or not image_submitted:
                        detail = []
                        if missing:
                            detail.append("required inspections not completed: " + ", ".join(sorted(missing)))
                        if failed_inspections:
                            detail.append("unresolved inspection failures: " + ", ".join(sorted(failed_inspections)))
                        if not image_submitted:
                            detail.append("no successful image inspection submitted to a completed request")
                        return "Verification unverified: " + "; ".join(detail) + ".", True
                return last_content or "(subagent returned no output)", False
            # Rebuild the assistant tool_calls with stable ids: local models
            # sometimes omit id, and the assistant entry + tool result MUST carry
            # the same one or a strict server 400s (mirrors the main loop).
            norm = []
            for i, tc in enumerate(calls):
                fn = tc.get("function") or {}
                norm.append({
                    "id": tc.get("id") or f"sub_{len(messages)}_{i}",
                    "type": "function",
                    "function": {"name": fn.get("name") or "",
                                 "arguments": fn.get("arguments") or "{}"},
                })
            if continuation:
                keep_reply(msg, tool_calls=norm)
            else:
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": norm,
                })
            round_images = []
            for tool_index, tc in enumerate(norm):
                await _work_checkpoint()
                fn = tc["function"]
                a, bad_args = _parse_tool_args(fn["arguments"])
                activity_id = f"{activity['run_id'] if activity else 'sub'}:{round_index}:{tool_index}:{str(tc['id'])[:100]}"
                self._agent_activity('tool_use', status='requested', data={
                    'id': activity_id, 'name': fn['name'], 'input': a if bad_args is None else fn['arguments']})
                try:
                    if bad_args is not None:
                        result_text, _err, stop = bad_args, True, False
                    else:
                        result_text, _err, stop = await self._guarded_exec(
                            fn["name"], a, call_history, allowed=allowed)
                except asyncio.CancelledError:
                    self._agent_activity('tool_result', status='interrupted', data={
                        'id': activity_id, 'name': fn['name'], 'content': 'Tool observation interrupted; outcome is unknown.', 'is_error': True})
                    raise
                except Exception as exc:
                    self._agent_activity('tool_result', status='failed', data={
                        'id': activity_id, 'name': fn['name'], 'content': f'{type(exc).__name__}: {exc}', 'is_error': True})
                    raise
                self._agent_activity('tool_result', status='failed' if _err else 'returned', data={
                    'id': activity_id, 'name': fn['name'], 'content': str(result_text), 'is_error': _err})
                if subagent_type == "verifier" and not isinstance(result_text, _LoopGuardResult):
                    name = fn['name'].lower()
                    # A new load/capture supersedes earlier visual observations.
                    if name in {"show_html", "save_screenshot"}:
                        inspected.difference_update(required_inspection if name == "show_html" else {"see"})
                        latest_visual_message, image_submitted = None, False
                    if _err:
                        inspected.discard(name)
                        failed_inspections.add(name)
                    else:
                        failed_inspections.discard(name)
                        if name in required_inspection:
                            inspected.add(name)
                    if name == "see":
                        latest_visual_message, image_submitted = None, False
                        if not _err and getattr(result_text, "images", None):
                            latest_visual_message = _visual_message(result_text.images)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _clamp(str(result_text), _TOOL_RESULT_CAP),
                })
                if subagent_type != "verifier":
                    round_images.extend(getattr(result_text, "images", []))
                if stop:
                    return (f"(subagent '{subagent_type}' stopped by the loop guard: "
                            f"it kept repeating the same tool call. Last note: "
                            f"{last_content or 'n/a'})"), True
            if round_images:
                messages.append(_visual_message(round_images))
            if latest_visual_message is not None and not any(m is latest_visual_message for m in messages):
                messages.append(latest_visual_message)
        return (f"(subagent '{subagent_type}' hit the {_SUB_MAX_ROUNDS}-round limit; "
                f"last note: {last_content or 'n/a'})"), True

    async def _apply_steering(self, *, final: bool = False) -> bool:
        inbox = getattr(self, 'steering_inbox', None)
        if inbox is None:
            return False
        corrections = await inbox.drain(final=final)
        for receipt in corrections:
            self._msg_seq += 1
            # Dream's continuation notice after a cut at the output ceiling (fix #85) is not a correction the
            # owner sent: it takes the name fix #14's instruction had, which the filer (_turn_text), the
            # verifier's request scope and compaction's last-user search all leave out.
            name = ('dream_recovery_instruction' if receipt.get('origin') == turn_origin.LENGTH
                    else 'dream_steering_user')
            self.messages.append({'role': 'user', 'name': name,
                'content': f"{receipt['text']}\n\n[id:m{self._msg_seq:04d}]"})
        return bool(corrections)

    async def ask(self, prompt: str) -> AsyncIterator[Event]:
        generation = self._interrupts.generation
        token = _REQUEST_INTERRUPTION.set((self._interrupts, generation))
        self._turn_active = True
        try:
            async with aclosing(self._ask(prompt)) as events:
                while True:
                    self._check_interruption(generation)
                    try:
                        event = await anext(events)
                    except StopAsyncIteration:
                        break
                    self._check_interruption(generation)
                    for notice in self._context_notices():
                        yield notice
                    yield event
        finally:
            # A pending sweep belongs to this invocation, including when its
            # iterator is closed after done() but before the final response.
            # It must not review the old artifact against an unrelated next task.
            self._verify_at_turn_end = None
            self._turn_active = False
            _REQUEST_INTERRUPTION.reset(token)

    async def _ask(self, prompt: str) -> AsyncIterator[Event]:
        if self._client is None:
            raise RuntimeError("Backend not connected.")
        self._delivery_review = {"status": "not_requested"}
        self._delivery_attempt = {"status": "not_requested"}
        # Active-turn constraints become ordinary prior history only on a new turn.
        for message in self.messages:
            if message.get('name') in {'dream_steering_user', 'dream_active_user'}:
                message['name'] = 'dream_prior_user'
        self._revealed.clear()
        self._schema_cache = None
        self._schema_usage = None
        if not self._foreground_prepared:
            await self.prepare_user_turn()
        self._foreground_prepared = False
        self._turn_tools, self._prepared_tools = self._prepared_tools, ()
        # A previous turn may have been abandoned mid-round; heal it before this
        # one inherits the damage.
        self._repair_dangling()
        # Keep the previous verifier's report structurally separate from the
        # user's new request. It is optional context for existing compaction.
        if self._pending_findings:
            self.messages.append({"role": "assistant", "name": "dream_verifier_report",
                                  "content": self._pending_findings})
            self._pending_findings = None
        for ev in self._live_state_sync():
            yield ev
        # Every user message carries an id the model can name to `snip`.
        self._msg_seq += 1
        self.messages.append({"role": "user",
                              "content": f"{prompt}\n\n[id:m{self._msg_seq:04d}]"})
        if getattr(self, 'steering_inbox', None) is not None:
            self.messages[-1]['name'] = 'dream_active_user'
        self._stage_council_context()

        turn_t0 = time.monotonic()
        self._delegated_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        # Per-turn speed aggregates across tool rounds. Server-side timings
        # (llama.cpp style) are exact; the client-side TTFT split is the fallback.
        agg = {"pp_n": 0, "pp_ms": 0.0, "gen_n": 0, "gen_ms": 0.0,
               "prompt_tokens": 0, "completion_tokens": 0, "ctx_used": None}
        # Loop-guard state, per (tool, canonical args): last result digest, whether
        # the last two executions returned identical results, whether the call has
        # already been blocked. Per-turn on purpose — a fresh user message may
        # legitimately redo an earlier call.
        call_history: dict[tuple[str, str], dict[str, Any]] = {}
        length_retries = 0        # automatic continuations after a cut at the output ceiling (fix #85)
        loop_retries = 0
        since_change = 0          # tool calls since one that changed something
        # Rounds spent of _MAX_TOOL_ROUNDS. Each request costs one; a tool round of only looks on
        # a local engine is refunded half (fix #17, _round_cost). A round starts only while a whole
        # one still fits, so the limit is never passed: a turn can end with half a round unused.
        # `last`: no round after this one.
        spent, prev_left = 0.0, float(_MAX_TOOL_ROUNDS)

        while spent + 1 <= _MAX_TOOL_ROUNDS:
            spent += 1
            last = spent + 1 > _MAX_TOOL_ROUNDS
            if self.runtime_meter is not None:
                try:
                    self.runtime_meter.check()
                except RuntimeError as exc:
                    yield Event("error", str(exc))
                    yield Event("result", {"is_error": True, "subtype": "run_budget",
                                           "stats": self._turn_stats(agg, time.monotonic() - turn_t0)})
                    return
            await self._apply_steering()
            for ev in self._phase_reset():
                yield ev
            for ev in self._maybe_compact():
                yield ev
            for ev in self._context_notices():
                yield ev
            payload = {
                "model": self.model,
                "messages": self.messages,
                "tools": self._request_tools(),
                "tool_choice": "auto",
                "stream": True,
                "temperature": self.temperature,
                # A hard ceiling on one generation: without it a looping local model
                # decodes until the context is exhausted (10s of minutes). A runaway
                # now self-terminates instead of hanging the session. Clamped to
                # what's left of the window so the request can't overrun it.
                "max_tokens": self._max_tokens(self._ctx_fill()),
                "stream_options": {"include_usage": True},
                **self._sampling,
                **self._effort_params(),
            }
            # A local server matches on the PROMPT'S HEAD — the system message and the
            # tool schemas, ~8,614 tokens here. If that head changes mid-session the
            # whole conversation is re-read, however little of it moved: live
            # 2026-09-21, 33,077 tokens in 143 s, and 42,231 in 155 s the day before.
            # Nothing said WHAT changed, so: fingerprint the head and say when it moves.
            if self.provider.key == "machx":
                head = self._head_hash(payload)
                was = getattr(self, "_head_print", None)
                if was is not None and was != head:
                    n_sys = len(str(self.messages[0].get("content", "")))
                    prev_sys, prev_n = getattr(self, "_head_parts", ("", 0))
                    what = ("the system message" if prev_sys != str(self.messages[0].get("content", ""))
                            else "the tool list")
                    yield Event("system", f"The prompt's head changed ({what}) — the local server will re-read "
                                          f"this conversation. System message {prev_n} → {n_sys} chars, "
                                          f"{len(payload['tools'])} tools.")
                self._head_print = head
                self._head_parts = (str(self.messages[0].get("content", "")),
                                    len(str(self.messages[0].get("content", ""))))
                # Ask MachX to stream a tool call's text while it is being written, so the
                # chat can show a file appearing instead of a long silence. Display only:
                # the call itself still arrives structured at the end, and a server that
                # doesn't know the field ignores it.
                payload["stream_tool_preview"] = True
            if self.profile or self._local_options or self.capability_status()['context_tokens']['known']:
                try:
                    payload["max_tokens"] = self._admit_request(self.messages, payload["tools"]).output
                except ContextOverflow as exc:
                    for ev in self._context_notices():
                        yield ev
                    yield Event("error", str(exc))
                    yield Event("result", {"is_error": True, "subtype": "context_overflow",
                                           "stats": self._turn_stats(agg, time.monotonic() - turn_t0)})
                    return
                for ev in self._context_notices():
                    yield ev
                yield Event("context_budget", self.context_report)
            # What this request is estimated at, for the calibration its usage teaches, and
            # its head as sent (admission may just have compacted and refreshed it).
            self._mark_sent(self.messages, payload["tools"])
            head_hash = self._head_hash(payload)
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: dict[int, dict[str, str]] = {}
            truncated_call: str | None = None
            usage = None
            timings = None
            finish_reason: str | None = None
            t_req = time.monotonic()
            t_first: float | None = None
            t_text: float | None = None
            timing_recorded = False
            measurement = self._measurement_request(payload, phase="lead") if self.turn_timing else None

            def record_request():
                nonlocal timing_recorded
                if self.turn_timing and not timing_recorded:
                    timing_recorded = True
                    self.turn_timing.request(time.monotonic() - t_req,
                        first_activity=t_first - t_req if t_first is not None else None,
                        first_text=t_text - t_req if t_text is not None else None,
                        usage=usage, server_timings=timings,
                        schema_fingerprint=self._schema_fingerprint(payload["tools"]), configuration=measurement)
            try:
                async with self._stream_with_retry(payload) as resp:
                    inbox = getattr(self, 'steering_inbox', None)
                    if inbox is not None:
                        await inbox.submitted()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        _raise_server_error(chunk)
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        if chunk.get("timings"):
                            timings = chunk["timings"]
                        from ..inference_coordination import primary_choice
                        choice = primary_choice(chunk)
                        if choice is None:
                            continue
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                            # MachX names the tool call a length cut landed in.
                            truncated_call = ((choice.get("truncated_tool_call") or {}).get("name")
                                              or truncated_call)
                        delta = choice.get("delta", {}) or {}
                        meaningful = bool(delta.get("content") or delta.get("reasoning_content")
                                          or any(tc.get("id") or tc.get("function", {}).get("name")
                                                 or tc.get("function", {}).get("arguments")
                                                 for tc in delta.get("tool_calls") or []))
                        if t_first is None and meaningful:
                            t_first = time.monotonic()
                        if meaningful and self.turn_timing:
                            kind = "text_delta" if delta.get("content") else (
                                "thinking_delta" if delta.get("reasoning_content") else "tool_use")
                            self.turn_timing.observe(kind)
                        if delta.get("content"):
                            if t_text is None:
                                t_text = time.monotonic()
                            text_parts.append(delta["content"])
                            yield Event("text_delta", delta["content"])
                        if delta.get("reasoning_content"):
                            reasoning_parts.append(delta["reasoning_content"])
                            yield Event("thinking_delta", delta["reasoning_content"])
                        if delta.get("tool_call_preview"):
                            # The call as it is being written, for display only. It never
                            # joins the reply text or the history — the structured call at
                            # the end of the stream is the one that runs.
                            yield Event("tool_preview", delta["tool_call_preview"])
                        for tc in delta.get("tool_calls") or []:
                            slot = tool_calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {}) or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
            except _ServerGenerationError as e:
                record_request()
                notice = self._observe_image_rejection(e)
                await self._observe_engine_fault(e)
                fault = getattr(self, '_engine_fault_notice', None)
                if fault:
                    self._engine_fault_notice = None
                    yield Event("system", fault)
                yield Event("error", f"{self.provider.label} {e}{notice}")
                yield Event("result", {"is_error": True, "subtype": e.subtype, "failure": self._failure_info(e, "server"),
                                       "stats": self._turn_stats(agg, time.monotonic() - turn_t0)})
                return
            except _RequestFailed as e:
                record_request()
                if text_parts:
                    self.messages.append({'role': 'assistant', 'content': ''.join(text_parts)
                                          + '\n[Incomplete response; no collected tool calls executed.]'})
                yield Event("error", f"{self.provider.label} {e}")
                yield Event("result", {"is_error": True, "subtype": "request_failed", "failure": self._failure_info(e, "request"),
                                       "stats": self._turn_stats(agg, time.monotonic() - turn_t0)})
                return
            except httpx.HTTPError as e:
                record_request()
                if text_parts:
                    self.messages.append({'role': 'assistant', 'content': ''.join(text_parts)
                                          + '\n[Incomplete response; no collected tool calls executed.]'})
                shown = _stream_failure(self.provider.label, e)
                yield Event("error", shown)
                yield Event("result", {"is_error": True, "subtype": "stream_error", "failure": self._failure_info(e, "stream", shown=shown),
                                       "stats": self._turn_stats(agg, time.monotonic() - turn_t0)})
                return
            finally:
                record_request()

            t_end = time.monotonic()
            round_stats: dict[str, Any] | None = None
            if timings:
                # llama.cpp-style server measurements — exact prefill/decode split.
                round_stats = {
                    "pp_n": int(timings.get("prompt_n") or 0),
                    "pp_ms": float(timings.get("prompt_ms") or 0.0),
                    "gen_n": int(timings.get("predicted_n") or 0),
                    "gen_ms": float(timings.get("predicted_ms") or 0.0),
                    "exact": True,
                }
            elif usage:
                # Client-side estimate: time-to-first-token ≈ prefill, the rest ≈ decode.
                round_stats = {
                    "pp_n": int(usage.get("prompt_tokens") or 0),
                    "pp_ms": ((t_first or t_end) - t_req) * 1000.0,
                    "gen_n": int(usage.get("completion_tokens") or 0),
                    "gen_ms": max(t_end - (t_first or t_end), 0.0) * 1000.0,
                    "exact": False,
                }
            if round_stats:
                for k in ("pp_n", "pp_ms", "gen_n", "gen_ms"):
                    agg[k] += round_stats[k]
                round_stats["ttft_ms"] = (
                    (t_first - t_req) * 1000.0 if t_first is not None else None
                )
                # For the stats line under the prompt box: what the prompt really
                # cost (cached tokens are not re-read) and how full the window is.
                if usage:
                    round_stats["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
                    round_stats["cached"] = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                round_stats["window"] = self._window()
                # Per-round speed for the live monitor pane — exact numbers the
                # moment the round ends, not only at the end of the whole turn.
                yield Event("stats", round_stats)
            if usage:
                agg["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
                if self.runtime_meter is not None:
                    self.runtime_meter.usage({**usage, "head_hash": head_hash})
                agg["completion_tokens"] += int(usage.get("completion_tokens") or 0)
                agg["ctx_used"] = (int(usage.get("prompt_tokens") or 0)
                                   + int(usage.get("completion_tokens") or 0))
                self._last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
                self._record_calibration(self._last_prompt_tokens)

            full = "".join(text_parts).strip()
            if usage:
                self.last_usage = usage

            subtype = ('success' if finish_reason in (None, 'stop', 'tool_calls', 'function_call')
                       else finish_reason if finish_reason in ('length', 'content_filter', 'repetition')
                       else 'unsupported_finish_reason')
            if subtype != 'success':
                # Valid JSON is not authorization to execute an unfinished or
                # filtered generation. Keep any text, but discard collected calls.
                tool_calls.clear()

            # No structured calls may mean the server's parser didn't recognise
            # this model's format (see _parse_text_tool_calls). A recovered call
            # for a tool that doesn't exist is NOT filtered out — it goes down
            # the same path a structured one would and comes back "Error:
            # unknown tool", which the model can react to. Dropping it instead
            # ends the turn silently, which is the bug this whole path exists
            # to fix.
            if not tool_calls and subtype == 'success':
                rest, recovered = _parse_text_tool_calls(full)
                if recovered:
                    full = rest
                    tool_calls = dict(enumerate(recovered))

            if not tool_calls:
                # Nothing parsed, but a call was started: the generation ran out
                # of room mid-call (a big write_file is the usual one). Say so —
                # otherwise this is indistinguishable from the model choosing to
                # stop, which is the exact silence this whole path exists to end.
                cut_call = truncated_call or ("tool" if _OPEN_INVOKE_RE.search(full) else None)
                if cut_call:
                    cap = self._max_tokens(self._ctx_fill())
                    why = (f"the {cap:,}-token limit for one generation"
                           if cap < config.MAX_OUTPUT_TOKENS
                           else f"DREAM_MAX_TOKENS ({config.MAX_OUTPUT_TOKENS:,})")
                    hint = ("the context window is nearly full — /new starts fresh"
                            if cap < config.MAX_OUTPUT_TOKENS
                            else "raise DREAM_MAX_TOKENS, or ask for it in pieces")
                    named = f" ({truncated_call})" if truncated_call else ""
                    yield Event("system", f"A tool call was cut off at {why} before "
                                          f"it finished{named}, so nothing ran. {hint}.")
                self.messages.append({"role": "assistant", "content": full})
                self._keep_reasoning(self.messages[-1], reasoning_parts)
                if full:
                    yield Event("assistant_done", full)
                if subtype == "length" and length_retries < _LENGTH_CONTINUATIONS and not last:
                    # Fix #85 (1), DREAM-117: a reply cut at the output ceiling with no completed tool call
                    # ended the turn (live 2026-09-24: 16,384 tokens in 22 minutes, twice, and the owner typed
                    # "continue" by hand each time). Say what happened and ask the model to go on in smaller
                    # pieces, at most _LENGTH_CONTINUATIONS times a turn. A structured cut call never joins the
                    # history (a text-format one stays in the reply text, as before). Fix #14's one
                    # retry-in-parts for a named cut call is folded in here.
                    length_retries += 1
                    ceiling = int(payload["max_tokens"])
                    what = f"while writing the {cut_call} call" if cut_call else "with no completed tool call"
                    notice = (f"[Dream] Your last reply reached the {ceiling:,}-token output ceiling {what}, so "
                              "nothing ran and nothing was written. Continue in smaller pieces: one file or one "
                              "script per tool call, at most ~300 lines each (write_file the first part, then "
                              "write_file with append=true for each further part), and make the call straight "
                              "away rather than planning it out. Check with list_dir/read_file what already "
                              "exists before rewriting.")
                    inbox = getattr(self, 'steering_inbox', None)
                    if inbox is not None:
                        # The owner's chat: the note travels the steering inbox like the progress guard's, so
                        # the transcript logs it as Dream's (tool_name dream:length, core/turn_origin.py), the
                        # pane's receipts say whose it is, and _apply_steering adds it to the next request.
                        try:
                            receipt = await inbox.submit(notice, uuid.uuid4().hex, origin=turn_origin.LENGTH)
                        except ValueError:          # closed for this turn, or its 32 receipts are taken
                            receipt = None
                        # Only a pending receipt is drained into the next request; a `retained` one (its
                        # transcript write failed) would leave the re-ask with no notice at all.
                        if not receipt or receipt.get('status') != 'pending':
                            inbox = None
                    if inbox is None:
                        # No inbox (a guided task, an autonomous loop), or one that could not take the note:
                        # in the request alone, as fix #14 did.
                        self.messages.append({"role": "user", "name": "dream_recovery_instruction", "content": notice})
                    if self.runtime_meter is not None:
                        self.runtime_meter.record("length_continuation", attempt=length_retries, ceiling=ceiling,
                                                  cut_call=cut_call)
                    yield Event("system", f"The reply reached the {ceiling:,}-token output ceiling {what}, so "
                                          "nothing ran; asked the model to continue in smaller pieces, one file or "
                                          f"one script per call (automatic continuation {length_retries} of "
                                          f"{_LENGTH_CONTINUATIONS}).")
                    continue
                if subtype == "length" and not cut_call:
                    # The whole output budget went on prose and the reply was cut at the
                    # cap without a single tool call (live 2026-09-20: 16,384 tokens over
                    # 40 minutes, ending "Now I'll build. Let me check the HTML controls
                    # before editing."). Reached once the automatic continuations are
                    # spent or no round is left, so the turn ends: SAY so -- the bare
                    # "Response incomplete (length)" reads like a server fault.
                    yield Event("system", "That reply used the whole output limit without making a single "
                                          "tool call, so it was cut off and nothing ran. Continue, and tell "
                                          "it to act rather than plan.")
                if subtype == "repetition" and loop_retries < 1 and not last:
                    # The engine stopped a reply that had started saying the same
                    # thing over and over (ie serve's repetition stop). Name it and
                    # ask once for a fresh answer, rather than ending the turn on
                    # what otherwise reads as an obscure server failure.
                    loop_retries += 1
                    self.messages.append({"role": "user", "name": "dream_recovery_instruction", "content": (
                        "[Dream] Your last reply started repeating the same words over and over, so it was "
                        "stopped and nothing ran. Answer again from the start, keep it short, and make any "
                        "tool call you need straight away.")})
                    yield Event("system", "The reply started repeating itself and was stopped — asked for it again.")
                    continue
                if subtype == 'success':
                    inbox = getattr(self, 'steering_inbox', None)
                    if inbox is not None and last:
                        # No next lead request is allowed. Do not inject a new
                        # correction into the auxiliary salvage/verifier work.
                        await inbox.close()
                        if any(r['status'] == 'retained' for r in inbox.receipts.values()):
                            continue
                    elif await self._apply_steering(final=True):
                        continue
                # A run cut off at max_tokens (finish_reason "length") is truncated,
                # not finished: surface a non-success subtype so callers that must
                # not trust a partial answer can tell. Preserve the partial text
                # while marking the result incomplete for every consumer.
                if subtype == 'success':
                    turn_text = self._turn_text(prompt)   # before a check inside the conversation adds to it
                    for ev in await self._run_verifier_sweep():
                        yield ev
                    for ev in await self._finish_filing(turn_text):
                        yield ev
                else:
                    tried = (f" after {length_retries} automatic continuation{'s' if length_retries != 1 else ''}"
                             if subtype == 'length' and length_retries else '')
                    detail = ('The reply kept repeating itself and was stopped'
                              if subtype == 'repetition' else f'Response incomplete ({subtype}){tried}')
                    yield Event('error', f'{detail}; no collected tool calls ran. Send new instructions to continue.')
                if subtype == 'success':
                    review_status = self._delivery_review['status']
                    if self._delivery_attempt['status'] == 'failed':
                        subtype = 'delivery_failed'
                    elif review_status == 'needs_attention':
                        subtype = 'verification_findings'
                    elif review_status == 'unverified':
                        subtype = 'verification_unverified'
                yield Event("result", {
                    "is_error": subtype != "success",
                    "num_turns": 1,
                    "usage": usage,
                    "total_cost_usd": None,
                    "subtype": subtype,
                    "delivery_review": dict(self._delivery_review),
                    "delivery_attempt": dict(self._delivery_attempt),
                    "stats": self._turn_stats(agg, time.monotonic() - turn_t0),
                })
                return

            if full:
                yield Event("assistant_done", full)

            calls = [tool_calls[i] for i in sorted(tool_calls)]
            self.messages.append({
                "role": "assistant",
                "content": full or None,
                "tool_calls": [
                    {
                        "id": c["id"] or f"call_{i}",
                        "type": "function",
                        # Arguments are capped like results: a write_file call
                        # carries its whole file body, and the model re-reads it
                        # on every later round. The CALL already ran with the
                        # full text (parsed from the stream below) — only the
                        # history copy is cut.
                        # The name is capped too: a confused local model can
                        # emit a multi-KB garbled name, and nothing downstream
                        # can shrink it. Far longer than any real tool name.
                        "function": {"name": (c["name"] or "")[:_TOOL_NAME_CAP],
                                     "arguments": _clamp_args(c["args"] or "{}")},
                    }
                    for i, c in enumerate(calls)
                ],
            })
            self._keep_reasoning(self.messages[-1], reasoning_parts)
            loop_break = False
            recorded = 0
            round_images = []
            # Phase 13: on a provider that is not single-flight, a contiguous run
            # of `task` calls starts together — at its FIRST slot, so every tool
            # the model wrote before it has already finished. Results are taken
            # in the order the model issued them.
            runs = self._task_batches(calls)
            batch: dict[int, "asyncio.Future[Any]"] = {}
            batch_scopes: dict[int, CancelScope] = {}
            try:
                for i, c in enumerate(calls):
                    name = c["name"]
                    call_id = c["id"] or f"call_{i}"
                    args, bad_args = _parse_tool_args(c["args"])
                    if i in runs:
                        for j, a in runs[i].items():
                            scope = batch_scopes[j] = CancelScope()
                            batch[j] = asyncio.ensure_future(self._bounded_task(a, call_history, scope))
                    yield Event("tool_use", {"name": name, "input": args, "id": call_id})
                    if bad_args is not None:
                        # Do NOT run the tool on {} — it would report a missing
                        # argument the model actually sent, and invite an identical
                        # retry. The parse failure IS the result.
                        result_text, is_error, stop = bad_args, True, False
                    elif i in batch:
                        try:
                            result_text, is_error, stop = await asyncio.shield(batch[i])
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:  # one subagent's failure is its own result
                            result_text, is_error, stop = f"Error: {type(e).__name__}: {e}", True, False
                    else:
                        try:
                            result_text, is_error, stop = await self._guarded_exec(
                                name, args, call_history)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:  # a handler's own crash is its result
                            result_text, is_error, stop = f"Error: {type(e).__name__}: {e}", True, False
                    if name.lower() == "done" and not isinstance(result_text, _LoopGuardResult):
                        # Observe the lead result here so argument/permission/
                        # lookup failures cannot bypass delivery bookkeeping.
                        self._delivery_attempt = {"status": "failed" if is_error else "prepared"}
                        if is_error:
                            self._verify_at_turn_end = None
                            self._last_done_path = None
                            self._delivery_review = {"status": "not_requested"}
                    loop_break = loop_break or stop
                    # Append BEFORE yielding: if the consumer abandons this generator
                    # right after the event (tool-budget cap in engine.ask, or a
                    # Ctrl-C), the assistant `tool_calls` must not be left without its
                    # matching `tool` message — a dangling pair 400s the next turn.
                    # The history copy is clamped; the event keeps the full result.
                    self.messages.append({"role": "tool", "tool_call_id": call_id,
                                          "content": _clamp(result_text, _TOOL_RESULT_CAP)})
                    round_images.extend(getattr(result_text, "images", []))
                    recorded = i + 1
                    yield Event("tool_result", {"name": name, "content": result_text,
                                                "is_error": is_error, "id": call_id})
            finally:
                for index, fut in batch.items():
                    if not fut.done():
                        batch_scopes[index].cancel()
                    elif not fut.cancelled() and fut.exception() is not None:
                        pass  # retrieved: asyncio must not log it at GC
                # A cancellation landing inside a handler (Ctrl-C) skips the rest
                # of the round's calls — stub them so the pairing still balances.
                for i in range(recorded, len(calls)):
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": calls[i]["id"] or f"call_{i}",
                        "content": _INTERRUPTED,
                    })
                if batch:
                    await asyncio.gather(*batch.values(), return_exceptions=True)

            if round_images:
                self.messages.append(_visual_message(round_images))

            # Notice a turn that is looking without converging. The loop guard above
            # catches a call REPEATED with the same arguments and the same result; it
            # cannot see a session whose every call differs and yet changes nothing
            # (live 2026-09-20: 114 calls over four hours on one visual detail, two
            # files edited). Counts calls since one that changed something.
            for call in calls:
                if (call.get("name") or "").lower() in _CHANGING_TOOLS:
                    since_change = 0
                    break
            else:
                since_change += len(calls)
            if since_change in _NO_PROGRESS_AT:
                last_tool = next((m for m in reversed(self.messages) if m.get("role") == "tool"), None)
                if last_tool is not None and isinstance(last_tool.get("content"), str):
                    last_tool["content"] += (
                        f"\n\n[Dream: {since_change} tool calls since anything changed — these have all been "
                        "looks, not edits. If you know the fix, make it now. If you are still narrowing it "
                        "down, say in one line what you are testing and what result would settle it.]")

            # Say when the turn's round budget runs low (Dream fix #24): live, a
            # model spent ~60 rounds on measurements and hit the limit mid-sweep.
            # A round of only looks may cost half (fix #17), so a mark is crossed, not hit.
            half = self._round_cost(calls) < 1
            spent -= 0.5 if half else 0
            left = _MAX_TOOL_ROUNDS - spent
            crossed = any(left <= mark < prev_left for mark in (25, 10, 3))
            prev_left = left
            if crossed:
                last_tool = next((m for m in reversed(self.messages) if m.get("role") == "tool"), None)
                if last_tool is not None and isinstance(last_tool.get("content"), str):
                    last_tool["content"] += (f"\n\n[Dream: {left:g} tool rounds left in this turn"
                                             + (" (a round of only reads costs half)" if self._cache_sensitive()
                                                else "")
                                             + ". Batch checks into fewer calls; finish the current step and "
                                             "report before they run out.]")

            if loop_break:
                inbox = getattr(self, 'steering_inbox', None)
                if inbox is not None:
                    await inbox.close()
                yield Event("system", "Loop guard: ended the turn — the model kept "
                                      "repeating the same tool call after being "
                                      "warned and blocked. Salvaging what it found…")
                # The tools already ran; their results are real. Ending here without
                # them is throwing away the turn's actual work.
                rescued = await self._salvage(
                    "the loop guard stopped you repeating a tool call that kept "
                    "returning the same result")
                if rescued:
                    self.messages.append({"role": "assistant", "content": rescued})
                    yield Event("assistant_done", rescued)
                else:
                    yield Event("system", "Nothing could be salvaged — the findings "
                                          "above are all there is.")
                turn_text = self._turn_text(prompt)
                for ev in await self._run_verifier_sweep():
                    yield ev
                for ev in await self._finish_filing(turn_text):
                    yield ev
                yield Event("result", {
                    "is_error": True,
                    "num_turns": 1,
                    "usage": self.last_usage,
                    "total_cost_usd": None,
                    "subtype": "loop_detected",
                    "delivery_review": dict(self._delivery_review),
                    "delivery_attempt": dict(self._delivery_attempt),
                    "stats": self._turn_stats(agg, time.monotonic() - turn_t0),
                })
                return

        inbox = getattr(self, 'steering_inbox', None)
        if inbox is not None:
            await inbox.close()
        yield Event("system", f"Reached the tool-round limit ({_MAX_TOOL_ROUNDS}) for this turn. "
                              "One round may contain multiple tool calls; this is not a context or time limit. "
                              + ("On this local engine a round of only reads counted half. "
                                 if self._cache_sensitive() else "")
                              + "Preserving partial findings…")
        rescued = await self._salvage(
            f"you reached the limit of {_MAX_TOOL_ROUNDS} tool rounds for one turn "
            "(a round may contain multiple tool calls)")
        if rescued:
            self.messages.append({"role": "assistant", "content": rescued})
            yield Event("assistant_done", rescued)
        turn_text = self._turn_text(prompt)
        for ev in await self._run_verifier_sweep():
            yield ev
        for ev in await self._finish_filing(turn_text):
            yield ev
        yield Event("result", {
            "is_error": True,
            "num_turns": _MAX_TOOL_ROUNDS,
            "usage": self.last_usage,
            "total_cost_usd": None,
            "subtype": "tool_round_limit",
            "delivery_review": dict(self._delivery_review),
            "delivery_attempt": dict(self._delivery_attempt),
            "tool_round_limit": _MAX_TOOL_ROUNDS,
            "stats": self._turn_stats(agg, time.monotonic() - turn_t0),
        })

    def _turn_stats(self, agg: dict[str, Any], duration_s: float) -> dict[str, Any]:
        return {
            "pp_tps": (agg["pp_n"] / (agg["pp_ms"] / 1000.0)) if agg["pp_ms"] > 0 else None,
            "gen_tps": (agg["gen_n"] / (agg["gen_ms"] / 1000.0)) if agg["gen_ms"] > 0 else None,
            "prompt_tokens": agg["prompt_tokens"] + self._delegated_usage["prompt_tokens"],
            "completion_tokens": agg["completion_tokens"] + self._delegated_usage["completion_tokens"],
            "delegated_usage": dict(self._delegated_usage),
            "ctx_used": agg["ctx_used"],
            "n_ctx": self.n_ctx,
            "duration_s": duration_s,
        }

    async def _bounded_task(self, args: dict, history: dict, scope: CancelScope | None = None):
        # A sibling finishing cancellation must not Task.cancel this worker
        # through its transport's shielded cleanup. The parent cancels this
        # scope and joins the task. A batch closed before scheduling runs no tool.
        with scope or CancelScope() as scope:
            if not scope.cancel_called:
                async with self._task_slots:
                    return await self._guarded_exec("task", args, history)
        raise asyncio.CancelledError("Delegate interrupted.")

    def _effort_params(self) -> dict[str, Any]:
        if self._active_performance is not None:
            effort = self._active_performance["reasoning_effort"]
            params = {"reasoning_effort": effort} if effort else {}
        else:
            params = self._base_effort_params()
        return self._adapt_effort(params)

    def _adapt_effort(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fix #40 (2026-09-21/22): with the effort fixed at high, orientation steps -- the model
        deciding which file to read next -- cost 3-18 minutes of hidden reasoning each. A request
        that follows a round of nothing but read-only tools runs at medium; the first call of a
        turn, and any call after a write, an edit or a consequential shell command, keeps the
        configured effort. An explicit /effort wins; DREAM_ADAPTIVE_EFFORT=0 turns this off."""
        if not params or self._effort is not None or os.environ.get("DREAM_ADAPTIVE_EFFORT", "1") == "0":
            return params
        if getattr(self, "_active_performance_mode", "custom") not in (None, "custom"):
            return params          # a chosen performance mode is explicit intent, like /effort
        if not self._orientation_round():
            return params
        try:
            lowered = self._native_effort("med")
        except ValueError:
            return params
        return {**params, "reasoning_effort": lowered}

    def _orientation_round(self) -> bool:
        """True when the next request follows a completed round whose tool calls were all reads."""
        if not self.messages or self.messages[-1].get("role") != "tool":
            return False
        for m in reversed(self.messages):
            if m.get("role") != "assistant":
                continue
            calls = m.get("tool_calls") or []
            if not calls:
                return False
            for tc in calls:
                fn = tc.get("function") or {}
                name = str(fn.get("name") or "").split("__")[-1]
                cap = policy.capability(name)
                if cap in {policy.READONLY, policy.MEMORY}:
                    continue
                if cap == policy.SHELL:
                    try:
                        command = str((json.loads(fn.get("arguments") or "{}") or {}).get("command") or "")
                    except ValueError:
                        return False
                    if policy.shell_read_only(command):
                        continue
                return False
            return True
        return False

    def _base_effort_params(self) -> dict[str, Any]:
        """reasoning_effort fragment for the request, or {} when no effort is set."""
        selected = (self._local_options.get("reasoning_effort")
                    if self.provider.key == 'machx' and self.model == self._local_options_model else None)
        effort = self._effort if self._effort is not None else selected
        return {'reasoning_effort': self._native_effort(effort)} if effort is not None else {}

    def _native_effort(self, level: str) -> str:
        """Honor explicit model vocabulary; otherwise use the adapter mapping.

        An adapter mapping permits a request, not a claim of model support.
        Known reported ladders, including an empty ladder, constrain requests.
        """
        if not isinstance(level, str) or not level.strip():
            raise ValueError('Reasoning effort must be a nonempty supported level.')
        native = level.strip().lower()
        reported = self.capability_status()['reasoning_levels']
        if reported['known'] and native in reported['value']:
            return native
        native = 'medium' if native == 'med' else native
        if self.provider.key == 'machx':
            from ...local.settings import BY_NAME
            if native not in BY_NAME['reasoning_effort'].choices:
                raise ValueError('Reasoning effort is not supported by this adapter or reported model.')
        else:
            # Keep the established med/max/ultra translation when no exact
            # reported native value applies. Do not silently discard errors.
            from ..effort import for_openai
            if native != 'low':
                try:
                    native = for_openai(level)['reasoning_effort']
                except ValueError:
                    raise ValueError('Reasoning effort is not supported by this adapter or reported model.') from None
        if reported['known'] and native not in reported['value']:
            raise ValueError('Reasoning effort is not supported by the reported model.')
        return native

    async def interrupt(self) -> None:
        # Unregister before cancelling so repeated Stop does not reissue a
        # cancellation during response cleanup. No server-idle claim.
        self._interrupts.generation += 1
        for scope in tuple(self._interrupts.active):
            self._interrupts.active.discard(scope)
            scope.cancel()

    async def set_model(self, model: str | None) -> None:
        if model == self.model:
            return
        if model:
            if model != self.model:
                # Launch options describe one loaded model. Reusing its sampling,
                # output ceiling or usage after a model change invents compatibility.
                self._local_options = {}
                self._image_rejection_model = None
                self._local_capabilities = {}
                self._provider_metadata = None
                self._server_props = None
                self._props_warning = None
                self.n_ctx = None
                self.temperature = self._default_temperature
                self._sampling = self._build_sampling()
                self._context_overflow = 'compact'
                self._last_prompt_tokens = 0
                self.last_usage = None
                self.context_report = None
                self._active_performance = None
                self._schema_cache = None
            self.model = model
            self._configure_tool_images()
            self._performance = None

    async def context_usage(self) -> dict[str, Any] | None:
        return self.last_usage

    async def disconnect(self) -> None:
        await self.close_background()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
