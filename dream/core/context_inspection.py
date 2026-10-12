"""Effective-context inspection (DREAM-257, phase 1): a bounded preview journal of what each HTTP request Dream sent
to a model carried.

One record per transport attempt, made at the point the request leaves (the OpenAI-compatible backend's
_stream_uncoordinated and _post_retry), after admission and compaction: the request's components as they were, each
with its source where assembly recorded one, a SHA-256 of its actual text, its size and a bounded, redacted preview.
The records live in this process's memory only, bounded per record, per session and across sessions; nothing here is
written to disk, to the runtime log or to an event, and no exception from here names a path or carries prompt text.

What a record is not: a copy of the request. The original text is NOT retained; what a preview left out or redacted
cannot be got back from here, and a request cannot be rebuilt from its record. Hashes and sizes are of the text that
was sent; previews are the bounded part shown.

What a record does not claim: that the model finished, that a task completed, or what the server charged beyond the
`usage` this attempt's own response reported. A token count is either the server's (observed, never summed across
responses), or a real tokenizer's count of one piece of text alone (`tokenizer_text`: text-derived, not the engine's
own count, not the rendered chat template), or null with the reason. Nothing is estimated and a missing count is
never zero. NOT_COUNTED lists what no count here covers.

The work done to make a record is bounded too (_Work): past a fixed allowance of text walked, hashed or tokenized, a
piece is recorded as skipped with the reason instead of being processed.

Coverage includes the OpenAI-compatible HTTP backend and the explicit prepared Responses advisory transport.
The latter separates provider input-count preflight from native inference usage and never apportions total
tokens to components or starts a local counter. A provider driven through its own SDK or CLI assembles its
context itself; this module does not see it and says so (COVERAGE).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from itertools import islice
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

SCHEMA = 2
# The inspector's own implementation, not Dream's version: a later export for training reads both from the record.
HARNESS = "dream-context-inspection/2 (DREAM-257 phase 1)"


def _package_version() -> str | None:
    try:
        from .. import __version__ as version
    except Exception:
        return None
    return version[:40] if isinstance(version, str) and version else None


# The installed Dream package's version string, read once. It names a release, not a source revision: uncommitted
# changes carry the same string, and nothing here reads git or hashes the tree.
PACKAGE = {"name": "dream", "version": _package_version(),
           "note": "The Dream package's version string. It does not identify uncommitted source changes."}

MAX_SESSIONS = 8                 # bound sessions kept, least recently written first out
MAX_PRIVATE = 4                  # backends with no bound session, kept apart so they never evict a session's records
MAX_RECORDS = 48                 # per session
MAX_SESSION_BYTES = 2_000_000    # per session, of the records as stored
MAX_RECORD_BYTES = 500_000       # one record, as stored; past it the record is reduced and says so
MAX_COMPONENTS = 160             # per record, of everything stored as a component (sections, messages, parts, calls)
_HEAD_COMPONENTS = 32
MAX_MESSAGE_COMPONENTS = 24      # one message's share; the rest of its pieces are merged into one, and it says so
MAX_SPANS = 10_000               # provenance spans read from one system prompt; more is treated as unknown
MAX_PARTS = 4096                 # parts of one multipart message looked at
MAX_PREVIEW_BYTES = 96_000       # per record, all component previews together
MAX_TOOL_PREVIEW_BYTES = 16_000  # per record, all tool description previews together
MAX_TOOLS_LISTED = 64
PREVIEW_CHARS = 600
SECTION_PREVIEW_CHARS = 2000     # a system prompt's section
TOOL_PREVIEW_CHARS = 240         # a tool's description
_NAME_CHARS = 128
_DETAIL_KEYS = 8
_DETAIL_CHARS = 200

# Work allowances for ONE capture, deterministic (characters, not time): text walked/serialised/hashed, messages
# walked, and text handed to the tokenizer on a cache miss.
MAX_WORK_CHARS = 24_000_000
MAX_WORK_MESSAGES = 4096
MAX_COUNT_TOTAL_CHARS = 1_200_000

ENV_TOKENIZER = "DREAM_CONTEXT_TOKENIZER"
ENV_TOKENIZER_MODEL = "DREAM_CONTEXT_TOKENIZER_MODEL"
MAX_TOKENIZER_BYTES = 64_000_000
MAX_COUNT_CHARS = 400_000
_COUNT_CACHE = 4096

RECIPIENTS = ("lead", "worker", "advisor")
REQUEST_ID = re.compile(r"[0-9a-f]{32}")

COVERAGE = {
    "transport": "openai_compatible_http",
    "prepared_responses_http": "observed",
    "native_sdk_or_cli": "not_observed",
    "note": ("Requests Dream sends over its OpenAI-compatible HTTP backend and its explicit prepared Responses "
             "advisory transport are recorded. A provider "
             "driven through its own SDK or CLI assembles its context itself: it is not observed here and nothing "
             "is claimed about it."),
}
JOURNAL_KIND = {
    "kind": "bounded_preview_journal",
    "note": ("A bounded journal of previews with the hashes, sizes and counts captured as each request left. It is "
             "not a copy of the request: text a preview left out or redacted is not retained, and a request cannot "
             "be rebuilt from its record."),
}
# What no tokenizer_text count here covers. The server's own usage is the only figure that includes them.
NOT_COUNTED = (
    "reasoning_content: its size is recorded, it is never tokenized here",
    "chat template: role markers, turn separators and special tokens the engine's template adds",
    "tool call representation: a call's arguments string is counted alone; its name, id and the template's call "
    "wrapper are not",
    "tool schema rendering: each schema's canonical JSON is counted, not the form the engine's template gives it",
    "images and multipart overhead: an image's cost and the boundaries between a message's parts",
)
CAVEATS = (
    "A record describes one transport attempt. Its status says how the HTTP exchange went, never whether the task "
    "was completed.",
    "usage is what this attempt's own response reported. It is never summed across attempts, and an attempt whose "
    "response reported none stays null.",
    "A tokenizer_text count is a local tokenizer's count of one piece of text alone. It is text-derived, not the "
    "engine's own count, and the counts are not additive: only the server's usage is the request's cost.",
    "Previews are bounded and redacted (images, base64 runs, values that look like credentials); a tool call's "
    "arguments are never shown. A SHA-256 is of the actual text, which is not kept.",
    "Past a fixed allowance of work or of stored bytes, a piece is recorded as skipped, merged or dropped, with the "
    "reason; it is never silently left out.",
)
EMPTY_NOTE = ("No request context has been recorded for this session yet. Context sent through a provider's own SDK "
              "or CLI is not observed; for an HTTP provider, send a new request and refresh.")

_DATA_URL = re.compile(r"data:[\w.+/-]{0,80};base64,[A-Za-z0-9+/=\s]{16,}")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{120,}={0,2}")
_BEARER = re.compile(r"(?i)\b(bearer)(\s+)()[^\s\"',;]{6,}")
_CREDENTIAL = re.compile(r"(?i)\b(authorization|api[_-]?key|access[_-]?token|token|secret|password)\b"
                         r"(\"?\s*[:=]\s*)(\"?)[^\s\"',;]{6,}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _opaque(value: Any) -> str:
    return f"<{type(value).__name__}>"


def _weigh(value: Any, limit: int) -> tuple[int, int]:
    """(about how many characters `value`'s JSON takes, how many elements were visited to find out), without
    building it. It stops once the weight is past `limit` (the answer is then only "more than limit") and also
    once more than `limit` elements have been visited, in which case the weight returned is past the limit too.
    An element is visited when it is put on the stack, so the stack never holds more than `limit` + 1 of them,
    and a dict or list is never iterated further than that. The second figure is what the walk itself cost: the
    caller charges it even when the value is then refused."""
    total, visited, stack = 0, 1, [value]
    while stack:
        item = stack.pop()
        total += 8 + (len(item) if isinstance(item, str) else 0)
        if total > limit:               # before anything inside it is looked at
            return total, visited
        if isinstance(item, dict):
            for key, inner in item.items():
                total += len(key) if isinstance(key, str) else 8
                visited += 1
                # more than `limit` elements weigh more than `limit` (8 each), so this refuses nothing that fits
                if total > limit or visited > limit:
                    return max(total, limit + 1), visited
                stack.append(inner)
        elif isinstance(item, (list, tuple)):
            total += len(item)
            if total > limit or visited + len(item) > limit:
                # not iterated at all: nothing of it was visited, so nothing more is charged for it
                return max(total, limit + 1), visited
            visited += len(item)
            stack.extend(item)
    return total, visited


def _digest(value: Any) -> tuple[str, int]:
    """(SHA-256, UTF-8 bytes) of a value's canonical JSON, unbounded: for a value the caller already knows is
    small (a test, one message)."""
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=_opaque).encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest(), len(data)


def _short(value: Any, limit: int = _NAME_CHARS) -> str | None:
    """A harness-side label (a model id, a role, a tool name) as one bounded printable line; None when it is not
    text."""
    if not isinstance(value, str) or not value:
        return None
    return "".join(ch if ch.isprintable() else " " for ch in value[:limit])


def _detail(detail: Any) -> dict | None:
    """What assembly said of a span, kept only as a few short scalars: at most _DETAIL_KEYS keys, each a short
    name, each value None, a bool, an int or a string cut to _DETAIL_CHARS. Anything else is dropped and the
    result says it was cut (`detail_truncated`), so a caller's detail can never carry text past the bounds."""
    if not isinstance(detail, dict):
        return None
    out: dict[str, Any] = {}
    cut = len(detail) > 64
    for key, value in islice(detail.items(), 64):
        if len(out) >= _DETAIL_KEYS or not isinstance(key, str) or not key or len(key) > 40 \
                or key == "detail_truncated":
            cut = True
        elif value is None or isinstance(value, bool) or (type(value) is int and -2**63 < value < 2**63):
            out[key] = value
        elif isinstance(value, str):
            out[key] = _short(value, _DETAIL_CHARS) or ""
            cut = cut or len(value) > _DETAIL_CHARS
        else:
            cut = True
    if cut:
        out["detail_truncated"] = True
    return out or None


def redact(text: str) -> str:
    """`text` with what a preview must never show replaced: an image data URL, a long base64-like run, a value that
    follows a credential's name. Best effort for credentials; the two image forms are structural."""
    text = _DATA_URL.sub("[image data redacted]", text)
    text = _BASE64_RUN.sub(lambda m: f"[{len(m.group(0))} base64-like characters redacted]", text)
    for pattern in (_BEARER, _CREDENTIAL):
        text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]", text)
    return text


