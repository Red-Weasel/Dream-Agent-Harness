"""The optional CPU helper counter for effective-context inspection (DREAM-257, phase 1): when it is used, what
it will and will not accept from its helper, and how it owns that process.

Nothing here calls a provider, a model, an engine, a GPU or an account, and no GGUF or tokenizer is loaded. The
"helper" is a small synthetic script this file writes into the test's own folder: it speaks the helper protocol
with a fixed behaviour (its count is the number of whitespace-separated words) and logs only process ids, request
ids and SHA-256 digests. The real C++ helper is not compiled or run by these tests."""
import asyncio
import hashlib
import json
import os
import signal
import sys
import threading
import time

import pytest

from dream.core import context_counter as cc
from dream.core import context_inspection as ci

MODEL = "fixture-model"

_FAKE = r'''#!@PYTHON@
import hashlib, json, os, signal, sys, time
MODE, LOG = @MODE@, @LOG@


def note(text):
    with open(LOG, "a") as log:
        log.write(text + "\n")


def put(obj):
    os.write(1, json.dumps(obj).encode() + b"\n")


args = sys.argv[1:]
if len(args) != 6 or args[0::2] != ["--gguf", "--session", "--open"]:
    sys.exit(2)
gguf, session, how = args[1], args[3], args[5]
note("start %d %s" % (os.getpid(), "pinned" if gguf.startswith("/proc/self/fd/") else "path"))
if MODE in ("ignore_term", "ignore_term_start"):
    signal.signal(signal.SIGUSR1, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    note("armed %d" % os.getpid())
if MODE in ("no_ready", "ignore_term_start"):
    time.sleep(20)
    sys.exit(0)
if MODE == "slow_ready":
    time.sleep(1.5)
info = os.stat(gguf)
with open(gguf, "rb") as source:
    data = source.read()
bos = MODE == "add_bos"
ready = {"v": 1, "type": "ready", "protocol": "dream-context-counter/1", "session": session,
         "counting": not bos, "diag": False,
         "tokenizer": {"vocab_size": 11, "pre": "fixture-pre", "pre_warning": False, "bos_token_id": 1,
                       "eos_token_id": 2, "pad_token_id": None, "add_bos": bos, "allow_special": False,
                       "empty_text_tokens": 1 if bos else 0},
         "gguf": {"version": 3, "tensors": 0, "file_size": info.st_size, "reader_file_size": info.st_size,
                  "header_bytes": len(data), "metadata_sha256": hashlib.sha256(data).hexdigest(), "open": how,
                  "dev": info.st_dev, "ino": info.st_ino, "mtime_ns": info.st_mtime_ns},
         "limits": {"line_bytes": 4194304, "text_bytes": 2097152}}
if bos:
    ready["reason"] = "bos_auto_add_unsupported"
if MODE == "bad_ready":
    ready["session"] = "0" * 32
if MODE == "wrong_file":
    ready["gguf"]["ino"] += 1
if MODE == "ready_not_json":
    os.write(1, b"loading model...\n")
else:
    put(ready)
if MODE in ("ignore_term", "never_read"):
    for _ in range(40):
        time.sleep(0.5)
    sys.exit(0)
seen = 0
for raw in sys.stdin.buffer:
    request = json.loads(raw)
    text = request["text"]
    seen += 1
    note("count %d %s" % (request["id"], hashlib.sha256(text.encode()).hexdigest()))
    answer = {"v": 1, "type": "count", "id": request["id"], "session": session, "ok": True,
              "count": len(text.split())}
    if MODE == "eof":
        sys.exit(0)
    if MODE == "silent":
        time.sleep(20)
        sys.exit(0)
    if MODE == "oversized":
        os.write(1, b"x" * 5000)
        time.sleep(20)
        sys.exit(0)
    if MODE == "garbage":
        os.write(1, b"not json at all\n")
        continue
    if MODE == "extra":
        os.write(1, json.dumps(answer).encode() + b"\n" + json.dumps(answer).encode() + b"\n")
        continue
    if MODE == "partial":
        line = json.dumps(answer).encode() + b"\n"
        for piece in (line[:5], line[5:20], line[20:]):
            os.write(1, piece)
            time.sleep(0.05)
        continue
    if MODE == "refuse_first" and seen == 1:
        del answer["count"]
        answer.update(ok=False, reason="encode_failed")
    if MODE == "bool":
        answer["count"] = True
    if MODE == "negative":
        answer["count"] = -1
    if MODE == "float":
        answer["count"] = 3.0
    if MODE == "string":
        answer["count"] = "3"
    if MODE == "huge":
        answer["count"] = 10 ** 9
    if MODE == "missing":
        del answer["count"]
    if MODE == "extra_key":
        answer["ids"] = [1, 2, 3]
    if MODE == "wrong_id":
        answer["id"] += 1
    if MODE == "wrong_session":
        answer["session"] = "0" * 32
    put(answer)
'''


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for name in (ci.ENV_TOKENIZER, ci.ENV_TOKENIZER_MODEL, "DREAM_CONTEXT_INSPECTION", ci.ENV_COUNTER,
                 cc.ENV_HELPER, cc.ENV_GGUF, cc.ENV_MODEL, cc.ENV_OPEN, cc.ENV_BUILD):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ci, "JOURNAL", ci.Journal())
    monkeypatch.setattr(ci, "TOKENIZER", ci.TextTokenizer())
    counter = cc.GgufHelperCounter()
    monkeypatch.setattr(cc, "COUNTER", counter)
    # generous for a loaded machine; the tests of a deadline set their own
    monkeypatch.setattr(cc, "START_TIMEOUT", 20.0)
    monkeypatch.setattr(cc, "CALL_TIMEOUT", 10.0)
    monkeypatch.setattr(cc, "CAPTURE_BUDGET", 30.0)
    yield
    counter.wait(5)
    counter.reset(wait=5.0)


