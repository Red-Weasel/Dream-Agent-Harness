"""Should this model load at all?

Answered in numbers, before the load starts, because both ways of getting it
wrong are expensive and both happened on 2026-08-07:

- Loading on top of a live model evicts work that may be mid-run and spends
  ~4.5 minutes of audible PCIe traffic doing it.
- Loading into VRAM that cannot hold it ends with the engine at 98.7% on one
  card, an unchecked ``malloc_device`` returning null, and the GPU dropping off
  the bus (``CAT error class=ccs`` -> ``UR_RESULT_ERROR_DEVICE_LOST``).

The verdict never loads anything itself; it only reports. ``force`` exists
because a human who has read the numbers may still be right — LM Studio's
"Load anyway" is the same idea — but nothing proceeds past a bad verdict
without someone saying so.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# Headroom over the model's own bytes: the KV cache, the compressor/attention
# caches, and the activation scratch all grow during decode out of whatever is
# left. 8% is not a derivation, it is a margin — the engine's own reserve is a
# fixed 2 GB and a run has already died at 32.6 GB of ~32.5 GB available.
COMFORT = 1.08


class Verdict(Enum):
    FITS = "fits"
    TIGHT = "tight"
    WONT_FIT = "wont_fit"
    ALREADY_SERVING = "already_serving"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Preflight:
    verdict: Verdict
    reason: str
    should_load: bool
    free_gb: float | None = None
    needed_gb: float | None = None

    @property
    def ok(self) -> bool:
        return self.verdict in (Verdict.FITS, Verdict.TIGHT)


def _discrete_free_gb(gpu: dict[str, Any] | None, gpus: int) -> float | None:
    """Free VRAM across the first ``gpus`` DISCRETE cards, or None if the
    numbers cannot be read. Integrated graphics are excluded: their "VRAM" is
    system memory and counting it would invent capacity that isn't there."""
    if not gpu or not gpu.get("ok"):
        return None
    devices = [d for d in (gpu.get("devices") or []) if not d.get("integrated")]
    if not devices:
        return None
    free = 0.0
    for d in devices[: max(gpus, 1)]:
        total, used = d.get("vram_total_mib"), d.get("vram_used_mib")
        if total is None or used is None:
            return None  # a card that won't report is not a card we can plan on
        free += (float(total) - float(used)) / 1024.0
    return free


def check(
    *,
    model_gb: float,
    gpus: int,
    serving: bool,
    gpu: dict[str, Any] | None,
    served_id: str | None = None,
    force: bool = False,
) -> Preflight:
    """Decide whether loading ``model_gb`` across ``gpus`` cards is a good idea.

    ``gpu`` is a GpuSnapshot as a dict (``dataclasses.asdict``), so this stays a
    pure function and the tests need no hardware.
    """
    # A live model outranks everything else, including a comfortable fit: the
    # question is not whether the new one would fit, it is that something is
    # already using the cards and may be mid-run.
    if serving:
        # Lead with the verdict, not the model id: an id that starts lowercase
        # would otherwise open the sentence in lowercase.
        who = served_id or "a model"
        return Preflight(
            Verdict.ALREADY_SERVING,
            f"Already loaded: {who} is serving. Stop it first, or reuse it — "
            "loading now would evict it mid-run and cost minutes of reload.",
            should_load=force,
        )

    free = _discrete_free_gb(gpu, gpus)
    if free is None:
        reason = (gpu or {}).get("reason") or "the sampler returned nothing usable"
        return Preflight(
            Verdict.UNKNOWN,
            f"Could not read GPU memory ({reason}), so there is no way to tell "
            "whether this model fits. Not loading blind.",
            should_load=force,
        )

    needed = model_gb * COMFORT
    card_word = "card" if gpus == 1 else f"{gpus} cards"
    if needed > free:
        # NOT a verdict that the model cannot run — only that its weights do not
        # fit in VRAM. A streaming-MoE engine keeps a fraction resident and
        # pages experts from host RAM: DeepSeek-V4 is 150.8 GB of weights and
        # serves fine on 64 GB of VRAM. Predicting which is which from a GGUF
        # is not something this can do honestly, so it reports the numbers and
        # lets a human who knows the model decide.
        return Preflight(
            Verdict.WONT_FIT,
            f"Weights exceed VRAM: {model_gb:.1f} GB (~{needed:.1f} GB with room "
            f"for caches) against {free:.1f} GB free across {card_word}. Fine for "
            "a streaming-MoE model that pages experts from host RAM; for a dense "
            "one this is how the GPU was lost before.",
            should_load=force, free_gb=free, needed_gb=needed,
        )
    if needed > free * 0.9:
        return Preflight(
            Verdict.TIGHT,
            f"Tight: {model_gb:.1f} GB against {free:.1f} GB free across "
            f"{card_word}. It should load, but the caches grow during decode "
            "and there is little room left for them — expect trouble at long "
            "context.",
            should_load=True, free_gb=free, needed_gb=needed,
        )
    return Preflight(
        Verdict.FITS,
        f"Fits: {model_gb:.1f} GB against {free:.1f} GB free across {card_word}.",
        should_load=True, free_gb=free, needed_gb=needed,
    )


def check_live(model_gb: float, gpus: int, *, force: bool = False) -> Preflight:
    """``check`` against the real machine. Every reading is best-effort: a
    sampler that throws yields UNKNOWN, which does not auto-load."""
    from dataclasses import asdict

    from . import machx

    try:
        serving = machx.is_serving()
        served = machx.served_model_id() if serving else None
    except Exception:
        serving, served = False, None
    snap = None
    try:
        from ..telemetry import GpuSampler

        sampler = GpuSampler()
        snap = asdict(sampler.sample_once())
    except Exception:
        snap = None
    return check(model_gb=model_gb, gpus=gpus, serving=serving, served_id=served,
                 gpu=snap, force=force)
