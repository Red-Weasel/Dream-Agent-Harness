"""The boot picker: choose an engine before Dream wakes.

Replaces the old forced-MachX flow. Bare ``dream`` lands here: pick Claude (live
now), a local GGUF (MachX), or — once Phases 2/3 land — ChatGPT/Grok/Gemini via
their signed-in CLIs, or Dream MoE. Frontier-CLI + MoE rows are shown with a
'Phase N' tag and are not yet selectable.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from rich import box as rbox
from rich.console import Console
from rich.table import Table

from ..local import machx
from ..local.launcher import (
    _ask,
    _pick_workspace,
    _run_harness,
    render_model_table,
    serve_and_run,
)
from ..local.models import scan_models
from .render import Renderer


@dataclass
class ProviderRow:
    key: str        # anthropic | codex | grok | gemini | moe
    label: str      # display name
    status: str     # "signed in" | "installed" | "not installed" | "signed in (model)"
    ready: bool     # selectable in this phase (only Claude, for now)
    note: str       # "" or "Phase 2" / "Phase 3"


def _cli_installed(name: str) -> bool:
    return shutil.which(name) is not None


def _codex_model() -> str | None:
    """Best-effort: the model codex is configured to use, from ~/.codex/config.toml."""
    cfg = Path.home() / ".codex" / "config.toml"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("model") and "=" in s:
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def detect_providers() -> list[ProviderRow]:
    """The frontier-provider rows with live install/sign-in status. Only Claude is
    selectable today; the rest are tagged with the phase that unlocks them."""
    rows: list[ProviderRow] = []

    claude_in = _cli_installed("claude")
    rows.append(ProviderRow(
        "anthropic", "Claude",
        "signed in" if claude_in else "CLI not found",
        claude_in, "",
    ))

    codex_in = _cli_installed("codex")
    codex_authed = (Path.home() / ".codex" / "auth.json").exists()
    codex_ready = codex_in and codex_authed
    if codex_ready:
        model = _codex_model()
        codex_status = f"signed in ({model})" if model else "signed in"
    elif codex_in:
        codex_status = "installed"
    else:
        codex_status = "not installed"
    # codex is a live engine once signed in; grok/gemini await their adapters.
    rows.append(ProviderRow("codex", "ChatGPT", codex_status, codex_ready, "" if codex_ready else "Phase 2"))

    grok_in = _cli_installed("grok")
    # The .grok dir exists after first run even when logged out; auth.json is the
    # real signal (it's written only once device-login completes).
    grok_authed = (Path.home() / ".grok" / "auth.json").exists()
    grok_ready = grok_in and grok_authed
    grok_status = ("signed in" if grok_ready
                   else "installed" if grok_in else "not installed")
    rows.append(ProviderRow("grok", "Grok", grok_status, grok_ready, "" if grok_ready else "sign in first"))

    gem_in = _cli_installed("gemini")
    # gemini adapter is built; live-verify pending an install + sign-in.
    rows.append(ProviderRow(
        "gemini", "Gemini",
        "installed" if gem_in else "not installed", gem_in,
        "" if gem_in else "install first",
    ))

    # An independent advisor may use the main provider, optionally with another model.
    ready_engines = sum(1 for row in rows if row.ready)
    if ready_engines >= 1:
        rows.append(ProviderRow(
            "moe", "Dream MoE", f"{ready_engines} available provider(s) for council", True, ""
        ))
    else:
        rows.append(ProviderRow(
            "moe", "Dream MoE", "needs an available provider", False, "set up a provider"
        ))
    return rows


async def moe_role_screen(console: Console, r: Renderer, providers: list[ProviderRow]):
    """Choose the main engine and explicitly enable its optional advisors.
    Offers to reuse the last saved council. Returns a core.moe.MoeConfig, or None."""
    from ..core import moe as moe_mod

    engines = [p for p in providers if p.ready and p.key != "moe"]
    if not engines:
        r.error("Dream MoE needs an available provider.")
        return None
    by_key = {e.key: e for e in engines}

    saved = moe_mod.load_config()
    if saved and saved.orchestrator in by_key:
        adv = ", ".join(by_key[a].label if a in by_key else f'{a} (setup needed)'
                        for a in saved.advisors) or 'none'
        ans = await _ask(
            f"reuse last council? {by_key[saved.orchestrator].label} leads · "
            f"advisors: {adv}  [Y/n] · "
        )
        if ans.lower() in ("", "y", "yes"):
            return saved

    table = Table(
        box=rbox.ROUNDED, border_style="grey37", header_style="bold #22d3ee",
        title="Dream MoE — who orchestrates?", title_style="dim", padding=(0, 1),
    )
    table.add_column("#", justify="right", style="bold #8b5cf6")
    table.add_column("engine")
    table.add_column("status", style="dim")
    for i, e in enumerate(engines, 1):
        table.add_row(str(i), e.label, e.status)
    console.print(table)
    r.system("Choose the main engine, then enable each advisor. Advisors act only within Dream's permission mode.")
    sel = await _ask("orchestrator # · ")
    if not sel.isdigit() or not (1 <= int(sel) <= len(engines)):
        r.error("No orchestrator chosen.")
        return None
    orch = engines[int(sel) - 1].key
    advisors = []
    for engine in engines:
        default = saved is not None and engine.key in saved.advisors
        answer = (await _ask(f"Enable {engine.label} as advisor? [{'Y/n' if default else 'y/N'}] · ")).lower()
        if answer in ('y', 'yes') or (not answer and default):
            advisors.append(engine.key)
    cfg = (replace(saved, orchestrator=orch, advisors=advisors,
                   orchestrator_effort=saved.orchestrator_effort if orch == saved.orchestrator else None,
                   advisor_models={k: v for k, v in saved.advisor_models.items() if k in advisors},
                   advisor_efforts={k: v for k, v in saved.advisor_efforts.items() if k in advisors})
           if saved else moe_mod.MoeConfig(orch, advisors))
    moe_mod.save_config(cfg)
    r.system(
        f"council set · {by_key[orch].label} orchestrates "
        f"{', '.join(by_key[a].label for a in advisors) or 'no advisors'}"
    )
    return cfg


def render_picker(console: Console, providers: list[ProviderRow], models: list) -> None:
    """The provider table (numbered 1..N), then the local-model table numbered
    straight after it, so one number space spans both."""
    table = Table(
        box=rbox.ROUNDED, border_style="grey37", header_style="bold #22d3ee",
        title="choose your engine", title_style="dim", padding=(0, 1),
    )
    table.add_column("#", justify="right", style="bold #8b5cf6")
    table.add_column("provider")
    table.add_column("status", style="dim")
    for i, p in enumerate(providers, 1):
        label = p.label if p.ready else f"[dim]{p.label}[/dim]"
        status = p.status if not p.note else f"{p.status}  · {p.note}"
        table.add_row(str(i), label, status)
    console.print(table)
    if models:
        render_model_table(console, models, start=len(providers) + 1)
    else:
        console.print("[dim]  (no local GGUF models found — set DREAM_MODELS_DIRS)[/dim]")


async def run_picker(*, consolidate_on_exit: bool = True, keep_hot: bool = False) -> None:
    """Show the picker and launch the chosen engine. Loops on invalid / not-yet-ready
    choices instead of exiting."""
    console = Console()
    r = Renderer(console)
    r.show_logo()

    providers = detect_providers()
    models: list = []
    if machx.available():
        try:
            models = scan_models()
        except Exception:
            models = []

    render_picker(console, providers, models)

    while True:
        sel = await _ask("choose # (or blank to quit) · ")
        if not sel:
            r.system("nothing chosen — goodnight. 🌙")
            return
        if not sel.isdigit():
            r.error(f"enter a number 1–{len(providers) + len(models)}")
            continue
        n = int(sel)

        if 1 <= n <= len(providers):
            p = providers[n - 1]
            if not p.ready:
                r.system(
                    f"{p.label} arrives in {p.note or 'a later phase'} — "
                    "for now pick Claude, ChatGPT, or a local model."
                )
                continue
            if p.key == "moe":
                cfg = await moe_role_screen(console, r, providers)
                if cfg is None:
                    continue
                await _run_harness(
                    r, provider=cfg.orchestrator, model=None,
                    consolidate_on_exit=consolidate_on_exit, keep_hot=keep_hot, moe=cfg,
                )
                return
            # A ready frontier engine: Claude (SDK) or a signed-in CLI (codex/grok).
            await _run_harness(
                r, provider=p.key, model=None,
                consolidate_on_exit=consolidate_on_exit, keep_hot=keep_hot,
            )
            return

        midx = n - len(providers) - 1
        if 0 <= midx < len(models):
            m = models[midx]
            await serve_and_run(
                console, r, m.name, m.path,
                consolidate_on_exit=consolidate_on_exit, keep_hot=keep_hot,
            )
            return

        r.error(f"pick a listed number (1–{len(providers) + len(models)})")