def preview(text: str, limit: int) -> tuple[str, bool]:
    """(the first `limit` characters of `text`, redacted; whether that is less than all of it). The redaction reads
    a bounded window, so a huge component costs no more than a small one."""
    window = text[: limit * 8 + 4096]
    shown = redact(window)
    return shown[:limit], len(shown) > limit or len(window) < len(text)


def spans_of(text: str) -> list[tuple[int, int, str, dict | None]]:
    """The provenance a system prompt carries (SystemPrompt.provenance: spans recorded while it was assembled), when
    the spans tile the text exactly; otherwise one span whose source is explicitly unknown. The text is never read
    to work out where a part came from: a heading inside a file the workspace owns proves nothing."""
    unknown = [(0, len(text), "unknown", {"reason": "text_without_assembly_metadata"})]
    spans = getattr(text, "provenance", None)
    if not isinstance(spans, tuple) or not spans or len(spans) > MAX_SPANS:
        return unknown
    at = 0
    out = []
    for span in spans:
        if not (isinstance(span, tuple) and len(span) == 4):
            return unknown
        start, end, source, detail = span
        if type(start) is not int or type(end) is not int or start != at or end < start \
                or not isinstance(source, str) or not (detail is None or isinstance(detail, dict)):
            return unknown
        out.append((start, end, source, detail))
        at = end
    return out if at == len(text) else unknown


# --- token counts ---------------------------------------------------------------------------------------------

def _count(value: Any) -> int | None:
    """A server's token count as it must be to be believed: an int, not a bool, not negative."""
    return value if type(value) is int and value >= 0 else None


def read_usage(usage: Any) -> dict[str, Any]:
    """What one response's `usage` says, each count on its own: a value that is missing or is not a non-negative
    integer is null with the reason, never zero and never repaired."""
    if not isinstance(usage, dict):
        return {"prompt_tokens": None, "prompt_tokens_reason": "usage_not_an_object",
                "cached_tokens": None, "cached_tokens_reason": "usage_not_an_object",
                "completion_tokens": None, "completion_tokens_reason": "usage_not_an_object"}
    details = usage.get("prompt_tokens_details")
    out: dict[str, Any] = {}
    for name, holder, key in (("prompt_tokens", usage, "prompt_tokens"),
                              ("cached_tokens", details if isinstance(details, dict) else {}, "cached_tokens"),
                              ("completion_tokens", usage, "completion_tokens")):
        value = _count(holder.get(key))
        out[name] = value
        out[f"{name}_reason"] = (None if value is not None else
                                 "not_reported" if holder.get(key) is None else "not_a_non_negative_integer")
    return out


def read_responses_usage(usage: Any) -> dict[str, Any]:
    """Native Responses usage, not a fabricated chat completion or an estimate."""
    names = ("input_tokens", "cached_tokens", "output_tokens")
    if not isinstance(usage, dict):
        return {key: value for name in names for key, value in
                ((name, None), (name + "_reason", "usage_not_an_object"))}
    details = usage.get("input_tokens_details")
    out = {}
    for name in names:
        holder = details if name == "cached_tokens" else usage
        holder = holder if isinstance(holder, dict) else {}
        value = _count(holder.get(name))
        out[name] = value
        out[name + "_reason"] = (None if value is not None else
                                 "not_reported" if holder.get(name) is None else "not_a_non_negative_integer")
    return out


