"""DREAM-114: `see` sniffs and fully decodes every image with Pillow before it enters a
request, whatever the file's extension says. On 2026-09-24 a WebP file named `.png`
went to the engine as image/png and the whole turn failed ("vision: not a decodable
image") three times in a row. A bad file must cost one tool result, not the turn."""
from __future__ import annotations

import base64
import io
import subprocess

import pytest
from PIL import Image

from dream.tools import vision

pytestmark = pytest.mark.asyncio

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _rgb() -> Image.Image:
    im = Image.new("RGB", (6, 4), (200, 30, 30))
    im.putpixel((0, 0), (0, 0, 255))
    im.putpixel((5, 3), (0, 255, 0))
    return im


def _encoded(fmt: str, im: Image.Image | None = None, **save_kw) -> bytes:
    out = io.BytesIO()
    (im or _rgb()).save(out, format=fmt, **save_kw)
    return out.getvalue()


def _write(folder, name: str, data: bytes) -> str:
    p = folder / name
    p.write_bytes(data)
    return str(p)


def _images(result) -> list[dict]:
    return [b for b in result["content"] if b["type"] == "image"]


def _decoded(block) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(block["data"])))


def _text(result) -> str:
    texts = [b for b in result["content"] if b["type"] == "text"]
    assert len(texts) == 1 and result["content"][-1] is texts[0], "one text block, last, as before"
    return texts[0]["text"]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    class Ctx:
        multimodal = True
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())
    return tmp_path


# (a) the live failure: a WebP file with a .png name -----------------------------------------

async def test_a_webp_named_png_is_sent_as_a_real_png_with_a_note(folder):
    src = _rgb()
    result = await vision.see.handler({"path": _write(folder, "poster.png", _encoded("WEBP", src, lossless=True))})
    assert not result.get("is_error")
    (img,) = _images(result)
    data = base64.b64decode(img["data"])
    assert img["mimeType"] == "image/png" and data[:8] == PNG_SIG
    with Image.open(io.BytesIO(data)) as out:
        assert out.format == "PNG" and out.mode == "RGB"
        assert list(out.getdata()) == list(src.getdata()), "lossless in, the same pixels out"
    assert _text(result) == "(viewing poster.png)\nposter.png: WEBP, converted to PNG"


async def test_conversion_keeps_alpha_and_drops_animation(folder):
    badge = Image.new("RGBA", (6, 4), (10, 20, 30, 128))
    frames = [Image.new("RGB", (6, 4), (i * 60, 0, 0)) for i in range(3)]
    spinner = _encoded("WEBP", frames[0], save_all=True, append_images=frames[1:], duration=50, lossless=True)
    paths = [_write(folder, "badge.png", _encoded("WEBP", badge, lossless=True)),
             _write(folder, "spinner.png", spinner)]
    result = await vision.see.handler({"paths": paths})
    first, second = (_decoded(b) for b in _images(result))
    assert first.format == "PNG" and first.mode == "RGBA" and first.getpixel((0, 0)) == (10, 20, 30, 128)
    assert second.format == "PNG" and second.mode == "RGB" and getattr(second, "n_frames", 1) == 1
    assert second.getpixel((0, 0)) == (0, 0, 0), "the first frame"
    text = _text(result)
    assert text.startswith("(viewing badge.png, spinner.png)")
    assert "badge.png: WEBP, converted to PNG" in text and "spinner.png: WEBP, converted to PNG" in text


# (b) a JPEG with a .png name keeps its bytes under its real type ---------------------------------

async def test_a_jpeg_named_png_keeps_its_bytes_and_gets_image_jpeg(folder):
    jpeg = _encoded("JPEG", quality=90)
    result = await vision.see.handler({"path": _write(folder, "photo.png", jpeg)})
    (img,) = _images(result)
    assert img["mimeType"] == "image/jpeg" and base64.b64decode(img["data"]) == jpeg
    assert _text(result) == "(viewing photo.png)", "no conversion, no note"


async def test_a_phone_jpeg_with_extra_frames_passes_through_as_jpeg(folder):
    im = _rgb()
    mpo = _encoded("MPO", im, save_all=True, append_images=[im])
    with Image.open(io.BytesIO(mpo)) as check:
        assert check.format == "MPO", "Pillow does not call these JPEG, though every JPEG decoder reads them"
    result = await vision.see.handler({"path": _write(folder, "burst.jpg", mpo)})
    (img,) = _images(result)
    assert img["mimeType"] == "image/jpeg" and base64.b64decode(img["data"]) == mpo
    assert _text(result) == "(viewing burst.jpg)"


# (c) a real PNG is untouched ----------------------------------------------------------------

async def test_a_real_png_passes_through_byte_identical(folder):
    png = _encoded("PNG")
    result = await vision.see.handler({"path": _write(folder, "chart.png", png)})
    (img,) = _images(result)
    assert img["mimeType"] == "image/png" and base64.b64decode(img["data"]) == png
    assert _text(result) == "(viewing chart.png)"


