"""The Settings tab of Studio's Controls dialog (DREAM-143, settings design P3 phase S2).

`view()` is the `settings_get` action: every row of core/settings.effective() once, grouped in the design's sections
(3.2), each with a plain label, a one-line explanation, its value, where the value came from, when a change applies,
and -- only for what `set_value`/`unset_value` can change -- the control that edits it. Everything else is shown
read-only with its source; a row the environment sets says which variable to change. `save()` is `settings_save`:
one key through the same writer as `dream settings set/unset`, so validation, the lock and the atomic write are
core/settings' and core/profiles'. Secrets never leave this module: API keys are reported as present or absent,
URLs are shown as scheme, host and path only (no credentials, query or fragment), and any value stored under a
secret-looking name (api_key, token, *_secret, password, ...) reads "hidden". Errors are plain sentences that name
the key; an unusable file's error names the entry, never the value in it.

S4 (DREAM-145): the critic, evaluator and /review roles are rows like the others; the Council section reads the
saved Council (var/moe.json) and edits each advisor's model and effort through moe.save_config, the file's own
writer; the Local engine layout section shows dream.core.engine_layout.layout_view() read-only when that module
exists (phase S3, built separately), through the one adapter `engine_layout()`.

S5 (DREAM-146): `save()` takes a `scope` -- "global" (the default, as before) or "project", the workspace's
`.dream/settings.json` (core/settings.project_path), where a role is saved with its model only; the view names the
project file (`project_file`) and a project-set row has origin "project". Council rows are always global.
"""
from __future__ import annotations

import importlib
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..core import settings
from ..core.profiles import MAX_OVERRIDE_NUMBER, PROFILE_ENVS, read_settings, settings_path, shown_path
from ..core.providers import PROVIDERS

# A name part (e.g. "api_key", "client_secret", "token") whose value is never shown; "max_tokens" is not one.
_SECRET = re.compile(r"(?:^|[_-])(?:api[_-]?key|key|token|secret|password|passwd|credentials?|auth|authorization|"
                     r"bearer|cookie)$", re.I)
_ASCII_NUMBER = re.compile(r"[0-9][0-9_,]*\Z")
_FILE_ENTRY = re.compile(r"\b(?:roles|output|behaviour|engine|overrides|models|profile|blender|studio|version)"
                         r"(?:\.[A-Za-z0-9_-]+)*")
_HIDDEN = "hidden"

# (id, title, intro). Rows land in the first section whose rule claims them; the rest go to "Other numbers".
SECTIONS = (
    ("models", "Models & providers",
     "Which provider and model this session runs on, and the providers Dream knows. Choose them when you start "
     "Dream (--provider/--model or the picker); API keys stay in your environment and are never shown here."),
    ("roles", "Roles",
     "Which model does which job. \"Same as main\" means the job runs on the session's own model; a role takes "
     "effect at the next run of that job. Council members are set in the Council panel."),
    ("council", "Council",
     "The saved Council: who leads it and which advisors it consults are set in the Council panel; each advisor's "
     "model and reasoning effort can be changed here."),                  # the intro is rewritten per state in view()
    ("lanes", "Engine lanes",
     "How many requests the local engine serves at once when Dream starts it (DREAM-151): the main model and "
     "sub-agents then decode together. A change applies the next time Dream starts the engine; the running engine's "
     "lanes are in the layout below."),
    ("workers", "Workers",
     "How many sub-agents one reply of the main agent may start (DREAM-197). How many of them run at once is set by "
     "the engine lanes above with the local engine, and by Parallel workers with a cloud provider; the rest queue."),
    ("engine", "Local engine layout",
     "Which model runs on which card arrives with the multi-model engine layout (phase S3). Nothing here to set "
     "yet; Dream never restarts the engine from this tab."),               # rewritten by engine_layout() in view()
    ("permissions", "Permissions & sandbox",
     "How much Dream may do without asking. Not saved between sessions yet."),
    ("media", "Media", "Programs Dream drives for media work."),
    ("output", "Output & reasoning", "How long one answer may be and how many steps a turn may take."),
    ("profile", "Profile & limits",
     "The runtime profile and the limits it sets. Change them in Runtime → Next session; they apply to new "
     "sessions."),
    ("context", "Context management",
     "What the main agent and its sub-agents do when their context window fills, and when. The warning comes a few "
     "points before the trigger so the agent can finish its step and save what it learned."),
    ("vitals", "Vitals", "What the local engine reports about itself while it works."),
    ("memory", "Memory", "How Dream checks its work and files what it learns."),
    ("privacy", "Privacy", "What may leave this machine, and how Studio is reached."),
    ("other", "Other numbers",
     "Tuning values read from the environment when Dream starts. Set the named variable before starting Dream "
     "to change one."),
)

