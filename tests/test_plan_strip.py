"""Fix list #88 and #89 (DREAM-119). #88: the plan (update_plan) was a card pinned at the top of the chat
stream; the owner wants it below the chat, left of the prompt box, as a compact strip that expands on click.
#89: the strip said "phase 1 of 6 · 0/15 steps" while the model built phase 3, PLAN.md unwritten for 2.5 hours:
the strip now shows the plan's age and turns stale after 30 minutes, and the progress guard asks the model to
update PLAN.md after PLAN_NUDGE_AFTER tool calls without a write to it (unit tests in test_progress_guard.py).

Only an isolated StudioServer, synthetic events and headless Chromium are used; no engine or model."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from dream.tools.context import ToolContext, set_context
from dream.tools.project import _LAST_PLAN, update_plan
from test_desktop_companion import studio  # noqa: F401
from test_fresh_start import live  # noqa: F401

pytestmark = pytest.mark.asyncio

PHASES = [
    {"name": "Analyze reference", "status": "done", "summary": "the brief is read",
     "steps": [{"name": "read the brief", "status": "done"}, {"name": "list the parts", "status": "done"}]},
    {"name": "Body", "status": "in_progress", "summary": "",
     "steps": [{"name": "block out", "status": "done"}, {"name": "greenhouse", "status": "in_progress"}]},
    {"name": "Cabin", "status": "pending", "summary": "", "steps": [{"name": "seats", "status": "pending"}]},
]
# The live shape (the 01:42 screenshot): six phases, 14 steps, phase 3 under way -- the widest summary in use.
WIDE = [
    {"name": "Analyze reference", "status": "done", "summary": "read", "steps": [{"name": "brief", "status": "done"}, {"name": "parts", "status": "done"}]},
    {"name": "Body", "status": "done", "summary": "blocked out", "steps": [{"name": "block out", "status": "done"}, {"name": "greenhouse", "status": "done"}]},
    {"name": "Cabin", "status": "in_progress", "summary": "", "steps": [{"name": "seats", "status": "done"}, {"name": "dashboard", "status": "in_progress"}, {"name": "roof liner", "status": "pending"}]},
    {"name": "Wheels", "status": "pending", "summary": "", "steps": [{"name": "rims", "status": "pending"}, {"name": "tyres", "status": "pending"}]},
    {"name": "Materials", "status": "pending", "summary": "", "steps": [{"name": "paint", "status": "pending"}, {"name": "chrome", "status": "pending"}]},
    {"name": "Render", "status": "pending", "summary": "", "steps": [{"name": "lights", "status": "pending"}, {"name": "camera", "status": "pending"}, {"name": "final", "status": "pending"}]},
]
STRIP = "footer .composer > #plan-panel"
NOWHERE_ELSE = "#main #plan-panel, #stream #plan-panel, #stream .plan-phase, #stream .plan-step"
CONNECTED = re.compile(r"^(Ready|live)$")                       # the companion's word, the browser's


def _plan(updated_at):
    return {"title": "Model car", "phases": PHASES, "path": "/ws/PLAN.md", "updated_at": updated_at}


def _url(url, layout):
    return url if layout == "companion" else url.replace("&companion=1", "")


# --- #88: a strip left of the prompt box, the phases on click, replayed after a reload -----------------


@pytest.mark.parametrize("layout", ["companion", "browser"])
async def test_the_plan_is_a_strip_left_of_the_prompt_box_not_a_card_in_the_stream(studio, layout):
    srv, page, url, _, errors = studio
    if layout == "browser":                                              # a window, not the 480 px pane
        await page.set_viewport_size({"width": 1200, "height": 800})
    await page.goto(_url(url, layout))
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    strip = page.locator(STRIP)
    await expect(strip).to_be_hidden()                                   # no plan yet
    srv.bus.publish(Event("plan", _plan(time.time())))
    await expect(strip).to_be_visible()
    summary = strip.locator("> summary")
    await expect(summary).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated just now")
    assert await page.locator(NOWHERE_ELSE).count() == 0                # the stream carries no card
    assert await strip.evaluate("el => el.open") is False               # collapsed by default
    await expect(strip.locator(".plan-phase").first).to_be_hidden()
    box, prompt = await strip.bounding_box(), await page.locator("#input").bounding_box()
    chat, composer = await page.locator("#main").bounding_box(), await page.locator("footer .composer").bounding_box()
    assert box["y"] >= chat["y"] + chat["height"] - 1, (box, chat)        # below the chat
    assert composer["x"] <= box["x"] and box["y"] >= composer["y"], (box, composer)   # inside the prompt box's row
    if layout == "browser":
        assert box["x"] + box["width"] <= prompt["x"], (box, prompt)     # left of the prompt box
    else:                                                                # the 480 px pane: its own line just above
        assert box["y"] + box["height"] <= prompt["y"] and box["x"] <= prompt["x"], (box, prompt)
    assert prompt["width"] >= 150, (prompt, composer)                    # the prompt box keeps a usable width
    await summary.click()                                                # expands to the phases
    assert await strip.evaluate("el => el.open") is True
    phases = strip.locator(".plan-phase")
    await expect(phases).to_have_count(3)
    await expect(phases.nth(0)).to_have_class("plan-phase done")
    await expect(phases.nth(1)).to_have_class("plan-phase in_progress")
    await expect(phases.nth(2)).to_have_class("plan-phase pending")
    for name in ("Analyze reference", "Body", "Cabin", "greenhouse"):
        await expect(strip.get_by_text(name, exact=True)).to_be_visible()
    await expect(strip.locator(".plan-step")).to_have_count(2)           # the current phase's steps only
    assert await page.locator(NOWHERE_ELSE).count() == 0
    await page.reload()                                                  # the retained transcript replays it
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    strip = page.locator(STRIP)
    await expect(strip).to_be_visible()
    await expect(strip.locator("> summary")).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated just now")
    assert await strip.evaluate("el => el.open") is False
    assert await page.locator(NOWHERE_ELSE).count() == 0
    assert errors == []


async def test_a_second_plan_event_replaces_the_strip_and_an_empty_one_hides_it(studio):
    srv, page, url, _, errors = studio
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    srv.bus.publish(Event("plan", _plan(time.time())))
    strip = page.locator(STRIP)
    await expect(strip).to_be_visible()
    await strip.locator("> summary").click()
    done = [dict(p, status="done", summary="done", steps=[dict(s, status="done") for s in p["steps"]]) for p in PHASES]
    srv.bus.publish(Event("plan", dict(_plan(time.time()), phases=done)))
    await expect(strip.locator("> summary")).to_have_text("Plan · all 3 phases done · 5/5 steps · updated just now")
    assert await strip.evaluate("el => el.open") is True                # an open strip stays open
    assert await page.locator(STRIP).count() == 1
    srv.bus.publish(Event("plan", {"title": "", "phases": []}))
    await expect(strip).to_be_hidden()
    assert errors == []


# --- #89 (1): the plan's age, stale after 30 minutes ---------------------------------------------------


@pytest.mark.parametrize("layout", ["companion", "browser"])
async def test_the_strip_shows_the_plans_age_and_turns_stale_after_thirty_minutes(studio, layout):
    srv, page, url, _, errors = studio
    t0 = 1_790_000_000                                                   # the page's clock, faked
    await page.clock.install(time=datetime.fromtimestamp(t0, tz=timezone.utc))
    await page.goto(_url(url, layout))
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    strip, summary = page.locator(STRIP), page.locator(STRIP + " > summary")
    srv.bus.publish(Event("plan", _plan(t0)))
    await expect(summary).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated just now")
    await expect(strip).not_to_have_class("stale")
    await page.clock.fast_forward(29 * 60 * 1000)
    await expect(summary).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated 29 min ago")
    await expect(strip).not_to_have_class("stale")
    fresh = await summary.evaluate("el => getComputedStyle(el).color")
    await page.clock.fast_forward(2 * 60 * 1000)
    await expect(summary).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated 31 min ago")
    await expect(strip).to_have_class("stale")
    assert await summary.evaluate("el => getComputedStyle(el).color") != fresh   # a look of its own
    # The live shape: a plan last written 2 h 22 min ago arrives stale (a reconnect replays such an event).
    srv.bus.publish(Event("plan", _plan(t0 + 31 * 60 - (2 * 60 + 22) * 60)))
    await expect(summary).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated 2 h 22 min ago")
    await expect(strip).to_have_class("stale")
    # A fresh write clears it.
    srv.bus.publish(Event("plan", _plan(t0 + 31 * 60)))
    await expect(summary).to_have_text("Plan · phase 2 of 3 · 3/5 steps · updated just now")
    await expect(strip).not_to_have_class("stale")
    assert errors == []


async def test_update_plan_stamps_the_event_with_the_time_of_the_write(tmp_path):
    emitted = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    _LAST_PLAN.clear()
    before = time.time()
    out = await update_plan.handler({"title": "Model car", "phases": PHASES})
    assert not out.get("is_error")
    ev = emitted[-1]
    assert ev.kind == "plan" and ev.data["phases"][1]["name"] == "Body"
    assert before <= ev.data["updated_at"] <= time.time()
    assert abs(ev.data["updated_at"] - (tmp_path / "PLAN.md").stat().st_mtime) < 5


# --- #89 (2): the guard's PLAN.md line reaches the transcript as the guard's own note -------------------


async def test_the_plan_nudge_travels_as_the_guards_note(live):
    """The `live` fixture: a real Engine, the real steering inbox and progress guard, a fake engine whose third
    turn makes 60 read-only calls. With a PLAN.md in the workspace that no call writes, every 12th call adds the
    PLAN.md line to the guard's note; the notes are logged as the guard's (tool_name dream:progress_guard)."""
    (live.workspace / "PLAN.md").write_text("# Plan: Model car\n\n## ◐ 1. Body\n- ○ greenhouse\n")
    for ask in ("Build the launch page", "Check the scene graph", "Fix the landing burn timing"):
        [ev async for ev in live.ask_chat(ask)]
    notes = [t for t in live.store.session_turns(live.session_id, limit=1000)
             if t["role"] == "user" and t["content"].startswith("[Dream progress guard]")]
    assert len(notes) == 9                                               # reads at 12, 18, ... 60 as before
    assert {t["tool_name"] for t in notes} == {"dream:progress_guard"}
    with_plan = [t["content"] for t in notes if "PLAN.md" in t["content"]]
    assert len(with_plan) == 5                                           # calls 12, 24, 36, 48, 60
    assert all(t.count("Update PLAN.md with update_plan") == 1 for t in with_plan)
    assert all("mark finished steps done" in t and "keep each phase's steps list" in t for t in with_plan)
    assert (live.workspace / "PLAN.md").read_text().startswith("# Plan: Model car")   # the guard never writes it


# --- the gate's findings (round 2): a narrow browser window, and the age at the companion's 1000 px breakpoint --


@pytest.mark.parametrize("width,own_line", [(640, True), (480, True), (760, True), (899, True), (900, False)])
async def test_a_narrow_browser_window_keeps_the_prompt_box_usable_idle_and_busy(studio, width, own_line):
    """The gate's probe: beside the strip the textarea had 191 px idle / 109 busy at 640 px and 98 / 16 at 480 px
    (pristine: 283 / 201). Below 900 px the browser view gives the strip its own line above the box, as the 480 px
    pane does (at 45 % the busy textarea reaches 150 px only from ~745 px: 740 -> 147, 760 -> 158, 780 -> 169; and
    from 760 to about 815 px the age was clipped by a few px, fix list #95); from 900 px it stays left of the box. Busy = a
    running turn (Send reads Queue, Steer shows)."""
    srv, page, url, _, errors = studio
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(_url(url, "browser"))
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    srv.bus.publish(Event("plan", _plan(time.time())))
    strip, prompt = page.locator(STRIP), page.locator("#input")
    await expect(strip).to_be_visible()
    for state in ("idle", "busy"):
        if state == "busy":
            srv.bus.publish(Event("turn_start", None))
            await expect(page.locator("#steer")).to_be_visible()
            await expect(page.locator("#send")).to_have_text("Queue")
        box, input_box = await strip.bounding_box(), await prompt.bounding_box()
        assert input_box["width"] >= 150, (width, state, input_box)
        if own_line:
            assert box["y"] + box["height"] <= input_box["y"], (width, state, box, input_box)   # its own line above
        else:
            assert box["x"] + box["width"] <= input_box["x"], (width, state, box, input_box)    # left of the box
    srv.bus.publish(Event("turn_end", {}))
    await expect(page.locator("#steer")).to_be_hidden()
    assert errors == []


async def test_the_age_is_whole_at_the_companions_side_by_side_breakpoint(studio):
    """At exactly 1000 px the companion puts the strip left of the box; the gate's probe found a "2 h 22 min ago"
    summary needing 340 px with 337 available, so the age's last pixels were cut."""
    srv, page, url, _, errors = studio
    await page.set_viewport_size({"width": 1000, "height": 800})
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    srv.bus.publish(Event("plan", dict(_plan(time.time() - (142 * 60 + 30)), phases=WIDE)))
    strip, summary = page.locator(STRIP), page.locator(STRIP + " > summary")
    await expect(summary).to_have_text("Plan · phase 3 of 6 · 5/14 steps · updated 2 h 22 min ago")
    box, prompt = await strip.bounding_box(), await page.locator("#input").bounding_box()
    assert box["x"] + box["width"] <= prompt["x"], (box, prompt)         # left of the box from 1000 px
    assert prompt["width"] >= 150, prompt
    widths = await summary.evaluate("el => [el.scrollWidth, el.clientWidth]")
    assert widths[0] <= widths[1], widths                                 # nothing hidden behind an ellipsis
    age, head = await strip.locator(".age").bounding_box(), await summary.bounding_box()
    assert age["x"] + age["width"] <= head["x"] + head["width"] + 0.5, (age, head)
    assert errors == []


@pytest.mark.parametrize("width", [760, 790, 810, 899, 900])
async def test_the_age_is_whole_in_a_browser_window(studio, width):
    """Fix list #95: from 760 to about 815 px the browser view put the strip left of the box and the age lost its
    last pixels (the 760, 790 and 810 cases fail on the old 759 px rule); below 900 px the strip now takes its own line,
    and from 900 px it fits beside the box with margin."""
    srv, page, url, _, errors = studio
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(_url(url, "browser"))
    await expect(page.locator("#stat")).to_have_text(CONNECTED)
    srv.bus.publish(Event("plan", dict(_plan(time.time() - (142 * 60 + 30)), phases=WIDE)))
    strip, summary = page.locator(STRIP), page.locator(STRIP + " > summary")
    await expect(summary).to_have_text("Plan · phase 3 of 6 · 5/14 steps · updated 2 h 22 min ago")
    widths = await summary.evaluate("el => [el.scrollWidth, el.clientWidth]")
    assert widths[0] <= widths[1], (width, widths)                        # nothing hidden behind an ellipsis
    age, head = await strip.locator(".age").bounding_box(), await summary.bounding_box()
    assert age["x"] + age["width"] <= head["x"] + head["width"] + 0.5, (width, age, head)
    assert errors == []
