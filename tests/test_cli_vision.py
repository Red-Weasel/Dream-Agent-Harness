"""Astra (codex CLI) can see: `see` returns an MCP image block, the session bridge
preserves it, and the codex provider no longer declares image input off."""
import base64
import json

import pytest

from dream.core.providers import PROVIDERS
from dream.mcp import bridge as bridge_mod
from dream.tools import vision

pytestmark = pytest.mark.asyncio

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


async def test_the_codex_cli_provider_allows_images():
    assert PROVIDERS["codex"].multimodal is True


async def test_see_returns_an_image_block_that_the_bridge_preserves(tmp_path, monkeypatch):
    shot = tmp_path / "frame.png"
    shot.write_bytes(PNG)
    monkeypatch.setattr(vision, "_resolve", lambda p: shot)

    class Ctx:
        multimodal = True
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())

    result = await vision.see.handler({"path": str(shot)})
    image = next(b for b in result["content"] if b["type"] == "image")
    assert image["mimeType"] == "image/png" and base64.b64decode(image["data"]) == PNG

    # What the parent callback returns must survive the bridge's MCP conversion
    # with the pixels intact -- that is the hop that reaches the codex CLI.
    wire = bridge_mod._result(result)
    carried = next(b for b in wire["content"] if b["type"] == "image")
    assert carried["data"] == image["data"] and carried["mimeType"] == "image/png"
    assert json.loads(json.dumps(wire))            # it is JSON, as the bridge ships it


async def test_see_still_refuses_when_a_session_has_images_off(tmp_path, monkeypatch):
    shot = tmp_path / "frame.png"
    shot.write_bytes(PNG)

    class Ctx:
        multimodal = False
    monkeypatch.setattr(vision, "ctx", lambda: Ctx())
    result = await vision.see.handler({"path": str(shot)})
    assert result.get("is_error") or result.get("isError")