# key -> (label, one-line explanation[, text for an empty value])
_CATALOG: dict[str, tuple[str, ...]] = {
    "roles.main": ("Main model", "The model this session talks to. Every job below set to \"same as main\" uses "
                   "it. /model changes it in the running session."),
    "roles.subagents.default": ("Sub-agents (default)", "The model for delegated work (researcher, coder, "
                                "explorer, ...) unless a sub-agent type has its own row."),
    "roles.verifier": ("Verifier", "The model that checks finished work before Dream reports it done."),
    "roles.filer": ("Filer", "The model that files what a session learned into memory."),
    "roles.critic": ("Critic", "The program /critique asks to judge a render against its reference: Claude, "
                     "Codex, Gemini or Grok, with an optional model. Unset, the first one installed is asked."),
    "roles.evaluator": ("Loop evaluator", "The provider and model that grade an autonomous run's result and draft "
                        "optimized prompts. For the run's grade, DREAM_EVALUATOR_PROVIDER and DREAM_EVALUATOR_MODEL "
                        "come first; the prompt optimizer reads this row only."),
    "roles.reviewer": ("Code reviewer (/review)", "The provider and model /review hands a diff to. Unset, the "
                       "session's own model reviews it."),
    "behaviour.single_slot_verifier": ("Verifier on a one-conversation engine", "On an engine that caches one "
                                       "conversation, skip the separate check or run it inside the main "
                                       "conversation (continue)."),
    "permissions.mode": ("Permission mode (this session)", "ask, accept-edits, auto or plan. Change it with "
                         "Shift+Tab in the terminal or the mode control in Studio; start with --mode to choose "
                         "another."),
    "blender.binary": ("Blender program", "The Blender executable Dream runs for 3D work. Read when Dream loads "
                       "its Blender support."),
    "output.max_tokens": ("Max output tokens", "The most tokens one answer may use. The context window still "
                          "bounds it."),
    "output.ceiling": ("Output a turn may use now", "What the next answer in this running Dream may use: the "
                       "smallest of the limit it started with (or /maxtokens), the profile's output reserve, a "
                       "model preset and the performance mode."),
    "numeric.tool_budget": ("Tool calls per prompt", "How many tool calls one prompt may make, with every "
                            "provider except the Claude SDK. /toolcalls changes it for a session.", "Unlimited"),
    "numeric.subagent_rounds": ("Sub-agent steps", "How many tool rounds a delegated sub-agent may run before it "
                                "must wrap up."),
    "numeric.max_tool_rounds": ("Steps per turn", "How many model generations one turn may take before Dream "
                                "stops it with an error."),
    "numeric.frequency_penalty": ("Frequency penalty", "Sent with requests to discourage the model from "
                                  "repeating the same tokens. 0 turns it off."),
    "numeric.presence_penalty": ("Presence penalty", "Sent with requests to discourage the model from reusing "
                                 "any token it has used. 0 turns it off."),
    "numeric.repetition_penalty": ("Repetition penalty", "Sent with requests to discourage loops in the model's "
                                   "output. 1.0 turns it off."),
    "behaviour.response_style": ("Response style", "How Dream words its replies. Default is Dream's own voice; "
                                 "the others add one short style block to the start of each session."),
    "behaviour.context_overflow": ("Main agent: when the context fills", "compact = cut old history into working "
                                   "notes; handoff = rewrite the conversation as one Handoff and keep going; stop = "
                                   "end the turn with a summary of what was done."),
    "behaviour.subagent_overflow": ("Sub-agents: when the context fills", "compact = cut old history; handoff = "
                                    "continue in a fresh context from a progress report; return = stop and report what "
                                    "it has to the main agent."),
    "behaviour.context_trigger": ("Context trigger (%)", "How full the context window gets (percent) before the "
                                  "choice above happens, for the main agent and sub-agents."),
    "behaviour.context_warning": ("Warning before the trigger (points)", "How many percentage points before the "
                                  "trigger the agent is told to finish its step and save what it learned. 0 = no "
                                  "warning."),
    "behaviour.vitals": ("Engine vital signs", "Ask the local engine (MachX) for vital signs such as speed and "
                         "memory with every answer."),
    "behaviour.auto_verify": ("Check work automatically", "After a task, run the verifier before reporting it "
                              "done."),
    "overrides.auto_filer": ("File memories automatically", "At the end of a session, file what was learned into "
                             "memory (DREAM_AUTO_FILE can override it)."),
    "numeric.memory_file_max": ("Memory file size note", "Characters after which a memory file carries a note "
                                "to consolidate it."),
    "numeric.rerank_candidates": ("Reranked memory candidates", "How many memory search hits the optional "
                                  "reranker re-orders."),
    "numeric.salience_halflife": ("Memory fade (days)", "Days after which an unused memory counts half as much "
                                  "when Dream wakes up."),
    "numeric.ann_threshold": ("Fast search above", "Number of stored vectors above which memory search switches "
                              "to approximate search."),
    "numeric.chunk_threshold": ("Split memories longer than", "Characters above which a memory is split so a "
                                "passage can be found."),
    "numeric.chunk_size": ("Memory piece size", "Characters per piece when a long memory is split."),
    "numeric.merge_threshold": ("Merge duplicates above", "Similarity above which consolidation merges two "
                                "memories."),
    "numeric.reconcile_threshold": ("Ask about near-duplicates above", "Similarity above which consolidation asks "
                                    "the model whether two memories should be merged."),
    "numeric.reconcile_max_clusters": ("Near-duplicate groups per pass", "How many groups of similar memories one "
                                       "consolidation pass asks about."),
    "numeric.reconcile_excerpt": ("Excerpt shown per memory", "Characters of each memory shown when the model is "
                                  "asked about near-duplicates."),
    "numeric.link_reserve": ("Linked memories kept in results", "Result slots reserved for memories linked to "
                             "a hit."),
    "numeric.autolink_threshold": ("Link memories above", "Similarity above which a new memory is linked to an "
                                   "existing one."),
    "numeric.autolink_k": ("Links per new memory", "How many existing memories a new one may link to."),
    "overrides.vision": ("Image input", "Whether the model is sent images. Automatic uses the provider's default.",
                         "Automatic"),
    "overrides.vision_helper": ("Image describer", "A cloud provider that describes images for a model that "
                                "cannot see them. When set, images leave this machine.", "Off"),
    "numeric.gui_port": ("Studio port", "The port Studio listens on (0 picks a free one). Studio always listens "
                         "on this machine only (loopback); that is not configurable.", "Any free port"),
    "profile": ("Runtime profile", "lean, balanced or frontier sizes context, output and parallel work; auto picks "
                "one for the provider."),
    "overrides.context_limit": ("Context limit", "The most tokens of conversation Dream keeps in view.",
                                "Model's own window"),
    "overrides.output_tokens": ("Output reserve", "Tokens kept free for the answer inside the context window."),
    "overrides.max_parallel": ("Parallel workers", "How many sub-agents may run at once with a cloud provider. With "
                               "the local engine its lanes decide (Engine lanes)."),
    "overrides.idle_timeout_s": ("Idle timeout (seconds)", "How long a request may go without any output before "
                                 "it is abandoned."),
    "overrides.subagent_timeout_s": ("Sub-agent timeout (seconds)", "How long one delegated sub-agent may run."),
    "overrides.max_run_tokens": ("Tokens per turn", "The most tokens one user turn may spend.", "Unlimited"),
    "overrides.max_run_tools": ("Tools per turn", "The most tool calls one user turn may make.", "Unlimited"),
    "overrides.max_run_seconds": ("Active time per turn (seconds)", "An optional limit on working time, "
                                  "approval waits excluded.", "Unlimited"),
    "overrides.max_wall_seconds": ("Total time per turn (seconds)", "An optional limit on elapsed time, "
                                   "approval waits included.", "Unlimited"),
    "overrides.schema_fraction": ("Tool description share", "The part of the context window tool descriptions "
                                  "may take."),
    "overrides.wake_tokens": ("Wake-up memory size", "Tokens of memory Dream reads in at the start of a "
                              "session."),
    "numeric.ctx_window": ("Assumed Claude context", "The context window the per-turn stats line assumes for "
                           "the Claude SDK when it reports none."),
    "numeric.searxng_port": ("Search service port", "The port of the local SearXNG search service."),
    "numeric.browser_timeout_ms": ("Browser page timeout (ms)", "How long the browser waits for a page."),
    "numeric.browser_idle_s": ("Browser idle shutdown (seconds)", "Idle time after which the browser closes to "
                               "save memory."),
    "numeric.tool_stale_days": ("Self-built tool goes stale after (days)", "Days unused after which a tool Dream "
                                "built is tagged as stale."),
    "numeric.http_timeout_s": ("Search request timeout (seconds)", "How long a request to the local SearXNG "
                               "search service may take."),
    "numeric.llm_read_timeout_s": ("Local answer timeout (seconds)", "How long Dream waits for a local model's "
                                   "answer. Empty means it waits as long as it takes.", "No limit"),
    "engine.parallel": ("Parallel requests (lanes)", "How many requests the local engine serves at once, so the main "
                        "model and sub-agents decode together. The setting applies to MiMo-V2.6, DeepSeek-V4.1, "
                        "Qwen3.8-Flash, the 35B-A3B class and Qwen3.8-27B; 1 to 16. Every extra lane reserves VRAM when "
                        "the model loads (on MiMo-V2.6 and DeepSeek-V4.1 out of the in-VRAM expert cache, so more lanes "
                        "can slow every lane on those two): set the number of agents you really run at once, give the "
                        "extra lanes a realistic Context per extra lane (16,384 rather than the whole context), and the "
                        "engine refuses, with its numbers, a count that does not fit (a smaller context or Context per "
                        "extra lane is the way out). A changed count takes effect at the next engine start: restart the "
                        "engine. Unset, each model keeps its own launch setting (1 unless changed); set, it replaces "
                        "that setting on every model named above. "
                        "MiMo-V2.6 takes images at 1 only: at 2 or more the engine refuses them, and Dream sends none; "
                        "Qwen3.8-Flash the same. DeepSeek-V4.1 keeps its images on lanes. DeepSeek-V4.1, Qwen3.8-Flash, "
                        "the 35B-A3B class and Qwen3.8-27B serve lanes on two cards only: Dream refuses a launch that "
                        "chose 1 GPU with 2 or more lanes. Qwen3.8-Flash's lanes refuse IE_P2P; Qwen3.8-27B with "
                        "speculation on serves its lanes through its slower joint-step path; INT8 KV is refused with "
                        "lanes on every model."),
    "engine.slot_ctx": ("Context per extra lane", "Tokens each lane after the first holds; the first keeps the "
                        "model's whole context, and a longer request waits for it. Unset or 0, the engine's own size: "
                        "32,768 on MiMo-V2.6, DeepSeek-V4.1, Qwen3.8-Flash and the 35B-A3B class, 65,536 on "
                        "Qwen3.8-27B. A larger one costs VRAM on both cards, and the engine refuses a load that does "
                        "not fit."),
    "nested.max_workers": ("Workers per reply", "How many sub-agents one reply of the main agent may start: 3, 7, 11 or "
                           "15, which with the main agent makes 4, 8, 12 or 16 agents. Task calls past it in one reply "
                           "are not run, and the main agent is told so. How many of the workers run at once is set by "
                           "the engine lanes with the local engine and by Parallel workers with a cloud provider; the "
                           "rest queue."),
}

