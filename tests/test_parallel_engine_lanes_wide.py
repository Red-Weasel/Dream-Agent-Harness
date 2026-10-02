"""DREAM-207: Dream's global engine.parallel also drives the other lane-capable engine models -- Qwen3.8-Flash
(qwen4exp), the 35B-A3B class (qwen35moe) and Qwen3.8-27B (qwen35) -- with the precedence MiMo-V2.6 and
DeepSeek-V4.1 already had (the setting, when set, replaces the model's own `parallel`; unset, the model's own stands
and the command is today's byte for byte) and the rails the DREAM-201 gate listed, each refused or said before
anything loads: their lanes need two cards (engine.cpp: qwen4exp time-slices on one card, the 35B class and the 27B
split over both), Qwen3.8-Flash's lanes refuse IE_P2P (whatever its value), INT8 KV serves no lanes on any model,
the 27B with --spec (or IE_QWEN35_LANES=0) takes its slower joint-step path, and its extra lanes hold 65,536 tokens
by default where the others hold 32,768. No engine is started: the launchers' heavy steps are the fixtures of
test_parallel_engine_launch.py."""
from __future__ import annotations

import pytest

from dream.core import settings
from dream.desktop import startup
from dream.local import machx, preflight
from dream.local import settings as local_settings
from dream.local import models as local_models
from dream.local.models import DIRECTORY_ARCHITECTURES, LANE_ARCHITECTURES, LANE_IMAGE_ARCHITECTURES

from test_parallel_engine_launch import MIMO, V41, _clean, _serve_args, _write_global, cli_launch, desktop_launch  # noqa: F401

GGUF = ("qwen4exp", "qwen35moe", "qwen35")
STARTS = {"qwen4exp": "Qwen3.8-Flash", "qwen35moe": "The 35B-A3B class", "qwen35": "Qwen3.8-27B"}   # as a sentence starts
FIVE = "MiMo-V2.6, DeepSeek-V4.1, Qwen3.8-Flash, the 35B-A3B class and Qwen3.8-27B"
GGUF_HINT = ("requests served at once, in lanes; 1–16 (every extra lane reserves VRAM at load on both cards, and a count "
             "that does not fit is refused with the engine's numbers); Settings engine.parallel replaces it when set; "
             "2 or more need two cards; images need 1")
NOTE_2 = ("2 lanes (Settings engine.parallel): up to 2 requests, the main model's and sub-agents', run at once; lane 2 "
          "holds 16,384 tokens (at most the context); images need 1 lane, so Dream sends none.")


@pytest.fixture(autouse=True)
def _no_engine_switches(monkeypatch):
    for name in ("IE_P2P", "IE_QWEN35_LANES", "IE_Q4E_LANES", "IE_Q35MOE_LANES"):
        monkeypatch.delenv(name, raising=False)


def caps(architecture, **features):
    return {"architecture": architecture, "supported": True, "load": ["gpus", "ctx", "parallel"],
            "defaults": {"parallel": 1}, "features": features}


# --- the table and the names -------------------------------------------------------------------------------------

def test_the_lanes_table_names_the_five_models_and_only_v41_takes_images_on_lanes():
    assert LANE_ARCHITECTURES == ("mimo_v2", "deepseek_v41", "qwen4exp", "qwen35moe", "qwen35")
    assert set(DIRECTORY_ARCHITECTURES) <= set(LANE_ARCHITECTURES)
    names = getattr(local_models, "LANE_MODEL_NAMES", None)          # absent before DREAM-207: the test fails, not the import
    assert names and tuple(names) == LANE_ARCHITECTURES and getattr(local_models, "LANE_MODELS_LISTED", None) == FIVE
    assert LANE_IMAGE_ARCHITECTURES == ("deepseek_v41",)


