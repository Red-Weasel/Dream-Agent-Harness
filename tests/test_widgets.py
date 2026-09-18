"""Phase 8 tools: every card validates, renders as text, and reaches Studio only
when a server is up; a malformed payload is refused, never rendered."""

from __future__ import annotations

import pytest

from dream.core.backends.base import Event
from dream.tools import context as tool_context, widgets
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.widgets import (
    WIDGET_TOOLS, _check, ask_user_input, chart_display_v0, comparison_card_display_v0,
    end_conversation, featured_card_display_v0, image_search, itinerary_display_v0,
    link_preview_display_v0, options_card_display_v0, places_list_display_v0,
    product_carousel_display_v0, quiz_display_v0, step_card_display_v0, suggest_research,
    translation_display_v0, visualize_read_me, visualize_show_widget,
)


@pytest.fixture
def ws(tmp_path):
    emitted: list = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    set_studio(object())
    widgets.reset_end()
    yield tmp_path, emitted
    set_studio(None)
    widgets.reset_end()
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


def _failed(res):
    return bool(res.get("is_error"))


# --- the validator -----------------------------------------------------------------------


def test_the_validator_names_what_is_wrong():
    sch = {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "format": "url"}},
        "n": {"type": "integer", "minimum": 3, "maximum": 5}, "e": {"type": "string", "enum": ["x", "y"]}}}
    assert _check({"a": ["https://a.b"]}, sch) is None
    assert "a is required" in _check({}, sch)
    assert "at least 1" in _check({"a": []}, sch)
    assert "at most 2" in _check({"a": ["https://a", "https://b", "https://c"]}, sch)
    assert "http(s) URL" in _check({"a": ["ftp://x"]}, sch)
    assert "integer" in _check({"a": ["https://a"], "n": 3.5}, sch)
    assert "at least 3" in _check({"a": ["https://a"], "n": 2}, sch)
    assert "one of x, y" in _check({"a": ["https://a"], "e": "z"}, sch)
    assert "must be a number" in _check({"a": ["https://a"], "n": True}, sch)


# --- the cards ------------------------------------------------------------------------------