_PLACEMENT = {
    "models": ("roles.main",),
    "roles": ("roles.", "behaviour.single_slot_verifier"),
    "council": ("council.",),
    "lanes": ("engine.parallel", "engine.slot_ctx"),
    "workers": ("nested.",),
    "engine": ("engine.",),
    "permissions": ("permissions.",),
    "media": ("blender.",),
    "output": ("output.", "behaviour.response_style", "numeric.tool_budget", "numeric.subagent_rounds", "numeric.max_tool_rounds",
               "numeric.frequency_penalty", "numeric.presence_penalty", "numeric.repetition_penalty"),
    "context": ("behaviour.context_", "behaviour.subagent_overflow"),
    "vitals": ("behaviour.vitals",),
    "memory": ("behaviour.auto_verify", "overrides.auto_filer", "numeric.memory_file_max",
               "numeric.rerank_candidates", "numeric.salience_halflife", "numeric.ann_threshold",
               "numeric.chunk_", "numeric.merge_", "numeric.reconcile_", "numeric.link_", "numeric.autolink_"),
    "privacy": ("overrides.vision", "numeric.gui_port"),
    "profile": ("profile", "overrides.", "numeric.ctx_window"),
}

_ENV_NAMES = {"profile": "DREAM_PROFILE", "output.max_tokens": "DREAM_MAX_TOKENS",
              "behaviour.auto_verify": "DREAM_AUTO_VERIFY", "behaviour.context_trigger": "DREAM_COMPACT_AT", "behaviour.single_slot_verifier": "DREAM_SINGLE_SLOT_VERIFIER",
              "overrides.vision": "DREAM_VISION", "overrides.vision_helper": "DREAM_VISION_HELPER",
              "blender.binary": "DREAM_BLENDER",
              **{f"overrides.{field}": env for field, (env, _) in PROFILE_ENVS.items() if field != "output_tokens"}}


def _section_of(key: str) -> str:
    for section, rules in _PLACEMENT.items():
        if any(key == rule or (rule.endswith((".", "_")) and key.startswith(rule)) for rule in rules):
            return section
    return "other"


def _env_name(key: str) -> str | None:
    if key.startswith("numeric."):
        return "DREAM_" + key.removeprefix("numeric.").upper()
    return _ENV_NAMES.get(key)


def _governing_env(key: str) -> str | None:
    """The environment variable(s) set right now that decide `key`, else None (the row is then editable)."""
    if key == "roles.evaluator":
        return ", ".join(name for name in settings.EVALUATOR_ENVS if name in os.environ) or None
    env = _env_name(key)
    return env if env and env in os.environ else None


def _clean_url(url: str | None) -> str | None:
    """A URL as scheme://host[:port]/path: user, password, query and fragment dropped. Other text is unchanged."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    host = parts.hostname or ""
    return urlunsplit((parts.scheme, host + (f":{port}" if port else ""), parts.path, "", ""))


def _secret_name(name: object) -> bool:
    return any(_SECRET.search(part) for part in re.split(r"[.\s]+", str(name)) if part)


def _mask(value: Any, name: object = "") -> Any:
    """`value` as the page may see it: hidden entirely when its name (a row key or a field) is a secret's."""
    if _secret_name(name):
        return _HIDDEN
    if isinstance(value, dict):
        return {k: _mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    if isinstance(value, str):
        return _clean_url(value)
    return value


def _origin(source: str) -> str:
    if source.startswith("env "):
        return "env"
    if source.startswith(settings.NOT_APPLIED):
        return "not-applied"
    head = source.split(" ", 1)[0]
    return head if head in ("session", "project", "global", "default") else "default"


def _display(value: Any, empty: str) -> str:
    if value is None:
        return empty
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, dict):
        if value and set(value) <= {"provider", "model"}:
            model = value.get("model") or "the provider's default model"
            return f"{model} on {value['provider']}" if value.get("provider") else str(model)
        return ", ".join(f"{k}: {_display(v, 'none')}" for k, v in value.items()) or empty
    return str(value) or empty


