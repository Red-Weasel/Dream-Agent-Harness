"""Vital signs in the desktop UI (DREAM-135): a "Vitals" card at the top of the Metrics rail, from recorded
`vitals` events and the stats Dream already has. A monitoring tool only: it never adds to the chat stream,
never moves the prompt box or the plan strip, and stays hidden until the local engine sends a reading.

Only an isolated StudioServer, synthetic events and headless Chromium are used; no engine or model."""
from __future__ import annotations

import time

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_companion import studio  # noqa: F401
from test_plan_strip import CONNECTED, STRIP, _plan, _url

pytestmark = pytest.mark.asyncio

WINDOWS = [{"n": 16, "H_mean": 0.41, "H_max": 2.9, "margin_min": 0.03, "n_hi": 2,
            "draft_offered": 14, "draft_accepted": 9},
           {"n": 8, "n_hi": 0, "draft_offered": 6, "draft_accepted": 6}]      # greedy: the parser left H out
SUMMARY = {"tokens": 24, "H_mean": 0.3, "n_hi": 2, "draft_offered": 20, "draft_accepted": 15,
           "cached_tokens": 38000, "cache_source": "slot 3", "prefill_ms": 1900.5, "restore_ms": 38.0,
           "decode_tps": 16.2}
STATS = {"pp_n": 41000, "pp_ms": 1900.0, "gen_n": 24, "gen_ms": 1500.0, "exact": False,
         "prompt_tokens": 41000, "cached": 38000, "window": 250000}
ROWS = ("certainty", "drafts", "reuse", "thinking", "speed")


async def _open(srv, page, url, layout, width):
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(_url(url, layout))
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    if await page.locator("#rail").evaluate("el => el.classList.contains('hide')"):
        await page.locator("#railtoggle").click()                       # the metrics rail, open
    await expect(page.locator("#railbody")).to_be_visible()


def _turn(srv, windows=WINDOWS, summary=SUMMARY, stats=STATS, thinking="x" * 400):
    srv.bus.publish(Event("user", "Build the page"))
    srv.bus.publish(Event("thinking_delta", thinking))
    for w in windows:
        srv.bus.publish(Event("vitals", {"window": w}))
    srv.bus.publish(Event("text_delta", "Done."))
    if summary is not None:
        srv.bus.publish(Event("vitals", {"summary": summary}))
    srv.bus.publish(Event("stats", stats))
    srv.bus.publish(Event("assistant_done", "Done."))


def _row(page, name):
    return page.locator(f'#vitals .vt-row[data-vital="{name}"]')


@pytest.mark.parametrize("layout,width", [("browser", 1600), ("companion", 480)])
async def test_the_card_shows_the_turn_in_plain_words(studio, layout, width):
    srv, page, url, _, errors = studio
    await _open(srv, page, url, layout, width)
    card = page.locator("#vitals")
    await expect(card).to_be_hidden()
    _turn(srv)
    await expect(card).to_be_visible()
    await expect(card.locator(".rail-h")).to_contain_text("(learning)")
    await expect(_row(page, "certainty").locator("b")).to_have_text("unsure spots: 2")
    await expect(_row(page, "drafts").locator("b")).to_have_text("75% (15/20)")
    await expect(_row(page, "reuse").locator("b")).to_have_text("38k of 41k")
    await expect(_row(page, "thinking").locator("b")).to_have_text("~100 tokens")
    await expect(_row(page, "speed").locator("b")).to_have_text("16.2 tok/s")
    # The card sits above the inference card, and nothing in it is red.
    order = await page.evaluate("[...document.querySelectorAll('#railbody > *')].map(e => e.id)")
    assert order.index("vitals") < order.index("telemetry"), order
    colors = await card.evaluate("""el => [...el.querySelectorAll('*')].map(e => getComputedStyle(e).color)""")
    err = await page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--err').trim()")
    assert err and all(c != "rgb(248, 113, 113)" for c in colors), colors
    assert errors == []


async def test_each_row_explains_itself_on_hover_and_on_focus(studio):
    srv, page, url, _, errors = studio
    await _open(srv, page, url, "browser", 1600)
    _turn(srv)
    await expect(page.locator("#vitals")).to_be_visible()
    for name in ROWS:
        row = _row(page, name)
        tip_id = await row.get_attribute("aria-describedby")
        tip = page.locator(f"#{tip_id}")
        assert await tip.get_attribute("role") == "tooltip"
        text = (await tip.text_content()).strip()
        assert len(text) > 40 and text.count(". ") == 0, text             # one sentence: what, and why
        assert await row.get_attribute("tabindex") == "0"
        await expect(tip).to_be_hidden()
        await row.hover()
        await expect(tip).to_be_visible()
        await page.mouse.move(0, 0)
        await expect(tip).to_be_hidden()
        await row.focus()
        await expect(tip).to_be_visible()
        await row.blur()
    spark = page.locator("#vitals .vt-spark")
    assert "turn" in (await spark.get_attribute("aria-label"))
    assert errors == []


async def test_hidden_without_readings_even_while_other_metrics_arrive(studio):
    """Another provider, or an engine without the field: stats, thinking and tool calls, no `vitals`."""
    srv, page, url, _, errors = studio
    await _open(srv, page, url, "browser", 1600)
    _turn(srv, windows=[], summary=None)
    srv.bus.publish(Event("tool_use", {"id": "t1", "name": "read_file", "input": {"path": "a.txt"}}))
    await expect(page.locator("#stream .tool")).to_have_count(1)
    await expect(page.locator("#vitals")).to_be_hidden()
    assert errors == []


