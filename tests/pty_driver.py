"""PTY child for the end-to-end monitor test: the REAL Dream TUI, with synthetic GPU
telemetry and the model swapped for a scripted stream at a known rate. Not a test file — the
parent (test_pty_e2e.py) launches this inside a pseudo-terminal.

Env contract (set by the parent BEFORE this runs):
  DREAM_ROOT    -> a temp dir, so data/ var/ memory/ never touch the repo
  DREAM_MONITOR -> "1"
"""

import asyncio
import os
import json
from pathlib import Path
import sys

assert os.environ.get("DREAM_ROOT"), "parent must set DREAM_ROOT to a temp dir"

import dream.core.backends.openai_compat as oc  # noqa: E402
from dream.core.backends.base import Event  # noqa: E402

RATE = 30.0  # scripted decode speed, tokens/second
N_TOKENS = 75


async def scripted_ask(self, prompt):
    await asyncio.sleep(0.8)  # a visible, deterministic TTFT
    for _ in range(N_TOKENS):
        yield Event("text_delta", "word ")
        await asyncio.sleep(1.0 / RATE)
    yield Event("stats", {"pp_n": 4000, "pp_ms": 2000.0, "gen_n": N_TOKENS,
                          "gen_ms": N_TOKENS / RATE * 1000.0, "exact": True,
                          "ttft_ms": 800.0})
    yield Event("assistant_done", "word " * N_TOKENS)
    yield Event("result", {
        "is_error": False, "num_turns": 1, "subtype": "success",
        "usage": {"prompt_tokens": 4000, "completion_tokens": N_TOKENS},
        "total_cost_usd": None,
        "stats": {"pp_tps": 2000.0, "gen_tps": RATE, "prompt_tokens": 4000,
                  "completion_tokens": N_TOKENS, "ctx_used": 4075,
                  "n_ctx": 32768, "duration_s": N_TOKENS / RATE + 0.8},
    })


async def probe_none(self):
    return None


oc.OpenAICompatBackend.ask = scripted_ask
oc.OpenAICompatBackend._probe_n_ctx = probe_none  # there is no server to ask

from dream.tui import app as appmod  # noqa: E402
from dream.telemetry.gpu import GpuDeviceSample, GpuSnapshot  # noqa: E402
from telemetry_guard import guard_telemetry  # noqa: E402


class SyntheticSampler:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.snapshots = 0
        self.running = False

    def start(self):
        self.starts += 1
        self.running = True

    def stop(self):
        self.stops += 1
        self.running = False

    def snapshot(self):
        assert self.running, "monitor read a stopped fixture sampler"
        self.snapshots += 1
        return GpuSnapshot(ok=True, devices=(GpuDeviceSample(
            index=0, name="Fixture GPU", bdf="0000:01:00.0", integrated=False,
            util_pct=42.0, fan_rpm=777, temp_c=44.0,
        ),))


async def main():
    with guard_telemetry(os.environ["DREAM_ROOT"]) as guard:
        sampler = SyntheticSampler()
        appmod.GpuSampler = lambda: sampler
        app = appmod.App(provider="machx", model="scripted", consolidate_on_exit=False,
                         workspace=os.environ["DREAM_ROOT"], gui=False)
        try:
            await app.run()
            # Require the real App shutdown to stop and release its sampler.
            assert app.gpu is None and app.studio is None
            assert sampler.starts == 1 and sampler.stops == 1
            assert sampler.snapshots > 0 and not sampler.running
        finally:
            if sampler.running:
                sampler.stop()
            report = {"hardware_attempts": len(guard.attempts),
                      "starts": sampler.starts, "stops": sampler.stops,
                      "snapshots": sampler.snapshots, "running": sampler.running,
                      "app_released": app.gpu is None}
            Path(os.environ["DREAM_ROOT"], "telemetry-fixture.json").write_text(
                json.dumps(report))
            guard.assert_clean()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