def _label(key: str) -> tuple[str, str, str]:
    if key in _CATALOG:
        label, help_text, *rest = _CATALOG[key]
        return label, help_text, rest[0] if rest else "Not set"
    if key.startswith("roles.subagents."):
        agent = key.removeprefix("roles.subagents.")
        return (f"Sub-agent: {agent}", f"The model for the {agent} sub-agent; it replaces the sub-agents default "
                "for this type.", "Not set")
    if key.startswith("roles."):
        name = key.removeprefix("roles.").removesuffix(" (in file)")
        return (f"Role in file: {name}", "A role this version does not apply yet. It is kept in the file and "
                "ignored.", "Not set")
    return key, "No description yet.", "Not set"


def _editable(key: str) -> bool:
    return key in ("output.max_tokens", "behaviour.vitals", settings.STYLE_SETTING, *settings.CONTEXT_SETTINGS,
                   *settings.ENGINE_SETTINGS, settings.NESTED_SETTING) or (
        key.startswith("roles.") and key != "roles.main" and not key.endswith(" (in file)")
        and (key.startswith("roles.subagents.") or key.removeprefix("roles.") in settings.ROLES))


def _applies(key: str) -> tuple[str, bool]:
    """(when a change takes effect, whether that needs a new session or a restart)."""
    if key == "output.max_tokens":
        return ("When Dream starts (restart Dream to use a saved value). This session keeps its limit; "
                "/maxtokens changes it now."), True
    if key == settings.STYLE_SETTING:
        return "New sessions (/new starts one).", True
    if key == "behaviour.vitals" or key in settings.CONTEXT_SETTINGS:
        return "Now, and in new sessions.", False
    if key == settings.NESTED_SETTING:     # DREAM-209: the cap now; the engine's --max-queue at its next start
        return ("Now: from the main agent's next reply, and in new sessions. The local engine's queue (--max-queue): "
                "from the engine's next start; a running engine keeps its queue."), False
    if key in settings.ENGINE_SETTINGS:
        return ("The next time Dream starts the local engine (load the model again); a running engine keeps its "
                "lanes."), True
    if key == "roles.main":
        return "Now, with /model in the terminal (the provider is chosen when Dream starts).", False
    if key.startswith("roles."):
        return "The next run of that job.", False
    if key == "output.ceiling":
        return "Follows the limits it is made of.", False
    if key == "permissions.mode":
        return "Now, in this session only.", False
    if key == "profile" or key.startswith("overrides."):
        return "New sessions.", True
    return "When Dream starts.", True


def _source_text(source: str, key: str) -> str:
    origin = _origin(source)
    if origin == "env":
        return f"Set by the environment variable {source.removeprefix('env ')}"
    if origin == "global":
        extra = source.removeprefix("global").strip()
        return f"Saved in {settings_path().name}" + (f" {extra}" if extra else "")
    if origin == "project":
        return f"Saved in this workspace's project file ({settings.project_path()})"
    if origin == "session":
        extra = source.removeprefix("session").strip()
        return "Set in this session" + (f" {extra}" if extra else "")
    if origin == "not-applied":
        return "Not used in this session: " + _not_applied_reason(source)
    return "Built-in default" + (f" {source.removeprefix('default').strip()}" if source != "default" else "")


def _not_applied_reason(source: str) -> str:
    """core/settings' "not applied" source, in plain words."""
    if "not a role this version applies" in source:
        return "this version of Dream keeps it in the file but does not use it"
    if "without a provider" in source:
        return "a role without a provider is only used with the local MachX engine"
    if "Claude SDK maps" in source:
        return "the Claude SDK uses a sub-agent role only when it names anthropic"
    named = re.search(r"it names (\S+?),", source)
    if named:
        return f"it names {named.group(1)} but this session runs on another provider, so that job stops with an error"
    return "this provider does not use a separate model per job"


def _file_error(exc: Exception) -> str:
    """An unusable file -- the global one, or the workspace's project file when the error names it (DREAM-146) --
    named by the entry that failed and never by the value in it."""
    project = settings.project_path()
    named = shown_path(project if project is not None and shown_path(project) in str(exc) else settings_path())
    text = str(exc).split(f"{named}:", 1)[-1].strip()
    # The offending key comes from the wording of core/settings' refusals, in this order (DREAM-147): a linked
    # folder; a section or key a project file may not set ("may not set <keys>;"); a cloud role's missing global
    # provider ("<key> needs <key> in the global file"); a folder the writer could not use; else the first entry
    # name in the sentence. Values in the file are never repeated here.
    # The key list ends where the refusal's explanation begins (a shown key may itself contain ";").
    forbidden = re.search(r"may not set (.+?); (?:it holds|a project file names)", text)
    needs = re.match(r"(\S+) needs (\S+) in the global file", text)
    if forbidden:
        where = f"a project file may not set {forbidden.group(1)}"
    elif needs:
        where = f"the {needs.group(1)} entry needs {needs.group(2)} in the global file"
    elif "could not be written" in text:          # before the folder-link case: ELOOP's own words name links too
        where = text.removesuffix("; nothing was written") + "; nothing was written"
    elif "symbolic link" in text:
        where = "its folder .dream is a symbolic link or not a real directory of the workspace"
    else:
        entry = _FILE_ENTRY.search(text)
        where = f"the {entry.group(0)} entry is not valid" if entry else "an entry in it is not valid"
    tail = ("Nothing can be saved to this workspace until then; saves to this computer still work."
            if named != shown_path(settings_path()) else "Nothing can be saved until then.")
    return f"The settings file {named} cannot be used: {where}. Dream never rewrites it; repair it by hand, then refresh. {tail}"


