"""DREAM-249: notice one streamed reply repeating the same tool call, while it is still being written.

The loop guard in the HTTP backend acts after a reply has ended. A local model that locks onto one call can write
it again and again inside a single reply until the output ceiling (60,000 tokens on the engine), and nothing can
end that reply from Dream's side. This module is the narrow detector for exactly that: the same tool NAME with the
same JSON-object ARGUMENTS at `threshold` (three) distinct tool-call indexes of one streamed reply.

What counts, and what does not:

* A call is identified by its name and the canonical form of its parsed arguments. Whitespace and key order are
  not part of the identity, and neither is the call's id. List order and value types are: `1`, `1.0`, `true` and
  `"1"` are four different values, and an integer keeps every digit.
* A call counts only once its arguments are one complete JSON object, read strictly: `NaN`/`Infinity`, a number
  that overflows to infinity, a repeated key in one object, nesting deeper than `max_depth`, a name or arguments
  past the size limits, or text after the object's closing brace give the call NO identity. A call without an
  identity is never counted, and it is not among the calls kept at a cut.
* An index is counted at most once: the count is of the indexes that hold a signature NOW. Text arriving later
  for an index that had completed (a longer name, more argument text) changes or removes that index's signature,
  and the count follows.
* Nothing else is read. Reply text, reasoning and a tool-call preview never reach this module, and a repeated
  substring inside one call's arguments (code, a JSON list of equal items) is one call.

Cost: each argument character is passed once by a small bracket/string scanner; the arguments of an index are
parsed in full exactly once, when their top-level object closes (a closed object can only become invalid by more
text, never valid again). Per index the detector keeps a few integers, the name (bounded) and two digests. It keeps
no argument text, and a `RepetitionCut` carries none.

Limit, stated plainly: a signature is what an index holds at the moment it is read from a stream that has not
ended. The server has not said that call is final. Acting on a cut is Dream's own decision to stop reading, not
the server's completion of the reply, and the caller must treat the request's outcome as uncertain.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

THRESHOLD = 3                 # distinct indexes holding one signature that end the reading
MAX_DEPTH = 32                # deepest nesting of objects and lists an identity is computed for
MAX_ARGS_CHARS = 1_048_576    # longest arguments text an identity is computed for
MAX_NAME_CHARS = 256          # longest tool name an identity is computed for
MAX_INDEXES = 1024            # tool-call indexes tracked in one reply; later ones are never counted or kept

_WHITESPACE = " \t\n\r"       # JSON's own whitespace, nothing more
_STRING_STOP = re.compile(r'["\\]')
_STRUCTURE = re.compile(r'["{}\[\]]')


@dataclass(frozen=True)
class RepetitionCut:
    """Why reading stopped and which calls are kept. Names, indexes and counts only: no argument text."""
    tool: str                     # the repeated call's name
    occurrences: int              # how many indexes held that call when reading stopped
    repeated: tuple[int, ...]     # those indexes, ascending
    kept: tuple[int, ...]         # the lowest index of each distinct complete call, ascending
    duplicates: int               # complete calls that repeat a kept one


class _Index:
    __slots__ = ("name", "chars", "started", "depth", "in_string", "escape", "closed", "dead", "args", "signature")

    def __init__(self) -> None:
        self.name = ""
        self.chars = 0
        self.started = self.in_string = self.escape = self.closed = self.dead = False
        self.depth = 0
        self.args: bytes | None = None        # digest of the canonical arguments, once the object closed and parsed
        self.signature: bytes | None = None   # digest of (name, args) while both are there


def _reject_constant(text: str) -> Any:
    raise ValueError("non-finite number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("repeated key")
    return value


def _length(n: int) -> bytes:
    return str(n).encode("ascii") + b":"


def _feed(digest: Any, value: Any, depth: int, max_depth: int) -> None:
    """The canonical form of a parsed JSON value, fed to `digest`: every value carries a type tag and every
    variable-length part its length, so no two different values share a form. Object keys are sorted; list order
    is kept. A float is identified by the double a tool handler would receive (its repr), an integer by its
    digits; bool is checked before int because bool is an int in Python."""
    if value is None:
        digest.update(b"n")
    elif value is True:
        digest.update(b"t")
    elif value is False:
        digest.update(b"f")
    elif type(value) is int:
        text = str(value).encode("ascii")
        digest.update(b"i" + _length(len(text)) + text)
    elif type(value) is float:
        text = repr(value).encode("ascii")
        digest.update(b"d" + _length(len(text)) + text)
    elif type(value) is str:
        data = value.encode("utf-8", "surrogatepass")
        digest.update(b"s" + _length(len(data)) + data)
    elif type(value) is list:
        if depth >= max_depth:
            raise ValueError("too deep")
        digest.update(b"[" + _length(len(value)))
        for item in value:
            _feed(digest, item, depth + 1, max_depth)
    elif type(value) is dict:
        if depth >= max_depth:
            raise ValueError("too deep")
        digest.update(b"{" + _length(len(value)))
        for key in sorted(value):
            _feed(digest, key, depth + 1, max_depth)
            _feed(digest, value[key], depth + 1, max_depth)
    else:
        raise ValueError("not a JSON value")


def canonical_arguments_digest(text: str, *, max_depth: int = MAX_DEPTH) -> bytes | None:
    """The digest of `text` read strictly as one JSON object, or None when it is not one (see the module's rules).
    Never raises for bad input."""
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float,
                           object_pairs_hook=_unique_object)
        if type(value) is not dict:
            return None
        digest = hashlib.sha256()
        _feed(digest, value, 0, max_depth)
        return digest.digest()
    except (ValueError, RecursionError, TypeError, OverflowError, MemoryError):
        # ValueError covers JSONDecodeError, the hooks above, an integer past Python's digit limit and a lone
        # surrogate that cannot be encoded.
        return None


def _signature(name: str, args: bytes) -> bytes:
    data = name.encode("utf-8", "surrogatepass")
    return hashlib.sha256(_length(len(data)) + data + args).digest()


class ToolCallRepetitionDetector:
    """Fed every tool-call fragment of one streamed reply, in arrival order; says when `threshold` distinct indexes
    hold the same complete call. After that it is spent: `cut` stays what it returned."""

    def __init__(self, *, threshold: int = THRESHOLD, max_depth: int = MAX_DEPTH,
                 max_args_chars: int = MAX_ARGS_CHARS, max_name_chars: int = MAX_NAME_CHARS,
                 max_indexes: int = MAX_INDEXES) -> None:
        if threshold < 2:
            raise ValueError("threshold must be at least 2")
        self.threshold, self.max_depth = threshold, max_depth
        self.max_args_chars, self.max_name_chars, self.max_indexes = max_args_chars, max_name_chars, max_indexes
        self._indexes: dict[int, _Index] = {}
        self._counts: dict[bytes, int] = {}
        self.cut: RepetitionCut | None = None
        self.parses = 0               # full parses made (one per index at most): a test's handle on the work bound

    def observe(self, index: Any, name: Any, fragment: Any, arguments: Callable[[], str]) -> RepetitionCut | None:
        """One fragment for the call at `index`: `name` is its whole name so far, `fragment` the argument text this
        fragment added ("" for none), `arguments` returns its whole argument text (called at most once per index,
        when its object closes). Returns the cut when this fragment completes the `threshold`-th equal call."""
        if self.cut is not None:
            return self.cut
        if type(index) is not int or index < 0:
            return None
        state = self._indexes.get(index)
        if state is None:
            if len(self._indexes) >= self.max_indexes:
                return None
            state = self._indexes[index] = _Index()
        if state.dead:
            return None
        if not isinstance(name, str) or not isinstance(fragment, str) or len(name) > self.max_name_chars:
            return self._kill(state)
        renamed = name != state.name
        state.name = name
        closed_now = False
        if fragment:
            state.chars += len(fragment)
            if state.chars > self.max_args_chars:
                return self._kill(state)
            was_closed = state.closed
            self._scan(state, fragment)
            if state.dead:
                return self._kill(state)
            closed_now = state.closed and not was_closed
        if closed_now:
            self.parses += 1
            try:
                text = arguments()
            except Exception:
                text = None
            state.args = canonical_arguments_digest(text, max_depth=self.max_depth) if text is not None else None
            if state.args is None:
                return self._kill(state)
        if not (closed_now or renamed) or state.args is None:
            return None
        return self._resign(state, _signature(state.name, state.args) if state.name else None)

    def _scan(self, state: _Index, fragment: str) -> None:
        """Advance the index's bracket/string state over `fragment`. It only decides WHEN to parse (the top-level
        object closed) and what can never be parsed (not an object, too deep, text after the close); the strict
        parse decides validity."""
        i, n = 0, len(fragment)
        while i < n:
            if state.closed:
                if fragment[i:].strip(_WHITESPACE):
                    state.dead = True
                return
            if not state.started:
                ch = fragment[i]
                i += 1
                if ch in _WHITESPACE:
                    continue
                if ch != "{":
                    state.dead = True
                    return
                state.started, state.depth = True, 1
                continue
            if state.in_string:
                if state.escape:
                    state.escape = False
                    i += 1
                    continue
                stop = _STRING_STOP.search(fragment, i)
                if stop is None:
                    return
                i = stop.end()
                if stop.group() == "\\":
                    state.escape = True
                else:
                    state.in_string = False
                continue
            mark = _STRUCTURE.search(fragment, i)
            if mark is None:
                return
            i = mark.end()
            ch = mark.group()
            if ch == '"':
                state.in_string = True
            elif ch in "{[":
                state.depth += 1
                if state.depth > self.max_depth:
                    state.dead = True
                    return
            else:
                state.depth -= 1
                if state.depth == 0:
                    state.closed = True

    def _kill(self, state: _Index) -> None:
        """This index has no identity from here on, whatever arrives for it."""
        state.dead, state.args = True, None
        self._resign(state, None)
        return None

    def _resign(self, state: _Index, signature: bytes | None) -> RepetitionCut | None:
        old = state.signature
        if old == signature:
            return None
        if old is not None:
            left = self._counts.get(old, 0) - 1
            if left > 0:
                self._counts[old] = left
            else:
                self._counts.pop(old, None)
        state.signature = signature
        if signature is None:
            return None
        count = self._counts[signature] = self._counts.get(signature, 0) + 1
        if count < self.threshold:
            return None
        repeated = tuple(sorted(i for i, s in self._indexes.items() if s.signature == signature))
        kept, seen = [], set()
        complete = 0
        for i in sorted(self._indexes):
            current = self._indexes[i].signature
            if current is None:
                continue
            complete += 1
            if current not in seen:
                seen.add(current)
                kept.append(i)
        self.cut = RepetitionCut(tool=state.name, occurrences=len(repeated), repeated=repeated, kept=tuple(kept),
                                 duplicates=complete - len(kept))
        return self.cut