@pytest.mark.parametrize("architecture", GGUF)
def test_a_gguf_lanes_models_own_control_carries_the_lanes_hint(architecture):
    [control] = [c for c in local_settings.available_controls(caps(architecture)) if c.name == "parallel"]
    assert (control.minimum, control.maximum, control.default) == (1, 16, 1)
    assert control.hint == GGUF_HINT
    assert control.refusal == "parallel must be a whole number from 1 to 16"
    assert local_settings.server_args({"parallel": 16}) == ["--parallel", "16"]
    # MiMo's and V4.1's hints are what DREAM-201 left them: the expert-cache words and their own endings
    [mimo] = [c for c in local_settings.available_controls(MIMO) if c.name == "parallel"]
    assert mimo.hint.startswith("requests served at once, in lanes; 1–16 (every extra lane reserves VRAM at load out of "
                                "the expert cache") and mimo.hint.endswith("replaces it when set; images need 1")
    [v41] = [c for c in local_settings.available_controls(V41) if c.name == "parallel"]
    assert v41.hint.endswith("replaces it when set; 2 or more need two cards")
    # GLM keeps the plain control: its engine runs --parallel as turns
    [glm] = [c for c in local_settings.available_controls(caps("glm5next")) if c.name == "parallel"]
    assert glm.hint == "server request slots; 1–16"


# --- the setting reaches the three, with the precedence MiMo and V4.1 have ----------------------------------------

@pytest.mark.parametrize("architecture", GGUF)
def test_the_setting_puts_a_gguf_lanes_model_on_lanes(architecture):
    _write_global({"version": 1, "engine": {"parallel": 2, "slot_ctx": 16384}})
    options = {"temperature": 0.7, "parallel": 1}
    launched, lanes, note = local_settings.engine_lanes(options, architecture, ctx=32768, gpus=2)
    assert launched == {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384} and lanes == 2
    assert options == {"temperature": 0.7, "parallel": 1}                      # the caller's dict is not changed
    assert note == NOTE_2
    assert local_settings.lanes_report(2, 2, architecture) == ("MachX serving 2 lanes: up to 2 requests run at once; "
                                                               "images need 1 lane")
    assert local_settings.engine_lanes(options, architecture, ctx=32768, gpus=None)[1] == 2     # auto: the engine's
    # its own lanes without the setting are reported too, and the launch is its own
    _write_global({"version": 1})
    launched, lanes, note = local_settings.engine_lanes({"parallel": 3}, architecture, ctx=32768, gpus=2)
    assert launched == {"parallel": 3} and lanes == 3
    assert note.startswith("3 lanes (this model's own parallel setting): up to 3 requests")


# (the global engine section, the model's own parallel) -> the `ie serve` arguments before this change (the model's own
# setting reached the command; the global one did not apply) and after it. The record's table is this list.
CASES = [({}, 1, ["--parallel", "1"], ["--parallel", "1"]),
         ({}, 4, ["--parallel", "4"], ["--parallel", "4"]),
         ({"slot_ctx": 16384}, 4, ["--parallel", "4"], ["--parallel", "4", "--slot-ctx", "16384"]),
         ({"parallel": 1}, 1, ["--parallel", "1"], ["--parallel", "1"]),
         ({"parallel": 1}, 4, ["--parallel", "4"], ["--parallel", "1"]),
         ({"parallel": 2}, 1, ["--parallel", "1"], ["--parallel", "2"]),
         ({"parallel": 2, "slot_ctx": 16384}, 1, ["--parallel", "1"], ["--parallel", "2", "--slot-ctx", "16384"]),
         ({"parallel": 2, "slot_ctx": 16384}, 4, ["--parallel", "4"], ["--parallel", "2", "--slot-ctx", "16384"]),
         ({"parallel": 16, "slot_ctx": 4096}, 1, ["--parallel", "1"], ["--parallel", "16", "--slot-ctx", "4096"])]


def _lanes_args(args):
    i = args.index("--parallel")
    out = args[i:i + 2]
    if "--slot-ctx" in args:
        j = args.index("--slot-ctx")
        out += args[j:j + 2]
    return out