def _row(key: str, value: Any, source: str, saved: dict) -> dict:
    label, help_text, empty = _label(key)
    applies, restart = _applies(key)
    value = _mask(value, key)
    origin = _origin(source)
    row = {"key": key, "label": label, "help": help_text, "value": value, "display": _display(value, empty),
           "source": source, "source_text": _source_text(source, key), "origin": origin,
           "applies": applies, "restart": restart,
           "restart_label": ("Restart Dream" if applies.startswith("When Dream starts") else "Engine restart"
                             if key in settings.ENGINE_SETTINGS else "New session")
           if restart else None, "edit": None, "locked": None}
    env, governing = _env_name(key), _governing_env(key)
    if _editable(key) and not governing:
        if key == "output.max_tokens":
            row["edit"] = {"kind": "number", "min": settings.MIN_MAX_TOKENS, "max": MAX_OVERRIDE_NUMBER,
                           "value": value if isinstance(value, int) else None}
        elif key == "behaviour.vitals":
            row["edit"] = {"kind": "switch"}
        elif key == settings.STYLE_SETTING:
            from ..core import response_styles
            row["edit"] = {"kind": "choice", "value": value, "choices": list(response_styles.STYLES),
                           "labels": dict(response_styles.LABELS),
                           "texts": {key: response_styles.SUMMARIES[key] + (
                               "\n" + response_styles.shown_text(key) if response_styles.shown_text(key) else "")
                               for key in response_styles.STYLES}}
            row["display"] = response_styles.label(value) if isinstance(value, str) else row["display"]
        elif key in ("behaviour.context_overflow", "behaviour.subagent_overflow"):
            row["edit"] = {"kind": "choice", "value": value, "choices": list(
                settings.CONTEXT_OVERFLOW if key == "behaviour.context_overflow" else settings.SUBAGENT_OVERFLOW)}
        elif key in settings.CONTEXT_SETTINGS:
            low, high = (10, 95) if key == "behaviour.context_trigger" else (0, 50)
            row["edit"] = {"kind": "number", "min": low, "max": high, "value": value if type(value) is int else None}
        elif key in settings.ENGINE_SETTINGS:
            low, high = (1, settings.MAX_LANES) if key == "engine.parallel" else (0, settings.MAX_SLOT_CTX)
            row["edit"] = {"kind": "number", "min": low, "max": high,
                           "value": value if type(value) is int else None}
        elif key == settings.NESTED_SETTING:
            # DREAM-197: the Nested view's 4 / 8 / 12 / 16 control counts the main agent; the value is its workers.
            row["edit"] = {"kind": "choice", "value": value, "choices": list(settings.MAX_WORKERS),
                           "labels": {str(n): f"{n + 1} agents" for n in settings.MAX_WORKERS}}
            if type(value) is int:
                row["display"] = f"{value + 1} agents ({value} workers)"
        else:
            path = key.removeprefix("roles.").split(".")
            entry = saved.get("roles", {})
            for part in path:
                entry = entry.get(part, {}) if isinstance(entry, dict) else {}
            row["edit"] = {"kind": "role", "model": entry.get("model", ""), "provider": entry.get("provider", ""),
                           "removable": bool(entry), **_role_editor(path[0], key)}
    elif origin == "env" and (governing or env):
        row["locked"] = f"Read-only here: change or unset {governing or env} where Dream is started, then restart Dream."
    elif key == "roles.main":
        row["locked"] = ("Change it with /model in the terminal; the provider is chosen when you start Dream "
                         "(--provider or the picker).")
    elif key == "permissions.mode":
        row["locked"] = "Shift+Tab in the terminal, or the mode control in Studio."
    elif key == "profile" or key.startswith("overrides."):
        row["locked"] = "Change it in Runtime → Next session" + (f", or with {env}" if env else "") + "."
    elif key == "output.ceiling":
        row["locked"] = "Worked out from the other limits; nothing to set."
    elif origin == "not-applied" or key.endswith(" (in file)"):
        row["locked"] = f"Not applied by this version. Remove it from {settings_path().name} by hand if unwanted."
    elif key == "blender.binary":
        row["locked"] = f"Set {env}, or blender.binary in {settings_path().name}, before starting Dream."
    elif env:
        row["locked"] = f"Set {env} before starting Dream to change it."
    else:
        row["locked"] = "Read-only here."
    return row


def _role_editor(name: str, key: str) -> dict:
    """What a role's editor offers, by the role's rule (core/settings): its providers, the words for an unset
    provider (for the critic that is the default itself, so an untouched Save writes nothing), the model field's
    placeholder and the reset button's words."""
    providers, model_required, provider_required = settings.role_rule(name)
    if name == "critic":
        unset, placeholder, reset = "First available (default)", "The program's default model", "Use first available"
    elif name in ("evaluator", "reviewer"):
        unset, placeholder, reset = "Same as main", "Same as main, or the provider's default", "Use main model"
    else:
        unset, placeholder = "Not set · local MachX only", "Same as main"
        reset = ("Use sub-agents default" if key.startswith("roles.subagents.") and key != "roles.subagents.default"
                 else "Use main model")
    return {"providers": list(providers), "provider_unset": unset, "model_placeholder": placeholder, "reset": reset,
            "model_required": model_required, "provider_required": provider_required}


def _providers() -> list[dict]:
    rows = []
    for key, spec in PROVIDERS.items():
        if spec.kind == "cli":
            credential = f"signs in through its own program ({spec.cli_cmd})"
        elif spec.kind == "anthropic":
            credential = "signs in through the Claude SDK"
        elif spec.default_api_key is not None:
            credential = "not needed"
        elif spec.api_key_env and os.environ.get(spec.api_key_env):
            credential = f"set in {spec.api_key_env} (hidden)"
        else:
            credential = f"not set ({spec.api_key_env})"
        rows.append({"id": key, "label": spec.label, "kind": {"openai": "HTTP (OpenAI-compatible)",
                     "anthropic": "Claude SDK", "cli": "command-line program"}.get(spec.kind, spec.kind),
                     "endpoint": _clean_url(spec.base_url), "credential": credential})
    return rows


def _blender_row(saved: dict) -> tuple[Any, str]:
    # The order of media/blender_live._configured_blender (read here, not imported: that module starts nothing,
    # but it resolves the path once at import).
    if os.environ.get("DREAM_BLENDER"):
        return os.environ["DREAM_BLENDER"], "env DREAM_BLENDER"
    section = saved.get("blender")
    if isinstance(section, dict) and "binary" in section:
        return str(section["binary"]), "global"
    return "/usr/bin/blender", "default"


# --- the Council (var/moe.json, through core/moe) ----------------------------------------------------------

def _plain_row(key: str, label: str, help_text: str, value: Any, display: str, *, source: str, source_text: str,
               applies: str, restart: bool = False, restart_label: str | None = None, edit: dict | None = None,
               locked: str | None = None) -> dict:
    """A row of the same shape as _row's, for values that do not come from core/settings."""
    return {"key": key, "label": label, "help": help_text, "value": value, "display": display, "source": source,
            "source_text": source_text, "origin": _origin(source), "applies": applies, "restart": restart,
            "restart_label": restart_label, "edit": edit, "locked": locked}


