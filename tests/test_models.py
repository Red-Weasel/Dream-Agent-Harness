"""Multi-drive GGUF scan: dedup, vocab/size filters, broken-symlink tolerance, and
volume tagging (tested on constructed paths — no real mounted drives required)."""

from pathlib import Path

from dream.local import machx, models


def _make_gguf(path: Path, size_bytes: int) -> Path:
    """Create a (sparse) .gguf of an exact reported size, cheaply."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        if size_bytes > 0:
            f.seek(size_bytes - 1)
            f.write(b"\0")
    return path


def _isolate_roots(monkeypatch):
    """Pin the base roots to nothing so scan_models only sees passed-in roots —
    real GGUFs on this dev machine must not leak into assertions."""
    monkeypatch.setattr(models, "_base_roots", lambda: [])


# --- volume tagging (the labeling helper, exercised on constructed paths) -----


def test_volume_for_media_usb():
    assert models.volume_for(Path("/media/example/USB DRIVE/model.gguf")) == "usb"


def test_volume_for_media_label_slug():
    assert models.volume_for(Path("/media/example/My Data/sub/model.gguf")) == "my-data"


def test_volume_for_mnt():
    assert models.volume_for(Path("/mnt/bigdisk/model.gguf")) == "bigdisk"


def test_volume_for_home():
    assert models.volume_for(Path("/home/example/models/model.gguf")) == "home"


def test_volume_for_root_device_is_nvme():
    assert models.volume_for(Path("/opt/models/model.gguf")) == "nvme"


def test_volume_for_never_raises_on_garbage():
    # Relative / short paths must not blow up — the helper is total.
    assert models.volume_for(Path("model.gguf")) == "nvme"


# --- scan filters -------------------------------------------------------------


def test_scan_skips_small_and_vocab(tmp_path, monkeypatch):
    _isolate_roots(monkeypatch)
    _make_gguf(tmp_path / "weights.gguf", 500)          # kept
    _make_gguf(tmp_path / "tiny.gguf", 10)              # too small
    _make_gguf(tmp_path / "tokenizer.vocab.gguf", 500)  # vocab in the name

    # threshold of 100 bytes: 500B passes, 10B fails.
    found = models.scan_models(extra_roots=[tmp_path], min_size_gb=100 / 1e9)
    names = {m.name for m in found}
    assert names == {"weights"}


def test_scan_dedups_by_resolved_path(tmp_path, monkeypatch):
    _isolate_roots(monkeypatch)
    real_root = tmp_path / "real"
    link_root = tmp_path / "linked"
    link_root.mkdir()
    target = _make_gguf(real_root / "model.gguf", 500)
    (link_root / "model.gguf").symlink_to(target)

    found = models.scan_models(
        extra_roots=[real_root, link_root], min_size_gb=100 / 1e9
    )
    assert len(found) == 1


def test_scan_tolerates_broken_symlink(tmp_path, monkeypatch):
    _isolate_roots(monkeypatch)
    (tmp_path / "dangling.gguf").symlink_to(tmp_path / "does-not-exist.gguf")
    _make_gguf(tmp_path / "good.gguf", 500)

    found = models.scan_models(extra_roots=[tmp_path], min_size_gb=100 / 1e9)
    assert {m.name for m in found} == {"good"}


def test_scan_populates_localmodel_fields(tmp_path, monkeypatch):
    _isolate_roots(monkeypatch)
    _make_gguf(tmp_path / "qwen3-8b.gguf", 200_000_000)  # 0.2 GB, real default threshold

    (m,) = models.scan_models(extra_roots=[tmp_path])
    assert m.name == "qwen3-8b"
    assert m.path == tmp_path / "qwen3-8b.gguf"
    assert abs(m.size_gb - 0.2) < 0.01
    assert m.volume == "nvme"  # tmp_path is not /media or /mnt


def test_scan_respects_default_size_threshold(tmp_path, monkeypatch):
    _isolate_roots(monkeypatch)
    _make_gguf(tmp_path / "shard.gguf", 50_000_000)  # 0.05 GB < 0.1 default
    assert models.scan_models(extra_roots=[tmp_path]) == []


# --- root assembly ------------------------------------------------------------


def test_base_roots_include_seal_and_machx_dirs(monkeypatch):
    monkeypatch.setattr(machx, "_MODEL_DIRS", [Path("/some/machx/models")])
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    monkeypatch.delenv("DREAM_MODELS_DIRS", raising=False)

    roots = models._base_roots()
    assert Path("/some/machx/models") in roots
    assert models.SEAL_MODELS_DIR in roots


def test_base_roots_honor_env(monkeypatch):
    monkeypatch.setattr(machx, "_MODEL_DIRS", [])
    monkeypatch.setattr(models, "_mounted_volume_roots", lambda: [])
    monkeypatch.setenv("DREAM_MODELS_DIRS", "/a/models:/b/models")

    roots = models._base_roots()
    assert Path("/a/models") in roots
    assert Path("/b/models") in roots


# --- machx delegation (preserves the 3-tuple API) -----------------------------


def test_list_models_returns_three_tuples(tmp_path, monkeypatch):
    _isolate_roots(monkeypatch)
    _make_gguf(tmp_path / "m.gguf", 200_000_000)
    # Point scan_models at our tmp root by pinning base roots to it.
    monkeypatch.setattr(models, "_base_roots", lambda: [tmp_path])

    tuples = machx.list_models()
    assert tuples == [("m", tmp_path / "m.gguf", tuples[0][2])]
    name, path, size = tuples[0]
    assert name == "m" and path == tmp_path / "m.gguf"
    assert abs(size - 0.2) < 0.01


def test_list_models_detailed_returns_localmodels(tmp_path, monkeypatch):
    _make_gguf(tmp_path / "m.gguf", 200_000_000)
    monkeypatch.setattr(models, "_base_roots", lambda: [tmp_path])

    detailed = machx.list_models_detailed()
    assert len(detailed) == 1
    assert isinstance(detailed[0], models.LocalModel)
    assert detailed[0].name == "m"
