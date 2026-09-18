"""`dream local` — pick a GGUF, set context + GPUs, boot MachX, warm SearXNG, and run
the harness on the local model. Everything else (Camoufox, memory, tools, the loop) is
identical to the Claude path; only the reasoning backend changes.
"""

from __future__ import annotations

import asyncio
import atexit
import subprocess
from pathlib import Path

from rich import box as rbox
from rich.console import Console
from rich.table import Table

from .. import config
from ..tui.render import Renderer
from ..web import searxng
from . import machx

_PROMPT_MARK = "\x1b[38;2;139;92;246m ❯\x1b[0m "  # brand violet


async def _ask(prompt: str, *, on_interrupt: str = "") -> str:
    try:
        return (await asyncio.to_thread(input, _PROMPT_MARK + prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        return on_interrupt


# `ie serve` without --ctx uses the engine's built-in default, not the model's
# trained context (EngineOptions::max_ctx in the IE repo). Keep in sync.
_ENGINE_DEFAULT_CTX = 8192


def parse_count(raw: str) -> int | None:
    """Human-typed integer → int. '222,000', '128_000' and '64 000' all count:
    a silently dropped separator once booted a 222k session at the engine's 8k
    default. None for blank or unparseable (callers warn on the latter)."""
    s = raw.replace(",", "").replace("_", "").replace(" ", "")
    return int(s) if s.isdigit() else None


def _browse_dialog() -> str | None:
    """Native folder-picker window (zenity on GNOME). None if unavailable/cancelled."""
    try:
        res = subprocess.run(
            ["zenity", "--file-selection", "--directory",
             "--title=Dream — choose your workspace"],
            capture_output=True, text=True, timeout=180,
        )
        return res.stdout.strip() or None if res.returncode == 0 else None
    except Exception:
        return None


async def _pick_workspace(r: Renderer) -> Path | None:
    """Where should Dream work this session? Enter opens a folder-picker window;
    a typed path works too; '.' means Dream's own repo. Reads are allowed anywhere,
    but writes outside the chosen workspace always require explicit approval."""
    raw = await _ask("workspace (Enter = choose in a window · '.' = Dream itself · or type a path) · ")
    if raw in (".", ""):
        if raw == ".":
            return None  # None → Dream's own repo (engine default)
        picked = await asyncio.to_thread(_browse_dialog)
        if picked:
            return Path(picked)
        r.system("no folder chosen — working in Dream's own repo")
        return None
    p = Path(raw).expanduser()
    if p.is_dir():
        return p.resolve()
    r.error(f"'{p}' is not a directory — working in Dream's own repo instead.")
    return None


async def _run_harness(
    r: Renderer,
    *,
    provider: str,
    model: str | None,
    consolidate_on_exit: bool,
    keep_hot: bool,
    moe: object | None = None,
) -> None:
    """Shared tail for every launch path: pick a workspace, warm SearXNG, run the
    App, and — for a local model — unload MachX on the way out. Used by the direct
    `dream local` flow and by the provider picker (Claude, local models, and MoE)."""
    workspace = await _pick_workspace(r)
    if workspace:
        r.system(f"workspace · {workspace}  (reads anywhere; writes outside ask first)")

    r.system("warming SearXNG…")
    up = await searxng.ensure_up()
    r.system("SearXNG ready" if up else "SearXNG unavailable (web_search will report it)")

    from ..tui.app import App

    app = App(
        provider=provider, model=model,
        consolidate_on_exit=consolidate_on_exit, workspace=workspace, moe=moe,
        gui=config.GUI,
    )
    try:
        await app.run()
    finally:
        if provider != "machx":
            return
        # Dream owns the MachX lifecycle: leaving home turns the lights off.
        if keep_hot:
            r.system("MachX left hot (--keep-hot) — model stays loaded in GPU memory")
        else:
            r.system("unloading MachX — freeing GPU memory…")
            stopped = await asyncio.to_thread(machx.stop)
            r.system("GPU memory freed" if stopped else "MachX was already down")


async def _edit_options(console: Console, r: Renderer, capabilities: dict, ctx: int, gpus: int | None = None, *, initial: dict | None = None, sources: dict | None = None) -> dict | None:
    from .settings import available_controls, parse_value, validate_options
    import json

    controls = available_controls(capabilities)
    values = {c.name: c.default for c in controls}
    values.update(initial or {})
    sources = dict(sources or {c.name: c.source for c in controls})
    r.system("MachX tuning · edit a name or number, Enter starts, 'cancel' quits. "
             "Settings apply to this launch and its Dream session.")
    while True:
        table = Table(box=rbox.SIMPLE, title="model tuning", show_lines=False)
        for column in ("#", "setting", "value", "source", "meaning"):
            table.add_column(column)
        for i, c in enumerate(controls, 1):
            value = values[c.name]
            shown = ("default" if value is None else "on" if value is True else
                     "off" if value is False else json.dumps(value) if isinstance(value, list) else str(value))
            table.add_row(str(i), c.name, shown, sources.get(c.name, c.source), c.hint)
        console.print(table)
        selected = (await _ask("setting #/name (Enter = start · cancel = quit) · ", on_interrupt="cancel")).lower()
        if selected in ("cancel", "quit", "q"):
            return None
        if not selected or selected == "start":
            try:
                return validate_options(values, ctx=ctx, gpus=gpus, architecture=capabilities.get('architecture'))
            except ValueError as exc:
                r.error(str(exc))
                continue
        control = next((c for i, c in enumerate(controls, 1)
                        if selected in (str(i), c.name)), None)
        if control is None:
            r.error("Choose a setting name or number from the table.")
            continue
        raw = await _ask(f"{control.name} (blank = keep · default = reset) · ", on_interrupt="cancel")
        if raw == "cancel":
            return None
        if not raw:
            continue
        try:
            values[control.name] = parse_value(control, raw)
            sources[control.name] = control.source if raw.strip().lower() == "default" else "Your edit"
        except ValueError as exc:
            r.error(str(exc))


async def _choose_model_settings(console: Console, r: Renderer, path: Path, capabilities: dict):
    """One-click recommended/saved launch, with explicit advanced editing/reset."""
    import copy
    import json
    from .model_defaults import recommend
    from .model_presets import Presets
    from .settings import available_controls, validate_options

    rec = await asyncio.to_thread(recommend, path, capabilities)
    saved = Presets().load(path)
    revision = saved["revision"] if saved else None
    values = {k: copy.deepcopy(rec[k]) for k in ("gpus", "ctx", "options")}
    sources = dict(rec["sources"])
    if saved:
        selection = saved["selection"]
        values.update({k: selection[k] for k in ("gpus", "ctx")})
        values["options"].update(selection["options"])
        sources.update({k: "Saved for this model" for k in ["gpus", "ctx", *selection["options"]]})
    for note in rec["notes"]:
        r.system(note)
    if rec["context_limit"]:
        r.system(f"Model metadata context limit: {rec['context_limit']:,} (not a memory-fit guarantee).")
    # These defaults also drive the per-control 'default' reset in Advanced.
    effective_caps = {**capabilities, "defaults": {**capabilities.get("defaults", {}), **rec["options"]},
                      "default_sources": rec["sources"]}
    controls = {c.name: c for c in available_controls(effective_caps)}

    def validated():
        ctx, gpus = values["ctx"], values["gpus"]
        if type(ctx) is not int or not 9 <= ctx <= 2**31-1:
            raise ValueError("Invalid saved context; use Advanced or reset.")
        if rec["context_limit"] and ctx > rec["context_limit"]:
            raise ValueError("Context exceeds this model's GGUF limit; use Advanced or reset.")
        maximum = capabilities.get("max_gpus")
        if gpus is not None and (type(gpus) is not int or gpus < 1 or (maximum and gpus > maximum)):
            raise ValueError("GPU count exceeds this backend's limits; use Advanced or reset.")
        if gpus is not None and rec['gpus'] is not None and gpus > rec['gpus']:
            raise ValueError("Saved GPU count exceeds the detected usable topology; use Advanced or reset.")
        for key, value in values["options"].items():
            if key not in controls:
                raise ValueError(f"Saved setting {key} is no longer supported; reset recommendations.")
            if controls[key].choices and value not in controls[key].choices:
                raise ValueError(f"Saved {key} is no longer supported; edit it or reset.")
        values["options"] = validate_options(values["options"],ctx=ctx,gpus=gpus,
                                             architecture=capabilities.get('architecture'))
        return copy.deepcopy(values), revision

    while True:
        table = Table(box=rbox.SIMPLE, title="Model settings · saved choices or recommendations")
        for col in ("setting", "value", "source"): table.add_column(col)
        for key, value in {"gpus":values["gpus"], "ctx":values["ctx"], **values["options"]}.items():
            shown = "auto" if value is None else "on" if value is True else "off" if value is False else json.dumps(value)
            table.add_row(key, shown, sources.get(key, "Your edit"))
        console.print(table)
        action = (await _ask("Enter = load · advanced · reset recommendations · cancel · ",on_interrupt="cancel")).lower()
        if action in ("cancel", "quit", "q"): return None
        if action in ("reset", "reset recommendations"):
            values = {k:copy.deepcopy(rec[k]) for k in ("gpus", "ctx", "options")}
            sources = dict(rec["sources"])
            r.system("Recommendations restored for this attempt; saved choices change only after a successful load.")
            continue
        if action in ("", "load", "start"):
            try: return validated()
            except (ValueError, TypeError) as exc: r.error(str(exc)); continue
        if action != "advanced":
            r.error("Choose load, advanced, reset or cancel.")
            continue
        while True:
            raw = await _ask(f"GPUs (blank = keep {values['gpus'] or 'auto'} · auto = engine selection) · ",on_interrupt="cancel")
            if raw.lower() == "cancel": return None
            candidate = values['gpus'] if not raw else None if raw.lower() == 'auto' else parse_count(raw)
            maximum = rec['gpus'] or capabilities.get('max_gpus')
            if (not raw or raw.lower() == 'auto' or candidate) and (candidate is None or not maximum or candidate <= maximum):
                values['gpus']=candidate;break
            r.error('Enter a supported positive GPU count, auto, or leave blank.')
        while True:
            raw = await _ask(f"context length (blank = keep {values['ctx']}) · ",on_interrupt="cancel")
            if raw.lower() == "cancel": return None
            candidate = values['ctx'] if not raw else parse_count(raw)
            if candidate and 9 <= candidate <= min(rec['context_limit'] or 2**31-1,2**31-1):
                values['ctx']=candidate;break
            r.error('Enter a context within the model limit, or leave blank.')
        if 'max_tokens' in values['options'] and values['options']['max_tokens'] >= values['ctx']:
            values['options']['max_tokens']=max(1,values['ctx']//2)
            sources['max_tokens']='Adjusted for selected context'
        edited = await _edit_options(console,r,effective_caps,values['ctx'],values['gpus'],initial=values['options'],sources=sources)
        if edited is None: return None
        values['options']=edited
        try: return validated()
        except (ValueError, TypeError) as exc: r.error(str(exc))


async def serve_and_run(
    console: Console,
    r: Renderer,
    name: str,
    path: Path,
    *,
    consolidate_on_exit: bool = True,
    keep_hot: bool = False,
) -> None:
    """Boot MachX on a specific GGUF (asking GPUs/context), then run the harness on
    it. Shared by `dream local` and the provider picker's local-model rows."""
    from .model_presets import model_key
    try:
        selected_identity = model_key(path)
        capabilities = await asyncio.to_thread(machx.capabilities, path)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        r.error(str(exc))
        return
    if not capabilities.get("supported"):
        r.error(f"MachX does not support this model architecture: {capabilities.get('architecture', 'unknown')}.")
        return
    for note in capabilities.get("notes", []):
        r.system(str(note))
    try:
        chosen = await _choose_model_settings(console, r, path, capabilities)
    except (ValueError, OSError) as exc:
        r.error(str(exc))
        return
    if chosen is None:
        r.system("not loading.")
        return
    selection, preset_revision = chosen
    gpus, ctx, options = selection["gpus"], selection["ctx"], selection["options"]

    # Look before loading. A load costs minutes of audible PCIe traffic, evicts
    # whatever was resident, and — if the weights don't fit — ends with the
    # engine at ~99% VRAM and the GPU dropping off the bus. Both happened.
    from .preflight import Verdict, check_live

    try:
        size_gb = path.stat().st_size / (1024 ** 3)
        if path.is_dir():
            from .models import directory_model_files
            size_gb = sum(p.stat().st_size for p in directory_model_files(path)
                          if p.suffix == ".safetensors") / (1024 ** 3)
        elif path.name.endswith("-00001-of-00005.gguf") or "-of-" in path.name:
            # A sharded GGUF's first file is a fraction of the real weight.
            size_gb = sum(
                p.stat().st_size for p in path.parent.glob(
                    path.name.rsplit("-", 3)[0] + "-*of*.gguf")
            ) / (1024 ** 3)
    except OSError:
        size_gb = 0.0
    if capabilities.get('architecture') == 'deepseek_v41' and capabilities.get('memory_planner') == 'streaming':
        # Use the same all-device and disk-backed residency checks as the GUI.
        from ..desktop.startup import launch_preflight
        try:
            reason = await asyncio.to_thread(launch_preflight, size_gb, gpus, capabilities)
        except ValueError as exc:
            r.error(str(exc))
            return
        from .preflight import Preflight
        pf = Preflight(Verdict.FITS, reason, should_load=True)
    else:
        pf = await asyncio.to_thread(check_live, size_gb, gpus or 1)
    r.system(f"preflight · {pf.reason}")
    if not pf.should_load:
        if pf.verdict is Verdict.ALREADY_SERVING:
            r.system("reuse it with the running-engine row, or /quit and stop it first.")
        ans = await _ask("load anyway? [y/N] · ")
        if (ans or "").strip().lower() not in ("y", "yes"):
            r.system("not loading.")
            return

    # A model on an external drive loads at the drive's read speed, not the
    # bus's: ~147 GB of expert pool is ~4 min from NVMe and 20-25 min from a
    # USB HDD. Say so, so a long load doesn't look like a hang.
    slow_disk = not str(path).startswith(("/home", "/root", "/opt", "/usr"))
    r.system(f"starting MachX · {name}"
             + (f" · {gpus} GPU(s)" if gpus else "")
             + (f" · ctx {ctx}" if ctx else f" · ctx engine default ({_ENGINE_DEFAULT_CTX})")
             + (f" · {size_gb:.0f} GB" if size_gb else "")
             + " … (loading weights, first boot can take a bit)")
    if slow_disk and size_gb > 20:
        r.system(f"  {path.parent.parent.name or 'external drive'} — expect "
                 f"~{size_gb / 7:.0f}-{size_gb / 4:.0f} min at typical external-drive "
                 "read speeds. Dream waits as long as the engine keeps making "
                 "progress; it only gives up if the log goes silent.")
    if model_key(path) != selected_identity:
        r.error("Model files changed while selecting settings; select the model again.")
        return
    proc = machx.serve(path, gpus=gpus, ctx=ctx, options=options)
    if not await asyncio.to_thread(machx.wait_ready, proc):
        r.error(f"MachX didn't come up — check {machx._log_file()}.")
        # A half-loaded server would squat on VRAM forever — take it down.
        await asyncio.to_thread(machx.stop)
        return
    model_id = machx.served_model_id() or name
    if capabilities.get("architecture") == "glm5next":
        residency = await asyncio.to_thread(machx.residency_summary)
        r.system(residency or "GLM residency report unavailable; full residency has not been verified.")
    r.system(f"MachX ready · serving {model_id} on {machx.BASE_URL}")

    # Only a successful load becomes the next launch's default. A failed/cancelled
    # attempt must never replace a working preset, nor overwrite a newer session.
    from .model_presets import Presets
    try:
        Presets().save(path, selection, expected=preset_revision, expected_key=selected_identity)
        r.system("Model settings saved for the next load.")
    except (ValueError, OSError) as exc:
        r.error(f"Model loaded, but settings were not saved: {exc}")

    if not keep_hot:
        # Belt and braces: even if the TUI dies unusually, the interpreter's exit
        # still unloads the GPUs. stop() is idempotent.
        atexit.register(machx.stop)

    from .settings import session_options
    with session_options(options, model_id, capabilities=capabilities):
        await _run_harness(
            r, provider="machx", model=model_id,
            consolidate_on_exit=consolidate_on_exit, keep_hot=keep_hot,
        )


async def run_local(*, consolidate_on_exit: bool = True, keep_hot: bool = False) -> None:
    console = Console()
    r = Renderer(console)
    r.show_logo()

    if not machx.available():
        r.error(
            f"MachX binary not found at {machx.IE_BIN}.\n"
            "  Build it (see the engine's QUICKSTART) or set DREAM_MACHX_DIR."
        )
        return

    if machx.is_serving():
        model_id = machx.served_model_id()
        r.info(f"MachX is already serving: {model_id}. Reusing it.")
        # Never unload a server this process did not start. Dream used to
        # atexit-stop it unless --keep-hot was passed, which meant: load a model
        # by hand, open Dream to use it, quit, and Dream unloads someone else's
        # weights. Whoever started it decides when it stops.
        r.system("left running on exit — Dream didn't start it (use `ie stop` to unload)")
        await _run_harness(
            r, provider="machx", model=model_id,
            consolidate_on_exit=consolidate_on_exit, keep_hot=keep_hot,
        )
        return

    models = machx.list_models_detailed()
    if not models:
        r.error("No .gguf models found under ~/models (set DREAM_MODELS_DIRS).")
        return
    render_model_table(console, models)
    sel = await _ask("model # · ")
    if not sel.isdigit() or not (1 <= int(sel) <= len(models)):
        r.error("No valid model selected.")
        return
    m = models[int(sel) - 1]
    await serve_and_run(
        console, r, m.name, m.path,
        consolidate_on_exit=consolidate_on_exit, keep_hot=keep_hot,
    )


def render_model_table(console: Console, models: list, start: int = 1) -> None:
    """The local-GGUF table (with drive tags), shared by `dream local` and the picker.
    ``start`` is the number of the first row (the picker offsets past its provider rows)."""
    table = Table(
        box=rbox.ROUNDED, border_style="grey37", header_style="bold #22d3ee",
        title="local GGUF models", title_style="dim", padding=(0, 1),
    )
    table.add_column("#", justify="right", style="bold #8b5cf6")
    table.add_column("model")
    table.add_column("size", justify="right", style="dim")
    table.add_column("drive", style="dim")
    for i, m in enumerate(models, start):
        table.add_row(str(i), m.name, f"{m.size_gb:.1f} GB", m.volume)
    console.print(table)
