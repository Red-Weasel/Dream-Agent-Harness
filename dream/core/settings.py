"""Every Dream setting in one view: the value in effect and where it came from (DREAM-142, settings design P3).

Sources, highest first: flag > env > session > project > global > default -- the design's order, with one exception
the runtime makes: a session command given after start (today `/maxtokens`) replaces what the environment set for
that setting, so a session value is reported above the environment. What a generation may actually use is a
separate row, `output.ceiling`: the smallest of the caps that apply (the profile's output_tokens, which
DREAM_MAX_TOKENS also sets, still bounds a session /maxtokens), with the source of the cap that binds.
`flag` is a stub: no row comes from a command-line flag (`--profile` arrives as DREAM_PROFILE and is reported as
env).

The project file (DREAM-146, design 3.1): `<workspace>/.dream/settings.json`, the same shape as the global file,
holding `roles` (model names only: the provider comes from the global file), `output` and `behaviour`; any other
section or key is refused with its name. The workspace is the session's -- App.workspace (`--workspace`, the picker
or the desktop's choice, else Dream's own root), registered here by the App (set_workspace); `dream settings` takes
`--workspace` (default: the current directory). Every reader in this module sees the merge (per key: the project's
value over the global's). A corrupt or refused project file is never rewritten: the explicit commands (`dream
settings`, /settings, the Settings tab's project scope) refuse it naming the file and the key, and the runtime
readers ignore it as a whole -- Dream starts on the global values with a connect note naming it (role_notes).
Two rules protect the owner from a cloned repository's file: `.dream` must be a real directory of the workspace
(opened and used through its own descriptor, so a link -- even one swapped in during a read or write -- is never
followed), and the critic's, evaluator's and reviewer's model may come from a project file only when the global
file already sets that role's provider.

A role row that the session's backend does not apply says so ("not applied on this backend (<provider>)"): the CLI
backends do not read roles, the Anthropic SDK reads only a sub-agent role that names anthropic (DREAM-145), and a
role without a provider applies to MachX only. The critic, the evaluator and the reviewer (DREAM-145) run their own
consult or backend, so they apply whatever the session runs on.

Storage stays where it is (design 3.1): the global file is data/runtime-settings.json, read and written through
core/profiles (validation, lock, atomic write, unknown sections kept). This module owns its `roles`, `output`,
`behaviour` and `engine` sections and validates them whenever it reads or writes them. `engine` (DREAM-151: the
engine's parallel lanes, parallel and slot_ctx) is the global file's only and applies when Dream next starts the
local engine (local/settings.engine_lanes).
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, NamedTuple

from ..environment import NUMERIC_SETTINGS, read_numeric
from .profiles import (_OVERRIDE_FIELDS, _settings_lock, _validate_settings, MAX_OVERRIDE_NUMBER, MAX_SETTINGS_BYTES,
                       PROFILE_ENVS, read_settings, resolve_profile, settings_path, shown, shown_path,
                       update_settings)
from .providers import PROVIDERS, get_provider

SOURCES = ("flag", "env", "session", "project", "global", "default")
SCOPES = ("global", "project")
PROJECT_FILE = Path(".dream") / "settings.json"     # under the session's workspace
PROJECT_SECTIONS = {"version", "roles", "output", "behaviour"}   # what a project file may hold (design 3.1)
_WORKSPACE: Path | None = None                       # the session's workspace; None = no project file is read
NOT_APPLIED = "not applied on this backend"
INHERIT = "same as main"
MIN_MAX_TOKENS = 256
# The roles this version applies (design 3.3; S1 = DREAM-142, S4 = DREAM-145) and what each may hold:
# name -> (providers it may name, model required, provider required, why the provider list is what it is).
#   subagents.<agent>/default: model required; provider optional -- an HTTP provider (routed on the lead's endpoint)
#                              or anthropic (the Claude SDK maps the alias, sdk_model()).
#   verifier, filer: model required; provider optional, HTTP only (they send chat requests on the lead's endpoint).
#   critic: provider required, one of the critics /critique knows (claude = anthropic, codex, gemini, grok); model
#           optional (the program's own default model).
#   evaluator, reviewer: provider and model optional, at least one; any provider (review_backend takes every kind).
#   main (S3 = DREAM-144): model required; provider optional, machx only -- which served model Dream attaches to
#                          when the local endpoint lists several (machx.served_model_id); not routed at any seam.
_HTTP = tuple(key for key, spec in PROVIDERS.items() if spec.kind == "openai")
_CRITICS = tuple(key for key, spec in PROVIDERS.items() if spec.kind in ("anthropic", "cli"))
_RULES: dict[str, tuple[tuple[str, ...], bool, bool, str]] = {
    "subagents": (_HTTP + ("anthropic",), True, False,
                  "a sub-agent runs on the lead's HTTP endpoint, or on the Claude SDK when the role names anthropic"),
    "verifier": (_HTTP, True, False, "the verifier and the filer send chat requests on the lead's endpoint"),
    "filer": (_HTTP, True, False, "the verifier and the filer send chat requests on the lead's endpoint"),
    "critic": (_CRITICS, False, True, "/critique asks one of these programs"),
    "evaluator": (tuple(PROVIDERS), False, False, ""),
    "reviewer": (tuple(PROVIDERS), False, False, ""),
    "main": (("machx",), True, False, "roles.main names the served model Dream attaches to on the local MachX endpoint"),
}
ROLES = tuple(name for name in _RULES if name != "subagents")   # the roles with one row each
_ROUTED = ("verifier", "filer")             # roles the local backend routes on the lead's endpoint (role_model, S1)
_OWN = ("critic", "evaluator", "reviewer")  # roles that run their own consult or backend (role(), S4)
_MAIN = "main"                              # read at attach by machx.served_model_id (main_model(), S3)
CRITIC_DEFAULT = "first available critic (claude, codex, gemini, grok)"
EVALUATOR_ENVS = ("DREAM_EVALUATOR_PROVIDER", "DREAM_EVALUATOR_MODEL")   # read before roles.evaluator
_AGENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
# Sections of the global file that someone reads; `check` lists any other as unknown (it is still kept).
_KNOWN_SECTIONS = {"version", "profile", "overrides", "models", "roles", "output", "behaviour", "blender", "studio",
                   "engine", "nested"}
# DREAM-151: the engine's parallel lanes, used when Dream starts `ie serve` (local/settings.engine_lanes) for a model
# whose engine serves requests in lanes. The global file only: a project file may not set `engine` (PROJECT_SECTIONS).
# parallel = the engine's --parallel (1-16: DREAM-201, the engine's range since v0.2.0; its first lanes build took 4);
# slot_ctx = its --slot-ctx, the context of every lane after the first (0 = the engine's own size). Unset, each
# model's own launch setting stands, as before.
ENGINE_SETTINGS = ("engine.parallel", "engine.slot_ctx")
MAX_LANES = 16
MAX_SLOT_CTX = 2**31 - 1
ENGINE_PARALLEL_DEFAULT = "each model's own launch setting (1 unless changed)"
ENGINE_SLOT_CTX_DEFAULT = "the engine's own (32,768 tokens per extra lane; Qwen3.8-27B: 65,536)"
# DREAM-197: how many workers (the lead's sub-agents) one reply may start; its task calls past that are refused. The
# Nested view's 4 / 8 / 12 / 16 control counts the orchestrator, so the values are 3, 7, 11 and 15; unset, 7 (sixteen
# agents is the owner's opt-in). The global file only: a cloned repository's project file may not raise it.
NESTED_SETTING = "nested.max_workers"
MAX_WORKERS = (3, 7, 11, 15)
MAX_WORKERS_DEFAULT = 7
_MAX_WORKERS_TEXT = "nested.max_workers must be 3, 7, 11 or 15 (with the orchestrator: 4, 8, 12 or 16 agents)"
_NESTED_SHAPE_TEXT = "nested must be an object holding only max_workers"


class Effective(NamedTuple):
    value: Any
    source: str


# --- the sections this module owns ------------------------------------------------------------------------

def _provider(value: object, label: str, name: str) -> str:
    allowed, _, _, why = _RULES[name]
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(allowed)}, not {shown(repr(value))}"
                         + (f": {why}" if why else ""))
    return value


def _model_name(value: object, label: str) -> str:
    """The one rule for a model value from a settings file (DREAM-147): a string of printable text, 1 to 256
    characters, no space at either end. str.isprintable() refuses C0 and C1 controls, DEL, format characters (a
    bidi override, a zero-width space), line and paragraph separators and lone surrogates -- anything a terminal or
    a page could act on, or that no UTF-8 stream could encode. The value is shown escaped, never raw."""
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 256
            or not value.isprintable()):
        raise ValueError(f"{label}.model must be a model name (printable text, up to 256 characters, no space at "
                         f"either end), not {shown(repr(value))}")
    return value


def _role(value: object, label: str, name: str) -> dict:
    """One role entry against its rule (see _RULES); `name` is "subagents" or a name in ROLES."""
    _, model_required, provider_required, _ = _RULES[name]
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object holding provider and model")
    unknown = sorted(set(value) - {"provider", "model"})
    if unknown:
        raise ValueError(f"{label} has unknown keys ({', '.join(shown(k) for k in unknown)}); a role holds provider and model")
    if "model" in value or model_required:
        _model_name(value.get("model"), label)
    if "provider" in value or provider_required:
        _provider(value.get("provider"), f"{label}.provider", name)
    if not value:
        raise ValueError(f"set {label}.model or {label}.provider; an empty role is no role")
    return value


def role_providers(name: str) -> tuple[str, ...]:
    """The providers role `name` ("subagents" or a name in ROLES) may be set to."""
    return _RULES[name][0]


def role_rule(name: str) -> tuple[tuple[str, ...], bool, bool]:
    """(providers, model required, provider required) for role `name`."""
    return _RULES[name][:3]


def _max_tokens(value: object) -> int:
    if type(value) is not int or not MIN_MAX_TOKENS <= value <= MAX_OVERRIDE_NUMBER:
        raise ValueError(f"output.max_tokens must be an integer from {MIN_MAX_TOKENS} to {MAX_OVERRIDE_NUMBER}")
    return value


def _unknown_role_names(raw: dict) -> list[str]:
    roles = raw.get("roles", {})
    return sorted(name for name in roles if name not in _RULES) if isinstance(roles, dict) else []


def unknown_roles(raw: dict) -> list[str]:
    """Role rows this version does not apply (e.g. a roles.summarizer): kept in the file and ignored, with
    a warning -- `set` refuses them, but one in the file must not stop a session from starting. The keys are as
    they may be shown (profiles.shown, DREAM-147): a name from a file never reaches a terminal raw or unbounded."""
    return [f"roles.{shown(name)}" for name in _unknown_role_names(raw)]


# Context management (DREAM-175): what the lead and its sub-agents do when their context fills, and when.
CONTEXT_OVERFLOW = ("compact", "handoff", "stop")
SUBAGENT_OVERFLOW = ("compact", "handoff", "return")
CONTEXT_DEFAULTS = {"context_overflow": "compact", "subagent_overflow": "return", "context_trigger": 80,
                    "context_warning": 5}
CONTEXT_SETTINGS = tuple(f"behaviour.{name}" for name in CONTEXT_DEFAULTS)
# DREAM-181: how replies are worded (core/response_styles); read when a session's prompt is built.
RESPONSE_STYLES = ("default", "professional", "warm", "concise", "weasel-ee")
STYLE_SETTING = "behaviour.response_style"
_BEHAVIOUR_KEYS = {"vitals", "response_style", *CONTEXT_DEFAULTS}


def _overflow_word(value: object) -> object:
    """`error` is what behaviour.context_overflow's `stop` was called before DREAM-175: an old file keeps loading."""
    return "stop" if value == "error" else value


