"""DREAM-127 (fix list #101, #102).

#101: the model answered Dream's own mid-turn notes with "That [id:m0015] marker isn't mine -- ignoring it"
(five times in one live session). A note Dream writes (the progress guard,
the notice after a reply cut at the output ceiling) reaches the model without the owner's [id:mNNNN] tag and opens
with "[Dream"; the owner's own corrections keep their ids, which `snip` names. The system prompt says what both are.

#102: machx.log had no timestamps and the runtime events no wall clock or request id, so the two logs were joined
by token-count arithmetic. Each engine line is now stamped with local wall-clock time as it is written; every runtime
event carries `ts`; a usage event carries the engine's reply id; scripts/session_report.py joins the two.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import dream.config as config
from dream.core import system_prompt, turn_origin
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.local import machx
from dream.telemetry.runtime import RunMeter

REPO = Path(__file__).resolve().parents[1]
STAMP = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] ")


def _backend():
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False, api_key=lambda: "n")
    b = OpenAICompatBackend(provider=p, model="m", system_prompt="SYSTEM PROMPT", tools=[], permission_cb=None)
    b.n_ctx = None
    return b


class _Inbox:
    def __init__(self, receipts):
        self.receipts = receipts

    async def drain(self, *, final=False):
        out, self.receipts = self.receipts, []
        return out

    async def submitted(self):
        pass


# --- #101 ----------------------------------------------------------------------------------------

def test_dream_notes_reach_the_model_without_an_id_tag_and_the_owners_keep_theirs():
    b = _backend()
    b.steering_inbox = _Inbox([
        {"id": "a" * 32, "text": "[Dream progress guard] 12 consecutive read-only steps.",
         "origin": turn_origin.PROGRESS_GUARD},
        {"id": "b" * 32, "text": "make the wheels chrome"},
        {"id": "c" * 32, "text": "[Dream] Your last reply reached the 16,384-token output ceiling.",
         "origin": turn_origin.LENGTH},
    ])
    assert asyncio.run(b._apply_steering())
    guard, owner, length = b.messages[-3:]
    assert guard["content"] == "[Dream progress guard] 12 consecutive read-only steps."
    assert length["content"] == "[Dream] Your last reply reached the 16,384-token output ceiling."
    assert owner["content"] == "make the wheels chrome\n\n[id:m0001]"
    # `snip` still names the owner's correction; Dream's notes were never the owner's to snip.
    assert [i for _, i in b._user_ids()] == ["m0001"]


@pytest.mark.parametrize("text, sent", [
    ("Act now: one call.", "[Dream] Act now: one call."),
    ("[Dreamy] not an opening", "[Dream] [Dreamy] not an opening"),
    ("[Dream progress guard] 12 steps.", "[Dream progress guard] 12 steps."),
    ("[Dream, not from the user: x]", "[Dream, not from the user: x]"),
])
def test_a_dream_note_always_reads_as_dreams(text, sent):
    b = _backend()
    b.steering_inbox = _Inbox([{"id": "d" * 32, "text": text, "origin": turn_origin.PROGRESS_GUARD}])
    asyncio.run(b._apply_steering())
    assert b.messages[-1]["content"] == sent


@pytest.mark.parametrize("base", [system_prompt.BASE, system_prompt.COMPACT_BASE], ids=["BASE", "COMPACT_BASE"])
def test_the_system_prompt_says_what_the_id_tag_and_dreams_notes_are(base):
    assert "[id:mNNNN]" in base and "[Dream" in base
    # Gate round 1: it must not hand authority to any text that merely starts with "[Dream" (a fetched page could).
    start = base.index("[id:mNNNN]")
    sentence = base[base.rindex("\n", 0, start - 40):base.index(".", base.index("[Dream", start)) + 200]
    assert not re.search(r"\b(follow|obey)", sentence, re.I)
    assert "harness" in sentence


# --- #102 (b): wall clock and the engine's reply id on the runtime events --------------------------

def _meter(tmp_path):
    return RunMeter("s", 1, SimpleNamespace(max_run_tools=10, max_run_tokens=None, max_run_seconds=None),
                    path=tmp_path / "run.jsonl")


def test_every_runtime_event_carries_wall_clock_time(tmp_path):
    before = time.time()
    _meter(tmp_path).record("turn_started")
    event = json.loads((tmp_path / "run.jsonl").read_text())
    assert before - 1 <= event["ts"] <= time.time() + 1


def test_the_usage_event_carries_the_engines_reply_id(tmp_path):
    meter = _meter(tmp_path)
    meter.usage({"prompt_tokens": 5, "completion_tokens": 1, "response_id": "chatcmpl-7"})
    meter.usage({"prompt_tokens": 5, "completion_tokens": 1})
    first, second = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert first["response_id"] == "chatcmpl-7" and "response_id" not in second


class _Stream:
    def __init__(self, lines):
        self.lines = lines

    async def __aenter__(self):
        lines = self.lines

        class _Response:
            status_code = 200

            async def aiter_lines(self):
                for line in lines:
                    yield line

            async def aread(self):
                return b""
        return _Response()

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, lines):
        self.lines, self.payloads = lines, []

    def stream(self, method, url, json=None):
        self.payloads.append(copy.deepcopy(json))
        return _Stream(self.lines)


async def test_a_lead_request_records_the_engines_reply_id(tmp_path):
    b = _backend()
    b._client = _Client([
        "data: " + json.dumps({"id": "chatcmpl-42", "choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}),
        "data: " + json.dumps({"id": "chatcmpl-42", "choices": [],
                               "usage": {"prompt_tokens": 120, "completion_tokens": 3}}),
        "data: [DONE]"])
    b.runtime_meter = _meter(tmp_path)
    [ev async for ev in b.ask("go")]
    usage = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()
             if json.loads(line)["event"] == "usage"]
    assert [(u["response_id"], u["input_tokens"]) for u in usage] == [("chatcmpl-42", 120)]


# --- #102 (a): the engine log's lines are stamped as they are written --------------------------------

def _stamper():
    return subprocess.Popen([sys.executable, "-I", str(REPO / "dream" / "local" / "log_stamp.py")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def test_the_stamper_prefixes_every_line_and_keeps_the_engines_text():
    proc = _stamper()
    out, _ = proc.communicate(b"[gen] prefill 5 tok (0 cached) / 9 ms\n\nsecond\ntail without newline", timeout=20)
    lines = out.decode().split("\n")
    assert lines[-1] == ""
    assert all(STAMP.match(line) for line in lines[:-1])
    assert [STAMP.sub("", line) for line in lines[:-1]] == [
        "[gen] prefill 5 tok (0 cached) / 9 ms", "", "second", "tail without newline"]


def test_the_stamper_outlives_the_engines_stop_signal_until_the_output_ends():
    """stop() signals the engine's whole process group, the stamper included: it must keep draining,
    or the engine's last lines are lost (and its next write would meet a closed pipe)."""
    proc = _stamper()
    proc.stdin.write(b"first\n")
    proc.stdin.flush()
    time.sleep(0.5)                      # let it install its handlers
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.2)
    out, _ = proc.communicate(b"shutdown line\n", timeout=20)
    assert [STAMP.sub("", line) for line in out.decode().splitlines()] == ["first", "shutdown line"]


