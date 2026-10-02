"""When a Sleepwalk trigger fires (DREAM-158), in the system's local time zone with its DST rules.

A slot is a day's wall-clock time as an aware datetime. On a spring-forward day a time the clock skips fires at the
same instant an hour later on the clock; on a fall-back day a repeated time fires once, at its first occurrence.
Every comparison is made in UTC: Python compares two times of the same ZoneInfo by the wall clock alone."""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GRACE = timedelta(minutes=10)      # a slot older than this when first seen was missed (Dream closed or asleep)


def zone():
    try:
        name = os.environ.get("TZ", "").removeprefix(":")
        if name and not name.startswith("/"):
            return ZoneInfo(name)
        with open(name or "/etc/localtime", "rb") as source:   # TZ may name a zone file by path
            return ZoneInfo.from_file(source)
    except (OSError, ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def now() -> datetime:
    return datetime.now(zone())


def _times(trigger: dict, day: date) -> list[time]:
    """The day's wall-clock times. "Every n hours" is one series anchored at `at` on its `from` day (set when saved)
    that runs on across midnight."""
    hour, minute = map(int, trigger["at"].split(":"))
    if trigger["every"] != "hours":
        return [time(hour, minute)]
    step, start = trigger["n"] * 60, date.fromisoformat(trigger.get("from", "2000-01-01"))
    since = (datetime.combine(day, time()) - datetime.combine(start, time(hour, minute))) // timedelta(minutes=1)
    first = -since % step                          # minutes after midnight of the day's first slot
    earliest = hour * 60 + minute if day == start else 0 if day > start else 24 * 60   # nothing before `at` on `from`
    return [time(m // 60, m % 60) for m in range(first, 24 * 60, step) if m >= earliest]


def _fires_on(trigger: dict, day: date) -> bool:
    every = trigger["every"]
    if every == "weekdays":
        return day.weekday() < 5
    if every == "week":
        return day.weekday() in trigger["days"]
    if every == "month":
        return day.day == min(trigger["day"], calendar.monthrange(day.year, day.month)[1])
    return True


def _slots(trigger: dict, moment: datetime, step: int):
    for offset in range(0, 63 * step, step):
        day = moment.date() + timedelta(days=offset)
        if _fires_on(trigger, day):
            times = _times(trigger, day)
            for at in reversed(times) if step < 0 else times:
                yield datetime.combine(day, at, tzinfo=moment.tzinfo).astimezone(timezone.utc)


def latest_slot(trigger: dict, moment: datetime) -> datetime | None:
    utc = moment.astimezone(timezone.utc)
    found = next((s for s in _slots(trigger, moment, -1) if s <= utc), None)
    return found and found.astimezone(moment.tzinfo)


def next_slot(trigger: dict, moment: datetime) -> datetime | None:
    utc = moment.astimezone(timezone.utc)
    found = next((s for s in _slots(trigger, moment, 1) if s > utc), None)
    return found and found.astimezone(moment.tzinfo)


def _parse(stamp, tz) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=tz)).astimezone(timezone.utc)


def due(automation: dict, moment: datetime, grace: timedelta = GRACE) -> tuple[datetime, dict, bool] | None:
    """The newest slot not yet handled (after `last_slot`, or `created` for an automation never scheduled), its
    trigger, and whether it was missed. Only the newest counts, so a long absence gives one catch-up at most."""
    found = [(s, t) for t in automation["triggers"] if (s := latest_slot(t, moment)) is not None]
    if not found:
        return None
    slot, trigger = max(found, key=lambda pair: pair[0].astimezone(timezone.utc))
    # last_slot is honoured as written, so a clock stepped back (up to a week) never replays a slot; only one
    # implausibly far ahead (over a week: written while the clock was wrong) is clamped to now less the on-time
    # window, so the next slot still fires.
    utc = moment.astimezone(timezone.utc)
    handled = _parse(automation.get("last_slot") or automation.get("created"), moment.tzinfo)
    handled = utc - grace if handled is not None and handled > utc + timedelta(days=7) else handled
    if handled is not None and slot.astimezone(timezone.utc) <= handled:
        return None
    return slot, trigger, moment.astimezone(timezone.utc) - slot.astimezone(timezone.utc) > grace


def next_run(automation: dict, moment: datetime) -> str | None:
    slots = [s for t in automation["triggers"] if (s := next_slot(t, moment)) is not None]
    return min(slots, key=lambda s: s.astimezone(timezone.utc)).isoformat() if slots else None
