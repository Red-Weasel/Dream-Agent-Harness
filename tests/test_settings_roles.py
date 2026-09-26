"""DREAM-142 S1 (settings design P3, 3.3): a `roles` section in data/runtime-settings.json names the model a
sub-agent, the verifier or the filer runs on; missing means "same as main" (the owner's default until they pick).
The seam is `_subagent_loop`'s payload model, on the lead's own endpoint: with no roles every request is exactly
today's. A verifier on another model is its own conversation, so fix #71's continuation/skip on a one-slot engine
does not apply to it. Also: `dream settings set|unset|check`, `/settings set|unset|check`, `/maxtokens n --save`
(`output.max_tokens`), and `behaviour.vitals`, the owner's switch for the `ie_vitals` request flag (default on).

No engine, no socket: FakeEngine/backend are the local-engine stand-ins of test_cache_friendly_head.
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import config, management
from dream.core import settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from test_cache_friendly_head import FakeClient, FakeEngine, _Json, backend, tool, turn
from test_cache_friendly_verifier import CHECKS, VERIFIER, page_backend, props, side

LEAD = "lead-model"
RESEARCHER = SimpleNamespace(name="researcher", description="research", prompt="RESEARCHER PROMPT",
                             tool_names=("probe",))
FILER = SimpleNamespace(name="filer", description="files memories", prompt="FILER PROMPT", tool_names=("probe",))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DREAM_MAX_TOKENS", "DREAM_SINGLE_SLOT_VERIFIER", "DREAM_AUTO_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 131072)


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class Capture(FakeClient):
    """FakeClient that also keeps each request body exactly as sent, and answers /v1/models with `served`
    (None: the endpoint does not answer it)."""

    def __init__(self, engine, served=None):
        super().__init__(engine)
        self.sent: list[dict] = []
        self.served = served

    async def get(self, url, timeout=None):
        if url.endswith("/v1/models"):
            if self.served is None:
                return SimpleNamespace(status_code=404, json=lambda: {})
            return _Json({"object": "list", "data": [{"id": name, "object": "model"} for name in self.served]})
        return await super().get(url, timeout=timeout)

    def stream(self, method, url, json=None):
        self.sent.append(copy.deepcopy(json))
        return super().stream(method, url, json=json)

    async def post(self, url, json=None):
        self.sent.append(copy.deepcopy(json))
        return await super().post(url, json=json)


def _capture(b, served=None):
    b._client = Capture(b._client.engine, served)
    return b._client


def _sub_backend(engine, **kw):
    b = backend(engine, model=LEAD, tools=[tool("probe", "probed")],
                subagents={"researcher": RESEARCHER, "filer": FILER}, **kw)
    return b, _capture(b)


# --- the seam: sub-agents -------------------------------------------------------------------------------

async def test_without_roles_a_subagent_runs_on_the_lead_model():
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}]))
    text, failed = await b._run_subagent("researcher", "look")
    assert (text, failed) == ("found it", False)
    assert [p["model"] for p in sent.sent] == [LEAD]


async def test_the_default_subagent_role_routes_every_subagent():
    _write({"roles": {"subagents": {"default": {"model": "small-model"}}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}, {"text": "NOTHING"}]))
    await b._run_subagent("researcher", "look")
    await b._run_subagent("filer", "file this")
    assert [p["model"] for p in sent.sent] == ["small-model", LEAD]   # the filer has its own row
    assert b.model == LEAD


async def test_a_named_subagent_row_beats_the_default():
    _write({"roles": {"subagents": {"default": {"model": "small-model"},
                                    "researcher": {"provider": "machx", "model": "research-model"}}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}]))
    await b._run_subagent("researcher", "look")
    assert sent.sent[0]["model"] == "research-model"


async def test_the_filer_role():
    _write({"roles": {"filer": {"model": "filing-model"}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "NOTHING"}]))
    assert await b._run_filer("the owner said something durable") == []
    assert [p["model"] for p in sent.sent] == ["filing-model"]


async def test_a_role_on_another_provider_fails_visibly_and_sends_nothing():
    """S1 routes on the lead's endpoint only; a role naming another provider must not quietly run on the lead's
    model instead."""
    _write({"roles": {"subagents": {"researcher": {"provider": "openai", "model": "gpt-x"}}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}]))
    text, failed = await b._run_subagent("researcher", "look")
    assert failed and sent.sent == []
    assert "roles.subagents.researcher" in text and "openai" in text and "machx" in text


async def test_an_invalid_roles_section_fails_the_subagent_visibly():
    _write({"roles": {"subagents": {"default": {"model": ""}}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}]))
    text, failed = await b._run_subagent("researcher", "look")
    assert failed and sent.sent == [] and "roles" in text


# --- the seam: the verifier and fix #71 -----------------------------------------------------------------

async def _sweep(b):
    b._verify_at_turn_end = "page.html"
    return await b._verifier_sweep()


async def test_a_routed_verifier_not_confirmed_separate_keeps_the_single_slot_skip():
    """Gate round 1 finding 1: today's engine answers ANY model name with its one loaded model, on its one slot,
    so a routed verifier there would still evict the lead's conversation (the 8-minute re-read #71 fixed).
    The endpoint lists only its own model: not confirmed, the skip stays."""
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    _capture(b, served=[LEAD])
    events = await _sweep(b)
    assert side(engine) == []
    assert any(str(e.data).startswith("verifier: skipped on page.html") for e in events)


@pytest.mark.parametrize("served", [None, ["checker-model"], [LEAD], []])
async def test_nothing_short_of_a_separate_listing_confirms_it(served):
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    _capture(b, served=served)
    assert await b._verifier_separate() is False
    await _sweep(b)
    assert side(engine) == []


async def test_an_unconfirmed_routed_verifier_continues_on_the_lead_model_when_asked(monkeypatch):
    monkeypatch.setenv("DREAM_SINGLE_SLOT_VERIFIER", "continue")
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    sent = _capture(b, served=[LEAD])
    await _sweep(b)
    assert len(side(engine)) == 2
    assert {p["model"] for p in sent.sent if "stream" in p and not p["stream"]} == {LEAD}
    assert any(m.get("name") == "dream_verifier_instruction" for m in b.messages)   # inside the conversation


async def test_a_verifier_listed_separately_runs_as_its_own_conversation():
    """The P2 supervisor lists several names at /v1/models: the verifier's model is served apart from the lead."""
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    sent = _capture(b, served=[LEAD, "checker-model"])
    events = await _sweep(b)
    checks = side(engine)
    assert len(checks) == 2
    assert checks[0]["messages"][0] == {"role": "system", "content": "VERIFIER PROMPT"}   # not a continuation
    assert [p["model"] for p in sent.sent] == ["checker-model", "checker-model"]
    assert not any(m.get("name") == "dream_verifier_instruction" for m in b.messages)
    assert any("verifier: findings on page.html" in str(e.data) for e in events)