def _read_tokenizer(path: str) -> tuple[bytes | None, tuple | None, str | None]:
    """(the file's bytes, its identity, None) or (None, None, reason): ONE descriptor, opened without blocking (a
    FIFO where a file belongs cannot hang a request), a regular file only, read to at most MAX_TOKENIZER_BYTES.
    The bytes returned are the only ones the caller hashes and parses, so a file swapped on disk afterwards cannot
    make the digest and the tokenizer disagree."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    except (OSError, ValueError, TypeError):
        return None, None, "tokenizer_file_unavailable"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, None, "tokenizer_file_unavailable"
        if info.st_size > MAX_TOKENIZER_BYTES:
            return None, None, "tokenizer_file_too_large"
        chunks, total = [], 0
        while total <= MAX_TOKENIZER_BYTES:
            chunk = os.read(fd, min(1 << 20, MAX_TOKENIZER_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_TOKENIZER_BYTES:
            return None, None, "tokenizer_file_too_large"
        return b"".join(chunks), _file_key(info), None
    except OSError:
        return None, None, "tokenizer_file_unavailable"
    finally:
        os.close(fd)


def _file_key(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class TextTokenizer:
    """An optional real tokenizer for the `tokenizer_text` counts: the `tokenizers` file DREAM_CONTEXT_TOKENIZER
    names, used only for requests whose model is exactly DREAM_CONTEXT_TOKENIZER_MODEL. Loaded on first use, on the
    CPU, from that local file alone (nothing is downloaded). The file is read once through one descriptor
    (_read_tokenizer); its SHA-256 is of exactly the bytes the tokenizer was parsed from (Tokenizer.from_buffer, or
    from_str), never of a second read of the path. Anything missing, mismatched or failing gives a null count and a
    reason code. A reason never carries the path or an exception's text.

    A count is of text, by this tokenizer: it is not the engine's own count, and the binding to a model is the
    owner's statement (the two environment variables), not something checked against the engine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (path, file identity, tokenizer, digest, reason)
        self._loaded: tuple[str, tuple | None, Any, str | None, str | None] | None = None
        self._counts: OrderedDict[tuple[str, str], int] = OrderedDict()

    def for_model(self, model: Any) -> tuple[Callable[..., tuple[int | None, str | None]], dict[str, Any]]:
        """(counter, description) for one request. counter(text, its sha256, allow) -> (count, None) or
        (None, reason); `allow(chars)` is asked before text is actually encoded (not for a remembered count) and
        may refuse."""
        path, bound = os.environ.get(ENV_TOKENIZER), os.environ.get(ENV_TOKENIZER_MODEL)
        tokenizer = digest = None
        if not path:
            reason = "tokenizer_not_configured"
        elif not bound:
            reason = "tokenizer_model_not_bound"
        elif not isinstance(model, str) or model != bound:
            reason = "tokenizer_model_mismatch"
        else:
            tokenizer, digest, reason = self._load(path)
        if reason is not None:
            return (lambda text, sha, allow=None: (None, reason)), {
                "method": None, "reason": reason, "digest": None, "bound_model": None, "engine_exact": False}
        return ((lambda text, sha, allow=None: self._count(tokenizer, digest, text, sha, allow)),
                {"method": "tokenizer_text", "reason": None, "digest": digest, "bound_model": _short(bound, 200),
                 # said with every count: text in, tokens out, by a local file the owner bound to this model name
                 "engine_exact": False})

    def _load(self, path: str) -> tuple[Any, str | None, str | None]:
        try:
            info = os.stat(path)
        except (OSError, ValueError, TypeError):
            return None, None, "tokenizer_file_unavailable"
        if not stat.S_ISREG(info.st_mode):
            return None, None, "tokenizer_file_unavailable"
        if info.st_size > MAX_TOKENIZER_BYTES:
            return None, None, "tokenizer_file_too_large"
        with self._lock:
            # The path still names the file that was parsed (device, inode, size, mtime, ctime): reuse it. The
            # remembered digest is of the parsed bytes whatever the path names now, so this check can only cost a
            # reload, never a digest that is not the tokenizer's.
            if self._loaded is not None and self._loaded[:2] == (path, _file_key(info)):
                return self._loaded[2:]
            tokenizer = digest = None
            data, key, reason = _read_tokenizer(path)
            if reason is None:
                try:
                    from tokenizers import Tokenizer
                except ImportError:
                    reason = "tokenizers_not_installed"
                except Exception:
                    reason = "tokenizer_load_failed"
                else:
                    try:
                        digest = hashlib.sha256(data).hexdigest()
                        from_buffer = getattr(Tokenizer, "from_buffer", None)
                        tokenizer = (from_buffer(data) if from_buffer is not None
                                     else Tokenizer.from_str(data.decode("utf-8")))
                        # A tokenizer.json may carry a saved truncation (max_length) or padding (a fixed length):
                        # either would make encode() return something other than the text's own tokens. Both are
                        # switched off on THIS in-memory object, which is used for counting only, before it is
                        # remembered or used. The file, its digest and any engine's configuration are untouched.
                        # A tokenizer that cannot be told so is not used (the load fails, with the reason).
                        tokenizer.no_truncation()
                        tokenizer.no_padding()
                        if getattr(tokenizer, "truncation", None) is not None \
                                or getattr(tokenizer, "padding", None) is not None:
                            raise ValueError("truncation or padding still set")
                    except Exception:
                        reason = "tokenizer_load_failed"
            if reason is not None:
                tokenizer = digest = None
            self._loaded = (path, key, tokenizer, digest, reason)
            self._counts.clear()
            return tokenizer, digest, reason

    def _count(self, tokenizer: Any, digest: str, text: Any, sha: Any,
               allow: Callable[[int], bool] | None = None) -> tuple[int | None, str | None]:
        if not isinstance(text, str) or not isinstance(sha, str):
            return None, "component_has_no_text"
        if len(text) > MAX_COUNT_CHARS:
            return None, "component_too_large_to_count"
        key = (digest, sha)
        with self._lock:
            known = self._counts.get(key)
            if known is not None:
                self._counts.move_to_end(key)
                return known, None
            if allow is not None and not allow(len(text)):
                return None, "count_budget_exhausted"
            try:
                count = len(tokenizer.encode(text, add_special_tokens=False).ids)
            except Exception:
                return None, "tokenizer_encode_failed"
            self._counts[key] = count
            while len(self._counts) > _COUNT_CACHE:
                self._counts.popitem(last=False)
            return count, None


TOKENIZER = TextTokenizer()

# DREAM_CONTEXT_COUNTER, when the operator set it in the environment Dream was started with, chooses another
# counter for the piece-text counts (context_counter: a local CPU helper over the engine's own tokenizer source).
# Unset or empty, nothing changes: the tokenizer above is used exactly as before and that module is not imported.
ENV_COUNTER = "DREAM_CONTEXT_COUNTER"


def _counter_for(model: Any) -> tuple[Callable[..., tuple[int | None, str | None]], dict[str, Any]]:
    """(counter, description) for one request, in TextTokenizer.for_model's shape, from the counter chosen."""
    mode = os.environ.get(ENV_COUNTER)
    if not mode:
        return TOKENIZER.for_model(model)
    from . import context_counter
    return context_counter.for_model(mode, model, max_chars=MAX_COUNT_CHARS)


# --- the journal ----------------------------------------------------------------------------------------------

