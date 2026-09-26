"""Every Dream setting in one view: the value in effect and where it came from (DREAM-142, settings design P3).

Sources, highest first: flag > env > session > project > global > default -- the design's order, with one exception
the runtime makes: a session command given after start (today `/maxtokens`) replaces what the environment set for
that setting, so a session value is reported above the environment. What a generation may actually use is a
separate row, `output.ceiling`: the smallest of the caps that apply (the profile's output_tokens, which
DREAM_MAX_TOKENS also sets, still bounds a session /maxtokens), with the source of the cap that binds.
`flag` and `project` are stubs for now: no row comes from a command-line flag (`--profile` arrives as DREAM_PROFILE
and is reported as env), and the project file (<workspace>/.dream/settings.json) is phase S5.

A role row that the session's backend does not apply says so ("not applied on this backend (<provider>)"): the
Anthropic SDK and the CLI backends do not read roles, and a role without a provider applies to MachX only.

Storage stays where it is (design 3.1): the global file is data/runtime-settings.json, read and written through
core/profiles (validation, lock, atomic write, unknown sections kept). This module owns its `roles`, `output` and
`behaviour` sections and validates them whenever it reads or writes them.
"""
from __future__ import annotations

import copy
import os
import re
from typing import Any, NamedTuple

from ..environment import NUMERIC_SETTINGS, read_numeric
from .profiles import (_OVERRIDE_FIELDS, MAX_OVERRIDE_NUMBER, PROFILE_ENVS, read_settings, resolve_profile,
                       settings_path, update_settings)
from .providers import PROVIDERS, get_provider

SOURCES = ("flag", "env", "session", "project", "global", "default")
NOT_APPLIED = "not applied on this backend"
INHERIT = "same as main"
MIN_MAX_TOKENS = 256
# Roles this version applies (design 3.3, phase S1): sub-agents (a default row and one per agent), the verifier
# and the filer. The design's other rows (main, critic, evaluator, reviewer, ...) arrive in later phases; a row
# for one of them would change nothing today, so it is refused rather than kept as a setting that does nothing.
_ROLES = ("verifier", "filer")
_AGENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
# Sections of the global file that someone reads; `check` lists any other as unknown (it is still kept).
_KNOWN_SECTIONS = {"version", "profile", "overrides", "models", "roles", "output", "behaviour", "blender", "studio"}


class Effective(NamedTuple):
    value: Any
    source: str


# --- the sections this module owns ------------------------------------------------------------------------

def _provider(value: object, label: str) -> str:
    if not isinstance(value, str) or value not in PROVIDERS:
        raise ValueError(f"{label} must be one of {', '.join(PROVIDERS)}, not {value!r}")
    if PROVIDERS[value].kind != "openai":
        raise ValueError(f"{label} must name an HTTP (OpenAI-compatible) provider, not {value!r}: sub-agents "
                         "send chat requests")
    return value