async def test_an_engine_with_more_slots_runs_the_routed_verifier_on_its_model():
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=4))
    b = await page_backend(engine, model=LEAD)
    sent = _capture(b, served=None)
    assert await b._verifier_separate() is True
    await _sweep(b)
    assert [p["model"] for p in sent.sent] == ["checker-model", "checker-model"]
    assert side(engine)[0]["messages"][0] == {"role": "system", "content": "VERIFIER PROMPT"}


async def test_a_verifier_role_naming_the_lead_model_keeps_the_single_slot_skip():
    _write({"roles": {"verifier": {"model": LEAD}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    _capture(b, served=[LEAD, "other"])
    events = await _sweep(b)
    assert side(engine) == []
    assert any(str(e.data).startswith("verifier: skipped on page.html") for e in events)


async def test_without_roles_the_single_slot_skip_is_unchanged():
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    events = await _sweep(b)
    assert side(engine) == []
    assert any(str(e.data).startswith("verifier: skipped on page.html") for e in events)


@pytest.mark.parametrize("served,runs", [([LEAD, "checker-model"], True), ([LEAD], False)])
async def test_a_routed_directed_check_on_a_single_slot_engine(served, runs):
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    sent = _capture(b, served=served)
    text, failed = await b._fork_verifier({"path": "page.html", "task": "check the header"})
    if runs:
        assert not text.startswith("Not run") and len(side(engine)) == 2
        assert {p["model"] for p in sent.sent} == {"checker-model"}
    else:
        assert text.startswith("Not run") and side(engine) == []


async def test_a_misconfigured_verifier_role_is_reported_not_skipped():
    _write({"roles": {"verifier": {"provider": "xai", "model": "grok-x"}}})
    engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine, model=LEAD)
    events = await _sweep(b)
    assert side(engine) == []
    said = " ".join(str(e.data) for e in events)
    assert "verifier: could not run on page.html" in said and "roles.verifier" in said


# --- the vitals switch ----------------------------------------------------------------------------------

async def _connected(monkeypatch):
    b = backend(FakeEngine(lead=[{"text": "ok"}]), model=LEAD)
    engine = b._client.engine

    async def probe():
        return 65536
    monkeypatch.setattr(b, "_probe_n_ctx", probe)
    await b.connect()
    await b._client.aclose()
    b._client = Capture(engine)
    return b


async def test_vitals_default_on_sends_the_flag(monkeypatch):
    b = await _connected(monkeypatch)
    assert b.vitals is True
    await turn(b, "hello")
    assert b._client.sent[0]["ie_vitals"] is True


async def test_vitals_off_leaves_the_flag_out(monkeypatch):
    _write({"behaviour": {"vitals": False}})
    b = await _connected(monkeypatch)
    assert b.vitals is False
    await turn(b, "hello")
    assert "ie_vitals" not in b._client.sent[0]
    assert b._client.sent[0]["stream_tool_preview"] is True        # only the vitals flag is gated


# --- CLI: set / unset / check ---------------------------------------------------------------------------

def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


def test_set_writes_through_the_runtime_settings_writer_and_keeps_other_sections(capsys):
    path = _write({"version": 1, "profile": "lean", "overrides": {"max_parallel": 1},
                   "blender": {"binary": "/opt/tool"}, "future": {"k": 1}})
    assert _cli(capsys, "set", "roles.subagents.default.model", "small-model")[0] == 0
    assert _cli(capsys, "set", "roles.subagents.default.provider", "machx")[0] == 0
    assert _cli(capsys, "set", "behaviour.vitals", "off")[0] == 0
    assert _cli(capsys, "set", "output.max_tokens", "50000")[0] == 0
    saved = read_settings(path)
    assert saved["roles"] == {"subagents": {"default": {"model": "small-model", "provider": "machx"}}}
    assert saved["behaviour"] == {"vitals": False} and saved["output"] == {"max_tokens": 50000}
    assert saved["blender"] == {"binary": "/opt/tool"} and saved["future"] == {"k": 1}
    assert saved["profile"] == "lean" and saved["overrides"] == {"max_parallel": 1}
    assert sorted(p.name for p in path.parent.iterdir()) == ["runtime-settings.json", "runtime-settings.json.lock"]
    rows = settings.effective()
    assert rows["roles.subagents.default"] == ({"model": "small-model", "provider": "machx"}, "global")
    assert rows["behaviour.vitals"] == (False, "global")


@pytest.mark.parametrize("argv,needle", [
    (("set", "roles.verifier.provider", "machx"), "roles.verifier.model"),     # a model first
    (("set", "roles.critic.model", "x"), "roles.critic"),                      # not wired in S1
    (("set", "roles.main.model", "x"), "roles.main"),
    (("set", "roles.subagents.researcher.provider", "nosuch"), "nosuch"),
    (("set", "roles.subagents.researcher.model", "m"), None),                  # fine: control case
    (("set", "roles.verifier.provider", "codex"), "codex"),                   # a CLI cannot take the request
    (("set", "output.max_tokens", "100"), "256"),
    (("set", "output.max_tokens", "lots"), "output.max_tokens"),
    (("set", "behaviour.vitals", "maybe"), "behaviour.vitals"),
    (("set", "no.such.key", "1"), "no.such.key"),
    (("set", "profile", "lean"), "dream profile"),
])
def test_set_refuses_what_it_cannot_apply(capsys, argv, needle):
    code, out = _cli(capsys, *argv)
    if needle is None:
        assert code == 0
        return
    assert code == 2 and needle in out
    assert not settings_path().exists()                                       # a refusal writes nothing


def test_a_provider_must_be_an_http_provider(capsys):
    assert _cli(capsys, "set", "roles.verifier.model", "m")[0] == 0
    code, out = _cli(capsys, "set", "roles.verifier.provider", "codex")
    assert code == 2 and "codex" in out
    assert read_settings()["roles"] == {"verifier": {"model": "m"}}


def test_unset(capsys):
    _write({"roles": {"verifier": {"provider": "machx", "model": "m"}, "filer": {"model": "f"}},
            "output": {"max_tokens": 50000}, "keep": True})
    assert _cli(capsys, "unset", "roles.verifier.provider")[0] == 0
    assert read_settings()["roles"]["verifier"] == {"model": "m"}
    assert _cli(capsys, "unset", "roles.verifier.model")[0] == 0          # a role without a model is no role
    assert read_settings()["roles"] == {"filer": {"model": "f"}}
    assert _cli(capsys, "unset", "roles.filer")[0] == 0
    assert _cli(capsys, "unset", "output.max_tokens")[0] == 0
    saved = read_settings()
    assert "roles" not in saved and "output" not in saved and saved["keep"] is True
    code, out = _cli(capsys, "unset", "output.max_tokens")
    assert code == 0 and "not set" in out


def test_check(capsys, monkeypatch):
    _write({"roles": {"verifier": {"model": "checker-model"}}, "future": {}})
    monkeypatch.setenv("DREAM_MAX_TOKENS", "70000")
    code, out = _cli(capsys, "check")
    assert code == 0
    report = json.loads(out)
    assert report["ok"] is True and (report["provider"], report["model"]) == ("machx", None)
    assert "future" in report["unknown_sections"]
    assert "output.max_tokens" in report["environment_overrides"]
    assert report["roles"]["roles.verifier"] == {"value": {"model": "checker-model"}, "source": "global"}
    # Gate round 1 finding 1: a verifier role is not confirmed to run apart from the lead's one cached conversation.
    assert any(w.startswith("roles.verifier: not confirmed to run separately") and "/v1/models" in w
               for w in report["warnings"])


def test_check_warns_about_unwired_roles_and_roles_this_backend_ignores(capsys):
    _write({"roles": {"critic": {"model": "x"}, "main": {"provider": "machx", "model": "m"},
                      "subagents": {"default": {"model": "small-model"}}}})
    code, out = _cli(capsys, "check", "--provider", "xai")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True
    said = " | ".join(report["warnings"])
    assert "roles.critic" in said and "roles.main (in file)" in said
    assert "roles.subagents.default: not applied on this backend (xai)" in said
    code, out = _cli(capsys, "check")
    assert "roles.subagents.default" not in " | ".join(json.loads(out)["warnings"])     # machx applies it
    _write({"roles": {"verifier": {"model": ""}}})
    code, out = _cli(capsys, "check")
    assert code == 2 and json.loads(out)["ok"] is False


# --- TUI: /settings set|unset|check, /maxtokens --save --------------------------------------------------

def _app(backend_obj=None):
    from dream.tui.app import App
    app = App(provider="machx", model=LEAD)
    app.engine = SimpleNamespace(store=None, backend=backend_obj or SimpleNamespace(n_ctx=None),
                                 provider=get_provider("machx"), model=LEAD)
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app


def test_tui_set_vitals_applies_to_the_live_backend():
    live = SimpleNamespace(n_ctx=None, vitals=True)
    app = _app(live)
    asyncio.run(app._command("/settings set behaviour.vitals off"))
    assert live.vitals is False and read_settings()["behaviour"] == {"vitals": False}
    asyncio.run(app._command("/settings unset behaviour.vitals"))
    assert live.vitals is True and "behaviour" not in read_settings()


def test_tui_set_role_unset_and_check():
    app = _app()
    asyncio.run(app._command("/settings set roles.verifier.model checker-model"))
    assert read_settings()["roles"] == {"verifier": {"model": "checker-model"}}
    asyncio.run(app._command("/settings check"))
    asyncio.run(app._command("/settings set roles.critic.model x"))
    out = app.renderer.console.file.getvalue()
    assert "roles.critic" in out                             # the refusal names the key
    asyncio.run(app._command("/settings unset roles.verifier"))
    assert "roles" not in read_settings()


def test_maxtokens_save_persists_and_applies_now():
    app = _app()
    asyncio.run(app._command("/maxtokens 40000 --save"))
    assert config.MAX_OUTPUT_TOKENS_OVERRIDE == 40000 and config.MAX_OUTPUT_TOKENS == 40000
    assert read_settings()["output"] == {"max_tokens": 40000}
    out = app.renderer.console.file.getvalue()
    assert "saved" in out and "DREAM_MAX_TOKENS" not in out.split("saved", 1)[1].split("\n")[0]


def test_maxtokens_save_says_the_environment_still_wins(monkeypatch):
    monkeypatch.setenv("DREAM_MAX_TOKENS", "70000")
    app = _app()
    asyncio.run(app._command("/maxtokens 40000 --save"))
    assert read_settings()["output"] == {"max_tokens": 40000}
    assert "DREAM_MAX_TOKENS" in app.renderer.console.file.getvalue()


def test_maxtokens_without_save_writes_nothing():
    app = _app()
    asyncio.run(app._command("/maxtokens 40000"))
    assert not settings_path().exists()


def test_maxtokens_rejected_value_with_save_writes_nothing():
    app = _app()
    asyncio.run(app._command("/maxtokens 12 --save"))
    assert not settings_path().exists() and config.MAX_OUTPUT_TOKENS_OVERRIDE is None


def test_a_saved_ceiling_applies_when_a_session_starts(monkeypatch):
    _write({"output": {"max_tokens": 50000}})
    app = _app()

    class Stop(Exception):
        pass

    def boot():
        raise Stop
    monkeypatch.setattr(app, "_boot_engine", boot)
    with pytest.raises(Stop):
        asyncio.run(app.start())
    assert config.MAX_OUTPUT_TOKENS == 50000
    assert config.MAX_OUTPUT_TOKENS_OVERRIDE is None       # a preset's max_tokens still wins, as with the env


def test_the_environment_beats_a_saved_ceiling_at_start(monkeypatch):
    _write({"output": {"max_tokens": 50000}})
    monkeypatch.setenv("DREAM_MAX_TOKENS", "70000")
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 70000)
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 70000


# --- gate round 1, finding 2: a role without a provider is for the local MachX provider only -----------

@pytest.mark.parametrize("key,base_url,applies", [("machx", "http://engine.test/v1", True),
                                                  ("openai", "https://api.openai.test/v1", False),
                                                  ("xai", "https://api.x.test/v1", False)])
async def test_a_role_without_a_provider_applies_to_machx_only(key, base_url, applies):
    _write({"roles": {"subagents": {"default": {"model": "small-model"}}, "filer": {"model": "filing-model"}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}, {"text": "NOTHING"}]), key=key, base_url=base_url)
    await b._run_subagent("researcher", "look")
    await b._run_filer("the owner said something durable")
    models = [p["model"] for p in sent.sent]
    assert models == (["small-model", "filing-model"] if applies else [LEAD, LEAD])
    notes = settings.role_notes(key)
    if applies:
        assert notes == []
    else:                                    # said, not silent
        assert any(f"roles.subagents.default: not applied on this backend ({key})" in n for n in notes)
        assert any("roles.filer" in n and "machx only" in n for n in notes)