def _stored(record: dict) -> int:
    """The record's size as stored: its JSON, without the figure itself."""
    return len(json.dumps({key: value for key, value in record.items() if key != "stored_bytes"},
                          ensure_ascii=False, default=_opaque).encode("utf-8", "replace"))


def _drop_previews(record: dict) -> None:
    for component in record.get("components") or ():
        if component.get("preview"):
            component.update(preview="", preview_truncated=True, preview_omitted=True,
                             preview_reason="record_byte_limit")


def _drop_tool_listing(record: dict) -> None:
    tools = record.get("tools")
    if isinstance(tools, dict) and tools.get("listed"):
        tools["listed_dropped_for_bytes"] = len(tools["listed"])
        tools["listed"], tools["names"] = [], []


def _drop_components(record: dict) -> None:
    components = record.get("components") or []
    if components:
        record["request"]["components_dropped_for_bytes"] = len(components)
        record["request"]["components_recorded"] = 0
        record["components"] = []


_REDUCTIONS = (("previews_dropped", _drop_previews), ("tool_listing_dropped", _drop_tool_listing),
               ("components_dropped", _drop_components))


def _reduce(record: dict, limit: int) -> bool:
    """Bring one record under `limit` stored bytes, least loss first: its previews, then its per-tool listing, then
    its components. Each step taken is named in the record's `reduced`, so what is missing is said. False when even
    the bare record does not fit."""
    record["stored_bytes"] = _stored(record)
    for label, step in _REDUCTIONS:
        if record["stored_bytes"] <= limit:
            break
        if label in record["reduced"]:
            continue
        step(record)
        record["reduced"].append(label)
        record["stored_bytes"] = _stored(record)
    return record["stored_bytes"] <= limit


class Journal:
    """The records, in memory: per bound session (the only ones a reader can ask for) and, apart, per backend that
    ran with no session bound. Bounded in sessions, in records per session, in bytes per record and in bytes per
    session; a record over its share is reduced (and says so), the oldest records go first, and a record that
    cannot fit even alone is not kept. The bounds hold after an update too. A read returns a detached copy."""

    def __init__(self, *, max_sessions: int = MAX_SESSIONS, max_private: int = MAX_PRIVATE,
                 max_records: int = MAX_RECORDS, max_session_bytes: int = MAX_SESSION_BYTES,
                 max_record_bytes: int | None = None) -> None:
        self._lock = threading.Lock()
        self._stores: dict[str, OrderedDict[str, OrderedDict[str, dict]]] = {"session": OrderedDict(),
                                                                           "private": OrderedDict()}
        self._limits = {"session": max(1, max_sessions), "private": max(1, max_private)}
        self.max_records, self.max_session_bytes = max(1, max_records), max(1, max_session_bytes)
        self.max_record_bytes = min(self.max_session_bytes,
                                    max(1, MAX_RECORD_BYTES if max_record_bytes is None else max_record_bytes))

    def _fit(self, store: OrderedDict, ident: str) -> None:
        """Hold the session's bounds, oldest record out first; even the last one goes when it alone is too large."""
        records = store.get(ident)
        if records is None:
            return
        while records and (len(records) > self.max_records
                           or sum(r["stored_bytes"] for r in records.values()) > self.max_session_bytes):
            records.popitem(last=False)
        if not records:
            del store[ident]

    def add(self, kind: str, ident: str, record: dict) -> bool:
        """Keep `record`; False when it could not be kept within the bounds (nothing of it is then stored)."""
        with self._lock:
            store = self._stores[kind]
            record.setdefault("reduced", [])
            if not _reduce(record, self.max_record_bytes):
                return False
            records = store.setdefault(ident, OrderedDict())
            store.move_to_end(ident)
            records[record["request_id"]] = record
            self._fit(store, ident)
            while len(store) > self._limits[kind]:
                store.popitem(last=False)
            return record["request_id"] in (store.get(ident) or {})

    def update(self, kind: str, ident: str, request_id: str, change: Callable[[dict], None]) -> None:
        with self._lock:
            store = self._stores[kind]
            record = store.get(ident, {}).get(request_id)
            if record is None:            # evicted since: there is nothing left to say it about
                return
            change(record)
            # an update is small, but the bounds are of what is stored now, not of what was stored at first
            if not _reduce(record, self.max_record_bytes):
                del store[ident][request_id]
            self._fit(store, ident)

    def list(self, session_id: Any) -> list[dict]:
        """A bound session's records, newest first, as summaries: no component, no preview, no prompt text."""
        if not isinstance(session_id, str) or not session_id:
            return []
        with self._lock:
            records = self._stores["session"].get(session_id) or {}
            return [_summary(r) for r in reversed(records.values())]

    def get(self, session_id: Any, request_id: Any) -> dict | None:
        """One record of a bound session, detached; None for another session's, an evicted or an unknown one."""
        if not isinstance(session_id, str) or not session_id or not isinstance(request_id, str):
            return None
        with self._lock:
            record = (self._stores["session"].get(session_id) or {}).get(request_id)
            if record is None:
                return None
            return {key: deepcopy(value) for key, value in record.items() if not key.startswith("_")}

    def stored_bytes(self, kind: str, ident: str) -> int:
        with self._lock:
            return sum(r["stored_bytes"] for r in (self._stores[kind].get(ident) or {}).values())

    def clear(self) -> None:
        with self._lock:
            for store in self._stores.values():
                store.clear()


JOURNAL = Journal()

_SUMMARY_KEYS = ("schema", "harness", "package_version", "request_id", "group_id", "attempt", "created_at",
                 "transport", "provider", "turn", "model", "recipient", "origin", "worker_id", "agent", "window",
                 "window_basis", "status", "phase", "http_status", "error_type", "cleanup_error_type", "body",
                 "stored_bytes", "protocol", "purpose")


def _summary(record: dict) -> dict:
    usage, request, tools = record["usage"], record["request"], record["tools"]
    return {**{key: record.get(key) for key in _SUMMARY_KEYS},
            "reduced": list(record.get("reduced") or ()),
            "usage": {key: usage.get(key) for key in ("observed", "observations", "prompt_tokens", "cached_tokens",
                                                      "completion_tokens", "input_tokens", "output_tokens")},
            "provider_count": deepcopy(record.get("provider_count")),
            "tokenizer_method": record["tokenizer"].get("method"),
            "tokenizer_reason": record["tokenizer"].get("reason"),
            "tools_tokens": tools.get("tokens"), "tools_tokens_reason": tools.get("tokens_reason"),
            "request": {key: request.get(key) for key in ("messages", "tools", "images", "bytes", "digest",
                                                          "complete", "components_recorded", "messages_omitted")}}


