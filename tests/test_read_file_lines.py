"""read_file speaks lines too: the model's prior is offset/limit in LINES, the tool's
units are characters (2026-09-21: limit=160 returned 160 characters, twice)."""
from __future__ import annotations
import pytest
from dream.tools.context import ToolContext, bind_context
from dream.tools.native import read_file

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path):
    with bind_context(ToolContext(store=None, working=None, browser=None,
                                  session_id="lines", workspace=tmp_path)):
        yield tmp_path


def text(result):
    return result["content"][0]["text"]


def _source(workspace, n=300):
    p = workspace / "env.js"
    p.write_text("".join(f"line {i:03d} // some code here\n" for i in range(1, n + 1)))
    return p


async def test_start_line_and_line_count_return_exactly_those_lines(workspace):
    p = _source(workspace)
    result = await read_file.handler({"path": str(p), "start_line": 10, "line_count": 5})
    body = text(result)
    assert body.startswith("line 010") and "line 014" in body and "line 015" not in body and "line 009" not in body
    assert "[lines 10-14 of 300" in body


async def test_line_mode_hint_points_at_the_next_block(workspace):
    p = _source(workspace)
    body = text(await read_file.handler({"path": str(p), "start_line": 296, "line_count": 10}))
    assert "line 300" in body and "[lines 296-300 of 300" in body and "end of file" in body


async def test_a_small_character_limit_explains_the_units(workspace):
    p = _source(workspace)
    body = text(await read_file.handler({"path": str(p), "offset": 1, "limit": 160}))
    assert "160 characters" in body and "start_line" in body


async def test_schema_says_characters_and_offers_lines():
    props = read_file.input_schema["properties"]
    assert "CHARACTERS" in props["limit"]["description"] and "CHARACTERS" in props["offset"]["description"]
    assert "start_line" in props and "line_count" in props


async def test_the_description_names_the_line_parameters_and_says_limit_is_never_lines():
    """2026-10-02: a model told "read at most 200 lines" called read_file with limit=200 and got 200 characters.
    The wording must say which parameters read LINES and that offset / limit never do. Key phrases, pinned."""
    said = read_file.description
    assert "To read LINES, use start_line and line_count" in said
    assert "start_line=1, line_count=200" in said, "an example of the first 200 lines"
    assert "offset and limit count CHARACTERS, never lines" in said
    assert "limit=200 returns 200 characters" in said
    assert said.index("start_line") < said.index("limit"), "the line parameters come first"
    assert said.startswith("Read text or PDF/DOCX/XLSX/PPTX extracts."), "the catalog line (first sentence) is kept"
    props = read_file.input_schema["properties"]
    assert props["limit"]["description"].startswith("A number of CHARACTERS, not lines")
    assert "line_count" in props["limit"]["description"]
    assert props["offset"]["description"].startswith("A position in CHARACTERS, not a line number")
    assert "start_line" in props["offset"]["description"]
    assert props["start_line"]["description"].startswith("First LINE to read")
    assert props["line_count"]["description"].startswith("Number of LINES to read")