async def test_a_role_naming_the_cloud_provider_applies_there():
    _write({"roles": {"subagents": {"default": {"provider": "openai", "model": "gpt-small"}}}})
    b, sent = _sub_backend(FakeEngine(side=[{"text": "found it"}]), key="openai",
                           base_url="https://api.openai.test/v1")
    await b._run_subagent("researcher", "look")
    assert sent.sent[0]["model"] == "gpt-small"


async def test_the_connect_note_reaches_the_owner_at_the_first_request(monkeypatch):
    _write({"roles": {"subagents": {"default": {"model": "small-model"}}}})
    b = backend(FakeEngine(lead=[{"text": "ok"}]), model=LEAD, key="openai", base_url="https://api.openai.test/v1")
    engine = b._client.engine
    await b.connect()
    await b._client.aclose()
    b._client = Capture(engine)
    events = await turn(b, "hello")
    notes = [str(e.data) for e in events if e.kind == "system" and "roles.subagents.default" in str(e.data)]
    assert len(notes) == 1 and "not applied on this backend (openai)" in notes[0]
    events = await turn(b, "again")
    assert not [e for e in events if e.kind == "system" and "roles." in str(e.data)]     # once


# --- gate round 1, finding 3: backends that do not apply roles say so ------------------------------------

@pytest.mark.parametrize("provider", ["anthropic", "codex", "gemini", "grok"])
def test_roles_are_marked_not_applied_on_backends_that_do_not_read_them(provider):
    _write({"roles": {"verifier": {"model": "checker-model"}, "subagents": {"default": {"model": "small-model"}}}})
    rows = settings.effective(provider=provider)
    for key in ("roles.verifier", "roles.subagents.default"):
        assert rows[key].source == f"not applied on this backend ({provider})", key
    assert settings.effective(provider="machx")["roles.verifier"].source == "global"