def test_carriage_return_progress_reaches_the_log_as_it_is_written(tmp_path):
    """Gate round 1: the DeepSeek-V4 loader prints per-layer progress with "\\r" and no "\\n" for minutes. Held back
    as a partial line, the log stopped growing and wait_ready called a live load stalled."""
    out = tmp_path / "log"
    with out.open("ab") as log:
        proc = subprocess.Popen([sys.executable, "-I", str(REPO / "dream" / "local" / "log_stamp.py")],
                                stdin=subprocess.PIPE, stdout=log)
    proc.stdin.write(b"layer 1/43\r")
    proc.stdin.flush()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and out.stat().st_size == 0:
        time.sleep(0.05)
    grown = out.stat().st_size
    proc.stdin.write(b"layer 2/43\rloaded\r\n[ie] serving\n")
    proc.communicate(timeout=20)
    text = out.read_bytes()
    assert grown > 0
    # Every segment is stamped; without the stamps the engine's bytes are exactly what it wrote.
    assert re.sub(rb"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] ", b"", text) == \
        b"layer 1/43\rlayer 2/43\rloaded\r\n[ie] serving\n"
    assert len(re.findall(rb"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] ", text)) == 4


def test_a_long_unfinished_line_costs_linear_time_and_stays_valid_utf8(tmp_path):
    """Gate follow-up: re-scanning the whole unfinished line on every read was quadratic (a 270 KB line with no
    newline did not finish in 60 s) and, while it scanned, the engine's writes to the pipe blocked. A forced flush of
    an over-long line must not cut a multibyte character either."""
    import threading
    data = ("漢" * 70_000).encode()            # 210 KB, no line break until the end
    out = tmp_path / "log"
    with out.open("ab") as log:
        proc = subprocess.Popen([sys.executable, "-I", str(REPO / "dream" / "local" / "log_stamp.py")],
                                stdin=subprocess.PIPE, stdout=log)

    def feed():
        for i in range(0, len(data), 509):
            proc.stdin.write(data[i:i + 509])
            proc.stdin.flush()
        proc.stdin.write(b"\n")
        proc.stdin.close()
    writer = threading.Thread(target=feed, daemon=True)
    t0 = time.monotonic()
    writer.start()
    writer.join(10)
    try:
        assert not writer.is_alive(), "the stamper stopped reading its pipe"
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert time.monotonic() - t0 < 10
    text = out.read_bytes()
    assert STAMP.match(text.decode())
    assert re.sub(rb"\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\] ", b"", text) == data + b"\n"
    text.decode("utf-8")                    # no stamp inside a character