class Attempt:
    """One transport attempt's record, as the transport tells it what happened. Every method is total: a failure in
    here is dropped (its type logged at debug), never raised into the request."""
    __slots__ = ("_journal", "_kind", "_ident", "_id")

    def __init__(self, journal: Journal | None = None, kind: str = "", ident: str = "", request_id: str = "") -> None:
        self._journal, self._kind, self._ident, self._id = journal, kind, ident, request_id

    def _change(self, change: Callable[[dict], None]) -> None:
        if self._journal is None:
            return
        try:
            self._journal.update(self._kind, self._ident, self._id, change)
        except Exception as exc:
            _LOG.debug("context inspection update dropped (%s)", type(exc).__name__)

    def response(self, http_status: Any) -> None:
        """The response's status line arrived: 200 is `response_received`, anything else `refused`."""
        def change(record: dict) -> None:
            record["http_status"] = http_status if type(http_status) is int else None
            record["status"] = "response_received" if http_status == 200 else "refused"
            record["phase"] = "body"
        self._change(change)

    def unwound(self, exc: BaseException | None) -> None:
        """How the attempt ended. None: the caller finished with the response (a stream then says whether its
        `[DONE]` was read). A cancellation is `interrupted`; a caller closing the stream, or Dream cutting a
        repeating reply, leaves the status as it was; any other exception is `failed`, by its type name alone
        (a read that failed after a refusal's status line too: the HTTP status stays beside it)."""
        def change(record: dict) -> None:
            if exc is None:
                if record["transport"] == "stream":
                    record["body"] = "done_seen" if record.pop("_done", False) else "ended_without_done"
                return
            record.pop("_done", None)
            name = type(exc).__name__[:80]
            if isinstance(exc, asyncio.CancelledError):
                record["status"] = "interrupted"
            elif isinstance(exc, GeneratorExit):
                record["body"] = "closed_by_caller"
            elif name == "_ControlledStreamCut":
                record["body"] = "cut_by_dream"
            else:
                record["status"], record["error_type"] = "failed", name
        self._change(change)

    def cleanup(self, exc: BaseException) -> None:
        """Closing the response raised `exc` (after `unwound` said how the body went). The cleanup's own failure is
        recorded by type; it turns an exchange that had looked fine into `interrupted` (a cancellation) or
        `failed`, and leaves one that had already failed or been interrupted as it was."""
        def change(record: dict) -> None:
            record["cleanup_error_type"] = type(exc).__name__[:80]
            record["phase"] = "cleanup"
            if record["status"] in ("failed", "interrupted"):
                return
            if isinstance(exc, asyncio.CancelledError):
                record["status"] = "interrupted"
            else:
                record["status"], record["error_type"] = "failed", type(exc).__name__[:80]
        self._change(change)

    def usage(self, usage: Any) -> None:
        """This response's `usage`. A later one in the same response replaces it (a stream may repeat its usage);
        nothing is added up."""
        try:
            seen = read_usage(usage)
        except Exception as exc:
            _LOG.debug("context inspection usage dropped (%s)", type(exc).__name__)
            return

        def change(record: dict) -> None:
            record["usage"] = {"observed": True, "observations": record["usage"]["observations"] + 1,
                               "reason": None, **seen}
        self._change(change)

    def sse_line(self, line: Any) -> None:
        """A line of the stream, as the caller is about to get it. Read only: the line itself is not touched."""
        if self._journal is None or not isinstance(line, str) or not line.startswith("data:"):
            return
        try:
            if '"usage"' in line and '"usage":null' not in line:      # most chunks say null: not parsed twice
                chunk = json.loads(line[5:])
                if isinstance(chunk, dict) and chunk.get("usage"):
                    self.usage(chunk["usage"])
            elif line[5:].strip() == "[DONE]":
                self._change(lambda record: record.__setitem__("_done", True))
        except Exception as exc:
            _LOG.debug("context inspection line dropped (%s)", type(exc).__name__)

    def body(self, data: Any) -> None:
        """A non-streaming response's parsed body. An `error` inside a 200 is said here (`error_in_body`); the
        transport then tells `unwound` the error it raises for it, which is what makes the attempt `failed`."""
        try:
            if not isinstance(data, dict):
                self._change(lambda record: record.__setitem__("body", "not_an_object"))
                return
            if data.get("usage"):
                self.usage(data["usage"])
            said = "error_in_body" if data.get("error") is not None else "json_object"
            self._change(lambda record: record.__setitem__("body", said))
        except Exception as exc:
            _LOG.debug("context inspection body dropped (%s)", type(exc).__name__)

    def responses_body(self, data: Any) -> None:
        """Observe numeric protocol metadata only, after the caller's cleanup fences.

        Never retain output, advice, response IDs or provider-controlled errors.
        Counting input is a preflight observation, not billable inference usage.
        """
        if not isinstance(data, dict):
            return
        def change(record):
            if record.get("protocol") != "openai_responses":
                return
            record["body"] = "json_object"
            if record["purpose"] == "input_tokens":
                value = _count(data.get("input_tokens"))
                if data.get("object") == "response.input_tokens" and value is not None:
                    record["provider_count"].update(observed=True, input_tokens=value, reason=None)
            elif isinstance(data.get("usage"), dict):
                record["usage"] = {"observed": True,
                    "observations": record["usage"]["observations"] + 1,
                    "reason": None, **read_responses_usage(data["usage"])}
        self._change(change)


NO_ATTEMPT = Attempt()


# --- capture --------------------------------------------------------------------------------------------------

class _Work:
    """One capture's work allowance. `take(chars)` is asked before text is walked, serialised or hashed;
    `count` before text is tokenized. A refusal is a reason in the record (and a tally in `skipped`), never an
    exception and never a silent gap."""

    def __init__(self, counter: Callable[..., tuple[int | None, str | None]]) -> None:
        self.left, self.count_left, self.counter = MAX_WORK_CHARS, MAX_COUNT_TOTAL_CHARS, counter
        self.skipped: dict[str, int] = {}

    def note(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def take(self, chars: int) -> bool:
        if chars > self.left:
            self.note("work_budget_exhausted")
            return False
        self.left -= chars
        return True

    def _allow(self, chars: int) -> bool:
        if chars > self.count_left:
            return False
        self.count_left -= chars
        return True

    def count(self, text: str, sha: str) -> tuple[int | None, str | None]:
        tokens, why = self.counter(text, sha, self._allow)
        if why in ("count_budget_exhausted", "component_too_large_to_count", "tokenizer_encode_failed",
                   # the optional helper counter's per-piece failures (context_counter); never seen by default
                   "component_not_utf8", "counter_deadline", "counter_busy", "counter_encode_failed",
                   "counter_response_invalid", "counter_response_oversized", "counter_helper_exited",
                   "counter_helper_failed"):
            self.note(why)
        return tokens, why

    def canonical(self, value: Any) -> str | None:
        """`value`'s canonical JSON, or None when building it would pass the allowance (it is weighed first, so a
        huge value is never serialised just to be thrown away).

        The weighing is work too, and it is charged whether or not the value is then accepted: an accepted value
        costs its weight (at least 8 per element visited); a refused one costs one unit per element the weighing
        visited before it stopped. So the elements visited for refused values, over a whole capture, never number
        more than the allowance the capture began with, and with nothing left nothing is walked at all."""
        if self.left <= 0:
            self.note("work_budget_exhausted")
            return None
        weight, visited = _weigh(value, self.left)
        if weight > self.left:
            self.note("work_budget_exhausted")
            self.left -= min(visited, self.left)
            return None
        self.left -= weight
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_opaque)


