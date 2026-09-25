"""DREAM-112, fix list #65: compaction decides on, and cuts by, the engine's own count.

Live 2026-09-24 (MiMo-V2.6, 200k window, image-heavy): the estimate read 1.4-1.55x the engine's count, and
compaction aimed at 85,000 tokens landed at 56,457 and 65,807 (and once at 102,835): one estimate/count
ratio for the whole history, while a flat 4,096 per image made the image share of it far too heavy.

The next prompt is the engine's last count plus what was added since, text and images each priced by
what the engine counted over recent requests; compaction measures what it removes the same way. The fake
engine below counts text at 1.5x and 1/1.5x of Dream's chars/4, and an image at 700 tokens.
"""
from __future__ import annotations

import statistics

import pytest

from dream.core.backends import openai_compat
from test_cache_friendly_head import FakeEngine, Meter, backend, tool, turn

WINDOW = 40_000
LINE = int(WINDOW * 0.75)          # compact_at below 64k
TARGET = LINE // 2                 # one big step to half the line


def rounds(n, *, images=0):
    """n tool rounds; `images` every that many rounds also looks at a screenshot (0: never)."""
    script = []
    for i in range(n):
        calls = [("read_file", {"path": f"f{i}.txt"})]
        if images and i % images == images - 1:
            calls.append(("see", {"path": f"shot{i}.png"}))
        script.append({"calls": calls})
    return script + [{"text": "done"}]


def session(ratio, *, images=0, n=60, size=6000):
    engine = FakeEngine(lead=rounds(n, images=images), ratio=ratio)
    tools = [tool("read_file", lambda a: a["path"] + " " + "y" * size)]
    if images:
        tools.append(tool("see", "one screenshot", images=1))
    # A head the size of Dream's own (its system prompt runs to thousands of tokens).
    b = backend(engine, tools=tools, n_ctx=WINDOW, multimodal=bool(images), system="Dream. " * 2000)
    b.runtime_meter = Meter()
    return engine, b


def landings(engine):
    """The first request after each compaction: its count fell by more than a third."""
    lead = [r for r in engine.requests if r["kind"] == "lead"]
    return [cur for prev, cur in zip(lead, lead[1:]) if cur["prompt_tokens"] < 0.7 * prev["prompt_tokens"]]


@pytest.fixture(autouse=True)
def _default_line(monkeypatch):
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", None)


@pytest.mark.parametrize("ratio", [1.5, 1 / 1.5])
async def test_it_fires_before_the_line_and_lands_on_its_target(ratio):
    engine, b = session(ratio, size=3000, n=90)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    assert max(lead) <= LINE, max(lead)                        # never sent past threshold x window
    landed = [r["prompt_tokens"] for r in landings(engine)]
    assert landed, "the session never reached the line"
    assert all(0.85 * TARGET <= n <= TARGET for n in landed), (landed, TARGET)
    assert abs(b._text_ratio - ratio) < 0.1 * ratio            # learned from the counts