@pytest.mark.parametrize("engine, own, before, after", CASES)
@pytest.mark.parametrize("architecture", GGUF)
def test_the_precedence_the_setting_replaces_the_models_own_when_set_and_stands_aside_when_not(
        monkeypatch, tmp_path, architecture, engine, own, before, after):
    _write_global({"version": 1, "engine": engine})
    options = {"temperature": 0.7, "top_k": 40, "parallel": own, "max_tokens": 16384}
    launched, lanes, note = local_settings.engine_lanes(options, architecture, ctx=32768, gpus=2)
    assert _lanes_args(_serve_args(monkeypatch, tmp_path, launched)) == after
    assert _lanes_args(_serve_args(monkeypatch, tmp_path, options)) == before        # what the model's own gave
    assert options["parallel"] == own
    if "parallel" not in engine and "slot_ctx" not in engine:
        assert launched == options and list(launched) == list(options)          # nothing set: today's, byte for byte
    if launched["parallel"] > 1:
        assert lanes == launched["parallel"] and note.startswith(f"{lanes} lanes (")
        assert ("Settings engine.parallel" in note) is ("parallel" in engine)
    else:
        assert lanes is None and "--slot-ctx" not in _serve_args(monkeypatch, tmp_path, launched)
    assert "does not apply" not in (note or "")


def test_mimo_and_v41_launch_as_before_in_every_combination(monkeypatch, tmp_path):
    """The widening changes nothing for the two models the table already held."""
    for architecture, ctx in (("mimo_v2", 32768), ("deepseek_v41", 75000)):
        for engine, own, _, after in CASES:
            _write_global({"version": 1, "engine": engine})
            launched, _, _ = local_settings.engine_lanes({"temperature": 0.7, "parallel": own}, architecture, ctx=ctx, gpus=2)
            assert _lanes_args(_serve_args(monkeypatch, tmp_path, launched, ctx=ctx)) == after, (architecture, engine, own)


@pytest.mark.parametrize("architecture", ["glm5next", "deepseek4"])
def test_the_not_a_lanes_note_names_the_five_models(architecture):
    _write_global({"version": 1, "engine": {"parallel": 4}})
    launched, lanes, note = local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768)
    assert launched == {"parallel": 1} and lanes is None
    assert note == (f"Settings engine.parallel (4) does not apply to {architecture}: Dream applies it to {FIVE} only; "
                    "this model starts with its own parallel setting (1), which takes 1–16 in its launch settings.")


def test_the_launch_table_says_what_replaces_a_gguf_lanes_models_parallel():
    _write_global({"version": 1, "engine": {"parallel": 2}})
    assert local_settings.engine_parallel_source("qwen35", "Saved for this model") == (
        "Saved for this model · replaced at launch by Settings engine.parallel (2)")
    assert local_settings.engine_parallel_source("glm5next", "Saved for this model") == "Saved for this model"
    _write_global({"version": 1})
    assert local_settings.engine_parallel_source("qwen4exp", "Your edit") == "Your edit"


# --- the rails, before anything loads ------------------------------------------------------------------------------

@pytest.mark.parametrize("architecture", GGUF)
def test_gguf_lanes_on_one_card_are_refused_before_loading(architecture):
    _write_global({"version": 1, "engine": {"parallel": 2}})
    with pytest.raises(ValueError) as refused:
        local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=1)
    # DREAM-207 re-check gate: on auto the engine may put the 35B class and the 27B on one card (the auto sentence says
    # to choose 2), so their way out is 2 GPUs alone; Qwen3.8-Flash takes two cards on auto when there are two.
    choose = "choose 2 GPUs or auto" if architecture == "qwen4exp" else "choose 2 GPUs"
    assert str(refused.value) == (f"{STARTS[architecture]} cannot serve 2 lanes here: its lanes need two cards, and this "
                                  f"launch has 1 GPU; {choose}, or set engine.parallel to 1 (Settings, Engine lanes).")
    assert local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=2)[1] == 2
    assert local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=None)[1] == 2
    _write_global({"version": 1})
    with pytest.raises(ValueError, match=f"; {choose}, or set this model's parallel to 1"):
        local_settings.engine_lanes({"parallel": 2}, architecture, ctx=32768, gpus=1)
    # one lane on one card is today's launch, nothing said
    assert local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=1) == ({"parallel": 1}, None, None)