CARDS = [
    (chart_display_v0, {"style": "bar", "title": "Sales", "series": [{"name": "Q", "values": [1, 2, 3]}],
                        "x_axis": {"categories": ["Jan", "Feb", "Mar"]}}, "[bar chart] Sales"),
    (comparison_card_display_v0, {"products": [
        {"name": "A", "price": "$1", "attributes": [{"label": "Display", "value": "11"}, {"label": "Battery", "value": "10h"}]},
        {"name": "B", "attributes": [{"label": "Display", "value": "13"}, {"label": "Battery", "value": "12h"}]}],
        "summary": "Two tablets compared"}, "[comparison]"),
    (featured_card_display_v0, {"products": [{"name": "Best", "price": "$549", "blurb": "why", "url": "https://x.y/p"}],
                                "summary": "One pick"}, "[featured pick]"),
    (product_carousel_display_v0, {"products": [{"name": "P1"}, {"name": "P2", "blurb": "b"}], "summary": "Two products"}, "[products]"),
    (itinerary_display_v0, {"title": "2 days", "days": [{"day_label": "Day 1", "stops": [{"name": "Temple", "time": "9:00"}]}],
                            "summary": "A day plan"}, "[itinerary] 2 days"),
    (link_preview_display_v0, {"links": [{"url": "https://example.com/a", "title": "A"}], "summary": "One link"}, "[links]"),
    (options_card_display_v0, {"title": "Knee", "options": [
        {"title": "Rest", "description": "d", "bullets": ["a", "b"]}, {"title": "Ice", "description": "d", "bullets": ["a", "b"]}],
        "summary": "Two options"}, "[options] Knee"),
    (places_list_display_v0, {"places": [{"name": "Cafe", "tips": ["Book ahead"]}], "summary": "One cafe"}, "[places]"),
    (step_card_display_v0, {"steps": [{"title": "Open", "description": "d"}, {"title": "Close", "description": "d"}],
                            "summary": "Two steps", "view": "list"}, "[steps · list]"),
    (translation_display_v0, {"source_language": "English", "source_text": "hi", "target_lang": "ja",
                              "target_language": "Japanese", "translation": "こんにちは", "pronunciation": "konnichiwa",
                              "summary": "Japanese hello"}, "[translation] English → Japanese"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_obj, payload, head", CARDS, ids=[c[0].name for c in CARDS])
async def test_each_card_renders_text_and_shows_in_studio(ws, tool_obj, payload, head):
    _, emitted = ws
    res = await tool_obj.handler(payload)
    assert not _failed(res), _text(res)
    assert _text(res).startswith(head) and "(shown as a card in Studio)" in _text(res)
    (ev,) = emitted
    assert isinstance(ev, Event) and ev.kind == "studio" and ev.data["op"] == "widget"
    assert ev.data["widget"] == tool_obj.name and ev.data["data"] == payload
    # without Studio: the same text, no event
    set_studio(None)
    res = await tool_obj.handler(payload)
    assert not _failed(res) and "Studio" not in _text(res) and len(emitted) == 1


@pytest.mark.asyncio
async def test_malformed_payloads_are_refused_and_never_shown(ws):
    _, emitted = ws
    bad = [
        (chart_display_v0, {"series": []}),                       # style required, series empty
        (chart_display_v0, {"style": "pie", "series": [{"values": [1]}]}),
        (comparison_card_display_v0, {"products": [{"name": "A", "attributes": [{"label": "x", "value": "1"}, {"label": "y", "value": "2"}]}], "summary": "s"}),
        (featured_card_display_v0, {"products": [{"name": "A", "url": "www.no-scheme.com"}], "summary": "s"}),
        (link_preview_display_v0, {"links": [{"url": "https://a", "title": "t"}] * 7, "summary": "s"}),
        (options_card_display_v0, {"options": [{"title": "t", "description": "d", "bullets": ["one"]}] * 2, "summary": "s"}),
        (step_card_display_v0, {"steps": [{"title": "t", "description": "d"}], "summary": "s"}),
        (itinerary_display_v0, {"days": [{"day_label": "A very long label", "stops": [{"name": "x"}]}], "summary": "s"}),
        (translation_display_v0, {"source_language": "en", "source_text": "x", "translation": "y", "summary": "s"}),
        (places_list_display_v0, {"places": [{"name": ""}], "summary": "s"}),
        (product_carousel_display_v0, {"products": [], "summary": "s"}),
        (quiz_display_v0, {"questions": [{"id": "q1", "question": "?", "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
                                          "correct_option_id": "zzz", "explanation": "e"}]}),
        (quiz_display_v0, {"questions": [{"id": "q1", "question": "?", "options": [{"id": "a", "text": "A"}, {"id": "a", "text": "B"}],
                                          "correct_option_id": "a", "explanation": "e"}]}),
    ]
    for tool_obj, payload in bad:
        res = await tool_obj.handler(payload)
        assert _failed(res) and "Nothing was shown" in _text(res), (tool_obj.name, _text(res))
    assert emitted == []


@pytest.mark.asyncio
async def test_quiz_renders_with_the_correct_option_marked(ws):
    res = await quiz_display_v0.handler({"title": "Capitals", "initial_mode": "flashcards", "questions": [
        {"id": "q1", "question": "Capital of France?", "options": [{"id": "a", "text": "Paris"}, {"id": "b", "text": "Rome"}],
         "correct_option_id": "a", "explanation": "It is Paris.", "hint": "starts with P"}]})
    assert not _failed(res)
    out = _text(res)
    assert out.startswith("[quiz · flashcards] Capitals") and "✓ [a] Paris" in out and "  [b] Rome" in out


# --- read_me / show_widget ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_me_returns_rules_per_module_and_refuses_unknown(ws):
    res = await visualize_read_me.handler({"modules": ["diagram", "chart"], "platform": "mobile"})
    out = _text(res)
    assert "# diagram" in out and "# chart" in out and "--accent" in out and "(mobile)" in out
    assert _failed(await visualize_read_me.handler({"modules": ["sculpture"]}))
    assert _failed(await visualize_read_me.handler({"modules": []}))


@pytest.mark.asyncio
async def test_show_widget_detects_svg_or_html_and_writes_a_file_without_studio(ws):
    root, emitted = ws
    res = await visualize_show_widget.handler({"title": "oauth flow!", "widget_code": "<svg viewBox='0 0 10 10'/>",
                                               "loading_messages": ["Drawing arrows"]})
    assert not _failed(res) and "'oauth_flow'" in _text(res)
    assert emitted[-1].data == {"op": "widget", "widget": "visualize_show_widget",
                                "data": {"title": "oauth_flow", "kind": "svg", "code": "<svg viewBox='0 0 10 10'/>",
                                         "loading": ["Drawing arrows"]}}
    set_studio(None)
    res = await visualize_show_widget.handler({"title": "calc", "widget_code": "<div>calc</div>"})
    assert not _failed(res) and (root / "widgets" / "calc.html").read_text() == "<div>calc</div>"
    assert _failed(await visualize_show_widget.handler({"title": "", "widget_code": "<p/>"}))


# --- image_search on a scripted SearXNG ----------------------------------------------------


@pytest.mark.asyncio
async def test_image_search_lists_images_with_dimensions(ws, monkeypatch):
    from dream.web import searxng

    calls = []

    async def fake_search(query, categories="general", limit=8, **kw):
        calls.append((query, categories, limit))
        return {"results": [
            {"title": "Tower", "img_src": "https://img.test/t.jpg", "thumbnail_src": "https://img.test/t_s.jpg",
             "resolution": "1200x800", "url": "https://page.test/t"},
            {"title": "no src"},
            {"title": "Data", "img_src": "data:image/png;base64,AAAA"},
            {"title": "Night", "img_src": "https://img.test/n.jpg", "img_width": 640, "img_height": 480},
            {"title": "Day", "img_src": "https://img.test/d.jpg"},
            {"title": "Extra", "img_src": "https://img.test/e.jpg"},
        ]}

    monkeypatch.setattr(searxng, "search", fake_search)
    _, emitted = ws
    res = await image_search.handler({"query": "Paris France Eiffel Tower", "max_results": 3})
    assert not _failed(res), _text(res)
    out = _text(res)
    assert calls == [("Paris France Eiffel Tower", "images", 9)]
    assert "Tower 1200×800" in out and "Night 640×480" in out and "Day\n" in out and "Extra" not in out
    assert "data:image" not in out and "(shown inline in Studio)" in out
    imgs = emitted[-1].data["data"]["images"]
    assert [i["src"] for i in imgs] == ["https://img.test/t.jpg", "https://img.test/n.jpg", "https://img.test/d.jpg"]
    assert _failed(await image_search.handler({"query": "x", "max_results": 9}))

    async def down(*a, **k):
        raise searxng.SearxngUnavailable("SearXNG is not reachable")

    monkeypatch.setattr(searxng, "search", down)
    res = await image_search.handler({"query": "anything"})
    assert _failed(res) and "not reachable" in _text(res)


# --- ask_user_input, suggest_research, end_conversation ------------------------------------


@pytest.mark.asyncio
async def test_ask_user_input_becomes_a_form_in_studio_and_numbers_in_the_tui(ws):
    _, emitted = ws
    q = {"questions": [{"question": "Goal?", "options": ["Strength", "Cardio"], "type": "multi_select"},
                       {"question": "Time?", "options": ["20m", "45m", "60m"]}]}
    res = await ask_user_input.handler(q)
    assert not _failed(res) and "END YOUR TURN" in _text(res)
    form = emitted[-1].data["form"]
    assert emitted[-1].data["op"] == "ask" and [x["id"] for x in form["questions"]] == ["q1", "q2"]
    assert form["questions"][0]["multi"] is True and form["questions"][1]["multi"] is False
    set_studio(None)
    out = _text(await ask_user_input.handler(q))
    assert "1. Goal?" in out and "   1) Strength" in out and "2. Time?" in out and "   3) 60m" in out
    assert _failed(await ask_user_input.handler({"questions": [{"question": "?", "options": ["only one"]}]}))


@pytest.mark.asyncio
async def test_suggest_research_offers_a_card_or_words(ws):
    _, emitted = ws
    res = await suggest_research.handler({"rationale": "comparative analysis across many vendors"})
    assert "'Start research' card" in _text(res) and emitted[-1].data["widget"] == "suggest_research"
    set_studio(None)
    assert "say 'research it'" in _text(await suggest_research.handler({"rationale": "r"}))
    assert _failed(await suggest_research.handler({"rationale": "x" * 201}))


@pytest.mark.asyncio
async def test_end_conversation_needs_a_second_call(ws):
    _, emitted = ws
    res = await end_conversation.handler({})
    assert "call end_conversation again" in _text(res) and not widgets.end_requested()
    res = await end_conversation.handler({})
    assert "Confirmed" in _text(res) and widgets.end_requested()
    assert emitted[-1].kind == "system" and "end_conversation confirmed" in emitted[-1].data
    widgets.reset_end()
    assert not widgets.end_requested()


def test_the_seventeen_tools_are_exported():
    names = [t.name for t in WIDGET_TOOLS]
    assert len(names) == 17 and names[:11] == [
        "chart_display_v0", "comparison_card_display_v0", "featured_card_display_v0", "itinerary_display_v0",
        "link_preview_display_v0", "options_card_display_v0", "places_list_display_v0",
        "product_carousel_display_v0", "quiz_display_v0", "step_card_display_v0", "translation_display_v0"]
    assert names[11:] == ["visualize_read_me", "visualize_show_widget", "image_search", "ask_user_input",
                          "suggest_research", "end_conversation"]


def test_the_widget_tools_are_registered_and_classified(tmp_path):
    from dream.core import policy
    from dream.tools.registry import _BASE_TOOLS

    registered = {t.name for t in _BASE_TOOLS}
    assert all(t.name in registered for t in WIDGET_TOOLS)
    free = [t.name for t in WIDGET_TOOLS if t.name not in ("visualize_show_widget", "end_conversation")]
    for name in free:
        for mode in policy.MODES:
            assert policy.decide(f"mcp__dream__{name}", {"x": 1}, mode, tmp_path)[0] == "allow", (name, mode)
    # a widget can land on disk when Studio is closed: a write, like save_screenshot
    assert policy.decide("mcp__dream__visualize_show_widget", {"title": "t", "widget_code": "<p/>"}, "ask", tmp_path)[0] == "ask"
    assert policy.decide("mcp__dream__visualize_show_widget", {"title": "t", "widget_code": "<p/>"}, "accept-edits", tmp_path)[0] == "allow"
    # ending the session is never a free pass
    assert policy.decide("mcp__dream__end_conversation", {}, "ask", tmp_path)[0] != "allow"


def test_the_system_prompt_teaches_the_cards(tmp_path):
    from dream.core import system_prompt
    from dream.memory.store import MemoryStore

    text = system_prompt.build_system_prompt(MemoryStore(tmp_path / "t.db"), "s")
    for name in ("chart_display_v0", "translation_display_v0", "visualize_read_me", "visualize_show_widget",
                 "image_search", "ask_user_input", "suggest_research", "end_conversation"):
        assert name in text, name


@pytest.mark.asyncio
async def test_a_confirmed_end_conversation_ends_the_session_after_the_turn(ws, monkeypatch):
    """Phase 8 criterion 4: the confirm round-trip lives inside the tool; once the
    turn that confirmed it finishes, the app leaves its loop the way /quit does."""
    import asyncio
    from types import SimpleNamespace

    from dream.tui.app import App

    app = App(provider="machx", model="m", workspace=ws[0])
    asked: list[str] = []

    class Engine:
        store = None

        async def ask(self, prompt):
            asked.append(prompt)
            await end_conversation.handler({})
            await end_conversation.handler({})  # armed, then confirmed
            yield SimpleNamespace(kind="text_delta", data="goodbye")

        async def interrupt(self):
            pass

    app.engine = Engine()
    inputs = iter(["bye", "still here?"])

    async def next_input():
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    async def nothing():
        pass

    monkeypatch.setattr(app, "start", nothing)
    monkeypatch.setattr(app, "_shutdown", nothing)
    monkeypatch.setattr(app, "_next_input", next_input)
    await asyncio.wait_for(app.run(), 10)
    assert asked == ["bye"], "the loop ended after the confirmed turn, before the next prompt"


# --- Gate 8 -----------------------------------------------------------------------------


def test_a_cards_text_form_is_printed_whole_in_the_terminal(tmp_path):
    """Gate 8 blocking finding: the terminal showed a card as a 99-char one-line
    preview, so a comparison's third product never reached the user."""
    import io

    from rich.console import Console

    from dream.tui.render import Renderer

    buf = io.StringIO()
    r = Renderer(Console(file=buf, width=100, force_terminal=False))
    text = ("[comparison]\n  • ThinkPad X1 — $1,499\n      Display: 14in\n  • MacBook Air — $1,099\n"
            "      Display: 13in\n  • Framework 13 — $1,049\n      Display: 13.5in\n  (Three laptops)"
            "\n\n(shown as a card in Studio)")
    r.tool_use("comparison_card_display_v0", {"products": []})
    r.tool_result("comparison_card_display_v0", text, False)
    r.card("comparison_card_display_v0", text)
    out = buf.getvalue()
    assert "Framework 13" in out and "13.5in" in out and "(Three laptops)" in out
    assert "shown as a card in Studio" not in out.split("Framework")[1]


@pytest.mark.asyncio
async def test_the_app_prints_the_card_after_a_widget_tool_result(ws):
    import io
    from types import SimpleNamespace

    from rich.console import Console

    from dream.tui.app import App

    app = App(provider="machx", model="m", workspace=ws[0])
    buf = io.StringIO()
    app.renderer.console = Console(file=buf, width=100, force_terminal=False)
    text = "[steps · list]\n  1. Open — d\n  2. Close — d\n  3. Lock — the third step\n  (Three steps)"

    class Engine:
        store = None

        async def ask(self, prompt):
            yield SimpleNamespace(kind="tool_use", data={"id": "c1", "name": "mcp__dream__step_card_display_v0", "input": {}})
            yield SimpleNamespace(kind="tool_result", data={"id": "c1", "name": "mcp__dream__step_card_display_v0",
                                                            "content": text, "is_error": False})
            yield SimpleNamespace(kind="tool_use", data={"id": "c2", "name": "mcp__dream__read_file", "input": {}})
            yield SimpleNamespace(kind="tool_result", data={"id": "c2", "name": "mcp__dream__read_file",
                                                            "content": "x" * 300, "is_error": False})
            yield SimpleNamespace(kind="text_delta", data="done")

        async def interrupt(self):
            pass

    app.engine = Engine()
    await app._ask("go")
    out = buf.getvalue()
    assert "Lock — the third step" in out and "(Three steps)" in out
    assert "x" * 200 not in out, "an ordinary tool result is still a preview"


@pytest.mark.asyncio
async def test_non_finite_numbers_are_refused_before_they_reach_the_panel(ws):
    _, emitted = ws
    for bad in (float("inf"), float("nan"), -float("inf")):
        res = await chart_display_v0.handler({"style": "bar", "series": [{"values": [1, bad]}]})
        assert _failed(res) and "finite" in _text(res)
    assert emitted == []


def test_the_model_sees_standard_json_schema_only():
    import json
    for t in WIDGET_TOOLS:
        assert "required_nonempty" not in json.dumps(t.input_schema), t.name
    # the validator still enforces it
    assert "must not be empty" in _check({"summary": " "}, {"type": "object", "properties": {
        "summary": {"type": "string", "required_nonempty": True}}})


@pytest.mark.asyncio
async def test_rank_priorities_asks_for_the_order_in_words(ws):
    _, emitted = ws
    res = await ask_user_input.handler({"questions": [
        {"question": "Order these", "options": ["Speed", "Cost", "Safety"], "type": "rank_priorities"}]})
    assert not _failed(res)
    q = emitted[-1].data["form"]["questions"][0]
    assert q["kind"] == "freeform" and "Speed · Cost · Safety" in q["subtitle"] and "most important first" in q["subtitle"]


@pytest.mark.asyncio
async def test_an_end_conversation_armed_in_one_turn_does_not_carry_into_the_next(ws, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from dream.tui.app import App

    app = App(provider="machx", model="m", workspace=ws[0])
    asked: list[str] = []

    class Engine:
        store = None

        async def ask(self, prompt):
            asked.append(prompt)
            await end_conversation.handler({})  # one call per turn: arm, never confirm
            yield SimpleNamespace(kind="text_delta", data="ok")

        async def interrupt(self):
            pass

    app.engine = Engine()
    inputs = iter(["one", "two", "three"])

    async def next_input():
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    async def nothing():
        pass

    monkeypatch.setattr(app, "start", nothing)
    monkeypatch.setattr(app, "_shutdown", nothing)
    monkeypatch.setattr(app, "_next_input", next_input)
    await asyncio.wait_for(app.run(), 10)
    assert asked == ["one", "two", "three"] and not widgets.end_requested()
