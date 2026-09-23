"""measure_image: exact pixel facts for a model that cannot see (DREAM-094).

A local model without vision (MiMo-V2.6 today) checked its own renders by hand-writing
pixel statistics in shell scripts -- mean luminance, "pixels differing by >= 16 levels" --
and Dream learned skills for it twice. This is that measurement as one tool, with the
formulas fixed here so every model gets the same numbers:

- luminance: Rec. 601 luma  Y = 0.299 R + 0.587 G + 0.114 B  on 0-255 (the alpha channel,
  when there is one, is reported separately and never composited into the measurements;
  only OCR composites, below);
- near-black: Y <= 16; near-white: Y >= 239 (sixteen levels from either end, the same step
  the change threshold below uses);
- verdict: "blank" when >= 99 % of the pixels are near-black or near-white; "uniform" when
  every channel's standard deviation is under 2 levels (one flat colour); otherwise
  "not blank";
- dominant colours: every pixel is binned at 5 bits per channel (32,768 bins); each
  reported colour is the mean of its bin's pixels, listed with the bin's share, the top
  five bins holding at least 1 %;
- edge density: the share of pixels whose luminance differs from the right-hand or the
  lower neighbour by >= 32 levels (forward differences; the last column and row have
  no such neighbour and count as no edge);
- compare_to: a pixel is "changed" when any RGB channel differs by >= 16 levels; the mean
  absolute difference is over every pixel and channel; SSIM is Wang et al. 2004 on the
  luma with the scikit-image defaults -- a 7x7 uniform window, sample covariance
  (N - 1), K1 = 0.01, K2 = 0.03, L = 255 -- averaged over the windows that lie fully
  inside the image. Images of different size are both resized to the smaller width and
  height (box filter) first, and the result says so;
- ocr: tesseract (default page segmentation, the installed language data) when the binary
  is on PATH; otherwise the result says OCR is unavailable. An image whose longer side is
  under 2000 px is upscaled 1.5x (bicubic) first -- an alpha channel composited on white
  before that, so text over transparency keeps a background -- and the text line says
  "OCR at 1.5x": on fixtures of 20-24 px UI text an upscale read 106 of 126 words against
  104 at 1x and never fewer, and 1.5x reads the same words as 2x while isolated digits read
  better (zeros 39/46 at 1x, 21/48 at 2x, 35/48 at 1.5x; 2026-09-23, tesseract 5.5.1); a
  larger image goes to tesseract as the file, which it composites for itself;
- 16-bit ("I;16", "I;16B", "I;16L", "I;16N") and 32-bit integer ("I") images are scaled to
  8-bit by >> 8, float ("F") images by their maximum when it is above 1 and by x255
  otherwise, before any measurement (Pillow's RGB conversion would clip them to 0-255);
  the header line says the image was scaled.

Numbers, not visual evidence. `see` shows the picture when image input is on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from claude_agent_sdk import tool

from .context import ctx, err, in_thread, ok

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)   # Rec. 601
_NEAR = 16          # near-black: Y <= 16; near-white: Y >= 255 - 16
_BLANK_SHARE = 0.99
_UNIFORM_STD = 2.0
_COLOUR_BITS = 5    # 32 levels per channel, 32,768 bins
_COLOUR_TOP = 5
_COLOUR_MIN_SHARE = 0.01
_EDGE_LEVELS = 32
_CHANGE_LEVELS = 16
_SSIM_WIN = 7
_SSIM_C1 = (0.01 * 255) ** 2
_SSIM_C2 = (0.03 * 255) ** 2
_MAX_PIXELS = 4 * 3840 * 2160     # ~33 MP (8K); 4K measures in well under 2 s, this in ~8 s
_OCR_TIMEOUT = 60
_OCR_CHARS = 400
_OCR_UPSCALE_BELOW = 2000   # longer side under this: tesseract reads a bicubic upscale piped in ...
_OCR_UPSCALE = 1.5          # ... by this factor (2x read isolated digits worse: zeros 21/48 against 35/48 at 1.5x)
_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off", ""}


class _Unreadable(Exception):
    """A file that could not be decoded as an image; the message names that file."""


def _resolve(raw: str) -> Path | None:
    """Relative paths live in the session workspace; absolute paths pass through; None for a path no
    filesystem can hold (a NUL byte), which is then simply not a file."""
    try:
        return (ctx().workspace / Path(raw).expanduser()).resolve()
    except ValueError:
        return None


def _flag(value: Any) -> bool | None:
    """A boolean argument as JSON (true/false, or the numbers 1/0) or as text ("false" is off, not truthy);
    None when it is neither."""
    if value is None or isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
    return None


@tool(
    "measure_image",
    "Measure an image's pixels: luminance, blank/uniform verdict, dominant colours, edges, diff, OCR. "
    "For checking a render or screenshot you cannot look at (image input off): size and channels, "
    "Rec. 601 luminance mean/std/min/max, near-black and near-white shares with a blank/uniform "
    "verdict, the dominant colours with percentages, edge density. `compare_to` adds the percent of "
    "pixels changed by >= 16 levels, the mean absolute difference and grayscale SSIM (1.0 = identical) "
    "against a second image. `ocr: true` reads visible text with tesseract when it is installed "
    "(seconds on a large image). "
    "Exact numbers, not visual evidence; use `see` for layout when image input is on. No approval needed.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The image to measure (png/jpg/webp/gif/bmp...)."},
            "compare_to": {"type": "string",
                           "description": "A second image: changed-pixel percent, mean |diff|, SSIM."},
            "ocr": {"type": "boolean", "description": "Also read the visible text with tesseract."},
        },
        "required": ["path"],
    },
)
async def measure_image(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("path")
    if not isinstance(raw, str) or not raw:
        return err("measure_image needs a 'path' argument.")
    p = _resolve(raw)
    if p is None or not p.is_file():
        return err(f"No file at {p if p is not None else repr(raw)}")
    other_raw = args.get("compare_to")
    other = None
    if other_raw:
        other = _resolve(str(other_raw))
        if other is None or not other.is_file():
            return err(f"compare_to: no file at {other if other is not None else repr(str(other_raw))}")
    ocr = _flag(args.get("ocr"))
    if ocr is None:
        return err(f"measure_image: 'ocr' must be true or false (got {args.get('ocr')!r}); "
                   "accepted spellings: true/yes/1/on and false/no/0/off.")
    try:
        return ok(await in_thread(_report, p, other, ocr))
    except _Unreadable as e:
        return err(str(e))
    except Exception as e:
        return err(f"measure_image failed on {p.name}: {type(e).__name__}: {e}")


def _report(p: Path, other: Path | None, ocr: bool) -> str:
    fmt, mode, frames, rgb, alpha = _load(p)
    h, w = rgb.shape[:2]
    n = _channels(mode)
    lines = [f"{p.name}: {w}x{h}, {mode} ({n} channel{'s' if n != 1 else ''}{_scaled(mode)}), {fmt}"
             + (f", first of {frames} frames" if frames > 1 else "")]
    lines.extend(_measure(rgb, alpha))
    if other is not None:
        lines.extend(_compare(p, rgb, other))
    if ocr:
        lines.append(_ocr(p, rgb, alpha))
    return "\n".join(lines)


def _load(p: Path):
    from PIL import Image

    try:
        with Image.open(p) as im:
            fmt, mode = im.format or "unknown", im.mode
            frames = int(getattr(im, "n_frames", 1) or 1)
            w, h = im.size
            if w * h > _MAX_PIXELS:
                raise ValueError(f"{w}x{h} is over the {_MAX_PIXELS // 1_000_000} MP limit; downscale a copy first")
            if _scaled(mode):
                return fmt, mode, frames, _rgb8(np.asarray(im), mode), None
            has_alpha = mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            if has_alpha:
                arr = np.asarray(im.convert("RGBA"))
                return fmt, mode, frames, np.ascontiguousarray(arr[..., :3]), arr[..., 3]
            return fmt, mode, frames, np.asarray(im.convert("RGB")), None
    except Exception as e:
        raise _Unreadable(f"Could not read {p.name} as an image: {type(e).__name__}: {e}") from e


def _scaled(mode: str) -> str:
    """The header note for a mode whose values are scaled to 8-bit here ("" for the others)."""
    if mode.startswith("I;16"):
        return ", scaled from 16-bit"
    if mode == "I":
        return ", scaled from 32-bit int"
    if mode == "F":
        return ", scaled from float"
    return ""


def _rgb8(a: np.ndarray, mode: str) -> np.ndarray:
    """One-channel 16-bit / 32-bit int / float pixels scaled to 8-bit and replicated into three channels."""
    if mode == "F":
        a = np.nan_to_num(a.astype(np.float64))
        top = float(a.max())
        grey = np.clip(np.rint(a / top * 255.0 if top > 1.0 else a * 255.0), 0, 255).astype(np.uint8)
    else:
        grey = np.clip(a.astype(np.int64) >> 8, 0, 255).astype(np.uint8)
    return np.repeat(grey[..., None], 3, axis=-1)


def _channels(mode: str) -> int:
    return {"1": 1, "L": 1, "P": 1, "I": 1, "F": 1, "I;16": 1, "I;16B": 1, "I;16L": 1, "I;16N": 1, "LA": 2,
            "PA": 2, "RGB": 3, "YCbCr": 3, "LAB": 3, "HSV": 3, "RGBA": 4, "CMYK": 4, "RGBa": 4}.get(mode, len(mode))


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb.astype(np.float32) @ _LUMA


def _measure(rgb: np.ndarray, alpha: np.ndarray | None) -> list[str]:
    y = _luma(rgb)
    n = y.size
    mean, std = float(y.mean()), float(y.std())
    near_black = float(np.count_nonzero(y <= _NEAR)) / n
    near_white = float(np.count_nonzero(y >= 255 - _NEAR)) / n
    channel_std = rgb.reshape(-1, 3).std(axis=0)
    if near_black >= _BLANK_SHARE:
        verdict = f"blank (black: {near_black:.1%} near-black)"
    elif near_white >= _BLANK_SHARE:
        verdict = f"blank (white: {near_white:.1%} near-white)"
    elif float(channel_std.max()) < _UNIFORM_STD:
        verdict = f"uniform (one flat colour {_hex(rgb.reshape(-1, 3).mean(axis=0))})"
    else:
        verdict = f"not blank (content present, luminance std {std:.1f})"
    lines = [
        f"luminance (Rec. 601, 0-255): mean {mean:.1f}  std {std:.1f}  min {float(y.min()):.1f}  max {float(y.max()):.1f}",
        f"near-black (<=16): {near_black:.1%}  near-white (>=239): {near_white:.1%}  verdict: {verdict}",
        "dominant colours (5-bit bins, top 5 >= 1%): " + _dominant(rgb),
        f"edge density: {_edges(y):.2%} of pixels differ from their right or lower neighbour by >= {_EDGE_LEVELS} levels",
    ]
    if alpha is not None:
        lines.append(f"alpha: {float(np.count_nonzero(alpha == 0)) / n:.1%} of pixels fully transparent"
                     f" (colour measured as stored, not composited)")
    return lines


def _hex(rgb_mean) -> str:
    r, g, b = (int(round(float(v))) for v in rgb_mean)
    return f"#{r:02x}{g:02x}{b:02x}"


def _dominant(rgb: np.ndarray) -> str:
    flat = rgb.reshape(-1, 3)
    shift = 8 - _COLOUR_BITS
    bins = ((flat[:, 0].astype(np.int32) >> shift) << (2 * _COLOUR_BITS)
            | (flat[:, 1].astype(np.int32) >> shift) << _COLOUR_BITS
            | (flat[:, 2].astype(np.int32) >> shift))
    counts = np.bincount(bins, minlength=1 << (3 * _COLOUR_BITS))
    top = np.argsort(counts)[::-1][:_COLOUR_TOP]
    n = flat.shape[0]
    parts = []
    for b in top:
        share = counts[b] / n
        if share < _COLOUR_MIN_SHARE:
            break
        members = flat[bins == b]
        parts.append(f"{_hex(members.mean(axis=0))} {share:.1%}")
    if not parts:
        return f"none reach 1% ({int(np.count_nonzero(counts))} bins in use)"
    return ", ".join(parts)


def _edges(y: np.ndarray) -> float:
    edge = np.zeros(y.shape, dtype=bool)
    edge[:, :-1] = np.abs(np.diff(y, axis=1)) >= _EDGE_LEVELS
    edge[:-1, :] |= np.abs(np.diff(y, axis=0)) >= _EDGE_LEVELS
    return float(np.count_nonzero(edge)) / y.size


def _compare(p: Path, rgb: np.ndarray, other: Path) -> list[str]:
    from PIL import Image

    _fmt, _mode, _frames, rgb2, _alpha = _load(other)
    h1, w1 = rgb.shape[:2]
    h2, w2 = rgb2.shape[:2]
    if (w1, h1) == (w2, h2):
        head = f"compare_to {other.name}: {w2}x{h2} (same size)"
    else:
        w, h = min(w1, w2), min(h1, h2)
        head = (f"compare_to {other.name}: sizes differ ({w1}x{h1} vs {w2}x{h2}) -- "
                f"compared at {w}x{h} after resizing both (box filter)")
        rgb = np.asarray(Image.fromarray(rgb, "RGB").resize((w, h), Image.Resampling.BOX))
        rgb2 = np.asarray(Image.fromarray(rgb2, "RGB").resize((w, h), Image.Resampling.BOX))
    diff = np.abs(rgb.astype(np.int16) - rgb2.astype(np.int16))
    changed = float(np.count_nonzero(diff.max(axis=2) >= _CHANGE_LEVELS)) / diff[..., 0].size
    mad = float(diff.mean())
    ssim = _ssim(_luma(rgb), _luma(rgb2))
    ssim_text = f"{ssim:.4f}" if ssim is not None else f"n/a (smaller than the {_SSIM_WIN}x{_SSIM_WIN} window)"
    return [head, f"  changed (any channel >= {_CHANGE_LEVELS} levels): {changed:.2%}  mean |diff|: {mad:.2f} levels"
                  f"  SSIM (grey, {_SSIM_WIN}x{_SSIM_WIN}): {ssim_text}"]


def _box_mean(a: np.ndarray, k: int) -> np.ndarray:
    """Mean over every k x k window fully inside ``a`` (an integral image, float64 so
    identical inputs give identical means and SSIM of a copy is exactly 1.0)."""
    s = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    np.cumsum(np.cumsum(a, axis=0, dtype=np.float64), axis=1, out=s[1:, 1:])
    return (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)


def _ssim(x: np.ndarray, y: np.ndarray) -> float | None:
    k = _SSIM_WIN
    if x.shape[0] < k or x.shape[1] < k:
        return None
    x = x.astype(np.float64)
    y = y.astype(np.float64)
    ux, uy = _box_mean(x, k), _box_mean(y, k)
    cov_norm = (k * k) / (k * k - 1)                     # sample covariance
    vx = cov_norm * (_box_mean(x * x, k) - ux * ux)
    vy = cov_norm * (_box_mean(y * y, k) - uy * uy)
    vxy = cov_norm * (_box_mean(x * y, k) - ux * uy)
    s = ((2 * ux * uy + _SSIM_C1) * (2 * vxy + _SSIM_C2)) / ((ux * ux + uy * uy + _SSIM_C1) * (vx + vy + _SSIM_C2))
    return float(s.mean())


def _ocr(p: Path, rgb: np.ndarray, alpha: np.ndarray | None) -> str:
    binary = shutil.which("tesseract")
    if binary is None:
        return "text: OCR unavailable -- tesseract is not installed on this machine; no text was read."
    h, w = rgb.shape[:2]
    if max(w, h) < _OCR_UPSCALE_BELOW:
        # Small UI text (20-24 px) reads better enlarged: the measured 8-bit pixels, bicubic, piped in.
        import io
        from PIL import Image

        if alpha is not None:
            # The colour stored under transparent pixels is arbitrary (often black, which swallows dark text):
            # composite on white, as tesseract manages for itself when it reads the file (probed 2026-09-23).
            a = alpha[..., None].astype(np.float32) / 255.0
            rgb = np.rint(rgb.astype(np.float32) * a + 255.0 * (1.0 - a)).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(rgb).resize((round(_OCR_UPSCALE * w), round(_OCR_UPSCALE * h)),
                                    Image.Resampling.BICUBIC).save(buf, "PNG", compress_level=1)
        argv, data, at = [binary, "stdin", "stdout"], buf.getvalue(), f" (OCR at {_OCR_UPSCALE:g}x)"
    else:
        argv, data, at = [binary, str(p), "stdout"], None, ""
    try:
        run = subprocess.run(argv, input=data, capture_output=True, timeout=_OCR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"text: OCR gave up after {_OCR_TIMEOUT} s{at}; no text was read."
    if run.returncode != 0:
        tail = run.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no message"]
        return f"text: OCR failed (tesseract exit {run.returncode}: {tail[0]}){at}; no text was read."
    words = " ".join(run.stdout.decode("utf-8", "replace").split())
    if not words:
        return f"text (tesseract): none found{at}"
    if len(words) > _OCR_CHARS:
        words = words[:_OCR_CHARS].rstrip() + f"... [{len(words) - _OCR_CHARS} more characters]"
    return f'text (tesseract): "{words}"{at}'
