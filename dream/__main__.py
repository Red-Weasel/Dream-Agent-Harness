"""Entrypoint: ``dream`` / ``python -m dream`` / ``dream local``."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> None:
    from .environment import NumericEnvironmentError, numeric_environment_errors
    try:
        # Fail before picker/native/provider imports. Keep CLI help available
        # for recovery; diagnostics reports every invalid known setting.
        if sys.argv[1:] not in (['-h'], ['--help']):
            errors = numeric_environment_errors()
            if errors:
                raise errors[0]
        _main()
    except NumericEnvironmentError as error:
        raise SystemExit(str(error)) from None


def _main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == "media":
        from .media.cli import main as media_main
        raise SystemExit(media_main(argv[1:]))

    if argv and argv[0] == "desktop":
        from .desktop.launcher import launch

        raise SystemExit(launch(argv[1:]))

    if argv and argv[0] in {"status", "profile", "extensions", "backup", "runs"}:
        from .management import main as manage
        raise SystemExit(manage(argv))

    # The picker and local launcher share profile selection with explicit
    # providers. Set this before constructing either App/Engine.
    argv = [piece for arg in argv for piece in (arg.split("=", 1) if arg.startswith("--profile=") else [arg])]
    if "--profile" in argv:
        pos = argv.index("--profile")
        if pos + 1 >= len(argv) or argv[pos + 1] not in {"auto", "lean", "balanced", "frontier"}:
            raise SystemExit("--profile requires auto, lean, balanced or frontier")
        os.environ["DREAM_PROFILE"] = argv[pos + 1]
        argv = argv[:pos] + argv[pos + 2:]

    # Bare `dream` → the engine picker: Claude, a local GGUF (MachX), or — as the
    # later phases land — a signed-in frontier CLI or Dream MoE. Flags still route
    # to the full CLI below (`dream --provider anthropic …`); `dream local` remains
    # the direct-to-MachX spelling.
    if not argv:
        keep_hot = os.environ.get("DREAM_KEEP_MACHX") == "1"
        from .tui.picker import run_picker

        try:
            asyncio.run(run_picker(keep_hot=keep_hot))
        except KeyboardInterrupt:
            pass
        return

    # `dream local` → interactive MachX launcher (pick model, context, GPUs).
    # Exiting unloads the GPUs; --keep-hot (or DREAM_KEEP_MACHX=1) keeps the
    # model loaded for a fast next boot.
    if argv[0] == "local":
        from . import config

        if "--gui" in argv:
            config.GUI = True
        no_consolidate = "--no-consolidate" in argv
        keep_hot = "--keep-hot" in argv or os.environ.get("DREAM_KEEP_MACHX") == "1"
        from .local.launcher import run_local

        try:
            asyncio.run(run_local(
                consolidate_on_exit=config.CONSOLIDATE_ON_EXIT and not no_consolidate,
                keep_hot=keep_hot))
        except KeyboardInterrupt:
            pass
        return

    parser = argparse.ArgumentParser(
        prog="dream", description="Dream — a personal multi-model agent harness. Use --profile auto|lean|balanced|frontier."
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "machx", "openai", "xai", "codex", "grok", "gemini"],
        default="anthropic",
        help="Model provider. Default: anthropic (Claude). codex/grok/gemini are the "
             "subscription CLIs; xai is the xAI HTTP API. For MachX, prefer `dream local`.",
    )
    parser.add_argument("--model", default=None, help="Model id/alias for the provider.")
    parser.add_argument(
        "--no-consolidate", action="store_true", help="Skip end-of-session memory consolidation."
    )
    parser.add_argument(
        "--workspace", default=None,
        help="Directory Dream works in this session (reads anywhere; writes outside ask first).",
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Open Dream Studio — a browser pane showing this session live, "
             "alongside the terminal. Loopback only, token-guarded.",
    )
    parser.add_argument(
        "--loop", metavar="GOAL", default=None, help="Run autonomously toward GOAL, then exit."
    )
    parser.add_argument("--iterations", type=int, default=None,
                        help="Optional positive autonomous iteration limit; unlimited when omitted.")
    parser.add_argument("--resume-run", metavar="ID", help="Resume an existing durable autonomous run")
    parser.add_argument("--resume-note", help="What you verified about interrupted actions, or input resolving a pause")
    parser.add_argument(
        "--mode",
        choices=["ask", "accept-edits", "auto"],
        default=None,
        help="Permission mode. Headless --loop defaults to 'auto': confined shell "
             "commands run without asking, anything reaching outside the workspace "
             "still asks (and with nobody to ask, is refused). Pass accept-edits to "
             "keep the interactive default, where every shell command is declined.",
    )
    args = parser.parse_args(argv)
    if args.iterations is not None and args.iterations < 1:
        parser.error("--iterations must be a positive integer; omit it for no iteration limit")

    from . import config
    from .tui.app import App

    if args.resume_run:
        from .core.run_state import RunState
        if args.loop:
            parser.error("--resume-run uses the recorded goal; omit --loop")
        try:
            state = RunState(config.LOOP_DIR, args.resume_run)
            try:
                args.loop = state.state["goal"]
                args.workspace = args.workspace or state.state["worker_workspace"]
            finally:
                state.close()
        except (OSError, ValueError, KeyError) as exc:
            parser.error(str(exc))
    elif args.resume_note:
        parser.error("--resume-note requires --resume-run")

    app = App(
        provider=args.provider, model=args.model,
        consolidate_on_exit=config.CONSOLIDATE_ON_EXIT and not args.no_consolidate,
        workspace=args.workspace,
        gui=args.gui or config.GUI,
    )
    # A headless loop has nobody to answer a permission prompt, so accept-edits —
    # which asks for EVERY shell command — silently refuses all of them. Measured:
    # Dream was blocked in 11 of 11 unattended benchmark runs and solved the tasks
    # by reasoning instead of executing. 'auto' still asks for anything not
    # provably confined to the workspace, so the boundary holds.
    if args.mode:
        app.mode = args.mode
    elif args.loop:
        app.mode = "auto"

    try:
        if args.loop:
            options = {"resume_run_id": args.resume_run, "resume_note": args.resume_note} if args.resume_run else {}
            asyncio.run(app.run_autonomous(args.loop, args.iterations, **options))
        else:
            asyncio.run(app.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    from .desktop.crash_log import enable
    stop_crash_log = enable()   # DREAM-086: only the desktop window's Terminal session sets the file
    try:
        main()
    finally:
        stop_crash_log()
