"""DREAM-144 (settings design P3, phase S3) at the request seam: a sub-agent routed by `roles` to another served
model (a supervised layout's other server) sends that model's name, sizes its request to THAT model's context
window (`GET /props?model=<name>`, the supervisor's per-server answer) and takes a lease of its own, so it runs
beside the lead's request instead of behind it. With no roles every request body and every /props call is exactly
today's (the cross-build byte comparison is in the DREAM-144 record; here: no per-model /props call, the lead's
model, the lead's window).

No engine, no socket: FakeEngine/backend are the local-engine stand-ins of test_cache_friendly_head; the lease
directory is always a temporary one, never /tmp/dream-inference-<uid>.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from dream.core import inference_coordination
from dream.core.backends import openai_compat
from dream.core.inference_coordination import CoordinationError, EndpointCoordinator
from test_cache_friendly_head import FakeEngine, _Json, backend, tool
from test_settings_roles import LEAD, RESEARCHER, FILER, Capture, _clean, _write  # noqa: F401  (autouse fixture)

LEAD_CTX = 65536
SMALL = "small-model"
SMALL_CTX = 8192
_MARGIN = openai_compat._CTX_MARGIN


class Front(Capture):
    """Capture that also answers the supervisor's `/props?model=<name>` per model (None: 404, as for a name no
    server has) and records every GET url."""

    def __init__(self, engine, served=None, props_by_model=None):
        super().__init__(engine, served)
        self.props_by_model = props_by_model or {}
        self.gets: list[str] = []

    async def get(self, url, timeout=None):
        self.gets.append(url)
        parts = urlsplit(url)
        if parts.path.endswith("/props") and parts.query:
            model = parse_qs(parts.query).get("model", [None])[0]
            props = self.props_by_model.get(unquote(model)) if model else None
            if props is None:
                return SimpleNamespace(status_code=404, json=lambda: {"error": {"code": "model_not_found"}})
            if props == "loading":                                   # a child that has not finished loading
                return SimpleNamespace(status_code=503, json=lambda: {"error": {"code": "loading"}})
            if props == "error":
                raise httpx.ConnectError("front gone")
            return _Json(props)
        return await super().get(url, timeout=timeout)


def _props(n_ctx):
    return {"default_generation_settings": {"n_ctx": n_ctx}, "prompt_cache_slots": 1}


def _sub_backend(engine, *, base_url="http://engine.test/v1", props_by_model=None):
    b = backend(engine, model=LEAD, tools=[tool("probe", "probed")],
                subagents={"researcher": RESEARCHER, "filer": FILER}, base_url=base_url, n_ctx=LEAD_CTX)
    b._server_props = _props(LEAD_CTX)                      # what connect()'s /props probe leaves behind
    b._server_props_model = LEAD
    b._client = Front(engine, served=[LEAD, SMALL], props_by_model=props_by_model)
    return b, b._client


def _model_gets(front):
    return [url for url in front.gets if "/props?model=" in url]


# --- the window: a routed sub-agent is sized to ITS model's context ---------------------------------------------

async def test_a_routed_subagent_sends_model_b_and_is_sized_to_bs_window():
    _write({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), props_by_model={SMALL: _props(SMALL_CTX)})
    text, failed = await b._run_subagent("researcher", "look")
    assert (text, failed) == ("found it", False)
    [payload] = front.sent
    assert payload["model"] == SMALL
    assert _model_gets(front) == [f"http://engine.test/props?model={SMALL}"]
    assert payload["max_tokens"] < SMALL_CTX - _MARGIN               # B's 8k window, not the lead's 64k
    assert payload["max_tokens"] > SMALL_CTX // 2


async def test_the_per_model_props_are_read_once_per_model_and_kept():
    _write({"roles": {"subagents": {"default": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"calls": [("probe", {})]}, {"text": "one"}, {"text": "NOTHING"}]),
                            props_by_model={SMALL: _props(SMALL_CTX)})
    await b._run_subagent("researcher", "look")                 # two rounds
    await b._run_subagent("researcher", "look again")
    assert len(front.sent) == 3 and all(p["model"] == SMALL for p in front.sent)
    assert _model_gets(front) == [f"http://engine.test/props?model={SMALL}"]


async def test_without_roles_no_per_model_props_are_read_and_the_lead_window_applies():
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), props_by_model={SMALL: _props(SMALL_CTX)})
    await b._run_subagent("researcher", "look")
    [payload] = front.sent
    assert payload["model"] == LEAD and _model_gets(front) == []
    assert payload["max_tokens"] > SMALL_CTX                       # the lead's 64k window


async def test_a_routed_model_whose_props_cannot_be_read_keeps_the_lead_window():
    """The supervisor answers /props?model= for its servers; a plain engine answers any name with its one model.
    When the routed model's props are not there, the request is sized as before -- and still sent to that model."""
    _write({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), props_by_model={})
    text, failed = await b._run_subagent("researcher", "look")
    assert (text, failed) == ("found it", False)
    [payload] = front.sent
    assert payload["model"] == SMALL and payload["max_tokens"] > SMALL_CTX
    assert _model_gets(front) == [f"http://engine.test/props?model={SMALL}"]


