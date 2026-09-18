"""Owned fixture processes only; never invoke a signed-in CLI or a live model."""
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import time

import pytest

from dream.core.backends import cli_agent
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter


def backend_for(tmp_path, body, *, timeout=2, **kwargs):
    script = tmp_path / 'fixture-cli'
    script.write_text('#!/usr/bin/python3\n' + body)
    script.chmod(0o700)
    adapter = CodexAdapter()
    adapter.cmd = str(script)
    return CliAgentBackend(adapter, system_prompt='FIXTURE SYSTEM', cwd=str(tmp_path),
                           idle_timeout=timeout, **kwargs)


async def collect(backend):
    return [event async for event in backend.ask('fixture request')]


def tree_fixture(tmp_path, complete=False):
    pids = tmp_path / 'pids.json'
    leaf = tmp_path / 'leaf.pid'
    code = f'''
import os, signal, time, json
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = os.fork()
if child == 0:
    os.setsid()
    if os.fork() != 0:
        os._exit(0)
    Path({str(leaf)!r}).write_text(str(os.getpid()))
    while True: time.sleep(.02)
os.waitpid(child, 0)
while not Path({str(leaf)!r}).exists(): time.sleep(.001)
Path({str(pids)!r}).write_text(json.dumps([os.getpid(), int(Path({str(leaf)!r}).read_text())]))
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'fixture alive'}}}}), flush=True)
if {complete!r}:
    print(json.dumps({{'type':'turn.completed','usage':{{}}}}), flush=True)
else:
    while True: time.sleep(.02)
'''
    return code, pids


async def wait_path(path):
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(.005)


@pytest.mark.parametrize('stop', ['success', 'close_iterator', 'cancel', 'interrupt', 'disconnect', 'idle'])
async def test_owned_tree_cleanup_including_double_fork_does_not_kill_sibling(tmp_path, stop):
    code, pids = tree_fixture(tmp_path, stop == 'success')
    if stop == 'idle':
        # This case measures idle cleanup after the tree exists, not interpreter
        # startup speed. Deliberately outlast its idle interval during setup.
        code = 'import time\ntime.sleep(.4)\n' + code
    backend = backend_for(tmp_path, code, timeout=.25 if stop == 'idle' else 2)
    sibling = await asyncio.create_subprocess_exec('/usr/bin/python3', '-c', 'import time; time.sleep(30)', start_new_session=True)
    gen = None
    task = None
    try:
        async with asyncio.timeout(5):
            if stop == 'close_iterator':
                gen = backend.ask('fixture request')
                assert (await gen.__anext__()).kind == 'assistant_done'
                await gen.aclose()
            else:
                with backend.pause_idle_timeout() if stop == 'idle' else nullcontext():
                    task = asyncio.create_task(collect(backend))
                    await wait_path(pids)
                if stop == 'cancel':
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif stop in ('interrupt', 'disconnect'):
                    await getattr(backend, stop)()
                    await task
                else:
                    events = await task
                    if stop == 'idle':
                        assert events[-1].kind == 'error' and 'idle timeout' in events[-1].data
                    else:
                        assert [event.kind for event in events] == ['assistant_done', 'result']
            assert all(not Path(f'/proc/{pid}').exists() for pid in json.loads(pids.read_text()))
            assert sibling.returncode is None and Path(f'/proc/{sibling.pid}').exists()
            assert backend._proc is None
    finally:
        if gen:
            await gen.aclose()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await backend.disconnect()
        sibling.terminate()
        await sibling.wait()


async def test_cancel_during_spawn_does_not_lose_created_child(tmp_path, monkeypatch):
    code, pids = tree_fixture(tmp_path)
    backend = backend_for(tmp_path, code)
    original = asyncio.create_subprocess_exec
    spawned, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args, **kwargs):
        proc = await original(*args, **kwargs)
        spawned.set()
        await release.wait()
        return proc
    monkeypatch.setattr(cli_agent.asyncio, 'create_subprocess_exec', delayed)
    task = asyncio.create_task(collect(backend))
    try:
        async with asyncio.timeout(5):
            await spawned.wait()
            await wait_path(pids)
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert all(not Path(f'/proc/{pid}').exists() for pid in json.loads(pids.read_text()))
            assert backend._spawning is None and backend._proc is None
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
async def test_partial_stdout_and_stderr_count_as_idle_activity(tmp_path, stream):
    code = f'''
import sys, time, json
message = json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'complete'}}}})
if {stream!r} == 'stdout':
    for part in [message[i:i+10] for i in range(0,len(message),10)]:
        sys.stdout.write(part); sys.stdout.flush(); time.sleep(.03)
    print('', flush=True)
else:
    for _ in range(10):
        sys.stderr.write('.'); sys.stderr.flush(); time.sleep(.03)
    print(message, flush=True)
print(json.dumps({{'type':'turn.completed'}}), flush=True)
'''
    events = await asyncio.wait_for(collect(backend_for(tmp_path, code, timeout=.15)), 3)
    assert [event.kind for event in events] == ['assistant_done', 'result']


@pytest.mark.parametrize('use_getter', [False, True])
async def test_human_approval_pauses_idle_then_restores_full_interval(tmp_path, use_getter):
    ready = tmp_path / 'ready'
    code = f'import time\nfrom pathlib import Path\nPath({str(ready)!r}).touch()\ntime.sleep(30)\n'
    paused = True
    backend = backend_for(tmp_path, code, timeout=.15,
                          idle_pause_getter=(lambda: paused) if use_getter else None)
    pause = backend.pause_idle_timeout()
    if not use_getter:
        pause.__enter__()
    task = asyncio.create_task(collect(backend))
    try:
        await wait_path(ready)
        await asyncio.sleep(.4)
        assert not task.done()
        resumed = time.monotonic()
        paused = False
        if not use_getter:
            pause.__exit__(None, None, None)
        events = await asyncio.wait_for(task, 3)
        assert time.monotonic() - resumed >= .12
        assert events[-1].kind == 'error' and 'idle timeout' in events[-1].data
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.disconnect()


def test_idle_timeout_config_is_explicit_bounded_and_backward_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_CLI_IDLE_TIMEOUT', '600')
    assert CliAgentBackend(CodexAdapter(), system_prompt='s', cwd=str(tmp_path)).idle_timeout == 600
    assert CliAgentBackend(CodexAdapter(), system_prompt='s', cwd=str(tmp_path), idle_timeout=300).idle_timeout == 300
    for value in (0, -1, float('inf'), float('nan'), 86401):
        with pytest.raises(ValueError):
            CliAgentBackend(CodexAdapter(), system_prompt='s', cwd=str(tmp_path), idle_timeout=value)
