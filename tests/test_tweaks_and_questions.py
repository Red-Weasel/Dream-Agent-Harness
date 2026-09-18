"""Phase 6 pieces with no panel dependency: the tweak merge and the question form."""

from __future__ import annotations

import pytest

from dream.core.backends.base import Event
from dream.gui import tweaks
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.questions import answers_as_prompt, as_text, questions_v2, validate

PAGE = """<!doctype html><script>
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "primaryColor": "#D97757",
  "fontSize": 16,
  "dark": false
}/*EDITMODE-END*/;
document.body.style.fontSize = TWEAK_DEFAULTS.fontSize + 'px';
</script><p>hi</p>"""


def test_apply_merges_only_the_given_keys_and_leaves_the_rest_of_the_file_alone():
    new, merged = tweaks.apply(PAGE, {"fontSize": 18, "accent": "#0af"})
    assert merged == {"primaryColor": "#D97757", "fontSize": 18, "dark": False, "accent": "#0af"}
    head, tail = PAGE.split("/*EDITMODE-BEGIN*/")[0], PAGE.split("/*EDITMODE-END*/")[1]
    assert new.startswith(head) and new.endswith(tail)
    assert tweaks.read_block(new) == merged
    again, _ = tweaks.apply(new, {"dark": True})
    assert tweaks.read_block(again)["dark"] is True


def test_apply_refuses_bad_blocks_before_writing(tmp_path):
    with pytest.raises(tweaks.TweakError, match="exactly one"):
        tweaks.apply("<p>no block</p>", {"a": 1})
    two = PAGE + "/*EDITMODE-BEGIN*/{}/*EDITMODE-END*/"
    with pytest.raises(tweaks.TweakError, match="found 2"):
        tweaks.apply(two, {"a": 1})
    bad = PAGE.replace('"fontSize": 16', "fontSize: 16")
    with pytest.raises(tweaks.TweakError, match="not valid JSON"):
        tweaks.apply(bad, {"a": 1})
    with pytest.raises(tweaks.TweakError, match="object"):
        tweaks.apply(PAGE, ["not", "a", "dict"])  # type: ignore[arg-type]
    f = tmp_path / "p.html"
    f.write_text(bad)
    with pytest.raises(tweaks.TweakError):
        tweaks.apply_to_file(f, {"a": 1})
    assert f.read_text() == bad, "a refused merge writes nothing"


def test_apply_to_file_round_trips(tmp_path):
    f = tmp_path / "p.html"
    f.write_text(PAGE)
    assert tweaks.apply_to_file(f, {"fontSize": 20})["fontSize"] == 20
    assert tweaks.read_block(f.read_text())["fontSize"] == 20
    before = f.read_text()
    tweaks.apply_to_file(f, {})
    assert f.read_text() == before


# --- questions_v2 -------------------------------------------------------------------------


class _StudioUp:
    async def ask_frame(self, *a, **k):  # pragma: no cover - never called here
        raise AssertionError


@pytest.fixture
def ws(tmp_path):
    emitted: list = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    set_studio(_StudioUp())  # the server is running: forms can show
    yield emitted
    set_studio(None)
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


FORM = {"title": "Quick questions about the deck", "questions": [
    {"id": "audience", "kind": "text-options", "title": "Who is it for?", "options": ["Execs", "Engineers"]},
    {"id": "length", "kind": "slider", "title": "How many slides?", "min": 5, "max": 40, "step": 1, "default": 12},
    {"id": "logo", "kind": "file", "title": "Logo", "accept": "image/*"},
    {"id": "vibe", "kind": "svg-options", "title": "Tone", "options": ["<svg viewBox='0 0 80 56'/>", "<svg/>"], "multi": True},
    {"id": "notes", "kind": "freeform", "title": "Anything else?", "subtitle": "optional"},
]}


