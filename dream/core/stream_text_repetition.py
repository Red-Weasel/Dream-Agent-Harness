"""Exact consecutive long-line repetition in text/reasoning, not tool arguments.

This is a narrow heuristic, not proof of lost progress. Fenced/indented code,
quotes, short lines and changed lines do not count. There are at most two bounded
line buffers and two digests; output text is not retained in a cut. Newlines are
semantic boundaries, so arbitrary SSE fragment boundaries cannot change a cut.
No-newline or differently worded loops are outside this detector.
"""
from dataclasses import dataclass
import hashlib
import re

_RAW_TOOL_TAG = re.compile(r'</?(invoke|function|tool_calls?)(?=[=\s>])')
_MAX_FENCE_WIDTH = 1_000_000


def _xml_tag(state, tag):
    closing = tag.group(0).startswith('</')
    if state.raw_tool is None and not closing:
        state.raw_tool = tag.group(1)
    elif closing and tag.group(1) == state.raw_tool:
        suffix = tag.string[tag.end():]
        if re.match(r'\s*>', suffix):
            state.raw_tool = None
            state.pending_close = False
        elif not suffix.strip():
            state.pending_close = True


def _xml_close_continuation(state, character):
    if state.pending_close and not character.isspace():
        if character == '>':
            state.raw_tool = None
        state.pending_close = False


class _WideLine:
    """Retain framing after dropping a large body, never the argument/code text."""
    __slots__ = ('mode', 'marker', 'width', 'ended', 'valid', 'tail')

    def __init__(self, state, prefix):
        text = prefix.strip()
        self.marker = text[:1]
        self.width = len(text) - len(text.lstrip(self.marker)) if self.marker in {'`', '~'} else 0
        suffix = prefix.lstrip()[self.width:]
        self.ended, self.valid, self.tail = bool(suffix), not suffix.strip(), ''
        indented = prefix.startswith(('    ', '\t'))
        if state.fence:
            self.mode = 'close' if not indented and self.marker == state.fence[0] else 'ignore'
        elif not state.raw_tool and not indented and not text.startswith('>') and self.width >= 3:
            self.mode = 'open'
        elif state.raw_tool or (not indented and not text.startswith('>')):
            self.mode = 'xml'
            for character in prefix:
                _xml_close_continuation(state, character)
                if not state.pending_close:
                    break
            for tag in _RAW_TOOL_TAG.finditer(prefix):
                _xml_tag(state, tag)
            # A tag name can straddle the body limit. The longest recognized
            # name plus its opening/closing syntax fits this fixed window.
            self.tail = prefix[-16:]
        else:
            self.mode = 'ignore'

    def take(self, state, character):
        if self.mode in ('open', 'close'):
            if not self.ended and character == self.marker:
                self.width = min(_MAX_FENCE_WIDTH + 1, self.width + 1)
            else:
                self.ended = True
                if not character.isspace():
                    self.valid = False
        elif self.mode == 'xml':
            _xml_close_continuation(state, character)
            self.tail = (self.tail + character)[-16:]
            for tag in _RAW_TOOL_TAG.finditer(self.tail):
                if tag.end() == len(self.tail) - 1:
                    _xml_tag(state, tag)

    def finish(self, state):
        if self.mode == 'open':
            # An excessively wide marker cannot be matched truthfully after
            # saturation. Conservatively keep its code exclusion until EOF.
            state.fence = (self.marker, self.width if self.width <= _MAX_FENCE_WIDTH else None)
        elif (self.mode == 'close' and self.valid and state.fence[1] is not None
              and self.width >= state.fence[1]):
            state.fence = None


@dataclass(frozen=True)
class TextRepetitionCut:
    channel: str
    occurrences: int
    line_sha256: str
    line_bytes: int


