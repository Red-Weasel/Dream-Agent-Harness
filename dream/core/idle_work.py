"""Bounded optional work that yields completely before foreground requests.

Factories must be cancellable async work. Cancellation is joined, with no timeout
that could leave work overlapping a foreground request. Interrupted jobs are never
replayed because they may already have written partial results. This helper owns
only asyncio tasks, never provider processes or threads.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import Context, copy_context
from dataclasses import dataclass
import logging

_LOG = logging.getLogger(__name__)


@dataclass
class _Job:
    factory: Callable[[], Awaitable[None]]
    label: str
    context: Context


class IdleWorkQueue:
    """One-loop queue. Event callbacks are synchronous metadata observers."""

    def __init__(self, *, max_pending: int = 4, idle_grace: float = 0.25,
                 on_event: Callable[[dict], None] | None = None):
        self.max_pending = max_pending
        self.idle_grace = idle_grace
        self.on_event = on_event
        self._pending: deque[_Job] = deque()
        self._worker: asyncio.Task | None = None
        self._running: str | None = None
        self._foreground = False
        self._closed = False
        self._counts = dict(completed=0, interrupted=0, dropped=0, failed=0)

    def snapshot(self) -> dict:
        return dict(self._counts, queued=len(self._pending), running=self._running,
                    foreground=self._foreground, closed=self._closed)

    def _event(self, kind: str, label: str, **metadata) -> None:
        if self.on_event is not None:
            try:
                self.on_event(dict(self.snapshot(), kind=kind, label=label, **metadata))
            except Exception:
                _LOG.exception('Idle work event observer failed')

    def enqueue(self, factory: Callable[[], Awaitable[None]], *, label: str) -> bool:
        """Retain the factory and its context; drop newest when full or closed."""
        if self._closed or len(self._pending) >= self.max_pending:
            self._counts['dropped'] += 1
            self._event('dropped', label, reason='closed' if self._closed else 'full')
            return False
        self._pending.append(_Job(factory, label, copy_context()))
        self._event('queued', label)
        self._start_worker()
        return True

    def _start_worker(self) -> None:
        if self._worker is None and self._pending and not self._foreground and not self._closed:
            self._worker = asyncio.create_task(self._run(), name='dream-idle-work')

    async def foreground_started(self, *, cancel_running: bool = True) -> None:
        """Cancel and join our timer/running job before foreground I/O starts."""
        self._foreground = True
        if not cancel_running and self._running is not None and self._worker is not None:
            try:
                await self._worker
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            return
        await self._stop_worker()

    def foreground_finished(self) -> None:
        """Resume unstarted work after the idle grace, unless closed."""
        self._foreground = False
        self._start_worker()

    async def _stop_worker(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                # Propagate cancellation of the caller itself, not just the worker.
                if asyncio.current_task().cancelling():
                    raise
            finally:
                # A task cancelled before its first instruction cannot run finally.
                if self._worker is worker and worker.done():
                    self._worker = None

    async def close(self) -> None:
        """Permanently discard pending jobs and join all owned async work."""
        self._closed = True
        while self._pending:
            job = self._pending.popleft()
            self._counts['dropped'] += 1
            self._event('dropped', job.label, reason='closed')
        await self._stop_worker()

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self.idle_grace)
            while self._pending and not self._foreground and not self._closed:
                job = self._pending.popleft()
                self._running = job.label
                self._event('running', job.label)

                async def execute():
                    # Call the factory inside its captured context as well as await it.
                    await job.factory()

                task = asyncio.create_task(execute(), context=job.context,
                                           name='dream-idle-job')
                try:
                    await task
                    if asyncio.current_task().cancelling():
                        # A job may suppress cancellation while finishing cleanup.
                        self._counts['interrupted'] += 1
                        self._event('interrupted', job.label)
                        return
                    self._counts['completed'] += 1
                    self._event('completed', job.label)
                except asyncio.CancelledError:
                    self._counts['interrupted'] += 1
                    self._event('interrupted', job.label)
                    raise
                except Exception as exc:
                    self._counts['failed'] += 1
                    self._event('failed', job.label, error_type=type(exc).__name__)
                finally:
                    self._running = None
        finally:
            if self._worker is asyncio.current_task():
                self._worker = None
                self._start_worker()
