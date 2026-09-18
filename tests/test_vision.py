"""Offline tests for the vision tool's plumbing (no model)."""

import asyncio
import struct
import zlib

from dream.tools import vision


def _png(path, w=8, h=8, rgb=(255, 0, 255)):
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    out = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(out)


def test_see_returns_image_content(tmp_path):
    p = tmp_path / "pic.png"
    _png(p)
    result = asyncio.run(vision.see.handler({"path": str(p)}))
    blocks = result["content"]
    img = [b for b in blocks if b["type"] == "image"]
    assert img and img[0]["mimeType"] == "image/png"
    assert isinstance(img[0]["data"], str) and len(img[0]["data"]) > 0


def test_see_missing_file():
    result = asyncio.run(vision.see.handler({"path": "/nope/does-not-exist.png"}))
    assert result.get("is_error") is True


def test_see_unsupported_type(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("not an image")
    result = asyncio.run(vision.see.handler({"path": str(p)}))
    assert result.get("is_error") is True
