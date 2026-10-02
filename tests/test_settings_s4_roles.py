"""DREAM-145 S4 (settings design P3, 3.3): three more roles read their model from `roles` in runtime-settings.json,
through the same validated writer as the S1 roles -- the render critic (`/critique`, `roles.critic`), the loop
evaluator and prompt optimizer (`ReviewSettings.resolve`, `roles.evaluator`) and the ad-hoc code review (`/review`,
`roles.reviewer`) -- and the Claude SDK backend maps a sub-agent role's model alias when that role names provider
anthropic (`subagents()`, otherwise "inherit" as before). With no new role set every existing choice stays as it was.

No engine, no CLI, no socket: the critic's consult path and the reviewer backend are fakes; the /review test uses a
throwaway git repository.
"""
from __future__ import annotations

import io
import json
import subprocess
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import config, management
from dream.core import moe, settings
from dream.core.backends.base import Event
from dream.core.evaluator import ReviewSettings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import PROVIDERS, get_provider
from dream.tui import critique_cmd
from test_manual_render_critique import ANSWER, _app as _critic_app, _image, available, consults, var  # noqa: F401

CRITICS = {"anthropic", "codex", "gemini", "grok"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DREAM_EVALUATOR_PROVIDER", "DREAM_EVALUATOR_MODEL", "DREAM_EVALUATOR_TIMEOUT", "DREAM_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


# --- the writer: critic, evaluator, reviewer -----------------------------------------------------------------

@pytest.mark.parametrize("key, value, saved", [
    ("roles.critic.provider", "codex", {"critic": {"provider": "codex"}}),
    ("roles.critic.provider", "anthropic", {"critic": {"provider": "anthropic"}}),
    ("roles.evaluator.model", "judge-model", {"evaluator": {"model": "judge-model"}}),
    ("roles.evaluator.provider", "anthropic", {"evaluator": {"provider": "anthropic"}}),
    ("roles.evaluator.provider", "machx", {"evaluator": {"provider": "machx"}}),
    ("roles.reviewer.provider", "gemini", {"reviewer": {"provider": "gemini"}}),
    ("roles.reviewer.model", "review-model", {"reviewer": {"model": "review-model"}}),
])
def test_set_accepts_the_new_roles(capsys, key, value, saved):
    code, out = _cli(capsys, "set", key, value)
    assert code == 0, out
    assert read_settings()["roles"] == saved
    assert "applies to" in out


def test_a_critic_keeps_its_model_and_the_others_keep_both_fields(capsys):
    assert _cli(capsys, "set", "roles.critic.provider", "codex")[0] == 0
    assert _cli(capsys, "set", "roles.critic.model", "critic-model")[0] == 0
    assert _cli(capsys, "set", "roles.reviewer.provider", "codex")[0] == 0
    assert _cli(capsys, "set", "roles.reviewer.model", "review-model")[0] == 0
    assert read_settings()["roles"] == {"critic": {"provider": "codex", "model": "critic-model"},
                                        "reviewer": {"provider": "codex", "model": "review-model"}}


def test_a_sub_agent_role_may_name_anthropic_for_the_claude_sdk_but_the_verifier_may_not(capsys):
    assert _cli(capsys, "set", "roles.subagents.default.model", "haiku")[0] == 0
    assert _cli(capsys, "set", "roles.subagents.default.provider", "anthropic")[0] == 0
    assert read_settings()["roles"]["subagents"]["default"] == {"model": "haiku", "provider": "anthropic"}
    assert _cli(capsys, "set", "roles.verifier.model", "checker")[0] == 0
    code, out = _cli(capsys, "set", "roles.verifier.provider", "anthropic")
    assert code == 2 and "roles.verifier.provider" in out
    assert read_settings()["roles"]["verifier"] == {"model": "checker"}


@pytest.mark.parametrize("argv, needle", [
    (("set", "roles.critic.model", "x"), "roles.critic.provider"),          # a critic is named by its provider
    (("set", "roles.critic.provider", "machx"), "roles.critic.provider"),   # not a critic: /critique asks a CLI or Claude
    (("set", "roles.critic.provider", "openai"), "roles.critic.provider"),
    (("set", "roles.critic.provider", "nosuch"), "nosuch"),
    (("set", "roles.evaluator.provider", "nosuch"), "nosuch"),
    (("set", "roles.reviewer.provider", "nosuch"), "nosuch"),
    (("set", "roles.critic.effort", "high"), "roles.critic"),               # a role holds provider and model
    (("set", "roles.evaluator", "x"), "roles.evaluator"),
    (("set", "roles.main.provider", "openai"), "roles.main.provider"),      # main (S3, DREAM-144) is the local endpoint's
    (("set", "roles.summarizer.model", "x"), "roles.summarizer"),
])
def test_set_refuses_what_the_new_roles_cannot_hold(capsys, argv, needle):
    code, out = _cli(capsys, *argv)
    assert code == 2 and needle in out, out
    assert not settings_path().exists()                                      # a refusal writes nothing


def test_unset_removes_a_field_or_the_role(capsys):
    _write({"roles": {"critic": {"provider": "codex", "model": "x"}, "evaluator": {"provider": "codex", "model": "m"},
                      "reviewer": {"model": "r"}}, "keep": True})
    assert _cli(capsys, "unset", "roles.critic.model")[0] == 0
    assert read_settings()["roles"]["critic"] == {"provider": "codex"}
    assert _cli(capsys, "unset", "roles.critic.provider")[0] == 0           # a critic without a provider is no critic
    assert "critic" not in read_settings()["roles"]
    assert _cli(capsys, "unset", "roles.evaluator.provider")[0] == 0
    assert read_settings()["roles"]["evaluator"] == {"model": "m"}
    assert _cli(capsys, "unset", "roles.evaluator.model")[0] == 0           # the last field removes the role
    assert "evaluator" not in read_settings()["roles"]
    assert _cli(capsys, "unset", "roles.reviewer")[0] == 0
    saved = read_settings()
    assert "roles" not in saved and saved["keep"] is True
    code, out = _cli(capsys, "unset", "roles.critic")
    assert code == 0 and "not set" in out


def test_the_file_is_validated_as_a_whole(capsys):
    for bad in ({"critic": {"model": "x"}}, {"critic": {"provider": "machx"}}, {"evaluator": {}},
                {"reviewer": {"provider": "codex", "extra": 1}}, {"evaluator": {"model": ""}}):
        _write({"roles": bad})
        with pytest.raises(ValueError, match="roles\\." + next(iter(bad))):
            settings.read()
        code, out = _cli(capsys, "check")
        assert code == 2 and json.loads(out)["ok"] is False


# --- the view -------------------------------------------------------------------------------------------------

def test_the_view_shows_the_new_roles_with_their_defaults_and_sources(monkeypatch):
    rows = settings.effective()
    assert rows["roles.critic"].source == "default" and "claude" in str(rows["roles.critic"].value)
    assert rows["roles.evaluator"] == (settings.INHERIT, "default")
    assert rows["roles.reviewer"] == (settings.INHERIT, "default")
    _write({"roles": {"critic": {"provider": "gemini"}, "evaluator": {"model": "judge"}, "reviewer": {"provider": "codex"}}})
    for provider in ("machx", "openai", "anthropic", "codex"):
        rows = settings.effective(provider=provider)
        # The critic is a CLI consult and the evaluator/reviewer run their own backend: whatever the session runs on.
        assert rows["roles.critic"] == ({"provider": "gemini"}, "global"), provider
        assert rows["roles.evaluator"] == ({"model": "judge"}, "global"), provider
        assert rows["roles.reviewer"] == ({"provider": "codex"}, "global"), provider
        assert settings.role_notes(provider) == []
    assert settings.unknown_roles({"roles": {"critic": {}, "evaluator": {}, "reviewer": {}, "main": {},
                                             "summarizer": {}}}) == ["roles.summarizer"]   # main: S3 (DREAM-144)
    report = settings.check()
    assert report["ok"] and set(report["roles"]) == {"roles.critic", "roles.evaluator", "roles.reviewer"}
    monkeypatch.setenv("DREAM_EVALUATOR_PROVIDER", "openai")
    row = settings.effective()["roles.evaluator"]
    assert row.source == "env DREAM_EVALUATOR_PROVIDER" and row.value == {"provider": "openai", "model": "judge"}
    monkeypatch.setenv("DREAM_EVALUATOR_MODEL", "env-model")
    row = settings.effective()["roles.evaluator"]
    assert row.source == "env DREAM_EVALUATOR_PROVIDER, DREAM_EVALUATOR_MODEL"
    assert row.value == {"provider": "openai", "model": "env-model"}


def test_a_sub_agent_role_for_the_claude_sdk_is_applied_on_anthropic_and_says_so_elsewhere():
    _write({"roles": {"subagents": {"default": {"provider": "anthropic", "model": "haiku"}}}})
    assert settings.effective(provider="anthropic")["roles.subagents.default"].source == "global"
    assert settings.role_notes("anthropic") == []
    machx = settings.effective(provider="machx")["roles.subagents.default"].source
    assert machx.startswith(settings.NOT_APPLIED) and "anthropic" in machx
    with pytest.raises(ValueError, match="anthropic"):                       # the S1 rule: that run fails with a message
        settings.role_model("researcher", "machx")
    _write({"roles": {"subagents": {"default": {"model": "local-small"}}, "verifier": {"model": "checker"}}})
    rows = settings.effective(provider="anthropic")
    assert "machx only" in rows["roles.subagents.default"].source
    assert rows["roles.verifier"].source == f"{settings.NOT_APPLIED} (anthropic)"
    assert settings.role_notes("machx") == []
    assert settings.role_model("researcher", "machx") == "local-small"


# --- the Claude SDK: a sub-agent's model alias ----------------------------------------------------------------

def test_the_claude_sdk_maps_a_sub_agent_alias_only_when_the_role_names_anthropic():
    from dream.core.subagents import subagents
    before = subagents()
    assert before and all(agent.model == "inherit" for agent in before.values())
    _write({"roles": {"subagents": {"default": {"provider": "anthropic", "model": "haiku"},
                                    "researcher": {"provider": "anthropic", "model": "sonnet"},
                                    "coder": {"model": "local-small"},
                                    "explorer": {"provider": "machx", "model": "local-small"}}}})
    agents = subagents()
    assert agents["researcher"].model == "sonnet"
    assert agents["coder"].model == "inherit" and agents["explorer"].model == "inherit"
    others = set(agents) - {"researcher", "coder", "explorer"}
    assert others and all(agents[name].model == "haiku" for name in others)
    for name, agent in agents.items():        # nothing but the model changed
        assert (agent.description, agent.prompt, agent.tools) == (
            before[name].description, before[name].prompt, before[name].tools)
    assert settings.sdk_model("researcher") == "sonnet" and settings.sdk_model("coder") == "inherit"
    _write({"roles": {"subagents": {"default": {"model": ""}}}})              # an unusable file stops nothing
    assert all(agent.model == "inherit" for agent in subagents().values())


# --- the critic ------------------------------------------------------------------------------------------------

def test_the_critic_role_picks_the_default_critic(available):
    assert critique_cmd.default_cli() == "claude"
    _write({"roles": {"critic": {"provider": "gemini"}}})
    assert critique_cmd.default_cli() == "gemini"
    available["gemini"] = False
    assert critique_cmd.default_cli() == "gemini"     # the owner's choice; a missing CLI fails the consult visibly
    _write({"roles": {"critic": {"model": "x"}}})     # unusable: no silent fallback to "first available"
    with pytest.raises(ValueError, match="roles.critic"):
        critique_cmd.default_cli()


async def test_the_critic_role_s_model_is_asked_for_and_an_explicit_critic_keeps_its_own(tmp_path, var, consults, available):
    _write({"roles": {"critic": {"provider": "codex", "model": "critic-model"}}})
    app = _critic_app(tmp_path)
    _image(app.workspace / "renders" / "compare_car.png")
    await critique_cmd.command(app, "")
    assert consults[-1]["provider"] == "codex" and consults[-1]["model"] == "critic-model"
    assert app.asked and "codex" in app.asked[-1][0]
    await critique_cmd.command(app, "gemini")
    assert consults[-1]["provider"] == "gemini" and "model" not in consults[-1]
    # A Council advisor model for the same CLI yields to the role's; another CLI's Council model still applies.
    app.engine._moe = moe.MoeConfig("machx", ["codex", "gemini"],
                                    advisor_models={"codex": "council-model", "gemini": "council-gemini"})
    await critique_cmd.command(app, "codex")
    assert consults[-1]["model"] == "critic-model"
    await critique_cmd.command(app, "gemini")
    assert consults[-1]["model"] == "council-gemini"


async def test_an_unusable_settings_file_is_said_plainly_and_no_critic_is_asked(tmp_path, var, consults, available):
    _write({"roles": {"critic": {"model": "x"}}})
    app = _critic_app(tmp_path)
    _image(app.workspace / "renders" / "compare_car.png")
    await critique_cmd.command(app, "")
    await critique_cmd.command(app, "codex")
    assert consults == [] and app.asked == []
    assert len(app.shown) == 2 and all("roles.critic" in text and "Nothing was sent" in text for text in app.shown)


async def test_without_a_critic_role_the_first_available_is_asked_as_before(tmp_path, var, consults, available):
    available["anthropic"] = False
    app = _critic_app(tmp_path)
    _image(app.workspace / "renders" / "compare_car.png")
    await critique_cmd.command(app, "")
    assert consults[-1]["provider"] == "codex" and "model" not in consults[-1]
    assert consults.answer == ANSWER


# --- the evaluator ----------------------------------------------------------------------------------------------

def _worker(provider="machx", model="lead-model"):
    return SimpleNamespace(provider=get_provider(provider), model=model, profile=None, runtime_meter=None)


def test_the_evaluator_role_sits_between_the_environment_and_the_worker(monkeypatch):
    worker = _worker()
    chosen = ReviewSettings.resolve(worker)
    assert (chosen.provider.key, chosen.model) == ("machx", "lead-model")
    _write({"roles": {"evaluator": {"model": "judge-model"}}})
    chosen = ReviewSettings.resolve(worker)
    assert (chosen.provider.key, chosen.model) == ("machx", "judge-model")
    assert chosen.profile is not None
    _write({"roles": {"evaluator": {"provider": "openai"}}})
    chosen = ReviewSettings.resolve(worker)
    assert (chosen.provider.key, chosen.model) == ("openai", get_provider("openai").default_model)
    _write({"roles": {"evaluator": {"provider": "codex", "model": "judge-model"}}})
    chosen = ReviewSettings.resolve(worker)
    assert (chosen.provider.key, chosen.model) == ("codex", "judge-model")
    monkeypatch.setenv("DREAM_EVALUATOR_PROVIDER", "anthropic")
    chosen = ReviewSettings.resolve(worker)
    assert (chosen.provider.key, chosen.model) == ("anthropic", "judge-model")    # env provider; the role's model still applies
    monkeypatch.setenv("DREAM_EVALUATOR_MODEL", "env-model")
    assert ReviewSettings.resolve(worker).model == "env-model"
    explicit = ReviewSettings.resolve(worker, provider="xai", model="explicit")
    assert (explicit.provider.key, explicit.model) == ("xai", "explicit")
    # The reviewer role is read for /review only and knows no environment variables.
    _write({"roles": {"evaluator": {"provider": "codex", "model": "judge-model"},
                      "reviewer": {"provider": "gemini", "model": "review-model"}}})
    chosen = ReviewSettings.resolve(worker, role="reviewer")
    assert (chosen.provider.key, chosen.model) == ("gemini", "review-model")
    plain = ReviewSettings.resolve(worker, role=None)
    assert (plain.provider.key, plain.model) == ("machx", "lead-model")


def test_an_unusable_file_stops_the_evaluator_with_its_reason_not_a_fallback():
    _write({"roles": {"evaluator": {}}})
    with pytest.raises(ValueError, match="roles.evaluator"):
        ReviewSettings.resolve(_worker())


# --- the prompt optimizer (gate round 1, finding 1) ----------------------------------------------------------------

def _optimizer_app(tmp_path):
    from dream.tui.app import App
    app = App(provider="machx", model="lead", workspace=tmp_path)
    app.engine = SimpleNamespace(provider=get_provider("machx"), model="lead", profile=None, session_id="s-1",
                                 runtime_meter=None, store=None, backend=SimpleNamespace(n_ctx=None))
    return app


def _draft(app):
    return {"_source_session": "s-1", "_source_generation": 0, "_source_provider": "machx", "_source_model": "lead",
            "_source_profile": None, "files": [],
            "draft": {"workspace": str(app.workspace), "session_id": "s-1", "goal": "Summarise the notes"}}


async def test_the_prompt_optimizer_drafts_with_the_evaluator_role_and_reads_no_environment_variables(tmp_path, monkeypatch):
    from dream import prompt_optimizer
    seen = []

    async def fake_optimize(fields, files, workspace, review_settings):
        seen.append((fields, review_settings))
        return {"ok": True}
    monkeypatch.setattr(prompt_optimizer, "optimize", fake_optimize)
    monkeypatch.setenv("DREAM_EVALUATOR_PROVIDER", "openai")            # the loop's variables: not this path's
    monkeypatch.setenv("DREAM_EVALUATOR_MODEL", "env-model")
    app = _optimizer_app(tmp_path)
    assert await app._prepare_optimized_prompt(_draft(app)) == {"ok": True}
    fields, chosen = seen[-1]
    assert fields == {"goal": "Summarise the notes"}
    # No role: exactly the settings this path built before (the lead's provider and model, its profile, 120 s).
    assert chosen == ReviewSettings(provider=app.engine.provider, model="lead", profile=None, timeout=120.0)
    assert chosen.runtime_meter is None and chosen.mode is None

    _write({"roles": {"evaluator": {"provider": "openai", "model": "role-eval-model"}}})
    await app._prepare_optimized_prompt(_draft(app))
    chosen = seen[-1][1]
    assert (chosen.provider.key, chosen.model, chosen.timeout) == ("openai", "role-eval-model", 120.0)
    assert chosen.profile is not None
    _write({"roles": {"evaluator": {"model": "judge"}}})                # model only: on the lead's provider
    await app._prepare_optimized_prompt(_draft(app))
    assert (seen[-1][1].provider.key, seen[-1][1].model) == ("machx", "judge")
    _write({"roles": {"evaluator": {"provider": "openai"}}})            # provider only: its default model
    await app._prepare_optimized_prompt(_draft(app))
    assert (seen[-1][1].provider.key, seen[-1][1].model) == ("openai", get_provider("openai").default_model)
    _write({"roles": {"evaluator": {}}})                                 # unusable file: the optimization fails, named
    with pytest.raises(ValueError, match="roles.evaluator"):
        await app._prepare_optimized_prompt(_draft(app))
    assert len(seen) == 4


# --- /review ------------------------------------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=str(repo), capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(r, "add", "a.py")
    _git(r, "commit", "-qm", "init")
    (r / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    return r


def _review_app(workspace, provider="machx"):
    from dream.tui.app import App
    app = App(provider=provider, model="lead-model", workspace=workspace)
    asked = []

    async def ask(prompt):
        asked.append(prompt)
        yield Event("assistant_done", "NO FINDINGS")

    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None), provider=get_provider(provider),
                                 model="lead-model", profile=None, runtime_meter=None, ask=ask, session_id="s-1")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app, asked