def _council_note() -> str | None:
    """An unusable Council file as the tab says it (DREAM-148): its path and the entry at fault, never a value in
    it, as a sentence without its final period; None when the file is usable or absent."""
    from ..core import moe
    note = moe.config_note(entry_only=True)
    return f"{note[0].upper()}{note[1:]}" if note else None


def _council_section(choices: dict[str, dict]) -> tuple[str, list[dict]]:
    """(intro, rows) for the Council section: the saved Council in moe.json (moe.load_config), its main agent
    read-only and one editable row per advisor (model and effort, with the Council panel's own suggestions)."""
    from ..core import moe
    name = moe.CONFIG_PATH.name
    cfg = moe.load_config()
    if cfg is None:
        note = _council_note()          # an unusable file (DREAM-148); never an offer to overwrite it
        if note:
            return f"{note}, then refresh.", []
        state = f"{name} cannot be read" if moe.CONFIG_PATH.exists() else f"no Council is saved yet ({name})"
        return (f"The Council's members and their models live in {name}: {state}. Set the Council up in the Council "
                "panel; each advisor's model and effort can then be changed here.", [])
    label_of = lambda key: PROVIDERS[key].label if key in PROVIDERS else key  # noqa: E731
    source_text = f"Saved in {name}, the Council's own file"
    main = {"provider": cfg.orchestrator, "effort": cfg.orchestrator_effort}
    rows = [_plain_row("council.main", "Council main agent", "The provider that leads a Council session, and its "
                       "reasoning effort. Members: " + (", ".join(label_of(k) for k in cfg.advisors) or "none") + ".",
                       main, f"{label_of(cfg.orchestrator)} ({cfg.orchestrator})"
                       + (f" · effort {cfg.orchestrator_effort}" if cfg.orchestrator_effort else ""),
                       source="global", source_text=source_text, applies="When a Council session starts.",
                       locked="Change the main agent and the members in the Council panel.")]
    for key in cfg.advisors:
        model, effort = cfg.advisor_models.get(key), cfg.advisor_efforts.get(key)
        choice = choices.get(key, {})
        rows.append(_plain_row(
            f"council.advisor.{key}", f"Council advisor: {label_of(key)}",
            f"The model and reasoning effort {label_of(key)} is consulted with. Empty means the provider's own "
            "default.", {"model": model, "effort": effort},
            f"{model or 'its default model'} · effort {effort or 'default'}",
            source="global" if (model or effort) else "default", source_text=source_text,
            applies="The next Council consult.",
            edit={"kind": "council", "model": model or "", "effort": effort or "", "removable": bool(model or effort),
                  "models": [{"id": m["id"], "label": m["label"]} for m in choice.get("models", [])
                             if isinstance(m, dict) and "id" in m],
                  "efforts": list(choice.get("efforts", []))}))
    intro = (f"The saved Council ({name}): its main agent and members are set in the Council panel; each advisor's "
             "model and reasoning effort can be changed here. The running session takes a change when its Council "
             "is this saved one.")
    return intro, rows


def _save_council(key: str, value: Any) -> dict:
    """`settings_save` for a council.advisor.<key> row: the advisor's model and effort in moe.json, checked with
    the Council's own rules (council_config) and written only through moe.save_config; null clears both."""
    from dataclasses import asdict, replace
    from ..core import council_config, moe
    advisor = key.removeprefix("council.advisor.")
    if not key.startswith("council.advisor.") or not advisor:
        raise ValueError(f"{key} cannot be changed here; the Council main agent and members are set in the Council "
                         "panel.")
    cfg = moe.load_config()
    if cfg is None:
        note = moe.config_note(entry_only=True)        # DREAM-148: an unusable file is named, and stays as it is
        if note:
            raise ValueError(f"{key}: {note}; nothing was saved.")
        raise ValueError(f"{key}: no Council is saved yet. Set one up in the Council panel first.")
    if advisor not in cfg.advisors:
        raise ValueError(f"{key}: {advisor} is not a member of the saved Council. Add it in the Council panel first.")
    models = {k: v for k, v in cfg.advisor_models.items() if k != advisor}
    efforts = {k: v for k, v in cfg.advisor_efforts.items() if k != advisor}
    if value is not None:
        if not isinstance(value, dict) or set(value) - {"model", "effort"}:
            raise ValueError(f"{key}: enter a model and an effort only.")
        try:
            model = council_config.validate_model(value.get("model") or "", allow_empty=True)
        except ValueError:
            raise ValueError(f"{key}.model: enter a model name of up to 256 characters without control "
                             "characters.") from None
        effort = value.get("effort") or ""
        if effort:
            try:
                council_config.validate_effort(advisor, effort, model or None, structural_only=True)
            except ValueError:
                raise ValueError(f"{key}.effort: choose one of the efforts listed for {advisor}.") from None
        if model:
            models[advisor] = model
        if effort:
            efforts[advisor] = effort
    new = replace(cfg, advisor_models=models, advisor_efforts=efforts)
    try:
        council_config.parse_config(asdict(new))         # the file's own rules, before anything is written
    except ValueError:
        raise ValueError(f"{key}: Dream refused this value; nothing was saved.") from None
    try:
        moe.save_config(new)
    except OSError as exc:
        raise ValueError(f"{key}: {moe.CONFIG_PATH.name} could not be written ({exc.strerror or 'unknown error'}); "
                         "nothing was saved.") from None
    return {"key": key, "applies": ("Removed from" if value is None else "Saved to") + f" {moe.CONFIG_PATH.name}."}


# --- the engine layout (phase S3's module, read here) -----------------------------------------------------

def engine_layout() -> tuple[list[dict], str]:
    """The read-only "Local engine layout" rows and the section's text. The one place that reads
    dream.core.engine_layout.layout_view() (phase S3, built separately): without the module the section says the
    view is not available; a mapping gives one row per top-level entry (engine.<name>), anything else one row
    (engine.layout). Values pass through _mask like every other row's. Neither a missing module nor one that fails
    to import or to run may break the view: each is a note in the section's text."""
    tail = " Nothing to set here; Dream never restarts the engine from this tab."
    try:
        module = importlib.import_module("dream.core.engine_layout")
    except ImportError:
        return [], ("Which model runs on which card is not available in this build: the layout view arrives with "
                    "the multi-model engine (phase S3)." + tail)
    except Exception as exc:  # noqa: BLE001 -- an error while the module loads is its problem, not the tab's
        return [], f"The layout view failed to load ({type(exc).__name__})." + tail
    try:
        data = module.layout_view()
    except Exception as exc:  # noqa: BLE001 -- the tab must open whatever the layout module does
        return [], f"The engine layout could not be read ({type(exc).__name__})." + tail
    entries = list(data.items()) if isinstance(data, dict) else [("layout", data)]
    rows = [_layout_row(f"engine.{name}", value) for name, value in entries]
    return rows, ("Which model runs on which card, read from the engine layout. A change to the layout applies "
                  "when the engine restarts; Dream never restarts the engine from this tab.")


