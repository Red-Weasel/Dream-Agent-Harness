"""`see` takes several images at once: nine frame reviews were nine 40-66 s model
round trips on 2026-09-21 because the schema had only `path`."""
from __future__ import annotations
import base64
import pytest
from dream.tools import vision

pytestmark = pytest.mark.asyncio
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def shots(tmp_path, monkeypatch):
    files = []
    for name in ("a.png", "b.png", "c.png"):
        f = tmp_path / name
        f.write_bytes(PNG)
        files.append(f)

    class Ctx:
        multimodal = True
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())
    monkeypatch.setattr(vision, "_resolve", lambda p: next((f for f in files if str(f) == p), None))
    return files


async def test_paths_return_one_image_block_per_file_and_one_caption(shots):
    result = await vision.see.handler({"paths": [str(shots[0]), str(shots[1])]})
    images = [b for b in result["content"] if b["type"] == "image"]
    captions = [b["text"] for b in result["content"] if b["type"] == "text"]
    assert len(images) == 2 and all(base64.b64decode(b["data"]) == PNG for b in images)
    assert captions == ["(viewing a.png, b.png)"]


async def test_a_missing_file_in_the_list_fails_the_whole_call(shots, tmp_path):
    result = await vision.see.handler({"paths": [str(shots[0]), str(tmp_path / "nope.png")]})
    assert result.get("is_error") and "nope.png" in result["content"][0]["text"]


async def test_too_many_paths_are_refused(shots):
    result = await vision.see.handler({"paths": [str(shots[0])] * 7})
    assert result.get("is_error") and "6" in result["content"][0]["text"]


async def test_schema_offers_paths_and_neither_is_required_alone():
    props = vision.see.input_schema["properties"]
    assert "paths" in props and props["paths"]["type"] == "array" and props["paths"].get("maxItems") == 6
    assert "required" not in vision.see.input_schema or vision.see.input_schema["required"] == []