class _Rig:
    """The synthetic helper, a stand-in "GGUF" (a few bytes: the fake only hashes it) and the configuration."""

    def __init__(self, tmp_path, monkeypatch):
        self.dir, self.monkeypatch = tmp_path, monkeypatch
        self.helper, self.gguf, self.log = tmp_path / "helper.py", tmp_path / "model.gguf", tmp_path / "helper.log"

    def write_helper(self, mode):
        source = (_FAKE.replace("@PYTHON@", sys.executable).replace("@MODE@", repr(mode))
                  .replace("@LOG@", repr(str(self.log))))
        fresh_file = self.dir / "helper.py.new"
        fresh_file.write_text(source)
        fresh_file.chmod(0o700)
        os.replace(fresh_file, self.helper)

    def install(self, mode="ok", model=MODEL):
        self.write_helper(mode)
        self.gguf.write_bytes(b"GGUF fixture header, not a model")
        self.monkeypatch.setenv(ci.ENV_COUNTER, cc.MODE)
        self.monkeypatch.setenv(cc.ENV_HELPER, str(self.helper))
        self.monkeypatch.setenv(cc.ENV_GGUF, str(self.gguf))
        self.monkeypatch.setenv(cc.ENV_MODEL, model)
        return self

    def lines(self, kind):
        if not self.log.exists():
            return []
        return [line.split()[1:] for line in self.log.read_text().splitlines() if line.startswith(kind + " ")]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    if not os.path.isdir("/proc/self/fd") or not hasattr(os, "mkfifo"):
        pytest.skip("the helper counter needs Linux's /proc/self/fd")
    return _Rig(tmp_path, monkeypatch)


def _started(model=MODEL):
    """Ask once (which starts the helper and is itself not counted), wait for the start, ask again."""
    _counter, first = cc.COUNTER.for_model(model)
    assert (first["method"], first["reason"]) == (None, "counter_helper_starting")
    assert cc.COUNTER.wait(30)
    return cc.COUNTER.for_model(model)


def _payload(system, *messages, model=MODEL):
    return {"model": model, "messages": [{"role": "system", "content": system}, *messages], "tools": [],
            "stream": False, "max_tokens": 64}


def _capture(payload, session="sess-A"):
    return ci.capture(payload, {}, session_id=session, private_id="private-1", transport="post", provider="fixture")


def _record(session="sess-A"):
    return ci.JOURNAL.get(session, ci.JOURNAL.list(session)[0]["request_id"])


# --- when the helper is used at all -----------------------------------------------------------------------------

def test_without_the_opt_in_nothing_changes_and_no_helper_is_started(rig):
    rig.install()
    rig.monkeypatch.delenv(ci.ENV_COUNTER)             # everything else is configured; the opt-in is not given
    _capture(_payload("a b c", {"role": "user", "content": "alpha beta"}))
    record = _record()
    # the existing default, key for key
    assert record["tokenizer"] == {"method": None, "reason": "tokenizer_not_configured", "digest": None,
                                   "bound_model": None, "engine_exact": False}
    assert {c["tokens_reason"] for c in record["components"]} == {"tokenizer_not_configured"}
    assert cc.COUNTER.starts == 0 and not rig.log.exists() and record["work"] == {"skipped": {}}
    rig.monkeypatch.setenv(ci.ENV_COUNTER, "")         # empty is unset
    _capture(_payload("a b c"))
    assert _record()["tokenizer"]["reason"] == "tokenizer_not_configured" and cc.COUNTER.starts == 0


