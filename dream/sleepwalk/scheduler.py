"""Sleepwalk's schedule while Dream is open (DREAM-158): a tick every 30 s inside the Dream server.

Only the process holding the scheduler lock fires runs, and each slot is claimed in the store before its run
starts, so two Dream windows or servers never run the same slot twice. A slot missed while Dream was closed
runs once or is recorded SKIPPED, as the automation's `missed` setting says; only the newest missed slot counts."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from . import runner, schedule, store

TICK = 30.0
log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, background: bool = False):
        # The background timer (DREAM-161) wakes every 15 minutes, so a slot up to 20 minutes old is on time for it.
        self.background, self.grace = background, timedelta(minutes=20 if background else 10)
        self._lock: int | None = None
        self._loop_task: asyncio.Task | None = None
        self.runs: set[asyncio.Task] = set()

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        tasks = [t for t in (self._loop_task, *self.runs) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:          # a bad tick must not stop the schedule
                log.warning("Sleepwalk tick failed: %s: %s", type(exc).__name__, exc)
            await asyncio.sleep(TICK)

    async def tick(self, now=None) -> list[asyncio.Task]:
        tasks = [asyncio.create_task(self._run(a, label)) for a, label in await asyncio.to_thread(self.due, now)]
        for task in tasks:
            self.runs.add(task)
            task.add_done_callback(self.runs.discard)
        return tasks

    def due(self, now=None) -> list[tuple[dict, str]]:
        """Claim every due slot and return the runs to start; missed slots set to skip are recorded here."""
        if not (store.ROOT / "automations.json").exists():
            return []                              # nothing to schedule: take no lock, create nothing
        if self._lock is None:
            self._lock = store.scheduler_lock()
            if self._lock is None:
                return []                          # another Dream process fires the runs
            for automation in store.load():        # scheduled runs the last firing process never finished
                for label in store.pending_of(automation):
                    runner.note(automation, label, "FAILED", "Interrupted: Dream closed during this run. It is not retried.")
        given, now = now, now or schedule.now()   # a claim re-checks at the real time unless a test clock is given
        start = []
        for automation in store.load():
            found = schedule.due(automation, now, self.grace) if automation["enabled"] else None
            if found is None:
                continue
            slot, trigger, missed = found
            label = f"Scheduled {slot:%a %H:%M}" + (f" ({trigger['label']})" if trigger.get("label") else "")
            skip = missed and automation.get("missed") == "skip"
            label += ", missed while Dream was closed" if missed and not skip else ""
            automation = store.claim(automation["id"], slot, self.grace, None if skip else label, given)
            if automation is None:                 # claimed elsewhere, turned off or edited since it was read
                continue
            if skip:
                runner.note(automation, label, "SKIPPED", "Missed while Dream was closed; this automation skips missed runs.")
            else:
                start.append((automation, label))
        return start

    async def _run(self, automation: dict, label: str) -> None:
        try:
            await runner.run(automation, label, background=self.background)
        except store.AlreadyRunning:
            runner.note(automation, label, "SKIPPED", "The previous run of this automation was still going.")
        except Exception as exc:                   # recorded with its real reason, which also clears `pending`
            log.warning("Sleepwalk run failed: %s: %s", type(exc).__name__, exc)
            try:
                runner.note(automation, label, "FAILED", f"{type(exc).__name__}: {exc}"[:500])
            except Exception as again:
                log.warning("Sleepwalk could not record the failure: %s", again)


async def run_due(now=None) -> int:
    """`dream sleepwalk run-due` (the background timer): one tick, then wait for its runs. It shares the scheduler
    lock with any open Dream, so while Dream is open it does nothing and Dream fires the runs."""
    from .background import logged_in
    if not await asyncio.to_thread(logged_in):  # logged-in only, even if linger keeps the user manager running
        print("sleepwalk run-due: no active login session; nothing runs")
        return 0
    scheduler = Scheduler(background=True)
    try:
        tasks = await scheduler.tick(now)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                log.warning("Sleepwalk background run failed: %s", result)
        held = scheduler._lock is None and (store.ROOT / "automations.json").exists()
        print("sleepwalk run-due: " + ("an open Dream fires the runs" if held else f"{len(tasks)} run(s)"))
        return 0
    finally:
        await scheduler.stop()
