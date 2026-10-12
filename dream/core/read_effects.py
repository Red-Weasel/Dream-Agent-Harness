"""Host observations of narrowly audited builtin read failures.

Not a model tool or a security boundary against trusted Python/same-user code.
Names, MCP annotations and returned fields confer no observation authority.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import inspect


class _ReadFailure:
    __slots__ = ('handler', 'action_id', 'reason')

    def __init__(self, handler, action_id):
        self.handler, self.action_id, self.reason = handler, action_id, None


_CURRENT = ContextVar('dream_builtin_read_failure', default=None)
_REASONS = frozenset({'invalid_path', 'missing_file', 'plain_text_lines'})


@contextmanager
def capture_read_failure(tool, action_id):
    from ..tools import native
    actual = inspect.unwrap(tool.handler)
    expected = inspect.unwrap(native.read_file.handler)
    observation = _ReadFailure(expected, action_id) if actual is expected else None
    token = _CURRENT.set(observation)
    try:
        yield observation
    finally:
        _CURRENT.reset(token)


def note_read_failure(handler, reason):
    """Called only inside audited builtin preflight/plain-text branches."""
    observation = _CURRENT.get()
    if (type(observation) is _ReadFailure and observation.handler is inspect.unwrap(handler)
            and reason in _REASONS):
        observation.reason = reason


def proven_read_failure(observation, action_id):
    if observation is None:
        return None
    if (type(observation) is not _ReadFailure or observation.action_id != action_id
            or observation.reason not in _REASONS):
        raise ValueError('Invalid host read-failure observation')
    return 'native.read_file:' + observation.reason