class _Line:
    __slots__ = ('buffer', 'discard', 'wide', 'fence', 'raw_tool', 'pending_close',
                 'signature', 'occurrences', 'blanks')

    def __init__(self):
        self.buffer = []
        self.discard = False
        self.wide = None
        self.fence = None
        self.raw_tool = None
        self.pending_close = False
        self.signature = None
        self.occurrences = self.blanks = 0

    def reset(self):
        self.signature = None
        self.occurrences = self.blanks = 0


class TextRepetitionDetector:
    def __init__(self, *, threshold=6, min_chars=48, max_chars=4096):
        if (type(threshold) is not int or not 3 <= threshold <= 16
                or type(min_chars) is not int or not 32 <= min_chars <= 4096
                or type(max_chars) is not int or not min_chars <= max_chars <= 4096):
            raise ValueError('Strict bounded text repetition settings required')
        self.threshold = threshold
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.cut = None
        self._channels = {name: _Line() for name in ('content', 'reasoning_content')}

    @property
    def retained_chars(self):
        return sum(len(state.buffer) + (len(state.wide.tail) if state.wide else 0)
                   for state in self._channels.values())

    def observe(self, channel, fragment):
        if self.cut is not None:
            return self.cut
        if type(channel) is not str or channel not in self._channels or not isinstance(fragment, str):
            return None
        state = self._channels[channel]
        for character in fragment:
            if character == '\n':
                if state.discard:
                    state.wide.take(state, character)
                    state.wide.finish(state)
                else:
                    self._line(channel, state, ''.join(state.buffer))
                state.buffer.clear()
                state.discard = False
                state.wide = None
                if self.cut is not None:
                    return self.cut
            elif not state.discard:
                if len(state.buffer) == self.max_chars:
                    state.wide = _WideLine(state, ''.join(state.buffer))
                    state.buffer.clear()
                    state.discard = True
                    state.reset()
                    state.wide.take(state, character)
                else:
                    state.buffer.append(character)
            else:
                state.wide.take(state, character)
        return self.cut

    def _line(self, channel, state, raw):
        text = raw.strip()
        marker = text[:1]
        width = len(text) - len(text.lstrip(marker)) if marker in {'`', '~'} else 0
        indented = raw.startswith(('    ', '\t'))
        if state.fence:
            if (not indented and marker == state.fence[0] and state.fence[1] is not None
                    and width >= state.fence[1] and not text[width:]):
                state.fence = None
            state.reset()
            return
        # An info string may contain XML-looking source text. Recognize an
        # actual code fence first, but never open one inside a tool argument.
        if not state.raw_tool and not indented and not text.startswith('>') and width >= 3:
            state.fence = (marker, width)
            state.reset()
            return
        # The newline is a real XML whitespace boundary, even when the next
        # line contains the attributes or closing bracket. Do not strip it.
        in_tool = bool(state.raw_tool)
        for character in raw:
            _xml_close_continuation(state, character)
            if not state.pending_close:
                break
        tags = list(_RAW_TOOL_TAG.finditer(raw + '\n'))
        if in_tool or state.raw_tool or (tags and not indented and not text.startswith('>')):
            # Compatibility tool arguments can also arrive as raw XML content.
            # Treat their wrapper as an exclusion, never as tool authorization.
            # Matching the first wrapper's close follows the fallback parser's
            # non-nested framing; retained state is only a bounded tag name.
            for tag in tags:
                _xml_tag(state, tag)
            state.reset()
            return
        if indented or text.startswith('>'):
            state.reset()
            return
        if not text:
            state.blanks += 1
            if state.blanks > 2:
                state.reset()
            return
        state.blanks = 0
        if len(text) < self.min_chars:
            state.reset()
            return
        encoded = text.encode('utf-8', 'surrogatepass')
        signature = hashlib.sha256(encoded).hexdigest()
        state.occurrences = state.occurrences + 1 if signature == state.signature else 1
        state.signature = signature
        if state.occurrences >= self.threshold:
            self.cut = TextRepetitionCut(channel, state.occurrences, signature, len(encoded))
