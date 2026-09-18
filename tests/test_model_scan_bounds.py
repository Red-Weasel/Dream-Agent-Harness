"""Bound model discovery by depth, elapsed time, and volume scope using fixtures."""

from __future__ import annotations

import time

import pytest

from dream.local import models


def _make_deep_tree(root, depth, width=3):
    """A directory tree with no GGUFs — the shape of a backup drive."""
    cur = root
    for d in range(depth):
        for w in range(width):
            (cur / f"dir{d}_{w}").mkdir(parents=True, exist_ok=True)
        cur = cur / f"dir{d}_0"


def test_scan_respects_the_depth_cap(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g"
    deep.mkdir(parents=True)
    (deep / "buried.gguf").write_bytes(b"x" * 1024)
    shallow = tmp_path / "models"
    shallow.mkdir()
    (shallow / "found.gguf").write_bytes(b"x" * 1024)

    names = {p.name for p in models._find_ggufs(tmp_path, max_depth=3, budget_s=5)}
    assert "found.gguf" in names
    assert "buried.gguf" not in names  # past the cap, not descended into


def test_scan_respects_the_time_budget(tmp_path):
    _make_deep_tree(tmp_path, depth=8, width=6)
    t0 = time.monotonic()
    models._find_ggufs(tmp_path, max_depth=99, budget_s=0.05)
    assert time.monotonic() - t0 < 2.0  # bailed on the budget, did not walk it all


def test_skip_dirs_are_not_descended(tmp_path):
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "vendored.gguf").write_bytes(b"x" * 1024)
    (tmp_path / "real.gguf").write_bytes(b"x" * 1024)
    names = {p.name for p in models._find_ggufs(tmp_path)}
    assert names == {"real.gguf"}


def test_a_mounted_volume_gets_the_tighter_bounds(tmp_path, monkeypatch):
    """A speculative root must not be walked as deeply as an explicit one."""
    vol = tmp_path / "media" / "ExampleVolume"
    deep = vol / "photos" / "2019" / "raw" / "sub"
    deep.mkdir(parents=True)
    (deep / "buried.gguf").write_bytes(b"x" * 1024)
    near = vol / "models"
    near.mkdir()
    (near / "top.gguf").write_bytes(b"x" * (200 * 1024 * 1024))

    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [vol])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")
    found = {m.name for m in models.scan_models()}
    assert "top.gguf".removesuffix(".gguf") in found
    assert "buried" not in found


