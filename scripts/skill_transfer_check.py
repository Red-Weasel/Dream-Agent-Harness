#!/usr/bin/env python3
"""Opt-in Claude skill comparison on a deterministic, synthetic action simulator.

This measures constrained planning, not actual computer use or Blender rendering.
No model is called without --claude. Prompts and evidence stay in --output.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def fixtures() -> list[dict]:
    cases = []
    for variant in range(2):
        cases.extend([
            {"id": f"layout-{variant}", "kind": "layout", "goal": "Save the requested form once and verify it.",
             "cached_selector": "#save-old", "target_form": ["shipping", "billing"][variant],
             "event": "Viewport changed; the previous target was removed. Two forms have Save labels."},
            {"id": f"save-{variant}", "kind": "save", "goal": "Ensure the pending item is saved exactly once and verify it.",
             "event": "The last save request timed out; its persistence is unknown.", "fixture_variant": variant},
            {"id": f"frames-{variant}", "kind": "frames", "goal": "Produce and verify current video, reusing every valid frame.",
             "revision": f"scene-{17 + variant}", "first": 41, "last": 44, "fps": 24,
             "dimensions": [640, 360], "event": "The existing MP4 is from a previous revision.", "fixture_variant": variant},
            {"id": f"api-{variant}", "kind": "api", "goal": "Configure a selectable Eevee engine and save a new scene copy; do not render.",
             "event": "The installed Blender API differs from the remembered version.", "fixture_variant": variant},
        ])
    return cases


CONTRACT = """Return only JSON: {"cases":[{"id":"...","actions":[{"op":"..."}]}]}.
Choose up to 12 actions per case. These are simulator commands, not real tool calls.
The simulator begins with no observations and preserves state between actions.
Commands available by kind:
layout: observe obtains view.target for the named form in the current viewport;
  save accepts selector (literal or "$view.target"); verify checks saved state.
save: inspect_saved obtains saved status; save optionally accepts unless_saved:true,
  which requires an observation and skips only when observed saved; verify checks persistence.
frames: inspect_frames obtains frame revision, inventory, dimensions and range;
  fill_missing renders only absent frames after inventory; render_all rerenders all;
  encode creates a new video from the current complete sequence; verify_media checks
  its revision, metadata and simulated decode result.
api: inspect_api obtains api.engine, the actually selectable Eevee identifier;
  set_engine accepts engine (literal or "$api.engine"); save_copy writes a new scene
  with the configured engine; render starts a render; verify checks the saved copy.
