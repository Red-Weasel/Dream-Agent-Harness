"""Repeated failures across argument churn are advice, never automatic replay."""
from types import SimpleNamespace

from dream.core.backends.openai_compat import _ExecutedToolResult
from test_loop_guard import _backend


def backend():
    tool = SimpleNamespace(name='run_bash', description='shell', input_schema={}, handler=None)
    return _backend(tool)


async def call(b, history, arg, text='missing shared library', failed=True):
    async def execute(*args, **kwargs):
        return _ExecutedToolResult(text), failed
    b._exec_tool = execute
    return await b._guarded_exec('run_bash', {'command': arg}, history)


async def test_failure_argument_churn_receives_recovery_after_three_results():
    b, history = backend(), {}
    for i in range(3):
        text, failed, stop = await call(b, history, str(i))
        assert failed and not stop
        assert isinstance(text, _ExecutedToolResult)
        assert ('[failure recovery]' in text) == (i == 2)
    assert 'bypass' in text and 'not proof' in text


async def test_changed_error_and_success_reset_failure_streak():
    b, history = backend(), {}
    await call(b, history, 'a')
    await call(b, history, 'b')
    await call(b, history, 'c', 'different failure')
    text, _, _ = await call(b, history, 'd')
    assert '[failure recovery]' not in text
    await call(b, history, 'e', failed=False)
    text, _, _ = await call(b, history, 'f')
    assert '[failure recovery]' not in text


async def test_successful_polling_never_receives_failure_guidance():
    b, history = backend(), {}
    for i in range(5):
        text, failed, stop = await call(b, history, str(i), 'still running', False)
        assert '[failure recovery]' not in text
        assert not failed and not stop


async def test_repairing_write_resets_failure_advice_without_resetting_mutation_guard():
    b, history = backend(), {}
    await call(b, history, 'a')
    await call(b, history, 'b')
    async def execute(*args, **kwargs):
        return _ExecutedToolResult('saved'), False
    b._exec_tool = execute
    await b._guarded_exec('write_file', {'path': 'fixture'}, history)
    text, _, _ = await call(b, history, 'c')
    assert '[failure recovery]' not in text
