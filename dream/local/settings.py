"""Validated settings for one local MachX launch and its Dream session."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import math
import os


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
    Control("context_overflow", "compact", "compact old history or error without eliding", kind="overflow"),
    Control("threads", None, "CPU expert-work threads; default = engine auto; 1–1024", "--threads", "int", 1, 1024),
    Control("prefill_chunk", None, "prompt processing chunk; default = engine; >=1", "--prefill-chunk", "int", 1, 2**31 - 1),
    Control("parallel", 1, "server request slots; 1–4", "--parallel", "int", 1, 4),
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
        if value not in ("compact", "error"):
            raise ValueError("Choose compact or error")
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
        raise ValueError(control.hint)
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
        if control.name == "parallel" and capabilities.get("architecture") == "deepseek_v41":
            control = replace(control, maximum=1, hint="V4.1 serves one request at a time; parallel = 1")
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
    if architecture == "deepseek_v41" and result.get("parallel", 1) != 1:
        raise ValueError("V4.1 requires parallel = 1")
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
