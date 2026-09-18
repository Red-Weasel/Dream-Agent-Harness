"""copy_starter_component: exact kinds, the vendor folder for JSX, the echo."""

from __future__ import annotations

import pytest

from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.starters import KINDS, STARTERS_DIR, VENDOR, copy_starter_component


@pytest.fixture
def ws(tmp_path):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    yield tmp_path
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


def test_every_kind_ships():
    for k in KINDS:
        assert (STARTERS_DIR / k).is_file(), k
    for v in VENDOR:
        assert (STARTERS_DIR / "vendor" / v).stat().st_size > 1000, v


@pytest.mark.asyncio
async def test_a_js_starter_copies_alone_and_echoes_its_content_and_tag(ws):
    res = await copy_starter_component.handler({"kind": "deck_stage.js"})
    assert not res.get("is_error")
    assert (ws / "deck_stage.js").read_text() == (STARTERS_DIR / "deck_stage.js").read_text()
    assert not (ws / "vendor").exists()
    out = _text(res)
    assert '<script src="deck_stage.js"></script>' in out
    assert "customElements.define('deck-stage'" in out


@pytest.mark.asyncio
async def test_a_jsx_starter_brings_vendor_and_the_babel_tags_into_a_subdirectory(ws):
    res = await copy_starter_component.handler({"kind": "ios_frame.jsx", "directory": "frames/"})
    assert not res.get("is_error")
    assert (ws / "frames" / "ios_frame.jsx").is_file()
    for v in VENDOR:
        assert (ws / "frames" / "vendor" / v).is_file(), v
    assert (ws / "frames" / "vendor" / "LICENSE-react.txt").read_text().startswith("MIT License")
    out = _text(res)
    assert '<script src="frames/vendor/babel.min.js"></script>' in out
    assert '<script type="text/babel" src="frames/ios_frame.jsx"></script>' in out
    assert "Object.assign(window, { IosFrame" in out


@pytest.mark.asyncio
async def test_the_kind_must_include_the_extension_and_stay_in_the_workspace(ws):
    res = await copy_starter_component.handler({"kind": "ios_frame"})
    assert res.get("is_error") and "Did you mean 'ios_frame.jsx'" in _text(res)
    res = await copy_starter_component.handler({"kind": "nope.jsx"})
    assert res.get("is_error") and "Unknown starter" in _text(res)
    res = await copy_starter_component.handler({"kind": "deck_stage.js", "directory": "../out"})
    assert res.get("is_error") and "inside the workspace" in _text(res)
    res = await copy_starter_component.handler({"kind": "deck_stage.js", "directory": "/tmp/elsewhere"})
    assert res.get("is_error") and "relative to the workspace" in _text(res)
    assert not (ws / "tmp").exists()