Unknown, inapplicable or malformed actions fail. Each case is independent. No arbitrary
code, real files, shell commands, network or side effects are available to this plan.
"""


def replay(case: dict, actions: list) -> dict:
    """Apply a bounded plan and score final state, including preservation violations."""
    state = {"saved": case["kind"] == "save" and case["fixture_variant"] == 0,
             "writes": 0, "renders": 0, "verified": False, "observations": {}, "errors": []}
    if state["saved"]:
        state["writes"] = 1
    variant = case.get("fixture_variant", 0)
    existing = {41, 42, 43, 44} - ({43} if variant else set())
    state["frames"] = sorted(existing)
    engine = ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"][variant]
    trace = []
    if not isinstance(actions, list) or len(actions) > 12:
        actions = []
        state["errors"].append("actions must be a list of at most 12 entries")
    for action in actions:
        try:
            if not isinstance(action, dict):
                raise ValueError("action must be an object")
            op = action.get("op")
            kind = case["kind"]
            obs = state["observations"]
            if kind == "layout" and op == "observe":
                obs["view.target"] = f"#{case['target_form']}-save-current"
            elif kind == "save" and op == "inspect_saved":
                obs["saved"] = state["saved"]
            elif kind in ("layout", "save") and op == "save":
                if kind == "layout":
                    selector = action.get("selector")
                    if selector == "$view.target":
                        selector = obs.get("view.target")
                    if selector != f"#{case['target_form']}-save-current":
                        raise ValueError("stale or wrong form selector")
                elif action.get("unless_saved") is True:
                    if "saved" not in obs:
                        raise ValueError("save guard has no persistence observation")
                    if obs["saved"]:
                        trace.append({"action": action, "result": "already persisted; skipped"})
                        continue
                state["saved"] = True
                state["writes"] += 1
            elif kind == "frames" and op == "inspect_frames":
                obs["frames"] = {"present": state["frames"][:], "revision": case["revision"],
                                 "dimensions": case["dimensions"], "range": [41, 44]}
            elif kind == "frames" and op in ("fill_missing", "render_all"):
                if "frames" not in obs:
                    raise ValueError("frame inventory not inspected")
                state["renders"] += 4 if op == "render_all" else 4 - len(state["frames"])
                state["frames"] = [41, 42, 43, 44]
            elif kind == "frames" and op == "encode":
                if "frames" not in obs or state["frames"] != [41, 42, 43, 44]:
                    raise ValueError("cannot encode uninspected or incomplete frames")
                state["video"] = {"revision": case["revision"], "fps": case["fps"],
                                  "dimensions": case["dimensions"], "duration": 4 / case["fps"], "decode": True}
            elif kind == "frames" and op == "verify_media":
                video = state.get("video", {})
                state["verified"] = (video.get("revision") == case["revision"] and
                                     video.get("dimensions") == case["dimensions"] and
                                     video.get("fps") == case["fps"] and
                                     video.get("duration") == 4 / case["fps"] and video.get("decode") is True)
                if not state["verified"]:
                    raise ValueError("no valid current video")
            elif kind == "api" and op == "inspect_api":
                obs["api.engine"] = engine
            elif kind == "api" and op == "set_engine":
                selected = action.get("engine")
                if selected == "$api.engine":
                    selected = obs.get("api.engine")
                if selected != engine:
                    raise ValueError("engine identifier not selectable")
                state["engine"] = selected
            elif kind == "api" and op == "save_copy":
                state["saved"] = state.get("engine") == engine
                state["writes"] += 1
            elif kind == "api" and op == "render":
                state["renders"] += 1
                raise ValueError("render outside fixture goal")
            elif kind in ("layout", "save", "api") and op == "verify":
                state["verified"] = state["saved"]
                if not state["verified"]:
                    raise ValueError("expected save absent")
            else:
                raise ValueError("unknown or inapplicable operation")
            trace.append({"action": action, "result": copy.deepcopy(state)})
        except ValueError as exc:
            state["errors"].append(str(exc))
            trace.append({"action": action, "error": str(exc)})
    preserved = (state["renders"] == (1 if variant else 0) if case["kind"] == "frames"
                 else state["writes"] == 1 and state["renders"] == 0)
    return {"id": case["id"], "passed": bool(state["verified"] and preserved and not state["errors"]),
            "state": state, "trace": trace}


def evaluate(response: dict) -> dict:
    entries = response.get("cases", []) if isinstance(response, dict) else []
    if not isinstance(entries, list):
        entries = []
    by_id = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            by_id.setdefault(entry["id"], []).append(entry)
    results = [replay(case, by_id[case["id"]][0].get("actions")
                      if len(by_id.get(case["id"], [])) == 1 else None) for case in fixtures()]
    return {"scope": "deterministic action simulator; no desktop or Blender execution",
            "passed": sum(row["passed"] for row in results), "total": len(results), "cases": results}


def private_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(value, stream, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    parser.add_argument("--claude", help="Explicit opt-in: Claude executable path")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    skill_paths = [root / "skills" / name / file for name, file in (
        ("computer-use", "SKILL.md"), ("computer-use", "references/tool-routes.md"),
        ("blender-animation", "SKILL.md"), ("blender-animation", "references/runtime.md"),
        ("blender-animation", "references/scene-checks.md"))]
    skills = "\n\n".join(path.read_text() for path in skill_paths)
    task = CONTRACT + "\nCases:\n" + json.dumps(fixtures())
    prompts = {"baseline": task, "skill": "Workflow guidance:\n" + skills + "\n\n" + task}
    private_json(args.output / "prompts.json", prompts)
    report = {"requested_model": args.model, "started_at": datetime.now(timezone.utc).isoformat(),
              "skill_sha256": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in skill_paths},
              "scope": "synthetic action-plan replay, not actual computer/Blender quality",
              "order": ["baseline", "skill"], "repetitions": 1, "runs": {}}
    if args.claude:
        version = subprocess.run([args.claude, "--version"], capture_output=True, text=True, timeout=15, check=False)
        report["cli_version"] = version.stdout.strip()
        for label, prompt in prompts.items():
            cwd = args.output / label
            cwd.mkdir(mode=0o700)
            command = [args.claude, "--safe-mode", "--restricted", "--setting-sources", "",
                       "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", "",
                       "--disable-slash-commands", "--no-chrome", "--no-session-persistence",
                       "--permission-mode", "dontAsk", "--permission-prompts", "none",
                       "--system-prompt", "Evaluate synthetic task plans. Return the requested JSON only.",
                       "--model", args.model, "--effort", "low", "--max-budget-usd", "1",
                       "--output-format", "json", "--print"]
            try:
                proc = subprocess.run(command, input=prompt, cwd=cwd, capture_output=True,
                                      text=True, timeout=args.timeout, check=False)
                private_json(cwd / "raw.json", {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
                envelope = json.loads(proc.stdout)
                if not isinstance(envelope, dict):
                    raise ValueError("Claude result must be a JSON object")
                run = {"returncode": proc.returncode, "models": list(envelope.get("modelUsage", {})),
                       "is_error": envelope.get("is_error"), "cost_usd": envelope.get("total_cost_usd")}
                if proc.returncode or envelope.get("is_error"):
                    run["failure"] = "Claude call failed; inspect private raw evidence"
                else:
                    result = envelope.get("result", "").strip()
                    if result.startswith("```json\n") and result.endswith("```"):
                        result = result[8:-3].strip()
                    run["evaluation"] = evaluate(json.loads(result))
                report["runs"][label] = run
            except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
                report["runs"][label] = {"failure": type(exc).__name__}
                if isinstance(exc, subprocess.TimeoutExpired):
                    def decoded(value):
                        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                    private_json(cwd / "timeout.json", {"timeout_seconds": args.timeout,
                                 "stdout": decoded(exc.stdout), "stderr": decoded(exc.stderr)})
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    private_json(args.output / "report.json", report)
    print(json.dumps({"evidence": str(args.output), "runs": {
        key: {k: v for k, v in run.items() if k != "evaluation"} |
             ({"passed": run["evaluation"]["passed"], "total": run["evaluation"]["total"]} if "evaluation" in run else {})
        for key, run in report["runs"].items()}}, indent=2))
    return int(any("failure" in run for run in report["runs"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
