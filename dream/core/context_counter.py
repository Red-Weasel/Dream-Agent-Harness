"""Optional CPU helper for the effective-context inspector's piece-text token counts (DREAM-257, phase 1).

What it does: when the operator opted in (DREAM_CONTEXT_COUNTER=gguf_helper in the environment Dream was started
with) and a request's model is EXACTLY the bound one, a local helper executable (scripts/
context_tokenizer_counter.cpp, built by the operator from the engine's own tokenizer sources) is started once, on
the CPU, and asked over a bounded JSON-lines pipe how many tokens each piece of text is. Nothing is downloaded, no
engine is contacted, stopped or signalled, and no text, path or helper output is logged or put in an exception.

What a count is: the engine-source tokenizer's count of ONE piece of text alone, with the tokenizer metadata of
the GGUF file the operator named (`engine_source_tokenizer_text`). It leaves out everything a rendered chat adds:
template, role markers, special tokens, tool and image formatting (COVERAGE). Text that spells a special token is
counted as ordinary text.

What it does not establish: that the GGUF is the checkpoint an engine is serving, or that the helper's build is a
running engine's binary. The binding to a model name is the operator's declaration. `engine_exact` is always
False here.

A count is a real integer from the helper (zero included) or None with a reason. Nothing is estimated.

Identity: the helper's SHA-256 is of the bytes behind the one descriptor the child is executed from
(/proc/self/fd/N); the GGUF facts are what that same child reported for the file it pinned, checked against the
descriptor this process holds on it. A changed helper or GGUF (device, inode, size, mtime, ctime) retires the
running helper and its remembered counts before anything else is counted.

Cost, said plainly: the first matching request starts the helper in a background thread and is itself NOT
counted (`counter_helper_starting`); loading a tokenizer from a GGUF maps the file and parses its vocabulary,
which takes real time and memory and is bounded here only by START_TIMEOUT. Counting blocks the caller for at
most CAPTURE_BUDGET seconds per capture and CALL_TIMEOUT per piece; the character allowances the inspector keeps
bound the work asked for, not the wall time it takes.

Linux only (it needs /proc/self/fd and poll); elsewhere every count is None with `counter_platform_unsupported`.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import secrets
import select
import stat
import subprocess
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

MODE = "gguf_helper"
KIND = "gguf_cpu_helper"
METHOD = "engine_source_tokenizer_text"
PROTOCOL = "dream-context-counter/1"

ENV_MODE = "DREAM_CONTEXT_COUNTER"              # "gguf_helper" opts in; unset keeps the inspector's own default
ENV_HELPER = "DREAM_CONTEXT_COUNTER_HELPER"     # absolute path of the helper executable
ENV_GGUF = "DREAM_CONTEXT_COUNTER_GGUF"         # absolute path of the GGUF whose tokenizer metadata is used
ENV_MODEL = "DREAM_CONTEXT_COUNTER_MODEL"       # the one request model name the helper is used for
ENV_OPEN = "DREAM_CONTEXT_COUNTER_GGUF_OPEN"    # "pinned" only; path-based reopening cannot establish identity
ENV_BUILD = "DREAM_CONTEXT_COUNTER_BUILD"       # optional operator label for the helper's source/build

START_TIMEOUT = 60.0        # seconds for the helper to say it is ready (in a background thread)
CALL_TIMEOUT = 2.0          # seconds for one piece: its write and its answer together
CAPTURE_BUDGET = 3.0        # seconds of helper time one capture may spend, all its pieces together
RETRY_AFTER = 60.0          # seconds before a failed helper is started again
TERMINATE_WAIT = 2.0        # seconds reset() waits for helpers it asked to stop

MAX_COUNT_CHARS = 400_000
MAX_TEXT_BYTES = 2 * 1024 * 1024          # the helper's own limit on one text
MAX_REQUEST_BYTES = 4 * 1024 * 1024       # the helper's own limit on one line
MAX_RESPONSE_BYTES = 512
MAX_READY_BYTES = 4096
MAX_HELPER_BYTES = 64_000_000
_COUNT_CACHE = 4096
_CHUNK = 65536

COVERAGE = {
    "counts": "piece_text_only",
    "excludes": ["chat_template", "role_markers", "special_tokens", "tool_formatting", "image_formatting",
                 "reasoning_content"],
    "note": ("Each count is the engine-source tokenizer's count of one piece of text alone. The rendered chat "
             "(template, role markers, special tokens, tool and image formatting) is not counted, and text that "
             "spells a special token is counted as ordinary text."),
}

# What the helper may say about itself, mapped to this module's reasons. Anything else is "counter_helper_failed":
# the helper's own words are never passed on.
_FATAL = {"gguf_open_failed": "counter_gguf_load_failed", "gguf_changed": "counter_source_changed",
          "gguf_header_unusable": "counter_gguf_load_failed", "tokenizer_load_failed": "counter_gguf_load_failed"}
_REFUSED = {"encode_failed": "counter_encode_failed", "text_too_large": "component_too_large_to_count",
            "bos_auto_add_unsupported": "counter_bos_policy_unsupported",
            "empty_text_not_zero": "counter_bos_policy_unsupported"}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PRE = re.compile(r"[A-Za-z0-9._?-]{0,64}\Z")
_LABEL = re.compile(r"[^A-Za-z0-9._:+@=-]")


class _Wire(Exception):
    """An exchange with the helper that did not go as the protocol says. Carries a reason code and nothing else."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _key(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_uid)


