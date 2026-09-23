"""DREAM-097 -- `visual_check`: a structured "please look" request for a model that cannot see.

A model without image input (MiMo-V2.6 on MachX today) can measure a render with `measure_image`;
when the numbers cannot settle a question it hands the user the picture(s) and one precise question
as a questions_v2 form: the images inline in Studio, radio options or a free answer, and the answer
arrives as the next prompt keyed by id. Without Studio the ask comes back as text for the reply.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from dream.core import policy
from dream.core.backends.base import Event
from dream.core.profiles import guidance, resolve_profile
from dream.core.providers import get_provider
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.native import NATIVE_TOOLS
from dream.tools.visual_check import visual_check

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"
QUESTION = "Is the header text legible?"
OPTIONS = ["Legible", "Clipped on the right", "Not rendered"]


class _StudioUp:
    """A running Studio server, as far as the tool can tell."""


@pytest.fixture
def ws(tmp_path):
    emitted: list = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    set_studio(_StudioUp())
    yield tmp_path, emitted
    set_studio(None)
    tool_context._CTX = None


def _png(folder: Path, name: str, size=(64, 40), colour=(200, 30, 30)) -> Path:
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(p)
    return p


def _text(res) -> str:
    return res["content"][0]["text"]


def _failed(res) -> bool:
    return bool(res.get("is_error"))


def _form(emitted) -> dict:
    (ev,) = emitted
    assert isinstance(ev, Event) and ev.kind == "studio" and ev.data["op"] == "ask"
    return ev.data["form"]


# --- (a) a native, read-only tool that shows a questions_v2 form with the images inline ----------------------


def test_visual_check_is_a_native_tool_and_read_only_in_every_mode():
    assert "visual_check" in {t.name for t in NATIVE_TOOLS}
    assert "visual_check" in policy.builtin_names(), "a custom tool must not be able to claim the name"
    assert policy.capability("visual_check") == policy.READONLY
    assert policy.capability("mcp__dream__visual_check") == policy.READONLY
    for mode in policy.MODES:
        for inp in ({"paths": ["/etc/x.png"], "question": "?"},
                    {"paths": ["shot.png", "/tmp/other.png"], "question": "?", "options": ["a", "b"]}):
            assert policy.decide("visual_check", inp, mode, Path("/tmp/ws-visual"))[0] == "allow", (mode, inp)


def test_the_schema_stays_deferrable_like_measure_image():
    # The local backend pins every tool DEFINED in dream.tools.native; a tool from its own
    # module may defer, and this one's schema must not eat into the pinned budget.
    assert visual_check.handler.__module__ == "dream.tools.visual_check"


async def test_the_form_carries_the_images_the_question_and_radio_options_and_ends_the_turn(ws):
    root, emitted = ws
    _png(root, "shot.png", size=(64, 40))
    _png(root, "sub/diff.png", size=(8, 8))
    res = await visual_check.handler({"paths": ["shot.png", "sub/diff.png"], "question": QUESTION, "options": OPTIONS})
    assert not _failed(res), _text(res)
    text = _text(res)
    assert "END YOUR TURN" in text and "keyed by id (answer)" in text and "shot.png" in text and "diff.png" in text
    form = _form(emitted)
    assert form["title"] == "Please look at shot.png and diff.png"
    assert form["images"] == [{"path": "shot.png", "name": "shot.png", "size": "64x40"},
                              {"path": "sub/diff.png", "name": "diff.png", "size": "8x8"}]
    assert form["questions"] == [{"id": "answer", "kind": "text-options", "title": QUESTION,
                                  "options": OPTIONS, "multi": False}]


async def test_without_options_the_answer_is_free_text(ws):
    root, emitted = ws
    _png(root, "shot.png")
    res = await visual_check.handler({"paths": ["shot.png"], "question": QUESTION})
    assert not _failed(res), _text(res)
    form = _form(emitted)
    assert form["title"] == "Please look at shot.png"
    assert form["questions"] == [{"id": "answer", "kind": "freeform", "title": QUESTION}]


async def test_paths_resolve_like_the_other_file_tools(ws):
    root, emitted = ws
    p = _png(root, "deep/er/shot.png")
    # an absolute path inside the workspace is served relative to it; a lone string is one image
    res = await visual_check.handler({"paths": str(p), "question": QUESTION})
    assert not _failed(res), _text(res)
    assert _form(emitted)["images"] == [{"path": "deep/er/shot.png", "name": "shot.png", "size": "64x40"}]


async def test_three_or_more_images_are_counted_in_the_title(ws):
    root, emitted = ws
    for n in range(3):
        _png(root, f"s{n}.png")
    res = await visual_check.handler({"paths": ["s0.png", "s1.png", "s2.png"], "question": QUESTION})
    assert not _failed(res), _text(res)
    assert _form(emitted)["title"] == "Please look at 3 images"


@pytest.mark.parametrize("args,needle", [
    ({"question": QUESTION}, "paths"),
    ({"paths": [], "question": QUESTION}, "paths"),
    ({"paths": [f"s{n}.png" for n in range(7)], "question": QUESTION}, "6"),
    ({"paths": ["shot.png"]}, "question"),
    ({"paths": ["shot.png"], "question": "   "}, "question"),
    ({"paths": ["shot.png"], "question": QUESTION, "options": "yes/no"}, "options"),
    ({"paths": ["shot.png"], "question": QUESTION, "options": ["ok", ""]}, "options"),
])
async def test_bad_arguments_are_refused_before_anything_is_asked(ws, args, needle):
    root, emitted = ws
    _png(root, "shot.png")
    res = await visual_check.handler(args)
    assert _failed(res) and needle in _text(res), _text(res)
    assert emitted == []


# --- (b) without Studio the ask comes back as text for the reply --------------------------------------------


async def test_without_studio_the_paths_and_the_question_come_back_as_text(ws):
    """The emit hook is always wired (the TUI funnel); Studio is what may be missing (questions_v2, Gate 6)."""
    root, emitted = ws
    set_studio(None)
    p = _png(root, "shot.png")
    res = await visual_check.handler({"paths": ["shot.png"], "question": QUESTION, "options": OPTIONS})
    assert not _failed(res), _text(res)
    text = _text(res)
    assert text.startswith("Studio is not open, so ask this in your reply and end the turn:")
    assert str(p) in text and QUESTION in text and "pick one: " + " / ".join(OPTIONS) in text
    assert emitted == []


async def test_without_studio_and_without_options_the_answer_is_free(ws):
    root, emitted = ws
    set_studio(None)
    _png(root, "shot.png")
    text = _text(await visual_check.handler({"paths": ["shot.png"], "question": QUESTION}))
    assert "free answer" in text and "pick one" not in text and emitted == []


# --- (c) a missing or unreadable image is named; the rest of the form still shows ----------------------------


async def test_a_missing_or_unreadable_image_is_named_and_the_rest_still_shows(ws):
    root, emitted = ws
    _png(root, "good.png")
    (root / "note.png").write_text("not an image at all")
    res = await visual_check.handler({"paths": ["good.png", "nope.png", "note.png"], "question": QUESTION})
    assert not _failed(res), _text(res)
    text = _text(res)
    assert f"Cannot show nope.png: No file at {root / 'nope.png'}" in text
    assert "Cannot show note.png: Could not read note.png as an image" in text
    assert "END YOUR TURN" in text
    form = _form(emitted)
    assert [im["name"] for im in form["images"]] == ["good.png"]
    assert form["title"] == "Please look at good.png"


async def test_an_image_outside_the_workspace_is_named_as_not_showable(ws, tmp_path_factory):
    root, emitted = ws
    _png(root, "good.png")
    outside = _png(tmp_path_factory.mktemp("elsewhere"), "shot.png")
    res = await visual_check.handler({"paths": ["good.png", str(outside)], "question": QUESTION})
    assert not _failed(res), _text(res)
    text = _text(res)
    assert f"Cannot show shot.png: {outside} is outside the workspace" in text
    assert "save_screenshot" in text and "copy_files" in text   # the two ways to bring a capture inside
    assert [im["name"] for im in _form(emitted)["images"]] == ["good.png"]


async def test_when_no_image_can_be_shown_the_call_fails_and_nothing_is_asked(ws):
    root, emitted = ws
    (root / "note.png").write_text("text")
    res = await visual_check.handler({"paths": ["nope.png", "note.png"], "question": QUESTION})
    assert _failed(res)
    text = _text(res)
    assert "no image to show" in text and "nope.png" in text and "note.png" in text
    assert emitted == []


async def test_without_studio_an_image_outside_the_workspace_is_still_named_by_its_path(ws, tmp_path_factory):
    # nothing is served without Studio, so an absolute path outside the workspace is simply the path to name
    root, emitted = ws
    set_studio(None)
    outside = _png(tmp_path_factory.mktemp("elsewhere"), "shot.png")
    text = _text(await visual_check.handler({"paths": [str(outside)], "question": QUESTION}))
    assert str(outside) in text and "Cannot show" not in text and emitted == []


# --- DREAM-099: the DREAM-097 gate's notes -------------------------------------------------------------------


async def test_a_nul_byte_in_a_path_is_named_and_the_rest_still_shows(ws):
    # The gate's note: a NUL byte escaped `_resolve` as an uncaught ValueError (the backend turned it into a crash
    # result); now it is one more image that cannot be shown.
    root, emitted = ws
    _png(root, "good.png")
    res = await visual_check.handler({"paths": ["good.png", "shot\x00.png"], "question": QUESTION})
    assert not _failed(res), _text(res)
    text = _text(res)
    assert "Cannot show 'shot\\x00.png'" in text and "NUL" in text and "END YOUR TURN" in text
    assert [im["name"] for im in _form(emitted)["images"]] == ["good.png"]


async def test_when_every_path_holds_a_nul_byte_the_call_fails_and_nothing_is_asked(ws):
    root, emitted = ws
    res = await visual_check.handler({"paths": ["shot\x00.png"], "question": QUESTION})
    assert _failed(res) and "no image to show" in _text(res) and "NUL" in _text(res)
    assert emitted == []


@pytest.mark.parametrize("question", [5, 1.5, True, ["Is it legible?"], {"text": "?"}])
async def test_a_non_string_question_is_refused_not_coerced(ws, question):
    root, emitted = ws
    _png(root, "shot.png")
    res = await visual_check.handler({"paths": ["shot.png"], "question": question})
    assert _failed(res), _text(res)
    text = _text(res)
    assert "question" in text and "string" in text and type(question).__name__ in text
    assert emitted == []


# --- (d) the no-image-input Runtime note names visual_check after measure_image ------------------------------


def test_the_no_image_input_note_names_visual_check_after_measure_image():
    provider = get_provider("machx")
    profile = resolve_profile(provider, None, model="m")
    off = guidance(replace(provider, multimodal=False), profile, "m")
    on = guidance(replace(provider, multimodal=True), profile, "m")
    assert "Image input is off in this session" in off and "measure_image" in off and "visual_check" in off
    assert off.index("measure_image") < off.index("visual_check")
    assert "when the numbers cannot settle it, ask the user to look" in off
    assert "Image input" not in on and "visual_check" not in on and "measure_image" not in on


# --- (e) Studio shows the card with the image inline and takes a pick ----------------------------------------


def test_the_panel_escapes_image_captions_and_encodes_their_paths():
    ui = UI.read_text()
    rf = ui[ui.index("function renderForm"):ui.index("async function submitForm")]
    assert "form.images" in rf
    assert "esc(String(im.name" in rf and "encodeURIComponent(String(im.path" in rf


def _render(folder: Path) -> Path:
    """A 1280x400 'page header' with a title and a right-hand label that runs off the edge."""
    img = Image.new("RGB", (1280, 400), (246, 243, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1280, 110], fill=(31, 41, 55))
    d.text((40, 36), "DREAM  FIELD NOTES", fill=(255, 255, 255), font_size=40)
    d.text((1100, 42), "Account settings and more", fill=(255, 255, 255), font_size=28)
    d.rectangle([40, 160, 700, 360], outline=(31, 41, 55), width=3)
    d.text((70, 200), "Is the header text legible?", fill=(31, 41, 55), font_size=44)
    p = folder / "header.png"
    img.save(p)
    return p


async def test_studio_shows_the_card_with_the_image_and_takes_a_pick(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright, expect
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    _render(tmp_path)
    prompts: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append,
                       session={"workspace": str(tmp_path), "provider": "test", "model": "fixture"})
    url = await srv.start()
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=srv.bus.publish))
    set_studio(srv)
    try:
        # the image route the card relies on: the workspace file, as an image, behind the token; nothing outside
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as c:
            r = await c.get(f"/api/download?path=header.png&token={srv.token}")
            assert r.status_code == 200 and r.headers["content-type"] == "image/png"
            assert (await c.get(f"/api/download?path=../header.png&token={srv.token}")).status_code == 400
            assert (await c.get("/api/download?path=header.png")).status_code == 401
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 2554, "height": 1338}, reduced_motion="reduce")
            page.set_default_timeout(8000)
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(url + "&companion=1")
            await expect(page.locator("#stat")).to_have_text("Ready")

            res = await visual_check.handler({"paths": ["header.png"], "question": QUESTION, "options": OPTIONS})
            assert not _failed(res), _text(res)

            card = page.locator("form.qform")
            await expect(card).to_be_visible()
            await expect(card.locator(".qtitle")).to_have_text("Please look at header.png")
            img = card.locator(".qimg img")
            await expect(img).to_be_visible()
            await expect(card.locator(".qimg figcaption")).to_have_text("header.png · 1280x400")
            assert await img.evaluate("i => i.complete && i.naturalWidth") == 1280, "the image must have loaded inline"
            await expect(card.locator("legend")).to_have_text(QUESTION)
            await expect(card.get_by_role("radio")).to_have_count(len(OPTIONS))
            await page.get_by_label("Clipped on the right", exact=True).check()
            await page.screenshot(path=str(tmp_path / "visual-check-2554x1338.png"))
            await page.get_by_role("button", name="Send answers").click()
            await expect(page.locator("form.qform")).to_have_count(0)
            assert prompts and prompts[-1] == "Answers to 'Please look at header.png':\n- answer: Clipped on the right"
            assert errors == []
            await browser.close()
    finally:
        set_studio(None)
        tool_context._CTX = None
        await srv.stop()
