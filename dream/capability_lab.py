"""Isolated, versioned experiments for illuminati-handshake.

The scaffold teaches a small action policy in a CPU-only calibration environment.
It does not train LLM weights or claim to solve the user's domain. Replace the
environment and acceptance checks for the actual gap before promoting a tool.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .core.run_state import atomic_write

ENVIRONMENT = '''"""CPU calibration task: inspect the state before acting.

Replace this environment with your actual bounded task. Observations must omit
the hidden answer; rewards should come from externally checkable outcomes.
This calibration fixture is not evidence the domain problem has been solved.
"""
import random

class Environment:
    actions = ("inspect", "act")

    def reset(self, seed=0):
        self.rng = random.Random(seed)
        self.ready = False
        self.turns = 0
        return "uninspected"

    def step(self, action):
        self.turns += 1
        if action == "inspect":
            self.ready = True
            return "inspected", -0.05, self.turns >= 5, {"success": False}
        if action == "act":
            success = self.ready
            return "done", 1.0 if success else -1.0, True, {"success": success}
        raise ValueError("Unknown action")
'''

TRAIN = '''"""Bounded tabular Q-learning with disjoint training/evaluation seeds.

Run: python3 train.py --episodes 200 --output report.json
This trains an action policy; it does not load a model or change model weights.
"""
import argparse
import json
import random
from pathlib import Path
from environment import Environment

def evaluate(q, seeds, *, random_policy=False):
    successes = 0
    total = 0.0
    for seed in seeds:
        env = Environment()
        state = env.reset(seed)
        rng = random.Random(seed)
        for _ in range(100):
            action = rng.choice(env.actions) if random_policy else max(env.actions, key=lambda a: q.get((state, a), 0.0))
            state, reward, done, info = env.step(action)
            total += float(reward)
            if done:
                successes += bool(info.get("success"))
                break
    return {"cases": len(seeds), "successes": successes, "success_rate": successes / len(seeds), "mean_reward": total / len(seeds)}

def train(episodes=200, seed=0):
    if not 1 <= episodes <= 10000:
        raise ValueError("episodes must be between 1 and 10000")
    q = {}
    rng = random.Random(seed)
    train_seeds = list(range(seed, seed + episodes))
    for sample in train_seeds:
        env = Environment()
        state = env.reset(sample)
        for _ in range(100):
            action = rng.choice(env.actions) if rng.random() < .2 else max(env.actions, key=lambda a: q.get((state, a), 0.0))
            next_state, reward, done, _ = env.step(action)
            future = 0.0 if done else .95 * max(q.get((next_state, a), 0.0) for a in env.actions)
            key = (state, action)
            q[key] = q.get(key, 0.0) + .3 * (float(reward) + future - q.get(key, 0.0))
            state = next_state
            if done:
                break
    held_out = list(range(seed + episodes + 10000, seed + episodes + 10050))
    return {"kind": "calibration-action-policy", "episodes": episodes,
            "train_seed_range": [train_seeds[0], train_seeds[-1]],
            "evaluation_seed_range": [held_out[0], held_out[-1]],
            "baseline": evaluate({}, held_out, random_policy=True),
            "candidate": evaluate(q, held_out),
            "policy": [{"state": s, "action": a, "value": value} for (s, a), value in sorted(q.items())],
            "domain_acceptance_verified": False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", default="report.json")
    args = parser.parse_args()
    report = train(args.episodes, args.seed)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\\n")
    print(json.dumps({k: v for k, v in report.items() if k != "policy"}))
'''


def _root(workspace: Path) -> Path:
    workspace = Path(workspace).resolve()
    root = workspace / ".dream" / "labs"
    if not root.resolve().is_relative_to(workspace):
        raise ValueError("Capability lab directory resolves outside the workspace")
    return root


def create(workspace: Path, name: str, goal: str, criteria: list[str], *, method: str = "tool") -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", name):
        raise ValueError("Use a lowercase capability name, at most 48 characters")
    if method not in {"tool", "rl"}:
        raise ValueError("Method must be tool or rl")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 8000:
        raise ValueError("Describe the capability gap")
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 20 or any(not isinstance(c, str) or not c.strip() or len(c) > 2000 for c in criteria):
        raise ValueError("Provide 1–20 concrete acceptance criteria")
    identifier = name + "-" + uuid4().hex[:8]
    folder = _root(workspace) / identifier
    folder.mkdir(parents=True, mode=0o700)
    manifest = {"version": 1, "id": identifier, "name": name, "goal": goal, "criteria": criteria,
                "method": method, "created": datetime.now(timezone.utc).isoformat(), "status": "experiment",
                "domain_acceptance_verified": False, "evidence": [], "promotion": "disabled until review"}
    atomic_write(folder / "experiment.json", json.dumps(manifest, indent=2))
    atomic_write(folder / "research.md", "# Research and design\n\nRecord source URLs, observations, alternatives, assumptions and the smallest proposed solution.\n")
    atomic_write(folder / "acceptance.md", "# Acceptance\n\n" + "\n".join("- [ ] " + c for c in criteria)
                 + "\n\nRecord actual commands, outputs, held-out cases and failures. Training reward alone is insufficient.\n")
    if method == "rl":
        atomic_write(folder / "environment.py", ENVIRONMENT)
        atomic_write(folder / "train.py", TRAIN)
        atomic_write(folder / "README.md", "# Action-policy lab\n\nRun `python3 train.py --episodes 200` here to validate the CPU calibration environment. "
                     "It trains a tabular action policy, not an LLM. Replace environment.py with the actual task and "
                     "independent reward checks. Keep held-out evaluation, bounded side effects and failure cases. "
                     "A GPU/model training job requires a separate resource preflight and explicit authorization.\n")
    return {**manifest, "directory": str(folder)}


def inspect(workspace: Path, identifier: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", identifier):
        raise ValueError("Invalid experiment id")
    folder = _root(workspace) / identifier
    if folder.is_symlink() or not folder.resolve().is_relative_to(_root(workspace).resolve()):
        raise ValueError("Experiment path escapes the lab")
    manifest = folder / "experiment.json"
    if manifest.is_symlink() or manifest.stat().st_size > 100_000:
        raise ValueError("Invalid experiment manifest")
    data = json.loads(manifest.read_text())
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            continue
        if len(files) >= 200:
            break
        size = path.stat().st_size
        files.append({"file": str(path.relative_to(folder)), "bytes": size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if size <= 5_000_000 else None})
    report = folder / "report.json"
    result = None
    if report.exists() and not report.is_symlink() and report.stat().st_size <= 1_000_000:
        result = json.loads(report.read_text())
    return {**data, "directory": str(folder), "files": files, "reported_evaluation": result,
            "verification": "Evaluation is an experiment artifact; domain acceptance requires independent checks."}