class FakeReviewer:
    made: list = []

    def __init__(self, review_settings, tools, system_prompt, cwd):
        self.settings, self.tools, self.system_prompt, self.cwd = review_settings, tools, system_prompt, cwd
        self.prompts = []
        FakeReviewer.made.append(self)

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def ask(self, prompt):
        self.prompts.append(prompt)
        yield Event("assistant_done", "NO FINDINGS")


async def test_review_uses_the_reviewer_role_s_backend_when_set(repo, monkeypatch):
    from dream.core import evaluator
    FakeReviewer.made = []
    monkeypatch.setattr(evaluator, "review_backend", lambda s, tools, system, cwd, **kw: FakeReviewer(s, tools, system, cwd))
    app, asked = _review_app(repo)
    await app._review("")
    assert len(asked) == 1 and "return 2" in asked[0] and FakeReviewer.made == []      # no role: the lead reviews
    assert "found nothing" in app.renderer.console.file.getvalue().lower()

    _write({"roles": {"reviewer": {"provider": "codex", "model": "review-model"}}})
    await app._review("")
    assert len(asked) == 1                                                             # the lead was not asked
    reviewer, = FakeReviewer.made
    assert (reviewer.settings.provider.key, reviewer.settings.model) == ("codex", "review-model")
    assert reviewer.settings.timeout == 600.0 and reviewer.settings.mode == app.mode
    assert reviewer.tools == [] and str(reviewer.cwd) == str(repo)
    assert len(reviewer.prompts) == 1 and "return 2" in reviewer.prompts[0]
    out = app.renderer.console.file.getvalue()
    assert "codex" in out and "review-model" in out

    _write({"roles": {"reviewer": {"model": "review-model"}}})                         # model only: the session's provider
    await app._review("")
    assert FakeReviewer.made[-1].settings.provider.key == "machx" and FakeReviewer.made[-1].settings.model == "review-model"
    assert len(asked) == 1