def _layout_row(key: str, value: Any) -> dict:
    masked = _mask(value, key)
    display = json.dumps(masked, ensure_ascii=False) if isinstance(masked, (dict, list)) else _display(masked, "Not set")
    return _plain_row(key, key.removeprefix("engine.").replace("_", " ").capitalize(),
                      "Read from the engine layout.", masked, display, source="global (engine layout)",
                      source_text="Read from the engine layout", applies="When the engine restarts.", restart=True,
                      restart_label="Engine restart",
                      locked="Read-only here: edit the layout itself; the change applies when the engine restarts.")


def _warnings(rows: list[dict], saved: dict, choices: dict[str, dict]) -> list[str]:
    """What the owner should look at, in plain words: rows not used in this session, the verifier caveat, and a
    critic whose program is not installed."""
    notes = []
    critic = saved.get("roles", {}).get("critic")
    if isinstance(critic, dict) and choices.get(critic.get("provider"), {}).get("available") is False:
        notes.append(f"Critic (roles.critic) names {critic['provider']}, whose program is not installed here; "
                     "/critique reports that failure until it is.")
    for row in rows:
        if row["origin"] != "not-applied":
            continue
        reason = row["source_text"].removeprefix("Not used in this session: ")
        if "keeps it in the file" in reason:
            notes.append(f"{row['label']} ({row['key']}) is kept in the settings file, but this version of Dream "
                         "does not use it.")
        else:
            notes.append(f"{row['label']} ({row['key']}) is not used in this session: {reason}.")
    if "verifier" in saved.get("roles", {}):
        notes.append("Verifier (roles.verifier): on an engine that holds one conversation at a time, the check still "
                     "runs on the main model, or is skipped, unless the engine shows it can serve the verifier's "
                     "model on its own. That is checked when the verifier runs.")
    return notes


def view(*, provider: str, model: str | None, session: dict | None = None, mode: str | None = None,
         running_max_tokens: int | None = None) -> dict:
    """The `settings_get` payload. An unusable settings file is reported (ok false) and never rewritten.
    `running_max_tokens` is the output ceiling this Dream process uses (config.MAX_OUTPUT_TOKENS): a saved
    output.max_tokens applies only when Dream starts, so "Output a turn may use now" is worked out from it."""
    from ..core import council_config
    from ..core.subagents import builtin_names
    project = settings.project_path()
    result: dict[str, Any] = {"ok": True, "error": None, "provider": provider, "model": model,
                              "file": str(settings_path()),
                              "project_file": str(project) if project is not None else None,   # DREAM-146
                              "sections": [], "warnings": [], "providers": _providers(),
                              "agents": sorted(builtin_names() - {"verifier", "filer"}),
                              "role_providers": list(settings.role_providers("subagents"))}   # the add-a-type form
    # The Council panel's own choices (availability, model suggestions, efforts): local prerequisites, no probe.
    choices = {row["key"]: row for row in council_config.provider_choices()}
    try:
        # As the runtime reads them (DREAM-146, gate round 2): an unusable project file is ignored as a whole and
        # reported below; the tab keeps working on the global values, and global saves still go through.
        saved = settings.read_runtime()
        rows = settings.effective(provider=provider, model=model, session=session, strict=False)
        session = dict(session or {})
        if running_max_tokens is not None and session.get("output.max_tokens") is None:
            running = settings.effective(provider=provider, model=model, strict=False,
                                         session={**session, "output.max_tokens": running_max_tokens})["output.ceiling"]
            rows["output.ceiling"] = (running if running.source != "session"
                                      else settings.Effective(running.value, "session (in effect since Dream started)"))
    except ValueError as exc:
        # The page shows only the error then, so an unusable Council file is said there too (DREAM-148).
        council = _council_note()
        result.update(ok=False, error=_file_error(exc) + (f" {council}." if council else ""))
        return result
    problem = settings.project_problem()
    result["project_error"] = (
        f"The project file {project} cannot be used and is ignored: {problem}. Dream never rewrites it; repair it "
        "by hand, then refresh. Saves to this computer still work; saves to this workspace are refused until then."
        if problem else None)
    extra = {"blender.binary": _blender_row(saved)}
    if mode:
        extra["permissions.mode"] = (mode, "session")
    grouped: dict[str, list[dict]] = {section: [] for section, *_ in SECTIONS}
    for key, (value, source) in [*rows.items(), *extra.items()]:
        grouped[_section_of(key)].append(_row(key, value, source, saved))
    intros = {}
    intros["council"], grouped["council"] = _council_section(choices)
    grouped["engine"], intros["engine"] = engine_layout()
    result["sections"] = [{"id": section, "title": title, "intro": intros.get(section, intro), "rows": grouped[section]}
                          for section, title, intro in SECTIONS if grouped[section] or section in ("engine", "council")]
    result["warnings"] = _warnings([row for rows_ in grouped.values() for row in rows_], saved, choices)
    if result["project_error"]:
        result["warnings"].insert(0, result["project_error"])
    council = _council_note() if not grouped["council"] else None
    if council:                         # DREAM-148: an unusable Council file is a setting to check
        result["warnings"].append(f"{council}.")
    return result


