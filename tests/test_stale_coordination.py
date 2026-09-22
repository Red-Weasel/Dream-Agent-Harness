"""A crash leaves the inference lease saying "running" for a dead process; on
2026-09-22 01:36 that refused the owner's first prompt in 0.26 s. When the owner is
dead AND the server is a newer process than the record, nothing can be in flight."""
from __future__ import annotations
import json
import os
import uuid
import subprocess
import time
import pytest
from dream.core.inference_coordination import CoordinationError, EndpointCoordinator

pytestmark = pytest.mark.asyncio
ENDPOINT = "http://127.0.0.1:11435/v1"


def _dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def _seed(c: EndpointCoordinator, record: dict) -> None:
    c.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = c.root / (c.key + ".json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)


def _record(c: EndpointCoordinator) -> dict:
    return json.loads((c.root / (c.key + ".json")).read_text())


async def test_dead_owner_with_a_newer_server_is_reconciled_automatically(tmp_path):
    started = time.time() - 600
    c = EndpointCoordinator(ENDPOINT, root=tmp_path / "coord", server_started_at=lambda: started + 60)
    _seed(c, {"schema_version": 1, "state": "running", "request_id": uuid.uuid4().hex, "pid": _dead_pid(), "started_at": started})
    events = []
    async with c.request(on_event=events.append):
        pass
    assert any(e.get("state") == "reconciled" for e in events)   # the lease cleared itself
    assert _record(c)["state"] == "idle"                         # and the request ran and released it


async def test_an_older_server_still_needs_a_human(tmp_path):
    started = time.time() - 600
    c = EndpointCoordinator(ENDPOINT, root=tmp_path / "coord", server_started_at=lambda: started - 60)
    _seed(c, {"schema_version": 1, "state": "running", "request_id": uuid.uuid4().hex, "pid": _dead_pid(), "started_at": started})
    with pytest.raises(CoordinationError):
        async with c.request():
            pass


async def test_a_live_owner_still_needs_a_human(tmp_path):
    started = time.time() - 600
    c = EndpointCoordinator(ENDPOINT, root=tmp_path / "coord", server_started_at=lambda: started + 60)
    _seed(c, {"schema_version": 1, "state": "running", "request_id": uuid.uuid4().hex, "pid": os.getpid(), "started_at": started})
    with pytest.raises(CoordinationError):
        async with c.request():
            pass
