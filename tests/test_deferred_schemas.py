"""Deferred tool schemas: which schemas the local backend spends context on.

Pure policy, so every case here is exact arithmetic — no server, no store. The
budget-boundary tests build their window from `measure` itself rather than
hard-coded token counts, so they pin the BEHAVIOUR (the hatch is paid for, the
pins are not negotiable) and not this month's schema byte count.
"""

import json

from dream.core import tool_budget_schemas as tbs


def _schema(name, description="Does a thing. And then some more prose.", nprops=1):
    """A schema shaped like the ones openai_compat._tool_schema emits."""
    props = {f"p{i}": {"type": "string", "description": "an argument"}
             for i in range(nprops)}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": []},
        },
    }


def _names(schemas):
    return [s["function"]["name"] for s in schemas]


# --- measure -----------------------------------------------------------------


def test_measure_uses_admission_estimate():
    from dream.core.context_budget import estimate
    s = _schema("read_file")
    assert tbs.measure([s]) == estimate([s])


def test_measure_of_empty_array_matches_admission():
    from dream.core.context_budget import account
    assert tbs.measure([]) == account([], [], 4096, 256).tools == 1


def test_measure_sums_the_whole_array():
    from dream.core.context_budget import estimate
    a, b = _schema("a"), _schema("b", nprops=5)
    assert tbs.measure([a, b]) == estimate([a, b])


# --- select ------------------------------------------------------------------


def test_zero_tools():
    assert tbs.select([], 16384) == ([], [])


def test_everything_fits_ships_untouched_with_no_hatch():
    schemas = [_schema(f"tool_{i}") for i in range(3)]
    sent, deferred = tbs.select(schemas, 100_000)
    assert deferred == []
    assert sent == schemas


def test_selection_stays_under_budget():
    schemas = [_schema(f"tool_{i:02d}", nprops=6) for i in range(30)]
    window = 16384
    sent, deferred = tbs.select(schemas, window)
    assert deferred, "30 fat schemas cannot fit a tenth of a 16k window"
    assert tbs.measure(sent) <= int(window * tbs.DEFAULT_BUDGET_FRAC)
    # Everything is either sent or catalogued — nothing vanishes.
    assert len(sent) == len(schemas) - len(deferred) + 1  # + the lookup hatch
    assert set(_names(sent)) == (set(_names(schemas)) - set(deferred)
                                 | {tbs.LOOKUP_TOOL_NAME})


def test_select_does_not_mutate_its_input():
    schemas = [_schema(f"tool_{i:02d}", nprops=6) for i in range(30)]
    before = json.dumps(schemas)
    tbs.select(schemas, 16384)
    assert json.dumps(schemas) == before


def test_always_tools_survive_an_impossible_budget():
    pins = ("read_file", "write_file", "run_bash", "recall")
    schemas = [_schema(n) for n in (*pins, "fetch_moon_phase")]
    sent, deferred = tbs.select(schemas, window=1, always=frozenset(pins))
    assert _names(sent) == [*pins, tbs.LOOKUP_TOOL_NAME]
    assert deferred == ["fetch_moon_phase"]


def test_single_oversized_tool_is_deferred_but_still_reachable():
    sent, deferred = tbs.select([_schema("mega", nprops=200)], 4096)
    assert deferred == ["mega"]
    assert _names(sent) == [tbs.LOOKUP_TOOL_NAME]
    hatch = sent[0]["function"]
    assert hatch["parameters"]["properties"]["name"]["enum"] == ["mega"]
    assert "mega — " in hatch["description"]


def test_single_oversized_tool_that_is_pinned_blows_the_budget_knowingly():
    fat = _schema("mega", nprops=200)
    sent, deferred = tbs.select([fat], 4096, always=frozenset({"mega"}))
    assert sent == [fat]
    assert deferred == []
    assert tbs.measure(sent) > int(4096 * tbs.DEFAULT_BUDGET_FRAC)