def _validate_behaviour(raw: dict) -> dict:
    behaviour = raw.get("behaviour", {})
    if not isinstance(behaviour, dict) or set(behaviour) - _BEHAVIOUR_KEYS:
        raise ValueError("behaviour must be an object holding only " + ", ".join(sorted(_BEHAVIOUR_KEYS)))
    if "vitals" in behaviour and type(behaviour["vitals"]) is not bool:
        raise ValueError("behaviour.vitals must be true or false")
    if "context_overflow" in behaviour and _overflow_word(behaviour["context_overflow"]) not in CONTEXT_OVERFLOW:
        raise ValueError("behaviour.context_overflow must be " + ", ".join(CONTEXT_OVERFLOW))
    if "response_style" in behaviour and behaviour["response_style"] not in RESPONSE_STYLES:
        raise ValueError("behaviour.response_style must be " + ", ".join(RESPONSE_STYLES))
    if "subagent_overflow" in behaviour and behaviour["subagent_overflow"] not in SUBAGENT_OVERFLOW:
        raise ValueError("behaviour.subagent_overflow must be " + ", ".join(SUBAGENT_OVERFLOW))
    for name, (low, high) in _CONTEXT_RANGES.items():
        if name in behaviour and (type(behaviour[name]) is not int or not low <= behaviour[name] <= high):
            raise ValueError(_CONTEXT_RANGE_TEXT[name])
    return behaviour


_CONTEXT_RANGES = {"context_trigger": (10, 95), "context_warning": (0, 50)}
_CONTEXT_RANGE_TEXT = {
    "context_trigger": "behaviour.context_trigger must be a whole percent of the context window from 10 to 95",
    "context_warning": "behaviour.context_warning must be a whole number of percentage points from 0 to 50 "
                       "(0 = no warning), below behaviour.context_trigger"}


def _check_context_pair(name: str, value: int, scope: str) -> None:
    """A warning at or past the trigger is refused when it is set (DREAM-175), against the values the session would
    use: this one with the other from the files as they are merged, the project file's over the global file's.
    Across files the pair is not a file error -- a file edited by hand keeps loading, and context_policy() then
    puts the warning just below the trigger."""
    merged = {**CONTEXT_DEFAULTS, **_own_section(_validate_behaviour)}
    if scope == "global" and _layer(layers(strict=False)[1], "behaviour", name) == "project":
        return          # the project file's value is the one used; the global one is only a fallback
    merged[name] = value
    if merged["context_warning"] and merged["context_warning"] >= merged["context_trigger"]:
        raise ValueError(f"behaviour.{name}: behaviour.context_warning ({merged['context_warning']}) must be below "
                         f"behaviour.context_trigger ({merged['context_trigger']}): it counts the points before "
                         "the trigger")


def _validate_output(raw: dict) -> dict:
    output = raw.get("output", {})
    if not isinstance(output, dict) or set(output) - {"max_tokens"}:
        raise ValueError("output must be an object holding only max_tokens")
    if "max_tokens" in output:
        _max_tokens(output["max_tokens"])
    return output


def _validate_engine(raw: dict) -> dict:
    engine = raw.get("engine", {})
    if not isinstance(engine, dict):
        raise ValueError("engine must be an object holding parallel and slot_ctx")
    unknown = sorted(set(engine) - {"parallel", "slot_ctx"})
    if unknown:
        raise ValueError(f"engine has unknown keys ({', '.join(shown(k) for k in unknown)}); it holds parallel and "
                         "slot_ctx")
    if "parallel" in engine and (type(engine["parallel"]) is not int or not 1 <= engine["parallel"] <= MAX_LANES):
        raise ValueError(f"engine.parallel must be an integer from 1 to {MAX_LANES} (the engine's --parallel)")
    slot_ctx = engine.get("slot_ctx", 0)
    if type(slot_ctx) is not int or not (slot_ctx == 0 or 9 <= slot_ctx <= MAX_SLOT_CTX):
        raise ValueError(f"engine.slot_ctx must be 0 (the engine's own size) or an integer from 9 to {MAX_SLOT_CTX}")
    return engine