async def test_a_role_naming_the_lead_model_itself_reads_nothing_extra():
    _write({"roles": {"subagents": {"researcher": {"model": LEAD}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), props_by_model={SMALL: _props(SMALL_CTX)})
    await b._run_subagent("researcher", "look")
    assert front.sent[0]["model"] == LEAD and _model_gets(front) == []


async def test_a_routed_subagents_history_is_compacted_against_its_own_window():
    """The compaction wall inside _subagent_loop uses the routed model's window: a history that fits the lead's
    64k but not B's 8k is compacted before the request, and the request is still admitted."""
    _write({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    engine = FakeEngine(side=[{"text": "found it"}])
    b, front = _sub_backend(engine, props_by_model={SMALL: _props(SMALL_CTX)})
    b.apply_context_policy({"subagent_overflow": "compact"})       # the owner's default is return (DREAM-177)
    text, failed = await b._run_subagent("researcher", "look at this\n" + "w" * 60_000)
    assert failed, text                                             # 60k characters cannot fit an 8k window
    assert front.sent == []                                        # refused before anything was sent
    assert "8,192" in text or "8192" in text


async def test_the_window_helpers_keep_todays_answers_without_a_window():
    b, _ = _sub_backend(FakeEngine())
    fill = 1000
    assert b._max_tokens(fill) == b._max_tokens(fill, window=None) == LEAD_CTX - fill - _MARGIN
    assert b._max_tokens(fill, window=SMALL_CTX) == SMALL_CTX - fill - _MARGIN
    assert b._props_window(_props(SMALL_CTX)) == SMALL_CTX
    assert b._props_window(None) is None and b._props_window({}) is None
    assert b._props_window({"default_generation_settings": {"n_ctx": "big"}}) is None


async def test_a_routed_models_props_are_asked_again_after_a_failed_answer():
    """Gate finding 2: a child still loading answers 503; that is not remembered as 'no props' for the session.
    Only a 200 is kept."""
    _write({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "one"}, {"text": "two"}, {"text": "three"}]),
                            props_by_model={SMALL: "loading"})
    await b._run_subagent("researcher", "look")
    assert front.sent[0]["max_tokens"] > SMALL_CTX                    # sized as before while it loads
    front.props_by_model[SMALL] = _props(SMALL_CTX)
    await b._run_subagent("researcher", "look")
    assert front.sent[1]["max_tokens"] < SMALL_CTX                    # asked again: B's window now
    await b._run_subagent("researcher", "look")
    assert _model_gets(front) == [f"http://engine.test/props?model={SMALL}"] * 2   # 503, 200, then kept


async def test_a_routed_models_props_are_asked_again_after_a_transport_error():
    _write({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "one"}, {"text": "two"}]), props_by_model={SMALL: "error"})
    text, failed = await b._run_subagent("researcher", "look")
    assert (text, failed) == ("one", False)                          # the run itself is not failed by the probe
    front.props_by_model[SMALL] = _props(SMALL_CTX)
    await b._run_subagent("researcher", "look")
    assert front.sent[1]["max_tokens"] < SMALL_CTX and len(_model_gets(front)) == 2


# --- the lead's own /props on a supervised endpoint (gate finding 1) ------------------------------------------