async def test_review_with_an_unusable_file_is_unavailable_not_a_review(repo, monkeypatch):
    from dream.core import evaluator
    FakeReviewer.made = []
    monkeypatch.setattr(evaluator, "review_backend", lambda *a, **kw: FakeReviewer(*a))
    _write({"roles": {"reviewer": {}}})
    app, asked = _review_app(repo)
    await app._review("")
    assert asked == [] and FakeReviewer.made == []
    out = app.renderer.console.file.getvalue()
    assert "roles.reviewer" in out and "unavailable" in out.lower()


def test_the_settable_list_names_every_role(capsys):
    for key in ("roles.critic.provider", "roles.evaluator.model", "roles.reviewer.model"):
        assert any(key.rsplit(".", 1)[0] in entry for entry in settings.SETTABLE), key
    code, out = _cli(capsys, "set", "no.such.key", "1")
    assert code == 2 and "roles.critic" in out and "roles.evaluator" in out and "roles.reviewer" in out
    assert set(settings.ROLES) == {"verifier", "filer", "critic", "evaluator", "reviewer", "main"}   # main: S3 (DREAM-144)
    assert set(settings.role_providers("main")) == {"machx"}
    assert set(settings.role_providers("critic")) == CRITICS
    assert set(settings.role_providers("evaluator")) == set(PROVIDERS)
    assert set(settings.role_providers("verifier")) == {k for k, p in PROVIDERS.items() if p.kind == "openai"}
    assert set(settings.role_providers("subagents")) == {k for k, p in PROVIDERS.items() if p.kind == "openai"} | {"anthropic"}
