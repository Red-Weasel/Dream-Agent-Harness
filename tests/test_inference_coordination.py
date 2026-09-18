"""CPU-only cross-process leases. No endpoint or GPU is contacted."""
import asyncio
import multiprocessing
import os
from pathlib import Path

import pytest

from dream.core.inference_coordination import EndpointCoordinator, CoordinationError, local_endpoint


def test_local_aliases_share_resource_without_credentials():
    assert local_endpoint('http://user:secret@localhost:11435/v1') == local_endpoint('http://127.0.0.1:11435/other')
    assert local_endpoint('http://[::1]:11435/v1') == local_endpoint('http://0.0.0.0:11435/v1')
    assert local_endpoint('https://api.example.test/v1') is None
    assert 'secret' not in local_endpoint('http://user:secret@localhost:11435/v1')


async def test_waiting_cancellation_does_not_change_owner(tmp_path):
    a = EndpointCoordinator('loopback:11435', root=tmp_path / 'leases')
    b = EndpointCoordinator('loopback:11435', root=tmp_path / 'leases')
    started = asyncio.Event()
    async with a.request():
        owner = a.status()['request_id']
        async def waiting():
            started.set()
            async with b.request():
                pytest.fail('overlap')
        task = asyncio.create_task(waiting())
        await started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert a.status()['request_id'] == owner
        assert a.status()['state'] == 'running'
    assert a.status()['state'] == 'idle'


async def test_interrupted_operation_requires_exact_reconciliation(tmp_path):
    a = EndpointCoordinator('loopback:11435', root=tmp_path / 'leases')
    with pytest.raises(asyncio.CancelledError):
        async with a.request():
            raise asyncio.CancelledError()
    record = a.status()
    assert record['state'] == 'uncertain'
    with pytest.raises(CoordinationError, match='uncertain'):
        async with a.request():
            pytest.fail('replayed')
    with pytest.raises(CoordinationError):
        a.reconcile('wrong', confirmed_idle=True)
    with pytest.raises(CoordinationError):
        a.reconcile(record['request_id'], confirmed_idle=False)
    a.reconcile(record['request_id'], confirmed_idle=True)
    async with a.request():
        pass
    assert a.status()['state'] == 'idle'


def _hold(root, ready, release):
    async def operation():
        async with EndpointCoordinator('loopback:11435', root=Path(root)).request():
            ready.set()
            while not release.is_set():
                await asyncio.sleep(.01)
    asyncio.run(operation())


async def test_separate_processes_cannot_overlap(tmp_path):
    context = multiprocessing.get_context('spawn')
    ready, release = context.Event(), context.Event()
    proc = context.Process(target=_hold, args=(str(tmp_path / 'leases'), ready, release))
    proc.start()
    try:
        for _ in range(200):
            if ready.is_set():
                break
            await asyncio.sleep(.01)
        assert ready.is_set()
        contender = EndpointCoordinator('loopback:11435', root=tmp_path / 'leases')
        with pytest.raises(CoordinationError, match='waiting'):
            async with contender.request(timeout=.03):
                pytest.fail('overlap')
        release.set()
        for _ in range(200):
            if not proc.is_alive():
                break
            await asyncio.sleep(.01)
        proc.join(timeout=1)
        assert proc.exitcode == 0
        async with contender.request():
            pass
    finally:
        release.set()
        proc.join(timeout=3)


def test_unsafe_state_and_symlinks_fail_closed(tmp_path):
    root = tmp_path / 'leases'
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(CoordinationError):
        EndpointCoordinator('loopback:11435', root=root).status()


def _crash(root):
    async def operation():
        async with EndpointCoordinator('loopback:19435', root=Path(root)).request():
            os._exit(0)
    asyncio.run(operation())


async def test_crashed_owner_leaves_a_fence_without_replaying(tmp_path):
    root = tmp_path / 'leases'
    process = multiprocessing.get_context('spawn').Process(target=_crash, args=(str(root),))
    process.start()
    await asyncio.to_thread(process.join, 3)
    assert process.exitcode == 0
    coordinator = EndpointCoordinator('loopback:19435', root=root)
    assert coordinator.status()['state'] == 'running'
    with pytest.raises(CoordinationError, match='uncertain'):
        async with coordinator.request():
            pytest.fail('replayed after crash')
    coordinator.reconcile(coordinator.status()['request_id'], confirmed_idle=True)
    assert coordinator.status()['state'] == 'idle'


async def test_symlink_lock_reports_actionable_coordination_failure(tmp_path):
    root = tmp_path / 'leases'
    root.mkdir(mode=0o700)
    coordinator = EndpointCoordinator('loopback:19435', root=root)
    target = tmp_path / 'untouched'
    target.write_text('safe')
    (root / (coordinator.key + '.lock')).symlink_to(target)
    with pytest.raises(CoordinationError, match='safely'):
        async with coordinator.request():
            pytest.fail('unsafe lock')
    assert target.read_text() == 'safe'
