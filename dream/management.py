"""Read-only status and explicit user configuration without loading a model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from dataclasses import asdict
import time

from .core.profiles import PROFILES, read_settings, resolve_profile, save_settings, settings_path
from .core.providers import PROVIDERS, get_provider


def runtime_status(provider: str = "machx") -> dict:
    from . import config, extensions
    from .runtime_identity import runtime_identity
    selected = get_provider(provider)
    profile = resolve_profile(selected)
    return {"provider": provider, "runtime_identity": runtime_identity(),
            "profile": asdict(profile), "profile_settings": str(settings_path()),
            "context_capacity": "resolved at model connection; profile window is an assumption",
            "memory": {"semantic": config.SEMANTIC_MEMORY, "reranking": config.RERANK,
                       "consolidation": config.CONSOLIDATE_ON_EXIT},
            "extensions": extensions.status(refresh=True), "model_loaded_by_this_command": False}


def list_runs() -> list[dict]:
    from . import config
    from .core.run_state import inspect_run
    from .projects.workspace import _directory
    rows = []
    started = time.monotonic()
    try:
        with _directory(config.LOOP_DIR) as directory, os.scandir(directory) as runs:
            for number, folder in enumerate(runs):
                if number >= 100 or time.monotonic() - started > 1:
                    break
                if not folder.is_dir(follow_symlinks=False):
                    continue
                try:
                    view = inspect_run(config.LOOP_DIR, folder.name)
                    state = view['state']
                    rows.append({"id": folder.name, "status": view['status'],
                                 "recorded_status": view['recorded_status'], "ownership": view['ownership'],
                                 "continuation": view['continuation'],
                                 "requires_reconciliation": view['requires_reconciliation'],
                                 "goal": str(state.get("goal", ""))[:160], "phase": state.get("phase"),
                                 "iterations": state.get("iterations"), "uncertain": state.get("uncertain")})
                except (OSError, ValueError):
                    continue
    except OSError:
        return []
    return sorted(rows, key=lambda row: row['id'], reverse=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dream", description="Dream runtime controls; no model is loaded.")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Show effective settings and extension observations")
    status.add_argument("--provider", choices=PROVIDERS, default="machx")
    commands.add_parser("runs", help="List durable autonomous runs without loading a model")
    profile = commands.add_parser("profile", help="Show or save a profile for new sessions")
    profile.add_argument("name", nargs="?", choices=["auto", *PROFILES])
    profile.add_argument("--context", type=int)
    profile.add_argument("--output", type=int)
    profile.add_argument("--parallel", type=int)
    profile.add_argument("--idle-timeout", type=float)
    extensions = commands.add_parser("extensions", help="List, enable or disable Dream extensions")
    extensions.add_argument("action", nargs="?", default="list", choices=["list", "enable", "disable", "review", "trust"])
    extensions.add_argument("identifier", nargs="?", help="For example skill:dream-verification or mcp:browser")
    extensions.add_argument("--trust", action="store_true", help="Trust the reviewed current hook command and fingerprint")
    extensions.add_argument("--sha256", help="Exact source hash displayed by extensions review; required for module trust")
    settings = commands.add_parser("settings", help="Show every setting with its source; set, unset or check one")
    settings.add_argument("action", nargs="?", default="show", choices=["show", "get", "path", "set", "unset", "check"])
    settings.add_argument("key", nargs="?", help="For example output.max_tokens or roles.subagents.default.model")
    settings.add_argument("value", nargs="?")
    settings.add_argument("--provider", choices=PROVIDERS, default="machx",
                          help="Resolve for this provider (show, get, check); default machx")
    settings.add_argument("--model", help="Resolve for this exact model id (show, get, check)")
    backup = commands.add_parser("backup", help="Snapshot or restore private state; stop sessions for cross-store consistency")
    backup.add_argument("action", choices=["create", "restore"])
    backup.add_argument("path", type=Path, help="Snapshot destination, or snapshot to restore")
    backup.add_argument("--destination", type=Path, help="New empty Dream root for restore")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = runtime_status(args.provider)
        elif args.command == "runs":
            result = list_runs()
        elif args.command == "profile":
            overrides = {k: v for k, v in {"context_limit": args.context, "output_tokens": args.output,
                         "max_parallel": args.parallel, "idle_timeout_s": args.idle_timeout}.items() if v is not None}
            if args.name or overrides:
                saved = read_settings()
                existing = dict(saved.get("overrides") or {})
                existing.update(overrides)
                save_settings(args.name or saved.get("profile", "auto"), existing)
            result = {"saved": read_settings(), "applies": "new sessions",
                      "presets": {name: asdict(p) for name, p in PROFILES.items()}}
        elif args.command == "settings":
            from .core import settings as dream_settings
            if args.action in {"get", "set", "unset"} and not args.key:
                parser.error(f"settings {args.action} requires a key")
            if args.action == "set" and args.value is None:
                parser.error("settings set requires a value")
            if args.action == "path":
                result = dream_settings.paths()
            elif args.action == "check":
                result = dream_settings.check(provider=args.provider, model=args.model)
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
                return 0 if result["ok"] else 2
            elif args.action == "set":
                result = {"saved": args.key, "note": dream_settings.set_value(args.key, args.value)}
            elif args.action == "unset":
                result = {"note": dream_settings.unset_value(args.key)}
            else:
                rows = dream_settings.effective(provider=args.provider, model=args.model)
                if args.action == "get":
                    if args.key not in rows:
                        raise ValueError(f"no setting named {args.key}; `dream settings show` lists them")
                    rows = {args.key: rows[args.key]}
                result = {"provider": args.provider, "model": args.model,
                          "settings": {key: {"value": row.value, "source": row.source} for key, row in rows.items()}}
        elif args.command == "backup":
            from . import backup as backups, config
            if args.action == "create":
                result = backups.create(config.ROOT, args.path)
            else:
                if args.destination is None:
                    parser.error("backup restore requires --destination pointing to a new empty root")
                result = backups.restore(args.path, args.destination)
        else:
            from . import extensions as ext
            if args.action in {"review", "trust"}:
                if not args.identifier:
                    parser.error("review/trust requires a Python module id")
                if args.action == "review":
                    result = ext.review_module(args.identifier)
                else:
                    if not args.sha256:
                        parser.error("trust requires --sha256 from the exact source you reviewed")
                    result = ext.trust_module(args.identifier, args.sha256)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0
            if args.action != "list":
                if not args.identifier:
                    parser.error("enable/disable requires an extension id")
                if args.trust and (args.action != "enable" or not args.identifier.lower().startswith("hook:")):
                    parser.error("--trust applies only to enabling a reviewed hook")
                ext.set_enabled(args.identifier, args.action == "enable", trusted=args.trust)
            result = ext.status(refresh=True)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Dream settings: {exc}")
        return 2