def _role_value(key: str, value: Any, scope: str = "global") -> dict:
    """A role's entry ({"model"?, "provider"?}), checked in plain words against the role's rule before anything
    is written. In the project scope (DREAM-146) the entry is the model alone; the provider stays the global file's."""
    name = key.removeprefix("roles.").split(".")[0]
    if name == "subagents" and not settings._AGENT.match(key.removeprefix("roles.subagents.")):
        raise ValueError(f"{key}: a sub-agent name uses lowercase letters, digits, - and _ (up to 64).")
    if not isinstance(value, dict):
        raise ValueError(f"{key}: enter a model name or choose a provider.")
    if set(value) - {"model", "provider"}:
        raise ValueError(f"{key}: a role holds only a model and a provider.")
    providers, model_required, provider_required = settings.role_rule(name)
    model, provider = value.get("model") or "", value.get("provider") or ""
    if scope == "project":
        if provider:
            raise ValueError(f"{key}.provider: a project file names models only; the provider comes from the global "
                             "file (save it with the global scope).")
        model_required, provider_required = True, False
    # The one model rule (core/settings._model_name, DREAM-147): printable text -- no control, invisible or
    # unencodable characters -- up to 256 characters, no space at either end.
    if (not isinstance(model, str) or (model_required and not model.strip()) or model != model.strip()
            or len(model) > 256 or not model.isprintable()):
        raise ValueError(f"{key}.model: enter a model name (printable text, up to 256 characters, no spaces at "
                         "either end).")
    if scope == "project":
        nested: dict = {"model": model}
        for part in reversed(key.removeprefix("roles.").split(".")):
            nested = {part: nested}
        settings.validate_project({"roles": nested})      # the project writer's own rules
        return {"model": model}
    if not isinstance(provider, str) or (provider and provider not in providers) or (provider_required and not provider):
        unset = (" (First available keeps the default and saves nothing)" if provider_required
                 else ", or leave it unset for the local MachX engine" if name not in ("evaluator", "reviewer")
                 else ", or leave it unset for the main model")
        raise ValueError(f"{key}.provider: choose {', '.join(providers)}{unset}.")
    if not model and not provider:
        raise ValueError(f"{key}: enter a model name or choose a provider.")
    entry = {**({"model": model} if model else {}), **({"provider": provider} if provider else {})}
    nested: dict = entry
    for part in reversed(key.removeprefix("roles.").split(".")):
        nested = {part: nested}
    settings.validate({"roles": nested})       # the writer's own rules, before anything is written
    return entry


def _max_tokens(value: Any) -> int:
    text = str(value) if type(value) is int else value if isinstance(value, str) else ""
    text = text.strip()
    number = int(text.replace("_", "").replace(",", "")) if _ASCII_NUMBER.match(text) else None
    if number is None or not settings.MIN_MAX_TOKENS <= number <= MAX_OVERRIDE_NUMBER:
        raise ValueError(f"output.max_tokens: enter a whole number from {settings.MIN_MAX_TOKENS:,} to "
                         f"{MAX_OVERRIDE_NUMBER:,}, in digits 0-9.")
    return number


def _engine_number(key: str, value: Any) -> str:
    """engine.parallel / engine.slot_ctx from the number field (DREAM-151): digits 0-9 (or an integer), checked by
    the writer's own rules; returned as the text `set_value` parses."""
    text = str(value) if type(value) is int else value.strip() if isinstance(value, str) else ""
    number = int(text.replace("_", "").replace(",", "")) if _ASCII_NUMBER.match(text) else None
    try:
        if number is None:
            raise ValueError
        settings._validate_engine({"engine": {key.split(".")[1]: number}})
    except ValueError:
        span = (f"1 to {settings.MAX_LANES}" if key == "engine.parallel"
                else f"0 (automatic) or 9 to {settings.MAX_SLOT_CTX:,}")
        raise ValueError(f"{key}: enter a whole number from {span}, in digits 0-9.") from None
    return str(number)


def save(key: Any, value: Any, scope: Any = "global") -> dict:
    """The `settings_save` action: set (or, with value null, remove) one editable setting, in the global file or
    (`scope` "project", DREAM-146) the workspace's project file. A refused value raises ValueError with a plain
    sentence naming the key, and nothing is written."""
    if not isinstance(key, str) or not key:
        raise ValueError("Choose a setting to save: the request named no key.")
    if scope not in settings.SCOPES:
        raise ValueError("scope: choose global (this computer) or project (this workspace).")
    if key.startswith("council."):
        if scope != "global":
            raise ValueError(f"{key}: the Council is one file for every workspace; save it with the global scope.")
        return {**_save_council(key, value), "scope": scope}   # the Council's own file and writer, not runtime-settings.json
    if not _editable(key):
        raise ValueError(f"{key} cannot be changed here" + (
            "; change it in Runtime \u2192 Next session." if key == "profile" or key.startswith("overrides.")
            else "."))
    env = _governing_env(key)
    if env:
        raise ValueError(f"{key} is set by the environment variable {env}. Change or unset it where Dream is "
                         "started, then restart Dream.")
    if key in settings.ENGINE_SETTINGS and scope != "global":
        raise ValueError(f"{key}: the engine is set on this computer only (the global scope); a project file may not "
                         "set it.")
    if key == settings.NESTED_SETTING and scope != "global":
        raise ValueError(f"{key}: the worker limit is set on this computer only (the global scope); a project file may "
                         "not set it.")
    try:
        # An unusable file is refused before any write: for the project scope either file, for the global scope
        # the global file alone -- a bad project file does not block saves to this computer (gate round 2).
        settings.read() if scope == "project" else settings.read_global()
    except ValueError as exc:
        raise ValueError(f"{key} was not saved. " + _file_error(exc)) from None
    try:
        if value is None:
            settings.unset_value(key, scope=scope)
        elif key == "output.max_tokens":
            settings.set_value(key, str(_max_tokens(value)), scope=scope)
        elif key == "behaviour.vitals":
            if type(value) is not bool:
                raise ValueError("behaviour.vitals: choose on or off.")
            settings.set_value(key, "on" if value else "off", scope=scope)
        elif key in settings.ENGINE_SETTINGS:
            settings.set_value(key, _engine_number(key, value), scope=scope)
        elif key == settings.NESTED_SETTING:
            # DREAM-197: the number, or its text (a <select> posts text); the writer's rule decides.
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(settings._MAX_WORKERS_TEXT)
            settings.set_value(key, str(value).strip(), scope=scope)
        elif key in (settings.STYLE_SETTING, *settings.CONTEXT_SETTINGS):
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(f"{key}: choose one of the listed values.")
            settings.set_value(key, str(value), scope=scope)
        else:
            settings.set_role(key, _role_value(key, value, scope), scope=scope)    # the whole role in one validated write
    except ValueError as exc:
        message = str(exc)
        if message.startswith(("Cannot use project settings", "Cannot read runtime settings",
                               "Cannot use runtime settings")):
            message = f"{key} was not saved. " + _file_error(exc)     # the file's problem, with its entry named
        elif not message.startswith(key) or "'" in message:           # the writer's own wording: keep only the key
            message = f"{key}: Dream refused this value; nothing was saved."
        raise ValueError(message) from None
    except OSError as exc:
        raise ValueError(f"{key}: the settings file could not be written ({exc.strerror or 'unknown error'}); "
                         "nothing was saved.") from None
    applies = ("Removed. " if value is None else "Saved. ") + _applies(key)[0]
    if scope == "project":
        applies += " In this workspace's project file."
    return {"key": key, "scope": scope, "applies": applies}