class _Previews:
    """One record's preview allowance: past it a component keeps its hash and size and says its preview was left
    out."""

    def __init__(self, allowance: int = MAX_PREVIEW_BYTES) -> None:
        self.left = allowance

    def take(self, text: str, limit: int) -> dict[str, Any]:
        shown, cut = preview(text, limit)
        cost = len(shown.encode("utf-8", "replace"))
        if cost > self.left:
            return {"preview": "", "preview_truncated": bool(text), "preview_omitted": True,
                    "preview_reason": "preview_allowance_used"}
        self.left -= cost
        return {"preview": shown, "preview_truncated": cut, "preview_omitted": False, "preview_reason": None}


def _no_text(reason: str, preview_reason: str) -> dict[str, Any]:
    return {"sha256": None, "bytes": None, "chars": None, "tokens": None, "tokens_reason": reason,
            "preview": "", "preview_truncated": False, "preview_omitted": True, "preview_reason": preview_reason}


def _text_fields(text: str, work: _Work, previews: _Previews | None, limit: int) -> dict[str, Any]:
    """A piece of text as recorded: SHA-256 and UTF-8 size of all of it, a real tokenizer's count of it alone (or
    null and why), and its bounded preview. `previews` None: the text is never shown (a call's arguments)."""
    if work.take(len(text)):
        data = text.encode("utf-8", "replace")
        sha, size = hashlib.sha256(data).hexdigest(), len(data)
        tokens, why = work.count(text, sha)
    else:
        sha = size = tokens = None
        why = "work_budget_exhausted"
    fields = {"sha256": sha, "bytes": size, "chars": len(text), "tokens": tokens, "tokens_reason": why}
    if previews is None:
        fields.update(preview="", preview_truncated=bool(text), preview_omitted=True,
                      preview_reason="arguments_not_shown")
    else:
        fields.update(previews.take(text, limit))
    return fields


def _units(message: Any) -> list[tuple]:
    """What one message will be stored as, before anything is hashed: at most MAX_MESSAGE_COMPONENTS units. A
    system message with text is one unit per provenance span; any other message is itself, then each text part of
    a multipart content, then each tool call. Past the cap the remaining units are merged into one that says how
    many and of what kind."""
    if not isinstance(message, dict):
        return [("message", "none", 0, 0)]
    content = message.get("content")
    units: list[tuple] = []
    if message.get("role") in ("system", "developer") and isinstance(content, str):
        units = [("section", start, end, source, detail) for start, end, source, detail in spans_of(content)]
    else:
        parts = content[:MAX_PARTS] if isinstance(content, list) else []
        images = sum(1 for part in parts if isinstance(part, dict) and part.get("type") == "image_url")
        texts = [(index, part["text"]) for index, part in enumerate(parts)
                 if isinstance(part, dict) and part.get("type") != "image_url" and isinstance(part.get("text"), str)]
        form = ("text" if isinstance(content, str) and content else
                "multipart" if isinstance(content, list) else "none")
        unseen = len(content) - len(parts) if isinstance(content, list) else 0
        units.append(("message", form, images, len(parts) - images - len(texts) + unseen))
        units.extend(("part", index, text) for index, text in texts)
        calls = message.get("tool_calls")
        for index, call in enumerate(calls[:MAX_PARTS] if isinstance(calls, list) else ()):
            fn = call.get("function") if isinstance(call, dict) else None
            fn = fn if isinstance(fn, dict) else {}
            units.append(("call", index, fn.get("name"), fn.get("arguments")))
    if len(units) <= MAX_MESSAGE_COMPONENTS:
        return units
    rest = units[MAX_MESSAGE_COMPONENTS - 1:]
    kinds: dict[str, int] = {}
    for unit in rest:
        kinds[unit[0]] = kinds.get(unit[0], 0) + 1
    span = (rest[0][1], rest[-1][2]) if rest[0][0] == "section" else None
    return [*units[:MAX_MESSAGE_COMPONENTS - 1], ("merged", len(rest), kinds, span)]


def _realise(index: int, message: Any, digest: str | None, size: int | None, units: list[tuple], work: _Work,
             previews: _Previews) -> list[dict]:
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    base = {"message_index": index, "role": _short(message.get("role"), 32), "message_sha256": digest,
            "message_bytes": size}
    out: list[dict] = []
    for unit in units:
        kind = unit[0]
        if kind == "section":
            _, start, end, source, detail = unit
            out.append({**base, "kind": "system_section", "source": _short(source, 64) or "unknown",
                        "detail": _detail(detail), "start": start, "end": end,
                        **_text_fields(content[start:end], work, previews, SECTION_PREVIEW_CHARS)})
        elif kind == "message":
            _, form, images, others = unit
            calls = message.get("tool_calls")
            reasoning = message.get("reasoning_content")
            if form == "text":
                fields = _text_fields(content, work, previews, PREVIEW_CHARS)
            elif form == "multipart":
                # no joined text is invented: each text part is its own component, counted alone
                fields = _no_text("multipart_text_counted_per_part", "see_parts")
            else:
                fields = _no_text("component_has_no_text", "no_text")
            out.append({**base, "kind": "message", "source": "conversation", "detail": None,
                        "name": _short(message.get("name"), 64), "content_form": form, "images": images,
                        "other_parts": others,
                        "tool_call_count": len(calls) if isinstance(calls, list) else 0,
                        # sized only: reasoning is not tokenized here (NOT_COUNTED)
                        "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
                        # the count is of the content's text alone, never the message's whole cost
                        "tokens_scope": "content_text_only", **fields})
        elif kind == "part":
            _, part_index, text = unit
            out.append({**base, "kind": "message_part", "source": "conversation", "detail": None,
                        "part_index": part_index, "tokens_scope": "part_text_only",
                        **_text_fields(text, work, previews, PREVIEW_CHARS)})
        elif kind == "call":
            _, call_index, name, arguments = unit
            if isinstance(arguments, str):
                representation, text = "function_arguments_string_as_sent", arguments
            elif arguments is None:
                representation, text = "no_arguments", None
            else:
                representation, text = "function_arguments_canonical_json", work.canonical(arguments)
            # a call's arguments can hold a file's whole body, a command or a credential: hashed, sized and
            # counted, never shown
            fields = (_text_fields(text, work, None, 0) if isinstance(text, str) else
                      _no_text("component_has_no_text" if arguments is None else "work_budget_exhausted",
                               "arguments_not_shown"))
            out.append({**base, "kind": "tool_call", "source": "conversation", "detail": None,
                        "call_index": call_index, "name": _short(name) or "unnamed",
                        "representation": representation, "tokens_scope": "arguments_text_only", **fields})
        else:
            _, merged, kinds, span = unit
            fields = _no_text("merged_not_counted", "merged")
            extra: dict[str, Any] = {}
            if span is not None and isinstance(content, str):
                # the merged stretch of the system text is still hashed, sized and counted as one piece
                extra = {"start": span[0], "end": span[1]}
                fields = {**_text_fields(content[span[0]:span[1]], work, None, 0), "preview_reason": "merged"}
            out.append({**base, "kind": "merged_components", "source": "merged", "detail": None, "merged": merged,
                        "merged_kinds": dict(kinds), "tokens_scope": "merged_text_as_one_piece", **extra, **fields})
    return out