@pytest.mark.parametrize("value", ["1", "", "0"])
def test_qwen4exp_lanes_refuse_ie_p2p_before_loading_whatever_its_value(monkeypatch, value):
    monkeypatch.setenv("IE_P2P", value)
    _write_global({"version": 1, "engine": {"parallel": 2}})
    with pytest.raises(ValueError) as refused:
        local_settings.engine_lanes({"parallel": 1}, "qwen4exp", ctx=32768, gpus=2)
    text = str(refused.value)
    assert text.startswith("Qwen3.8-Flash cannot serve 2 lanes here: IE_P2P is set in Dream's environment")
    assert text.endswith("unset IE_P2P or set engine.parallel to 1 (Settings, Engine lanes).")
    for other in ("qwen35moe", "qwen35", "mimo_v2", "deepseek_v41"):     # the other engines' lanes do not read it
        assert local_settings.engine_lanes({"parallel": 1}, other, ctx=32768, gpus=2)[1] == 2
    _write_global({"version": 1})
    with pytest.raises(ValueError, match="unset IE_P2P or set this model's parallel to 1"):
        local_settings.engine_lanes({"parallel": 2}, "qwen4exp", ctx=32768, gpus=2)
    monkeypatch.delenv("IE_P2P")
    _write_global({"version": 1, "engine": {"parallel": 2}})
    assert local_settings.engine_lanes({"parallel": 1}, "qwen4exp", ctx=32768, gpus=2)[1] == 2


@pytest.mark.parametrize("architecture", ["qwen35", "qwen4exp", "qwen35moe", "mimo_v2", "deepseek_v41"])
def test_int8_kv_with_lanes_is_refused_before_anything_loads(architecture):
    """Today validate_options refuses INT8 KV with parallel above 1 only inside machx.serve (server_args), past the
    launchers' error handling; the lanes refusal comes first, in the launchers' own words."""
    _write_global({"version": 1, "engine": {"parallel": 2}})
    with pytest.raises(ValueError) as refused:
        local_settings.engine_lanes({"parallel": 1, "int8_kv": True}, architecture, ctx=32768, gpus=2)
    assert str(refused.value).endswith("cannot serve 2 lanes here: INT8 KV cache is on, and the engine serves no lanes "
                                       "with it; turn int8_kv off, or set engine.parallel to 1 (Settings, Engine lanes).")
    _write_global({"version": 1})
    with pytest.raises(ValueError, match="turn int8_kv off, or set this model's parallel to 1"):
        local_settings.engine_lanes({"parallel": 2, "int8_kv": True}, architecture, ctx=32768, gpus=2)
    # one lane with INT8 KV is today's launch here (its one-GPU rule stays validate_options's)
    assert local_settings.engine_lanes({"parallel": 1, "int8_kv": True}, architecture, ctx=32768, gpus=1) == (
        {"parallel": 1, "int8_kv": True}, None, None)


def test_the_27b_with_speculation_on_gets_the_joint_step_notice(monkeypatch):
    _write_global({"version": 1, "engine": {"parallel": 2}})
    plain = local_settings.engine_lanes({"parallel": 1}, "qwen35", ctx=32768, gpus=2)[2]
    assert plain == NOTE_2.replace("16,384 tokens", "the engine's own 65,536 tokens") and "joint-step" not in plain
    launched, lanes, note = local_settings.engine_lanes({"parallel": 1, "speculative": True, "temperature": 0}, "qwen35",
                                                        ctx=32768, gpus=2)
    assert lanes == 2 and launched == {"parallel": 2, "speculative": True, "temperature": 0}
    assert note == plain + (" Speculation is on, so the engine serves these lanes through its joint-step path (63 "
                            "against 108 tok/s at 16 lanes in the engine's measurements): turn speculation off for "
                            "the lanes path.")
    for value in ("0", "00"):                           # the notice names the value set (DREAM-207 gate, round 3)
        monkeypatch.setenv("IE_QWEN35_LANES", value)
        assert local_settings.engine_lanes({"parallel": 1}, "qwen35", ctx=32768, gpus=2)[2] == plain + (
            f" IE_QWEN35_LANES={value} is set in Dream's environment, so the engine serves these lanes through its "
            "joint-step path.")
    for value in ("1", " 0"):                           # lanes on as the engine reads them: only a leading 0 is off
        monkeypatch.setenv("IE_QWEN35_LANES", value)
        assert local_settings.engine_lanes({"parallel": 1}, "qwen35", ctx=32768, gpus=2)[2] == plain
    # the switch is the 27B's; speculation on another lanes model says nothing more
    note = local_settings.engine_lanes({"parallel": 1, "speculative": True, "temperature": 0}, "qwen4exp", ctx=32768, gpus=2)[2]
    assert "joint-step" not in note