def _server_props(n_ctx, slots, vision_ready):
    return {"default_generation_settings": {"n_ctx": n_ctx}, "prompt_cache_slots": slots,
            "vision": {"ready": vision_ready, "reason": ""}}


async def test_the_lead_reads_its_own_servers_props_when_several_models_are_listed():
    """Live gate finding 1: with roles.main on a non-default server, the bare /props answered for the DEFAULT
    server (card 1's ctx 8192 was reported as 4096). When /v1/models lists more than one model the lead asks for
    its own model's /props; n_ctx, the cache slots and the vision report then all come from the main model's server."""
    engine = FakeEngine(props=_server_props(4096, 1, False))          # the bare answer: the default server's
    b, front = _sub_backend(engine, props_by_model={LEAD: _server_props(8192, 4, True), SMALL: _props(SMALL_CTX)})
    b.n_ctx = await b._probe_n_ctx()
    assert b.n_ctx == 8192
    assert b._prompt_cache_slots() == (4, "the engine's /props")
    assert b.capability_status()["vision_ready"]["value"] is True
    assert front.gets == ["http://engine.test/v1/models", f"http://engine.test/props?model={LEAD}"]


async def test_one_listed_model_keeps_the_bare_props_call():
    engine = FakeEngine(props=_server_props(4096, 1, False))
    b, front = _sub_backend(engine, props_by_model={LEAD: _server_props(8192, 4, True)})
    front.served = [LEAD]
    b.n_ctx = await b._probe_n_ctx()
    assert b.n_ctx == 4096 and b._prompt_cache_slots() == (1, "the engine's /props")
    assert front.gets == ["http://engine.test/v1/models", "http://engine.test/props"]


async def test_an_endpoint_without_a_model_list_keeps_the_bare_props_call():
    engine = FakeEngine(props=_server_props(4096, 1, False))
    b, front = _sub_backend(engine)
    front.served = None                                               # /v1/models is not answered
    b.n_ctx = await b._probe_n_ctx()
    assert b.n_ctx == 4096
    assert front.gets == ["http://engine.test/v1/models", "http://engine.test/props"]


# --- the lease: one per (endpoint, model) -------------------------------------------------------------------

def test_the_lease_key_is_todays_without_a_model_and_per_model_with_one(tmp_path):
    plain = EndpointCoordinator("loopback:11470", root=tmp_path)
    assert plain.key == hashlib.sha256(b"loopback:11470").hexdigest()
    a = EndpointCoordinator("loopback:11470", root=tmp_path, model="a")
    b = EndpointCoordinator("loopback:11470", root=tmp_path, model="b")
    assert a.key == EndpointCoordinator("loopback:11470", root=tmp_path, model="a").key
    assert len({plain.key, a.key, b.key}) == 3
    assert EndpointCoordinator("loopback:11471", root=tmp_path, model="a").key != a.key


async def test_leases_for_different_models_on_one_endpoint_overlap(tmp_path):
    a = EndpointCoordinator("loopback:11470", root=tmp_path / "leases", model="a")
    b = EndpointCoordinator("loopback:11470", root=tmp_path / "leases", model="b")
    events = []
    async with a.request():
        started = time.monotonic()
        async with b.request(timeout=2, on_event=events.append):
            assert a.status()["state"] == b.status()["state"] == "running"
            assert a.status()["request_id"] != b.status()["request_id"]
        assert time.monotonic() - started < 0.5
    assert [e["state"] for e in events] == ["running"]             # b never waited
    assert a.status()["state"] == b.status()["state"] == "idle"


async def test_leases_for_the_same_model_still_run_one_at_a_time(tmp_path):
    a = EndpointCoordinator("loopback:11470", root=tmp_path / "leases", model="a")
    other = EndpointCoordinator("loopback:11470", root=tmp_path / "leases", model="a")
    events = []
    async with a.request():
        with pytest.raises(CoordinationError, match="Timed out"):
            async with other.request(timeout=0.3, on_event=events.append):
                pytest.fail("overlap on one model")
    assert events[0]["state"] == "waiting"