def test_the_opt_in_takes_precedence_and_an_incomplete_or_unknown_one_counts_nothing(rig, tmp_path):
    rig.install()
    # a tokenizer.json binding beside it is not consulted once the helper counter is chosen
    rig.monkeypatch.setenv(ci.ENV_TOKENIZER, str(tmp_path / "absent.json"))
    rig.monkeypatch.setenv(ci.ENV_TOKENIZER_MODEL, MODEL)

    def reason():
        _capture(_payload("a b c", {"role": "user", "content": "alpha"}))
        record = _record()
        assert all(c["tokens"] is None for c in record["components"])
        assert {c["tokens_reason"] for c in record["components"]} == {record["tokenizer"]["reason"]}
        assert (record["tokenizer"]["method"], record["tokenizer"]["digest"], record["tokenizer"]["engine_exact"]) == (
            None, None, False)
        assert str(tmp_path) not in json.dumps(record)
        return record["tokenizer"]["reason"]

    rig.monkeypatch.setenv(ci.ENV_COUNTER, "some_other_counter")
    assert reason() == "counter_mode_unknown"
    rig.monkeypatch.setenv(ci.ENV_COUNTER, cc.MODE)
    rig.monkeypatch.delenv(cc.ENV_GGUF)
    assert reason() == "counter_not_configured"
    rig.monkeypatch.setenv(cc.ENV_GGUF, str(rig.gguf))
    rig.monkeypatch.delenv(cc.ENV_MODEL)
    assert reason() == "counter_model_not_bound"
    rig.monkeypatch.setenv(cc.ENV_MODEL, MODEL)
    rig.monkeypatch.setenv(cc.ENV_HELPER, "helper.py")                 # not absolute
    assert reason() == "counter_config_invalid"
    rig.monkeypatch.setenv(cc.ENV_HELPER, str(rig.helper))
    rig.monkeypatch.setenv(cc.ENV_OPEN, "whatever")
    assert reason() == "counter_config_invalid"
    assert cc.COUNTER.starts == 0 and not rig.log.exists()
    assert _record()["tokenizer"]["counter"] == {"kind": "gguf_cpu_helper"}       # which counter was chosen is said


def test_a_request_for_any_other_model_starts_no_child(rig):
    rig.install(model="the-bound-model")
    for other in (MODEL, "the-bound-model ", "The-Bound-Model", "the-bound-model:latest", ""):
        _capture(_payload("a b c", model=other))
        record = _record()
        assert record["tokenizer"]["reason"] == "counter_model_mismatch"
        assert record["components"][0]["tokens"] is None
    for odd in (None, 7, ["the-bound-model"]):
        assert cc.COUNTER.for_model(odd)[1]["reason"] == "counter_model_mismatch"
    assert cc.COUNTER.wait(1) and cc.COUNTER.starts == 0 and not rig.log.exists()


# --- counts ----------------------------------------------------------------------------------------------------

