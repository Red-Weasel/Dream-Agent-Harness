"""str_replace_edit names an indentation-insensitive near miss instead of only "read the file again".

2026-09-23 10:18-10:21, live session 20260923-094144-f5ed (MiMo-V2.6): three edits in a row failed with "old_string
matches 0 time(s)"; each old_string began mid-line with indentation the file did not have there
(`      \"\"\"PLACEHOLDER_ATTR\"\"\"` where the file had `      "summary": \"\"\"PLACEHOLDER_ATTR\"\"\"`). Ignoring each
line's leading and trailing whitespace, every one matched exactly once. The refusal now says so and quotes the file's
exact text, which the model can copy as old_string. Nothing changes for an exact match or a real miss.
"""
from __future__ import annotations

import json

import pytest

from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.files import str_replace_edit

LIVE = ('{\n  "nodes": [\n    {\n      "id": "function:rocket/build.py:set_attr",\n'
        '      "summary": """PLACEHOLDER_ATTR"""\n    }\n  ],\n  "__edges_pending__": true\n}')


@pytest.fixture
def ws(tmp_path):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    yield tmp_path
    tool_context._CTX = None


def _text(res) -> str:
    return res["content"][0]["text"]


@pytest.mark.asyncio
async def test_the_live_miss_names_the_near_match_and_quotes_the_files_text(ws):
    f = ws / "batch-1.json"
    f.write_text(LIVE)
    res = await str_replace_edit.handler({"path": "batch-1.json", "new_string": "x",
                                          "old_string": '      """PLACEHOLDER_ATTR"""\n    }\n  ],'})
    text = _text(res)
    assert res.get("is_error") and "matches 0 time(s)" in text and "Nothing was changed" in text
    assert "Ignoring indentation it matches once, at line 5" in text
    quoted = json.dumps('"""PLACEHOLDER_ATTR"""\n    }\n  ],')
    assert quoted in text                                       # the exact file text, ready to copy
    assert f.read_text() == LIVE
    # copying the quoted text works
    ok = await str_replace_edit.handler({"path": "batch-1.json", "old_string": json.loads(quoted), "new_string": '"done"'})
    assert not ok.get("is_error") and '"summary": "done"' in f.read_text()


@pytest.mark.asyncio
async def test_a_real_miss_and_an_ambiguous_near_miss_say_nothing_extra(ws):
    f = ws / "a.txt"
    f.write_text("  alpha\n  beta\n\n    alpha\n    beta\n")
    miss = _text(await str_replace_edit.handler({"path": "a.txt", "old_string": "gamma", "new_string": "x"}))
    assert "matches 0 time(s)" in miss and "Ignoring indentation" not in miss
    two = _text(await str_replace_edit.handler({"path": "a.txt", "old_string": "alpha\nbeta", "new_string": "x"}))
    assert "matches 0 time(s)" in two and "2 places" in two and "widen" in two.lower()
    blank = _text(await str_replace_edit.handler({"path": "a.txt", "old_string": "   ", "new_string": "x"}))
    assert "Ignoring indentation" not in blank
    assert f.read_text() == "  alpha\n  beta\n\n    alpha\n    beta\n"


FILE_410 = ('nt.links.new(tc.outputs["Object"], grad.inputs["Vector"])\n'
            'nt.links.new(tc.outputs["Object"], nui.inputs["Vector"])\n'
            'nt.links.new(nui.outputs["Fac"], cr.inputs["Ф"] if False else cr.inputs[0])\n'
            'nt.links.new(grad.outputs["Fac"], mul.inputs[0])\n'
            'nt.links.new(cr.outputs["Color"], mul.inputs[1])\n')


@pytest.mark.asyncio
async def test_a_line_the_file_never_had_is_named_with_the_files_closest_line(ws):
    """2026-09-23 10:56, the same live session: the model's old_string recalled its own earlier write with
    look-alike characters (`"Fac”, cr.inputs[Ⅎ]` for `"Fac"], cr.inputs["Ф"]`). No whitespace near miss exists, so
    the refusal said only "read the file again"; it now names the first old_string line the file lacks and the
    file's closest line, with its number."""
    f = ws / "build.py"
    f.write_text(FILE_410)
    old = ('nt.links.new(tc.outputs["Object"], nui.inputs["Vector"])\n'
           'nt.links.new(nui.outputs["Fac”, cr.inputs[Ⅎ] if False else cr.inputs[0])\n'
           'nt.links.new(grad.outputs["Fac"], mul.inputs[0])')
    text = _text(await str_replace_edit.handler({"path": "build.py", "old_string": old, "new_string": "x"}))
    assert "matches 0 time(s)" in text and "Ignoring indentation" not in text
    assert "line 2 of old_string is not in the file" in text
    assert "closest is line 3:" in text
    assert json.dumps('nt.links.new(nui.outputs["Fac"], cr.inputs["Ф"] if False else cr.inputs[0])', ensure_ascii=False) in text
    assert f.read_text() == FILE_410
    # a line with nothing like it in the file gets no closest line
    far = _text(await str_replace_edit.handler({"path": "build.py", "old_string": "import antigravity", "new_string": "x"}))
    assert "line 1 of old_string is not in the file" in far and "closest" not in far
