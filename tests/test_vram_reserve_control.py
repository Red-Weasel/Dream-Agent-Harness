"""DREAM-100: the model picker's "VRAM reserve (GiB)" control for MachX loads.

The engine's auto expert tier keeps a per-card headroom (IE_MIMO26_VRAM_RESERVE_GIB, default 1.5 GiB) that only a
terminal could raise; `ie serve --vram-reserve-gib F` now takes it and the engine advertises `vram_reserve_gib` in
capabilities.load for the arches that honour it. Dream turns that into a picker control like the others.
"""
import pytest

from dream.local.settings import BY_NAME, available_controls, server_args, validate_options


def caps(advertised: bool, default=1.5) -> dict:
    load = ["gpus", "ctx", "prefill_chunk", "parallel"] + (["vram_reserve_gib"] if advertised else [])
    defaults = {"temperature": 0.7, "parallel": 1, "prefill_chunk": 256}
    if advertised:
        defaults["vram_reserve_gib"] = default
    return {"schema_version": 1, "architecture": "mimo_v2", "supported": True, "load": load,
            "sampling": ["temperature"], "features": {"prompt_cache": True}, "defaults": defaults,
            "reasoning": {"effort_levels": [], "default_effort": None, "thinking_description": ""}}


def test_the_control_exists_as_a_float_with_the_engines_flag():
    control = BY_NAME["vram_reserve_gib"]
    assert control.kind == "float"
    assert control.flag == "--vram-reserve-gib"
    assert control.default is None            # unset = the engine's own default
    assert (control.minimum, control.maximum) == (0, 24)
    assert "GiB" in control.hint


def test_the_picker_shows_it_only_when_the_engine_advertises_it():
    shown = {c.name: c for c in available_controls(caps(True))}
    assert "vram_reserve_gib" in shown
    assert shown["vram_reserve_gib"].default == 1.5
    assert shown["vram_reserve_gib"].source == "MachX backend"
    assert "vram_reserve_gib" not in {c.name for c in available_controls(caps(False))}


def test_a_saved_value_becomes_the_serve_flag_and_unset_sends_nothing():
    assert server_args({"vram_reserve_gib": 2.5}) == ["--vram-reserve-gib", "2.5"]
    assert server_args({"vram_reserve_gib": None}) == []
    assert server_args({}) == []


@pytest.mark.parametrize("bad", [-0.5, 24.5, True, "lots"])
def test_out_of_range_and_non_numeric_values_are_refused(bad):
    with pytest.raises(ValueError):
        validate_options({"vram_reserve_gib": bad})


def test_zero_is_allowed_and_means_no_headroom():
    assert validate_options({"vram_reserve_gib": 0})["vram_reserve_gib"] == 0
    assert server_args({"vram_reserve_gib": 0}) == ["--vram-reserve-gib", "0.0"] or \
        server_args({"vram_reserve_gib": 0}) == ["--vram-reserve-gib", "0"]