@pytest.mark.parametrize("every", [2, 1])      # a screenshot every other round; one every round
async def test_an_image_heavy_session_is_cut_to_its_target_not_a_third_below(every):
    engine, b = session(1.0, images=every, n=60, size=3000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    assert max(lead) <= LINE, max(lead)
    landed = [r["prompt_tokens"] for r in landings(engine)]
    assert landed, "the session never reached the line"
    assert all(0.85 * TARGET <= n <= TARGET for n in landed), (landed, TARGET)
    assert 600 <= b._image_tokens <= 800                       # the engine's price of an image, not 4,096


async def test_it_fires_before_the_line_when_the_history_is_mostly_pictures():
    """Live 2026-09-24 14:18: a prompt of 170,342 went out past a 170,000 line. Screenshots priced at
    4,096 dragged one estimate/count ratio down to ~0.7, so each round of text looked smaller than it was."""
    engine = FakeEngine(ratio=1.0, lead=[{"calls": [("see", {"path": f"s{i}.png"})]} for i in range(16)]
                        + [{"calls": [("read_file", {"path": f"f{i}.txt"})]} for i in range(20)] + [{"text": "done"}])
    b = backend(engine, tools=[tool("see", "one screenshot", images=1),
                               tool("read_file", lambda a: a["path"] + " " + "y" * 12_000)],
                n_ctx=WINDOW, multimodal=True, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    landed = [r["prompt_tokens"] for r in landings(engine)]
    assert landed, "the session never reached the line"
    assert max(lead) <= LINE, max(lead)
    assert all(0.85 * TARGET <= n <= TARGET for n in landed), (landed, TARGET)


async def test_the_compaction_event_says_what_it_aimed_at_in_the_engines_tokens():
    engine, b = session(1.5)
    await turn(b)
    fields = next(f for e, f in b.runtime_meter.records if e == "compaction")
    assert fields["bound"] > LINE >= fields["projected"] and fields["target"] == TARGET
    assert fields["fill"] <= fields["bound"]                   # the plain estimate, and the one taken high
    assert fields["projected"] <= TARGET and fields["before"] > fields["after"]    # raw estimates kept too
    assert fields["text_ratio"] == pytest.approx(1.5, rel=0.1)


def test_the_prices_come_from_the_counts():
    b = backend(FakeEngine())
    # text alone: the totals price it
    for text, count in ((1000, 1500), (2000, 3000), (3000, 4500)):
        b._cal_totals.append((text, 0, count))
    b._refit()
    assert b._text_ratio == pytest.approx(1.5) and b._image_tokens == 4096
    # rounds that added an image price the image beyond their text
    b._cal_deltas.extend([(100, 1, 850), (200, 1, 1000)])
    b._cal_totals.append((3300, 2, 6450))
    b._refit()
    assert b._image_tokens == pytest.approx(700, rel=0.05) and b._text_ratio == pytest.approx(1.5, rel=0.05)


def test_a_screenshot_loop_does_not_run_the_prices_off():
    """Every round the same text and one image: those rounds alone cannot tell the prices apart."""
    b = backend(FakeEngine())
    b._cal_totals.append((4000, 0, 4000))       # before the first image: text at 1.0
    b._refit()
    for i in range(1, 8):
        b._cal_deltas.append((817, 1, 1507))
        b._cal_totals.append((4000 + 817 * i, i, 4000 + 1507 * i))
    b._refit()
    assert b._text_ratio == 1.0 and b._image_tokens == 690


def test_images_that_came_with_the_history_are_priced_from_what_the_totals_leave():
    b = backend(FakeEngine())
    b._cal_totals.append((200, 8, 3000))        # 8 images and a little text counted 3,000 in all
    b._refit()
    assert b._text_ratio == 1.0 and b._image_tokens == pytest.approx((3000 - 200) / 8)
    b._cal_totals.append((100, 1, 90_000))      # never above the flat price
    b._refit()
    assert b._image_tokens == 4096


# --- the gate's finding 2: every run fires before the line, and lands at 0.85-1.0 of its target ----------

import hashlib  # noqa: E402

BIG = 80_000
BIG_LINE = int(BIG * 0.85)         # compact_at from 64k up
BIG_TARGET = BIG_LINE // 2


def priced_images(seed):
    """Each image at its own price, 300-2,048 tokens (MiMo's range), fixed per image, different per seed."""
    return lambda url: 300 + int(hashlib.sha256(f"{seed}:{url}".encode()).hexdigest(), 16) % 1749


@pytest.mark.parametrize("seed", range(16))
async def test_images_of_varying_price_never_carry_a_prompt_past_the_line(seed):
    """DREAM-112 gate: priced at the average image, 8 of 16 such runs sent a prompt past the line
    (worst 170,539 against 170,000, the live #65 symptom)."""
    script = [{"calls": [("read_file", {"path": f"f{i}.txt"}), ("see", {"path": f"shot{i}.png"})]}
              for i in range(80)] + [{"text": "done"}]
    engine = FakeEngine(lead=script, image_tokens=priced_images(seed))
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * 3000),
                               tool("see", "one screenshot", images=1)],
                n_ctx=BIG, multimodal=True, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    landed = [r["prompt_tokens"] for r in landings(engine)]
    assert len(landed) >= 2, landed
    assert max(lead) <= BIG_LINE, max(lead)
    assert all(0.85 * BIG_TARGET <= n <= BIG_TARGET for n in landed), (landed, BIG_TARGET)