def test_tui_settings_marks_roles_on_a_backend_that_does_not_apply_them():
    _write({"roles": {"verifier": {"model": "checker-model"}}})
    from dream.tui.app import App
    app = App(provider="anthropic", model="claude-x")
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None),
                                 provider=get_provider("anthropic"), model="claude-x")
    app.renderer.console = Console(file=io.StringIO(), width=220, force_terminal=False, color_system=None)
    asyncio.run(app._command("/settings"))
    line = next(ln for ln in app.renderer.console.file.getvalue().splitlines() if ln.startswith("roles.verifier"))
    assert "not applied on this backend (anthropic)" in line


# --- gate round 1, finding 6: an unwired role in the file is a warning, not a failed start ---------------

async def test_an_unwired_role_does_not_stop_connect_and_is_said(monkeypatch):
    _write({"roles": {"main": {"provider": "machx", "model": "mimo"}, "verifier": {"model": "checker-model"}},
            "behaviour": {"vitals": False}})
    b = backend(FakeEngine(lead=[{"text": "ok"}]), model=LEAD)
    engine = b._client.engine

    async def probe():
        return 65536
    monkeypatch.setattr(b, "_probe_n_ctx", probe)
    await b.connect()                                         # no raise
    await b._client.aclose()
    b._client = Capture(engine)
    assert b.vitals is False                                  # the rest of the file still applies
    assert b._role_model("verifier") == "checker-model"
    events = await turn(b, "hello")
    assert any("roles.main is not a role this version applies; it is ignored" in str(e.data)
               for e in events if e.kind == "system")
    rows = settings.effective()
    assert rows["roles.main (in file)"].source.startswith("not applied on this backend")
    assert rows["roles.main"].source == "default"


async def test_a_malformed_role_is_a_note_at_connect_and_fails_only_that_run(monkeypatch):
    _write({"roles": {"subagents": {"default": {"model": ""}}}})
    b, sent = _sub_backend(FakeEngine(lead=[{"text": "ok"}], side=[{"text": "found it"}]))
    engine = b._client.engine

    async def probe():
        return 65536
    monkeypatch.setattr(b, "_probe_n_ctx", probe)
    await b.connect()
    await b._client.aclose()
    b._client = sent
    assert b.vitals is True
    assert any("could not be used" in n for n in b._settings_notices)
    text, failed = await b._run_subagent("researcher", "look")
    assert failed and "roles" in text