def test_the_first_request_is_not_counted_and_later_ones_carry_real_counts_and_the_counters_identity(rig):
    rig.install()
    rig.monkeypatch.setenv(cc.ENV_BUILD, "/home/owner/src engine@abc123")
    payload = _payload("a b c", {"role": "user", "content": "alpha beta"})
    _capture(payload)
    first = _record()
    # the start does not block the request, and a request made meanwhile is unknown, not zero
    assert first["tokenizer"]["reason"] == "counter_helper_starting"
    assert [c["tokens"] for c in first["components"]] == [None, None]
    assert cc.COUNTER.wait(30)
    _capture(payload)
    record = _record()
    head, user = record["components"]
    assert (head["tokens"], head["tokens_reason"], user["tokens"], user["tokens_reason"]) == (3, None, 2, None)
    described = record["tokenizer"]
    assert (described["method"], described["reason"], described["bound_model"], described["engine_exact"]) == (
        "engine_source_tokenizer_text", None, MODEL, False)
    identity = described["counter"]
    assert identity["helper_sha256"] == hashlib.sha256(rig.helper.read_bytes()).hexdigest()
    assert identity["gguf_metadata_sha256"] == hashlib.sha256(rig.gguf.read_bytes()).hexdigest()
    assert (identity["kind"], identity["protocol"], identity["vocab_size"], identity["pre"], identity["add_bos"],
            identity["allow_special"], identity["gguf_open"], identity["gguf_file_size"]) == (
        "gguf_cpu_helper", "dream-context-counter/1", 11, "fixture-pre", False, False, "pinned",
        rig.gguf.stat().st_size)
    # the binding is a declaration and no engine identity is claimed; an operator's label carries no path
    assert (identity["binding"], identity["engine_identity"]) == ("operator_declared", "not_established")
    assert identity["declared_build"] == "_home_owner_src_engine@abc123"
    assert len(described["digest"]) == 64 and described["digest"] != identity["helper_sha256"]
    assert described["coverage"]["counts"] == "piece_text_only"
    assert {"chat_template", "tool_formatting", "image_formatting"} <= set(described["coverage"]["excludes"])
    blob = json.dumps(record)
    assert str(rig.dir) not in blob and "/proc/" not in blob and "/home/owner" not in blob
    assert ci.JOURNAL.list("sess-A")[0]["tokenizer_method"] == "engine_source_tokenizer_text"
    assert "tokens" not in record["request"]                        # still no request total
    assert cc.COUNTER.starts == 1 and rig.lines("start")[0][1] == "pinned"


def test_a_real_zero_is_a_count_and_the_text_reaches_the_helper_byte_for_byte(rig):
    rig.install()
    counter, described = _started()
    assert described["reason"] is None
    blank = "  \n\t "
    assert counter(blank, _sha(blank)) == (0, None)                 # zero, with no reason: not "unknown"
    assert counter(blank, _sha(blank)) == (0, None)                 # remembered as zero too
    text = 'naïve 🚀 日本語\nline two\\n literal backslash-n\ttab \x00 nul "quoted"   <|im_end|>'
    assert counter(text, _sha(text)) == (len(text.split()), None)
    sent = [digest for _id, digest in rig.lines("count")]
    assert sent == [_sha(blank), _sha(text)]                        # the bytes sent hash as the caller's text does
    # text with no UTF-8 form is not altered to make it countable, and is never sent
    assert counter("lone \ud800 surrogate", "0" * 64) == (None, "component_not_utf8")
    assert counter(None, "0" * 64) == (None, "component_has_no_text")
    assert counter("a b", None) == (None, "component_has_no_text")
    assert len(rig.lines("count")) == 2 and cc.COUNTER.starts == 1


def test_a_remembered_count_costs_no_helper_call_and_the_inspectors_allowances_still_hold(rig, monkeypatch):
    rig.install()
    _started()
    payload = _payload("a b c", {"role": "user", "content": "alpha beta gamma delta"})
    _capture(payload)
    asked = len(rig.lines("count"))
    assert asked >= 2
    _capture(payload)
    assert len(rig.lines("count")) == asked                         # the same texts: nothing was sent again
    assert [c["tokens"] for c in _record()["components"]] == [3, 4]

    # the aggregate allowance of one capture: what is past it is unknown with the reason
    monkeypatch.setattr(ci, "MAX_COUNT_TOTAL_CHARS", 8)
    _capture(_payload("d e f", {"role": "user", "content": "one two three four"}), session="sess-budget")
    record = _record("sess-budget")
    head, user = record["components"]
    assert (head["tokens"], user["tokens"], user["tokens_reason"]) == (3, None, "count_budget_exhausted")
    assert record["work"]["skipped"]["count_budget_exhausted"] >= 1
    # the per-piece limit is the inspector's own
    monkeypatch.setattr(ci, "MAX_COUNT_TOTAL_CHARS", 1_200_000)
    monkeypatch.setattr(ci, "MAX_COUNT_CHARS", 6)
    _capture(_payload("g h i", {"role": "user", "content": "five six seven"}), session="sess-size")
    head, user = _record("sess-size")["components"]
    assert (head["tokens"], user["tokens"], user["tokens_reason"]) == (3, None, "component_too_large_to_count")
    assert cc.COUNTER.starts == 1


def test_a_large_text_is_written_in_parts_and_an_answer_arriving_in_parts_is_read_whole(rig):
    rig.install("partial")
    counter, described = _started()
    assert described["reason"] is None
    large = "word " * 60_000                      # 300,000 bytes: several times a pipe's buffer
    assert counter(large, _sha(large)) == (60_000, None)
    assert counter("a b", _sha("a b")) == (2, None)
    assert [digest for _id, digest in rig.lines("count")] == [_sha(large), _sha("a b")]