async def test_before_any_image_is_counted_an_added_one_is_priced_at_the_flat_ceiling():
    """Text rounds until the prompt is 2,700 tokens from the line, then the session's first screenshot, at MiMo's
    cap (2,048). Nothing has taught an image's price yet, so the trigger prices it at IMAGE_TOKENS (4,096) and
    compacts first. Once an image has been counted, its measured price decides (gate round 3; see
    test_a_small_window_compacts_at_its_line_and_lands_on_its_target)."""
    engine = FakeEngine(image_tokens=2048)

    def step(payload):
        k = len(payload["messages"])
        calls = [("read_file", {"path": f"f{k}.txt"})]
        if sum(tokens for _, tokens in engine._blocks(payload)) >= BIG_LINE - 2_700:
            calls.append(("see", {"path": f"shot{k}.png"}))
        return {"calls": calls}

    engine.lead = [step] * 100 + [{"text": "done"}]
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * 3000),
                               tool("see", "one screenshot", images=1)],
                n_ctx=BIG, multimodal=True, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    assert landings(engine), "the session never reached the line"
    assert max(lead) <= BIG_LINE, max(lead)


async def test_what_no_rate_explains_is_covered_by_the_largest_recent_miss():
    """An engine whose template adds 150 tokens to every message: big rounds teach a text ratio that absorbs
    it, but rounds of a few characters are too small to learn from and cost ~300 tokens each that no rate
    predicts. The first such round's miss becomes the margin, so the next ones stop before the line."""
    engine = FakeEngine(per_message=150)

    def step(payload):
        k = len(payload["messages"])
        near = sum(tokens for _, tokens in engine._blocks(payload)) >= BIG_LINE - 2_500
        return {"calls": [("read_file", {"path": f"{'tiny' if near else 'big'}{k}"})]}

    engine.lead = [step] * 80 + [{"text": "done"}]
    b = backend(engine, tools=[tool("read_file", lambda a: "ok" if a["path"].startswith("tiny") else "y" * 3000)],
                n_ctx=BIG, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    assert landings(engine), "the session never reached the line"
    assert max(lead) <= BIG_LINE, max(lead)


CODE = "function f(x) { return x + 1; }\n" * 94          # the engine counts code at 1.5x chars/4
PROSE = "the quick brown fox jumps over the lazy dog. " * 67  # and prose at 1x


def kinds(first, n_first, second, n_second):
    script = [{"calls": [("read_file", {"path": f"f{i}.txt"})]} for i in range(n_first + n_second)]
    body = {f"f{i}.txt": (first if i < n_first else second) for i in range(n_first + n_second)}
    return script + [{"text": "done"}], (lambda a: body[a["path"]])


@pytest.mark.parametrize("order", ["code, then prose", "prose, then code"])
async def test_old_messages_are_removed_at_their_own_price(order):
    """DREAM-112 gate: old code-heavy results priced at the recent prose ratio landed compaction at
    10,216 of a 15,000 target (0.68x). Each message now keeps what the engine counted for it."""
    first, second = (CODE, PROSE) if order.startswith("code") else (PROSE, CODE)
    n_first = 30 if first is CODE else 45
    script, result = kinds(first, n_first, second, 40)
    engine = FakeEngine(lead=script, ratio=lambda text: 1.5 if "{" in text else 1.0)
    b = backend(engine, tools=[tool("read_file", result)], n_ctx=BIG, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    landed = [r["prompt_tokens"] for r in landings(engine)]
    assert landed, "the session never reached the line"
    assert max(lead) <= BIG_LINE, max(lead)
    assert all(0.85 * BIG_TARGET <= n <= BIG_TARGET for n in landed), (landed, BIG_TARGET)


# --- gate round 3: a message's template cost is measured, not assumed; small windows ----------------------

@pytest.fixture
def _long_turn(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 1000)


@pytest.mark.usefixtures("_long_turn")
@pytest.mark.parametrize("images", [0, 1], ids=["text", "a screenshot every round"])
@pytest.mark.parametrize("template", [4, 8, 12, 16])
async def test_landings_do_not_drift_whatever_a_message_costs_in_template(template, images):
    """DREAM-112 gate round 2: every newly counted message was given 16 tokens of template. A compaction's stubs
    are newly counted, so each kept 16 where the engine charged its real template; the difference came out of
    the live messages counted with them, which a later compaction removed at too low a price, so every cut
    landed deeper than the one before (0.998 -> 0.764 of the target over 22 compactions at a 4-token template).
    The template is measured from the engine's counts at a compaction (MiMo's 4 before the first): 25
    compactions land within 0.85-1.0 of the target, the last where the first did. (An 80k window: at 40k the
    stubs these rounds leave behind outgrow the target before the 25th compaction.)"""
    engine = FakeEngine(lead=rounds(270 if images else 330, images=images), per_message=template)
    tools = [tool("read_file", lambda a: a["path"] + " " + "y" * 12_000)]
    if images:
        tools.append(tool("see", "one screenshot", images=1))
    b = backend(engine, tools=tools, n_ctx=BIG, multimodal=bool(images), system="Dream. " * 2000)
    b.runtime_meter = Meter()
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    landed = [round(r["prompt_tokens"] / BIG_TARGET, 3) for r in landings(engine)]
    assert len(landed) >= 25, landed
    assert max(lead) <= BIG_LINE, max(lead)
    assert all(0.85 <= x <= 1.0 for x in landed), landed
    assert abs(sum(landed[-5:]) - sum(landed[:5])) / 5 < 0.03, landed      # no drift
    # measured: the engine's template, plus the half token its rounding up adds to a message on average
    assert template <= b._template <= template + 1, b._template


def small_session(window, every, count, price, n):
    """n rounds of reading (600+ characters) and, every `every` rounds, a look at `count` screenshots."""
    script = []
    for i in range(n):
        calls = [("read_file", {"path": f"f{i}.txt"})]
        if every and i % every == every - 1:
            calls.append(("see", {"path": f"shot{i}.png"}))
        script.append({"calls": calls})
    engine = FakeEngine(lead=script + [{"text": "done"}], image_tokens=price or 700)
    tools = [tool("read_file", lambda a: a["path"] + " " + "y" * max(600, window // 40)),
             tool("see", "screenshots", images=max(count, 1))]
    b = backend(engine, tools=tools, n_ctx=window, multimodal=True, system="Dream. " * (window // 40))
    b.runtime_meter = Meter()
    return engine, b


SMALL = [  # (window, a screenshot every k rounds (0: never), images per screenshot, tokens per image)
    (16_384, 0, 0, 0), (16_384, 1, 1, 700), (32_768, 0, 0, 0), (32_768, 3, 3, 1000), (40_000, 5, 4, 1500)]


@pytest.mark.usefixtures("_long_turn")
@pytest.mark.parametrize("window,every,count,price", SMALL,
                         ids=[f"{w // 1024}k-{f'{c}x{p}-every-{e}' if e else 'text'}" for w, e, c, p in SMALL])
async def test_a_small_window_compacts_at_its_line(window, every, count, price):
    """DREAM-112 gate round 2: the trigger priced every added image at no less than 4,096 tokens even after
    images had been counted, so a 16k window with one 700-token screenshot a round compacted at 72 % of its
    line, 38 times in 120 rounds (32k with 3-image rounds: 21; 40k with 4-image rounds: 11). Once an image has
    been counted its price decides, and each compaction fires when the next prompt would pass the line."""
    line = int(window * openai_compat.compact_at(window))
    engine, b = small_session(window, every, count, price, 120)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    fired = [round(f["fill"] / line, 3) for e, f in b.runtime_meter.records if e == "compaction"]
    assert fired and max(lead) <= line, (fired, max(lead))
    assert all(x >= 0.9 for x in fired), fired            # at its line, not 4,096 tokens an image early


@pytest.mark.usefixtures("_long_turn")
@pytest.mark.parametrize("window,every,n", [(16_384, 0, 120), (16_384, 1, 70), (32_768, 0, 120), (32_768, 1, 120)],
                         ids=["16k-text", "16k-a-screenshot-every-round", "32k-text", "32k-a-screenshot-every-round"])
async def test_a_small_window_lands_on_its_target(window, every, n):
    """DREAM-112 gate round 2: 16k text alone landed at 0.65-0.80 of its target, each stub priced at 16 tokens of
    template where the engine charged 4. Each compaction now lands within 0.85-1.0. (Two limits no pricing
    changes, stated in the record: a message holding several images is elided whole, so a 6,000-token burst
    of four screenshots can land a 40k window's cut at 0.6 of its target; and the stubs and calls every round
    leaves, ~60 tokens with a screenshot, outgrow a 16k window's 6,144-token target after ~90 rounds.)"""
    target = int(window * openai_compat.compact_at(window)) // 2
    engine, b = small_session(window, every, 1, 700, n)
    await turn(b)
    landed = [round(r["prompt_tokens"] / target, 3) for r in landings(engine)]
    assert landed and all(0.85 <= x <= 1.0 for x in landed), landed


async def test_a_text_round_dearer_than_the_average_does_not_carry_the_prompt_past_the_line():
    """The trigger prices added text at the dearest ratio of the recent rounds of text alone (_text_high), not at
    their average. One small code result among prose sets it; a large code result near the line is then priced
    as code (1.5) and compacts first. At the prose-weighted average (~1.04) it would cross by ~500-1,300."""
    engine = FakeEngine(ratio=lambda text: 1.5 if "{" in text else 1.0)
    served: set[str] = set()

    def step(payload):
        if "big" in served:
            return {"text": "done"}
        k, now = len(payload["messages"]), sum(tokens for _, tokens in engine._blocks(payload))
        kind = ("big" if "small" in served and now >= BIG_LINE - 4_000
                else "small" if "small" not in served and now >= BIG_LINE - 7_000 else "prose")
        served.add(kind)
        return {"calls": [("read_file", {"path": f"{kind}{k}"})]}

    def result(args):
        if args["path"].startswith("big"):
            return CODE * 4                           # ~12,000 characters of code
        if args["path"].startswith("small"):
            return CODE[:1_000]
        return PROSE

    engine.lead = [step] * 150
    b = backend(engine, tools=[tool("read_file", result)], n_ctx=BIG, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    assert "big" in served and landings(engine), served
    assert max(lead) <= BIG_LINE, max(lead)


async def test_an_image_as_dear_as_one_counted_earlier_does_not_carry_the_prompt_past_the_line():
    """The trigger prices an added image at the dearest the engine has counted this session, not only in the last
    eight rounds (gate round 3: priced at the dearest of the recent rounds, 3 of 16 runs of varying price sent a
    prompt past the line). Two full screenshots (2,048), then small ones (300) for many rounds, then full ones
    again from 2,600 tokens below the line: priced at 300, the first of those would cross."""
    engine = FakeEngine(image_tokens=lambda url: 2048 if "BIG" in url else 300)
    shots = []

    def step(payload):
        k = len(payload["messages"])
        now = sum(tokens for _, tokens in engine._blocks(payload))
        size = "big" if len(shots) < 2 or now >= BIG_LINE - 2_600 else "small"
        shots.append(size)
        return {"calls": [("read_file", {"path": f"f{k}.txt"}), ("see", {"path": f"{size}{k}.png"})]}

    async def see(args):
        return {"content": [{"type": "text", "text": "one screenshot"},
                            {"type": "image", "mimeType": "image/png", "data": args["path"].upper()}]}

    engine.lead = [step] * 75 + [{"text": "done"}]
    shot = tool("see")
    shot.handler = see
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * 3000), shot],
                n_ctx=BIG, multimodal=True, system="Dream. " * 2000)
    await turn(b)
    lead = [r["prompt_tokens"] for r in engine.requests if r["kind"] == "lead"]
    assert landings(engine), "the session never reached the line"
    assert max(lead) <= BIG_LINE, max(lead)


@pytest.mark.usefixtures("_long_turn")
@pytest.mark.parametrize("seed", [1, 2])
async def test_screenshots_of_varying_price_leave_the_template_where_it_was(seed):
    """At a compaction the template is measured from what the prices leave unexplained, which is the template or
    an error in the estimate of the round the compaction ran in. A screenshot's price that varies (300-2,048)
    makes that estimate a guess, so such a compaction leaves the template alone; measured through it, the
    template wandered from 0.4 to 19 over eleven compactions."""
    script = [{"calls": [("read_file", {"path": f"f{i}.txt"}), ("see", {"path": f"shot{i}.png"})]}
              for i in range(110)] + [{"text": "done"}]
    engine = FakeEngine(lead=script, image_tokens=priced_images(seed))
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * 2500),
                               tool("see", "one screenshot", images=1)],
                n_ctx=WINDOW, multimodal=True, system="Dream. " * 2000)
    await turn(b)
    assert len(landings(engine)) >= 8
    assert b._template == 4.0


@pytest.mark.usefixtures("_long_turn")
@pytest.mark.parametrize("window,size", [(WINDOW, 3000), (BIG, 6000)], ids=["40k", "80k"])
async def test_a_screenshot_in_every_message_from_the_first_keeps_each_landing_under_its_target(window, size):
    """The text ratio is then never fitted on text alone, and each round's split between its text (priced at that
    ratio) and its image (the rest) is a guess: 1.0 here where the engine counts 1.5, so the image message is
    priced ~375 tokens high a round of 3,000 characters. Such a session neither measures the template through those
    prices nor drops the reserve at any cut, and no landing passes its target (at 40k without either: 1.003 and
    1.004; at 80k with the reserve dropped once a landing had shown the template: 1.001 and 1.010). The guess prices
    the text low, so the 80k session's first cut lands deep (0.70; round 2's first four 0.69-0.79)."""
    target = int(window * openai_compat.compact_at(window)) // 2
    script = [{"calls": [("read_file", {"path": f"f{i}.txt"}), ("see", {"path": f"shot{i}.png"})]}
              for i in range(80 if window == WINDOW else 120)] + [{"text": "done"}]
    engine = FakeEngine(lead=script, ratio=1.5)
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * size),
                               tool("see", "one screenshot", images=1)],
                n_ctx=window, multimodal=True, system="Dream. " * 2000)
    b.messages.append({"role": "user", "name": "dream_visual_evidence", "content": [
        {"type": "text", "text": "attached"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]})
    await turn(b)
    landed = [round(r["prompt_tokens"] / target, 3) for r in landings(engine)]
    assert len(landed) >= 5 and all(x <= 1.0 for x in landed), landed
    if window == WINDOW:
        assert all(x >= 0.85 for x in landed), landed
    assert b._template == 4.0 and not b._text_learned


@pytest.mark.usefixtures("_long_turn")
async def test_the_reserve_lasts_until_a_landing_has_shown_the_template():
    """A screenshot every round, an 8,000-character result with each: the first cut rewrites only the results, whose
    price does not depend on the template, so its landing shows nothing of it. The second cut rewrites screenshot
    messages, which do (the round's template share was taken out of them); an engine charging 16 a message, 12 more
    than MiMo's, carried that landing to 1.008 of its target when the reserve stopped after the first cut."""
    engine = FakeEngine(lead=rounds(120, images=1), per_message=16, image_tokens=1000)
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * 8000),
                               tool("see", "one screenshot", images=1)],
                n_ctx=BIG, multimodal=True, system="Dream. " * 2000)
    await turn(b)
    landed = [round(r["prompt_tokens"] / BIG_TARGET, 4) for r in landings(engine)]
    assert len(landed) >= 5 and all(0.85 <= x <= 1.0 for x in landed), landed


