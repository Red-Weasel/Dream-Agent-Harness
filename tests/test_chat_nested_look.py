"""The desktop page in the Nested view's look. DREAM-240 (the owner, 2026-10-04, screenshots of Chat, Artifacts and
Nested: [feedback]) and DREAM-242 (the same day: [feedback]; [feedback]). Pinned here: nothing above the work area in any view, what the
hidden bars held under the composer (and floating where a view has none), Nested's palette (one set of values in two
files) on the whole page, the conversation's rows, the chat as a resizable column beside an open preview, and the
Nested view's live tool-call text. Real Chromium; no model, no engine."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.async_api import expect

from dream.core.backends.base import Event
from test_chat_artifact_panel import COFFEE, build  # noqa: F401
from test_desktop_chat import chat  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "dream" / "gui" / "static"
ROSE, CARD = "rgb(255, 122, 168)", "rgb(21, 19, 24)"          # --nd-rose, --nd-card

BOXES = """ids => Object.fromEntries(ids.map(s => { const e = document.querySelector(s); if(!e) return [s, null];
  const r = e.getBoundingClientRect();
  return [s, {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom, shown: getComputedStyle(e).display !== 'none' && r.width > 0}]; }))"""


def test_the_palette_is_the_nested_views_own_values():
    nested = re.search(r"#dream-nested-page\{(.*?)\n\}", (STATIC / "nested.css").read_text(), re.S).group(1)
    mine = re.search(r"\nhtml\.dream-design\.companion\{(.*?)\n\}", (STATIC / "chat-nested.css").read_text(), re.S).group(1)
    nd, cn = (dict(re.findall(r"--(nd-[\w-]+):([^;]+);", block)) for block in (nested, mine))
    assert len(cn) >= 16, cn
    assert {name: nd.get(name) for name in cn} == cn             # a colour changed in one file and not the other fails here


async def test_no_view_has_bars_above_the_work_area_and_the_controls_sit_under_the_composer(chat):
    srv, page, _, _ = chat
    composer = {"home", "memory", "files", "optimizer", "studio", "chat"}          # the views that show the composer
    for view in ("home", "projects", "skills", "memory", "files", "sleepwalk", "optimizer", "studio", "chat"):
        await page.evaluate("v => document.getElementById('dream-nav-' + v).click()", view)
        await expect(page.locator("html")).to_have_attribute("data-dream-view", view)
        box = await page.evaluate(BOXES, ["body>header", "#dream-workbar", ".split", "#chat-menu", "#cn-stat", "#cn-float"])
        assert not box["body>header"]["shown"] and not box["#dream-workbar"]["shown"], (view, box)
        assert box[".split"]["y"] <= 16, (view, box[".split"])                       # the work area starts at the window's top
        assert box["#chat-menu"]["shown"] == (view in composer) and box["#cn-stat"]["shown"] == (view in composer), (view, box)
        assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(11, 10, 14)", view   # --nd-bg, every view
    # A view without the composer: a recording in progress still shows, floating at the work area's lower right.
    await page.evaluate("document.getElementById('dream-nav-projects').click()")
    floating = page.locator("#cn-recording-float")
    await expect(floating).to_be_hidden()
    await page.evaluate("(() => { const r = document.getElementById('dream-recording'); r.textContent = 'Recording'; r.hidden = false; })()")
    await expect(floating).to_be_visible()
    await expect(floating).to_have_text("Recording")
    box = await page.evaluate(BOXES, ["#cn-recording-float", ".split"])
    assert box[".split"]["r"] - box["#cn-recording-float"]["r"] <= 24 and box[".split"]["b"] - box["#cn-recording-float"]["b"] <= 24, box
    await page.evaluate("document.getElementById('dream-recording').hidden = true")
    await expect(floating).to_be_hidden()
    await page.evaluate("document.getElementById('dream-nav-chat').click()")
    await expect(page.locator("#cn-recording-float")).to_be_hidden()                  # in Chat the row under the composer says it
    # The work bar no longer has the session's model (DREAM-251..285), so there is no model chip here.
    await expect(page.locator("#cn-model")).to_have_count(0)

    # The More menu: one row per control of the hidden bars; a row clicks the page's own control.
    menu = page.locator("#chat-menu")
    await menu.locator("summary").click()
    rows = await menu.locator("[data-proxy]:visible").all_text_contents()
    assert rows[:2] == ["New chat", "Controls"] and {"Context", "Design", "Checkpoints", "Metrics"} <= set(rows), rows
    await menu.get_by_role("menuitem", name="Controls").click()
    await expect(page.locator("#dream-controls")).to_have_attribute("open", "")
    assert await menu.evaluate("m => m.open") is False
    await page.keyboard.press("Escape")
    await expect(menu.locator("summary")).to_be_focused()                         # the dialog gives the focus back to the menu
    await menu.locator("summary").click()
    await page.keyboard.press("Escape")
    assert await menu.evaluate("m => m.open") is False
    await menu.locator("summary").click()
    await menu.get_by_role("menuitem", name="Metrics").click()
    await expect(page.locator("#rail")).not_to_have_class(re.compile(r"\bhide\b"))
    rail = (await page.evaluate(BOXES, ["#rail", ".split"]))
    assert rail["#rail"]["w"] <= 560 and abs(rail["#rail"]["r"] - rail[".split"]["r"]) <= 2, rail   # a drawer at the right
    await page.evaluate("document.getElementById('railtoggle').click()")

    # The chips mirror the bars' own controls: the text is theirs, a click is a click on them.
    await page.evaluate("document.getElementById('dream-effort').textContent = 'Effort · high'")
    await expect(page.locator("#cn-effort")).to_have_text("Effort · high")
    await page.evaluate("document.getElementById('dream-effort').textContent = 'Effort · unreported'")
    await expect(page.locator("#cn-effort")).to_be_hidden()
    net = page.locator("#cn-net")
    await expect(net).to_have_attribute("data-state", "on")
    await net.click()
    await expect(page.locator("#netpill")).to_have_class(re.compile(r"\boff\b"))
    await expect(net).to_have_attribute("data-state", "off")
    await net.click()
    await expect(net).to_have_attribute("data-state", "on")
    await expect(page.locator("#cn-stat")).to_have_text("Ready")
    # A phone-width window: the row wraps, nothing pushes the page sideways.
    await page.set_viewport_size({"width": 390, "height": 800})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")


async def test_the_conversation_reads_as_the_nested_views_does(chat):
    srv, page, prompts, _ = chat
    await expect(page.get_by_label("Feed detail")).to_have_value("compact")       # the desktop starts with one line per item
    await page.locator("#input").fill("open the rocket animation")
    await page.locator("#send").click()
    srv.bus.publish(Event("thinking_delta", "The first thought. " + "filler words " * 120 + "and the newest words"))
    srv.bus.publish(Event("tool_use", {"id": "t1", "name": "read_file", "input": {"path": "notes/plan.md", "limit": 40}}))
    srv.bus.publish(Event("tool_result", {"id": "t1", "content": "Line one\nLine two", "is_error": False}))
    srv.bus.publish(Event("tool_use", {"id": "t2", "name": "run_bash", "input": {"command": "ls -la"}}))
    srv.bus.publish(Event("tool_result", {"id": "t2", "content": "boom", "is_error": True}))
    srv.bus.publish(Event("text_delta", "**Done.** It is open."))
    srv.bus.publish(Event("assistant_done", {}))
    srv.bus.publish(Event("result", {}))
    await expect(page.locator("#stream .msg.dream .body")).to_contain_text("It is open.")
    look = await page.evaluate("""() => { const q = s => document.querySelector(s), cs = e => getComputedStyle(e), r = e => e.getBoundingClientRect();
      const you = q('#stream .msg.you'), dream = q('#stream .msg.dream'), think = q('#stream > .think'), tail = think.querySelector('.think-tail');
      return {main: cs(q('#main')).backgroundColor, youWho: [you.querySelector('.who').textContent, r(you.querySelector('.who')).height > 0],
              youBubble: cs(you.querySelector('.body')).backgroundColor, youLeft: Math.round(r(you.querySelector('.body')).x - r(you.querySelector('.who')).x),
              dreamWho: [dream.querySelector('.who').textContent, r(dream.querySelector('.who')).height > 0], size: cs(dream.querySelector('.body')).fontSize,
              bold: dream.querySelector('.body strong')?.textContent,
              think: {open: think.open, edge: cs(think).borderLeftColor, label: cs(think.querySelector('summary')).color, h: Math.round(r(think).height),
                      tail: tail.textContent, tailShown: r(tail).width > 200, src: cs(think.querySelector('.think-src')).display, live: cs(think.querySelector('.think-live')).display,
                      cut: tail.scrollWidth > tail.clientWidth}}; }""")
    assert look["main"] == CARD, look
    assert look["youWho"] == ["You", True] and look["dreamWho"] == ["Dream", True], look        # the names are shown
    assert look["youBubble"] == "rgba(0, 0, 0, 0)" and look["youLeft"] == 0, look               # no bubble: the text under its name
    assert look["size"] == "14px" and look["bold"] == "Done.", look                             # markdown still renders
    think = look["think"]
    assert not think["open"] and think["edge"] == ROSE and think["label"] == ROSE and think["h"] <= 34, think
    assert think["tail"].endswith("and the newest words") and think["tailShown"] and think["cut"], think   # the END of the text, across the row
    assert think["src"] == "none" and think["live"] == "none", think
    await page.locator("#stream > .think summary").click()
    await expect(page.locator("#stream > .think > div")).to_be_visible()
    await expect(page.locator("#stream > .think .think-tail")).to_be_hidden()

    read, bash = page.locator("#stream > .tool").nth(0), page.locator("#stream > .tool").nth(1)
    await expect(read.locator(".targ")).to_have_text("notes/plan.md")             # the one thing the call touched
    await expect(read).to_have_attribute("data-kind", "read")
    await expect(read.locator(".badge")).to_have_text("Completed")
    assert await read.evaluate("t => !t.open && t.getBoundingClientRect().height <= 28 && getComputedStyle(t).borderTopWidth === '0px'")
    await read.locator("summary").click()
    await expect(read.locator(".feed-preview")).to_be_visible()                   # opened, it shows its result's first lines
    await expect(read.locator(".feed-preview")).to_contain_text("Line one")
    await expect(bash).to_have_attribute("data-kind", "run")
    await expect(bash.locator(".targ")).to_have_text("ls -la")
    await expect(bash).to_have_attribute("open", "")                             # a failed call opens by itself
    await expect(bash.locator(".badge")).to_have_text("Failed")
    assert await bash.locator(".badge").evaluate("b => getComputedStyle(b).color") == "rgb(255, 85, 102)"


async def test_beside_a_preview_the_chat_is_a_column_with_a_resize_edge(build):
    scripted, page, prompts, errors = build
    await scripted.turn("Build me a coffee shop page", [("write_file", {"path": "index.html", "content": COFFEE})])
    await expect(page.locator("body")).to_have_class(re.compile(r"\bcn-column\b"))
    await page.wait_for_timeout(400)                                              # the panel's slide has ended
    ids = ["#main", "#artpanel", "body>footer", ".split", "#chat-split", ".composer"]

    async def boxes():
        await page.evaluate("new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
        return await page.evaluate(BOXES, ids)

    box = await boxes()
    main, panel, foot, split, edge = (box[i] for i in ids[:5])
    assert abs(foot["r"] - main["r"]) <= 1, box                                   # the composer is in the chat's column
    assert box[".composer"]["r"] <= main["r"], box
    assert abs(panel["b"] - foot["b"]) <= 1 and abs(panel["y"] - main["y"]) <= 1, box   # the preview runs from the top to the bottom
    assert main["w"] >= 360 and panel["w"] >= 388, box
    assert edge["shown"] and edge["w"] == 8 and abs(edge["x"] - main["r"]) <= 2 and abs(edge["h"] - panel["h"]) <= 2, box
    handle = page.locator("#chat-split")
    assert await handle.get_attribute("role") == "separator" and await handle.get_attribute("aria-orientation") == "vertical"

    async def drag(to_x):
        b = (await boxes())["#chat-split"]
        await page.mouse.move(b["x"] + 4, 300)
        await page.mouse.down()
        await page.mouse.move(to_x, 320, steps=6)
        await page.mouse.up()
        return await boxes()

    box = await drag(main["x"] + 520)
    assert abs(box["#main"]["w"] - 520) <= 2, box
    assert abs(box["body>footer"]["r"] - box["#main"]["r"]) <= 1, box             # the composer follows the edge
    assert await page.evaluate("localStorage.getItem('dream.chat.width')") == str(round(box["#main"]["w"]))
    box = await drag(0)                                                           # bounded: the chat keeps 360 px ...
    assert box["#main"]["w"] == 360, box
    box = await drag(split["r"])                                                  # ... and the preview 388
    assert abs(box["#artpanel"]["w"] - 388) <= 2 and box["#main"]["w"] > 520, box
    widest = box["#main"]["w"]
    await handle.focus()
    await page.keyboard.press("ArrowLeft")
    box = await boxes()
    assert abs(box["#main"]["w"] - (widest - 16)) <= 1, box
    assert int(await handle.get_attribute("aria-valuenow")) == round(box["#main"]["w"] / box[".split"]["w"] * 100)
    kept = await page.evaluate("localStorage.getItem('dream.chat.width')")
    assert kept == str(round(box["#main"]["w"]))
    await page.reload()                                                           # the width is remembered in this browser
    await expect(page.locator("#stat")).to_have_text("Ready")
    assert await page.evaluate("document.documentElement.style.getPropertyValue('--dream-chat-share')") == kept + "px"
    await page.locator("#chat-split").dispatch_event("dblclick")                  # a double click gives the default back
    assert await page.evaluate("[localStorage.getItem('dream.chat.width'), document.documentElement.style.getPropertyValue('--dream-chat-share')]") == [None, ""]

    # Under 1000 px the preview is a sheet over the chat: no column, no edge, nothing pushed sideways.
    await page.set_viewport_size({"width": 900, "height": 800})
    await expect(page.locator("body")).not_to_have_class(re.compile(r"\bcn-column\b"))
    box = await boxes()
    assert not box["#chat-split"]["shown"], box
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert errors == []


async def test_the_nested_view_keeps_the_recording_indicator_and_its_drawer_gives_the_focus_back(chat):
    # The Nested view hides the same bars (DREAM-239): the recording indicator is a chip in its footer, and a drawer
    # opened from its More menu returns the focus to that menu.
    srv, page, _, _ = chat
    await page.locator("#dream-nav-nested").click()
    await expect(page.locator("#dream-nested-page")).to_be_visible()
    await expect(page.locator("#nd-recording")).to_be_hidden()
    await page.evaluate("(() => { const r = document.getElementById('dream-recording'); r.textContent = 'Recording'; r.hidden = false; })()")
    await expect(page.locator("#nd-recording")).to_be_visible()
    await expect(page.locator("#nd-recording")).to_have_text("Recording")
    await page.evaluate("document.getElementById('dream-recording').hidden = true")
    await expect(page.locator("#nd-recording")).to_be_hidden()
    for _ in range(2):                                                            # Metrics opens the drawer, Metrics again shuts it
        await page.locator("#nd-menu > summary").click()
        await page.locator('#nd-menu [data-proxy="railtoggle"]').click()
    await expect(page.locator("#rail")).to_be_hidden()
    await expect(page.locator("#nd-menu > summary")).to_be_focused()


async def test_the_nested_view_shows_a_tool_call_while_the_model_writes_it(chat):
    # The owner, 2026-10-04: [feedback] / [feedback] -- MachX streams a tool call's text (tool_preview); the
    # chat shows it as "Writing", and the Nested column ignored it, so a long write looked like an idle column.
    srv, page, _, _ = chat
    await page.locator("#dream-nav-nested").click()
    await srv._accept_prompt("write the sea module")
    srv.bus.publish(Event("turn_start", {}))
    srv.bus.publish(Event("text_delta", "Writing the new sea module now."))
    for piece in ("<tool_call><function=write_file><parameter=path>falcon9/sea.js</parameter>",
                  "<parameter=content>" + "const wave = 1;\n" * 300, "export const LAST_LINE = 42;"):
        srv.bus.publish(Event("tool_preview", piece))
    block = page.locator("#nd-convo details.nd-writing")
    await expect(block).to_be_visible()
    await expect(block.locator(".nd-think-label")).to_have_text("Writing")
    await expect(block.locator(".nd-tail")).to_contain_text("export const LAST_LINE = 42;")      # the END of the text
    look = await block.evaluate("d => { const t = d.querySelector('.nd-tail'); return [d.open, d.getBoundingClientRect().height <= 40, t.scrollWidth > t.clientWidth]; }")
    assert look == [False, True, True], look                                      # one line, cut at its older end
    await block.locator("summary").click()
    await expect(block.locator("pre")).to_contain_text("LAST_LINE = 42;")
    srv.bus.publish(Event("tool_preview", " // one more line"))
    await expect(block.locator("pre")).to_contain_text("// one more line")        # it stays open while the text arrives
    assert await block.evaluate("d => d.open")
    srv.bus.publish(Event("tool_use", {"id": "w1", "name": "write_file", "input": {"path": "falcon9/sea.js", "content": "x"}}))
    await expect(block).to_have_count(0)                                          # the call's own row replaces it
    await expect(page.locator("#nd-convo .nd-tool-msg")).to_contain_text("falcon9/sea.js")
    srv.bus.publish(Event("tool_preview", "<tool_call><function=run_bash>"))
    await expect(block).to_be_visible()
    srv.bus.publish(Event("turn_end", {"interrupted": True}))
    await expect(block).to_have_count(0)                                          # a stopped turn leaves no "Writing"
    await page.reload()                                                           # the replayed history carries no live text
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.locator("#dream-nav-nested").click()
    await expect(page.locator("#nd-convo .nd-tool-msg")).to_contain_text("falcon9/sea.js")
    await expect(page.locator("#nd-convo details.nd-writing")).to_have_count(0)