async def test_a_full_re_read_is_named_and_thinking_resets_at_an_action(studio):
    srv, page, url, _, errors = studio
    await _open(srv, page, url, "browser", 1600)
    _turn(srv, stats=dict(STATS, prompt_tokens=90000, pp_n=90000, cached=0),
          summary=dict(SUMMARY, cached_tokens=0, cache_source="none"))
    await expect(_row(page, "reuse").locator("b")).to_have_text("full re-read: 90k tokens")
    await expect(_row(page, "thinking").locator("b")).to_have_text("~100 tokens")
    srv.bus.publish(Event("tool_use", {"id": "t1", "name": "write_file", "input": {"path": "a.txt"}}))
    await expect(_row(page, "thinking").locator("b")).to_have_text("none")
    srv.bus.publish(Event("thinking_delta", "y" * 8000))
    await expect(_row(page, "thinking").locator("b")).to_have_text("~2.0k tokens")
    assert errors == []


async def test_the_history_keeps_the_last_twelve_turns(studio):
    srv, page, url, _, errors = studio
    await _open(srv, page, url, "browser", 1600)
    for i in range(14):
        _turn(srv, windows=[dict(WINDOWS[0], H_mean=0.1 * (i + 1), n_hi=0)])
    spark = page.locator("#vitals .vt-spark")
    await expect(spark.locator("i")).to_have_count(12)
    await expect(_row(page, "certainty").locator("b")).to_have_text("mixed")   # H 1.4 with no unsure spot
    assert errors == []


@pytest.mark.parametrize("layout,width", [("browser", 1200), ("companion", 480)])
async def test_nothing_reaches_the_stream_and_nothing_moves(studio, layout, width):
    srv, page, url, _, errors = studio
    await _open(srv, page, url, layout, width)
    srv.bus.publish(Event("plan", _plan(time.time())))
    await expect(page.locator(STRIP)).to_be_visible()
    srv.bus.publish(Event("user", "Build the page"))
    srv.bus.publish(Event("text_delta", "Working."))
    srv.bus.publish(Event("assistant_done", "Working."))
    srv.bus.publish(Event("stats", STATS))                              # the #perf line fills before, not after
    await expect(page.locator("#stream .msg")).to_have_count(2)
    await expect(page.locator("#perf")).to_contain_text("decode")
    watch = ("footer .composer", "#input", STRIP, "#main", "footer .hint", "#stream")

    async def boxes():
        return [await page.locator(s).first.bounding_box() for s in watch]

    before_html = await page.locator("#stream").inner_html()
    before = await boxes()
    for w in WINDOWS * 3:
        srv.bus.publish(Event("vitals", {"window": w}))
    srv.bus.publish(Event("vitals", {"summary": SUMMARY}))
    await expect(page.locator("#vitals")).to_be_attached()
    await expect(_row(page, "drafts").locator("b")).to_have_text("75% (45/60)")
    assert await page.locator("#stream").inner_html() == before_html     # not one node in the chat
    assert await boxes() == before, (layout, width)
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


# --- gate round 1: greedy decoding claims nothing; junk fed straight to the page never shows as NaN -----------


async def test_a_turn_with_no_entropy_says_so_instead_of_steady(studio):
    """Greedy decoding (temperature 0): every window's H is null, so certainty is unknown, not "steady"."""
    srv, page, url, _, errors = studio
    await _open(srv, page, url, "browser", 1600)
    _turn(srv, windows=[{"n": 16, "n_hi": 0, "draft_offered": 10, "draft_accepted": 7}, WINDOWS[1]])
    await expect(_row(page, "certainty").locator("b")).to_have_text("n/a")
    await expect(_row(page, "drafts").locator("b")).to_have_text("81% (13/16)")
    assert errors == []


async def test_junk_values_never_render_as_nan(studio):
    """The backend drops malformed fields; the page guards again in case an event reaches it another way."""
    srv, page, url, _, errors = studio
    await _open(srv, page, url, "browser", 1600)
    srv.bus.publish(Event("user", "Build the page"))
    srv.bus.publish(Event("vitals", {"window": {"n": "x", "H_mean": "y", "n_hi": "z", "draft_offered": "a",
                                                "draft_accepted": None}}))
    srv.bus.publish(Event("vitals", {"window": {"n": 16, "H_mean": "junk", "n_hi": 0}}))
    srv.bus.publish(Event("vitals", {"summary": {"decode_tps": "fast", "cached_tokens": "many",
                                                 "draft_offered": "x", "draft_accepted": 3}}))
    srv.bus.publish(Event("stats", {"prompt_tokens": "lots", "cached": {}, "gen_n": "a", "gen_ms": "b"}))
    card = page.locator("#vitals")
    await expect(card).to_be_visible()
    await expect(_row(page, "certainty").locator("b")).to_have_text("n/a")
    await expect(_row(page, "thinking").locator("b")).to_have_text("none")
    text = await card.inner_text()
    assert "NaN" not in text and "undefined" not in text and "Infinity" not in text, text
    for name in ("drafts", "reuse", "speed"):
        await expect(_row(page, name)).to_be_hidden()
    assert "NaN" not in (await page.locator("#vitals .vt-spark").get_attribute("aria-label"))
    assert errors == []