@pytest.mark.usefixtures("_long_turn")
@pytest.mark.parametrize("size", [9_000, 10_750, 11_750])
async def test_the_first_cut_reserves_for_a_template_dearer_than_mimos(size):
    """Nothing has measured the engine's template before the first compaction, so the first cut prices each message
    at MiMo's 4 tokens of template. An engine charging 20 makes every rewritten result's removal ~32 tokens
    dearer than priced; the first cut therefore reserves 16 tokens a template it moves (within 3 % of the
    target) and its landing stays under the target (without the reserve, at these sizes: 1.011, 1.012, 1.003)."""
    engine = FakeEngine(lead=rounds(40), per_message=20)
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * size)], n_ctx=BIG,
                system="Dream. " * 2000)
    b.runtime_meter = Meter()
    await turn(b)
    first = next(f for e, f in b.runtime_meter.records if e == "compaction")
    landed = [r["prompt_tokens"] for r in landings(engine)]
    assert landed and landed[0] <= BIG_TARGET, (landed, BIG_TARGET)
    assert first["projected"] <= BIG_TARGET * 0.99 and first["template_tokens"] == 4.0, first


def switching_session(lead_in):
    """Code results (the engine counts code at 1.5) until two compactions have measured the template, then prose (1.0)
    from `lead_in` rounds before the round that sets off the third compaction."""
    engine = FakeEngine(ratio=lambda text: 1.5 if "{" in text else 1.0)
    seen, prose = [], []

    def step(payload):
        now = sum(tokens for _, tokens in engine._blocks(payload))
        seen.append(now)
        cuts = sum(1 for a, b in zip(seen, seen[1:]) if b < 0.7 * a)
        if cuts >= 2 and now >= LINE - 1_100 - lead_in * 1_130:
            prose.append(len(seen))
        return {"calls": [("read_file", {"path": f"{'p' if prose else 'c'}{len(payload['messages'])}"})]}

    engine.lead = [step] * 80 + [{"text": "done"}]
    b = backend(engine, tools=[tool("read_file", lambda a: (PROSE if a["path"].startswith("p") else CODE)[:3000])],
                n_ctx=WINDOW, system="Dream. " * 2000)
    return engine, b