def _images(message: Any) -> int:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return 0
    return sum(1 for part in content[:MAX_PARTS] if isinstance(part, dict) and part.get("type") == "image_url")


def _components(messages: list, work: _Work) -> tuple[list[dict], dict[str, Any]]:
    """The request's messages as stored components, and its totals. At most MAX_COMPONENTS components are stored,
    whatever they are made of: the head of the conversation, then as much of its tail as fits; the messages between
    are hashed and sized (while the work allowance lasts) and the totals say how many were left out."""
    total = len(messages)
    previews = _Previews()
    # which messages are stored: decided on unit counts alone, before any hashing
    used = head_end = 0
    while head_end < total:
        units = len(_units(messages[head_end]))
        if head_end and used + units > _HEAD_COMPONENTS:
            break
        used, head_end = used + units, head_end + 1
    tail_start = total
    while tail_start > head_end:
        units = len(_units(messages[tail_start - 1]))
        if used + units > MAX_COMPONENTS:
            break
        used, tail_start = used + units, tail_start - 1
    edge = MAX_WORK_MESSAGES // 2
    components: list[dict] = []
    running = hashlib.sha256()
    stats = {"messages": total, "images": 0, "bytes": 0, "messages_omitted": 0, "omitted_bytes": 0,
             "messages_not_digested": 0, "messages_not_walked": 0, "messages_not_walked_range": None}
    # Too many messages to walk them all: the stretch [lo, hi) between the two edges (and outside what is stored)
    # is left out by arithmetic. Its indexes are never visited and its messages never read, and the totals say
    # how many and which.
    lo = hi = total
    if total > MAX_WORK_MESSAGES:
        lo = max(edge, head_end)
        hi = max(lo, min(total - edge, tail_start))
    if hi > lo:
        stats["messages_omitted"] += hi - lo
        stats["messages_not_digested"] += hi - lo
        stats["messages_not_walked"] = hi - lo
        stats["messages_not_walked_range"] = [lo, hi]
        work.skipped["message_walk_limit"] = hi - lo
    for index in (*range(lo), *range(hi, total)) if hi > lo else range(total):
        kept = index < head_end or index >= tail_start
        message = messages[index]
        text = work.canonical(message if isinstance(message, dict) else {"role": None, "content": None})
        if text is None:
            digest = size = None
            stats["messages_not_digested"] += 1
        else:
            data = text.encode("utf-8", "replace")
            digest, size = hashlib.sha256(data).hexdigest(), len(data)
            running.update(digest.encode("ascii"))
            stats["bytes"] += size
        stats["images"] += _images(message)
        if not kept:
            stats["messages_omitted"] += 1
            stats["omitted_bytes"] += size or 0
            continue
        components.extend(_realise(index, message, digest, size, _units(message), work, previews))
    del components[MAX_COMPONENTS:]                   # the selection above already holds this; a last guarantee
    stats["components_recorded"] = len(components)
    # totals (bytes, images) and the digest cover the digested messages only; `complete` says whether that is all
    stats["complete"] = stats["messages_not_digested"] == 0
    stats["message_digests"] = running.hexdigest() if stats["complete"] else None
    return components, stats


def _json_fields(value: Any, work: _Work) -> dict[str, Any]:
    """A JSON value (a tool schema, the tools array) as recorded: SHA-256 and size of its canonical JSON and a real
    tokenizer's count of that JSON text (not of the form a chat template renders it in), or null and why."""
    text = work.canonical(value)
    if text is None:
        return {"sha256": None, "bytes": None, "tokens": None, "tokens_reason": "work_budget_exhausted"}
    data = text.encode("utf-8", "replace")
    sha = hashlib.sha256(data).hexdigest()
    tokens, why = work.count(text, sha)
    return {"sha256": sha, "bytes": len(data), "tokens": tokens, "tokens_reason": why}


def _tools(tools: Any, work: _Work) -> dict[str, Any]:
    """The tool schemas the request carries: the whole array's hash, size and count, and the first MAX_TOOLS_LISTED
    schemas each with its name, hash, size, count, parameter names and a bounded preview of its description."""
    tools = tools if isinstance(tools, list) else []
    previews = _Previews(MAX_TOOL_PREVIEW_BYTES)
    out: dict[str, Any] = {"count": len(tools), "representation": "canonical_json_text",
                           "tokens_scope": "tools_array_json_text_only"}
    out.update(_json_fields(tools, work))
    if not tools:
        out["tokens"], out["tokens_reason"] = None, "request_has_no_tools"
    listed = []
    for position, tool in enumerate(tools[:MAX_TOOLS_LISTED]):
        fn = tool.get("function") if isinstance(tool, dict) else None
        fn = fn if isinstance(fn, dict) else {}
        parameters = fn.get("parameters")
        properties = parameters.get("properties") if isinstance(parameters, dict) else None
        properties = properties if isinstance(properties, dict) else {}
        description = fn.get("description") if isinstance(fn.get("description"), str) else ""
        listed.append({"index": position, "name": _short(fn.get("name")) or "unnamed",
                       "tokens_scope": "schema_json_text_only", **_json_fields(tool, work),
                       "parameters": [_short(key, 64) or "unnamed" for key in islice(properties, 16)],
                       "parameters_omitted": max(0, len(properties) - 16),
                       "description_chars": len(description),
                       **previews.take(description, TOOL_PREVIEW_CHARS)})
    out["listed"] = listed
    out["listed_omitted"] = max(0, len(tools) - MAX_TOOLS_LISTED)
    out["names"] = [entry["name"] for entry in listed]
    out["names_omitted"] = out["listed_omitted"]
    return out


def capture(payload: Any, meta: dict | None, *, session_id: Any, private_id: str, transport: str,
            provider: Any = None, journal: Journal | None = None) -> Attempt:
    """Record the request `payload` as one attempt and return its handle. `meta` is the caller's private note of
    who the request is for (recipient, origin, worker, window, turn): it is counted up for each attempt of the same
    logical request and is never part of the payload, which is only read. `session_id` is the BOUND session's, or
    None: the record is then kept under `private_id`, where no reader can ask for it. Never raises an Exception: a
    request that cannot be recorded has no record, and only the failure's type is logged. A cancellation is not
    caught here (nothing in a capture awaits)."""
    if os.environ.get("DREAM_CONTEXT_INSPECTION", "1") == "0" or not isinstance(payload, dict):
        return NO_ATTEMPT
    try:
        return _capture(payload, meta if isinstance(meta, dict) else {}, session_id, private_id, transport, provider,
                        JOURNAL if journal is None else journal)
    except Exception as exc:
        _LOG.debug("context inspection capture dropped (%s)", type(exc).__name__)
        return NO_ATTEMPT


