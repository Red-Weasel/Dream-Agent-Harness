"""The boot picker: choose an engine before Dream wakes.

Replaces the old forced-MachX flow. Bare ``dream`` lands here: pick Claude,
ChatGPT, Grok or Gemini through its installed CLI, a local GGUF (MachX), or Dream
MoE. A provider row can be chosen when its CLI is installed (DREAM-246: found by
``core.cli_discovery``, the lookup the spawning code uses). Whether that CLI is
signed in is not checked here and is never claimed.
"""

from __future__ import annotations

import os
import shutil  # noqa: F401  (kept as a module attribute: tests replace shutil.which through it)
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
    status: str     # "installed" | "installed · model <name>" | "not installed" (moe: a count)
    ready: bool     # selectable: the CLI is installed. Never a claim of being signed in.
    note: str       # "sign-in not checked" | "install first" | ""


def _cli_installed(name: str) -> bool:
    """Installed, by the lookup the spawning code uses (DREAM-246). Says nothing about sign-in."""
    # Imported here, as this module does for core.moe, so loading the picker loads no more than before.
    from ..core.cli_discovery import find_cli
    return find_cli(name) is not None


def _cli_row(key: str, label: str, cmd: str, detail: str | None = None) -> ProviderRow:
    if _cli_installed(cmd):
        return ProviderRow(key, label, f"installed · {detail}" if detail else "installed", True,
                           "sign-in not checked")
    return ProviderRow(key, label, "not installed", False, "install first")


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
    """The frontier-provider rows with their install status.

    ``ready`` means the CLI is installed and so can be chosen; it does not mean signed
    in. Sign-in is never checked here: no credential file is looked at (a keyring
    sign-in leaves none), so every installed row says so instead of guessing."""
    rows: list[ProviderRow] = [_cli_row("anthropic", "Claude", "claude")]

    # The configured model is a best-effort display detail only; it decides nothing.
    model = _codex_model() if _cli_installed("codex") else None
    rows.append(_cli_row("codex", "ChatGPT", "codex", f"model {model}" if model else None))
    rows.append(_cli_row("grok", "Grok", "grok"))
    rows.append(_cli_row("antigravity", "Gemini · Antigravity", "agy"))

    # An independent advisor may use the main provider, optionally with another model.
    # The count is installed CLIs (the local prerequisite), not accounts that work.
    installed = sum(1 for row in rows if row.ready)
    if installed >= 1:
        rows.append(ProviderRow(
            "moe", "Dream MoE", f"{installed} installed provider(s) for council", True,
            "sign-in not checked",
        ))
    else:
        rows.append(ProviderRow(
            "moe", "Dream MoE", "needs an installed provider", False, "install a provider CLI first"
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
                    f"{p.label} can't be chosen yet: {p.status}"
                    f"{' — ' + p.note if p.note else ''}. Pick an installed provider or a local model."
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
            # An installed frontier engine. Sign-in was not checked; the engine reports it.
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
