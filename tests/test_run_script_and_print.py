"""run_script's sandbox and open_for_print, on the real hidden frame."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from dream import config
from dream.gui import preview as preview_mod
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.export_tools import open_for_print
from dream.tools.studio import run_script, save_screenshot, show_html

STARTER = Path(__file__).parent.parent / "dream" / "gui" / "starters" / "deck_stage.js"


@pytest.fixture
async def ws(tmp_path, monkeypatch):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path / "var" / "shots")
    preview_mod._PREVIEW = None
    yield tmp_path
    if preview_mod._PREVIEW is not None:
        await preview_mod._PREVIEW.aclose()
        preview_mod._PREVIEW = None
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


def _failed(res):
    return bool(res.get("is_error"))


@pytest.mark.asyncio
async def test_run_script_reads_transforms_and_saves_files_and_images(ws):
    (ws / "parts").mkdir()
    (ws / "parts" / "a.txt").write_text("alpha\n")
    (ws / "parts" / "b.txt").write_text("beta\n")
    Image.new("RGB", (20, 10), (0, 0, 255)).save(ws / "photo.png")
    res = await run_script.handler({"code": """
      const files = await ls('parts');
      let combined = '';
      for (const f of files) combined += await readFile('parts/' + f);
      await saveFile('combined.txt', combined.toUpperCase());
      const img = await readImage('photo.png');
      const c = createCanvas(img.width, img.height);
      const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0); ctx.fillStyle = 'red'; ctx.fillRect(0, 0, 10, 10);
      await saveFile('photo-marked.png', c);
      const blob = await readFileBinary('photo.png');
      log('files', files, 'blob', blob.size, 'size', img.width + 'x' + img.height);
    """})
    assert not _failed(res), _text(res)
    assert (ws / "combined.txt").read_text() == "ALPHA\nBETA\n"
    with Image.open(ws / "photo-marked.png") as im:
        assert im.size == (20, 10) and im.getpixel((2, 2))[:3] == (255, 0, 0) and im.getpixel((15, 5))[:3] == (0, 0, 255)
    assert 'files ["a.txt","b.txt"] blob' in _text(res) and "size 20x10" in _text(res)


@pytest.mark.asyncio
async def test_run_script_stays_inside_the_workspace_and_reports_errors(ws):
    (ws.parent / "secret.txt").write_text("nope")
    res = await run_script.handler({"code": "log(await readFile('../secret.txt'))"})
    assert _failed(res) and "outside the workspace" in _text(res)
    res = await run_script.handler({"code": "await saveFile('../escape.txt', 'x')"})
    assert _failed(res) and not (ws.parent / "escape.txt").exists()
    res = await run_script.handler({"code": "await saveFile('x.txt', 42)"})
    assert _failed(res) and "must be a string" in _text(res)
    res = await run_script.handler({"code": "syntax error here"})
    assert _failed(res)
    res = await run_script.handler({"code": "log(await fetch('http://example.com/').then(() => 'ok').catch(() => 'blocked'))"})
    assert not _failed(res) and "blocked" in _text(res)


@pytest.mark.asyncio
async def test_run_script_gets_the_captures_save_screenshot_stashed(ws):
    (ws / "p.html").write_text("<!doctype html><body style='background:#0a0'><h1>hi</h1></body>")
    await show_html.handler({"path": "p.html"})
    assert not _failed(await save_screenshot.handler({"path": "p.html", "steps": [{}, {}], "in_memory_png_key": "shots"}))
    res = await run_script.handler({"code": """
      const caps = await getCaptures('shots');
      log('captures', caps.length, caps[0].type, caps[0].size > 1000);
      await saveFile('first.png', caps[0]);
    """})
    assert not _failed(res), _text(res)
    assert "captures 2 image/png true" in _text(res)
    with Image.open(ws / "first.png") as im:
        assert im.size == (1280, 800)


@pytest.mark.asyncio
async def test_open_for_print_writes_a_pdf_one_page_per_slide(ws):
    shutil.copy(STARTER, ws / "deck_stage.js")
    (ws / "deck.html").write_text("""<!doctype html><html><head><meta charset="utf-8"></head><body>
<deck-stage><section><h1>One</h1></section><section><h1>Two</h1></section><section><h1>Three</h1></section></deck-stage>
<script src="deck_stage.js"></script></body></html>""")
    res = await open_for_print.handler({"project_relative_file_path": "deck.html", "width": 1920, "height": 1080})
    assert not _failed(res), _text(res)
    pdf = ws / "deck.pdf"
    assert pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-"
    import re
    assert len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes())) == 3, "one page per slide"
    (ws / "doc.html").write_text("<!doctype html><h1>Report</h1><p>text</p>")
    res = await open_for_print.handler({"project_relative_file_path": "doc.html", "output_path": "out/report.pdf"})
    assert not _failed(res) and (ws / "out" / "report.pdf").read_bytes()[:5] == b"%PDF-"
    res = await open_for_print.handler({"project_relative_file_path": "doc.html", "output_path": "../x.pdf"})
    assert _failed(res) and "inside the workspace" in _text(res)


@pytest.mark.asyncio
async def test_a_timed_out_script_still_returns_the_log_lines_it_printed(ws, monkeypatch):
    """Gate 7 observation: the timeout threw away everything logged before it."""
    orig = preview_mod.Preview.run_script

    async def fast(self, code, workspace, *, timeout_s=30.0):
        return await orig(self, code, workspace, timeout_s=1)

    monkeypatch.setattr(preview_mod.Preview, "run_script", fast)
    res = await run_script.handler({"code": "log('step 1 ok'); log('step 2 ok'); await new Promise(() => {})"})
    assert _failed(res) and "timed out" in _text(res)
    assert "step 1 ok" in _text(res) and "step 2 ok" in _text(res)
    # the frame is still usable afterwards
    res = await run_script.handler({"code": "log('alive')"})
    assert not _failed(res) and "alive" in _text(res)