def _validate_nested(raw: dict) -> dict:
    nested = raw.get("nested", {})
    if not isinstance(nested, dict) or set(nested) - {"max_workers"}:
        raise ValueError(_NESTED_SHAPE_TEXT)
    if "max_workers" in nested and (type(nested["max_workers"]) is not int or nested["max_workers"] not in MAX_WORKERS):
        raise ValueError(_MAX_WORKERS_TEXT)
    return nested


def validate(raw: dict) -> dict:
    """Check `roles`, `output`, `behaviour`, `engine` and `nested`; the other sections are core/profiles' or kept as
    they are. Unknown role names are not errors (see unknown_roles)."""
    roles = raw.get("roles", {})
    if not isinstance(roles, dict):
        raise ValueError("roles must be an object")
    for name, value in roles.items():
        if name == "subagents":
            if not isinstance(value, dict):
                raise ValueError("roles.subagents must be an object")
            for agent, entry in value.items():
                if not _AGENT.match(agent):
                    raise ValueError(f"roles.subagents.{shown(repr(agent))} is not an agent name")
                _role(entry, f"roles.subagents.{agent}", "subagents")
        elif name in ROLES:
            _role(value, f"roles.{name}", name)
    _validate_output(raw)
    _validate_behaviour(raw)
    _validate_engine(raw)
    _validate_nested(raw)
    return raw


def _checked(check, raw: dict, path: Path | None = None) -> Any:
    """`check(raw)`, an error naming the file it is about: the global file, or the project file at `path`."""
    try:
        return check(raw)
    except ValueError as exc:
        what = (f"project settings {shown_path(path)}" if path is not None
                else f"runtime settings {shown_path(settings_path())}")
        raise ValueError(f"Cannot use {what}: {exc}; repair this file explicitly") from exc


# --- the project file (DREAM-146) -------------------------------------------------------------------------

def set_workspace(path: str | os.PathLike | None) -> None:
    """Register the session's workspace: its `.dream/settings.json` is read from now on (None: none is)."""
    global _WORKSPACE
    _WORKSPACE = Path(path).expanduser().resolve() if path is not None else None


def workspace() -> Path | None:
    return _WORKSPACE


def project_path() -> Path | None:
    """<workspace>/.dream/settings.json, or None when no workspace is registered."""
    return _WORKSPACE / PROJECT_FILE if _WORKSPACE is not None else None


def _project_entries(roles: dict):
    """(label as it may be shown, agent name or None, entry) for every role entry in a roles section:
    roles.<name>, roles.subagents.<agent>."""
    for name, value in roles.items():
        if name == "subagents" and isinstance(value, dict):
            for agent, entry in value.items():
                yield f"roles.subagents.{shown(agent)}", agent, entry
        else:
            yield f"roles.{shown(name)}", None, value


def validate_project(raw: dict) -> dict:
    """A project file holds `roles` (model names only), `output` and `behaviour`; anything else -- a provider,
    the profile and its overrides, model presets, the engine, permissions, privacy -- is refused by name: those
    are the owner's global choices (design 3.1, the Codex rule). Unknown role names are kept and ignored, as in
    the global file (unknown_roles)."""
    if not isinstance(raw, dict):
        raise ValueError("expected a settings object")
    forbidden = sorted(set(raw) - PROJECT_SECTIONS)
    if forbidden:
        raise ValueError(f"a project file may not set {', '.join(shown(key) for key in forbidden)}; it holds roles "
                         "(model names only), output and behaviour -- everything else is set in the global file")
    roles = raw.get("roles", {})
    if not isinstance(roles, dict):
        raise ValueError("roles must be an object")
    if "subagents" in roles and not isinstance(roles["subagents"], dict):
        raise ValueError("roles.subagents must be an object")
    for label, agent, entry in _project_entries(roles):
        if not isinstance(entry, dict):
            raise ValueError(f"{label} must be an object holding model")
        extra = sorted(set(entry) - {"model"})
        if extra:
            raise ValueError(f"a project file may not set {', '.join(f'{label}.{shown(key)}' for key in extra)}; a "
                             "project file names models only, and the provider comes from the global file")
        if agent is not None and not _AGENT.match(agent):
            raise ValueError(f"roles.subagents.{shown(repr(agent))} is not an agent name")
        _model_name(entry.get("model"), label)
    _validate_output(raw)
    _validate_behaviour(raw)
    return raw


def _project_dir_fd(*, create: bool) -> int | None:
    """A descriptor for <workspace>/.dream, opened through the workspace's own descriptor with O_NOFOLLOW |
    O_DIRECTORY: what is opened is an entry of the workspace directory that is a directory and not a link, and
    every later open, temp file, replace and fsync happens relative to this descriptor (read_project,
    _write_project_file) -- so swapping .dream for a link while a read or write is under way (a background
    swapper in the workspace, gate round 2) redirects nothing. read_settings' O_NOFOLLOW guards the file itself
    only: a cloned repository can ship `.dream -> ../anywhere` (gate round 1). None when the folder (or the
    workspace) is absent and `create` is False; with `create` the folder is made first -- os.mkdir on the workspace
    descriptor fails on anything already there, a dangling link included, and what is there is then opened and
    checked, not followed. A link or a file in the folder's place raises, naming it. The caller closes the fd."""
    path = project_path()
    try:
        ws_fd = os.open(_WORKSPACE, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        if create:
            raise ValueError(f"Cannot use project settings {shown_path(path)}: the workspace "
                             f"{shown_path(_WORKSPACE)} is not a directory that can be opened ({exc.strerror})") from exc
        return None
    name = PROJECT_FILE.parent.name
    try:
        for _ in range(8):
            if create:
                try:
                    os.mkdir(name, dir_fd=ws_fd)
                except FileExistsError:
                    pass
            try:
                return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ws_fd)
            except FileNotFoundError:
                if not create:
                    return None
                continue        # gone between mkdir and open: something else is changing it; make and open again
            except OSError as exc:              # ELOOP: a link; ENOTDIR: a file
                raise ValueError(f"Cannot use project settings {shown_path(path)}: {shown_path(path.parent)} is a "
                                 f"symbolic link or not a real directory of the workspace {shown_path(_WORKSPACE)}; "
                                 "the project settings folder must be a plain directory named .dream inside the "
                                 "workspace, and nothing is read from or written through a link") from exc
        raise ValueError(f"Cannot use project settings {shown_path(path)}: {shown_path(path.parent)} kept changing "
                         "while it was being opened; nothing was saved -- retry when the workspace is quiet")
    finally:
        os.close(ws_fd)