def test_concurrent_callers_each_get_their_own_answer(rig):
    rig.install()
    counter, described = _started()
    assert described["reason"] is None
    texts = {(thread, n): "w " * (thread * 20 + n + 1) for thread in range(6) for n in range(8)}
    results, errors = {}, []

    def work(thread):
        try:
            for n in range(8):
                text = texts[(thread, n)]
                results[(thread, n)] = counter(text, _sha(text))
        except BaseException as exc:                       # reported by the assertion below
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=work, args=(thread,)) for thread in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not errors and len(results) == 48
    assert all(results[key] == (key[0] * 20 + key[1] + 1, None) for key in texts)
    ids = [int(request_id) for request_id, _digest in rig.lines("count")]
    assert sorted(ids) == list(range(1, 49)) and cc.COUNTER.starts == 1


# --- a helper that does not keep to the protocol ---------------------------------------------------------------

@pytest.mark.parametrize("mode", ["bool", "negative", "float", "string", "huge", "missing", "extra_key",
                                  "wrong_id", "wrong_session", "garbage", "extra"])
def test_a_malformed_or_foreign_answer_is_unknown_never_a_count_and_the_helper_is_retired(rig, mode):
    rig.install(mode)
    counter, described = _started()
    assert described["reason"] is None
    assert counter("a b c", _sha("a b c")) == (None, "counter_response_invalid")
    # the helper is no longer in step: nothing more is asked of it, nothing was remembered
    assert counter("d e", _sha("d e")) == (None, "counter_response_invalid")
    assert len(rig.lines("count")) == 1
    again, after = cc.COUNTER.for_model(MODEL)
    assert (after["method"], after["reason"], after["digest"]) == (None, "counter_response_invalid", None)
    assert again("a b c", _sha("a b c")) == (None, "counter_response_invalid")
    assert cc.COUNTER.wait(1) and cc.COUNTER.starts == 1            # and it is not restarted at once


def test_a_refused_text_is_unknown_and_the_helper_stays_in_step(rig):
    rig.install("refuse_first")
    counter, _described = _started()
    assert counter("a b c", _sha("a b c")) == (None, "counter_encode_failed")
    assert counter("d e", _sha("d e")) == (2, None)
    assert counter("a b c", _sha("a b c")) == (3, None)             # the refusal was not remembered as a count
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] is None and cc.COUNTER.starts == 1


@pytest.mark.parametrize("mode, reason", [("eof", "counter_helper_exited"), ("silent", "counter_deadline"),
                                          ("oversized", "counter_response_oversized"),
                                          ("never_read", "counter_deadline")])
def test_an_exit_a_silence_or_an_endless_answer_ends_within_the_deadline_as_unknown(rig, monkeypatch, mode, reason):
    rig.install(mode)
    counter, described = _started()
    assert described["reason"] is None
    monkeypatch.setattr(cc, "CALL_TIMEOUT", 0.5)
    text = "word " * 60_000 if mode == "never_read" else "a b c"      # more than the pipe holds: the write stalls
    began = time.monotonic()
    assert counter(text, _sha(text)) == (None, reason)
    assert time.monotonic() - began < 8
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == reason and cc.COUNTER.starts == 1


def test_a_capture_out_of_time_asks_nothing_and_leaves_the_helper_running(rig, monkeypatch):
    rig.install()
    _started()
    monkeypatch.setattr(cc, "CAPTURE_BUDGET", 0.0)
    _capture(_payload("a b c", {"role": "user", "content": "alpha beta"}))
    record = _record()
    assert {c["tokens_reason"] for c in record["components"]} == {"counter_deadline"}
    assert all(c["tokens"] is None for c in record["components"])
    assert record["tokenizer"]["method"] == "engine_source_tokenizer_text"     # the counter was there; time was not
    assert record["work"]["skipped"]["counter_deadline"] >= 2 and rig.lines("count") == []
    monkeypatch.setattr(cc, "CAPTURE_BUDGET", 30.0)
    counter, described = cc.COUNTER.for_model(MODEL)
    assert described["reason"] is None and counter("a b c", _sha("a b c")) == (3, None) and cc.COUNTER.starts == 1