async def test_a_dead_owners_lease_for_a_model_is_reconciled_as_today(tmp_path):
    """Fix #53 unchanged under the per-model key: a "running" record whose owner is gone, for an engine that
    started after it, is cleared instead of refusing the first request."""
    root = tmp_path / "leases"
    seeded = EndpointCoordinator("loopback:11470", root=root, model="b", server_started_at=lambda: time.time())
    import subprocess
    gone = subprocess.Popen(["true"])
    gone.wait()
    root.mkdir(mode=0o700)
    record = {"schema_version": 1, "state": "running", "request_id": "ab" * 16, "pid": gone.pid,
              "started_at": time.time() - 600}
    fd = os.open(root / (seeded.key + ".json"), os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    events = []
    async with seeded.request(on_event=events.append):
        pass
    assert [e["state"] for e in events] == ["reconciled", "running"]
    assert seeded.status()["state"] == "idle"


# --- the backend takes the lease of the model it sends to ---------------------------------------------------

@pytest.fixture
def leases(tmp_path, monkeypatch):
    """Every EndpointCoordinator the backend makes lives under tmp_path."""
    leases_root = tmp_path / "leases"

    class Local(EndpointCoordinator):
        def __init__(self, endpoint, *, root=None, server_started_at=None, model=None):
            super().__init__(endpoint, root=leases_root if root is None else root,
                             server_started_at=server_started_at, model=model)
    monkeypatch.setattr(inference_coordination, "EndpointCoordinator", Local)
    return leases_root


def test_the_backend_keys_its_coordinators_by_endpoint_and_model(leases):
    b, _ = _sub_backend(FakeEngine(), base_url="http://127.0.0.1:11470/v1")
    lead = b._coordinator()
    assert lead is b._coordinator(LEAD) is b._coordinator()
    assert lead.key == EndpointCoordinator("loopback:11470", root=leases, model=LEAD).key
    other = b._coordinator(SMALL)
    assert other is not lead and other.key != lead.key and other is b._coordinator(SMALL)
    assert b.coordination_status()["enabled"] is True              # the lead's, as before
    assert backend(FakeEngine(), key="openai", base_url="https://api.example.com/v1")._coordinator(SMALL) is None


async def test_a_routed_subagent_runs_while_another_dream_request_holds_the_lead_models_lease(leases):
    _write({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), base_url="http://127.0.0.1:11470/v1",
                            props_by_model={SMALL: _props(SMALL_CTX)})
    holder = EndpointCoordinator("loopback:11470", root=leases, model=LEAD)
    async with holder.request():                                   # the lead's model is busy elsewhere
        started = time.monotonic()
        text, failed = await b._run_subagent("researcher", "look")
        assert (text, failed) == ("found it", False)
        assert time.monotonic() - started < 1.0
    assert front.sent[0]["model"] == SMALL


async def test_an_unrouted_subagent_waits_behind_the_lead_models_lease_as_before(leases):
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), base_url="http://127.0.0.1:11470/v1")
    notices = []
    b._background_emit = lambda event: notices.append(event.data)
    holder = EndpointCoordinator("loopback:11470", root=leases, model=LEAD)
    async with holder.request():
        run = asyncio.create_task(b._run_subagent("researcher", "look"))
        await asyncio.sleep(0.4)
        assert not run.done() and front.sent == []                # nothing sent while the lead's model is held
    text, failed = await asyncio.wait_for(run, 5)
    assert (text, failed) == ("found it", False) and front.sent[0]["model"] == LEAD
    assert any("Waiting for another Dream request" in str(n) for n in notices)


async def test_two_routed_subagents_on_one_model_still_run_one_at_a_time(leases):
    _write({"roles": {"subagents": {"default": {"model": SMALL}}}})
    engine = FakeEngine(side=[{"text": "one"}, {"text": "two"}])
    b, front = _sub_backend(engine, base_url="http://127.0.0.1:11470/v1", props_by_model={SMALL: _props(SMALL_CTX)})
    holder = EndpointCoordinator("loopback:11470", root=leases, model=SMALL)
    async with holder.request():
        run = asyncio.create_task(b._run_subagent("researcher", "look"))
        await asyncio.sleep(0.4)
        assert not run.done() and front.sent == []
    assert await asyncio.wait_for(run, 5) == ("one", False)