@pytest.mark.usefixtures("_long_turn")
async def test_a_switch_in_the_round_a_compaction_ran_in_does_not_move_the_template():
    """That round is priced before it is counted, at the code ratio, ~377 tokens over, and with no spread in the recent
    ratios to warn of it. The next compaction rewrites it and its error reads as a template near 16 -- one outlier among
    the measurements, and the template is their median, so it stays where two compactions of code measured it."""
    engine, b = switching_session(lead_in=0)
    await turn(b)
    first = statistics.median(list(b._templates)[:2])
    assert len(b._templates) >= 3 and max(b._templates) > first + 8, list(b._templates)   # the outlier came
    assert abs(b._template - first) < 0.5, (b._template, list(b._templates))


@pytest.mark.usefixtures("_long_turn")
async def test_an_estimate_from_rates_that_spread_is_not_measured_through():
    """Prose from two rounds before the compaction's round: the recent text ratios now spread (1.0 to 1.5), so the
    uncounted round's estimate carries that doubt, and so does its price when the next compaction rewrites it; no
    measurement is taken through it (taken, it read as 11.8)."""
    engine, b = switching_session(lead_in=2)
    await turn(b)
    assert len(b._templates) >= 2 and max(b._templates) - min(b._templates) < 1, list(b._templates)


@pytest.mark.usefixtures("_long_turn")
async def test_a_compaction_that_deletes_a_message_is_not_measured():
    """A verifier report left in the history is deleted by the next compaction. The count's change then also lost that
    message, whose price is not among the rewritten ones: measured, it read as a template of 1.4 against 4.2."""
    engine = FakeEngine(lead=[{"calls": [("read_file", {"path": f"f{i}"})]} for i in range(58)] + [{"text": "done"}])
    b = backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + PROSE[:3000])], n_ctx=WINDOW,
                system="Dream. " * 2000)
    await turn(b)
    measured = list(b._templates)
    b.messages.append({"role": "assistant", "name": "dream_verifier_report", "content": "v" * 480})
    engine.lead = [{"calls": [("read_file", {"path": f"g{i}"})]} for i in range(30)] + [{"text": "done"}]
    await turn(b, "go on")
    assert len(measured) >= 2 and not any(m.get("name") == "dream_verifier_report" for m in b.messages)
    assert list(b._templates) == measured