@pytest.mark.parametrize("mode, reason", [("bad_ready", "counter_ready_invalid"),
                                          ("ready_not_json", "counter_ready_invalid"),
                                          ("wrong_file", "counter_identity_mismatch"),
                                          ("add_bos", "counter_bos_policy_unsupported"),
                                          ("no_ready", "counter_deadline")])
def test_a_helper_that_does_not_start_as_required_gives_no_counts(rig, monkeypatch, mode, reason):
    if mode == "no_ready":
        monkeypatch.setattr(cc, "START_TIMEOUT", 0.5)
    rig.install(mode)
    counter, described = _started()
    assert (described["method"], described["reason"], described["digest"]) == (None, reason, None)
    assert counter("a b c", _sha("a b c")) == (None, reason)
    assert rig.lines("count") == [] and cc.COUNTER.starts == 1
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == reason and cc.COUNTER.wait(1) and cc.COUNTER.starts == 1


def test_a_slow_start_never_blocks_the_request_path(rig):
    rig.install("slow_ready")
    began = time.monotonic()
    _capture(_payload("a b c"))
    assert time.monotonic() - began < 1.0                    # the helper itself sleeps longer than this
    assert _record()["tokenizer"]["reason"] == "counter_helper_starting"
    _capture(_payload("a b c"))                              # still starting: not a second child
    assert cc.COUNTER.wait(30) and cc.COUNTER.starts == 1
    _capture(_payload("a b c"))
    assert _record()["components"][0]["tokens"] == 3


def test_a_failed_helper_is_started_again_only_after_the_retry_interval(rig, monkeypatch):
    rig.install("eof")
    counter, _described = _started()
    assert counter("a b c", _sha("a b c")) == (None, "counter_helper_exited")
    for _ in range(3):
        assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_exited"
    assert cc.COUNTER.wait(1) and cc.COUNTER.starts == 1
    # with no interval to wait, the next request starts a new one (and is itself not counted)
    fresh_counter = cc.GgufHelperCounter()
    monkeypatch.setattr(cc, "COUNTER", fresh_counter)
    monkeypatch.setattr(cc, "RETRY_AFTER", 0.0)
    try:
        counter, _described = _started()
        assert counter("a b c", _sha("a b c")) == (None, "counter_helper_exited")
        # The helper's pipe closes before its process has finished exiting; until it has, the next request answers
        # counter_helper_stopping (a live helper is never replaced). Wait for it, so the request below is the retry.
        fresh_counter._session.proc.wait(5)
        assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_starting"
        assert cc.COUNTER.wait(30) and cc.COUNTER.starts == 2
    finally:
        fresh_counter.wait(5)
        fresh_counter.reset(wait=5.0)


# --- identity and the files it is of -------------------------------------------------------------------------

def test_a_changed_gguf_or_helper_retires_the_helper_and_its_remembered_counts(rig):
    rig.install()
    counter, first = _started()
    assert counter("a b c", _sha("a b c")) == (3, None)
    old = cc.COUNTER._session.proc

    rig.gguf.write_bytes(b"another GGUF fixture header, of another length")
    stale, changed = cc.COUNTER.for_model(MODEL)
    assert changed["reason"] in ("counter_helper_starting", "counter_helper_stopping")
    assert stale("a b c", _sha("a b c"))[0] is None
    assert cc.COUNTER.wait(30)
    if changed["reason"] == "counter_helper_stopping":
        assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_starting"
        assert cc.COUNTER.wait(30)
    counter, second = cc.COUNTER.for_model(MODEL)
    assert second["reason"] is None and cc.COUNTER.starts == 2
    assert second["counter"]["gguf_metadata_sha256"] == hashlib.sha256(rig.gguf.read_bytes()).hexdigest()
    assert second["counter"]["gguf_metadata_sha256"] != first["counter"]["gguf_metadata_sha256"]
    assert second["digest"] != first["digest"]
    assert second["counter"]["helper_sha256"] == first["counter"]["helper_sha256"]
    asked = len(rig.lines("count"))
    assert counter("a b c", _sha("a b c")) == (3, None)
    assert len(rig.lines("count")) == asked + 1                      # asked again: the old count was not reused
    old.wait(10)                                                     # the old helper was told to stop, and did
    assert old.returncode is not None and old.returncode != -signal.SIGKILL

    rig.write_helper("partial")                                      # another program under the same path
    changing = cc.COUNTER.for_model(MODEL)[1]["reason"]
    assert changing in ("counter_helper_starting", "counter_helper_stopping")
    assert cc.COUNTER.wait(30)
    if changing == "counter_helper_stopping":
        assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_starting"
        assert cc.COUNTER.wait(30)
    _counter, third = cc.COUNTER.for_model(MODEL)
    assert third["reason"] is None and cc.COUNTER.starts == 3
    assert third["counter"]["helper_sha256"] == hashlib.sha256(rig.helper.read_bytes()).hexdigest()
    assert third["counter"]["helper_sha256"] != second["counter"]["helper_sha256"]
    assert third["digest"] not in (first["digest"], second["digest"])

    rig.gguf.unlink()                                                # the file is gone: nothing is counted
    gone, missing = cc.COUNTER.for_model(MODEL)
    assert missing["reason"] == "counter_gguf_unavailable" and gone("a b c", _sha("a b c"))[0] is None
    assert cc.COUNTER._session is None and cc.COUNTER.starts == 3