@pytest.mark.parametrize("architecture, variable, says", [("qwen4exp", "IE_Q4E_LANES", "time-slices these requests"),
                                                          ("qwen35moe", "IE_Q35MOE_LANES", "one at a time")])
def test_the_engines_own_lanes_switches_in_dreams_environment_are_said(monkeypatch, architecture, variable, says):
    _write_global({"version": 1, "engine": {"parallel": 2}})
    for value in ("0", "0x"):                           # the notice names the value set (DREAM-207 gate, round 3)
        monkeypatch.setenv(variable, value)
        note = local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=2)[2]
        assert note.startswith(NOTE_2.replace("16,384 tokens", "the engine's own 32,768 tokens"))
        assert f"{variable}={value} is set in Dream's environment" in note and says in note
    for value in ("1", " 0"):                           # lanes on as the engine reads them: only a leading 0 is off
        monkeypatch.setenv(variable, value)
        assert variable not in local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=2)[2]


def auto_note(lanes: int) -> str:
    return (" GPUs are on auto, so the engine picks the card count by fit, and these lanes need two cards: on one card "
            f"it does not serve them as lanes, while its /props still reports {lanes}; choose 2 GPUs.")


@pytest.mark.parametrize("architecture", ["qwen35moe", "qwen35"])
def test_gpus_on_auto_the_sentence_says_the_engine_picks_the_cards_and_these_lanes_need_two(architecture):
    """DREAM-207 gate, known limit 2: with GPUs on auto the engine places the 35B-A3B class and the 27B by fit; on one
    card it serves them no lanes, and /props total_slots echoes --parallel (engine.hpp: parallel() is the option), so
    neither the one-card rail nor lanes_report can tell. The sentence says so and recommends 2 GPUs; the launch itself
    is the engine's choice, as before, and with 2 GPUs chosen nothing more is said."""
    _write_global({"version": 1, "engine": {"parallel": 2}})
    auto = local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=None)
    two = local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=2)
    assert auto[:2] == two[:2] == ({"parallel": 2}, 2)
    assert auto[2] == two[2] + auto_note(2) and "GPUs are on auto" not in two[2]
    _write_global({"version": 1})                                        # the model's own lanes: the same sentence
    assert local_settings.engine_lanes({"parallel": 3}, architecture, ctx=32768, gpus=None)[2].endswith(auto_note(3))
    assert local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768, gpus=None) == ({"parallel": 1}, None, None)


def test_the_27b_with_speculation_on_and_gpus_on_auto_says_both_the_joint_step_path_and_the_two_cards():
    """DREAM-207 re-check gate: the auto sentence is not dropped when another notice was said -- the 27B with
    speculation on and GPUs on auto ends with the joint-step notice followed by the auto sentence."""
    _write_global({"version": 1, "engine": {"parallel": 2}})
    note = local_settings.engine_lanes({"parallel": 1, "speculative": True, "temperature": 0}, "qwen35", ctx=32768,
                                       gpus=None)[2]
    assert note.endswith(" Speculation is on, so the engine serves these lanes through its joint-step path (63 against "
                         "108 tok/s at 16 lanes in the engine's measurements): turn speculation off for the lanes path."
                         + auto_note(2))


