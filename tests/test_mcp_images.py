"""External MCP tools that return pictures (DREAM-109: live Blender's viewport screenshot).

A model with image input gets the pictures, bounded in number and size; a model without it
gets the text and a note that no pixels were attached, never raw image blocks. Results with
no pictures are exactly what they were before.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

from dream import mcp_client
from dream.mcp_client import McpClients
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, bind_context

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

SERVER = r'''
import base64, sys
from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ImageContent, TextContent

PNG = base64.b64decode(sys.argv[1])
mcp = FastMCP("pics")

@mcp.tool()
def words() -> str:
    """Only text."""
    return "just words"

@mcp.tool()
def picture() -> list:
    """Text and one picture."""
    return ["the viewport", Image(data=PNG, format="png")]

@mcp.tool()
def many() -> list:
    """Six pictures."""
    return [Image(data=PNG, format="png") for _ in range(6)]

@mcp.tool()
def odd() -> list:
    """A picture type models do not take, a broken picture and a huge one."""
    return [ImageContent(type="image", data=base64.b64encode(b"<svg/>").decode(), mimeType="image/svg+xml"),
            ImageContent(type="image", data="not base64 at all!", mimeType="image/png"),
            ImageContent(type="image", data=base64.b64encode(b"\x00" * (5 * 1024 * 1024)).decode(),
                         mimeType="image/png"),
            TextContent(type="text", text="after")]

mcp.run()
'''


@pytest.fixture
async def pics(tmp_path):
    script = tmp_path / "pics_server.py"
    script.write_text(SERVER)
    clients = McpClients()
    tools, warnings = await clients.start([{"name": "pics", "command": sys.executable,
                                            "args": [str(script), base64.b64encode(PNG).decode()]}])
    assert not warnings
    yield {t.name.split("__", 1)[1]: t for t in tools}
    await clients.stop()


def _context(tmp_path: Path, multimodal: bool) -> ToolContext:
    return ToolContext(None, None, None, "s", workspace=tmp_path, multimodal=multimodal)


async def _call(tool, tmp_path, multimodal):
    if multimodal is None:  # no session context at all
        saved, tool_context._CTX = tool_context._CTX, None
        try:
            return await tool.handler({})
        finally:
            tool_context._CTX = saved
    with bind_context(_context(tmp_path, multimodal)):
        return await tool.handler({})


@pytest.mark.parametrize("multimodal", [True, False, None])
async def test_a_text_only_result_is_unchanged(pics, tmp_path, multimodal):
    assert await _call(pics["words"], tmp_path, multimodal) == {
        "content": [{"type": "text", "text": "just words"}], "is_error": False}


async def test_a_model_with_image_input_gets_the_picture(pics, tmp_path):
    result = await _call(pics["picture"], tmp_path, True)
    assert result["is_error"] is False
    assert result["content"] == [{"type": "text", "text": "the viewport"},
                                 {"type": "image", "mimeType": "image/png",
                                  "data": base64.b64encode(PNG).decode()}]


@pytest.mark.parametrize("multimodal", [False, None])
async def test_a_model_without_image_input_gets_text_and_a_note(pics, tmp_path, multimodal):
    result = await _call(pics["picture"], tmp_path, multimodal)
    assert [block["type"] for block in result["content"]] == ["text"]
    text = result["content"][0]["text"]
    assert text.startswith("the viewport\n") and "not attached" in text and "no image input" in text


async def test_pictures_are_bounded_in_number(pics, tmp_path):
    result = await _call(pics["many"], tmp_path, True)
    blocks = result["content"]
    assert [b["type"] for b in blocks].count("image") == mcp_client.MAX_IMAGES == 4
    assert "2 more image(s) omitted" in blocks[0]["text"]


async def test_unusable_or_oversized_pictures_are_named_not_passed(pics, tmp_path):
    result = await _call(pics["odd"], tmp_path, True)
    blocks = result["content"]
    assert [b["type"] for b in blocks] == ["text"]
    text = blocks[0]["text"]
    assert text.startswith("after\n")
    assert "image/svg+xml" in text and "not valid base64" in text and "over the 4 MB limit" in text