def test_validate_normalizes_and_adds_the_standard_options():
    form, why = validate(FORM)
    assert why is None
    q = {x["id"]: x for x in form["questions"]}
    assert q["audience"]["options"] == ["Execs", "Engineers", "Explore a few options", "Decide for me", "Other"]
    assert q["length"] == {"id": "length", "kind": "slider", "title": "How many slides?",
                           "min": 5.0, "max": 40.0, "step": 1.0, "default": 12.0}
    assert q["vibe"]["multi"] is True and q["logo"]["accept"] == "image/*"
    assert q["notes"]["subtitle"] == "optional"


@pytest.mark.parametrize("broken, msg", [
    ({"title": "", "questions": [{"id": "a", "kind": "freeform", "title": "t"}]}, "needs a title"),
    ({"title": "t", "questions": []}, "non-empty"),
    ({"title": "t", "questions": [{"id": "bad id", "kind": "freeform", "title": "t"}]}, "snake_case"),
    ({"title": "t", "questions": [{"id": "a", "kind": "freeform", "title": "t"}, {"id": "a", "kind": "freeform", "title": "t"}]}, "duplicate"),
    ({"title": "t", "questions": [{"id": "a", "kind": "wheel", "title": "t"}]}, "kind must be"),
    ({"title": "t", "questions": [{"id": "a", "kind": "text-options", "title": "t", "options": []}]}, "non-empty list"),
    ({"title": "t", "questions": [{"id": "a", "kind": "slider", "title": "t", "min": 5, "max": 1}]}, "min < max"),
    ({"title": "t", "questions": [{"id": "a", "kind": "slider", "title": "t", "min": "x"}]}, "must be numbers"),
])
def test_validate_names_what_is_wrong(broken, msg):
    form, why = validate(broken)
    assert form is None and msg in why


@pytest.mark.asyncio
async def test_the_tool_shows_the_form_in_studio_and_tells_the_model_to_end_the_turn(ws):
    emitted = ws
    res = await questions_v2.handler(FORM)
    assert not res.get("is_error") and "END YOUR TURN" in _text(res) and "audience" in _text(res)
    (ev,) = emitted
    assert isinstance(ev, Event) and ev.kind == "studio" and ev.data["op"] == "ask"
    assert ev.data["form"]["title"] == FORM["title"]
    bad = await questions_v2.handler({"title": "t", "questions": []})
    assert bad.get("is_error") and len(emitted) == 1


@pytest.mark.asyncio
async def test_without_studio_the_questions_come_back_as_text(ws):
    """Gate 6 finding 1: the emit hook is ALWAYS wired in the TUI; what may be
    missing is the Studio server. No server → text, and no event into the void."""
    emitted = ws
    set_studio(None)
    res = await questions_v2.handler(FORM)
    assert emitted == []
    out = _text(res)
    assert "Studio is not open" in out and "## Quick questions" in out
    assert "pick one: Execs / Engineers / Explore a few options / Decide for me / Other" in out
    assert "number from 5 to 40 (default 12)" in out


def test_answers_become_a_prompt_keyed_by_id():
    txt = answers_as_prompt("Quick questions", {"audience": "Execs", "vibe": ["a", "b"], "length": 12})
    assert txt.splitlines() == ["Answers to 'Quick questions':", "- audience: Execs", "- vibe: a, b", "- length: 12"]
    assert as_text(validate(FORM)[0]).startswith("## Quick questions")


def test_tweaks_keep_crlf_line_endings(tmp_path):
    f = tmp_path / "p.html"
    f.write_bytes(PAGE.replace("\n", "\r\n").encode())
    tweaks.apply_to_file(f, {"fontSize": 20})
    raw = f.read_bytes()
    assert b"\r\n" in raw and b"\n\n" not in raw.replace(b"\r\n", b"") and tweaks.read_block(raw.decode())["fontSize"] == 20


def test_validate_refuses_infinite_sliders_and_non_string_accept():
    form, why = validate({"title": "t", "questions": [{"id": "a", "kind": "slider", "title": "t", "min": 0, "max": 1e999}]})
    assert form is None and "finite" in why
    form, why = validate({"title": "t", "questions": [{"id": "a", "kind": "file", "title": "t", "accept": ["image/*"]}]})
    assert form is None and "accept must be a string" in why