def _label(value: Any, limit: int = 120) -> str | None:
    """An operator's label as a short token with no path separators or spaces; None when there is none."""
    if not isinstance(value, str) or not value:
        return None
    return _LABEL.sub("_", value[:limit])


def _printable(value: str, limit: int) -> str:
    return "".join(ch if ch.isprintable() else " " for ch in value[:limit])


def _uint(value: Any, most: int = 2 ** 64 - 1) -> bool:
    return type(value) is int and 0 <= value <= most


def _token_id(value: Any) -> bool:
    return value is None or (type(value) is int and -2 ** 31 <= value < 2 ** 31)


def _loads(line: bytes) -> Any:
    try:
        return json.loads(line)
    except (ValueError, RecursionError):        # not UTF-8, not JSON, or absurdly nested
        return None


def _events(fd: int, mask: int, timeout_ms: float) -> int:
    poller = select.poll()
    poller.register(fd, mask)
    found = poller.poll(timeout_ms)
    return found[0][1] if found else 0


def _send(fd: int, data: bytes, deadline: float) -> None:
    """All of `data` into a non-blocking pipe before `deadline`, however slowly the reader takes it."""
    view = memoryview(data)
    while len(view):
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Wire("counter_deadline")
        if not _events(fd, select.POLLOUT, left * 1000.0):
            continue
        try:
            sent = os.write(fd, view[:_CHUNK])
        except BlockingIOError:
            continue
        except OSError:
            raise _Wire("counter_helper_exited") from None
        view = view[sent:]


def _recv_line(fd: int, buffer: bytearray, limit: int, deadline: float) -> bytes:
    """One line (without its newline) of at most `limit` bytes before `deadline`. What arrived after the newline
    stays in `buffer` for the caller to judge."""
    while True:
        at = buffer.find(b"\n")
        if at >= 0:
            if at > limit:
                raise _Wire("counter_response_oversized")
            line = bytes(buffer[:at])
            del buffer[:at + 1]
            return line
        if len(buffer) > limit:
            raise _Wire("counter_response_oversized")
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Wire("counter_deadline")
        if not _events(fd, select.POLLIN, left * 1000.0):
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        except OSError:
            raise _Wire("counter_helper_exited") from None
        if not chunk:
            raise _Wire("counter_helper_exited")
        buffer += chunk


def _shut(proc: subprocess.Popen) -> None:
    """Stop OUR helper, softly: its pipes are closed (it reads end-of-input and leaves) and it is sent SIGTERM if it
    is still there. Never SIGKILL, never waited for here, and never any process but this child."""
    for pipe in (proc.stdin, proc.stdout):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass
    if proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass


