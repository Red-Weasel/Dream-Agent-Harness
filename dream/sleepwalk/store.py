"""Sleepwalk's private JSON store (DREAM-156): automations and their run records under data/sleepwalk/.

Every file is written atomically (a temporary file, fsync, rename) with mode 0600. data/sleepwalk/ and every folder
below it are set to 0700 whenever Sleepwalk writes there, including folders that already existed (DREAM-157). A file
lock serialises every change across Dream processes; damaged entries are skipped, reported and left as they are."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import secrets
import shutil
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .. import config
from ..core.run_state import atomic_write
from . import schedule

ROOT = config.DATA_DIR / "sleepwalk"
TEMPLATES = Path(__file__).with_name("templates.json")
ICONS = ("spark", "sun", "moon", "bulb", "book", "mail", "globe", "pen", "leaf", "heart", "star", "note")
EVERY = ("day", "weekdays", "week", "month", "hours")
_ID = re.compile(r"^[a-z0-9]{12}\Z")
_RUN = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]{6}\Z")
_CONNECTOR = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}\Z")
_TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]\Z")


def _folder(path: Path) -> Path:
    ROOT.parent.mkdir(parents=True, exist_ok=True)
    chain = [path, *path.parents]
    for folder in reversed(chain[:chain.index(ROOT) + 1]):
        folder.mkdir(mode=0o700, exist_ok=True)
        folder.chmod(0o700)
    return path


class AlreadyRunning(ValueError):
    pass


@contextmanager
def _flock(path: Path, *, wait: bool = True):
    _folder(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise AlreadyRunning("This automation is already running") from None
        yield
    finally:
        os.close(fd)


@contextmanager
def running(identifier: str):
    """Held for a whole run, across every Dream process and window; a second Run now is refused, not queued."""
    if not _ID.match(identifier or ""):
        raise KeyError(identifier)
    with _flock(ROOT / "locks" / f"{identifier}.lock", wait=False):
        yield


def _report(problems: list[str], found: list[str]) -> None:
    for problem in found:
        logging.getLogger(__name__).warning("Sleepwalk: %s", problem)
    problems.extend(found)


def _write(path: Path, data: dict) -> None:
    _folder(path.parent)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")    # mkstemp: created 0600


def _text(value, name: str, limit: int, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} must be text of at most {limit:,} characters" + (" and not empty" if required else ""))
    return value.strip()


def _trigger(raw) -> dict:
    if not isinstance(raw, dict) or raw.get("every") not in EVERY:
        raise ValueError("Each trigger needs a repeat: " + ", ".join(EVERY))
    if not isinstance(raw.get("at"), str) or not _TIME.match(raw["at"]):
        raise ValueError("Each trigger needs a time of day as HH:MM")
    out = {"every": raw["every"], "at": raw["at"]}
    if raw["every"] == "week":
        days = raw.get("days")
        if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise ValueError("A weekly trigger needs days from 0 (Monday) to 6 (Sunday)")
        out["days"] = sorted(set(days))
    if raw["every"] == "month":
        if type(raw.get("day")) is not int or not 1 <= raw["day"] <= 31:
            raise ValueError("A monthly trigger needs a day from 1 to 31")
        out["day"] = raw["day"]
    if raw["every"] == "hours":
        if type(raw.get("n")) is not int or not 1 <= raw["n"] <= 24:
            raise ValueError("An hourly trigger needs a number of hours from 1 to 24")
        out["n"] = raw["n"]
        try:
            out["from"] = date.fromisoformat(raw.get("from") or schedule.now().date().isoformat()).isoformat()
        except (TypeError, ValueError):
            raise ValueError("An hourly trigger's start day must be a date") from None
    label = _text(raw.get("label"), "A trigger label", 60, required=False)
    return {**out, "label": label} if label else out


def _runner(raw) -> dict:
    from ..core.council_config import validate_effort, validate_model
    from ..core.providers import PROVIDERS
    if not isinstance(raw, dict) or raw.get("provider") not in PROVIDERS:
        raise ValueError("Choose a runner from Dream's providers")
    model = validate_model(raw.get("model") or "", allow_empty=True) or None
    effort = raw.get("effort") or None
    if effort is not None:
        validate_effort(raw["provider"], effort, model)
    return {"provider": raw["provider"], "model": model, "effort": effort}


def validate(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Expected an automation object")
    if raw.get("icon") not in ICONS:
        raise ValueError("Unknown icon")
    triggers = raw.get("triggers")
    if not isinstance(triggers, list) or len(triggers) > 8:
        raise ValueError("An automation holds at most 8 triggers")
    if not str(raw.get("title", "")).isprintable():
        raise ValueError("The title must be one line of text")
    return {"title": _text(raw.get("title"), "Title", 120), "icon": raw["icon"],
            "enabled": raw.get("enabled", True) is not False,
            "instructions": _text(raw.get("instructions"), "Instructions", 8000),
            "triggers": [_trigger(t) for t in triggers], "runner": _runner(raw.get("runner")),
            "connectors": _ids(raw.get("connectors", []), "connector"), "notify": ["app", *_ids(raw.get("notify", []), "channel")],
            "missed": _choice(raw.get("missed", "run_once"), ("run_once", "skip"), "missed-run policy"),
            "fallback": _fallback(raw.get("fallback"))}


def _ids(raw, name: str) -> list[str]:
    """Connector ids (DREAM-160); whether one is installed is checked when the run uses it."""
    if not isinstance(raw, list) or len(raw) > 8 or any(not isinstance(i, str) or not _CONNECTOR.match(i) for i in raw):
        raise ValueError(f"Unknown {name}")
    return list(dict.fromkeys(i for i in raw if i != "app"))


def _choice(value, allowed: tuple, name: str) -> str:
    if value not in allowed:
        raise ValueError(f"Unknown {name}")
    return value


def _fallback(raw) -> dict | None:
    """The cloud runner used when the local model is not loaded (DREAM-158); None means the run is skipped."""
    from ..core.moe import can_isolate
    if raw is None:
        return None
    runner = _runner(raw)
    if runner["provider"] == "machx" or not can_isolate(runner["provider"]):
        raise ValueError("The fallback must be a cloud runner that can run isolated")
    return runner


def _usable(a) -> bool:
    try:
        return (_ID.match(a["id"]) is not None and all(isinstance(a[k], str) for k in ("title", "icon", "instructions"))
                and isinstance(a["runner"]["provider"], str) and all(_trigger(t) for t in a["triggers"]))
    except (TypeError, KeyError, ValueError, AttributeError):
        return False


def _read() -> tuple[dict | None, list[str]]:
    path = ROOT / "automations.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "automations": []}, []
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("automations"), list):
        return None, [f"{path} is not a Sleepwalk store Dream can read; it was left as it is"]
    bad = sum(not _usable(a) for a in data["automations"])
    return data, [f"{bad} damaged automation(s) in {path} were skipped and left as they are"] if bad else []


def load(problems: list[str] | None = None) -> list[dict]:
    data, found = _read()
    _report(problems if problems is not None else [], found)
    return [a for a in data["automations"] if _usable(a)] if data else []


def get(identifier: str) -> dict:
    found = next((a for a in load() if a["id"] == identifier), None)
    if found is None:
        raise KeyError(identifier)
    return found


def _change(edit) -> None:
    with _flock(ROOT / ".store.lock"):
        data, found = _read()
        if data is None:
            raise ValueError(found[0])
        data["automations"] = edit(data["automations"])
        _write(ROOT / "automations.json", data)


def save(raw: dict) -> dict:
    automation = validate(raw)
    identifier = raw.get("id")

    def edit(automations):
        nonlocal automation
        old = next((a for a in automations if isinstance(a, dict) and a.get("id") == identifier), None) if identifier else None
        if identifier and old is None:
            raise KeyError(identifier)
        # Saving (an edit, or the card's on/off switch) starts the schedule from now: no earlier slot fires.
        automation = {"id": identifier or secrets.token_hex(6), **automation,
                      "created": (old or {}).get("created") or datetime.now().isoformat(timespec="seconds"),
                      "last_slot": schedule.now().isoformat(timespec="seconds"),
                      **({"pending": pending_of(old)} if old and pending_of(old) else {})}
        return [automation if old is not None and a is old else a for a in automations] + ([] if old else [automation])
    _change(edit)
    return automation


def pending_of(automation: dict) -> list[str]:
    """The labels of scheduled runs not yet recorded (a single string from an older version reads as one)."""
    marks = automation.get("pending") or []
    return [marks] if isinstance(marks, str) else [m for m in marks if isinstance(m, str)] if isinstance(marks, list) else []


def claim(identifier: str, slot: datetime, grace, pending: str | None = None, now=None) -> dict | None:
    """Mark `slot` handled, once, and return the automation as it is now, under the store lock: None when another
    tick or process claimed it, or when it was turned off or saved since (a save moves last_slot to now), so a stale
    copy is never run. `pending` (the run's label) stays listed until that run's record is written (see add_run)."""
    claimed = None

    def edit(automations):
        nonlocal claimed
        a = next((a for a in automations if isinstance(a, dict) and a.get("id") == identifier and _usable(a)), None)
        found = schedule.due(a, now or schedule.now(), grace) if a is not None and a.get("enabled") else None
        if found is not None and found[0] == slot:
            a["last_slot"], claimed = slot.isoformat(), a
            a["pending"] = [*pending_of(a), pending] if pending else pending_of(a)
        return automations
    _change(edit)
    return claimed


def scheduler_lock() -> int | None:
    """The scheduler's lock, held by one Dream process for its lifetime; None when another process holds it."""
    _folder(ROOT)
    fd = os.open(ROOT / ".scheduler.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def delete(identifier: str) -> None:
    def edit(automations):
        kept = [a for a in automations if not (isinstance(a, dict) and a.get("id") == identifier)]
        if len(kept) == len(automations):
            raise KeyError(identifier)
        return kept
    _change(edit)
    shutil.rmtree(attachments(identifier), ignore_errors=True)


def connector_settings() -> dict:
    """Non-secret connector settings; secrets live in the keyring or the environment (credentials.py)."""
    try:
        data = json.loads((ROOT / "connectors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def save_connector_settings(cid: str, values: dict) -> None:
    with _flock(ROOT / ".store.lock"):
        saved = connector_settings()
        _write(ROOT / "connectors.json", {**saved, cid: {**saved.get(cid, {}), **values}})


def attachments(identifier: str) -> Path:
    """The automation's attachment folder (DREAM-160); copied into each run's folder."""
    if not _ID.match(identifier or ""):
        raise KeyError(identifier)
    return ROOT / "attachments" / identifier


def attach(identifier: str, name: str, data: bytes) -> None:
    get(identifier)
    if not _FILE.match(name or "") or len(data) > 2_000_000:
        raise ValueError("Attachments need a plain file name and at most 2 MB")
    folder = _folder(attachments(identifier))
    if name not in {p.name for p in folder.iterdir()} and len(list(folder.iterdir())) >= 10:
        raise ValueError("An automation holds at most 10 attachments")
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def detach(identifier: str, name: str) -> None:
    if not _FILE.match(name or ""):
        raise KeyError(name)
    (attachments(identifier) / name).unlink()


def templates() -> list[dict]:
    return json.loads(TEMPLATES.read_text(encoding="utf-8"))


def add_run(identifier: str, record: dict) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    record = {"run": stamp, "automation": identifier, **record}
    with _flock(ROOT / ".store.lock"):
        _write(ROOT / "runs" / identifier / f"{stamp}.json", record)
        data, _ = _read()
        claimed = next((a for a in data["automations"] if isinstance(a, dict) and a.get("id") == identifier
                        and record.get("trigger") in pending_of(a)), None) if data else None
        if claimed is not None:                     # this scheduled run has ended; another run's mark stays
            claimed["pending"] = pending_of(claimed)
            claimed["pending"].remove(record["trigger"])
            _write(ROOT / "automations.json", data)
    return record


def _run_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    usable = (isinstance(record, dict) and record.get("status") in ("OK", "FAILED", "SKIPPED")
              and all(isinstance(record.get(k), str) for k in ("run", "automation", "title", "started"))
              and isinstance(record.get("runner"), dict))
    return record if usable else None


def runs(limit: int = 100, problems: list[str] | None = None) -> list[dict]:
    files = sorted((ROOT / "runs").glob("*/*.json"), key=lambda p: p.name, reverse=True)[:limit]
    rows = [r for r in map(_run_record, files) if r is not None]
    if len(rows) < len(files):
        _report(problems if problems is not None else [],
                [f"{len(files) - len(rows)} damaged run record(s) under {ROOT / 'runs'} were skipped"])
    return [{k: v for k, v in r.items() if k != "output"} for r in rows]


def read_run(identifier: str, run: str) -> dict:
    if not _ID.match(identifier or "") or not _RUN.match(run or ""):
        raise KeyError(run)
    path = ROOT / "runs" / identifier / f"{run}.json"
    if not path.is_file():
        raise KeyError(run)
    record = _run_record(path)
    if record is None:
        raise ValueError(f"The run record {path} is damaged; it was left as it is")
    return record
