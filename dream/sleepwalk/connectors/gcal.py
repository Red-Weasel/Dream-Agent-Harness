"""Google Calendar (DREAM-160): today's and tomorrow's events from the calendar's secret iCal address (read-only).

Recurring events are expanded with dateutil's RRULE support; EXDATEs and moved occurrences (RECURRENCE-ID) are
honoured. An event this reader cannot interpret is counted, not guessed."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..schedule import now as local_now

MAX_BYTES, DEADLINE, REPEATS = 5_000_000, 30, 1_000_000    # feed size, download seconds, repeat steps per day read
CPU, WALL, MEMORY = 20, 45, 768 * 1024 * 1024                # the reader process: CPU seconds, wall seconds, bytes
RULE_SECONDS, EXPAND_SECONDS = 0.2, 5                        # one repeat rule, all of them in one day's read
OUTPUT = 64 * 1024                                           # bytes of the reader's answer (UTF-8, so a quarter in characters)


class _Slow(Exception):
    pass


def _stop(*_):
    raise _Slow()

CONNECTOR = {"id": "gcal", "label": "Google Calendar", "kinds": ["context"], "fields": [
    {"name": "ical_url", "label": "Secret address in iCal format", "secret": True}]}


def _parse(text: str) -> list[dict]:
    events, current = [], None
    for line in re.sub(r"\r?\n[ \t]", "", text).splitlines():
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            current.setdefault(name.upper(), []).append((dict(p.split("=", 1) for p in params if "=" in p), value))
    return events


def _when(prop: tuple, tz) -> tuple[datetime, bool]:
    params, value = prop
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.combine(date(int(value[:4]), int(value[4:6]), int(value[6:8])), time(), tz), True
    moment = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    try:
        zone = timezone.utc if value.endswith("Z") else ZoneInfo(params["TZID"]) if "TZID" in params else tz
    except (ValueError, KeyError):
        zone = tz
    return moment.replace(tzinfo=zone), False


def events(text: str, start: datetime, end: datetime) -> tuple[list[tuple[datetime, bool, str]], int]:
    from dateutil.rrule import rrulestr
    tz, parsed, found, unread, budget, began = start.tzinfo, _parse(text), [], 0, REPEATS, monotonic()
    moved, bad = set(), set()
    for e in (e for e in parsed if "RECURRENCE-ID" in e):          # a moved occurrence Dream cannot place is counted
        try:
            moved.add((e["UID"][0][1], _when(e["RECURRENCE-ID"][0], tz)[0]))
        except Exception:
            bad.add(id(e))
    unread = len(bad)
    # A rule whose filters never match can spin inside dateutil without yielding, so in the reader process (main
    # thread) each rule also gets a wall-clock alarm, and the rules together EXPAND_SECONDS; over either, counted.
    timed = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGALRM, _stop) if timed else None
    for event in (e for e in parsed if id(e) not in bad):
        try:
            first, all_day = _when(event["DTSTART"][0], tz)
            length = _when(event["DTEND"][0], tz)[0] - first if "DTEND" in event else timedelta(days=all_day)
            uid, title = event.get("UID", [({}, "")])[0][1], event.get("SUMMARY", [({}, "(no title)")])[0][1]
            starts = [first]
            if "RRULE" in event and "RECURRENCE-ID" not in event:
                skip = {_when((p, v), tz)[0] for p, values in event.get("EXDATE", []) for v in values.split(",")}
                low, starts = start - length - timedelta(days=1), []
                if monotonic() - began > EXPAND_SECONDS:
                    raise OverflowError("out of time")
                signal.setitimer(signal.ITIMER_REAL, RULE_SECONDS) if timed else None
                try:
                    for s in rrulestr(event["RRULE"][0][1], dtstart=first):
                        budget -= 1
                        if budget < 0:
                            raise OverflowError("too many repeats")
                        if s > end:
                            break
                        if s >= low and s not in skip and (uid, s) not in moved:
                            starts.append(s)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0) if timed else None
            found += [(s, all_day, title.replace("\\,", ",")[:200]) for s in starts
                      if s < end and (s + length > start if length else s >= start)]
        except Exception:                                          # dateutil raises many kinds; count, keep going
            unread += 1
    signal.signal(signal.SIGALRM, previous) if timed else None
    return sorted(found, key=lambda e: (e[0].astimezone(timezone.utc), e[2])), unread


def _download(url: str) -> str:
    """At most MAX_BYTES as sent, within DEADLINE seconds in all; a compressed answer is refused, never unpacked."""
    deadline, body = monotonic() + DEADLINE, bytearray()
    with httpx.stream("GET", url, timeout=10, follow_redirects=True, headers={"Accept-Encoding": "identity"}) as response:
        if response.status_code != 200:
            raise RuntimeError(f"the calendar address answered HTTP {response.status_code}")
        if response.headers.get("content-encoding", "identity").lower() not in ("identity", ""):
            raise RuntimeError("the calendar was sent compressed, which is refused")
        for chunk in response.iter_raw():
            body += chunk
            if len(body) > MAX_BYTES or monotonic() > deadline:
                raise RuntimeError(f"the calendar is over {MAX_BYTES // 1_000_000} MB or took over {DEADLINE} s")
    return body.decode("utf-8", "replace")


def _read(url: str, moment: datetime) -> str:
    text, today = _download(url), datetime.combine(moment.date(), time(), moment.tzinfo)
    days = [(name, start, *events(text, start, start + timedelta(days=1)))
            for name, start in (("Today", today), ("Tomorrow", today + timedelta(days=1)))]
    # The counts come first, so a cut answer still says what it held and what could not be read.
    lines = ["; ".join(f"{name} ({start:%A %d %B}): {len(found)} event(s)" for name, start, found, _ in days)
             + (f"; {days[0][3]} calendar event(s) could not be read" if days[0][3] else "")]
    for name, start, found, _ in days:
        lines.append(f"{name} ({start:%A %d %B}):")
        lines += [f"- {'All day' if all_day else s.astimezone(moment.tzinfo).strftime('%H:%M')}: {title}"
                  for s, all_day, title in found] or ["- No events."]
    text = "\n".join(lines)
    return text if len(text) < OUTPUT // 4 else text[:OUTPUT // 4 - 16] + "\n[truncated]"


def fetch(cfg: dict, moment: datetime | None = None) -> str:
    """Download and read the feed in a child process with CPU, memory and wall-clock limits, killed on timeout: a
    hostile feed or repeat rule cannot keep anything of Dream's running."""
    moment = moment or local_now()
    job = {"url": cfg["ical_url"], "at": moment.isoformat(), "zone": getattr(moment.tzinfo, "key", None),
           "deadline": DEADLINE, "max": MAX_BYTES, "cpu": CPU, "expand": EXPAND_SECONDS}
    package_root = str(Path(__file__).resolve().parents[3])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [package_root, os.environ.get("PYTHONPATH")])),
           "PYTHONDONTWRITEBYTECODE": "1"}                  # the file-size limit must meet only its answer
    # The child's answer goes to files it cannot grow past OUTPUT bytes (RLIMIT_FSIZE); the parent reads no more.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            done = subprocess.run([sys.executable, "-m", "dream.sleepwalk.connectors.gcal"], input=json.dumps(job).encode(),
                                  stdout=out, stderr=err, timeout=WALL, env=env, start_new_session=True)
        except subprocess.TimeoutExpired:       # run() has killed and reaped the child
            raise RuntimeError(f"reading the calendar took over {WALL} s and was stopped") from None
        out.seek(0), err.seek(0)
        text, problem = out.read(OUTPUT).decode("utf-8", "replace"), err.read(OUTPUT).decode("utf-8", "replace")
    if done.returncode:
        raise RuntimeError(f"the calendar reader was stopped (signal {-done.returncode})" if done.returncode < 0
                           else (problem.strip().splitlines() or ["the calendar reader failed"])[-1][:300])
    return text


if __name__ == "__main__":                      # the child process
    import resource
    job = json.loads(sys.stdin.read())
    resource.setrlimit(resource.RLIMIT_CPU, (job["cpu"], job["cpu"]))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT, OUTPUT))
    DEADLINE, MAX_BYTES, EXPAND_SECONDS = job["deadline"], job["max"], job["expand"]
    at = datetime.fromisoformat(job["at"])
    try:
        print(_read(job["url"], at.astimezone(ZoneInfo(job["zone"])) if job["zone"] else at))
    except Exception as exc:
        sys.exit(f"{type(exc).__name__}: {exc}")
