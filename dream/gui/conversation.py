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
             'turn_timing', 'background_work', 'agent_activity', 'council_activity', 'plan'}

    def __init__(self, max_chars=2_000_000, max_events=2000):
        self.events = deque()
        self.max_chars, self.max_events = max_chars, max_events
        self.chars = 0
        self.trimmed = False
        self._lock = threading.Lock()
        self._open = {}     # run_id -> that worker's open delta row (DREAM-190)

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
            elif kind == 'agent_activity' and (row := self._open_row(data)) is not None:
                # A worker's streamed piece joins its run's open row, the same request's same kind of delta, wherever
                # other runs' rows or the lead's events came since (DREAM-190; gate P3 round 1: joining only the last
                # event merged nothing once runs interleave). Never the lead's text, never another run's. It counts
                # by its text: that is what the record grew by.
                row['data']['text'] = row['data'].get('text', '') + data.get('text', '')
                size = len(data.get('text', ''))
            else:
                self.events.append({'kind': kind, 'data': data})
                if kind == 'agent_activity':
                    # A run's new delta row is its open row; any other row of that run closes it.
                    if data.get('kind') in {'text_delta', 'thinking_delta'}:
                        self._open[data.get('run_id')] = self.events[-1]
                    else:
                        self._open.pop(data.get('run_id'), None)
            self.chars += size
            while self.events and (self.chars > self.max_chars or len(self.events) > self.max_events):
                gone = self.events.popleft()
                self.chars -= len(str(gone['data']))
                self.trimmed = True
                if gone['kind'] == 'agent_activity' and self._open.get(gone['data'].get('run_id')) is gone:
                    del self._open[gone['data']['run_id']]

    def _open_row(self, data):
        """The run's open delta row, when this worker delta carries it on: the same request, the same kind."""
        if data.get('kind') not in {'text_delta', 'thinking_delta'}:
            return None
        row = self._open.get(data.get('run_id'))
        if row is None or row['data'].get('kind') != data['kind'] \
                or row['data'].get('request_index') != data.get('request_index'):
            return None
        return row

    def snapshot(self):
        with self._lock:
            return {'events': deepcopy(list(self.events)), 'trimmed': self.trimmed}

    def clear(self):
        with self._lock:
            self.events.clear()
            self._open.clear()
            self.chars = 0
            self.trimmed = False