def test_isolated_internal_scan_is_fast_and_still_finds_models(tmp_path, monkeypatch):
    """Default root assembly finds a synthetic model without scanning the host."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    with (model_dir / "fixture.gguf").open("wb") as fixture:
        fixture.truncate(200_000_000)
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [model_dir])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "absent")
    monkeypatch.delenv("DREAM_MODELS_DIRS", raising=False)
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    t0 = time.monotonic()
    found = models.scan_models()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"scan took {elapsed:.1f}s"
    assert found, "found no models at all — the bounds are too tight"


def test_a_volume_is_searched_by_name_not_by_walking(tmp_path, monkeypatch):
    """Find a named model library without descending into unrelated media."""
    vol = tmp_path / "ExampleVolume"
    lib = vol / "LLM Models" / "DeepSeek-V4-Flash-GGUF" / "UD-Q3_K_XL"
    lib.mkdir(parents=True)
    (lib / "DeepSeek-V4-UD-Q3_K_XL-00002-of-00004.gguf").write_bytes(b"x" * (200 * 1024 * 1024))

    # The decoy: a deep tree that must not be walked, with a GGUF planted at the
    # bottom so descending into it would be detectable.
    photos = vol / "Example Photos"
    deep = photos / "2019" / "raw" / "sub" / "deeper"
    deep.mkdir(parents=True)
    (deep / "decoy.gguf").write_bytes(b"x" * (200 * 1024 * 1024))

    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [vol])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")

    found = {m.name for m in models.scan_models()}
    # The shard suffix is stripped by grouping; what this test pins is that the
    # library was found at all and the photo tree was not descended into.
    assert "DeepSeek-V4-UD-Q3_K_XL" in found
    assert "decoy" not in found  # the photo tree was never descended


def test_loose_weights_at_a_volume_top_are_still_found(tmp_path, monkeypatch):
    """A drive with no obviously-named library still gets a shallow glance."""
    vol = tmp_path / "Stick"
    vol.mkdir()
    (vol / "solo.gguf").write_bytes(b"x" * (200 * 1024 * 1024))
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [vol])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")
    assert "solo" in {m.name for m in models.scan_models()}


def test_a_stalled_volume_cannot_hang_the_picker(tmp_path, monkeypatch):
    """A blocking volume syscall must not stall the model picker indefinitely."""
    vol = tmp_path / "Slow"
    vol.mkdir()

    def _stall(_v):
        time.sleep(30)  # a drive that never answers
        return [vol / "never.gguf"]

    monkeypatch.setattr(models, "_volume_ggufs_blocking", _stall)
    monkeypatch.setattr(models, "_VOLUME_WALL_CLOCK_S", 0.2)
    t0 = time.monotonic()
    got = models._volume_ggufs(vol)
    elapsed = time.monotonic() - t0
    assert got == []
    assert elapsed < 3.0, f"the picker waited {elapsed:.1f}s on a stalled disk"


def test_a_responsive_volume_still_returns_its_models(tmp_path, monkeypatch):
    vol = tmp_path / "Fast"
    lib = vol / "LLM Models"
    lib.mkdir(parents=True)
    (lib / "quick.gguf").write_bytes(b"x" * 1024)
    assert [p.name for p in models._volume_ggufs(vol)] == ["quick.gguf"]


# --- split-GGUF grouping -----------------------------------------------------


def _shard(d, base, i, n, size):
    (d / f"{base}-{i:05d}-of-{n:05d}.gguf").write_bytes(b"x" * size)


def test_split_model_is_one_entry_pointing_at_part_one(tmp_path, monkeypatch):
    """Small index shards remain the loadable entry for grouped model weights."""
    d = tmp_path / "models"
    d.mkdir()
    _shard(d, "DeepSeek-V4-UD-Q8_K_XL", 1, 5, 5_000_000)      # 5 MB index
    for i in (2, 3, 4, 5):
        _shard(d, "DeepSeek-V4-UD-Q8_K_XL", i, 5, 300_000_000)

    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [d])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")

    found = models.scan_models()
    assert len(found) == 1, [m.name for m in found]
    m = found[0]
    assert m.path.name.endswith("-00001-of-00005.gguf")  # the loadable part
    assert "5 parts" in m.name
    assert m.size_gb == pytest.approx(1.205, abs=0.01)  # the WHOLE model


def test_unsplit_models_are_untouched(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    (d / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"x" * 300_000_000)
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [d])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")
    found = models.scan_models()
    assert [m.name for m in found] == ["Qwen3-4B-Q4_K_M"]
    assert "parts" not in found[0].name


def test_two_quantisations_stay_separate(tmp_path, monkeypatch):
    q3 = tmp_path / "models" / "Q3"
    q8 = tmp_path / "models" / "Q8"
    q3.mkdir(parents=True)
    q8.mkdir(parents=True)
    _shard(q3, "Model-UD-Q3_K_XL", 1, 2, 5_000_000)
    _shard(q3, "Model-UD-Q3_K_XL", 2, 2, 300_000_000)
    _shard(q8, "Model-UD-Q8_K_XL", 1, 2, 5_000_000)
    _shard(q8, "Model-UD-Q8_K_XL", 2, 2, 300_000_000)
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [tmp_path / "models"])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")
    assert len({m.name for m in models.scan_models()}) == 2


def test_a_tiny_lone_gguf_is_still_filtered(tmp_path, monkeypatch):
    """The size filter must still do its original job on unsplit files."""
    d = tmp_path / "models"
    d.mkdir()
    (d / "tokenizer.gguf").write_bytes(b"x" * 5_000_000)
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    monkeypatch.setattr(models.machx, "_MODEL_DIRS", [d])
    monkeypatch.setattr(models, "SEAL_MODELS_DIR", tmp_path / "nope")
    assert models.scan_models() == []