def test_gpus_on_auto_says_nothing_more_where_the_engine_takes_two_cards_or_dream_holds_them():
    """Qwen3.8-Flash on auto takes two cards when two are there (engine.cpp: min(available, 2)); MiMo-V2.6's and
    DeepSeek-V4.1's cards are the preflight's and the engine's, as before."""
    _write_global({"version": 1, "engine": {"parallel": 2}})
    for architecture in ("qwen4exp", "mimo_v2", "deepseek_v41"):
        assert "GPUs are on auto" not in local_settings.engine_lanes({"parallel": 1}, architecture, ctx=32768,
                                                                     gpus=None)[2], architecture


@pytest.mark.parametrize("off", ["0", "00", "0x"])
def test_ie_p2p_is_not_refused_when_qwen4exp_time_slices_and_the_time_slice_notice_speaks(monkeypatch, off):
    """DREAM-207 gate, known limit 3: with IE_Q4E_LANES starting with 0 the engine keeps the time-sliced path, which runs
    no host handoff, so IE_P2P is no reason to refuse (engine.cpp: the refusal needs n_lanes > 1); the time-slice
    notice is what the launch says. With lanes on, IE_P2P is refused as before."""
    monkeypatch.setenv("IE_P2P", "1")
    monkeypatch.setenv("IE_Q4E_LANES", off)                              # any value starting with 0 (engine.cpp)
    _write_global({"version": 1, "engine": {"parallel": 2}})
    launched, lanes, note = local_settings.engine_lanes({"parallel": 1}, "qwen4exp", ctx=32768, gpus=2)
    assert (launched, lanes) == ({"parallel": 2}, 2)
    assert note.endswith(f"IE_Q4E_LANES={off} is set in Dream's environment, so the engine time-slices these requests "
                         "instead of serving them in lanes.") and "IE_P2P" not in note     # the value set, named
    monkeypatch.setenv("IE_Q4E_LANES", "1")
    with pytest.raises(ValueError, match="unset IE_P2P or set engine.parallel to 1"):
        local_settings.engine_lanes({"parallel": 1}, "qwen4exp", ctx=32768, gpus=2)


def test_ie_p2p_is_still_refused_when_ie_q4e_lanes_only_looks_like_0(monkeypatch):
    """DREAM-207 gate, round 3: the engine reads IE_Q4E_LANES as off only when its first character is 0 (engine.cpp:
    `v && *v == '0'`), so " 0" leaves the lanes on, with their host handoff, which it refuses with IE_P2P. Dream refuses
    that launch too; a value stripped before the check would let it through to the engine's refusal at load. Without
    IE_P2P the lanes are served and no time-slice notice is said."""
    monkeypatch.setenv("IE_P2P", "1")
    monkeypatch.setenv("IE_Q4E_LANES", " 0")
    _write_global({"version": 1, "engine": {"parallel": 2}})
    with pytest.raises(ValueError, match="unset IE_P2P or set engine.parallel to 1"):
        local_settings.engine_lanes({"parallel": 1}, "qwen4exp", ctx=32768, gpus=2)
    monkeypatch.delenv("IE_P2P")
    assert "IE_Q4E_LANES" not in local_settings.engine_lanes({"parallel": 1}, "qwen4exp", ctx=32768, gpus=2)[2]


def test_the_27bs_extra_lanes_default_to_65536_tokens_and_the_others_to_32768():
    _write_global({"version": 1, "engine": {"parallel": 3}})
    assert "lanes 2-3 hold the engine's own 65,536 tokens each" in local_settings.engine_lanes(
        {"parallel": 1}, "qwen35", ctx=131072, gpus=2)[2]
    for architecture in ("qwen4exp", "qwen35moe", "mimo_v2", "deepseek_v41"):
        assert "lanes 2-3 hold the engine's own 32,768 tokens each" in local_settings.engine_lanes(
            {"parallel": 1}, architecture, ctx=131072, gpus=2)[2], architecture
    assert settings.ENGINE_SLOT_CTX_DEFAULT == "the engine's own (32,768 tokens per extra lane; Qwen3.8-27B: 65,536)"
    assert settings.effective()["engine.slot_ctx"] == settings.Effective(settings.ENGINE_SLOT_CTX_DEFAULT, "default")


