"""Preflight before loading a local model.

Two failures this exists to stop, both observed for real on 2026-08-07:

1. Loading a second model on top of a live one. The cards hold ~32 GB each and
   a DeepSeek-V4 load pins ~63 GB of VRAM plus a ~101 GB host arena; a second
   load evicts work that may be mid-run, and takes ~4.5 minutes doing it.
2. Loading into VRAM that cannot hold it. That night ended with the engine at
   98.7% VRAM on card 0, an unchecked malloc_device returning null, and the GPU
   dropping off the bus with a CAT error.

So the load path answers "should this load at all" BEFORE it starts, in numbers,
and a caller that wants to proceed anyway has to say so explicitly.
"""

from __future__ import annotations

from dream.local.preflight import Verdict, check


def _gpu(*devices):
    """A GpuSnapshot-shaped stub: (used_gb, total_gb) per discrete card."""
    return {
        "ok": True,
        "devices": [
            {"index": i, "name": f"Card {i}", "integrated": False,
             "vram_used_mib": u * 1024, "vram_total_mib": t * 1024}
            for i, (u, t) in enumerate(devices)
        ],
    }


# --- rule 1: never load on top of a live model --------------------------------


def test_refuses_when_a_model_is_already_serving():
    v = check(model_gb=20.0, gpus=2, serving=True, served_id="DeepSeek-V4-Flash",
              gpu=_gpu((1, 32), (1, 32)))
    assert v.verdict is Verdict.ALREADY_SERVING
    assert not v.should_load
    assert "DeepSeek-V4-Flash" in v.reason


def test_already_serving_outranks_a_comfortable_fit():
    """Even a model that would fit easily must not displace a live one."""
    v = check(model_gb=1.0, gpus=2, serving=True, served_id="tiny",
              gpu=_gpu((0, 32), (0, 32)))
    assert v.verdict is Verdict.ALREADY_SERVING and not v.should_load


def test_force_is_the_only_way_past_a_live_model():
    v = check(model_gb=1.0, gpus=2, serving=True, served_id="tiny",
              gpu=_gpu((0, 32), (0, 32)), force=True)
    assert v.should_load and v.verdict is Verdict.ALREADY_SERVING


# --- rule 2: fit, in numbers --------------------------------------------------


def test_a_model_that_fits_comfortably():
    v = check(model_gb=20.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32)))
    assert v.verdict is Verdict.FITS and v.should_load
    assert v.free_gb == 64.0 and "20.0" in v.reason


def test_a_model_that_only_just_fits_is_flagged_tight():
    # 58 of 64 GB free -> inside the wall but past the comfort margin.
    v = check(model_gb=58.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32)))
    assert v.verdict is Verdict.TIGHT
    assert v.should_load  # allowed, but it says so
    assert "tight" in v.reason.lower()


def test_a_model_bigger_than_vram_will_not_load():
    v = check(model_gb=90.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32)))
    assert v.verdict is Verdict.WONT_FIT and not v.should_load
    assert "90.0" in v.reason and "64.0" in v.reason


def test_oversized_weights_say_streaming_moe_may_still_be_fine():
    """DeepSeek-V4 is 150.8 GB of weights and serves on 64 GB of VRAM, because
    MachX keeps a fraction resident and pages experts from pinned host RAM.
    This check cannot tell a streaming MoE from a dense model, so it must
    report the numbers WITHOUT claiming the model can't run — otherwise it
    incorrectly rejects a potentially supported streaming configuration."""
    v = check(model_gb=150.8, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32)))
    assert v.verdict is Verdict.WONT_FIT
    assert "moe" in v.reason.lower() or "stream" in v.reason.lower()
    assert "150.8" in v.reason


def test_wont_fit_can_still_be_forced_but_says_what_it_costs():
    v = check(model_gb=90.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32)), force=True)
    assert v.should_load and v.verdict is Verdict.WONT_FIT


def test_only_the_requested_number_of_cards_counts():
    """--gpus 1 must not be told it has both cards' VRAM."""
    one = check(model_gb=40.0, gpus=1, serving=False, gpu=_gpu((0, 32), (0, 32)))
    two = check(model_gb=40.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32)))
    assert one.verdict is Verdict.WONT_FIT and two.verdict is Verdict.FITS


def test_vram_already_in_use_is_subtracted():
    """The real 2026-08-07 state: cards nearly full, nothing serving on the API
    port. Free VRAM is what matters, not card capacity."""
    v = check(model_gb=20.0, gpus=2, serving=False, gpu=_gpu((31.5, 31.9), (30.6, 31.9)))
    assert v.verdict is Verdict.WONT_FIT and not v.should_load
    assert v.free_gb < 2.0


def test_integrated_graphics_are_not_counted_as_capacity():
    gpu = _gpu((0, 32))
    gpu["devices"].append({"index": 9, "name": "iGPU", "integrated": True,
                           "vram_used_mib": 0, "vram_total_mib": 64 * 1024})
    v = check(model_gb=40.0, gpus=1, serving=False, gpu=gpu)
    assert v.verdict is Verdict.WONT_FIT


# --- degrading honestly -------------------------------------------------------


def test_unreadable_gpu_state_is_unknown_and_does_not_silently_proceed():
    """Rule 2 of the house rules: if GPU status cannot be determined, do not
    pretend it can. UNKNOWN does not auto-load."""
    v = check(model_gb=20.0, gpus=2, serving=False, gpu={"ok": False, "reason": "no xpu-smi"})
    assert v.verdict is Verdict.UNKNOWN and not v.should_load
    assert "could not" in v.reason.lower() or "unknown" in v.reason.lower()


def test_missing_vram_numbers_are_unknown_not_zero():
    gpu = {"ok": True, "devices": [{"index": 0, "name": "c", "integrated": False,
                                    "vram_used_mib": None, "vram_total_mib": None}]}
    v = check(model_gb=20.0, gpus=1, serving=False, gpu=gpu)
    assert v.verdict is Verdict.UNKNOWN and not v.should_load


def test_the_reason_is_always_human_readable():
    for v in (
        check(model_gb=20.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32))),
        check(model_gb=90.0, gpus=2, serving=False, gpu=_gpu((0, 32), (0, 32))),
        check(model_gb=1.0, gpus=1, serving=True, served_id="x", gpu=_gpu((0, 32))),
        check(model_gb=1.0, gpus=1, serving=False, gpu={"ok": False}),
    ):
        assert v.reason and v.reason[0].isupper() and len(v.reason) > 20