def _role(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object with a model")
    unknown = sorted(set(value) - {"provider", "model"})
    if unknown:
        raise ValueError(f"{label} has unknown keys ({', '.join(map(str, unknown))}); a role holds provider and model")
    model = value.get("model")
    if (not isinstance(model, str) or not model.strip() or model != model.strip() or len(model) > 256
            or any(ord(c) < 32 for c in model)):
        raise ValueError(f"{label}.model must be a model name")
    if "provider" in value:
        _provider(value["provider"], f"{label}.provider")
    return value


def _max_tokens(value: object) -> int:
    if type(value) is not int or not MIN_MAX_TOKENS <= value <= MAX_OVERRIDE_NUMBER:
        raise ValueError(f"output.max_tokens must be an integer from {MIN_MAX_TOKENS} to {MAX_OVERRIDE_NUMBER}")
    return value


def unknown_roles(raw: dict) -> list[str]:
    """Role rows this version does not apply (e.g. the design's roles.main): kept in the file and ignored, with
    a warning -- `set` refuses them, but one in the file must not stop a session from starting."""
    roles = raw.get("roles", {})
    return sorted(f"roles.{name}" for name in roles if name != "subagents" and name not in _ROLES) \
        if isinstance(roles, dict) else []


def _validate_behaviour(raw: dict) -> dict:
    behaviour = raw.get("behaviour", {})
    if not isinstance(behaviour, dict) or set(behaviour) - {"vitals"}:
        raise ValueError("behaviour must be an object holding only vitals")
    if "vitals" in behaviour and type(behaviour["vitals"]) is not bool:
        raise ValueError("behaviour.vitals must be true or false")
    return behaviour


def _validate_output(raw: dict) -> dict:
    output = raw.get("output", {})
    if not isinstance(output, dict) or set(output) - {"max_tokens"}:
        raise ValueError("output must be an object holding only max_tokens")
    if "max_tokens" in output:
        _max_tokens(output["max_tokens"])
    return output


def validate(raw: dict) -> dict:
    """Check `roles`, `output` and `behaviour`; the other sections are core/profiles' or kept as they are.
    Unknown role names are not errors (see unknown_roles)."""
    roles = raw.get("roles", {})
    if not isinstance(roles, dict):
        raise ValueError("roles must be an object")
    for name, value in roles.items():
        if name == "subagents":
            if not isinstance(value, dict):
                raise ValueError("roles.subagents must be an object")
            for agent, entry in value.items():
                if not _AGENT.match(agent):
                    raise ValueError(f"roles.subagents.{agent!r} is not an agent name")
                _role(entry, f"roles.subagents.{agent}")
        elif name in _ROLES:
            _role(value, f"roles.{name}")
    _validate_output(raw)
    _validate_behaviour(raw)
    return raw


def _checked(check, raw: dict) -> Any:
    try:
        return check(raw)
    except ValueError as exc:
        raise ValueError(f"Cannot use runtime settings {settings_path()}: {exc}; repair this file explicitly") from exc


def read() -> dict:
    """The global file, validated; a missing file is {}. An invalid file raises and is never rewritten."""
    return _checked(validate, read_settings())


# --- what the runtime asks -------------------------------------------------------------------------------

def _lookup(roles: dict, name: str) -> tuple[str, dict | None]:
    if name in _ROLES:
        return f"roles.{name}", roles.get(name)
    agents = roles.get("subagents", {})
    label = f"roles.subagents.{name}" if name in agents else "roles.subagents.default"
    return label, agents.get(name) or agents.get("default")


def _not_applied(entry: dict, provider: str) -> str | None:
    """Why a configured role does not apply on this session's provider, or None when it does."""
    spec = PROVIDERS.get(provider)
    if spec is None or spec.kind != "openai":
        return f"{NOT_APPLIED} ({provider})"
    other = entry.get("provider")
    if other is None and provider != "machx":
        return f"{NOT_APPLIED} ({provider}): a role without a provider applies to machx only"
    if other is not None and other != provider:
        return f"{NOT_APPLIED} ({provider}): it names {other}, so that run fails with a message"
    return None


def role_model(name: str, provider: str) -> str | None:
    """The model a configured role names for `name` (a sub-agent type, "verifier" or "filer"); None means the
    same as main. Sub-agents use their own row, else roles.subagents.default; the verifier and the filer have
    their own rows only. A role without a provider applies to the local MachX provider only: on a cloud provider
    it is ignored (role_notes says so at connect), so a local model name never reaches a cloud endpoint. A role
    naming another provider than the session's raises: S1 routes on the lead's endpoint."""
    label, entry = _lookup(read().get("roles", {}), name)
    if entry is None:
        return None
    other = entry.get("provider")
    if other is None and provider != "machx":
        return None
    if other and other != provider:
        raise ValueError(f"{label} names provider {other}, but this session runs on {provider}, and a role runs on "
                         f"the lead's endpoint in this version; set {label}.provider to {provider} or unset it")
    return entry["model"]


def role_notes(provider: str) -> list[str]:
    """What the owner should hear at connect about the roles in the file: rows this version ignores and rows
    this provider does not apply. Never raises for the roles section: a bad row is a note, not a failed start."""
    try:
        raw = read_settings()
        notes = [f"{key} is not a role this version applies; it is ignored" for key in unknown_roles(raw)]
        validate(raw)
    except ValueError as exc:
        return [f"Dream's own note: the roles in runtime settings could not be used: {exc}"]
    roles = raw.get("roles", {})
    configured = [(f"roles.subagents.{a}", e) for a, e in roles.get("subagents", {}).items()]
    configured += [(f"roles.{n}", roles[n]) for n in _ROLES if n in roles]
    notes += [f"{key}: {why}" for key, entry in configured if (why := _not_applied(entry, provider))]
    return [f"Dream's own note: {note}." for note in notes]


def vitals_enabled() -> bool:
    """behaviour.vitals: whether the local engine is asked for vital signs (`ie_vitals`); on by default. Reads
    only its own section, so a problem elsewhere in the file cannot stop a session from connecting."""
    return _checked(_validate_behaviour, read_settings()).get("vitals", True)


def apply_saved_output() -> None:
    """At session start: a saved output.max_tokens takes DREAM_MAX_TOKENS's place when that is not set (the
    environment wins). It is the same ceiling, so a local preset's max_tokens still comes first, as with the env."""
    if "DREAM_MAX_TOKENS" in os.environ:
        return
    value = _checked(_validate_output, read_settings()).get("max_tokens")
    if value is not None:
        from .. import config
        config.MAX_OUTPUT_TOKENS = value


# --- the view --------------------------------------------------------------------------------------------

def _env_row(name: str, value: Any) -> Effective:
    return Effective(value, f"env {name}")


def effective(*, provider: str = "machx", model: str | None = None, session: dict | None = None) -> dict[str, Effective]:
    """{key: (value, source)} for every setting this view covers, resolved for `provider`/`model`. `session`
    holds what a running session knows: "output.max_tokens" (/maxtokens), "output.preset_max_tokens" (the local
    model preset's), "output.performance" (the performance mode's output cap), "roles.main"; the CLI has none."""
    saved = read()
    session = session or {}
    env = os.environ
    rows: dict[str, Effective] = {}

    rows["profile"] = (_env_row("DREAM_PROFILE", env["DREAM_PROFILE"]) if "DREAM_PROFILE" in env
                       else Effective(saved["profile"], "global") if "profile" in saved
                       else Effective("auto", "default"))

    # The profile's fields: values from resolve_profile itself, so this table cannot drift from a session's.
    resolved = resolve_profile(get_provider(provider), model=model)
    field_envs = {field: name for field, (name, _) in PROFILE_ENVS.items()}
    field_envs.update(vision="DREAM_VISION", vision_helper="DREAM_VISION_HELPER")
    model_saved = saved.get("models", {}).get(f"{provider}:{model}", {}) if model else {}
    for field in sorted(_OVERRIDE_FIELDS):
        value = getattr(resolved, field)
        if field in field_envs and field_envs[field] in env:
            row = _env_row(field_envs[field], value)
        elif field in model_saved:
            row = Effective(value, f"global models[{provider}:{model}]")
        elif field in saved.get("overrides", {}):
            row = Effective(value, "global")
        else:
            row = Effective(value, "default")
        rows[f"overrides.{field}"] = row

    output = saved.get("output", {})
    rows["output.max_tokens"] = (
        Effective(session["output.max_tokens"], "session") if session.get("output.max_tokens") is not None
        else _env_row("DREAM_MAX_TOKENS", read_numeric("DREAM_MAX_TOKENS")) if "DREAM_MAX_TOKENS" in env
        else Effective(output["max_tokens"], "global") if "max_tokens" in output
        else Effective(int(NUMERIC_SETTINGS["DREAM_MAX_TOKENS"].default), "default"))

    # What one generation may use before the window clamp: openai_compat._max_tokens's order.
    configured = rows["output.max_tokens"]
    preset = session.get("output.preset_max_tokens")
    if preset is not None:
        ceiling = (configured if session.get("output.max_tokens") is not None
                   else Effective(preset, "global (local model preset)"))
    else:
        cap = rows["overrides.output_tokens"]
        ceiling = (Effective(cap.value, f"{cap.source} (profile output_tokens)") if cap.value < configured.value
                   else configured)
    performance = session.get("output.performance")
    if performance is not None and performance < ceiling.value:
        ceiling = Effective(performance, "session (performance mode)")
    rows["output.ceiling"] = ceiling

    behaviour = saved.get("behaviour", {})
    rows["behaviour.vitals"] = (Effective(behaviour["vitals"], "global") if "vitals" in behaviour
                                else Effective(True, "default"))
    rows["behaviour.auto_verify"] = (_env_row("DREAM_AUTO_VERIFY", env["DREAM_AUTO_VERIFY"] == "1")
                                     if "DREAM_AUTO_VERIFY" in env else Effective(True, "default"))
    rows["behaviour.single_slot_verifier"] = (
        _env_row("DREAM_SINGLE_SLOT_VERIFIER",
                 "continue" if env["DREAM_SINGLE_SLOT_VERIFIER"].strip().lower() == "continue" else "skip")
        if "DREAM_SINGLE_SLOT_VERIFIER" in env else Effective("skip", "default"))

    roles = saved.get("roles", {})
    agents = roles.get("subagents", {})
    rows["roles.main"] = (Effective(session["roles.main"], "session") if session.get("roles.main")
                          else Effective("chosen per session (--provider/--model or the picker)", "default"))

    def role_row(entry):
        return Effective(entry, _not_applied(entry, provider) or "global")
    rows["roles.subagents.default"] = (role_row(agents["default"]) if "default" in agents
                                       else Effective(INHERIT, "default"))
    for agent in sorted(set(agents) - {"default"}):
        rows[f"roles.subagents.{agent}"] = role_row(agents[agent])
    for name in _ROLES:
        rows[f"roles.{name}"] = role_row(roles[name]) if name in roles else Effective(INHERIT, "default")
    for key in unknown_roles(saved):
        row_key = key if key not in rows else f"{key} (in file)"     # roles.main: the session's row keeps its key
        rows[row_key] = Effective(roles[key.removeprefix("roles.")], f"{NOT_APPLIED}: not a role this version applies")

    for name in NUMERIC_SETTINGS:
        if name != "DREAM_MAX_TOKENS":    # output.max_tokens above
            rows["numeric." + name.removeprefix("DREAM_").lower()] = (
                _env_row(name, read_numeric(name)) if name in env else Effective(read_numeric(name), "default"))
    return rows


def paths() -> dict[str, str]:
    """Where each part of the settings lives (design 3.1: referenced, not moved)."""
    from .. import config, extensions
    from ..local.model_presets import Presets
    return {"global": str(settings_path()),
            "project": "not read yet (phase S5): <workspace>/.dream/settings.json",
            "model_presets": str(Presets().path),
            "council": str(config.VAR_DIR / "moe.json"),       # core/moe.py CONFIG_PATH
            "extensions": str(extensions.settings_path()),
            "mcp": str(config.MCP_CONFIG_PATH),
            "instructions": str(config.INSTRUCTIONS_FILE)}


# --- set / unset / check ---------------------------------------------------------------------------------

SETTABLE = ("output.max_tokens", "behaviour.vitals", "roles.subagents.<agent>.model|provider",
            "roles.verifier.model|provider", "roles.filer.model|provider")
_APPLIES = {"output": "new sessions (DREAM_MAX_TOKENS still wins); /maxtokens <n> changes the running one",
            "behaviour": "new sessions, and this one when set with /settings",
            "roles": "the next sub-agent, verifier or filer run"}


class _Unchanged(Exception):
    pass


def _role_key(key: str) -> tuple[tuple[str, ...], str | None]:
    """roles.subagents.<agent>[.field] / roles.<verifier|filer>[.field] -> (path under roles, field or None)."""
    parts = key.split(".")
    if len(parts) >= 2 and parts[1] == "subagents":
        if len(parts) not in (3, 4) or not _AGENT.match(parts[2]):
            raise ValueError(f"{key}: name one agent, e.g. roles.subagents.default.model")
        where, rest = ("subagents", parts[2]), parts[3:]
    elif len(parts) >= 2 and parts[1] in _ROLES:
        if len(parts) not in (2, 3):
            raise ValueError(f"{key} is not a role setting")
        where, rest = (parts[1],), parts[2:]
    else:
        role = ".".join(parts[:2])
        raise ValueError(f"{role} is not a role this version applies (subagents, verifier, filer)")
    field = rest[0] if rest else None
    if field not in (None, "model", "provider"):
        raise ValueError(f"{key}: a role holds model and provider")
    return where, field


def _parse(key: str, text: str) -> Any:
    if key == "output.max_tokens":
        digits = text.replace("_", "").replace(",", "")
        if not digits.isdigit():
            raise ValueError(f"output.max_tokens must be an integer from {MIN_MAX_TOKENS}, not {text!r}")
        return _max_tokens(int(digits))
    if key == "behaviour.vitals":
        words = {"on": True, "true": True, "1": True, "yes": True, "off": False, "false": False, "0": False, "no": False}
        if text.lower() not in words:
            raise ValueError(f"behaviour.vitals must be on or off, not {text!r}")
        return words[text.lower()]
    raise AssertionError(key)


def _refuse_other(key: str) -> None:
    if key == "profile" or key.startswith(("overrides.", "models.")):
        raise ValueError(f"{key}: use `dream profile` (or /profile) for the profile and its overrides")
    raise ValueError(f"{key} is not a setting `set` can change; settable: {', '.join(SETTABLE)}")


def set_value(key: str, text: str) -> str:
    """Save one setting through the runtime-settings writer; returns when it applies. Refused values write nothing."""
    if key in ("output.max_tokens", "behaviour.vitals"):
        section, name = key.split(".")
        value = _parse(key, text)

        def change(saved):
            saved = copy.deepcopy(saved)
            saved.setdefault(section, {})[name] = value
            return validate({**saved, "version": 1})
    elif key.startswith("roles."):
        where, field = _role_key(key)
        if field is None:
            raise ValueError(f"{key}: set {key}.model (and optionally {key}.provider)")
        prefix = ".".join(("roles", *where))
        if field == "provider":
            _provider(text, key)

        def change(saved):
            saved = copy.deepcopy(saved)
            parent = saved.setdefault("roles", {})
            for part in where[:-1]:
                parent = parent.setdefault(part, {})
            entry = parent.setdefault(where[-1], {})
            if field == "provider" and "model" not in entry:
                raise ValueError(f"set {prefix}.model first; a role without a model is no role")
            entry[field] = text
            return validate({**saved, "version": 1})
    else:
        _refuse_other(key)
    update_settings(change)
    return "applies to " + _APPLIES[key.split(".")[0]]


def unset_value(key: str) -> str:
    """Remove one setting (a role's model removes the role); an emptied section goes too."""
    if key in ("output.max_tokens", "behaviour.vitals"):
        section, name = key.split(".")
        trail = [(section,), (section, name)]
    elif key.startswith("roles."):
        where, field = _role_key(key)
        target = ("roles", *where) if field in (None, "model") else ("roles", *where, field)
        trail = [target[:i] for i in range(1, len(target) + 1)]
    else:
        _refuse_other(key)

    def change(saved):
        saved = copy.deepcopy(saved)
        chain = [saved]
        for step in trail:
            node = chain[-1]
            if not isinstance(node, dict) or step[-1] not in node:
                raise _Unchanged
            chain.append(node[step[-1]])
        del chain[-2][trail[-1][-1]]
        for depth in range(len(trail) - 1, 0, -1):     # drop parents the removal emptied
            if chain[depth] == {}:
                del chain[depth - 1][trail[depth - 1][-1]]
        return validate(saved)
    try:
        update_settings(change)
    except _Unchanged:
        return f"{key} was not set"
    return f"removed {key}; " + "applies to " + _APPLIES[key.split(".")[0]]


def check(*, provider: str = "machx", model: str | None = None) -> dict:
    """Is the file usable, what does it hold that nothing reads, which values does the environment override,
    which roles are set, and what to watch. Model names are not checked against the engine here (design 3.5 is
    phase S3)."""
    report: dict[str, Any] = {"ok": True, "file": str(settings_path()), "provider": provider, "model": model,
                              "errors": [], "warnings": []}
    try:
        saved = read()
        rows = effective(provider=provider, model=model)
    except ValueError as exc:
        report.update(ok=False, errors=[str(exc)])
        return report
    report["unknown_sections"] = sorted(set(saved) - _KNOWN_SECTIONS)
    report["environment_overrides"] = {key: row.source for key, row in rows.items() if row.source.startswith("env ")}
    report["roles"] = {key: {"value": row.value, "source": row.source} for key, row in rows.items()
                       if key.startswith("roles.") and row.source != "default" and key != "roles.main"}
    report["warnings"] += [f"{key}: {row['source']}" for key, row in report["roles"].items()
                           if row["source"].startswith(NOT_APPLIED)]
    if "verifier" in saved.get("roles", {}):
        report["warnings"].append(
            "roles.verifier: not confirmed to run separately. On an engine that caches one conversation the "
            "verifier keeps fix #71's skip (or DREAM_SINGLE_SLOT_VERIFIER=continue) unless the endpoint's "
            "/v1/models lists this model as its own entry beside another; that is checked when the verifier runs")
    report["not_checked"] = "model names against the engine's /v1/models (a later phase)"
    return report
