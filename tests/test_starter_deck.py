"""The deck starter, driven in the hidden frame: scaling, navigation, the counter,
persistence, labels, the postMessage contract, and three screenshots at size."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from dream.gui.preview import VIEWPORT, Preview

STARTER = Path(__file__).parent.parent / "dream" / "gui" / "starters" / "deck_stage.js"

DECK = """<!doctype html><html><head><meta charset="utf-8">
<script>window.__msgs = []; window.addEventListener('message', e => window.__msgs.push(e.data));</script>
<style>section{font:64px system-ui;padding:80px}</style></head><body>
<deck-stage>
  <section><h1>Welcome</h1></section>
  <section data-screen-label="02 Agenda"><p>agenda</p></section>
  <section><h2>Thanks</h2></section>
</deck-stage>
<script src="deck_stage.js"></script>
</body></html>"""


@pytest.fixture
async def deck(tmp_path):
    shutil.copy(STARTER, tmp_path / "deck_stage.js")
    (tmp_path / "deck.html").write_text(DECK)
    pv = Preview()
    yield pv, tmp_path / "deck.html"
    await pv.aclose()


@pytest.mark.asyncio
async def test_the_deck_scales_navigates_counts_labels_and_posts(deck):
    pv, path = deck
    assert await pv.load(path) == []
    ev = pv.eval
    # scaled to fit the viewport
    expect = min(VIEWPORT["width"] / 1920, VIEWPORT["height"] / 1080)
    assert abs(await ev("document.querySelector('deck-stage').scale") - expect) < 1e-6
    # first slide active, counter, labels auto-tagged (explicit one kept)
    assert await ev("document.querySelector('deck-stage').index") == 0
    assert await ev("document.querySelector('deck-stage').shadowRoot.getElementById('count').textContent") == "1 / 3"
    labels = await ev("[...document.querySelectorAll('deck-stage > section')].map(s => s.dataset.screenLabel)")
    assert labels == ["01 Welcome", "02 Agenda", "03 Thanks"]
    assert await ev("[...document.querySelectorAll('deck-stage > section')].every(s => s.hasAttribute('data-om-validate'))")
    # keyboard
    await pv._page.keyboard.press("ArrowRight")
    assert await ev("document.querySelector('deck-stage').index") == 1
    assert await ev("document.querySelector('deck-stage > section[data-active] p').textContent") == "agenda"
    await pv._page.keyboard.press("End")
    assert await ev("document.querySelector('deck-stage').index") == 2
    await pv._page.keyboard.press("ArrowLeft")
    assert await ev("document.querySelector('deck-stage').index") == 1
    # the global exporters call, and clamping
    await ev("goToSlide(99)")
    assert await ev("document.querySelector('deck-stage').index") == 2
    # postMessage on init and every change (delivery is async: let the queue drain)
    await pv._page.wait_for_timeout(50)
    assert await ev("window.__msgs.map(m => m.slideIndexChanged)") == [0, 1, 2, 1, 2]
    # persisted: a reload lands on the same slide
    assert await ev("localStorage.getItem('deck-stage:' + location.pathname)") == "2"
    await pv.load(path)
    assert await ev("document.querySelector('deck-stage').index") == 2
    # print rules exist: one page per slide at canvas size
    assert "@page" in await ev("document.getElementById('deck-stage-print').textContent")
    assert "page-break-after" in await ev("document.querySelector('deck-stage').shadowRoot.querySelector('style').textContent")


@pytest.mark.asyncio
async def test_tap_navigation_and_noscale(deck):
    pv, path = deck
    await pv.load(path)
    w = VIEWPORT["width"]
    await pv._page.mouse.click(w * 0.75, 400)   # right half → next
    assert await pv.eval("document.querySelector('deck-stage').index") == 1
    await pv._page.mouse.click(w * 0.25, 400)   # left half → back
    assert await pv.eval("document.querySelector('deck-stage').index") == 0
    await pv.eval("document.querySelector('deck-stage').setAttribute('noscale', '')")
    await pv.eval("window.dispatchEvent(new Event('resize'))")
    assert await pv.eval("getComputedStyle(document.querySelector('deck-stage').shadowRoot.querySelector('.stage')).transform") == "none"


@pytest.mark.asyncio
async def test_three_steps_yield_three_screenshots_at_viewport_size(deck, tmp_path):
    """Phase 5 criterion 7, at the Preview level."""
    pv, path = deck
    await pv.load(path)
    out = await pv.screenshot([{"code": f"goToSlide({i})", "delay": 30} for i in range(3)],
                              save_to=tmp_path / "slides.jpg")
    assert [p.name for p in out] == ["01-slides.jpg", "02-slides.jpg", "03-slides.jpg"]
    for p in out:
        with Image.open(p) as im:
            assert im.size == (VIEWPORT["width"], VIEWPORT["height"])
    assert len({p.read_bytes() for p in out}) == 3, "three different slides"
