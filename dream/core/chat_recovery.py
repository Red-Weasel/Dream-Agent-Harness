"""Durable, bounded status records for ordinary chat recovery."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_ATTEMPT_RE = re.compile(r"[0-9a-f]{32}\Z")
_STATES = frozenset({"started", "success", "error", "incomplete", "interrupted"})
_MAX_CONTENT = 2048


@dataclass(frozen=True)
class ChatRecovery:
    state: str
    needs_inspection: bool
    attempt_id: str | None
    summary: str


def status_record(attempt_id: str, state: str, *, needs_inspection: bool) -> str:
    if not isinstance(attempt_id, str) or not _ATTEMPT_RE.fullmatch(attempt_id):
        raise ValueError("ordinary chat attempt ID is invalid")
    if not isinstance(state, str) or state not in _STATES:
        raise ValueError("ordinary chat state is invalid")
    if type(needs_inspection) is not bool:
        raise ValueError("ordinary chat inspection flag is invalid")
    if (state == "success") == needs_inspection or (state == "started" and not needs_inspection):
        raise ValueError("ordinary chat state and inspection flag conflict")
    return json.dumps({
        "schema": 1,
        "kind": "ordinary_chat",
        "attempt_id": attempt_id,
        "state": state,
        "needs_inspection": needs_inspection,
        "explanation": _summary(state),
    }, ensure_ascii=False)


def _summary(state: str) -> str:
    if state == "success":
        return "The prior chat turn completed at the protocol level. Task success is unknown."
    if state == "unknown":
        return "UNKNOWN prior chat outcome. Inspect existing effects before explicit continuation."
    return f"The prior chat turn ended as {state}. Inspect existing effects before explicit continuation."


def _unknown(attempt_id: str | None = None) -> ChatRecovery:
    return ChatRecovery("unknown", True, attempt_id, _summary("unknown"))


def _decode(row: Mapping[str, Any]) -> tuple[int, dict[str, Any]] | None:
    if row.get("role") != "turn_status" or type(row.get("id")) is not int or row["id"] <= 0:
        return None
    content = row.get("content")
    if not isinstance(content, str) or len(content) > _MAX_CONTENT:
        return None
    try:
        def reject_duplicates(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = item
            return result
        value = json.loads(content, object_pairs_hook=reject_duplicates)
    except (TypeError, ValueError, RecursionError):
        return None
    required = {"schema", "kind", "attempt_id", "state", "needs_inspection", "explanation"}
    if not isinstance(value, dict) or set(value) != required:
        return None
    if type(value["schema"]) is not int or value["schema"] != 1 or value["kind"] != "ordinary_chat":
        return None
    if not isinstance(value["attempt_id"], str) or not _ATTEMPT_RE.fullmatch(value["attempt_id"]):
        return None
    if not isinstance(value["state"], str) or value["state"] not in _STATES:
        return None
    if type(value["needs_inspection"]) is not bool:
        return None
    if not isinstance(value["explanation"], str) or len(value["explanation"]) > _MAX_CONTENT:
        return None
    return row["id"], value


def parse_last_ordinary_chat_status(
    rows: Sequence[Mapping[str, Any]], latest_activity_id: int | None = None,
) -> ChatRecovery | None:
    """Read at most two newest status rows without trusting stored prose.

    A status is terminal only when it immediately follows a matching durable
    ``started`` record. Later transcript activity makes that result stale.
    """
    status_rows = [row for row in rows if row.get("role") == "turn_status"]
    if not status_rows:
        return None
    if type(latest_activity_id) is not int and latest_activity_id is not None:
        return _unknown()
    decoded = [_decode(row) for row in status_rows[:2]]
    if not decoded or decoded[0] is None:
        return _unknown()
    newest_id, newest = decoded[0]
    if latest_activity_id is not None and latest_activity_id > newest_id:
        return _unknown(newest["attempt_id"])
    if newest["state"] == "started":
        return _unknown(newest["attempt_id"])
    if len(decoded) < 2 or decoded[1] is None:
        return _unknown(newest["attempt_id"])
    started_id, started = decoded[1]
    if (started_id >= newest_id or started["state"] != "started" or not started["needs_inspection"]
            or started["attempt_id"] != newest["attempt_id"]):
        return _unknown(newest["attempt_id"])
    if newest["state"] == "success" and newest["needs_inspection"]:
        return _unknown(newest["attempt_id"])
    if newest["state"] != "success" and not newest["needs_inspection"]:
        return _unknown(newest["attempt_id"])
    return ChatRecovery(newest["state"], newest["needs_inspection"], newest["attempt_id"], _summary(newest["state"]))