def read_project() -> dict:
    """The registered workspace's project file, validated against the project rules; {} when there is no
    workspace or no file. Read by name through the checked .dream descriptor, never through a link; an invalid
    file raises, naming it, and is never rewritten."""
    path = project_path()
    if path is None:
        return {}
    dir_fd = _project_dir_fd(create=False)
    if dir_fd is None:
        return {}
    try:
        raw = read_settings(path, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return _checked(validate_project, raw, path)


def _write_project_file(change, dir_fd: int, path: Path) -> dict:
    """The project file's read-modify-write with every step relative to `dir_fd`, the checked .dream descriptor:
    the lock file, the read, the temp file (O_CREAT | O_EXCL), the replace (src_dir_fd/dst_dir_fd) and the fsync.
    A .dream swapped for a link meanwhile is a different directory entry; this descriptor still names the real
    one, so nothing lands outside the workspace. The same lock, validation, size cap and atomic replace as the
    global writer (core/profiles.update_settings, which is unchanged)."""
    if not isinstance(dir_fd, int):     # never a path walk: without the descriptor nothing is opened at all
        raise ValueError(f"Cannot use project settings {path}: the project folder is not open; nothing was saved")
    with _settings_lock(path, dir_fd=dir_fd):
        saved = read_settings(path, dir_fd=dir_fd)
        updated = _validate_settings(change(saved))
        data = (json.dumps(updated, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_SETTINGS_BYTES:
            raise ValueError(f"Project settings exceed {MAX_SETTINGS_BYTES} bytes; no changes saved")
        tmp = f".{path.name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
        finally:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
    return updated


def _cloud_roles(saved: dict, project: dict, path: Path) -> None:
    """The critic, the evaluator and the reviewer run on whatever provider their role names -- a cloud account
    included -- so a project file may name their model only when the global file already sets that role's
    provider: the owner, not a cloned repository, chooses whose account (and how expensive a model) a job runs on
    (gate finding 3). Sub-agents, the verifier and the filer stay as they are: they run on the lead's endpoint."""
    for name in _OWN:
        if name in project.get("roles", {}) and not saved.get("roles", {}).get(name, {}).get("provider"):
            raise ValueError(f"Cannot use project settings {shown_path(path)}: roles.{name}.model needs "
                             f"roles.{name}.provider in the global file {shown_path(settings_path())} (the provider "
                             "decides whose account the job runs on); set it there first")


def _merge(saved: dict, project: dict) -> dict:
    """The global file with the project's values over it, key by key: output and behaviour per field, a role per
    entry (the project's model over the global entry, whose provider stays)."""
    merged = copy.deepcopy(saved)
    for section in ("output", "behaviour"):
        if section in project:
            merged.setdefault(section, {}).update(project[section])
    if "roles" in project:
        roles = merged.setdefault("roles", {})
        for name, value in project["roles"].items():
            if name == "subagents":
                agents = roles.setdefault("subagents", {})
                for agent, entry in value.items():
                    agents[agent] = {**agents.get(agent, {}), **entry}
            else:
                roles[name] = {**roles.get(name, {}), **value}
    return merged


def layers(*, strict: bool = True) -> tuple[dict, dict, dict]:
    """(global, project, merged), each validated; the merge is checked with the ordinary rules and an error there
    names the project file (the global file alone passed): e.g. a project roles.critic.model with no global
    roles.critic.provider. `strict` False is the runtime's reading (gate round 2, decision 2): a project file that
    cannot be used -- a linked .dream, a refused key, corrupt JSON, a cloud role without its global provider -- is
    ignored as a whole (project {} and merged = global) and project_problem() says why. An unusable global file
    raises either way."""
    saved = _checked(validate, read_settings())
    try:
        project = read_project()
        if not project:
            return saved, {}, saved
        _cloud_roles(saved, project, project_path())
        merged = _checked(validate, _merge(saved, project), project_path())
    except ValueError:
        if strict:
            raise
        return saved, {}, saved
    return saved, project, merged


def read() -> dict:
    """The settings in effect from the files: the global file with the project file's values over it, validated;
    no file is {}. An invalid file raises, naming it, and is never rewritten -- the explicit commands' reading
    (`dream settings`, /settings, the Settings tab's project scope)."""
    return layers()[2]


def read_runtime() -> dict:
    """The runtime readers' view (role_model, role, sdk_model, main_model): the same merge, but an unusable
    project file is ignored as a whole instead of stopping Dream from starting; role_notes carries the note."""
    return layers(strict=False)[2]


def read_global() -> dict:
    """The global file alone, validated -- what a global-scope save must find usable."""
    return _checked(validate, read_settings())


def project_problem() -> str | None:
    """Why the project file is ignored right now (the text after "Cannot use project settings <path>: "), or
    None when it is usable or absent. A problem in the global file is not reported here: read() names that."""
    try:
        layers()
    except ValueError as exc:
        path = project_path()
        if path is not None and shown_path(path) in str(exc):
            return str(exc).split(f"{shown_path(path)}:", 1)[-1].strip()
    return None


# --- what the runtime asks -------------------------------------------------------------------------------

def _lookup(roles: dict, name: str) -> tuple[str, dict | None]:
    if name in _ROUTED:
        return f"roles.{name}", roles.get(name)
    agents = roles.get("subagents", {})
    label = f"roles.subagents.{name}" if name in agents else "roles.subagents.default"
    return label, agents.get(name) or agents.get("default")


def _not_applied(entry: dict, provider: str, name: str = "subagents") -> str | None:
    """Why a configured role does not apply on this session's provider, or None when it does. `name` is
    "subagents" or a name in ROLES: the critic, the evaluator and the reviewer run their own consult or backend,
    so they apply whatever the session runs on; on the Anthropic SDK a sub-agent role applies when it names
    anthropic (sdk_model)."""
    if name in _OWN:
        return None
    spec = PROVIDERS.get(provider)
    other = entry.get("provider")
    if provider == "anthropic" and name == "subagents":
        if other == "anthropic":
            return None
        if other is None:
            return f"{NOT_APPLIED} ({provider}): a role without a provider applies to machx only"
        return f"{NOT_APPLIED} ({provider}): it names {other}; the Claude SDK maps a sub-agent role only when it names anthropic"
    if spec is None or spec.kind != "openai":
        return f"{NOT_APPLIED} ({provider})"
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
    label, entry = _lookup(read_runtime().get("roles", {}), name)
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
    notes = []
    problem = project_problem()
    if problem:      # the project file is ignored as a whole; the session runs on the global values
        notes.append(f"the project settings file {shown_path(project_path())} could not be used and is ignored: "
                     f"{problem}")
    try:
        raw = read_runtime()
        notes += [f"{key} is not a role this version applies; it is ignored" for key in unknown_roles(raw)]
    except ValueError as exc:
        return ([f"Dream's own note: {note}." for note in notes]
                + [f"Dream's own note: the roles in runtime settings could not be used: {exc}"])
    roles = raw.get("roles", {})
    configured = [(f"roles.subagents.{a}", e, "subagents") for a, e in roles.get("subagents", {}).items()]
    configured += [(f"roles.{n}", roles[n], n) for n in ROLES if n in roles]
    notes += [f"{key}: {why}" for key, entry, name in configured if (why := _not_applied(entry, provider, name))]
    return [f"Dream's own note: {note}." for note in notes]


def role(name: str) -> dict | None:
    """The configured entry for a role that runs its own consult or backend -- "critic" ({"provider", "model"?}),
    "evaluator" or "reviewer" ({"provider"?, "model"?}) -- or None for the job's own default. An unusable file
    raises, as read() does: the caller says so and nothing falls back to another choice."""
    if name not in _OWN:
        raise ValueError(f"{name} is not a role with its own consult or backend ({', '.join(_OWN)})")
    return read_runtime().get("roles", {}).get(name)


def sdk_model(name: str) -> str:
    """The Claude SDK's model for sub-agent `name` (core/subagents.subagents): the role's model alias when
    roles.subagents.<name>, else roles.subagents.default, names provider anthropic; otherwise "inherit", the
    lead's model, exactly as before. A role without a provider is MachX-only (S1) and one naming another provider
    is not the SDK's. Never raises: an unusable file leaves every agent on "inherit" (the view, `check` and the
    Settings tab report the file)."""
    try:
        agents = read_runtime().get("roles", {}).get("subagents", {})
    except ValueError:
        return "inherit"
    entry = agents.get(name) or agents.get("default")
    if entry and entry.get("provider") == "anthropic":
        return entry["model"]
    return "inherit"


def main_model() -> str | None:
    """roles.main.model (DREAM-144): the served model Dream attaches to when the local endpoint lists several;
    None when unset. The rule admits provider machx only, so a row is the local endpoint's or refused."""
    entry = read_runtime().get("roles", {}).get(_MAIN)
    return entry["model"] if entry else None


def _own_section(check) -> dict:
    """One section (`check` validates it) from the global file with the project file's over it. Of the global
    file only that section is read, so a problem elsewhere in it cannot stop a session from connecting (today's
    rule). The project file counts only when it is usable as a whole with the global file (layers): a refused
    project file -- a forbidden section, a provider, a linked folder -- contributes nothing, and the session starts
    on the global values with role_notes' note (gate round 2, decision 2)."""
    section = dict(_checked(check, read_settings()))
    try:
        project = layers()[1]
    except ValueError:
        project = {}
    section.update(check(project))
    return section


def context_policy() -> dict:
    """The context-management settings (DREAM-175) as the session uses them: every key of CONTEXT_DEFAULTS, the
    project file's over the global file's over the defaults; an old `error` reads as `stop`. The trigger and the
    warning are checked together once the layers are merged: a pair that does not fit falls back to the defaults'
    points before the trigger rather than warning at or past it."""
    policy = {**CONTEXT_DEFAULTS, **_own_section(_validate_behaviour)}
    policy = {key: policy[key] for key in CONTEXT_DEFAULTS}
    policy["context_overflow"] = _overflow_word(policy["context_overflow"])
    if policy["context_warning"] >= policy["context_trigger"]:
        policy["context_warning"] = min(CONTEXT_DEFAULTS["context_warning"], policy["context_trigger"] - 1)
    return policy


def response_style() -> str:
    """behaviour.response_style (DREAM-181), the project file's over the global file's; `default` when unset or when
    the files cannot be read (a session still starts)."""
    try:
        return _own_section(_validate_behaviour).get("response_style", "default")
    except ValueError:
        return "default"


def vitals_enabled() -> bool:
    """behaviour.vitals: whether the local engine is asked for vital signs (`ie_vitals`); on by default."""
    return _own_section(_validate_behaviour).get("vitals", True)


def engine_launch() -> dict:
    """The owner's engine lanes for the next `ie serve` Dream starts (DREAM-151): {"parallel"?: n, "slot_ctx"?: c},
    {} when neither is set. The global file only -- a project file may not set the engine -- and of it only this
    section, so a problem elsewhere in the file does not stop a model from loading. An unusable engine section
    raises, naming the file; nothing falls back to another value."""
    return dict(_checked(_validate_engine, read_settings()))


def nested_max_workers() -> int:
    """nested.max_workers (DREAM-197): how many workers one reply of the lead may start, 7 when unset. The global file
    only, and of it only this section; an unusable section raises, naming the file."""
    return _checked(_validate_nested, read_settings()).get("max_workers", MAX_WORKERS_DEFAULT)


def apply_saved_output() -> None:
    """At session start: a saved output.max_tokens (the project file's, else the global's) takes DREAM_MAX_TOKENS's
    place when that is not set (the environment wins). It is the same ceiling, so a local preset's max_tokens still
    comes first, as with the env."""
    if "DREAM_MAX_TOKENS" in os.environ:
        return
    value = _own_section(_validate_output).get("max_tokens")
    if value is not None:
        from .. import config
        config.MAX_OUTPUT_TOKENS = value


# --- the view --------------------------------------------------------------------------------------------

def _env_row(name: str, value: Any) -> Effective:
    return Effective(value, f"env {name}")


def _layer(project: dict, *path: str) -> str:
    """"project" when the project file holds the entry at `path`, else "global"."""
    node: Any = project
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return "global"
        node = node[part]
    return "project"


def _evaluator_row(entry: dict | None, env, layer: str) -> Effective:
    """roles.evaluator as evaluator.ReviewSettings.resolve reads it: DREAM_EVALUATOR_PROVIDER and _MODEL first,
    field by field, then the role (from the file `layer`), then the worker (INHERIT)."""
    names = [name for name in EVALUATOR_ENVS if name in env]
    if not names:
        return Effective(entry, layer) if entry else Effective(INHERIT, "default")
    value = dict(entry or {})
    if "DREAM_EVALUATOR_PROVIDER" in env:
        value["provider"] = env["DREAM_EVALUATOR_PROVIDER"]
    if "DREAM_EVALUATOR_MODEL" in env:
        value["model"] = env["DREAM_EVALUATOR_MODEL"]
    return Effective(value, "env " + ", ".join(names))


def effective(*, provider: str = "machx", model: str | None = None, session: dict | None = None,
              strict: bool = True) -> dict[str, Effective]:
    """{key: (value, source)} for every setting this view covers, resolved for `provider`/`model`. `session`
    holds what a running session knows: "output.max_tokens" (/maxtokens), "output.preset_max_tokens" (the local
    model preset's), "output.performance" (the performance mode's output cap), "roles.main"; the CLI has none.
    A value the project file sets has source "project" (DREAM-146); the global file's, "global". `strict` False
    (the Settings tab) shows the global values when the project file is unusable, as the runtime does."""
    _, project, saved = layers(strict=strict)
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
        else Effective(output["max_tokens"], _layer(project, "output", "max_tokens")) if "max_tokens" in output
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
    rows["behaviour.vitals"] = (Effective(behaviour["vitals"], _layer(project, "behaviour", "vitals"))
                                if "vitals" in behaviour else Effective(True, "default"))
    for name, default in CONTEXT_DEFAULTS.items():
        value = _overflow_word(behaviour[name]) if name in behaviour else default
        rows[f"behaviour.{name}"] = (Effective(value, _layer(project, "behaviour", name)) if name in behaviour
                                     else Effective(default, "default"))
    rows[STYLE_SETTING] = (Effective(behaviour["response_style"], _layer(project, "behaviour", "response_style"))
                           if "response_style" in behaviour else Effective("default", "default"))
    trigger, warning = rows["behaviour.context_trigger"].value, rows["behaviour.context_warning"].value
    if warning and warning >= trigger:       # a pair edited by hand: the value the session uses (context_policy)
        rows["behaviour.context_warning"] = Effective(min(CONTEXT_DEFAULTS["context_warning"], trigger - 1),
                                                      rows["behaviour.context_warning"].source
                                                      + f" ({warning} is not below the trigger; moved below it)")
    if "DREAM_COMPACT_AT" in env:
        try:        # a fraction of the window in the variable, a percent in the row
            shown_at = round(100 * float(env["DREAM_COMPACT_AT"]))
        except (ValueError, OverflowError):
            shown_at = env["DREAM_COMPACT_AT"]
        rows["behaviour.context_trigger"] = _env_row("DREAM_COMPACT_AT", shown_at)
    rows["behaviour.auto_verify"] = (_env_row("DREAM_AUTO_VERIFY", env["DREAM_AUTO_VERIFY"] == "1")
                                     if "DREAM_AUTO_VERIFY" in env else Effective(True, "default"))
    rows["behaviour.single_slot_verifier"] = (
        _env_row("DREAM_SINGLE_SLOT_VERIFIER",
                 "continue" if env["DREAM_SINGLE_SLOT_VERIFIER"].strip().lower() == "continue" else "skip")
        if "DREAM_SINGLE_SLOT_VERIFIER" in env else Effective("skip", "default"))

    # DREAM-151: always the global file's (a project file may not set the engine).
    engine = saved.get("engine", {})
    rows["engine.parallel"] = (Effective(engine["parallel"], "global") if "parallel" in engine
                               else Effective(ENGINE_PARALLEL_DEFAULT, "default"))
    rows["engine.slot_ctx"] = (Effective(engine["slot_ctx"], "global") if "slot_ctx" in engine
                               else Effective(ENGINE_SLOT_CTX_DEFAULT, "default"))
    # DREAM-197: the global file's too (a project file may not set it).
    nested = saved.get("nested", {})
    rows[NESTED_SETTING] = (Effective(nested["max_workers"], "global") if "max_workers" in nested
                            else Effective(MAX_WORKERS_DEFAULT, "default"))

    roles = saved.get("roles", {})
    agents = roles.get("subagents", {})
    def role_row(entry, name="subagents", agent=None):
        layer = _layer(project, "roles", *(("subagents", agent) if agent else (name,)))
        return Effective(entry, _not_applied(entry, provider, name) or layer)
    # The running session's choice first; else the saved roles.main (what the next attach to a local endpoint
    # listing several models picks, DREAM-144); else the picker's.
    rows["roles.main"] = (Effective(session["roles.main"], "session") if session.get("roles.main")
                          else role_row(roles[_MAIN], _MAIN) if _MAIN in roles
                          else Effective("chosen per session (--provider/--model or the picker)", "default"))
    rows["roles.subagents.default"] = (role_row(agents["default"], agent="default") if "default" in agents
                                       else Effective(INHERIT, "default"))
    for agent in sorted(set(agents) - {"default"}):
        rows[f"roles.subagents.{agent}"] = role_row(agents[agent], agent=agent)
    for name in _ROUTED:
        rows[f"roles.{name}"] = role_row(roles[name], name) if name in roles else Effective(INHERIT, "default")
    rows["roles.critic"] = role_row(roles["critic"], "critic") if "critic" in roles else Effective(CRITIC_DEFAULT, "default")
    rows["roles.evaluator"] = _evaluator_row(roles.get("evaluator"), env, _layer(project, "roles", "evaluator"))
    rows["roles.reviewer"] = role_row(roles["reviewer"], "reviewer") if "reviewer" in roles else Effective(INHERIT, "default")
    unknown_source = f"{NOT_APPLIED}: not a role this version applies"
    for name in _unknown_role_names(saved):
        key = f"roles.{shown(name)}"          # the row key as it may be shown: never a raw or unbounded name
        if key in rows and rows[key].source != unknown_source:
            key = f"{key} (in file)"          # a name that is already a real row keeps that row
        row_key, n = key, 2
        while row_key in rows:                # two long names cut to the same 80-character prefix (DREAM-147)
            row_key, n = f"{key} ({n})", n + 1
        rows[row_key] = Effective(roles[name], unknown_source)

    for name in NUMERIC_SETTINGS:
        if name != "DREAM_MAX_TOKENS":    # output.max_tokens above
            rows["numeric." + name.removeprefix("DREAM_").lower()] = (
                _env_row(name, read_numeric(name)) if name in env else Effective(read_numeric(name), "default"))
    return rows


def paths() -> dict[str, str]:
    """Where each part of the settings lives (design 3.1: referenced, not moved)."""
    from .. import config, extensions
    from ..local.model_presets import Presets
    project = project_path()
    return {"global": str(settings_path()),
            "project": (str(project) if project is not None else
                        "no workspace registered: <workspace>/.dream/settings.json (dream settings --workspace <dir>)"),
            "model_presets": str(Presets().path),
            "council": str(config.VAR_DIR / "moe.json"),       # core/moe.py CONFIG_PATH
            "extensions": str(extensions.settings_path()),
            "mcp": str(config.MCP_CONFIG_PATH),
            "instructions": str(config.INSTRUCTIONS_FILE)}


# --- set / unset / check ---------------------------------------------------------------------------------

SETTABLE = ("output.max_tokens", "behaviour.vitals", STYLE_SETTING, *CONTEXT_SETTINGS, "roles.main.model|provider",
            "roles.subagents.<agent>.model|provider",
            "roles.verifier.model|provider", "roles.filer.model|provider", "roles.critic.provider|model",
            "roles.evaluator.model|provider", "roles.reviewer.model|provider", *ENGINE_SETTINGS, NESTED_SETTING)
_APPLIES = {"output": "the next Dream start (DREAM_MAX_TOKENS still wins); /maxtokens <n> changes the running one",
            "nested": "new sessions, and the running session from its next reply when set in Studio or with /settings; "
                      "the local engine's queue (--max-queue) from the engine's next start, and a running engine keeps "
                      "its queue",
            "behaviour": "new sessions, and this one when set with /settings",
            "roles": "the next run of that job: a sub-agent, the verifier or the filer, /critique, the loop's "
                     "evaluator, the prompt optimizer or /review",
            "roles.main": "the next time Dream attaches to a local endpoint that lists several models",
            "engine": "the next time Dream starts the local engine (a model loaded from the picker or the desktop); "
                      "a running engine keeps its lanes"}


def _applies(key: str) -> str:
    if key == STYLE_SETTING:
        return "new sessions (/new starts one); the running session keeps its prompt"
    return _APPLIES["roles.main" if key.startswith("roles.main") else key.split(".")[0]]


class _Unchanged(Exception):
    pass


def _role_key(key: str) -> tuple[tuple[str, ...], str | None]:
    """roles.subagents.<agent>[.field] / roles.<verifier|filer>[.field] -> (path under roles, field or None)."""
    parts = key.split(".")
    if len(parts) >= 2 and parts[1] == "subagents":
        if len(parts) not in (3, 4) or not _AGENT.match(parts[2]):
            raise ValueError(f"{key}: name one agent, e.g. roles.subagents.default.model")
        where, rest = ("subagents", parts[2]), parts[3:]
    elif len(parts) >= 2 and parts[1] in ROLES:
        if len(parts) not in (2, 3):
            raise ValueError(f"{key} is not a role setting")
        where, rest = (parts[1],), parts[2:]
    else:
        role = ".".join(parts[:2])
        raise ValueError(f"{role} is not a role this version applies (subagents, {', '.join(ROLES)})")
    field = rest[0] if rest else None
    if field not in (None, "model", "provider"):
        raise ValueError(f"{key}: a role holds model and provider")
    return where, field


def _parse(key: str, text: str) -> Any:
    if key in ENGINE_SETTINGS:
        digits = text.strip().replace("_", "").replace(",", "")
        if not digits.isdigit():
            raise ValueError(f"{key} must be a whole number, not {text!r}")
        value = int(digits)
        _validate_engine({"engine": {key.split(".")[1]: value}})
        return value
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
    if key == STYLE_SETTING:
        word = text.strip().lower()
        _validate_behaviour({"behaviour": {"response_style": word}})
        return word
    if key in CONTEXT_SETTINGS:
        name = key.split(".")[1]
        if name in ("context_overflow", "subagent_overflow"):
            word = text.strip().lower()
            value = _overflow_word(word) if name == "context_overflow" else word
        else:
            digits = text.strip().rstrip("%").strip()
            if not (digits.isascii() and digits.isdigit()):
                raise ValueError(_CONTEXT_RANGE_TEXT[name])
            value = int(digits)
        _validate_behaviour({"behaviour": {name: value}})
        return value
    if key == NESTED_SETTING:
        digits = text.strip()
        if not (digits.isascii() and digits.isdigit()):
            raise ValueError(_MAX_WORKERS_TEXT)
        value = int(digits)
        _validate_nested({"nested": {"max_workers": value}})
        return value
    raise AssertionError(key)


def _engine_global_only(key: str, scope: str) -> None:
    """engine.* lives in the global file only (DREAM-151): the project scope is refused before anything is opened."""
    if key in ENGINE_SETTINGS and scope != "global":
        raise ValueError(f"{key}: a project file may not set the engine; it is set on this computer, in the global "
                         f"file {shown_path(settings_path())} (run it without --project)")


def _nested_global_only(key: str, scope: str) -> None:
    """nested.max_workers too (DREAM-197): a cloned repository's project file may not raise the worker cap."""
    if key == NESTED_SETTING and scope != "global":
        raise ValueError(f"{key}: a project file may not set nested.max_workers; it is set on this computer, in the "
                         f"global file {shown_path(settings_path())} (run it without --project)")


def _refuse_other(key: str) -> None:
    if key == "profile" or key.startswith(("overrides.", "models.")):
        raise ValueError(f"{key}: use `dream profile` (or /profile) for the profile and its overrides")
    raise ValueError(f"{key} is not a setting `set` can change; settable: {', '.join(SETTABLE)}")


def _scope_target(scope: str) -> Path | None:
    """The file a `scope` writes: None for the global file, the registered workspace's project file for
    "project" (DREAM-146)."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}, not {scope!r}")
    if scope == "global":
        return None
    path = project_path()
    if path is None:
        raise ValueError("the project scope needs a workspace, and none is registered (dream settings --workspace <dir>)")
    return path


def _write(change, scope: str) -> None:
    """`change(saved)` -> the new file content, written by the locked, atomic runtime-settings writer to the file
    `scope` names. The global file is checked with the ordinary rules; a project file with the project rules (an
    error names the file: it may be what the file already holds, gate finding 4), the cloud-role rule, and its
    merge with the current global file with the ordinary ones, so a value the session could not use is refused
    now, with its key, and nothing is written. The project write runs entirely on the checked .dream descriptor
    (_project_dir_fd, _write_project_file). A global write does not read the project file: `check` reports a
    merge that no longer works."""
    target = _scope_target(scope)
    if target is None:
        update_settings(lambda saved: validate(change(saved)))
        return
    saved = read_global()

    def project_change(current):
        try:
            validate_project(current)          # what the file already holds is refused first, by name
            new = validate_project(change(current))
        except ValueError as exc:
            raise ValueError(f"Cannot use project settings {shown_path(target)}: {exc}; nothing was saved") from exc
        _cloud_roles(saved, new, target)
        try:
            validate(_merge(saved, new))
        except ValueError as exc:
            raise ValueError(f"{exc} -- with the global file {shown_path(settings_path())} this project value could "
                             "not be used, so nothing was saved") from exc
        return new
    # A linked or odd .dream is refused here, before anything is opened; an operating-system error from the folder
    # or its files -- a linked lock file (ELOOP), a .dream deleted while the writer holds it (ENOENT), no permission
    # -- is a plain refusal naming the project file, never a raw OSError (DREAM-147).
    try:
        dir_fd = _project_dir_fd(create=True)
    except OSError as exc:
        raise ValueError(_not_written(target, exc)) from exc
    try:
        _write_project_file(project_change, dir_fd, target)
    except OSError as exc:
        raise ValueError(_not_written(target, exc)) from exc
    finally:
        os.close(dir_fd)


def _not_written(path: Path, exc: OSError) -> str:
    where = f" ({shown(exc.filename)})" if getattr(exc, "filename", None) else ""
    return (f"Cannot use project settings {shown_path(path)}: its folder could not be written -- "
            f"{exc.strerror or exc}{where}; nothing was written")


def _saved_where(key: str, scope: str) -> str:
    return "applies to " + _applies(key) + (f" (project file {project_path()})" if scope == "project" else "")


def set_value(key: str, text: str, *, scope: str = "global") -> str:
    """Save one setting through the runtime-settings writer, to the global file or (`scope` "project") the
    workspace's project file, which takes model names only; returns when it applies. Refused values write
    nothing."""
    _engine_global_only(key, scope)
    _nested_global_only(key, scope)
    if key in ("output.max_tokens", "behaviour.vitals", STYLE_SETTING, *CONTEXT_SETTINGS, *ENGINE_SETTINGS,
               NESTED_SETTING):
        section, name = key.split(".")
        value = _parse(key, text)
        if name in _CONTEXT_RANGES:
            _check_context_pair(name, value, scope)

        def change(saved):
            saved = copy.deepcopy(saved)
            node = saved.setdefault(section, {})
            if not isinstance(node, dict):      # a hand-edited section (DREAM-209 gate): refused by name, no TypeError
                raise ValueError(f"Cannot use runtime settings {shown_path(settings_path())}: {section} must be an "
                                 f"object; make it one or remove it, then set {key} again")
            node[name] = value
            return {**saved, "version": 1}
    elif key.startswith("roles."):
        where, field = _role_key(key)
        if field is None:
            raise ValueError(f"{key}: set {key}.model or {key}.provider, not the role itself")
        if field == "provider":
            if scope == "project":
                raise ValueError(f"{key}: a project file names models only; the provider comes from the global "
                                 f"file (dream settings set {key} <provider>, without --project)")
            _provider(text, key, where[0])

        def change(saved):
            saved = copy.deepcopy(saved)
            parent = saved.setdefault("roles", {})
            for part in where[:-1]:
                parent = parent.setdefault(part, {})
            entry = parent.setdefault(where[-1], {})
            entry[field] = text
            return {**saved, "version": 1}      # the role's rule decides what it still needs
    else:
        _refuse_other(key)
    _write(change, scope)
    return _saved_where(key, scope)


def set_role(key: str, entry: dict, *, scope: str = "global") -> str:
    """Save a whole role at once (the Settings tab): `entry` replaces roles.<...> and is validated with the rest
    of the file before anything is written; an empty entry removes the role. In the project scope the entry holds
    the model only."""
    where, field = _role_key(key)
    if field is not None:
        raise ValueError(f"{key}: name the role, not one of its fields")
    if not entry:
        return unset_value(key, scope=scope)
    if scope == "project" and entry.get("provider"):
        raise ValueError(f"{key}.provider: a project file names models only; the provider comes from the global file")

    def change(saved):
        saved = copy.deepcopy(saved)
        parent = saved.setdefault("roles", {})
        for part in where[:-1]:
            parent = parent.setdefault(part, {})
        parent[where[-1]] = dict(entry)
        return {**saved, "version": 1}
    _write(change, scope)
    return _saved_where(key, scope)


def unset_value(key: str, *, scope: str = "global") -> str:
    """Remove one setting from the global file or (`scope` "project") the project file; an emptied section or
    role goes too. Removing a field the role requires (a sub-agent's, the verifier's or the filer's model; the
    critic's provider) removes the role."""
    _engine_global_only(key, scope)
    _nested_global_only(key, scope)
    if key in ("output.max_tokens", "behaviour.vitals", STYLE_SETTING, *CONTEXT_SETTINGS, *ENGINE_SETTINGS,
               NESTED_SETTING):
        section, name = key.split(".")
        trail = [(section,), (section, name)]
    elif key.startswith("roles."):
        where, field = _role_key(key)
        _, model_required, provider_required = role_rule(where[0])
        required = (field == "model" and model_required) or (field == "provider" and provider_required)
        target = ("roles", *where) if field is None or required else ("roles", *where, field)
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
        return saved
    try:
        _write(change, scope)
    except _Unchanged:
        return f"{key} was not set"
    return f"removed {key}; " + _saved_where(key, scope)


def _local_role_models(rows: dict[str, Effective], provider: str) -> dict[str, str]:
    """{row key: model} for every configured role whose model runs on the local MachX engine when the session runs
    on `provider`: a role naming machx, or one without a provider that applies here -- sub-agents, the verifier,
    the filer and main run on MachX only (a row not applied on this backend is skipped), the evaluator and the
    reviewer run on the session's own provider."""
    local = {}
    for key, row in rows.items():
        entry = row.value
        if (not key.startswith("roles.") or not isinstance(entry, dict) or not entry.get("model")
                or row.source.startswith(NOT_APPLIED)):
            continue
        runs_on = entry.get("provider") or (provider if key in ("roles.evaluator", "roles.reviewer") else "machx")
        if runs_on == "machx":
            local[key] = entry["model"]
    return local


ENGINE_DEADLINE_S = 3.0        # the whole /v1/models question: connecting, the headers and the body together
MAX_MODELS_BYTES = 1_000_000   # the most of a /v1/models answer `check` reads
MAX_LISTED = 10                # names one line lists; the rest are counted


def _listed(names: list[str]) -> str:
    """Names as one bounded line: the first MAX_LISTED, each through `shown`, then how many more."""
    more = len(names) - MAX_LISTED
    return (", ".join(shown(name) for name in names[:MAX_LISTED]) + (f" and {more} more" if more > 0 else "")
            if names else "no model")


def _engine_get(url: str, what: str) -> tuple[int, bytes] | str:
    """(status, body) of one GET to the local engine at `url`, or why there is none (a clause naming `url`). One
    overall deadline covers connecting, the headers and the body (gate round 1: httpx's timeouts are per operation,
    so a front that sends a byte every few seconds never trips them), and no more than MAX_MODELS_BYTES of the body is
    read. It runs its own event loop, so a caller with a running loop calls it from a thread (the TUI does). `what`
    names the answer awaited ("model list")."""
    import asyncio

    import httpx

    async def ask() -> tuple[int, bytes | None]:
        async with httpx.AsyncClient(timeout=ENGINE_DEADLINE_S) as client:
            async with client.stream("GET", url, headers={"accept-encoding": "identity"}) as response:
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body += chunk
                    if len(body) > MAX_MODELS_BYTES:
                        return response.status_code, None
                return response.status_code, bytes(body)

    where = shown_path(url)
    try:
        status, body = asyncio.run(asyncio.wait_for(ask(), ENGINE_DEADLINE_S))
    except httpx.ConnectError:
        return f"nothing answered at {where}"
    except (TimeoutError, httpx.TimeoutException):
        return f"no {what} from {where} within {ENGINE_DEADLINE_S:g} s"
    except httpx.HTTPError as exc:
        return f"{where} broke off its answer ({type(exc).__name__})"
    if body is None:
        return f"{where} answered with more than {MAX_MODELS_BYTES:,} bytes"
    return status, body


def _served_models(url: str) -> tuple[list[dict] | None, str]:
    """(the entries the engine lists at `url`, its /v1/models, or None; why not, when None), read by _engine_get."""
    answer = _engine_get(url, "model list")
    if isinstance(answer, str):
        return None, answer
    status, body = answer
    where = shown_path(url)
    try:
        data = json.loads(body).get("data") if status == 200 else None
    except (ValueError, RecursionError, AttributeError):      # not JSON, not UTF-8, too deep, not an object
        data = None
    if not isinstance(data, list):
        return None, f"{where} answered HTTP {status} without a model list Dream can read"
    return [m for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)], ""


def _model_names(rows: dict[str, Effective], provider: str) -> tuple[str, list[str]]:
    """(the report's `model_names` line, warnings): each role model the local engine runs, against the engine's
    /v1/models (design 3.5, DREAM-148) -- served when it is a listed id, or the `root` (the model file's id) of
    exactly one listed server, the two names the engine's front routes by. Nothing configured for the engine asks
    nothing; an engine that does not answer is the line, never an error; a model it does not serve is a warning."""
    local = _local_role_models(rows, provider)
    if not local:
        return "no role names a model on the local engine; nothing to check", []
    from ..local import machx
    url = machx.BASE_URL + "/models"
    listed, why = _served_models(url)
    if listed is None:
        head = "engine not running" if why.startswith("nothing answered") else "the engine did not list its models"
        return f"{head}; model names not checked ({why})", []
    ids = {m["id"] for m in listed}
    roots: dict[str, list[str]] = {}
    for m in listed:
        if isinstance(m.get("root"), str):
            roots.setdefault(m["root"], []).append(m["id"])
    served = _listed([m["id"] for m in listed])
    warnings = []
    for key, name in local.items():
        if name in ids or len(roots.get(name, ())) == 1:
            continue
        if name in roots:
            warnings.append(f"{key}: model {shown(name)} is the model file of several served models "
                            f"({_listed(roots[name])}); the engine refuses a request naming it as ambiguous -- "
                            "name one of them")
        else:
            warnings.append(f"{key}: model {shown(name)} is not served by the engine at {shown_path(url)} "
                            f"(it serves {served})")
    return f"checked against {shown_path(url)}: {len(listed)} served ({served})", warnings


def _engine_lanes(rows: dict[str, Effective]) -> tuple[str, list[str]]:
    """(the report's `engine_lanes` line, warnings), DREAM-151: the lanes the running local engine serves -- its
    /props total_slots -- against engine.parallel. Asked only when an engine setting is saved (DREAM-148's rule:
    nothing configured asks nothing); an engine that does not answer is the line, never an error."""
    parallel, slot_ctx = rows["engine.parallel"], rows["engine.slot_ctx"]
    if parallel.source != "global" and slot_ctx.source != "global":
        return "engine.parallel is not set; nothing to check", []
    from ..local import machx
    root = machx.BASE_URL.removesuffix("/v1")
    answer = _engine_get(root + "/props", "answer")
    if isinstance(answer, str):
        head = "engine not running" if answer.startswith("nothing answered") else "the engine did not answer /props"
        return f"{head}; lanes not checked ({answer})", []
    status, body = answer
    try:
        props = json.loads(body) if status == 200 else None
    except (ValueError, RecursionError):
        props = None
    slots = props.get("total_slots") if isinstance(props, dict) else None
    served = slots if type(slots) is int and slots >= 1 else None
    doing = (f"serves {served} lane{'' if served == 1 else 's'}" if served is not None
             else "does not report its lanes")
    line = f"the running engine at {shown_path(root)} {doing}" + (
        " (its /props total_slots)" if served is not None else f" (HTTP {status}, no /props total_slots)")
    warnings = []
    if parallel.source == "global" and served != parallel.value:
        from ..local.models import LANE_MODELS_LISTED
        warnings.append(f"engine.parallel: {parallel.value} lanes are set, but the running engine {doing}; the "
                        "setting applies the next time Dream starts the engine, and only to a model that serves "
                        f"lanes ({LANE_MODELS_LISTED})")
    return line, warnings


def check(*, provider: str = "machx", model: str | None = None, ask_engine: bool = False) -> dict:
    """Is the file usable, what does it hold that nothing reads, which values does the environment override,
    which roles are set, and what to watch. The saved Council file (var/moe.json) is checked too: unusable, it is a
    warning naming it and its entry (DREAM-148). `ask_engine` (`dream settings check`, /settings check) also asks
    the local engine's /v1/models whether each role model it runs is served (_model_names) and, when an engine setting
    is saved, its /props how many lanes it serves (_engine_lanes, DREAM-151); without it nothing is asked."""
    from . import moe
    project = project_path()
    report: dict[str, Any] = {"ok": True, "file": str(settings_path()),
                              "project": str(project) if project is not None else None,
                              "provider": provider, "model": model, "errors": [], "warnings": []}
    council = moe.config_note()         # a separate file: named whether or not the settings file is usable
    if council:
        report["warnings"].append(council)
    try:
        saved = read()
        rows = effective(provider=provider, model=model)
    except ValueError as exc:
        report.update(ok=False, errors=[str(exc)])
        return report
    report["unknown_sections"] = sorted(shown(key) for key in set(saved) - _KNOWN_SECTIONS)
    report["environment_overrides"] = {key: row.source for key, row in rows.items() if row.source.startswith("env ")}
    report["roles"] = {key: {"value": row.value, "source": row.source} for key, row in rows.items()
                       if key.startswith("roles.") and row.source != "default"}
    report["warnings"] += [f"{key}: {row['source']}" for key, row in report["roles"].items()
                           if row["source"].startswith(NOT_APPLIED)]
    if "verifier" in saved.get("roles", {}):
        report["warnings"].append(
            "roles.verifier: not confirmed to run separately. On an engine that caches one conversation the "
            "verifier keeps fix #71's skip (or DREAM_SINGLE_SLOT_VERIFIER=continue) unless the endpoint's "
            "/v1/models lists this model as its own entry beside another; that is checked when the verifier runs")
    if ask_engine:
        report["model_names"], warnings = _model_names(rows, provider)
        report["warnings"] += warnings
        report["engine_lanes"], warnings = _engine_lanes(rows)
        report["warnings"] += warnings
    else:
        report["model_names"] = "not checked here (dream settings check and /settings check ask the local engine)"
        report["engine_lanes"] = "not checked here (dream settings check and /settings check ask the local engine)"
    return report
