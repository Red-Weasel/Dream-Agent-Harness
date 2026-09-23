"""DREAM-094 -- `measure_image`: exact pixel facts for a model that cannot see.

Every number asserted here is computed by hand from the fixture, not copied from a run:
a solid colour, half black / half white, a grey ramp, uniform noise, a flat frame with a
brightened half, a drawn word.
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from dream.core import policy
from dream.core.profiles import guidance, resolve_profile
from dream.core.providers import get_provider
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools import measure_image as measure_module
from dream.tools.measure_image import measure_image
from dream.tools.native import NATIVE_TOOLS


@pytest.fixture
def ws(tmp_path):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    yield tmp_path
    tool_context._CTX = None


def _text(res) -> str:
    return res["content"][0]["text"]


def _failed(res) -> bool:
    return bool(res.get("is_error"))


def _num(text: str, label: str) -> float:
    """The number printed right after ``label`` (``label 12.3`` or ``label: 12.3%``)."""
    m = re.search(re.escape(label) + r":?\s*(-?\d+(?:\.\d+)?)", text)
    assert m, f"{label!r} not in:\n{text}"
    return float(m.group(1))


def _save(ws: Path, name: str, rgb: np.ndarray) -> Path:
    p = ws / name
    Image.fromarray(rgb.astype(np.uint8), "RGB").save(p)
    return p


def _solid(h, w, colour):
    a = np.empty((h, w, 3), np.uint8)
    a[:] = colour
    return a


async def _measure(**args):
    res = await measure_image.handler(args)
    assert not _failed(res), _text(res)
    return _text(res)


# --- (b) one image, known fixtures -----------------------------------------------------------------------


async def test_a_solid_colour_is_uniform_with_one_dominant_colour_and_no_edges(ws):
    _save(ws, "red.png", _solid(48, 64, (255, 0, 0)))
    out = await _measure(path="red.png")
    assert out.startswith("red.png: 64x48, RGB (3 channels), PNG")
    assert _num(out, "mean") == pytest.approx(76.2, abs=0.05)      # 0.299 * 255 = 76.245
    assert _num(out, "std") == 0.0
    assert _num(out, "min") == _num(out, "max") == pytest.approx(76.2, abs=0.05)
    assert _num(out, "near-black (<=16)") == 0.0 and _num(out, "near-white (>=239)") == 0.0
    assert "verdict: uniform (one flat colour #ff0000)" in out
    assert "#ff0000 100.0%" in out
    assert _num(out, "edge density") == 0.0


async def test_half_black_half_white_splits_every_measure_in_two(ws):
    a = _solid(100, 200, (0, 0, 0))
    a[:, 100:] = 255
    _save(ws, "half.png", a)
    out = await _measure(path="half.png")
    assert _num(out, "mean") == 127.5 and _num(out, "std") == 127.5
    assert _num(out, "min") == 0.0 and _num(out, "max") == 255.0
    assert _num(out, "near-black (<=16)") == 50.0 and _num(out, "near-white (>=239)") == 50.0
    assert "verdict: not blank" in out
    assert "#000000 50.0%" in out and "#ffffff 50.0%" in out
    # One column (x=99) sees a white right-hand neighbour: 100 of 20,000 pixels.
    assert _num(out, "edge density") == 0.5


async def test_a_grey_ramp_has_the_ramp_statistics_and_no_edges(ws):
    ramp = np.tile(np.arange(256, dtype=np.uint8), (32, 1))
    _save(ws, "ramp.png", np.stack([ramp] * 3, axis=-1))
    out = await _measure(path="ramp.png")
    assert _num(out, "mean") == 127.5
    assert _num(out, "std") == pytest.approx(73.9, abs=0.05)        # sqrt((256^2 - 1) / 12) = 73.90
    assert _num(out, "min") == 0.0 and _num(out, "max") == 255.0
    assert _num(out, "near-black (<=16)") == pytest.approx(6.6, abs=0.05)   # 17 of 256 columns
    assert _num(out, "near-white (>=239)") == pytest.approx(6.6, abs=0.05)
    assert "verdict: not blank" in out
    # 32 bins of 5-bit grey, 8 columns each: every bin holds 3.125 %.
    assert out.count("3.1%") == 5
    assert _num(out, "edge density") == 0.0                        # neighbours differ by 1 level


async def test_uniform_noise_has_no_dominant_colour_and_is_all_edges(ws):
    rng = np.random.default_rng(0)
    _save(ws, "noise.png", rng.integers(0, 256, (256, 256, 3), dtype=np.uint8))
    out = await _measure(path="noise.png")
    assert 125.0 < _num(out, "mean") < 130.0
    assert 47.0 < _num(out, "std") < 52.0        # 73.9 * sqrt(.299^2 + .587^2 + .114^2) = 49.4
    assert _num(out, "near-black (<=16)") < 1.0 and _num(out, "near-white (>=239)") < 1.0
    assert "verdict: not blank" in out
    assert "dominant colours" in out and "none reach 1%" in out
    assert _num(out, "edge density") > 70.0     # P(|a-b| >= 32) = (224/256)^2 = 0.766 per direction


async def test_black_and_white_frames_are_called_blank(ws):
    _save(ws, "black.png", _solid(40, 40, (0, 0, 0)))
    _save(ws, "white.png", _solid(40, 40, (255, 255, 255)))
    black = await _measure(path="black.png")
    white = await _measure(path="white.png")
    assert "verdict: blank (black" in black and _num(black, "near-black (<=16)") == 100.0
    assert "verdict: blank (white" in white and _num(white, "near-white (>=239)") == 100.0


# --- (c) two images ----------------------------------------------------------------------------------------


async def test_identical_images_are_zero_percent_changed_with_ssim_one(ws):
    rng = np.random.default_rng(1)
    a = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
    _save(ws, "a.png", a)
    _save(ws, "b.png", a)
    out = await _measure(path="a.png", compare_to="b.png")
    assert "compare_to b.png: 96x64 (same size)" in out
    assert _num(out, "changed (any channel >= 16 levels)") == 0.0
    assert _num(out, "mean |diff|") == 0.0
    assert _num(out, "SSIM (grey, 7x7)") == 1.0


async def test_a_brightened_half_reports_its_exact_change_and_a_lower_ssim(ws):
    base = _solid(128, 128, (100, 100, 100))
    lit = base.copy()
    lit[:, :64] = 140
    _save(ws, "base.png", base)
    _save(ws, "lit.png", lit)
    out = await _measure(path="base.png", compare_to="lit.png")
    assert _num(out, "changed (any channel >= 16 levels)") == 50.0
    assert _num(out, "mean |diff|") == 20.0
    ssim = _num(out, "SSIM (grey, 7x7)")
    # 122 window positions across 128 columns: 58 fully lit windows score
    # (2*100*140 + C1) / (100^2 + 140^2 + C1) = 0.94596 (C1 = 6.5025), 58 fully dark ones 1.0,
    # and the 6 straddling the boundary (m = 1..6 lit columns, sigma_x = 0 so sigma_xy = 0) score
    # (2*100*mu_y + C1) / (100^2 + mu_y^2 + C1) * C2 / (1600 * (m/7)(1 - m/7) * 49/48 + C2)
    # = 0.2260, 0.1485, 0.1260, 0.1250, 0.1447, 0.2169 (C2 = 58.5225); the mean is 0.9332.
    assert ssim == pytest.approx(0.9332, abs=0.0005)


async def test_a_change_below_sixteen_levels_does_not_count_as_changed(ws):
    base = _solid(32, 32, (100, 100, 100))
    _save(ws, "base.png", base)
    _save(ws, "dim.png", _solid(32, 32, (115, 100, 100)))    # 15 levels on one channel
    out = await _measure(path="base.png", compare_to="dim.png")
    assert _num(out, "changed (any channel >= 16 levels)") == 0.0
    assert _num(out, "mean |diff|") == 5.0                      # 15 / 3 channels


async def test_different_sizes_are_compared_at_the_smaller_size_and_say_so(ws):
    _save(ws, "small.png", _solid(100, 100, (0, 0, 0)))
    _save(ws, "wide.png", _solid(100, 200, (0, 0, 0)))
    out = await _measure(path="small.png", compare_to="wide.png")
    assert "sizes differ (100x100 vs 200x100)" in out and "compared at 100x100" in out
    assert _num(out, "changed (any channel >= 16 levels)") == 0.0
    assert _num(out, "SSIM (grey, 7x7)") == 1.0


# --- (d) text ----------------------------------------------------------------------------------------------


def _draw_words(ws: Path, words: str) -> Path:
    im = Image.new("RGB", (640, 160), "white")
    ImageDraw.Draw(im).text((24, 40), words, fill="black", font=ImageFont.load_default(size=64))
    p = ws / "words.png"
    im.save(p)
    return p


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed here")
async def test_ocr_reads_a_drawn_word_back_through_tesseract(ws):
    _draw_words(ws, "HELLO WORLD")
    out = await _measure(path="words.png", ocr=True)
    m = re.search(r'text \(tesseract\): "(.*)"', out)
    assert m, out
    assert "HELLO" in m.group(1) and "WORLD" in m.group(1)


async def test_without_tesseract_the_result_says_ocr_is_unavailable_and_nothing_fails(ws, monkeypatch):
    _draw_words(ws, "HELLO WORLD")
    monkeypatch.setenv("PATH", str(ws / "no-binaries-here"))
    res = await measure_image.handler({"path": "words.png", "ocr": True})
    assert not _failed(res)
    out = _text(res)
    assert "OCR unavailable" in out and "tesseract" in out
    assert "luminance" in out and "verdict" in out         # the measurements still arrived


async def test_ocr_is_off_unless_asked(ws):
    _draw_words(ws, "HELLO WORLD")
    out = await _measure(path="words.png")
    assert "text (tesseract)" not in out and "OCR" not in out


# --- (e) bounded -------------------------------------------------------------------------------------------


async def test_a_4k_frame_measures_in_under_two_seconds_with_compact_output(ws):
    rng = np.random.default_rng(2)
    frame = rng.integers(0, 256, (2160, 3840, 3), dtype=np.uint8)
    p = ws / "4k.png"
    Image.fromarray(frame, "RGB").save(p, compress_level=1)
    t0 = time.perf_counter()
    out = await _measure(path="4k.png")
    elapsed = time.perf_counter() - t0
    print(f"\n4K measure: {elapsed:.2f} s, {len(out)} chars")
    assert elapsed < 2.0, f"{elapsed:.2f} s"
    assert len(out) <= 1500, len(out)
    assert out.startswith("4k.png: 3840x2160, RGB (3 channels), PNG")


async def test_a_4k_compare_stays_bounded(ws):
    rng = np.random.default_rng(3)
    frame = rng.integers(0, 256, (2160, 3840, 3), dtype=np.uint8)
    Image.fromarray(frame, "RGB").save(ws / "a.png", compress_level=1)
    Image.fromarray(frame, "RGB").save(ws / "b.png", compress_level=1)
    t0 = time.perf_counter()
    out = await _measure(path="a.png", compare_to="b.png")
    elapsed = time.perf_counter() - t0
    print(f"\n4K compare: {elapsed:.2f} s, {len(out)} chars")
    assert elapsed < 4.0, f"{elapsed:.2f} s"
    assert len(out) <= 1500, len(out)
    assert _num(out, "SSIM (grey, 7x7)") == 1.0


# --- (a) every backend, read-only, the existing read rules ------------------------------------------------


def test_measure_image_is_a_native_tool_and_read_only_in_every_mode():
    assert "measure_image" in {t.name for t in NATIVE_TOOLS}
    assert "measure_image" in policy.builtin_names(), "a custom tool must not be able to claim the name"
    assert policy.capability("measure_image") == policy.READONLY
    assert policy.capability("mcp__dream__measure_image") == policy.READONLY
    for mode in policy.MODES:
        for inp in ({"path": "/etc/x.png"}, {"path": "shot.png", "compare_to": "/tmp/other.png", "ocr": True}):
            assert policy.decide("measure_image", inp, mode, Path("/tmp/ws-measure"))[0] == "allow", (mode, inp)


async def test_relative_paths_resolve_into_the_workspace_and_missing_or_non_images_are_refused(ws):
    (ws / "sub").mkdir()
    _save(ws, "sub/pic.png", _solid(8, 8, (0, 0, 255)))
    assert (await _measure(path="sub/pic.png")).startswith("pic.png: 8x8")
    missing = await measure_image.handler({"path": "sub/nope.png"})
    assert _failed(missing) and "No file at" in _text(missing)
    (ws / "note.txt").write_text("not an image")
    bad = await measure_image.handler({"path": "note.txt"})
    assert _failed(bad) and "Could not read note.txt as an image" in _text(bad)
    _save(ws, "ok.png", _solid(8, 8, (0, 0, 0)))
    gone = await measure_image.handler({"path": "ok.png", "compare_to": "nope.png"})
    assert _failed(gone) and "compare_to" in _text(gone)
    none = await measure_image.handler({})
    assert _failed(none) and "path" in _text(none)


# --- (f) the runtime note for a session without image input points here -----------------------------------


def test_the_no_image_input_note_points_at_measure_image():
    provider = get_provider("machx")
    profile = resolve_profile(provider, None, model="m")
    off = guidance(replace(provider, multimodal=False), profile, "m")
    on = guidance(replace(provider, multimodal=True), profile, "m")
    assert "Image input is off" in off and "measure_image" in off
    assert "measure_image" not in on


# --- DREAM-095: the DREAM-094 gate's four notes ------------------------------------------------------------


async def test_16_bit_and_32_bit_int_images_are_scaled_to_8_bit_not_clipped(ws):
    # 256 steps of 257: after >> 8 the ramp is exactly 0..255, the same numbers as the 8-bit ramp above.
    ramp16 = np.tile(np.arange(256, dtype=np.uint16) * 257, (32, 1))
    Image.fromarray(ramp16).save(ws / "ramp16.png")                       # Pillow mode I;16
    out = await _measure(path="ramp16.png")
    assert out.startswith("ramp16.png: 256x32, I;16 (1 channel, scaled from 16-bit), PNG"), out
    assert _num(out, "mean") == 127.5 and _num(out, "min") == 0.0 and _num(out, "max") == 255.0
    assert _num(out, "near-white (>=239)") == pytest.approx(6.6, abs=0.05) and "verdict: not blank" in out
    Image.fromarray(ramp16.astype(np.int32)).save(ws / "ramp32.tif")     # Pillow mode I (32-bit int)
    out = await _measure(path="ramp32.tif")
    assert out.startswith("ramp32.tif: 256x32, I (1 channel, scaled from 32-bit int), TIFF"), out
    assert _num(out, "mean") == 127.5 and _num(out, "max") == 255.0
    # A mid-grey 16-bit frame (128 * 257) is one flat grey, not near-white.
    Image.fromarray(np.full((8, 8), 128 * 257, np.uint16)).save(ws / "grey16.png")
    out = await _measure(path="grey16.png")
    assert "verdict: uniform (one flat colour #808080)" in out and _num(out, "near-white (>=239)") == 0.0


async def test_float_images_are_scaled_by_their_range(ws):
    unit = np.tile(np.arange(256, dtype=np.float32) / 255, (32, 1))      # 0..1: x255
    Image.fromarray(unit).save(ws / "unit.tif")                            # Pillow mode F
    out = await _measure(path="unit.tif")
    assert out.startswith("unit.tif: 256x32, F (1 channel, scaled from float), TIFF"), out
    assert _num(out, "mean") == 127.5 and _num(out, "min") == 0.0 and _num(out, "max") == 255.0
    Image.fromarray(unit * 1000).save(ws / "big.tif")                      # 0..1000: by the max
    out = await _measure(path="big.tif")
    assert _num(out, "mean") == 127.5 and _num(out, "min") == 0.0 and _num(out, "max") == 255.0


async def test_an_unreadable_compare_to_is_blamed_on_the_compare_to_file(ws):
    _save(ws, "one.png", _solid(8, 8, (0, 0, 0)))
    (ws / "note.txt").write_text("not an image")
    res = await measure_image.handler({"path": "one.png", "compare_to": "note.txt"})
    assert _failed(res)
    assert _text(res).startswith("Could not read note.txt as an image"), _text(res)
    assert "one.png" not in _text(res)


async def test_ocr_accepts_string_flags_and_refuses_junk(ws, monkeypatch):
    _draw_words(ws, "HELLO WORLD")
    monkeypatch.setenv("PATH", str(ws / "no-binaries-here"))    # OCR on -> the deterministic "unavailable" line
    for off in ("false", "False", "no", "0", "off", ""):
        out = await _measure(path="words.png", ocr=off)
        assert "OCR" not in out, off
    for on in ("true", "TRUE", "yes", "1", "on", " On "):
        out = await _measure(path="words.png", ocr=on)
        assert "OCR unavailable" in out, on
    bad = await measure_image.handler({"path": "words.png", "ocr": "maybe"})
    assert _failed(bad) and "ocr" in _text(bad) and "maybe" in _text(bad), _text(bad)


async def test_ocr_runs_tesseract_on_a_1_5x_upscale_under_2000_px_and_on_the_file_from_2000(ws, monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw.get("input")))
        return subprocess.CompletedProcess(argv, 0, stdout=b"TEXT\n", stderr=b"")

    monkeypatch.setattr(measure_module.shutil, "which", lambda name: "/fake/tesseract")
    monkeypatch.setattr(measure_module.subprocess, "run", fake_run)
    _save(ws, "small.png", _solid(160, 640, (0, 0, 0)))
    out = await _measure(path="small.png", ocr=True)
    argv, data = calls[-1]
    # 1.5x, not 2x (DREAM-099): the DREAM-095 gate measured words 48/48 at both, isolated zeros 39/46 at 1x,
    # 21/48 at 2x and 35/48 at 1.5x, and its own page 12/12 at 1.5x.
    assert argv[1:] == ["stdin", "stdout"] and Image.open(io.BytesIO(data)).size == (960, 240)
    assert 'text (tesseract): "TEXT" (OCR at 1.5x)' in out, out
    _save(ws, "wide.png", _solid(20, 2000, (0, 0, 0)))          # longer side 2000: not under, the file as is
    out = await _measure(path="wide.png", ocr=True)
    argv, data = calls[-1]
    assert Path(argv[1]).name == "wide.png" and argv[2:] == ["stdout"] and data is None
    assert 'text (tesseract): "TEXT"' in out and "OCR at" not in out, out


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed here")
async def test_ocr_at_1_5x_reads_22_px_light_on_dark_header_text(ws):
    # The DREAM-094 gate's case: 22 px light text on a dark header bar over a white page. Measured
    # 2026-09-23 with tesseract 5.5.1: at 1x the header's last word came back as "Accou"; enlarged,
    # every word is read (17 fixtures, 126 words: 104 at 1x, 106 at 2x, never fewer; the same words
    # at 1.5x, which the DREAM-095 gate preferred for isolated digits -- DREAM-099).
    im = Image.new("RGB", (1280, 720), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1280, 56], fill=(32, 32, 32))
    d.text((24, 17), "Dashboard   Projects   Settings   Help   Account", fill=(230, 230, 230),
           font=ImageFont.load_default(size=22))
    d.text((24, 120), "Welcome back your three projects are ready", fill=(40, 40, 40),
           font=ImageFont.load_default(size=22))
    im.save(ws / "page.png")
    out = await _measure(path="page.png", ocr=True)
    m = re.search(r'text \(tesseract\): "(.*)" \(OCR at 1.5x\)', out)
    assert m, out
    for word in ("Dashboard", "Projects", "Settings", "Help", "Account", "Welcome", "ready"):
        assert word in m.group(1).split(), (word, m.group(1))


# --- DREAM-099: the DREAM-095 gate's polish notes -----------------------------------------------------------


def _transparent_words(ws: Path, words: str = "HELLO WORLD") -> Path:
    """Black text on a fully transparent background: the colour stored under the transparency is black too."""
    im = Image.new("RGBA", (640, 160), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((24, 40), words, fill=(0, 0, 0, 255), font=ImageFont.load_default(size=64))
    p = ws / "glyphs.png"
    im.save(p)
    return p


async def test_ocr_composites_an_alpha_channel_on_white_before_the_upscale(ws, monkeypatch):
    piped = []

    def fake_run(argv, **kw):
        piped.append(kw.get("input"))
        return subprocess.CompletedProcess(argv, 0, stdout=b"TEXT\n", stderr=b"")

    monkeypatch.setattr(measure_module.shutil, "which", lambda name: "/fake/tesseract")
    monkeypatch.setattr(measure_module.subprocess, "run", fake_run)
    _transparent_words(ws)
    out = await _measure(path="glyphs.png", ocr=True)
    sent = Image.open(io.BytesIO(piped[-1]))
    assert sent.mode == "RGB" and sent.size == (960, 240)
    arr = np.asarray(sent)
    assert float((arr.min(axis=2) >= 250).mean()) > 0.8        # the transparent background became white ...
    assert bool((arr.max(axis=2) < 64).any())                  # ... and the black glyphs are still there
    # The measurements themselves are untouched: alpha is still reported as stored, not composited.
    assert "alpha: " in out and "not composited" in out
    assert "verdict: blank (black" in out


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed here")
async def test_ocr_reads_black_text_drawn_on_a_transparent_background(ws):
    # The DREAM-095 gate's note: at the piped upscale the black stored under the transparency swallowed the
    # text ("none found"); tesseract reading the FILE itself composites, so only the pipe needed it.
    _transparent_words(ws)
    out = await _measure(path="glyphs.png", ocr=True)
    m = re.search(r'text \(tesseract\): "(.*)" \(OCR at 1.5x\)', out)
    assert m, out
    assert "HELLO" in m.group(1) and "WORLD" in m.group(1)


async def test_ocr_accepts_json_numbers_as_flags(ws, monkeypatch):
    _draw_words(ws, "HELLO WORLD")
    monkeypatch.setenv("PATH", str(ws / "no-binaries-here"))    # OCR on -> the deterministic "unavailable" line
    for off in (0, 0.0):
        assert "OCR" not in await _measure(path="words.png", ocr=off), off
    for on in (1, 1.0):
        assert "OCR unavailable" in await _measure(path="words.png", ocr=on), on
    for junk in (2, 0.5, -1):
        bad = await measure_image.handler({"path": "words.png", "ocr": junk})
        assert _failed(bad) and "ocr" in _text(bad) and str(junk) in _text(bad), _text(bad)


async def test_a_nul_byte_in_a_path_is_the_normal_error_result_not_a_crash(ws):
    _save(ws, "ok.png", _solid(8, 8, (0, 0, 0)))
    res = await measure_image.handler({"path": "shot\x00.png"})
    assert _failed(res) and _text(res).startswith("No file at"), _text(res)
    res = await measure_image.handler({"path": "ok.png", "compare_to": "other\x00.png"})
    assert _failed(res) and _text(res).startswith("compare_to: no file at"), _text(res)