def test_split_is_deterministic():
    def make():
        return [_schema(f"tool_{i:02d}", nprops=(i % 5) + 1) for i in range(20)]

    sent_a, deferred_a = tbs.select(make(), 8192)
    sent_b, deferred_b = tbs.select(make(), 8192)
    assert deferred_a and deferred_b
    assert deferred_a == deferred_b
    assert _names(sent_a) == _names(sent_b)


def test_sent_keeps_the_input_order():
    schemas = [_schema(f"tool_{i:02d}", nprops=6) for i in range(30)]
    sent, _ = tbs.select(schemas, 16384)
    kept = _names(sent)[:-1]  # drop the hatch, which is appended last
    assert kept == [n for n in _names(schemas) if n in set(kept)]


def test_cheapest_survive_when_nothing_is_known_about_usage():
    graded = [_schema(f"tool_{n}", nprops=n) for n in (1, 2, 3, 20)]
    # Room for the two cheapest plus the hatch — and, just as affordable, for one
    # mid-priced tool instead. Only cheapest-first picks the pair.
    budget = tbs.measure([*graded[:2], tbs.lookup_schema(graded[2:])])
    sent, deferred = tbs.select(graded, budget, budget_frac=1.0)
    assert _names(sent) == ["tool_1", "tool_2", tbs.LOOKUP_TOOL_NAME]
    assert deferred == ["tool_3", "tool_20"]


def test_usage_counts_change_what_survives():
    cheap = [_schema(f"cheap_{i}", nprops=6) for i in range(4)]
    fat = _schema("fat_but_loved", nprops=40)
    schemas = [*cheap, fat]
    # A budget that fits exactly one configuration: the fat tool kept and every
    # cheap one catalogued. Cheapest-first can't reach it; usage can.
    budget = tbs.measure([fat, tbs.lookup_schema(cheap)])
    sent, deferred = tbs.select(schemas, budget, budget_frac=1.0)
    assert "fat_but_loved" in deferred

    uses = {"fat_but_loved": 50}
    sent, deferred = tbs.select(schemas, budget, budget_frac=1.0,
                                usage=lambda n: uses.get(n, 0))
    assert _names(sent) == ["fat_but_loved", tbs.LOOKUP_TOOL_NAME]
    assert deferred == _names(cheap)


def test_hatch_cost_counts_against_the_budget():
    schemas = [_schema(f"tool_{i:02d}", nprops=4) for i in range(12)]
    # Exactly six schemas' worth of room — and the hatch has to come out of it.
    budget = tbs.measure(schemas[:6])
    sent, deferred = tbs.select(schemas, budget, budget_frac=1.0)
    assert tbs.LOOKUP_TOOL_NAME in _names(sent)
    assert len(deferred) > 6, "the hatch must displace at least one tool"
    assert tbs.measure(sent) <= budget


# --- catalog -----------------------------------------------------------------


def test_catalog_line_is_one_line_and_never_the_schema():
    s = _schema("browse", "Fetch a page with a real browser. " + "Long prose. " * 40,
                nprops=8)
    line = tbs.catalog_line(s)
    assert line == "browse — Fetch a page with a real browser."
    assert "\n" not in line
    assert "properties" not in line and "{" not in line


def test_catalog_line_caps_a_rambling_first_sentence():
    s = _schema("rambler", "word " * 200)
    line = tbs.catalog_line(s)
    assert "\n" not in line
    assert len(line) < 140
    assert line.endswith("…")


def test_catalog_line_survives_a_description_that_is_one_bare_sentence():
    assert tbs.catalog_line(_schema("note", "Write a note")) == "note — Write a note"


def test_hatch_catalogues_every_deferred_tool_one_line_each():
    deferred = [_schema(f"tool_{i}", "Sentence one. Sentence two.") for i in range(5)]
    hatch = tbs.lookup_schema(deferred)["function"]
    assert hatch["name"] == tbs.LOOKUP_TOOL_NAME
    assert hatch["parameters"]["properties"]["name"]["enum"] == _names(deferred)
    for s in deferred:
        assert f"{s['function']['name']} — Sentence one." in hatch["description"]
    # One line per deferred tool, and no schema body smuggled in with them.
    assert hatch["description"].count("Sentence one.") == 5
    assert "properties" not in hatch["description"]