def test_wait_ready_does_not_call_a_carriage_return_load_stalled(monkeypatch, tmp_path):
    engine = tmp_path / "engine"
    (engine / "scripts").mkdir(parents=True)
    (engine / "scripts" / "env.sh").write_text("")
    ie = engine / "build" / "src" / "ie"
    ie.parent.mkdir(parents=True)
    ie.write_text("#!/bin/bash\nfor i in $(seq 1 20); do printf 'layer %d/43\\r' $i; sleep 0.25; done\n")
    ie.chmod(0o755)

    @contextmanager
    def no_lock(port):
        r, w = os.pipe()
        try:
            yield r
        finally:
            os.close(r)
            os.close(w)
    monkeypatch.setattr("dream.local.load_lock.load_lock", no_lock)
    monkeypatch.setattr(machx, "MACHX_DIR", engine)
    monkeypatch.setattr(machx, "PORT", 1)
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: False)
    monkeypatch.setattr(machx.config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(machx.config, "VAR_DIR", tmp_path)
    proc = machx.serve(Path("stand-in.gguf"), keep_hot=True)
    t0 = time.monotonic()
    ready = machx.wait_ready(proc, stall_s=1.5, max_s=3.0, poll_s=0.1)
    waited = time.monotonic() - t0
    proc.wait(20)
    assert ready is False and waited >= 2.9      # ran to the backstop: the progress kept the load alive


def test_serve_routes_the_engines_output_through_the_stamper(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(machx.config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(machx.config, "VAR_DIR", tmp_path)
    monkeypatch.setattr(machx.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(pid=42))
    machx.serve(tmp_path / "model.gguf", gpus=1, ctx=8192)
    (args, kw), = calls                                          # one process: the engine, still exec'd
    script = args[-1]
    assert "log_stamp.py" in script and ">(" in script
    assert shlex.split(script.split(" && exec ", 1)[1])[:2] == ["./build/src/ie", "serve"]


def test_capabilities_output_is_not_stamped(monkeypatch):
    seen = []
    monkeypatch.setattr(machx.subprocess, "run", lambda args, **kw: seen.append(args) or SimpleNamespace(
        returncode=0, stderr="", stdout='{"schema_version": 1}'))
    machx.capabilities(Path("model.gguf"))
    assert "log_stamp" not in seen[0][-1]


def test_a_launched_engines_lines_land_stamped_in_the_log(monkeypatch, tmp_path):
    engine = tmp_path / "engine"
    (engine / "scripts").mkdir(parents=True)
    (engine / "scripts" / "env.sh").write_text("")
    ie = engine / "build" / "src" / "ie"
    ie.parent.mkdir(parents=True)
    ie.write_text("#!/bin/sh\necho '[ie] serving'\necho '[gen] prefill 7 tok (0 cached) / 3 ms' >&2\n")
    ie.chmod(0o755)

    @contextmanager
    def no_lock(port):
        r, w = os.pipe()
        try:
            yield r
        finally:
            os.close(r)
            os.close(w)
    monkeypatch.setattr("dream.local.load_lock.load_lock", no_lock)
    monkeypatch.setattr(machx, "MACHX_DIR", engine)
    monkeypatch.setattr(machx, "PORT", 1)
    monkeypatch.setattr(machx.config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(machx.config, "VAR_DIR", tmp_path)
    proc = machx.serve(Path("stand-in.gguf"), keep_hot=True)
    proc.wait(20)
    log = tmp_path / "logs" / "machx.log"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and log.read_text().count("\n") < 2:
        time.sleep(0.1)
    lines = log.read_text().splitlines()
    assert [STAMP.sub("", line) for line in lines] == ["[ie] serving", "[gen] prefill 7 tok (0 cached) / 3 ms"]
    assert all(STAMP.match(line) for line in lines)


# --- #102 (c): the session report ---------------------------------------------------------------------

def _stamp(t):
    return time.strftime("[%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}] "


def _fixture(root, t0=1_790_000_000.0):
    runtime = root / "var" / "logs" / "runtime"
    runtime.mkdir(parents=True)
    events = [
        {"event": "turn_started", "session": "S", "turn": 1, "elapsed_s": 0.0, "ts": t0},
        {"event": "usage", "session": "S", "turn": 1, "elapsed_s": 60.0, "ts": t0 + 60, "input_tokens": 20000,
         "output_tokens": 100, "cached_tokens": 0, "response_id": "chatcmpl-1"},
        {"event": "tool_started", "session": "S", "turn": 1, "elapsed_s": 61.0, "ts": t0 + 61, "tool": "see"},
        {"event": "usage", "session": "S", "turn": 1, "elapsed_s": 90.0, "ts": t0 + 90, "input_tokens": 20400,
         "output_tokens": 50, "cached_tokens": 20100, "response_id": "chatcmpl-2"},
        {"event": "context_compacted", "session": "S", "turn": 1, "elapsed_s": 91.0, "ts": t0 + 91, "messages": 9},
        {"event": "usage", "session": "S", "turn": 1, "elapsed_s": 150.0, "ts": t0 + 150, "input_tokens": 12000,
         "output_tokens": 10, "cached_tokens": 0, "response_id": "chatcmpl-3"},
        {"event": "turn_timing", "session": "S", "turn": 1, "elapsed_s": 151.0, "ts": t0 + 151,
         "phases": {"tool_execution": {"count": 1, "seconds": 4.5}}},
    ]
    (runtime / "S.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    log = [
        "[gen] prefill 999 tok (0 cached) / 99999 ms  |  decode 9 tok / 9999 ms = 1.0 tok/s",   # older, unstamped
        _stamp(t0 - 3600) + "[gen] prefill 5 tok (0 cached) / 50000 ms  |  decode 5 tok / 5000 ms = 1.0 tok/s",
        _stamp(t0 + 2) + "[ie] serving",
        _stamp(t0 + 30) + "[mimo26] vision: image at 10, 50x80 patches -> 1000 tokens, encoded in 1500 ms",
        _stamp(t0 + 60.2) + "[gen] prefill 20000 tok (0 cached) / 40000 ms = 500 tok/s  |  decode 100 tok / 10000 ms = 10.0 tok/s",
        _stamp(t0 + 90.1) + "[gen] prefill 20400 tok (20100 cached) / 1000 ms  |  decode 50 tok / 5000 ms = 10.0 tok/s",
        _stamp(t0 + 150.3) + "[gen] prefill 12000 tok (0 cached) / 30000 ms  |  decode 10 tok / 1000 ms = 10.0 tok/s",
    ]
    (root / "var" / "logs" / "machx.log").write_text("\n".join(log) + "\n")


def _report(*args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "session_report.py"), *args],
                          capture_output=True, text=True, timeout=60)


def _row(out, label):
    line = next(line for line in out.splitlines() if line.startswith(f"| {label} "))
    return [cell.strip() for cell in line.strip("|").split("|")][1:]


def test_the_session_report_joins_the_two_logs_by_time(tmp_path):
    _fixture(tmp_path)
    result = _report("S", "--root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert _row(out, "Prefill") == ["3", "71.0", "32,300"]
    assert _row(out, "Re-reads (>10k uncached)") == ["2", "70.0", "32,000"]
    assert _row(out, "Decode") == ["3", "16.0", "160"]
    assert _row(out, "Vision encode") == ["1", "1.5", ""]
    assert _row(out, "Tool execution") == ["1", "4.5", ""]
    assert _row(out, "Compactions (logged)") == ["1", "", ""]
    assert _row(out, "Prompt drops (resets)") == ["1", "", ""]
    assert "3 of 3 usage events joined to an engine [gen] line" in out
    assert "0 [gen] line(s) in range not joined" in out


def test_the_report_says_when_an_engine_request_is_not_the_sessions(tmp_path):
    """Gate round 1: a foreign request inside the window was summed silently and the join still read "all joined"."""
    _fixture(tmp_path)
    log = tmp_path / "var" / "logs" / "machx.log"
    t0 = 1_790_000_000.0
    log.write_text(log.read_text() + _stamp(t0 + 120) +
                   "[gen] prefill 40000 tok (0 cached) / 80000 ms  |  decode 5 tok / 500 ms = 10.0 tok/s\n")
    out = _report("S", "--root", str(tmp_path)).stdout
    assert _row(out, "Prefill") == ["4", "151.0", "72,300"]
    assert "3 of 3 usage events joined" in out
    assert "1 [gen] line(s) in range not joined (still counted in the totals)" in out


def test_an_unstamped_log_needs_explicit_lines(tmp_path):
    _fixture(tmp_path)
    log = tmp_path / "var" / "logs" / "machx.log"
    log.write_text("\n".join(STAMP.sub("", line) for line in log.read_text().splitlines()) + "\n")
    out = _report("S", "--root", str(tmp_path)).stdout
    assert "no timestamped engine lines" in out
    result = _report("S", "--root", str(tmp_path), "--machx-lines", "5:7")
    assert result.returncode == 0, result.stderr
    assert _row(result.stdout, "Prefill") == ["3", "71.0", "32,300"]
    assert "same prompt size, in order)" in result.stdout and "within" not in result.stdout
