"""Validated settings for one local MachX launch and its Dream session."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import math
import os
from .models import (DIRECTORY_ARCHITECTURES, LANE_ARCHITECTURES, LANE_IMAGE_ARCHITECTURES, LANE_MODEL_NAMES,
                     LANE_MODELS_LISTED)


@dataclass(frozen=True)
class Control:
    name: str
    default: object
    hint: str
    flag: str | None = None
    kind: str = "float"
    minimum: float = 0
    maximum: float = 2
    choices: tuple[str, ...] = ()
    source: str = "Dream fallback"
    refusal: str | None = None      # what a value outside the range is told (DREAM-201 gate); the hint when None


CONTROLS = (
    Control("temperature", .7, "0 = greedy; 0–2", "--temp"),
    Control("top_k", 40, "0 = sampler ceiling; 0–1024", "--top-k", "int", 0, 1024),
    Control("top_p", .95, "probability mass; >0–1", "--top-p", maximum=1),
    Control("min_p", 0., "relative probability cutoff; 0–1", "--min-p", maximum=1),
    Control("repeat_penalty", 1., "1 = off; >0–10", "--repeat-penalty", maximum=10),
    Control("repeat_last_n", 64, "history for all penalties; 0–512", "--repeat-last-n", "int", 0, 512),
    Control("presence_penalty", 0., "penalty for seen tokens; -2–2", "--presence-penalty", minimum=-2),
    Control("frequency_penalty", 0., "penalty per occurrence; -2–2", "--frequency-penalty", minimum=-2),
    Control("seed", 0, "0 = random", "--seed", "int", 0, 2**53 - 1),
    Control("max_tokens", 4096, "maximum output per generation; bounded by context", "--max-tokens", "int", 1, 2**32 - 1),
    Control("stop", [], 'JSON string array, e.g. ["END", "\\nUser:"]; [] = none', "--stop", "stop"),
    Control("context_overflow", "compact", "when the context fills: compact old history, handoff (one Handoff, "
            "then keep going) or stop (end the turn with a summary)", kind="overflow",
            choices=("compact", "handoff", "stop")),
    Control("threads", None, "CPU expert-work threads; default = engine auto; 1–1024", "--threads", "int", 1, 1024),
    Control("prefill_chunk", None, "prompt processing chunk; default = engine; >=1", "--prefill-chunk", "int", 1, 2**31 - 1),
    # DREAM-201: `ie serve --parallel N` takes 1..16 for every model (engine v0.2.0, kMaxParallel); 4 was the first lanes
    # engine's range. The lanes models' hint (available_controls) says what a lane costs; a refusal is one sentence.
    Control("parallel", 1, "server request slots; 1–16", "--parallel", "int", 1, 16,
            refusal="parallel must be a whole number from 1 to 16"),
    # DREAM-100: the auto expert tier's per-card headroom (the forward's growth + the vision tower's encode block); raise it
    # for very long contexts. Shown only when the engine advertises it (mimo_v2, deepseek41); unset = the engine's default.
    Control("vram_reserve_gib", None, "VRAM kept free per card in GiB; default = the engine's own; raise for long contexts; 0–24",
            "--vram-reserve-gib", "float", 0, 24),
    Control("slot_ctx", 0, "context per slot; 0 = automatic, otherwise >=9", "--slot-ctx", "int", 0, 2**31 - 1),
    Control("prompt_cache", True, "reuse eligible prompt state; on/off", "--no-prompt-cache", "bool"),
    Control("thinking", None, "on/off; controls model thinking template", "--thinking", "bool"),
    Control("reasoning_effort", None, "model reasoning effort", "--reasoning-effort", "enum",
            choices=("low", "medium", "high", "max", "xhigh")),
    Control("int8_kv", False, "INT8 KV cache; requires one GPU and one slot", "--int8-kv", "bool"),
    Control("speculative", False, "MTP speculation; requires temperature 0 and neutral penalties", "--spec", "bool"),
    Control("spec_k", 2, "speculative draft length; 1–64", "--spec-k", "int", 1, 64),
)
BY_NAME = {c.name: c for c in CONTROLS}
SESSION_ENV = "DREAM_MACHX_SESSION_OPTIONS"


def parse_value(control: Control, raw: str):
    if raw.strip().lower() == "default":
        return control.default
    if control.kind == "stop":
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ValueError("Use a JSON array of literal strings") from exc
        if (not isinstance(value, list) or len(value) > 4
                or any(not isinstance(s, str) or not s or "\0" in s for s in value)):
            raise ValueError("Use up to four nonempty strings without NUL; [] clears stops")
        return value
    if control.kind == "enum":
        value = raw.strip().lower()
        if value not in control.choices:
            raise ValueError("Choose " + ", ".join(control.choices))
        return value
    if control.kind == "overflow":
        value = raw.strip().lower()
        value = "stop" if value == "error" else value     # the pre-DREAM-175 name of stop: old saved launches load
        if value not in control.choices:
            raise ValueError("Choose compact, handoff or stop")
        return value
    if control.kind == "bool":
        value = raw.strip().lower()
        if value not in ("on", "off"):
            raise ValueError("Choose on, off, or default")
        return value == "on"
    try:
        value = int(raw) if control.kind == "int" else float(raw)
    except ValueError as exc:
        raise ValueError("Enter a whole number" if control.kind == "int" else "Enter a number") from exc
    if (not math.isfinite(value) or not control.minimum <= value <= control.maximum
            or (control.name in ("top_p", "repeat_penalty") and value == 0)
            or (control.name == "slot_ctx" and 0 < value < 9)):
        raise ValueError(control.refusal or control.hint)
    return value


def available_controls(capabilities: dict) -> list[Control]:
    advertised = set(capabilities.get("sampling", [])) | set(capabilities.get("load", []))
    if "repeat_window" in advertised:
        advertised.add("repeat_last_n")
    features = capabilities.get("features", {})
    if features.get("int8_kv"):
        advertised.add("int8_kv")
    if features.get("speculative"):
        advertised.update(("speculative", "spec_k"))
    defaults = capabilities.get("defaults", {})
    reasoning = capabilities.get("reasoning", {})
    controls = []
    for control in CONTROLS:
        if control.name != "context_overflow" and control.name not in advertised:
            continue
        if control.name == "prompt_cache" and not features.get("prompt_cache"):
            continue
        if control.name == "parallel" and capabilities.get("architecture") in LANE_ARCHITECTURES:
            architecture = capabilities.get("architecture")
            if architecture in DIRECTORY_ARCHITECTURES:
                # DREAM-151: the engine serves this model in lanes (P4 B4); Settings engine.parallel wins when set.
                # DREAM-154: V4.1's lanes (P4 B6b) need two cards and keep its images.
                # DREAM-201: 1-16 (the engine's range since v0.2.0; 4 was its first lanes build's). The words about VRAM
                # are the engine session's measurements: every extra lane is reserved at load out of the in-VRAM expert
                # cache (MiMo at 16 lanes of 32,768 tokens: 10.2 / 9.0 GB per card), and these models are bound by expert
                # reads.
                control = replace(control, hint="requests served at once, in lanes; 1–16 (every extra lane reserves VRAM "
                                                "at load out of the expert cache, so more lanes can slow each one, and a "
                                                "count that does not fit is refused with the engine's numbers: set the "
                                                "agents really run at once, with a realistic slot_ctx); Settings "
                                                "engine.parallel replaces it when set; " + ("2 or more need two cards"
                                                if architecture == "deepseek_v41" else "images need 1"))
            else:
                # DREAM-207: the GGUF lanes models (engine v0.2.0): their lanes run on two cards, and an extra lane is
                # taken out of VRAM at load (the engine's README: about 0.55 GiB per card at 32K on Qwen3.8-Flash, 0.34
                # on the 35B-A3B class; the 27B prints its own numbers at load). Dream sends no images on lanes (the
                # engine refuses them on Qwen3.8-Flash; the 35B class has no vision path). The rails are engine_lanes's.
                control = replace(control, hint="requests served at once, in lanes; 1–16 (every extra lane reserves VRAM "
                                                "at load on both cards, and a count that does not fit is refused with "
                                                "the engine's numbers); Settings engine.parallel replaces it when set; "
                                                "2 or more need two cards; images need 1")
        elif control.name == "parallel" and capabilities.get("architecture") in DIRECTORY_ARCHITECTURES:
            control = replace(control, maximum=1, hint="This model serves one request at a time; parallel = 1",
                              refusal=None)   # its hint is its refusal, not the lanes models' 1-16 sentence
        if control.name in defaults:
            value = defaults[control.name]
            # MachX uses zero for automatic thread selection; the UI uses None.
            if control.name == "threads" and value == 0 and not isinstance(value, bool):
                value = None
            try:
                # Only scalar validation here. Cross-field constraints belong to
                # the complete selection (e.g. speculative=True with temperature=0).
                if value is not None:
                    if control.kind in ("int", "float") and (isinstance(value, bool) or not isinstance(value, (int, float))):
                        raise ValueError("expected a numeric backend default")
                    if control.kind == "bool" and not isinstance(value, bool):
                        raise ValueError("expected a boolean backend default")
                    raw = json.dumps(value) if control.kind == "stop" else "on" if value is True else "off" if value is False else str(value)
                    value = parse_value(control, raw)
                elif control.default is not None:
                    raise ValueError("unexpected null backend default")
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid MachX default for {control.name}: {exc}") from exc
            control = replace(control, default=value, source="MachX backend")
        if control.name == "reasoning_effort":
            levels = tuple(reasoning.get("effort_levels", []))
            if not levels:
                continue
            default = defaults.get("reasoning_effort", reasoning.get("default_effort"))
            if default not in levels or any(level not in control.choices for level in levels):
                raise ValueError("Invalid model reasoning effort capabilities; rebuild MachX")
            control = replace(control, default=default, choices=levels,
                              hint="Choose " + ", ".join(levels), source="Model reasoning template")
        elif control.name == "thinking":
            default = defaults.get("thinking")
            if not isinstance(default, bool):
                raise ValueError("Missing model thinking default; rebuild MachX")
            control = replace(control, default=default,
                              hint=reasoning.get("thinking_description") or control.hint)
        if control.name in capabilities.get("default_sources", {}):
            control = replace(control, source=capabilities["default_sources"][control.name])
        controls.append(control)
    return controls


def validate_options(options: dict, *, ctx: int | None = None, gpus: int | None = -1,
                     architecture: str | None = None) -> dict:
    # -1 means no launch topology supplied (request settings validation only).
    # None means the launcher's auto topology, which cannot promise one GPU.
    if not isinstance(options, dict):
        raise ValueError("MachX settings must be an object")
    result = {}
    for name, value in options.items():
        if name not in BY_NAME:
            raise ValueError(f"Unknown MachX setting: {name}")
        control = BY_NAME[name]
        if value is None and control.default is None:
            result[name] = None
            continue
        if control.kind in ("int", "float") and isinstance(value, bool):
            raise ValueError(f"{name} must be numeric")
        raw = (json.dumps(value) if control.kind == "stop" else
               "on" if value is True else "off" if value is False else str(value))
        result[name] = parse_value(control, raw)
    if architecture in DIRECTORY_ARCHITECTURES and architecture not in LANE_ARCHITECTURES and result.get("parallel", 1) != 1:
        raise ValueError("This model requires parallel = 1")
    if ctx and result.get("slot_ctx", 0) > ctx:
        raise ValueError("slot_ctx cannot exceed context length")
    if result.get("int8_kv") and ((gpus != -1 and gpus != 1) or result.get("parallel", 1) != 1):
        raise ValueError("INT8 KV requires an explicit one-GPU selection and parallel = 1")
    if result.get("speculative"):
        neutral = (result.get("repeat_last_n", 64) == 0 or
                   (result.get("repeat_penalty", 1) == 1
                    and result.get("presence_penalty", 0) == 0
                    and result.get("frequency_penalty", 0) == 0))
        if result.get("temperature", .7) != 0 or not neutral:
            raise ValueError("Speculation requires temperature = 0 and neutral penalties or repeat_last_n = 0")
    return result


def engine_lanes(options: dict, architecture: str | None, *, ctx: int | None,
                 gpus: int | None = None) -> tuple[dict, int | None, str | None]:
    """DREAM-151: (the options one `ie serve` launch uses, the lanes it asks for, a sentence to show). The owner's
    engine.parallel / engine.slot_ctx (the global runtime settings, core/settings.engine_launch) apply to a model whose
    engine serves requests in lanes (LANE_ARCHITECTURES) and replace its own `parallel`; `slot_ctx` goes only with two
    or more lanes. Any other model keeps its own setting, and the sentence says why. With neither set, `options` come
    back as they are (the command is today's, byte for byte, plus --max-queue with 2 or more lanes: DREAM-209,
    machx.lanes_queue). Lanes are None unless a lanes model asks for 2 or more;
    an unusable settings file, or a slot_ctx larger than `ctx`, raises before anything loads. `options` is not
    changed. DREAM-154: DeepSeek-V4.1 serves lanes too; a V4.1 launch with lanes that its engine would refuse
    (_v41_lanes_refusal; `gpus` is the launch's GPU count) raises before anything loads, and its sentence keeps the
    images. DREAM-207: Qwen3.8-Flash, the 35B-A3B class and the 27B serve lanes too, with the same precedence (the
    setting, when set, replaces the model's own `parallel`; unset, the model's own stands and the command is today's)
    and their own rails: a launch that chose 1 GPU, IE_P2P on Qwen3.8-Flash and INT8 KV on any lanes model are refused
    before anything loads (_lanes_refusal); speculation on the 27B, the engine's own lanes switches in Dream's
    environment and, with GPUs on auto, the 35B-A3B class's and the 27B's need for two cards are said in the sentence
    (_lanes_notices); the 27B's extra lanes hold 65,536 tokens by default."""
    from ..core.settings import engine_launch
    wanted = engine_launch()
    own = options.get("parallel", 1)
    if architecture not in LANE_ARCHITECTURES:
        if wanted.get("parallel", 1) > 1:
            # DREAM-201 gate: Dream's own scope, not an engine limit (GLM-5.3 and DeepSeek-V4-Flash run --parallel as
            # turns; models.LANE_ARCHITECTURES says which models are in the table and why).
            return options, None, (f"Settings engine.parallel ({wanted['parallel']}) does not apply to "
                                   f"{architecture or 'this model'}: Dream applies it to {LANE_MODELS_LISTED} "
                                   f"only; this model starts with its own parallel setting ({own}), which takes 1–16 "
                                   "in its launch settings.")
        return options, None, None
    launched = dict(options)
    if "parallel" in wanted:
        launched["parallel"] = wanted["parallel"]
    lanes = launched.get("parallel", 1)
    if lanes > 1 and "slot_ctx" in wanted:
        if ctx and wanted["slot_ctx"] > ctx:
            raise ValueError(f"engine.slot_ctx ({wanted['slot_ctx']:,}) is larger than this launch's context "
                             f"({ctx:,}); lower it in Settings (Engine lanes) or raise the context")
        launched["slot_ctx"] = wanted["slot_ctx"]
    if lanes <= 1:
        note = (f"Settings engine.parallel (1) replaces this model's own parallel ({own}): one request at a time."
                if lanes != own else None)
        return launched, None, note
    refusal = _lanes_refusal(architecture, gpus, launched)
    if refusal:
        cause, variable, undo = refusal
        fix = ("set engine.parallel to 1 (Settings, Engine lanes)" if "parallel" in wanted
               else "set this model's parallel to 1")
        name = LANE_MODEL_NAMES[architecture]
        raise ValueError(f"{name[0].upper() + name[1:]} cannot serve {lanes} lanes here: {cause}; "
                         + (f"unset {variable} or " if variable else "") + (f"{undo}, or " if undo else "") + f"{fix}.")
    extra = launched.get("slot_ctx") or 0
    # The engine's --slot-ctx 0: 32,768 tokens on MiMo-V2.6, DeepSeek-V4.1, Qwen3.8-Flash and the 35B-A3B class
    # (q4e_lane_ctx), 65,536 on the 27B (q27_lane_ctx, its joint-step banks' default).
    size = f"{extra:,} tokens" if extra else f"the engine's own {65536 if architecture == 'qwen35' else 32768:,} tokens"
    others = f"lane 2 holds {size}" if lanes == 2 else f"lanes 2-{lanes} hold {size} each"
    source = "Settings engine.parallel" if "parallel" in wanted else "this model's own parallel setting"
    images = ("images still work (an image prompt prefills while the other lanes wait); no routing profile is "
              "recorded (the engine records one at 1 lane only)." if architecture in LANE_IMAGE_ARCHITECTURES
              else "images need 1 lane, so Dream sends none.")
    note = (f"{lanes} lanes ({source}): up to {lanes} requests, the main model's and sub-agents', run at once; "
            f"{others} (at most the context); {images}")
    return launched, lanes, " ".join([note, *_lanes_notices(architecture, launched, gpus)])


def _v41_lanes_refusal(gpus: int | None) -> tuple[str, str | None] | None:
    """DREAM-154: why DeepSeek-V4.1's engine would refuse to load with lanes (--parallel > 1; engine P4 B6b,
    ds41_engine.cpp), when Dream can tell before loading: (the cause, the environment variable to unset or None), or
    None. In the engine's order. The engine inherits Dream's environment (machx._launch adds only Dream's routing-profile
    default, and only at one lane). Its loader takes every visible Arc card whatever --gpus says, and the preflight
    holds a directory model's GPUs at auto or all detected (desktop/startup.launch_preflight), so one GPU here is a
    one-card machine; auto (None) is left to the engine. Expert parallel is refused only after the weights are up."""
    env = os.environ
    if env.get("IE_DS41_SPEC") == "1":
        return ("IE_DS41_SPEC=1 (DSpark speculation) is set in Dream's environment, and the engine runs DSpark at 1 "
                "lane only", "IE_DS41_SPEC")
    for name, what in (("IE_DS41_PROFILE_OUT", "records a routing profile"), ("IE_DS41_DUMP_ROUTING", "dumps routing")):
        if env.get(name):
            return f"{name} is set in Dream's environment, and the engine {what} at 1 lane only", name
    if gpus == 1:
        return "its engine serves lanes on two cards, and this launch has 1 GPU", None
    expert_parallel = env.get("IE_DS41_EP", "")
    if expert_parallel and expert_parallel != "0":
        return (f"IE_DS41_EP={expert_parallel} (expert parallel) is set in Dream's environment, and the engine's lanes "
                "refuse expert parallel", "IE_DS41_EP")
    return None


def _lanes_refusal(architecture: str | None, gpus: int | None, launched: dict) -> tuple[str, str | None, str | None] | None:
    """DREAM-154 / DREAM-207: why this model's engine would refuse the lanes asked for, when Dream can tell before
    loading: (the cause, the environment variable to unset or None, another way out or None), or None. INT8 KV is
    not compatible with `--parallel` above 1 on any model (src/cli/main.cpp; the 27B's init_lanes refuses it too), and
    without this check validate_options would only trip inside machx.serve. DeepSeek-V4.1's causes are
    _v41_lanes_refusal's. The GGUF lanes models refuse a launch that chose 1 GPU -- `gpus` is the owner's choice for
    them and auto (None) the engine's; engine.cpp: qwen4exp time-slices the requests on one card instead of serving
    lanes, the 35B-A3B class and the 27B split over both cards -- and Qwen3.8-Flash's lanes run the host handoff,
    which the engine refuses when IE_P2P is in its environment, whatever its value (std::getenv != nullptr). With
    IE_Q4E_LANES=0 it time-slices instead, runs no host handoff and refuses nothing (DREAM-207 gate; _lanes_notices says
    so; its two-card time-sliced path still hands off between its stages). The way out on one card is 2 GPUs, or auto
    for Qwen3.8-Flash alone: on auto the engine may place the 35B-A3B class and the 27B on one card (the auto sentence)."""
    if launched.get("int8_kv"):
        return "INT8 KV cache is on, and the engine serves no lanes with it", None, "turn int8_kv off"
    if architecture == "deepseek_v41":
        refusal = _v41_lanes_refusal(gpus)
        return (refusal[0], refusal[1], None) if refusal else None
    if architecture in ("qwen4exp", "qwen35moe", "qwen35"):
        if gpus == 1:
            return ("its lanes need two cards, and this launch has 1 GPU", None,
                    "choose 2 GPUs or auto" if architecture == "qwen4exp" else "choose 2 GPUs")
        if (architecture == "qwen4exp" and "IE_P2P" in os.environ
                and not os.environ.get("IE_Q4E_LANES", "").startswith("0")):
            return ("IE_P2P is set in Dream's environment, and Qwen3.8-Flash's lanes run the host handoff (the engine "
                    "refuses them with IE_P2P, whatever its value)", "IE_P2P", None)
    return None


def _lanes_notices(architecture: str | None, launched: dict, gpus: int | None) -> list[str]:
    """DREAM-207: what the engine would do with these lanes other than serve them on its lanes path, when Dream can
    tell (engine.cpp): the 27B with --spec, or with IE_QWEN35_LANES=0, takes its joint-step path (the engine measured
    63 tok/s at 16 lanes against 108); IE_Q4E_LANES=0 time-slices Qwen3.8-Flash's requests; IE_Q35MOE_LANES=0 serves
    the 35B-A3B class one at a time. A value starting with 0 is what the engine reads as off (" 0" is on), and the
    notice names the value set (DREAM-207 gate, round 3). DREAM-207 gate: with GPUs
    on auto (None) the engine places the 35B-A3B class and the 27B by fit, and on one card serves them no lanes while
    /props total_slots still echoes --parallel (neither the one-card rail nor lanes_report can tell), so the sentence
    says to choose 2 GPUs; Qwen3.8-Flash on auto takes two cards when there are two."""
    def off(name):
        return os.environ.get(name, "").startswith("0")
    said = []
    if architecture == "qwen35":
        if launched.get("speculative"):
            said.append("Speculation is on, so the engine serves these lanes through its joint-step path (63 against 108 "
                        "tok/s at 16 lanes in the engine's measurements): turn speculation off for the lanes path.")
        elif off("IE_QWEN35_LANES"):
            said.append(f"IE_QWEN35_LANES={os.environ['IE_QWEN35_LANES']} is set in Dream's environment, so the engine "
                        "serves these lanes through its joint-step path.")
    elif architecture == "qwen4exp" and off("IE_Q4E_LANES"):
        said.append(f"IE_Q4E_LANES={os.environ['IE_Q4E_LANES']} is set in Dream's environment, so the engine time-slices "
                    "these requests instead of serving them in lanes.")
    elif architecture == "qwen35moe" and off("IE_Q35MOE_LANES"):
        said.append(f"IE_Q35MOE_LANES={os.environ['IE_Q35MOE_LANES']} is set in Dream's environment, so the engine "
                    "serves these requests one at a time instead of in lanes.")
    if gpus is None and architecture in ("qwen35moe", "qwen35"):
        said.append(f"GPUs are on auto, so the engine picks the card count by fit, and these lanes need two cards: on one "
                    f"card it does not serve them as lanes, while its /props still reports {launched['parallel']}; "
                    "choose 2 GPUs.")
    return said


def lanes_report(requested: int, served: int | None, architecture: str | None) -> str:
    """DREAM-151: what a launch that asked for `requested` lanes got -- the running server's /props total_slots."""
    if served is None:
        return (f"asked for {requested} lanes; the engine did not say how many lanes it serves (no /props "
                "total_slots)")
    if served == requested:
        return f"MachX serving {served} lanes: up to {served} requests run at once" + (
            "" if architecture in LANE_IMAGE_ARCHITECTURES else "; images need 1 lane")
    lanes = f"{served} lane{'' if served == 1 else 's'}"
    return (f"MachX serves {lanes}, not the {requested} asked for" + (
        f": this engine build does not serve {architecture} in lanes -- update the engine, or set engine.parallel to 1"
        if served == 1 else ""))


def engine_parallel_source(architecture: str | None, source: str) -> str:
    """The `parallel` row's source in a launch-settings table (DREAM-151): for a lanes model, that Settings
    engine.parallel replaces it at launch when set."""
    if architecture not in LANE_ARCHITECTURES:
        return source
    from ..core.settings import engine_launch
    try:
        wanted = engine_launch().get("parallel")
    except ValueError:
        return source
    if wanted is None:
        return source
    return (f"{source} · replaced" if source else "Replaced") + f" at launch by Settings engine.parallel ({wanted})"


def server_args(options: dict, *, ctx: int | None = None, gpus: int | None = None) -> list[str]:
    args = []
    for name, value in validate_options(options, ctx=ctx, gpus=gpus).items():
        control = BY_NAME[name]
        if value is None or not control.flag:
            continue
        if name == "prompt_cache":
            if not value:
                args.append(control.flag)
        elif name in ("int8_kv", "speculative"):
            if value:
                args.append(control.flag)
        elif name == "stop":
            for stop in value:
                args.extend([control.flag, stop])
        else:
            args.extend([control.flag, ("on" if value else "off") if control.kind == "bool" else str(value)])
    return args


def _validated_session_capabilities(capabilities: dict) -> dict:
    """Detach bounded JSON metadata and validate the existing local control schema."""
    try:
        if not isinstance(capabilities, dict):
            raise ValueError("capabilities must be an object")
        raw = json.dumps(capabilities, allow_nan=False)
        if len(raw.encode()) > 65_536:
            raise ValueError("capabilities exceed 64 KiB")
        value = json.loads(raw)
        for key in ("load", "sampling"):
            if key in value and (not isinstance(value[key], list) or len(value[key]) > 256
                                 or any(not isinstance(name, str) for name in value[key])):
                raise ValueError("invalid advertised controls")
        for key in ("features", "defaults", "reasoning", "default_sources"):
            if key in value and not isinstance(value[key], dict):
                raise ValueError("invalid capability section")
        available_controls(value)
        from ..core.capabilities import capability_report
        if capability_report(machx_capabilities=value)["warnings"]:
            raise ValueError("invalid reasoning capabilities")
        return value
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError("Invalid session capability metadata") from exc


@contextmanager
def session_options(options: dict, model: str, *, capabilities: dict | None = None):
    """Scope request preferences to this launch; never leak into another model."""
    old = os.environ.get(SESSION_ENV)
    value = {"model": model, "options": validate_options(options)}
    if capabilities is not None:
        value["capabilities"] = _validated_session_capabilities(capabilities)
    os.environ[SESSION_ENV] = json.dumps(value)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(SESSION_ENV, None)
        else:
            os.environ[SESSION_ENV] = old


def read_session_options(model: str) -> dict:
    raw = os.environ.get(SESSION_ENV)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        if value["model"] != model:
            return {}
        return validate_options(value["options"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid {SESSION_ENV}: {exc}") from exc


def read_session_capabilities(model: str) -> dict:
    """Read only capabilities explicitly captured for this model's launch."""
    raw = os.environ.get(SESSION_ENV)
    if not raw:
        return {}
    try:
        if len(raw.encode()) > 131_072:
            raise ValueError("session metadata exceeds 128 KiB")
        value = json.loads(raw)
        if value["model"] != model:
            return {}
        return _validated_session_capabilities(value.get("capabilities", {}))
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Invalid session capability metadata") from exc