def _open_regular(path: str) -> tuple[int, os.stat_result]:
    """One descriptor on a regular file, opened without blocking (a FIFO where a file belongs cannot hang)."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def _hash_fd(fd: int, size: int) -> str | None:
    """SHA-256 of exactly `size` bytes behind `fd`, or None when the file is not that long any more."""
    digest, at = hashlib.sha256(), 0
    while at < size:
        chunk = os.pread(fd, min(1 << 20, size - at), at)
        if not chunk:
            return None
        digest.update(chunk)
        at += len(chunk)
    return None if os.pread(fd, 1, at) else digest.hexdigest()


def _trusted(info: os.stat_result) -> bool:
    """An executable this process may run: owned by this user or root, executable, not writable by group or
    others."""
    return (info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022 and bool(info.st_mode & 0o111))


def _parse_ready(line: bytes, token: str, open_mode: str, info: os.stat_result) -> tuple[dict | None, str | None]:
    """(the facts the helper reported, None) or (None, reason). Every field is checked for type and range; the
    file facts must be those of the descriptor this process holds on the GGUF."""
    bad = (None, "counter_ready_invalid")
    msg = _loads(line)
    if not isinstance(msg, dict) or type(msg.get("v")) is not int or msg["v"] != 1 or msg.get("session") != token:
        return bad
    if msg.get("type") == "fatal":
        said = msg.get("reason")
        return None, _FATAL.get(said, "counter_helper_failed") if isinstance(said, str) else "counter_helper_failed"
    tok, gguf = msg.get("tokenizer"), msg.get("gguf")
    if msg.get("type") != "ready" or msg.get("protocol") != PROTOCOL or msg.get("diag") is not False \
            or not isinstance(tok, dict) or not isinstance(gguf, dict) or type(msg.get("counting")) is not bool:
        return bad
    pre = tok.get("pre")
    if not (_uint(tok.get("vocab_size"), 2 ** 31) and tok["vocab_size"] > 0 and isinstance(pre, str)
            and _PRE.match(pre) and type(tok.get("pre_warning")) is bool
            and all(_token_id(tok.get(name)) for name in ("bos_token_id", "eos_token_id", "pad_token_id"))
            and tok.get("add_bos") in (True, False, None) and tok.get("allow_special") is False
            and _uint(tok.get("empty_text_tokens"), 2 ** 31)):
        return bad
    digest = gguf.get("metadata_sha256")
    if not (all(_uint(gguf.get(name)) for name in ("version", "tensors", "file_size", "reader_file_size",
                                                   "header_bytes", "dev", "ino"))
            and type(gguf.get("mtime_ns")) is int and isinstance(digest, str) and _HEX64.match(digest)
            and gguf.get("open") == open_mode and 0 < gguf["header_bytes"] <= gguf["file_size"]):
        return bad
    if (gguf["dev"], gguf["ino"], gguf["file_size"], gguf["mtime_ns"]) != (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
        return None, "counter_identity_mismatch"
    # The helper adds a BOS by itself when the metadata says so; nothing is subtracted to correct that. Counting
    # is used only when it is stated off AND an empty text was seen to encode to nothing.
    if msg["counting"] is not True or tok["add_bos"] is not False or tok["empty_text_tokens"] != 0:
        return None, "counter_bos_policy_unsupported"
    return {"protocol": PROTOCOL, "vocab_size": tok["vocab_size"], "pre": pre, "pre_warning": tok["pre_warning"],
            "bos_token_id": tok["bos_token_id"], "eos_token_id": tok["eos_token_id"],
            "pad_token_id": tok["pad_token_id"], "add_bos": False, "allow_special": False,
            "gguf_version": gguf["version"], "gguf_tensors": gguf["tensors"], "gguf_file_size": gguf["file_size"],
            "gguf_header_bytes": gguf["header_bytes"], "gguf_metadata_sha256": digest,
            "gguf_open": open_mode}, None


def _parse_count(line: bytes, request_id: int, token: str, nbytes: int) -> tuple[int | None, str | None, bool]:
    """(count, None, False), or (None, reason, whether the helper can no longer be trusted to be in step)."""
    bad = (None, "counter_response_invalid", True)
    msg = _loads(line)
    if not isinstance(msg, dict) or type(msg.get("v")) is not int or msg["v"] != 1:
        return bad
    if msg.get("type") == "fatal":
        return None, "counter_helper_failed", True
    if msg.get("type") != "count" or type(msg.get("id")) is not int or msg["id"] != request_id \
            or msg.get("session") != token:
        return bad
    if msg.get("ok") is True:
        count = msg.get("count")
        # an int, not a bool, not negative, and not more than this text could possibly be
        if set(msg) != {"v", "type", "id", "session", "ok", "count"} or type(count) is not int \
                or not 0 <= count <= 2 * nbytes + 8:
            return bad
        return count, None, False
    if msg.get("ok") is False and set(msg) == {"v", "type", "id", "session", "ok", "reason"}:
        said = msg.get("reason")
        reason = _REFUSED.get(said) if isinstance(said, str) else None
        if reason is not None:
            return None, reason, False
    return bad


class _Session:
    """One running helper and the identity it was started with. One request is on the pipe at a time."""

    def __init__(self, proc: subprocess.Popen, token: str, facts: dict, helper_sha256: str,
                 build: str | None) -> None:
        self.proc, self.token = proc, token
        self.identity = {
            "kind": KIND,
            "binding": "operator_declared",
            "engine_identity": "not_established",
            "declared_build": build,
            "helper_sha256": helper_sha256,
            "helper_scope": "bytes_of_the_descriptor_the_helper_was_executed_from",
            "gguf_scope": "header_region_of_the_file_the_helper_pinned",
            **facts,
        }
        self.digest = hashlib.sha256(json.dumps(self.identity, sort_keys=True, separators=(",", ":"))
                                     .encode("utf-8")).hexdigest()
        self.dead: str | None = None
        self._io = threading.Lock()
        self._in, self._out = proc.stdin.fileno(), proc.stdout.fileno()
        self._buffer = bytearray()
        self._next = 1
        self._pending = self._closed = False

    def _shut(self) -> None:            # with _io held
        if not self._closed:
            self._closed = True
            _shut(self.proc)

    def _fail(self, reason: str) -> None:       # with _io held
        if self.dead is None:
            self.dead = reason
        self._shut()

    def close(self, reason: str) -> None:
        """No more counts from this helper. Its pipes are closed now if no exchange is on them, otherwise by the
        thread that is using them, as soon as it is done (never under it)."""
        if self.dead is None:
            self.dead = reason
        self._pending = True
        if self._io.acquire(blocking=False):
            try:
                self._shut()
            finally:
                self._io.release()

    def count(self, text: str, nbytes: int, deadline: float) -> tuple[int | None, str | None]:
        left = deadline - time.monotonic()
        if left <= 0:
            return None, "counter_deadline"
        if not self._io.acquire(timeout=left):
            return None, "counter_busy"
        try:
            if self.dead is not None:
                return None, self.dead
            request_id = self._next
            line = json.dumps({"v": 1, "op": "count", "id": request_id, "text": text}, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8") + b"\n"
            if len(line) > MAX_REQUEST_BYTES:
                return None, "component_too_large_to_count"
            self._next += 1
            limit = min(deadline, time.monotonic() + CALL_TIMEOUT)
            try:
                # nothing may be waiting before a request is sent: a line nobody asked for is not an answer
                waiting = _events(self._out, select.POLLIN, 0)
                if self._buffer or waiting & select.POLLIN:
                    raise _Wire("counter_response_invalid")
                if waiting:
                    raise _Wire("counter_helper_exited")
                _send(self._in, line, limit)
                raw = _recv_line(self._out, self._buffer, MAX_RESPONSE_BYTES, limit)
                if self._buffer:
                    raise _Wire("counter_response_invalid")
            except _Wire as failure:
                self._fail(failure.reason)
                return None, failure.reason
            except (OSError, ValueError):
                self._fail("counter_helper_failed")
                return None, "counter_helper_failed"
            except BaseException:
                # a cancellation or an exit passes through as itself; the helper is no longer in step, so it goes
                self._fail("counter_interrupted")
                raise
            count, reason, broken = _parse_count(raw, request_id, self.token, nbytes)
            if broken:
                self._fail(reason or "counter_response_invalid")
            return count, reason
        finally:
            self._io.release()
            if self._pending:
                self.close(self.dead or "counter_closed")


def _unavailable(reason: str) -> tuple[Callable[..., tuple[int | None, str | None]], dict[str, Any]]:
    return (lambda text, sha, allow=None: (None, reason)), {
        "method": None, "reason": reason, "digest": None, "bound_model": None, "engine_exact": False,
        "counter": {"kind": KIND}}


def _stat_keys(config: tuple) -> tuple[tuple | None, str | None]:
    keys = []
    for path, reason in ((config[0], "counter_helper_unavailable"), (config[1], "counter_gguf_unavailable")):
        try:
            info = os.stat(path)
        except (OSError, ValueError):
            return None, reason
        if not stat.S_ISREG(info.st_mode):
            return None, reason
        keys.append(_key(info))
    return tuple(keys), None


class GgufHelperCounter:
    """The helper this process owns (at most one running), the counts it has given, and its lifecycle.

    for_model() has the inspector's existing shape: (counter, description), counter(text, sha256, allow) ->
    (count, None) or (None, reason). It never blocks on starting the helper: the start runs in a background
    thread and requests made meanwhile get `counter_helper_starting`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: _Session | None = None
        self._state: tuple | None = None            # (config, file keys) the session, start or failure is of
        self._starter: threading.Thread | None = None
        self._starting_proc: subprocess.Popen | None = None
        self._failure: tuple[str, float] | None = None
        self._epoch = 0
        self._retired: list[subprocess.Popen] = []
        self._counts: OrderedDict[tuple[str, str], int] = OrderedDict()
        self.starts = 0                             # helper processes started, ever

    # --- configuration ------------------------------------------------------------------------------------

    def for_model(self, model: Any, max_chars: int = MAX_COUNT_CHARS
                  ) -> tuple[Callable[..., tuple[int | None, str | None]], dict[str, Any]]:
        helper, gguf, bound = (os.environ.get(name) for name in (ENV_HELPER, ENV_GGUF, ENV_MODEL))
        if not helper or not gguf:
            return _unavailable("counter_not_configured")
        if not bound:
            return _unavailable("counter_model_not_bound")
        # exact match, before any file is looked at and before any process exists
        if not isinstance(model, str) or model != bound:
            return _unavailable("counter_model_mismatch")
        open_mode = os.environ.get(ENV_OPEN) or "pinned"
        if open_mode == "path":
            return _unavailable("counter_path_mode_unsupported")
        if open_mode != "pinned" or not os.path.isabs(helper) or not os.path.isabs(gguf):
            return _unavailable("counter_config_invalid")
        if not hasattr(select, "poll") or not os.path.isdir("/proc/self/fd"):
            return _unavailable("counter_platform_unsupported")
        config = (helper, gguf, bound, open_mode, _label(os.environ.get(ENV_BUILD)))
        session, reason = self._ensure(config)
        if session is None:
            return _unavailable(reason or "counter_helper_failed")
        deadline = time.monotonic() + CAPTURE_BUDGET
        return ((lambda text, sha, allow=None: self._count(session, deadline, max_chars, text, sha, allow)),
                {"method": METHOD, "reason": None, "digest": session.digest,
                 "bound_model": _printable(bound, 200), "engine_exact": False,
                 "counter": dict(session.identity),
                 "coverage": {**COVERAGE, "excludes": list(COVERAGE["excludes"])}})

    def _ensure(self, config: tuple) -> tuple[_Session | None, str | None]:
        keys, reason = _stat_keys(config)
        with self._lock:
            self._reap()
            state = (config, keys)
            if self._state != state:
                # another configuration, or a helper or GGUF that is no longer the file that was loaded
                self._drop()
                self._state = state
            if reason is not None:
                return None, reason
            session = self._session
            if session is not None:
                if session.dead is None:
                    return session, None
                self._session = None
                self._retired.append(session.proc)
                self._failure = (session.dead, time.monotonic() + RETRY_AFTER)
                self._counts.clear()
            if self._starter is not None:
                return None, "counter_helper_starting"
            if self._failure is not None:
                if time.monotonic() < self._failure[1]:
                    return None, self._failure[0]
                self._failure = None
            # A retired helper may ignore SIGTERM. Backoff expiry does not make it safe to replace.
            self._reap()
            if self._retired:
                return None, "counter_helper_stopping"
            self._starter = threading.Thread(target=self._start, args=(config, keys, self._epoch),
                                             name="dream-context-counter-start", daemon=True)
            self._starter.start()
            return None, "counter_helper_starting"

    # --- lifecycle ----------------------------------------------------------------------------------------

    def _reap(self) -> None:            # with _lock held
        self._retired = [proc for proc in self._retired if proc.poll() is None]

    def _drop(self) -> None:            # with _lock held
        self._epoch += 1                # invalidate publication, but retain ownership until startup exits
        if self._starting_proc is not None and self._starting_proc.poll() is None:
            try:
                self._starting_proc.terminate()
            except OSError:
                pass
        self._failure = None
        self._counts.clear()
        session, self._session = self._session, None
        if session is not None:
            session.close("counter_closed")
            self._retired.append(session.proc)

    def _start(self, config: tuple, keys: tuple, epoch: int) -> None:
        session, reason = None, "counter_helper_failed"
        try:
            session, reason = self._launch(config, keys, epoch)
        except Exception:               # a defect here is a failed start, not a traceback; exits pass through
            session, reason = None, "counter_helper_failed"
        finally:
            with self._lock:
                self._starter = None
                self._starting_proc = None
                if self._epoch != epoch:
                    if session is not None:
                        session.close("counter_closed")
                        self._retired.append(session.proc)
                else:
                    if session is not None:
                        self._session = session
                    else:
                        self._failure = (reason or "counter_helper_failed", time.monotonic() + RETRY_AFTER)

    def _launch(self, config: tuple, keys: tuple, epoch: int) -> tuple[_Session | None, str | None]:
        helper, gguf, _bound, open_mode, build = config
        helper_fd = gguf_fd = -1
        proc: subprocess.Popen | None = None
        try:
            try:
                helper_fd, helper_info = _open_regular(helper)
            except (OSError, ValueError):
                return None, "counter_helper_unavailable"
            if _key(helper_info) != keys[0]:
                return None, "counter_source_changed"
            if not _trusted(helper_info) or helper_info.st_size > MAX_HELPER_BYTES:
                return None, "counter_helper_untrusted"
            helper_sha256 = _hash_fd(helper_fd, helper_info.st_size)
            if helper_sha256 is None:
                return None, "counter_source_changed"
            try:
                gguf_fd, gguf_info = _open_regular(gguf)
            except (OSError, ValueError):
                return None, "counter_gguf_unavailable"
            if _key(gguf_info) != keys[1]:
                return None, "counter_source_changed"
            if gguf_info.st_mode & 0o002:
                return None, "counter_gguf_untrusted"
            if min(helper_fd, gguf_fd) < 3:
                return None, "counter_helper_failed"
            token = secrets.token_hex(16)
            with self._lock:
                if self._epoch != epoch:
                    return None, "counter_closed"
            # A fixed argv, no shell. The child is executed from the descriptor that was just hashed, so the bytes
            # that run are the bytes the digest is of whatever the path names by then; in "pinned" mode it is
            # given the GGUF the same way.
            argv = [f"/proc/self/fd/{helper_fd}",
                    "--gguf", f"/proc/self/fd/{gguf_fd}",
                    "--session", token, "--open", open_mode]
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0, close_fds=True,
                                        pass_fds=(helper_fd, gguf_fd), cwd="/", env={"LC_ALL": "C"}, shell=False)
            except (OSError, ValueError, subprocess.SubprocessError):
                return None, "counter_helper_failed"
            with self._lock:
                self.starts += 1
                self._starting_proc = proc
                if self._epoch != epoch:
                    raise _Wire("counter_closed")
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
            if _key(os.fstat(helper_fd)) != keys[0]:        # written in place between the hash and the exec
                raise _Wire("counter_source_changed")
            buffer = bytearray()
            start_deadline = time.monotonic() + START_TIMEOUT
            while True:
                with self._lock:
                    if self._epoch != epoch:
                        raise _Wire("counter_closed")
                try:
                    line = _recv_line(proc.stdout.fileno(), buffer, MAX_READY_BYTES,
                                      min(start_deadline, time.monotonic() + 0.05))
                    break
                except _Wire as failure:
                    if failure.reason != "counter_deadline" or time.monotonic() >= start_deadline:
                        raise
            if buffer:
                raise _Wire("counter_ready_invalid")
            facts, reason = _parse_ready(line, token, open_mode, gguf_info)
            if facts is None:
                raise _Wire(reason or "counter_ready_invalid")
            if _key(os.fstat(gguf_fd)) != keys[1]:
                raise _Wire("counter_source_changed")
            session = _Session(proc, token, facts, helper_sha256, build)
            proc = None
            return session, None
        except _Wire as failure:
            return None, failure.reason
        except OSError:
            return None, "counter_helper_failed"
        finally:
            for fd in (helper_fd, gguf_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if proc is not None:
                _shut(proc)
                with self._lock:
                    self._retired.append(proc)

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for startup and retired children. A ready, current helper does not need to exit."""
        until = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._lock:
            starter = self._starter
        if starter is not None:
            starter.join(None if until is None else max(0.0, until - time.monotonic()))
        with self._lock:
            procs = list(self._retired)
        for proc in procs:
            try:
                proc.wait(None if until is None else max(0.0, until - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        with self._lock:
            self._reap()
            return self._starter is None and not self._retired

    def reset(self, wait: float = TERMINATE_WAIT) -> int:
        """Cancel startup and retire the current helper, never escalating beyond SIGTERM.

        Return live owned children, plus a pending startup reservation if it has not yet registered its child.
        Ownership remains recorded after a timeout, so another capture cannot launch a replacement early.
        """
        with self._lock:
            self._drop()
            self._state = None
        self.wait(max(0.0, wait))
        with self._lock:
            self._reap()
            live = {id(proc) for proc in self._retired}
            if self._starting_proc is not None and self._starting_proc.poll() is None:
                live.add(id(self._starting_proc))
            return len(live) + int(self._starter is not None and self._starting_proc is None)

    # --- counting -----------------------------------------------------------------------------------------

    def _count(self, session: _Session, deadline: float, max_chars: int, text: Any, sha: Any,
               allow: Callable[[int], bool] | None) -> tuple[int | None, str | None]:
        if not isinstance(text, str) or not isinstance(sha, str):
            return None, "component_has_no_text"
        if len(text) > max_chars:
            return None, "component_too_large_to_count"
        key = (session.digest, sha)
        with self._lock:
            if self._session is not session or session.dead is not None:
                return None, session.dead or "counter_closed"
            known = self._counts.get(key)
            if known is not None:
                self._counts.move_to_end(key)
                return known, None
        if session.dead is not None:
            return None, session.dead
        if time.monotonic() >= deadline:
            return None, "counter_deadline"
        if allow is not None and not allow(len(text)):
            return None, "count_budget_exhausted"
        try:
            nbytes = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            # a lone surrogate has no bytes to send; the text is not altered to make it countable
            return None, "component_not_utf8"
        if nbytes > MAX_TEXT_BYTES:
            return None, "component_too_large_to_count"
        count, reason = session.count(text, nbytes, deadline)
        with self._lock:
            # Reset/configuration changes and result publication are one atomic ownership decision.
            if self._session is not session or session.dead is not None:
                return None, session.dead or "counter_closed"
            if count is not None:
                self._counts[key] = count
                while len(self._counts) > _COUNT_CACHE:
                    self._counts.popitem(last=False)
            return count, reason


COUNTER = GgufHelperCounter()


def for_model(mode: Any, model: Any, max_chars: int = MAX_COUNT_CHARS
              ) -> tuple[Callable[..., tuple[int | None, str | None]], dict[str, Any]]:
    """The inspector's entry: `mode` is DREAM_CONTEXT_COUNTER's value, already known to be set. An unknown mode
    counts nothing (it never falls back to another counter under a name the operator did not choose)."""
    if mode != MODE:
        return _unavailable("counter_mode_unknown")
    return COUNTER.for_model(model, max_chars)


def _at_exit() -> None:
    COUNTER.reset()


atexit.register(_at_exit)
