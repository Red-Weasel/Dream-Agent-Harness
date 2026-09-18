"""Bounded transcript for reconnecting views of the active session.

Only display events are retained. Replaying this record never executes a tool,
answers a permission request, or runs script in an artifact frame.
"""
from collections import deque
from copy import deepcopy
import threading


class Conversation:
    KINDS = {'user', 'text_delta', 'thinking_delta', 'assistant_done',
             'tool_use', 'tool_result', 'system', 'error', 'result', 'turn_start', 'turn_end',
             'turn_timing', 'background_work', 'agent_activity', 'council_activity'}

    def __init__(self, max_chars=2_000_000, max_events=2000):
        self.events = deque()
        self.max_chars, self.max_events = max_chars, max_events
        self.chars = 0
        self.trimmed = False
        self._lock = threading.Lock()

    def append(self, event):
        kind = getattr(event, 'kind', '')
        if kind not in self.KINDS:
            return
        if kind == 'agent_activity':
            from ..agent_activity import bounded_agent_activity
            try:
                data = bounded_agent_activity(getattr(event, 'data', None))
            except Exception:
                return
            if data is None:
                return
        else:
            data = None
        try:
            if kind != 'agent_activity':
                data = deepcopy(getattr(event, 'data', None))
        except Exception:
            # Resource-backed payloads can still be streamed; retain a display
            # representation without allowing transcript bookkeeping to abort a turn.
            try:
                data = repr(getattr(event, 'data', None))
            except Exception:
                data = '[Display payload could not be retained]'
        if kind == 'tool_result' and isinstance(data, dict):
            content = str(data.get('content', ''))
            if len(content) > 20000:
                data['content'] = content[:20000] + '\n… Display truncated; full result remains with the agent.'
        size = len(str(data))
        with self._lock:
            if kind in {'text_delta', 'thinking_delta'} and self.events and self.events[-1]['kind'] == kind:
                self.events[-1]['data'] += str(data or '')
            else:
                self.events.append({'kind': kind, 'data': data})
            self.chars += size
            while self.events and (self.chars > self.max_chars or len(self.events) > self.max_events):
                self.chars -= len(str(self.events.popleft()['data']))
                self.trimmed = True

    def snapshot(self):
        with self._lock:
            return {'events': deepcopy(list(self.events)), 'trimmed': self.trimmed}

    def clear(self):
        with self._lock:
            self.events.clear()
            self.chars = 0
            self.trimmed = False