def test_path_mode_is_refused_before_probing_or_starting_a_helper(rig, monkeypatch):
    rig.install()
    monkeypatch.setenv(cc.ENV_OPEN, "path")
    def forbidden(*_args):
        raise AssertionError("path mode must not inspect files or launch")
    monkeypatch.setattr(cc, "_stat_keys", forbidden)
    counter, described = cc.COUNTER.for_model(MODEL)
    assert described["reason"] == "counter_path_mode_unsupported"
    assert counter("a b", _sha("a b")) == (None, "counter_path_mode_unsupported")
    assert cc.COUNTER.starts == 0 and not rig.log.exists()


def test_a_helper_or_gguf_that_is_not_a_trusted_regular_file_is_never_run_or_read(rig, tmp_path):
    rig.install()
    rig.helper.chmod(0o777)                                          # anyone could have rewritten it
    _counter, described = _started()
    assert described["reason"] == "counter_helper_untrusted"
    rig.helper.chmod(0o600)                                          # not executable
    cc.COUNTER.for_model(MODEL)
    assert cc.COUNTER.wait(30) and cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_untrusted"
    rig.helper.chmod(0o700)
    rig.gguf.chmod(0o666)
    cc.COUNTER.for_model(MODEL)
    assert cc.COUNTER.wait(30) and cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_gguf_untrusted"
    rig.gguf.chmod(0o644)

    # a pipe where a file belongs is refused at once, never opened for reading and never waited on
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    began = time.monotonic()
    rig.monkeypatch.setenv(cc.ENV_GGUF, str(fifo))
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_gguf_unavailable"
    rig.monkeypatch.setenv(cc.ENV_GGUF, str(rig.gguf))
    rig.monkeypatch.setenv(cc.ENV_HELPER, str(fifo))
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_unavailable"
    rig.monkeypatch.setenv(cc.ENV_HELPER, str(tmp_path))             # a folder
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_unavailable"
    rig.monkeypatch.setenv(cc.ENV_HELPER, str(tmp_path / "absent"))
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_unavailable"
    assert time.monotonic() - began < 2
    assert cc.COUNTER.wait(1) and cc.COUNTER.starts == 0 and not rig.log.exists()


# --- lifecycle -----------------------------------------------------------------------------------------------

def test_reset_closes_the_helper_softly_and_a_cancellation_is_never_swallowed(rig, monkeypatch):
    rig.install()
    counter, _described = _started()
    assert counter("a b c", _sha("a b c")) == (3, None)
    proc = cc.COUNTER._session.proc
    assert proc.poll() is None
    assert cc.COUNTER.reset(wait=10.0) == 0                          # every helper left within the wait
    assert proc.returncode is not None and proc.returncode != -signal.SIGKILL
    assert proc.stdin.closed and proc.stdout.closed
    # a counter handed out before the reset gives no stale count and reaches no process
    assert counter("d e", _sha("d e")) == (None, "counter_closed")
    assert len(rig.lines("count")) == 1

    # a cancellation in the middle of an exchange passes through as itself; the helper, out of step, is retired
    counter, _described = _started()
    real = cc._send

    def cancelled(*_args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(cc, "_send", cancelled)
    with pytest.raises(asyncio.CancelledError):
        counter("f g", _sha("f g"))
    monkeypatch.setattr(cc, "_send", real)
    assert counter("f g", _sha("f g")) == (None, "counter_interrupted")
    assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_interrupted" and cc.COUNTER.starts == 2


def test_a_helper_that_will_not_leave_is_left_alone_never_killed(rig):
    rig.install("ignore_term")
    _counter, described = _started()
    assert described["reason"] is None
    proc = cc.COUNTER._session.proc
    try:
        began = time.monotonic()
        assert cc.COUNTER.reset(wait=0.5) == 1                       # it was asked, it did not go
        assert time.monotonic() - began < 5
        assert proc.poll() is None                                   # still running: nothing escalated
        assert proc.stdin.closed and proc.stdout.closed              # and this process holds none of its pipes
    finally:
        proc.send_signal(signal.SIGUSR1)                            # fixture-only cooperative exit
        proc.wait(10)


@pytest.mark.parametrize("during_start", [False, True])
def test_retired_child_blocks_replacement_until_actual_exit(rig, monkeypatch, during_start):
    rig.install("ignore_term_start" if during_start else "ignore_term")
    if during_start:
        cc.COUNTER.for_model(MODEL)
        until = time.monotonic() + 5
        while not rig.lines("armed") and time.monotonic() < until:
            time.sleep(.01)
        assert rig.lines("armed")
        proc = cc.COUNTER._starting_proc
    else:
        _started()
        proc = cc.COUNTER._session.proc
    try:
        assert cc.COUNTER.reset(wait=.1) == 1
        assert not cc.COUNTER.wait(.01)
        monkeypatch.setattr(cc, "RETRY_AFTER", 0)
        for _ in range(5):
            assert cc.COUNTER.for_model(MODEL)[1]["reason"] in (
                "counter_helper_starting", "counter_helper_stopping")
        assert cc.COUNTER.starts == 1 and len(rig.lines("start")) == 1
        assert proc.poll() is None
    finally:
        proc.send_signal(signal.SIGUSR1)
        proc.wait(5)
        assert cc.COUNTER.wait(5)
    rig.write_helper("ok")
    counter, description = _started()
    assert description["reason"] is None
    assert counter("now usable", _sha("now usable")) == (2, None)
    assert cc.COUNTER.starts == 2


def test_reset_invalidates_already_cached_counter_closure(rig):
    rig.install()
    counter, _ = _started()
    assert counter("alpha beta", _sha("alpha beta")) == (2, None)
    cc.COUNTER.reset(wait=2)
    assert counter("alpha beta", _sha("alpha beta")) == (None, "counter_closed")
    assert len(rig.lines("count")) == 1 and not cc.COUNTER._counts


def test_reset_during_process_creation_preserves_pending_start(rig, monkeypatch):
    rig.install()
    entered, release = threading.Event(), threading.Event()
    original = cc.subprocess.Popen
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(cc.subprocess, "Popen", paused)
    cc.COUNTER.for_model(MODEL)
    assert entered.wait(5)
    try:
        assert cc.COUNTER.reset(wait=0) == 1
        assert not cc.COUNTER.wait(.01)
        assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_starting"
        assert cc.COUNTER.starts == 0
    finally:
        release.set()
        assert cc.COUNTER.wait(5)
    assert cc.COUNTER._session is None and not cc.COUNTER._retired
    assert cc.COUNTER.starts == 1


def test_configuration_retirement_rejects_inflight_response(rig, monkeypatch):
    rig.install("partial")
    counter, _ = _started()
    received, release = threading.Event(), threading.Event()
    original = cc._recv_line
    def paused(*args):
        line = original(*args)
        received.set()
        assert release.wait(5)
        return line
    monkeypatch.setattr(cc, "_recv_line", paused)
    result = []
    worker = threading.Thread(target=lambda: result.append(counter("alpha beta", _sha("alpha beta"))))
    worker.start()
    try:
        assert received.wait(5)
        monkeypatch.setenv(cc.ENV_BUILD, "changed-build")
        assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_stopping"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert result == [(None, "counter_closed")] and not cc.COUNTER._counts


def test_failed_uncooperative_helper_is_not_replaced_when_retry_expires(rig, monkeypatch):
    rig.install("ignore_term")
    counter, _ = _started()
    proc = cc.COUNTER._session.proc
    monkeypatch.setattr(cc, "CALL_TIMEOUT", .05)
    monkeypatch.setattr(cc, "RETRY_AFTER", 0)
    try:
        assert counter("alpha", _sha("alpha")) == (None, "counter_deadline")
        for _ in range(5):
            assert cc.COUNTER.for_model(MODEL)[1]["reason"] == "counter_helper_stopping"
        assert proc.poll() is None and cc.COUNTER.starts == 1
        assert not cc.COUNTER.wait(.01)
    finally:
        proc.send_signal(signal.SIGUSR1)
        proc.wait(5)
    assert cc.COUNTER.wait(5)