# --- the words: the tab, the check ---------------------------------------------------------------------------------

def test_the_settings_tab_help_names_the_five_models_and_their_rails():
    from dream.gui import settings_panel
    text = settings_panel._CATALOG["engine.parallel"][1]
    assert f"applies to {FIVE}" in text and "1 to 16" in text
    for words in ("two cards", "IE_P2P", "speculation", "INT8 KV", "replaces that setting", "MiMo-V2.6 takes images at 1 only"):
        assert words in text, words
    slot = settings_panel._CATALOG["engine.slot_ctx"][1]
    assert "32,768" in slot and "65,536 on Qwen3.8-27B" in slot


# --- the launchers -----------------------------------------------------------------------------------------------

def _gguf_preflight(monkeypatch):
    """A GGUF model goes through the terminal launcher's live preflight (check_live), not the directory models'
    launch_preflight the fixture replaces: answer "fits" so the launch reaches the lanes step."""
    monkeypatch.setattr(preflight, "check_live",
                        lambda size, gpus: preflight.Preflight(preflight.Verdict.FITS, "fits", should_load=True))


async def test_the_terminal_launcher_starts_a_gguf_lanes_model_on_lanes_and_refuses_one_card(cli_launch, monkeypatch):
    _gguf_preflight(monkeypatch)
    _write_global({"version": 1, "engine": {"parallel": 2}})
    cli_launch.caps = caps("qwen35")
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 2}]
    assert cli_launch.saved[0]["options"] == {"temperature": 0.7, "parallel": 1}     # the preset keeps its own
    assert any(line.startswith("2 lanes (Settings engine.parallel)") for line in cli_launch.lines)
    assert not any("does not apply" in line for line in cli_launch.lines)
    ready = next(line for line in cli_launch.lines if line.startswith("MachX ready"))
    assert ready.endswith("MachX serving 2 lanes: up to 2 requests run at once; images need 1 lane")
    assert cli_launch.errors == [] and len(cli_launch.ran) == 1
    cli_launch.served.clear()
    cli_launch.ran.clear()
    cli_launch.lines.clear()
    cli_launch.gpus = 1                                                            # one card: refused before loading
    await cli_launch.run()
    assert cli_launch.served == [] and cli_launch.ran == []
    [error] = cli_launch.errors
    assert error.startswith("Qwen3.8-27B cannot serve 2 lanes here: its lanes need two cards, and this launch has 1 GPU")


async def test_the_terminal_launcher_without_the_setting_launches_a_gguf_model_as_today(cli_launch, monkeypatch):
    _gguf_preflight(monkeypatch)
    monkeypatch.setattr(machx, "served_lanes", lambda timeout=3.0: pytest.fail("no /props read at one lane"))
    cli_launch.caps = caps("qwen4exp")
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 1}]
    assert [line for line in cli_launch.lines if line.startswith("MachX ready")] == [
        f"MachX ready · serving MiMo-V2.6-Flash-RL on {machx.BASE_URL}"]
    assert not any("lane" in line for line in cli_launch.lines) and cli_launch.errors == []


async def test_the_desktop_starts_a_gguf_lanes_model_on_lanes_and_refuses_one_card(desktop_launch, tmp_path):
    _write_global({"version": 1, "engine": {"parallel": 2, "slot_ctx": 16384}})
    desktop_launch.settings = {**desktop_launch.settings, "architecture": "qwen35moe", "capabilities": caps("qwen35moe")}
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == [{"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}]
    assert desktop_launch.saved[0]["options"] == {"temperature": 0.7, "parallel": 1}
    messages = [message for _, message in desktop_launch.published]
    assert any(m.startswith("2 lanes (Settings engine.parallel)") for m in messages)
    assert "MachX serving 2 lanes" in " ".join(messages)
    desktop_launch.served.clear()
    desktop_launch.request["selection"] = {**desktop_launch.request["selection"], "gpus": 1}
    with pytest.raises(ValueError, match="The 35B-A3B class cannot serve 2 lanes here: its lanes need two cards"):
        await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == []
