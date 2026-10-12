"""DREAM-258 phase 2: the experimental description-first discovery mode, away from the HTTP loop.

What is held here: the mode is an explicit opt-in and an unknown value never enables it; the catalog and the two
skill indexes are bounded whatever the toolset, preset or window; a description is only ever of something real;
words past a line's preview stay searchable; the wrapper's unwrapping refuses instead of guessing; skill selection
injects nothing and opens no SKILL.md in the mode, and is untouched outside it; and the accounting never turns a
missing count into a number or a claim.

The loop itself (lookup, definition, call, permission, Stop) is tests/test_description_discovery_http.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dream import mcp_client
from dream.core import discovery, system_prompt
from dream.skills import loader, selection
from dream.tools import installed_skill_tools


def _started_with(monkeypatch, value):
    """As if the process had started with DREAM_DISCOVERY_MODE=`value` (None: unset): the variable is set and the
    startup decision is taken from it. Dream itself takes that decision once, at import, and never again; a
    later monkeypatch.setenv / delenv on its own is "the variable changed while Dream runs"."""
    if value is None:
        monkeypatch.delenv(discovery.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(discovery.MODE_ENV, value)
    monkeypatch.setattr(discovery, "_STARTUP", discovery.read_environment())


@pytest.fixture(autouse=True)
def _no_ambient_mode(monkeypatch):
    _started_with(monkeypatch, None)


def _schema(name, description="Does a thing.", props=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props or {"p": {"type": "string"}}}}}


def _catalog_names(surface):
    """The tool names the catalog lists, read the way a model reads it: EVERY line under `Available:` is one
    tool (`name — description`, or the name alone) except a bracketed notice. Nothing is skipped for lacking a
    dash, so a line that is neither a tool nor a notice shows up here as a name and fails the caller's check."""
    text = surface.tools[0]["function"]["description"]
    body = text.split("Available:\n", 1)[1] if "Available:\n" in text else ""
    return [line.split(" — ", 1)[0] for line in body.splitlines() if discovery.is_entry(line)]


# --- the mode ------------------------------------------------------------------


def test_unset_is_the_default_and_says_nothing():
    mode = discovery.resolve(None, environ={})
    assert not mode.descriptions and mode.problem is None


@pytest.mark.parametrize("value", ["descriptions", "  Descriptions\n"])
def test_the_one_mode_is_an_explicit_opt_in(value):
    assert discovery.resolve(None, environ={discovery.MODE_ENV: value}).descriptions
    assert discovery.resolve(value).descriptions


@pytest.mark.parametrize("value", ["description", "1", "true", "lean", "descriptions,full"])
def test_an_unknown_value_never_enables_and_is_observable(value):
    mode = discovery.resolve(None, environ={discovery.MODE_ENV: value})
    assert not mode.descriptions
    assert mode.problem and discovery.MODE_ENV in mode.problem


def test_an_explicit_off_is_the_default_without_a_complaint():
    mode = discovery.resolve("off")
    assert not mode.descriptions and mode.problem is None


def test_the_process_mode_is_one_startup_decision_and_the_environment_changing_later_is_not_applied(monkeypatch):
    # started without the opt-in; the variable appears while Dream runs
    assert discovery.startup() == discovery.Mode() and discovery.drift() is None
    monkeypatch.setenv(discovery.MODE_ENV, "descriptions")
    assert not discovery.resolve(None).descriptions, "a caller without a mode gets the startup decision"
    assert discovery.read_environment().descriptions, "the reading sees the change; it is not the mode in force"
    said = discovery.drift()
    assert said and discovery.MODE_ENV in said and "'default'" in said and "Restart" in said
    assert discovery.resolve("descriptions").descriptions, "an explicit mode is still the caller's own"
    assert discovery.resolve(None, environ={discovery.MODE_ENV: "descriptions"}).descriptions

    # started with the opt-in; the variable is removed (or mistyped) while Dream runs
    _started_with(monkeypatch, "descriptions")
    assert discovery.resolve(None).descriptions and discovery.drift() is None
    monkeypatch.delenv(discovery.MODE_ENV)
    assert discovery.resolve(None).descriptions and "unset" in discovery.drift()
    monkeypatch.setenv(discovery.MODE_ENV, "lean")
    assert discovery.resolve(None).descriptions and "'lean'" in discovery.drift()

    # an unknown value going away changes nothing, so nothing is reported
    _started_with(monkeypatch, "lean")
    assert not discovery.resolve(None).descriptions and discovery.resolve(None).problem
    monkeypatch.delenv(discovery.MODE_ENV)
    assert discovery.drift() is None


# --- descriptions and the bounded catalog ----------------------------------------


def test_a_description_is_at_most_two_sentences_on_one_line():
    text = "Reads   a file.\nReturns its text!  Third sentence that must not appear. Fourth."
    out = discovery.concise(text)
    assert out == "Reads a file. Returns its text!"
    long = discovery.concise("word " * 500)
    assert "\n" not in long and len(long) < len("word " * 500) and long.endswith("…")


def test_the_surface_does_not_grow_with_the_toolset():
    def size(n):
        tools = discovery.surface([_schema(f"tool_{i:05d}", f"Tool number {i} does one job.") for i in range(n)])
        return sum(len(text) for text in tools.components.values()), tools

    small, s_small = size(300)
    huge, s_huge = size(6000)
    assert abs(huge - small) <= 8, "only the count of unlisted tools may differ"
    assert s_huge.omitted > s_small.omitted > 0
    assert len(s_huge.listed) == len(s_small.listed) > 0
    assert "enum" not in str(s_huge.tools[0]["function"]["parameters"]), "no name list rides in the schema"


def test_every_listed_description_belongs_to_a_schema_that_was_given():
    schemas = [_schema(f"t{i}", "x" * 400) for i in range(80)]
    surface = discovery.surface(schemas)
    names = _catalog_names(surface)
    assert names and set(names) <= {s["function"]["name"] for s in schemas}
    assert names == list(surface.listed)
    assert surface.omitted == len(schemas) - len(names) > 0


def test_the_count_of_unlisted_tools_is_a_notice_and_never_reads_as_a_tool():
    schemas = [_schema(f"t{i}", "x" * 400) for i in range(80)]
    surface = discovery.surface(schemas)
    text = surface.tools[0]["function"]["description"]
    last = text.splitlines()[-1]
    assert last == discovery.omission_notice(surface.omitted) and str(surface.omitted) in last
    assert last.startswith("[") and last.endswith("]") and " — " not in last, "not the shape of an entry"
    assert not discovery.is_entry(last) and "search" in last
    # every other line under the heading IS an entry, and each is a real tool with its dash
    body = text.split("Available:\n", 1)[1].splitlines()[:-1]
    assert body and all(discovery.is_entry(line) and " — " in line for line in body)
    assert [line.split(" — ", 1)[0] for line in body] == list(surface.listed)
    # nothing left out: no notice at all, and a tool with no description is still listed (by its name alone)
    small = discovery.surface([_schema("alpha", "Does a."), _schema("bare", "")])
    assert _catalog_names(small) == ["alpha", "bare"] == list(small.listed) and small.omitted == 0
    assert "more not listed" not in small.tools[0]["function"]["description"]


def test_priority_tools_get_a_line_before_the_room_runs_out_and_order_is_the_input_order():
    fillers = [_schema(f"filler_{i:04d}", "Filler text that takes room. " * 3) for i in range(500)]
    wanted = _schema("read_file", "Read a file from the workspace by its path. "
                                  "Returns its text with line numbers for the range asked for.")
    schemas = fillers + [wanted]
    # The premise, checked rather than assumed: every filler line costs the same, the fillers that fit leave
    # less room than read_file's line needs, so without priority there is genuinely no room left for it.
    filler_cost = len(discovery.catalog_line(fillers[0])) + 1
    assert {len(discovery.catalog_line(s)) + 1 for s in fillers} == {filler_cost}
    wanted_cost = len(discovery.catalog_line(wanted)) + 1
    left_over = discovery.CATALOG_CHARS % filler_cost
    assert left_over < wanted_cost <= discovery.CATALOG_CHARS, (left_over, wanted_cost)

    plain = discovery.surface(schemas)
    assert "read_file" not in plain.listed and "read_file" not in _catalog_names(plain)
    assert len(plain.listed) == discovery.CATALOG_CHARS // filler_cost, "the fillers used all the room there was"

    favoured = discovery.surface(schemas, priority={"read_file"})
    assert "read_file" in favoured.listed, "a priority tool is given its line first"
    assert len(favoured.listed) <= len(plain.listed), "its room came out of the fillers', not out of the bound"
    assert set(favoured.listed) - {"read_file"} < set(plain.listed), "and a filler gave way for it"
    # chosen first, shown where the input has it: last
    assert list(favoured.listed) == [s["function"]["name"] for s in schemas
                                     if s["function"]["name"] in set(favoured.listed)]
    assert favoured.listed[-1] == "read_file" and _catalog_names(favoured) == list(favoured.listed)
    assert discovery.surface(schemas, priority={"read_file"}).tools == favoured.tools, "the same input, the same text"


def test_controls_are_kept_whole_and_counted_as_their_own_part():
    task = _schema("task", "Delegate. " * 200, {"subagent_type": {"type": "string", "enum": ["a", "b"]}})
    surface = discovery.surface([_schema("t0")], controls=[task])
    assert surface.tools[-1] == task and surface.tools[-1] is not task
    assert "Delegate." in surface.components["control_tools"]
    assert sum(len(text) for text in surface.components.values()) >= len(str(task["function"]["description"]))


def test_search_reads_the_whole_description_not_the_preview():
    deep = _schema("ext__moon", "Reports on the sky. " * 30 + "Includes the perigee distance.")
    other = _schema("ext__tide", "Reports on the sea.")
    assert "perigee" not in discovery.catalog_line(deep), "the premise: the word is past the preview"
    lines, total = discovery.search([other, deep], "perigee sky")
    assert total == 1 and lines[0].startswith("ext__moon — ")
    assert discovery.search([other, deep], "perigee sea") == ([], 0), "every word must match"
    assert discovery.search([other, deep], "   ") == ([], 0)
    many, count = discovery.search([_schema(f"t{i}", "shared word") for i in range(50)], "shared", limit=5)
    assert len(many) == 5 and count == 50


# --- unwrapping the invocation wrapper -------------------------------------------

_CONTROLS = {"task", "snip", "fork_verifier_agent", "tool_schema", "tool_call"}


def test_a_well_formed_wrapper_yields_the_target_and_its_own_arguments():
    assert discovery.unwrap_invocation({"name": " read_file ", "arguments": {"path": "a"}},
                                       controls=_CONTROLS) == ("read_file", {"path": "a"}, None)
    assert discovery.unwrap_invocation({"name": "sleep"}, controls=_CONTROLS) == ("sleep", {}, None)
    # a model that writes the object as text: still one JSON object, so it is taken
    assert discovery.unwrap_invocation({"name": "read_file", "arguments": '{"path": "a"}'},
                                       controls=_CONTROLS) == ("read_file", {"path": "a"}, None)


@pytest.mark.parametrize("args", [
    None, [], "read_file", {}, {"name": ""}, {"name": 7, "arguments": {}},
    {"name": "task", "arguments": {"subagent_type": "x", "prompt": "y"}},
    {"name": "Task", "arguments": {}},
    {"name": "tool_call", "arguments": {"name": "read_file", "arguments": {}}},
    {"name": "TOOL_CALL", "arguments": {}},
    {"name": "tool_schema", "arguments": {"name": "read_file"}},
    {"name": "snip", "arguments": {}}, {"name": "fork_verifier_agent", "arguments": {}},
    {"name": "read_file", "path": "a"},
    {"name": "read_file", "arguments": "{not json"},
    {"name": "read_file", "arguments": "[1, 2]"},
    {"name": "read_file", "arguments": [1]}, {"name": "read_file", "arguments": 3},
])
def test_a_wrapper_that_cannot_be_taken_is_refused_with_nothing_to_run(args):
    name, inner, refusal = discovery.unwrap_invocation(args, controls=_CONTROLS)
    assert name is None and inner is None
    assert refusal and "Nothing ran" in refusal


# --- honest accounting -------------------------------------------------------------


def _all_parts():
    surface = discovery.surface([_schema("t0")])
    return {**surface.components, "base_prompt": "Base rules. Use tools for evidence.\n", "stable_sections": "",
            "learned_skill_index": "", "installed_skill_index": "x" * 40, "mcp_section": "", "task_guidance": ""}


def _gate_counter(text):
    """The gate's synthetic counter: one "token" per four characters. Not a tokenizer."""
    return len(text) // 4


def test_the_base_prompts_guidance_is_a_required_part_and_its_absence_stops_the_comparison():
    # The gate's finding: seven parts were accepted as the whole surface and compared with the target while the
    # base prompt's mandatory tool and skill guidance was not in the count.
    seven = _all_parts()
    del seven["base_prompt"], seven["stable_sections"]
    assert set(seven) == {"lookup_tool", "invocation_tool", "control_tools", "learned_skill_index",
                          "installed_skill_index", "mcp_section", "task_guidance"}
    assert {"base_prompt", "stable_sections"} <= set(discovery.REQUIRED_COMPONENTS)
    report = discovery.report(seven, counter=_gate_counter, counter_label="synthetic chars/4")
    assert report["missing_components"] == ["base_prompt", "stable_sections"] and report["complete"] is False
    assert report["count_status"] == "COUNTED", "what was supplied is still counted, and labelled incomplete"
    assert report["within_target"] is None and report["over_target_by"] is None, "no verdict on a partial surface"
    assert "base_prompt" in report["note"] and "incomplete" in report["note"]


def test_a_surface_whose_compulsory_parts_exceed_the_target_is_reported_over_it():
    # The real base prompt, counted whole: it is not dropped, condensed or excused to make the number fit.
    for base in (system_prompt.BASE, system_prompt.COMPACT_BASE):
        parts = {**_all_parts(), "base_prompt": base}
        report = discovery.report(parts, counter=_gate_counter, counter_label="synthetic chars/4")
        assert report["complete"] is True and report["components"]["base_prompt"]["chars"] == len(base)
        assert report["components"]["base_prompt"]["tokens"] == len(base) // 4
        assert report["total_tokens"] == sum(len(text) // 4 for text in parts.values())
        assert report["within_target"] is (report["total_tokens"] <= discovery.TARGET_TOKENS)
        assert report["over_target_by"] == max(0, report["total_tokens"] - discovery.TARGET_TOKENS)
        assert report["qualified"] is False
    # the premise for the full style: by this synthetic measure the base tier alone is over the whole target
    assert len(system_prompt.BASE) // 4 > discovery.TARGET_TOKENS
    full = discovery.report({**_all_parts(), "base_prompt": system_prompt.BASE}, counter=_gate_counter)
    assert full["within_target"] is False and full["over_target_by"] > 0
    assert set(full["components"]) >= {"control_tools", "lookup_tool", "invocation_tool"}, "nothing was dropped"


def test_a_total_says_what_kind_of_total_it_is_and_what_it_leaves_out():
    report = discovery.report(_all_parts(), counter=_gate_counter)
    assert report["total_kind"] == discovery.TOTAL_KIND
    assert "Not additive" in report["total_kind"] and "serialized request" in report["total_kind"]
    assert "chat template" in report["total_kind"]
    assert report["required_components"] == list(discovery.REQUIRED_COMPONENTS)
    assert report["not_counted"] and "the user's message" in report["not_counted"]
    assert "not additive" in report["note"] and "not the engine's count" in report["note"]


def test_the_base_and_stable_parts_come_from_the_prompts_own_assembly_record_or_not_at_all():
    base, stable, wake = "BASE GUIDANCE\n", "\n## Delegation\nUse task.\n", system_prompt.WAKE_HEADER + "wake"
    prompt = system_prompt.SystemPrompt(base + "style\n" + stable + wake)
    at = len(base) + len("style\n")
    prompt.provenance = ((0, len(base), "base", {"prompt_style": "full"}),
                         (len(base), at, "response_style", None),
                         (at, at + len(stable), "stable_config", {"index": 0}),
                         (at + len(stable), len(prompt), "wake", None))
    assert system_prompt.discovery_prompt_components(prompt) == {"base_prompt": base, "stable_sections": stable}
    no_stable = system_prompt.SystemPrompt(base + wake)
    no_stable.provenance = ((0, len(base), "base", None), (len(base), len(no_stable), "wake", None))
    assert system_prompt.discovery_prompt_components(no_stable) == {"base_prompt": base, "stable_sections": ""}
    # nothing recorded, or a head of unknown source: not reconstructible, so nothing is offered as if it were
    assert system_prompt.discovery_prompt_components("a plain string") == {}
    assert system_prompt.discovery_prompt_components(system_prompt.SystemPrompt(base + wake)) == {}
    unknown = system_prompt.SystemPrompt(base + wake)
    unknown.provenance = ((0, len(base), "unknown", {"reason": "text_without_assembly_metadata"}),
                          (len(base), len(unknown), "wake", {"refreshed": True}))
    assert system_prompt.discovery_prompt_components(unknown) == {}
    assert system_prompt.discovery_prompt_components(system_prompt.sourced("role", "subagent_role_prompt")) == {}


def test_the_base_tier_that_is_counted_is_the_base_tier_that_is_built(monkeypatch, tmp_path):
    monkeypatch.setattr(system_prompt.config, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(system_prompt.config, "IDENTITY_FILE", tmp_path / "IDENTITY.md")
    monkeypatch.setattr(system_prompt.config, "THREADS_FILE", tmp_path / "THREADS.md")
    full = system_prompt.base_text(None)
    assert "__MEMORY_DIR__" not in full and str(tmp_path / "memory") in full
    assert full == (system_prompt.BASE.replace("__MEMORY_DIR__", str(tmp_path / "memory"))
                    .replace("__IDENTITY_FILE__", str(tmp_path / "IDENTITY.md"))
                    .replace("__THREADS_FILE__", str(tmp_path / "THREADS.md")))
    compact = system_prompt.base_text(SimpleNamespace(prompt_style="compact"))
    assert compact == system_prompt.COMPACT_BASE.replace("__MEMORY_DIR__", str(tmp_path / "memory"))
    assert system_prompt.base_text(SimpleNamespace(prompt_style="full")) == full


def test_without_a_counter_tokens_are_unavailable_and_nothing_is_claimed():
    report = discovery.report(_all_parts())
    assert report["count_status"] == discovery.UNAVAILABLE == report["total_tokens"]
    assert all(part["tokens"] == discovery.UNAVAILABLE for part in report["components"].values())
    assert report["within_target"] is None and report["qualified"] is False
    assert report["total_chars"] == sum(part["chars"] for part in report["components"].values()) > 0


def test_an_injected_counter_is_reported_as_injected_and_never_as_a_qualification():
    report = discovery.report(_all_parts(), counter=lambda text: len(text.split()), counter_label="synthetic words")
    assert report["count_status"] == "COUNTED" and report["counter"] == "synthetic words"
    assert report["total_tokens"] == sum(part["tokens"] for part in report["components"].values())
    assert report["within_target"] is (report["total_tokens"] <= report["target_tokens"])
    assert report["qualified"] is False
    over = discovery.report(_all_parts(), counter=lambda text: 10 ** 6)
    assert over["within_target"] is False, "a surface that is too large is said to be too large"


def test_a_missing_part_stops_any_comparison_with_the_target():
    parts = _all_parts()
    del parts["installed_skill_index"]
    report = discovery.report(parts, counter=lambda text: 1)
    assert report["missing_components"] == ["installed_skill_index"] and report["complete"] is False
    assert report["within_target"] is None, "a total with a part left out is not a total"


@pytest.mark.parametrize("counter", [lambda text: -1, lambda text: 1.5, lambda text: True, lambda text: None,
                                     lambda text: 1 / 0])
def test_a_counter_that_fails_or_answers_nonsense_leaves_tokens_unavailable(counter):
    report = discovery.report(_all_parts(), counter=counter)
    assert report["count_status"] == discovery.UNAVAILABLE and report["within_target"] is None


# --- skills: no eager body, no body read -------------------------------------------


@pytest.fixture
def big_skill(tmp_path, monkeypatch):
    root = tmp_path / "bigskill"
    root.mkdir()
    manifest = root / "SKILL.md"
    manifest.write_text("---\nname: bigskill\ndescription: Use it for big jobs.\n---\nBODY-START\n"
                        + "step line\n" * 200_000, encoding="utf-8")
    skill = loader.FileSkill(name="bigskill", description="Use it for big jobs. Not for small ones. Extra.",
                             manifest=manifest, root=root, source="test")
    monkeypatch.setattr(selection.loader, "enabled", lambda s: True)
    reads = []
    real = selection._read_workflow
    monkeypatch.setattr(selection, "_read_workflow", lambda s, limit: (reads.append(s.name), real(s, limit))[1])
    return skill, reads


def test_outside_the_mode_an_explicit_skill_is_selected_and_read_as_before(big_skill, monkeypatch):
    skill, reads = big_skill
    for setting in (None, "default", "no-such-mode"):
        if setting == "no-such-mode":
            _started_with(monkeypatch, setting)                   # started with an unknown value: it changes nothing
            setting = None
        reads.clear()
        guidance = selection.select_for_task("$bigskill please tidy this", [skill], discovery_mode=setting)
        assert guidance.names == ("bigskill",) and "BODY-START" in guidance.text
        assert reads == ["bigskill"]
    # a negated request and an opt-out keep their meaning
    assert selection.select_for_task("do not use $bigskill here", [skill]).names == ()
    assert selection.select_for_task("fix it without using any skills, $bigskill", [skill]) == selection.TaskGuidance()


@pytest.mark.parametrize("via_env", [True, False])
def test_in_the_mode_nothing_is_injected_and_no_skill_file_is_opened(big_skill, monkeypatch, via_env):
    skill, reads = big_skill
    opened = []
    real_open = type(skill.manifest).open

    def spy(self, *a, **k):
        opened.append(self)
        return real_open(self, *a, **k)

    monkeypatch.setattr(type(skill.manifest), "open", spy)
    if via_env:
        _started_with(monkeypatch, "descriptions")
    guidance = selection.select_for_task("$bigskill please tidy this", [skill],
                                         discovery_mode=None if via_env else "descriptions")
    # Empty guidance: the engine appends no workflow text, so it records no skill as opened.
    assert guidance == selection.TaskGuidance()
    assert guidance.text == "" and guidance.names == () and guidance.tools == ()
    assert reads == [] and skill.manifest not in opened


def test_selection_follows_the_startup_mode_whatever_the_environment_says_later(big_skill, monkeypatch):
    """The gate's finding 3, at the selector, called exactly as the Engine calls it (no mode handed over)."""
    skill, reads = big_skill
    opened = []
    real_open = type(skill.manifest).open
    monkeypatch.setattr(type(skill.manifest), "open",
                        lambda self, *a, **k: (opened.append(self), real_open(self, *a, **k))[1])

    def engine_call():
        return selection.select_for_task("$bigskill please tidy this", [skill], max_chars=4000, used=())

    # Started in description mode, then the variable is switched off: no workflow body may come back.
    _started_with(monkeypatch, "descriptions")
    monkeypatch.delenv(discovery.MODE_ENV)
    assert engine_call() == selection.TaskGuidance()
    monkeypatch.setenv(discovery.MODE_ENV, "off")
    assert engine_call() == selection.TaskGuidance()
    assert reads == [] and skill.manifest not in opened, "no SKILL.md was read or opened"
    assert discovery.drift(), "the change is reported, not applied"

    # Started in the default, then the variable is switched on: selection goes on exactly as it started.
    _started_with(monkeypatch, None)
    before = engine_call()
    monkeypatch.setenv(discovery.MODE_ENV, "descriptions")
    after = engine_call()
    assert before == after and after.names == ("bigskill",) and "BODY-START" in after.text
    assert reads == ["bigskill", "bigskill"]
    # an explicit mode handed to this one call is still honoured, in both directions
    assert selection.select_for_task("$bigskill go", [skill], discovery_mode="descriptions") == selection.TaskGuidance()
    _started_with(monkeypatch, "descriptions")
    assert selection.select_for_task("$bigskill go", [skill], discovery_mode="default").names == ("bigskill",)


def _skills(n, curated=True):
    return [loader.FileSkill(name=f"skill-{i:05d}", description=f"Use for job {i}. " + "More detail here. " * 20,
                             manifest=SimpleNamespace(), root=SimpleNamespace(), source="test", curated=curated)
            for i in range(n)]


@pytest.mark.parametrize("preset", [None, object()])
def test_the_installed_index_is_bounded_whatever_the_preset_holds(monkeypatch, preset):
    monkeypatch.setattr("dream.presets.session", lambda: preset)
    sizes = {}
    for n in (300, 5000):
        skills = _skills(n)
        monkeypatch.setattr(installed_skill_tools, "installed", lambda refresh=False, s=skills: s)
        lines, omitted = installed_skill_tools.description_index()
        assert lines and omitted == n - len(lines)
        assert {line.split(" — ")[0] for line in lines} <= {s.name for s in skills}
        assert all("More detail here. More detail here." not in line for line in lines), "two sentences at most"
        sizes[n] = sum(len(line) for line in lines)
    assert sizes[300] == sizes[5000], "a larger preset costs nothing more"


def test_without_a_preset_only_curated_skills_are_described(monkeypatch):
    monkeypatch.setattr("dream.presets.session", lambda: None)
    skills = _skills(3, curated=False) + [loader.FileSkill(
        name="writing", description="Draft and revise prose.", manifest=SimpleNamespace(), root=SimpleNamespace(),
        source="test", curated=True)]
    monkeypatch.setattr(installed_skill_tools, "installed", lambda refresh=False: skills)
    assert installed_skill_tools.description_index() == (["writing — Draft and revise prose."], 0)


# --- the wake-up's skill indexes ---------------------------------------------------


@pytest.fixture
def wake(monkeypatch, tmp_path):
    store = SimpleNamespace(project=None, previous_session=lambda session_id, scope=None: None,
                            top_memories=lambda limit=8, scope=None, active=False: [])
    monkeypatch.setattr(system_prompt.config, "IDENTITY_FILE", tmp_path / "IDENTITY.md")
    monkeypatch.setattr(system_prompt.config, "THREADS_FILE", tmp_path / "THREADS.md")
    monkeypatch.setattr(system_prompt.longterm, "index_lines", lambda scope=None: [])
    monkeypatch.setattr(system_prompt, "_open_work", lambda store, scope: [])
    state = {"learned": ["tidy-repo — when a repo needs tidying"], "skills": _skills(4)}
    monkeypatch.setattr(system_prompt.skills, "index_lines", lambda store, scope=None: list(state["learned"]))
    monkeypatch.setattr(installed_skill_tools, "installed", lambda refresh=False: state["skills"])
    monkeypatch.setattr("dream.presets.session", lambda: None)
    return store, state


def test_the_default_wake_up_is_the_same_text_however_the_default_is_reached(wake, monkeypatch):
    store, _ = wake
    unset = system_prompt._wake_context(store, "s1")
    assert "may be supplied with the task" in unset, "the default framing"
    assert system_prompt._wake_context(store, "s1", discovery_mode="default") == unset
    _started_with(monkeypatch, "not-a-mode")                      # started with an unknown value
    assert system_prompt._wake_context(store, "s1") == unset
    _started_with(monkeypatch, None)
    monkeypatch.setenv(discovery.MODE_ENV, "descriptions")        # switched on while Dream runs: not applied
    assert system_prompt._wake_context(store, "s1") == unset


def test_the_wake_up_follows_the_startup_mode_when_the_variable_is_removed_later(wake, monkeypatch):
    store, _ = wake
    explicit = system_prompt._wake_context(store, "s1", discovery_mode="descriptions")
    _started_with(monkeypatch, "descriptions")
    assert system_prompt._wake_context(store, "s1") == explicit
    monkeypatch.delenv(discovery.MODE_ENV)
    assert system_prompt._wake_context(store, "s1") == explicit, "the prompt and the selector cannot disagree"
    assert "skill_open(name)" in explicit and "may be supplied with the task" not in explicit


def test_the_described_wake_up_says_to_open_before_use_and_is_bounded(wake):
    store, state = wake
    described = system_prompt._wake_context(store, "s1", discovery_mode="descriptions")
    assert "may be supplied with the task" not in described
    assert "skill_open(name)" in described and "skill_load" in described
    assert "skill-00000 — Use for job 0. More detail here." in described
    small = len(described)
    state["learned"] = [f"learned-{i:05d} — when job {i} comes up, " + "and more " * 60 for i in range(4000)]
    state["skills"] = _skills(4000)
    huge = system_prompt._wake_context(store, "s1", discovery_mode="descriptions")
    sections = system_prompt.discovery_skill_sections(store)
    assert all(section in huge for section in sections.values()), "what is counted is what is rendered"
    assert len(huge) - small < discovery.SKILL_INDEX_CHARS + discovery.LEARNED_INDEX_CHARS + 400
    assert "skill_find searches them" in huge and "skill_list shows them all" in huge
    assert "learned-00000" in huge and "learned-03999" not in huge


def test_a_refresh_keeps_the_mode_the_prompt_was_built_in(wake, monkeypatch):
    store, _ = wake
    built = system_prompt.SystemPrompt("HEAD" + system_prompt.WAKE_HEADER + "old wake")
    built.discovery_mode = "descriptions"
    fresh = system_prompt.refresh_wake(built, store, "s1")
    assert fresh.startswith("HEAD") and "skill_open(name)" in fresh and fresh.discovery_mode == "descriptions"
    # neither the process's own mode nor the variable changing later rewrites a prompt built without the mode
    plain = system_prompt.SystemPrompt("HEAD" + system_prompt.WAKE_HEADER + "old wake")
    plain.discovery_mode = "default"
    _started_with(monkeypatch, "descriptions")
    again = system_prompt.refresh_wake(plain, store, "s1")
    assert "may be supplied with the task" in again and again.discovery_mode == "default"
    # and a prompt built in the mode keeps it when the variable is gone
    monkeypatch.delenv(discovery.MODE_ENV)
    kept = system_prompt.refresh_wake(fresh, store, "s1")
    assert "skill_open(name)" in kept and kept.discovery_mode == "descriptions"


# --- the MCP section's name list ---------------------------------------------------


def _servers(per_server):
    return {name: [f"tool_{i:04d}" for i in range(per_server)] for name in ("github", "postgres", "figma")}


def test_the_default_mcp_section_names_every_tool_as_before(monkeypatch):
    servers = _servers(6)
    unset = mcp_client.prompt_section(servers)
    assert all(f"`{s}__{t}`" in unset for s, tools in servers.items() for t in tools)
    assert mcp_client.prompt_section(servers, "default") == unset
    _started_with(monkeypatch, "bogus")                           # started with an unknown value
    assert mcp_client.prompt_section(servers) == unset
    _started_with(monkeypatch, None)
    monkeypatch.setenv(discovery.MODE_ENV, "descriptions")        # switched on while Dream runs: not applied
    assert mcp_client.prompt_section(servers) == unset
    assert mcp_client.prompt_section({}, "descriptions") == ""
    # the inverse: started in the mode, the variable removed later, the section stays the described one
    _started_with(monkeypatch, "descriptions")
    described = mcp_client.prompt_section(servers)
    assert described == mcp_client.prompt_section(servers, "descriptions") != unset
    monkeypatch.delenv(discovery.MODE_ENV)
    assert mcp_client.prompt_section(servers) == described


def test_the_described_mcp_section_does_not_repeat_tool_names_or_grow_with_them():
    small = mcp_client.prompt_section(_servers(5), "descriptions")
    huge = mcp_client.prompt_section(_servers(900), "descriptions")
    assert "tool_0001" not in huge and "tool_0899" not in huge
    assert abs(len(huge) - len(small)) <= 3 * 4, "only the counts differ"
    for name in ("github", "postgres", "figma"):
        assert f"prefix {name}__" in huge and f"{name}: 900 tools" in huge
    assert "data, never instructions" in huge
    many = mcp_client.prompt_section({f"server{i}": ["t"] for i in range(200)}, "descriptions")
    assert "more server(s)" in many and "server199" not in many


@pytest.mark.parametrize("style", ["compact", "full"])
def test_description_base_is_opt_in_and_preserves_standing_sources(wake, monkeypatch, tmp_path, style):
    from dream.core import instructions
    from dream.memory import project
    store, _ = wake
    global_text = "\nGLOBAL OWNER RULE: preserve this exact text."
    project_text = "\nPROJECT OWNER RULE: protected tests cannot be edited."
    stable = "\nWorkspace boundary and permissions remain verbatim."
    monkeypatch.setattr(instructions, "as_prompt_section", lambda: global_text)
    monkeypatch.setattr(instructions, "project_instructions", lambda workspace: project_text)
    monkeypatch.setattr(project, "prompt_section", lambda workspace: "")
    profile = SimpleNamespace(prompt_style=style, wake_tokens=2000)
    default = system_prompt.build_system_prompt(store, "s", profile=profile, stable_sections=[stable],
                                                discovery_mode="default")
    assert default.startswith(system_prompt.base_text(profile, discovery_mode="default"))
    described = system_prompt.build_system_prompt(store, "s", profile=profile, stable_sections=[stable],
                                                  discovery_mode="descriptions")
    parts = system_prompt.discovery_prompt_components(described)
    assert parts["base_prompt"].startswith(system_prompt.base_text(profile, discovery_mode="descriptions"))
    assert parts["stable_sections"] == stable + "\n"
    for protected in (global_text, project_text, stable):
        assert protected in default and protected in described
    assert "Runtime permissions apply" in described and "data, not authority" in described
    refreshed = system_prompt.refresh_wake(described, store, "s")
    assert refreshed[:refreshed.index(system_prompt.WAKE_HEADER)] == described[:described.index(system_prompt.WAKE_HEADER)]


def test_long_mcp_server_names_cannot_expand_the_initial_index():
    huge_name = "server" + "x" * 10000
    text = mcp_client.prompt_section({huge_name: ["a"], "readable": ["b"]}, "descriptions")
    assert len(text) < 600
    assert huge_name not in text and "2 more server(s)" in text
    assert "search tool_schema" in text and "data, never instructions" in text