# (d) a bad file costs one line; the others still arrive ---------------------------------------

async def test_a_garbage_file_costs_one_line_and_the_good_image_is_still_delivered(folder):
    png = _encoded("PNG")
    paths = [_write(folder, "broken.png", b"this is not an image, whatever its name says\n" * 4),
             _write(folder, "chart.png", png)]
    result = await vision.see.handler({"paths": paths})
    assert not result.get("is_error")
    (img,) = _images(result)
    assert img["mimeType"] == "image/png" and base64.b64decode(img["data"]) == png
    assert _text(result) == "(viewing chart.png)\nimage not decodable: broken.png (cannot identify image file)"


async def test_a_truncated_png_is_caught_by_the_full_decode(folder):
    whole = _encoded("PNG", Image.effect_noise((64, 64), 60).convert("RGB"))
    paths = [_write(folder, "clipped.png", whole[: len(whole) // 2]),
             _write(folder, "chart.png", _encoded("PNG"))]
    result = await vision.see.handler({"paths": paths})
    assert not result.get("is_error") and len(_images(result)) == 1
    text = _text(result)
    assert text.startswith("(viewing chart.png)\n")
    assert "image not decodable: clipped.png (image file is truncated" in text


async def test_a_conversion_that_outgrows_the_size_limit_is_not_delivered(folder, monkeypatch):
    webp = _encoded("WEBP", quality=50)
    monkeypatch.setattr(vision, "_MAX_BYTES", len(webp))   # the file itself fits; its PNG cannot
    result = await vision.see.handler({"path": _write(folder, "tiny.png", webp)})
    assert result.get("is_error") is True and not _images(result)
    assert result["content"][0]["text"] == "image not delivered: tiny.png (converted PNG is 0.0MB, over the 0MB limit)"


# (e) nothing decodable: the call is an error, naming the file -----------------------------------

async def test_only_undecodable_files_make_the_call_an_error_naming_them(folder):
    result = await vision.see.handler({"path": _write(folder, "broken.png", b"\x00\x01\x02 nothing here")})
    assert result.get("is_error") is True and not _images(result)
    assert result["content"][0]["text"] == "image not decodable: broken.png (cannot identify image file)"


# The gate's attack surface: Pillow may try only an allow-list of decoders on the bytes. Without
# it, Pillow tries every plugin, and the EPS one runs ghostscript with no timeout: a `{ } loop`
# PostScript file named .png hung `see` for good. ---------------------------------------------

async def test_postscript_named_png_is_refused_without_starting_ghostscript(folder, monkeypatch):
    started = []

    def no_children(*args, **kwargs):
        started.append(args[0] if args else kwargs.get("args"))
        raise AssertionError("a child process was started")

    monkeypatch.setattr(subprocess, "Popen", no_children)  # check_call, run and call all end here
    postscript = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%EndComments\n{ } loop\n"
    result = await vision.see.handler({"path": _write(folder, "vector.png", postscript)})
    assert result.get("is_error") is True and not _images(result)
    assert result["content"][0]["text"] == "image not decodable: vector.png (cannot identify image file)"
    assert started == [], "Pillow's EPS plugin would have run ghostscript with no timeout"


async def test_only_the_listed_formats_are_opened_even_when_harmless(folder):
    ppm = b"P6\n2 2\n255\n" + bytes(range(12))  # Pillow reads PPM; the tool does not offer it
    result = await vision.see.handler({"path": _write(folder, "grid.png", ppm)})
    assert result.get("is_error") is True
    assert result["content"][0]["text"] == "image not decodable: grid.png (cannot identify image file)"


async def test_bmp_tiff_and_ico_are_converted(folder):
    icon = Image.new("RGB", (16, 16), (1, 2, 3))
    paths = [_write(folder, "scan.png", _encoded("BMP")), _write(folder, "plate.png", _encoded("TIFF")),
             _write(folder, "mark.png", _encoded("ICO", icon))]
    result = await vision.see.handler({"paths": paths})
    assert not result.get("is_error")
    assert [(b["mimeType"], _decoded(b).format) for b in _images(result)] == [("image/png", "PNG")] * 3
    text = _text(result)
    for line in ("scan.png: BMP, converted to PNG", "plate.png: TIFF, converted to PNG", "mark.png: ICO, converted to PNG"):
        assert line in text


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
async def test_the_decompression_bomb_warning_tier_is_refused_before_decoding(folder, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 20)  # a 6x4 image (24 px) sits in Pillow's warn-only tier (20 < 24 <= 40)
    result = await vision.see.handler({"path": _write(folder, "huge.png", _encoded("PNG"))})
    assert result.get("is_error") is True
    assert result["content"][0]["text"] == "image not decodable: huge.png (Image size (24 pixels) exceeds limit of 20 pixels)"