def _capture(payload: dict, meta: dict, session_id: Any, private_id: str, transport: str, provider: Any,
             journal: Journal) -> Attempt:
    attempt = meta["attempts"] = int(meta.get("attempts") or 0) + 1
    group = meta.setdefault("group", uuid.uuid4().hex)
    model = payload.get("model") if isinstance(payload.get("model"), str) else None
    counter, tokenizer = _counter_for(model)
    work = _Work(counter)
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    components, stats = _components(messages, work)
    tools = _tools(payload.get("tools"), work)
    stats["tools"] = tools["count"]
    stats["bytes"] += tools["bytes"] or 0
    message_digests = stats.pop("message_digests")
    if message_digests is None or tools["sha256"] is None:
        stats["complete"] = False
        stats["digest"], stats["digest_reason"] = None, "work_budget_exhausted"
    else:
        stats["digest"] = hashlib.sha256((message_digests + tools["sha256"]).encode("ascii")).hexdigest()
        stats["digest_reason"] = None
    recipient = meta.get("recipient")
    window = meta.get("window")
    bound = isinstance(session_id, str) and bool(session_id) and len(session_id) <= 200
    record = {
        "schema": SCHEMA, "harness": HARNESS, "package_version": PACKAGE["version"],
        "request_id": uuid.uuid4().hex, "group_id": group, "attempt": attempt,
        "created_at": time.time(), "transport": transport, "provider": _short(provider, 64),
        "session": "bound" if bound else "not_bound",
        "turn": meta.get("turn") if type(meta.get("turn")) is int else None,
        "model": _short(model, 200),
        "recipient": recipient if recipient in RECIPIENTS else "unknown",
        "origin": _short(meta.get("origin"), 64) or "unregistered_caller",
        "worker_id": _short(meta.get("worker_id"), 100), "agent": _short(meta.get("agent"), 100),
        "window": window if type(window) is int and window > 0 else None,
        "window_basis": _short(meta.get("window_basis"), 64),
        "params": {"stream": payload.get("stream") is True,
                   "max_tokens": _count(payload.get("max_tokens")),
                   "tool_choice": _short(payload.get("tool_choice"), 32),
                   "reasoning_effort": _short(payload.get("reasoning_effort"), 32)},
        "status": "attempted", "phase": "before_response", "http_status": None, "error_type": None,
        "cleanup_error_type": None, "body": None,
        "usage": {"observed": False, "observations": 0, "reason": "no_usage_reported_by_this_attempt",
                  "prompt_tokens": None, "prompt_tokens_reason": "not_reported",
                  "cached_tokens": None, "cached_tokens_reason": "not_reported",
                  "completion_tokens": None, "completion_tokens_reason": "not_reported"},
        "tokenizer": tokenizer, "request": stats, "tools": tools, "components": components,
        # what this capture left unprocessed, by reason; empty when everything was hashed and (where a tokenizer
        # is bound) counted
        "work": {"skipped": dict(work.skipped)},
        "reduced": [],
    }
    kind, ident = ("session", session_id) if bound else ("private", str(private_id))
    if not journal.add(kind, ident, record):
        return NO_ATTEMPT
    return Attempt(journal, kind, ident, record["request_id"])


def capture_responses(payload: Any, meta: dict | None = None, *, session_id: Any,
                      private_id: str, purpose: str, journal: Journal | None = None) -> Attempt:
    """Capture the explicit advisory transport's bounded actual POST input.

    Its six effective input controls must be identical for counting and inference.
    The whole provider count cannot be apportioned to component text; this path
    starts no local tokenizer. Unknown request forms are omitted, never guessed.
    """
    if os.environ.get("DREAM_CONTEXT_INSPECTION", "1") == "0":
        return NO_ATTEMPT
    try:
        fields = {"model", "input", "tools", "tool_choice", "parallel_tool_calls", "truncation"}
        allowed = fields | {"max_output_tokens", "store", "stream"}
        if (type(payload) is not dict or len(payload) > len(allowed)
                or purpose not in {"input_tokens", "inference"}
                or not fields <= payload.keys() or not payload.keys() <= allowed
                or type(payload["input"]) is not str or len(payload["input"]) > MAX_COUNT_CHARS
                or type(payload["model"]) is not str or len(payload["model"]) > 200
                or type(payload["tools"]) is not list or payload["tools"]
                or payload["tool_choice"] != "none" or payload["parallel_tool_calls"] is not False
                or payload["truncation"] != "disabled"
                or any(type(payload[key]) is not bool for key in ("store", "stream") if key in payload)
                or "max_output_tokens" in payload and _count(payload["max_output_tokens"]) is None):
            return NO_ATTEMPT
        def canonical(value):
            return json.dumps(value, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False).encode("utf-8")
        effective = {key: payload[key] for key in fields}
        encoded, body = canonical(effective), canonical(payload)
        text = payload["input"]
        shown, cut = preview(text, SECTION_PREVIEW_CHARS)
        preview_bytes = shown.encode("utf-8")
        if len(preview_bytes) > MAX_PREVIEW_BYTES:
            shown, cut = preview_bytes[:MAX_PREVIEW_BYTES].decode("utf-8", "ignore"), True
        meta = meta if type(meta) is dict else {}
        attempt = meta["attempts"] = int(meta.get("attempts") or 0) + 1
        group = meta.setdefault("group", uuid.uuid4().hex)
        bound = isinstance(session_id, str) and bool(session_id) and len(session_id) <= 200
        record = {
            "schema": SCHEMA, "harness": HARNESS, "package_version": PACKAGE["version"],
            "request_id": uuid.uuid4().hex, "group_id": group, "attempt": attempt,
            "created_at": time.time(), "protocol": "openai_responses", "purpose": purpose,
            "transport": "post", "provider": "openai", "session": "bound" if bound else "not_bound",
            "model": payload["model"], "recipient": "advisor", "origin": "prepared_responses_help",
            "turn": None, "worker_id": None, "agent": None, "window": None, "window_basis": "not_reported",
            "params": {key: payload[key] for key in allowed - {"input", "model"} if key in payload},
            "status": "attempted", "phase": "before_response", "http_status": None,
            "error_type": None, "cleanup_error_type": None, "body": None,
            "provider_count": {"observed": False, "input_tokens": None, "reason": "not_reported",
                               "method": "openai_responses_input_tokens"},
            "usage": {"observed": False, "observations": 0, "reason": "no_usage_reported_by_this_attempt",
                      **read_responses_usage(None)},
            "tokenizer": {"method": None, "reason": "provider_total_only", "engine_exact": False},
            "request": {"messages": None, "tools": 0, "images": 0, "bytes": len(body),
                        "input_sha256": hashlib.sha256(encoded).hexdigest(),
                        "body_sha256": hashlib.sha256(body).hexdigest(),
                        "complete": True, "components_recorded": 1},
            "tools": {"count": 0, "listed": [], "names": []},
            "components": [{"kind": "responses_input", "source": "host_approved_packet",
                            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "bytes": len(text.encode("utf-8")), "chars": len(text),
                            "tokens": None, "tokens_reason": "provider_total_only",
                            "preview": shown, "preview_truncated": cut, "preview_omitted": False}],
            "work": {"skipped": {}}, "reduced": []}
        journal = JOURNAL if journal is None else journal
        kind, ident = ("session", session_id) if bound else ("private", str(private_id))
        if journal.add(kind, ident, record):
            return Attempt(journal, kind, ident, record["request_id"])
    except Exception as exc:
        _LOG.debug("Responses context inspection dropped (%s)", type(exc).__name__)
    return NO_ATTEMPT
