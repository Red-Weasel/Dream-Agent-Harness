"""DREAM-151: Dream starts `ie serve` with a model's lanes -- since the owner's plan of 2026-10-01, the model's OWN
launch settings: the load screen's parallel and sub-agent context, saved per model when it loads.

- The engine gate's blocker 1: dream/local/settings.py capped `parallel` at 1 for every directory checkpoint
  (available_controls, validate_options). MiMo-V2.6 now serves lanes (engine P4 B4), so its per-model control takes
  1-16 (DREAM-201; 1-4 until the engine's v0.2.0 served 16); DREAM-154: so does DeepSeek-V4.1 (engine P4 B6b), on two
  cards, and a V4.1 launch the engine would refuse with
  lanes (one card, DSpark, a routing profile or dump, expert parallel) is refused before anything loads. Dream's own
  routing-profile default (fix list #49) goes only on a one-lane launch: V4.1 refuses it with lanes.
- A lanes model's parallel (2 or more) and its sub-agent context (slot_ctx, when set) go on the command line
  (`--parallel N --slot-ctx C`); at one lane, or with the sub-agent context left at 0, no --slot-ctx goes. The global
  engine.parallel / engine.slot_ctx of DREAM-151 are retired: a file that still holds them launches exactly as one
  without them. Any other model launches with its own options as they are.
- With 2 or more lanes the command carries --max-queue max(8, parallel + 1) (DREAM-209), from the launch's own options:
  Dream queues its own agents, so no setting is read and none can stop a launch.
- After the load Dream reads what the server really serves (/props total_slots) and says so; a refused load shows the
  engine's own words from the log. The preset saves the selection as it was chosen.

No engine is started: machx.serve/_launch, the preflight and the readiness wait are replaced; a real HTTP server on an
ephemeral loopback port answers /props where one is needed.
"""
from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console

from dream import config
from dream.core import moe, settings
from dream.core.profiles import settings_path
from dream.desktop import startup
from dream.local import launcher, machx, model_presets
from dream.local import settings as local_settings

MIMO = {"architecture": "mimo_v2", "memory_planner": "streaming", "supported": True, "load": ["gpus", "ctx", "parallel"],
        "defaults": {"parallel": 1}, "features": {"vision": True}}
V41 = {**MIMO, "architecture": "deepseek_v41"}
# DREAM-154: what the V4.1 engine refuses at --parallel > 1 when it is in its environment (ds41_engine.cpp, P4 B6b).
V41_LANE_ENV = ("IE_DS41_SPEC", "IE_DS41_PROFILE_OUT", "IE_DS41_DUMP_ROUTING", "IE_DS41_EP")
_REAL_SERVE = machx.serve          # the launch fixtures replace it; DREAM-209's launcher test runs the real one
_REAL_SAVE = model_presets.Presets.save
_REAL_MODEL_SETTINGS = startup.model_settings     # the desktop fixture replaces it
# The owner's file of 2026-10-01: the retired keys a launch must not read.
RETIRED = {"version": 1, "engine": {"parallel": 5, "slot_ctx": 4096}, "nested": {"max_workers": 3},
           "overrides": {"max_parallel": 1}}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    for name in V41_LANE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(moe, "CONFIG_PATH", tmp_path / "var" / "moe.json")
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(config, "VAR_DIR", tmp_path / "var")              # machx.pid lives here
    monkeypatch.setenv("DREAM_MACHX_CTX", "0")                             # serve() sets it; restored after
    settings.set_workspace(None)
    yield
    settings.set_workspace(None)


def _write_global(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- blocker 1: the per-model control ---------------------------------------------------------------------------

# DREAM-201: the lanes hint. 1-16 is the engine's range (`ie serve --parallel N`, N = 1..16, v0.2.0); the rest is what
# the engine session measured: every extra lane is reserved at load out of the in-VRAM expert cache (MiMo at 16 lanes
# of 32,768 tokens lost 10.2 / 9.0 GB per card), so more lanes can slow each one, and a count that does not fit is
# refused at load with the engine's numbers.
LANES_HINT = ("requests served at once, in lanes; 1–16 (every extra lane reserves VRAM at load out of the expert cache, "
              "so more lanes can slow each one, and a count that does not fit is refused with the engine's numbers: set "
              "the agents really run at once, with a realistic sub-agent context); ")


def test_mimo_takes_lanes_in_its_own_launch_settings():
    [control] = [c for c in local_settings.available_controls(MIMO) if c.name == "parallel"]
    assert control.maximum == 16 and control.minimum == 1 and "lanes" in control.hint and "engine.parallel" not in control.hint
    assert "image" in control.hint
    assert control.default == 1                                      # DREAM-201: the default stays 1
    assert control.hint == LANES_HINT + "images need 1"
    assert local_settings.validate_options({"parallel": 2}, architecture="mimo_v2") == {"parallel": 2}
    assert local_settings.validate_options({"parallel": 4}, architecture="mimo_v2") == {"parallel": 4}
    assert local_settings.validate_options({"parallel": 16}, architecture="mimo_v2") == {"parallel": 16}
    for refused in (17, 0):
        with pytest.raises(ValueError, match=PARALLEL_REFUSAL):
            local_settings.validate_options({"parallel": refused}, architecture="mimo_v2")


def test_deepseek_v41_takes_lanes_in_its_own_launch_settings():
    """DREAM-154 (was: V4.1 serves one request at a time): the engine serves V4.1 in lanes (P4 B6b) on two cards, and
    its images keep working there, so the hint says the cards and not "images need 1"."""
    [control] = [c for c in local_settings.available_controls(V41) if c.name == "parallel"]
    assert control.maximum == 16 and "lanes" in control.hint and "engine.parallel" not in control.hint
    assert "two cards" in control.hint and "image" not in control.hint
    assert control.hint == LANES_HINT + "2 or more need two cards" and control.default == 1
    assert local_settings.validate_options({"parallel": 2}, architecture="deepseek_v41") == {"parallel": 2}
    assert local_settings.validate_options({"parallel": 4}, architecture="deepseek_v41") == {"parallel": 4}
    assert local_settings.validate_options({"parallel": 16}, architecture="deepseek_v41") == {"parallel": 16}
    for refused in (17, 0):
        with pytest.raises(ValueError, match=PARALLEL_REFUSAL):
            local_settings.validate_options({"parallel": refused}, architecture="deepseek_v41")


@pytest.mark.parametrize("architecture", ["glm5next", "deepseek4"])
def test_every_other_models_own_parallel_takes_sixteen_too(architecture):
    """DREAM-201: the engine's `ie serve --parallel N` takes 1..16 for every model, so the plain `parallel` control --
    the one a GGUF model's own launch settings use, which reach `ie serve` through server_args -- takes 16 and refuses
    17, default 1. Gate follow-up 3: the plain hint is the range alone (GLM and DeepSeek-V4-Flash run --parallel as
    turns, and their per-slot VRAM is not measured). DREAM-207: Qwen3.8-Flash, the 35B-A3B class and the 27B carry
    the lanes hint now (test_parallel_engine_lanes_wide.py)."""
    caps = {"architecture": architecture, "load": ["gpus", "ctx", "parallel"], "defaults": {"parallel": 1}}
    [control] = [c for c in local_settings.available_controls(caps) if c.name == "parallel"]
    assert (control.minimum, control.maximum, control.default) == (1, 16, 1)
    assert control.hint == "server request slots; 1–16"
    assert local_settings.server_args({"parallel": 16}) == ["--parallel", "16"]
    for refused in (17, 0):
        with pytest.raises(ValueError, match=PARALLEL_REFUSAL):
            local_settings.server_args({"parallel": refused})


# DREAM-201 gate follow-up 2: a refused `parallel` is told in a sentence, not the whole hint.
PARALLEL_REFUSAL = "parallel must be a whole number from 1 to 16"


@pytest.mark.parametrize("capabilities", [MIMO, V41, {"architecture": "glm5next", "load": ["parallel"],
                                                       "defaults": {"parallel": 1}}])
@pytest.mark.parametrize("value", [0, 17, 100])
def test_a_refused_parallel_is_one_short_sentence(capabilities, value):
    [control] = [c for c in local_settings.available_controls(capabilities) if c.name == "parallel"]
    with pytest.raises(ValueError) as info:
        local_settings.parse_value(control, str(value))
    assert str(info.value) == PARALLEL_REFUSAL
    with pytest.raises(ValueError) as info:
        local_settings.validate_options({"parallel": value}, architecture=capabilities["architecture"])
    assert str(info.value) == PARALLEL_REFUSAL
    assert "1–16" in control.hint                                       # the hint still says the range
    assert local_settings.parse_value(control, "16") == 16 and local_settings.parse_value(control, "default") == 1


@pytest.mark.parametrize("architecture", ["glm5next", "deepseek4"])
def test_a_model_outside_the_lanes_table_launches_with_its_own_parallel_and_nothing_said(architecture):
    """Gate follow-up 1 kept: GLM-5.3 and DeepSeek-V4-Flash run --parallel as turns; their own parallel (1-16) reaches
    the command as it is, and the retired engine.parallel says nothing about them now."""
    _write_global(RETIRED)
    for own in (1, 4):
        options = {"temperature": 0.7, "parallel": own, "slot_ctx": 0}
        assert local_settings.engine_lanes(options, architecture, ctx=32768) == (options, None, None)


def test_a_one_request_model_refuses_a_second_request_in_its_own_words(monkeypatch):
    """DREAM-205 gate KL9: a directory model outside the lanes table serves one request at a time (maximum 1); its
    refusal is its own hint, not the 1-16 sentence of the lanes models. No such model today: MiMo-V2.6 is taken out of
    the lanes table here to stand for one."""
    monkeypatch.setattr(local_settings, "LANE_ARCHITECTURES", ("deepseek_v41",))
    [control] = [c for c in local_settings.available_controls(MIMO) if c.name == "parallel"]
    assert control.maximum == 1
    with pytest.raises(ValueError) as info:
        local_settings.parse_value(control, "2")
    assert str(info.value) == "This model serves one request at a time; parallel = 1"
    assert local_settings.parse_value(control, "1") == 1


def test_the_lanes_table_names_the_five_models_and_only_v41_takes_images_on_lanes():
    from dream.local.models import DIRECTORY_ARCHITECTURES, LANE_ARCHITECTURES, LANE_IMAGE_ARCHITECTURES
    assert LANE_ARCHITECTURES == ("mimo_v2", "deepseek_v41", "qwen4exp", "qwen35moe", "qwen35")    # DREAM-207
    assert set(DIRECTORY_ARCHITECTURES) <= set(LANE_ARCHITECTURES)
    assert LANE_IMAGE_ARCHITECTURES == ("deepseek_v41",)


# --- engine_lanes: what one launch takes from the model's own settings -------------------------------------------

@pytest.mark.parametrize("architecture", ["mimo_v2", "deepseek_v41", "glm5next", "qwen35", None])
def test_at_one_lane_every_launch_is_todays(architecture):
    options = {"temperature": 0.7, "parallel": 1, "max_tokens": 16384}
    launched, lanes, note = local_settings.engine_lanes(options, architecture, ctx=32768)
    assert launched == options and list(launched) == list(options) and lanes is None and note is None


@pytest.mark.parametrize("architecture", ["mimo_v2", "deepseek_v41", "qwen35", "qwen35moe", "qwen4exp"])
def test_the_retired_engine_section_no_longer_replaces_a_models_own_parallel(architecture):
    """The owner's file still holds engine.parallel 5 and slot_ctx 4096: a launch is exactly what it is without them."""
    for own in ({"parallel": 1}, {"parallel": 3}, {"parallel": 2, "slot_ctx": 16384}):
        options = {"temperature": 0.7, **own}
        _write_global({"version": 1})
        clean = local_settings.engine_lanes(options, architecture, ctx=75000, gpus=2)
        _write_global(RETIRED)
        assert local_settings.engine_lanes(options, architecture, ctx=75000, gpus=2) == clean
        assert clean[1] == (own["parallel"] if own["parallel"] > 1 else None)


def test_mimos_own_lanes_and_sub_agent_context_go_to_the_launch():
    options = {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}
    launched, lanes, note = local_settings.engine_lanes(options, "mimo_v2", ctx=200000)
    assert launched == {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384} and lanes == 2
    assert options == {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}       # the caller's dict is not changed
    assert note == ("2 lanes: up to 2 requests, the main model's and sub-agents', run at once; lane 2 holds 16,384 "
                    "tokens (at most the context); images need 1 lane, so Dream sends none.")
    launched, lanes, note = local_settings.engine_lanes({"temperature": 0.7, "parallel": 3, "slot_ctx": 0}, "mimo_v2",
                                                        ctx=200000)
    assert launched == {"temperature": 0.7, "parallel": 3} and lanes == 3          # 0 = the engine's own: not sent
    assert "lanes 2-3 hold the engine's own 32,768 tokens each" in note
    launched, lanes, note = local_settings.engine_lanes({"parallel": 16, "slot_ctx": 16384}, "mimo_v2", ctx=200000)
    assert launched == {"parallel": 16, "slot_ctx": 16384} and lanes == 16          # DREAM-201
    assert note.startswith("16 lanes: up to 16 requests") and "lanes 2-16 hold 16,384 tokens each" in note
    assert local_settings.lanes_report(2, 2, "mimo_v2") == ("MachX serving 2 lanes: up to 2 requests run at once; "
                                                            "images need 1 lane")


def test_at_one_lane_the_sub_agent_context_stays_off_the_command():
    launched, lanes, note = local_settings.engine_lanes({"parallel": 1, "slot_ctx": 16384}, "mimo_v2", ctx=32768)
    assert launched == {"parallel": 1} and lanes is None and note is None


def test_v41s_own_lanes_keep_its_images():
    """DREAM-154: DeepSeek-V4.1 serves lanes as MiMo does, and its note does not take the images away: the V4.1 engine
    serves an image prompt on its lanes (in a serial turn, B6b). It says the one thing lanes cost there: no routing
    profile is recorded (the engine records one at one lane only)."""
    options = {"temperature": 0.7, "parallel": 2, "vram_reserve_gib": 6.0, "slot_ctx": 16384}
    launched, lanes, note = local_settings.engine_lanes(options, "deepseek_v41", ctx=75000, gpus=2)
    assert launched == options and lanes == 2
    assert note.startswith("2 lanes: up to 2 requests, the main model's and sub-agents', run at once; lane 2 holds "
                           "16,384 tokens (at most the context)")
    assert "images need 1" not in note and "sends none" not in note and "images still work" in note
    assert "routing profile" in note
    assert local_settings.lanes_report(2, 2, "deepseek_v41") == "MachX serving 2 lanes: up to 2 requests run at once"
    # An engine build before B6b forces V4.1 to one request: said plainly, as for MiMo.
    report = local_settings.lanes_report(2, 1, "deepseek_v41")
    assert "does not serve deepseek_v41 in lanes" in report and "set this model's parallel to 1" in report


@pytest.mark.parametrize("env, says", [({"IE_DS41_SPEC": "1"}, "IE_DS41_SPEC=1"),
                                       ({"IE_DS41_PROFILE_OUT": "/tmp/mine.txt"}, "IE_DS41_PROFILE_OUT"),
                                       ({"IE_DS41_DUMP_ROUTING": "1"}, "IE_DS41_DUMP_ROUTING"),
                                       ({"IE_DS41_EP": "1"}, "IE_DS41_EP=1"),
                                       ({"IE_DS41_EP": "control"}, "IE_DS41_EP=control")])
def test_v41_lanes_its_engine_would_refuse_are_refused_before_loading(monkeypatch, env, says):
    """DREAM-154: the V4.1 engine refuses --parallel > 1 with DSpark, a routing profile or dump, or expert parallel in its
    environment (the engine inherits Dream's). Dream says so plainly before anything loads -- EP's refusal comes only
    after the weights are up -- naming the variable and the two ways out."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as refused:
        local_settings.engine_lanes({"parallel": 2}, "deepseek_v41", ctx=32768, gpus=2)
    text = str(refused.value)
    assert text.startswith("DeepSeek-V4.1 cannot serve 2 lanes here: ") and says in text
    assert text.endswith("set this model's parallel to 1.") and "engine.parallel" not in text
    # At one lane the engine takes them: today's launch, nothing said.
    assert local_settings.engine_lanes({"parallel": 1}, "deepseek_v41", ctx=32768, gpus=2) == ({"parallel": 1}, None,
                                                                                               None)
    # MiMo's engine does not read them.
    assert local_settings.engine_lanes({"parallel": 2}, "mimo_v2", ctx=32768, gpus=2)[1] == 2


@pytest.mark.parametrize("env", [{"IE_DS41_SPEC": "0"}, {"IE_DS41_SPEC": ""}, {"IE_DS41_EP": "0"}, {"IE_DS41_EP": ""},
                                 {"IE_DS41_PROFILE_OUT": ""}, {"IE_DS41_DUMP_ROUTING": ""}])
def test_values_the_v41_engine_reads_as_off_do_not_refuse_lanes(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert local_settings.engine_lanes({"parallel": 2}, "deepseek_v41", ctx=32768, gpus=2)[1] == 2


def test_v41_lanes_on_one_card_are_refused_before_loading():
    """The V4.1 engine serves lanes on its two-card lane pipe and refuses them with one card. Its loader takes every
    visible Arc card whatever --gpus says, and the preflight holds a directory model's GPUs at auto or all detected
    (launch_preflight), so one GPU here is a one-card machine. Auto (None) is left to the engine."""
    with pytest.raises(ValueError, match="two cards") as refused:
        local_settings.engine_lanes({"parallel": 2}, "deepseek_v41", ctx=32768, gpus=1)
    assert str(refused.value).startswith("DeepSeek-V4.1 cannot serve 2 lanes here: ")
    assert local_settings.engine_lanes({"parallel": 2}, "deepseek_v41", ctx=32768, gpus=None)[1] == 2
    assert local_settings.engine_lanes({"parallel": 2}, "deepseek_v41", ctx=32768, gpus=2)[1] == 2
    assert local_settings.engine_lanes({"parallel": 1}, "deepseek_v41", ctx=32768, gpus=1) == ({"parallel": 1}, None,
                                                                                               None)


def test_a_sub_agent_context_larger_than_the_context_is_refused_before_loading():
    with pytest.raises(ValueError, match="slot_ctx \\(sub-agent context\\) cannot exceed context length"):
        local_settings.validate_options({"parallel": 2, "slot_ctx": 65536}, ctx=32768)


# --- the command line -------------------------------------------------------------------------------------------

def _serve_args(monkeypatch, tmp_path, options, ctx=32768):
    seen = []
    monkeypatch.setattr(machx, "_launch", lambda args, **kw: seen.append(args) or SimpleNamespace(pid=0))
    machx.serve(tmp_path / "MiMo-V2.6-Flash-RL", gpus=2, ctx=ctx, options=options)
    return seen[-1]


def test_the_lanes_reach_ie_serve(monkeypatch, tmp_path):
    launched, _, _ = local_settings.engine_lanes({"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}, "mimo_v2",
                                                 ctx=32768)
    args = _serve_args(monkeypatch, tmp_path, launched)
    assert args[args.index("--parallel") + 1] == "2" and args[args.index("--slot-ctx") + 1] == "16384"
    assert args.count("--parallel") == 1 and args[args.index("--max-queue") + 1] == "8"


@pytest.mark.parametrize("architecture, ctx", [("mimo_v2", 32768), ("deepseek_v41", 75000)])
def test_sixteen_lanes_reach_ie_serve(monkeypatch, tmp_path, architecture, ctx):
    """DREAM-201: a model's own parallel 16 launches `ie serve ... --parallel 16` (with its sub-agent context)."""
    launched, lanes, _ = local_settings.engine_lanes({"temperature": 0.7, "parallel": 16, "slot_ctx": 4096},
                                                     architecture, ctx=ctx, gpus=2)
    assert lanes == 16
    args = _serve_args(monkeypatch, tmp_path, launched, ctx=ctx)
    assert args[args.index("--parallel") + 1] == "16" and args[args.index("--slot-ctx") + 1] == "4096"
    assert args.count("--parallel") == 1


def test_v41s_lanes_reach_ie_serve_and_its_one_lane_command_is_todays(monkeypatch, tmp_path):
    options = {"temperature": 0.7, "top_k": 40, "parallel": 1, "max_tokens": 16384, "vram_reserve_gib": 6.0}
    launched, lanes, note = local_settings.engine_lanes(options, "deepseek_v41", ctx=75000, gpus=2)
    assert (launched, lanes, note) == (options, None, None)
    args = _serve_args(monkeypatch, tmp_path, launched, ctx=75000)
    assert args == _serve_args(monkeypatch, tmp_path, options, ctx=75000)
    assert args[args.index("--parallel") + 1] == "1" and "--slot-ctx" not in args and "--max-queue" not in args
    launched, _, _ = local_settings.engine_lanes({**options, "parallel": 2, "slot_ctx": 16384}, "deepseek_v41",
                                                 ctx=75000, gpus=2)
    args = _serve_args(monkeypatch, tmp_path, launched, ctx=75000)
    assert args[args.index("--parallel") + 1] == "2" and args[args.index("--slot-ctx") + 1] == "16384"
    assert args.count("--parallel") == 1


def test_a_one_lane_command_is_todays_whatever_the_settings_file_holds(monkeypatch, tmp_path):
    """DREAM-209: no --max-queue at one lane, and the argv is what it was, byte for byte, whatever the settings file
    holds -- the retired keys, max agents, an unusable nested section."""
    options = {"temperature": 0.7, "parallel": 1}
    today = ["./build/src/ie", "serve", str(tmp_path / "MiMo-V2.6-Flash-RL"), "--host", machx.HOST, "--port",
             str(machx.PORT), "--gpus", "2", "--ctx", "32768", "--temp", "0.7", "--parallel", "1"]
    assert _serve_args(monkeypatch, tmp_path, options) == today
    for data in (RETIRED, {"version": 1, "nested": {"max_agents": 3}}, {"version": 1, "nested": {"max_agents": 0}},
                 {"version": 1, "nested": [7]}):
        _write_global(data)
        launched, lanes, _ = local_settings.engine_lanes({**options, "slot_ctx": 16384}, "mimo_v2", ctx=32768)
        assert lanes is None and _serve_args(monkeypatch, tmp_path, launched) == today


@pytest.mark.parametrize("lanes, queue", [(2, 8), (4, 8), (7, 8), (8, 9), (11, 12), (16, 17)])
def test_with_lanes_the_engine_queue_is_one_more_set_of_lanes_and_a_lead(monkeypatch, tmp_path, lanes, queue):
    """DREAM-209, decided again on 2026-10-01: Dream queues its own agents (max agents, the lanes) and its lease lets no
    more than the lanes into one model, so the engine's queue holds what arrives besides -- max(8, parallel + 1), from
    the launch's own options. Past it the engine answers 429 at once; its own default is 8."""
    args = _serve_args(monkeypatch, tmp_path, {"temperature": 0.7, "parallel": lanes})
    assert args[args.index("--max-queue") + 1] == str(queue) and args.count("--max-queue") == 1
    assert machx.lanes_queue({"parallel": lanes}) == queue and machx.lanes_queue({"parallel": 1}) is None


@pytest.mark.parametrize("data", [RETIRED, {"version": 1, "nested": {"max_agents": 2}},
                                  {"version": 1, "nested": {"max_agents": 0}}, {"version": 1, "nested": "7"},
                                  {"version": 1, "nested": {"max_workers": 12, "x": 1}}])
def test_the_queue_reads_no_setting_so_no_settings_file_stops_a_lanes_launch(monkeypatch, tmp_path, data):
    _write_global(data)
    args = _serve_args(monkeypatch, tmp_path, {"parallel": 4})
    assert args[args.index("--parallel") + 1] == "4" and args[args.index("--max-queue") + 1] == "8"


@pytest.mark.parametrize("architecture", ["mimo_v2", "qwen4exp"])
def test_a_models_own_lanes_get_the_queue(monkeypatch, tmp_path, architecture):
    launched, _, _ = local_settings.engine_lanes({"temperature": 0.7, "parallel": 4}, architecture, ctx=32768, gpus=2)
    args = _serve_args(monkeypatch, tmp_path, launched)
    assert args[args.index("--parallel") + 1] == "4" and args[args.index("--max-queue") + 1] == "8"


# --- what the running server serves ------------------------------------------------------------------------------

class _Props(BaseHTTPRequestHandler):
    def do_GET(self):
        status, body = self.server.answer if self.path == "/props" else (404, {})
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def props_server(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Props)
    server.answer = (200, {})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(machx, "BASE_URL", f"http://127.0.0.1:{server.server_address[1]}/v1")
    yield server
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.mark.parametrize("answer, lanes", [((200, {"total_slots": 2}), 2), ((200, {"total_slots": 1}), 1),
                                           ((200, {}), None), ((200, {"total_slots": "2"}), None),
                                           ((200, {"total_slots": 0}), None), ((503, {"total_slots": 2}), None)])
def test_served_lanes_is_the_servers_total_slots(props_server, answer, lanes):
    props_server.answer = answer
    assert machx.served_lanes() == lanes


def test_served_lanes_with_nothing_running_is_none():
    assert machx.served_lanes(timeout=1) is None


@pytest.mark.parametrize("served, says", [(2, "MachX serving 2 lanes"), (1, "serves 1 lane, not the 2"),
                                          (None, "did not say how many lanes it serves")])
def test_lanes_report(served, says):
    assert says in local_settings.lanes_report(2, served, "mimo_v2")


@pytest.mark.parametrize("served, says", [(16, "MachX serving 16 lanes: up to 16 requests run at once"),
                                          (8, "MachX serves 8 lanes, not the 16 asked for"),
                                          (1, "MachX serves 1 lane, not the 16 asked for: this engine build does not")])
def test_lanes_report_at_sixteen_says_what_the_engine_really_serves(served, says):
    """DREAM-201: the engine may come up with fewer lanes than asked; Dream says the count it got. Only one lane earns
    the "update the engine" advice (an engine build without lanes); fewer than asked is stated as it is."""
    report = local_settings.lanes_report(16, served, "deepseek_v41")
    assert report.startswith(says), report
    assert ("update the engine" in report) is (served == 1)


def test_launch_error_reads_only_what_this_launch_wrote(tmp_path):
    log = config.LOG_DIR / "machx.log"
    log.write_text("[10:00:00] load failed: an older launch\n")
    mark = machx.log_mark()
    assert machx.launch_error(mark) is None
    with log.open("a") as f:
        f.write("[10:01:00] [mimo26] loading ...\n[10:01:09] load failed: lanes: 1 more lane at 32768 needs 1.34 GiB"
                " on card 1, 0.90 GiB free\n")
    assert machx.launch_error(mark) == "load failed: lanes: 1 more lane at 32768 needs 1.34 GiB on card 1, 0.90 GiB free"
    with log.open("a") as f:
        f.write("[10:02:00] invalid options: --slot-ctx cannot exceed --ctx\n")
    assert machx.launch_error(mark) == "invalid options: --slot-ctx cannot exceed --ctx"
    assert machx.launch_error(log.stat().st_size) is None


# --- the terminal launcher --------------------------------------------------------------------------------------

@pytest.fixture
def cli_launch(monkeypatch, tmp_path):
    """serve_and_run with every heavy step replaced: capabilities, the settings table, the preflight, the load, the
    readiness wait, the preset store and the session itself."""
    path = tmp_path / "MiMo-V2.6-Flash-RL"
    path.mkdir()
    state = SimpleNamespace(path=path, caps=dict(MIMO), served=[], saved=[], ran=[], lanes=2, ready=True, gpus=2,
                            options={"temperature": 0.7, "parallel": 1}, lines=[], errors=[])
    monkeypatch.setattr(machx, "capabilities", lambda _: state.caps)

    async def choose(*args):
        return {"ctx": 32768, "gpus": state.gpus, "options": dict(state.options)}, "rev-1"
    monkeypatch.setattr(launcher, "_choose_model_settings", choose)
    monkeypatch.setattr(startup, "launch_preflight", lambda *a: "fits")
    monkeypatch.setattr(model_presets, "model_key", lambda _: "model-key")

    def serve(p, gpus=None, ctx=None, options=None, *, keep_hot=False):
        state.served.append(dict(options))
        return SimpleNamespace(pid=0)
    monkeypatch.setattr(machx, "serve", serve)
    monkeypatch.setattr(machx, "wait_ready", lambda proc: state.ready)
    monkeypatch.setattr(machx, "stop", lambda *a, **k: True)
    monkeypatch.setattr(machx, "served_model_id", lambda: "MiMo-V2.6-Flash-RL")
    monkeypatch.setattr(machx, "served_lanes", lambda timeout=3.0: state.lanes)
    monkeypatch.setattr(model_presets.Presets, "save", lambda self, p, selection, **kw: state.saved.append(selection))

    async def run_harness(r, **kw):
        state.ran.append(kw)
    monkeypatch.setattr(launcher, "_run_harness", run_harness)
    state.renderer = SimpleNamespace(system=state.lines.append, error=state.errors.append)

    async def run():
        await launcher.serve_and_run(Console(file=io.StringIO()), state.renderer, "MiMo", path, keep_hot=True)
    state.run = run
    return state


async def test_the_terminal_launcher_starts_mimo_on_its_own_lanes_and_saves_them_with_it(cli_launch):
    _write_global(RETIRED)                                                 # the retired keys change nothing
    cli_launch.options = {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}]
    assert cli_launch.saved[0]["options"] == {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}   # per model
    out = "\n".join(cli_launch.lines)
    assert "2 lanes: up to 2 requests" in out and "lane 2 holds 16,384 tokens" in out
    assert "engine.parallel" not in out
    assert any(line.startswith("MachX ready · serving MiMo-V2.6-Flash-RL") and "MachX serving 2 lanes" in line
               for line in cli_launch.lines)
    assert cli_launch.errors == [] and len(cli_launch.ran) == 1


async def test_the_terminal_launcher_says_plainly_when_the_engine_serves_fewer_lanes(cli_launch):
    cli_launch.options = {"temperature": 0.7, "parallel": 2}
    cli_launch.lanes = 1
    await cli_launch.run()
    ready = next(line for line in cli_launch.lines if line.startswith("MachX ready"))
    assert "serves 1 lane, not the 2 asked for" in ready and len(cli_launch.ran) == 1


async def test_the_terminal_launcher_at_one_lane_is_unchanged(cli_launch, monkeypatch):
    monkeypatch.setattr(machx, "served_lanes", lambda timeout=3.0: pytest.fail("no /props read at one lane"))
    _write_global(RETIRED)
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 1}]
    assert [line for line in cli_launch.lines if line.startswith("MachX ready")] == [
        f"MachX ready · serving MiMo-V2.6-Flash-RL on {machx.BASE_URL}"]
    assert not any("lane" in line for line in cli_launch.lines)


async def test_a_refused_load_shows_the_engines_own_words(cli_launch):
    cli_launch.options = {"temperature": 0.7, "parallel": 4}
    cli_launch.ready = False
    log = config.LOG_DIR / "machx.log"
    log.write_text("[09:00:00] load failed: from an earlier launch\n")
    original = cli_launch.served

    class Recording(list):
        def append(self, options):
            super().append(options)
            with log.open("a") as f:
                f.write("[09:05:00] load failed: lanes: 3 more lanes at 32768 need 4.02 GiB on card 0\n")
    cli_launch.served = Recording(original)
    await cli_launch.run()
    [error] = cli_launch.errors
    assert "didn't come up" in error and "The engine said: load failed: lanes: 3 more lanes" in error
    assert "earlier launch" not in error and cli_launch.ran == []


async def test_the_terminal_launcher_starts_sixteen_lanes_and_says_so(cli_launch):
    """DREAM-201: a model's own parallel 16 reaches the launch as `parallel: 16` and the lines say 16."""
    cli_launch.options = {"temperature": 0.7, "parallel": 16}
    cli_launch.lanes = 16
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 16}]
    assert any(line.startswith("16 lanes: up to 16 requests") for line in cli_launch.lines)
    ready = next(line for line in cli_launch.lines if line.startswith("MachX ready"))
    assert "MachX serving 16 lanes: up to 16 requests run at once" in ready
    assert cli_launch.errors == [] and len(cli_launch.ran) == 1


async def test_the_terminal_launcher_says_the_lanes_it_got_when_the_engine_serves_fewer_than_sixteen(cli_launch):
    """DREAM-201: an engine that comes up with fewer lanes than the 16 asked for (its /props total_slots) is reported
    with the count it really serves, and the session still starts."""
    cli_launch.options = {"temperature": 0.7, "parallel": 16}
    cli_launch.lanes = 8
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 16}]
    ready = next(line for line in cli_launch.lines if line.startswith("MachX ready"))
    assert "MachX serves 8 lanes, not the 16 asked for" in ready and "update the engine" not in ready
    assert cli_launch.errors == [] and len(cli_launch.ran) == 1


async def test_the_terminal_launcher_starts_lanes_with_the_queue(cli_launch, monkeypatch):
    """DREAM-209: the terminal launch's real `ie serve` command carries --max-queue with its lanes, whatever the
    settings file holds -- even a nested section no setting reader could use."""
    _write_global({"version": 1, "nested": {"max_workers": 12}, "engine": "five"})
    cli_launch.options = {"temperature": 0.7, "parallel": 16, "slot_ctx": 8192}
    seen = []
    monkeypatch.setattr(machx, "serve", _REAL_SERVE)
    monkeypatch.setattr(machx, "_launch", lambda args, **kw: seen.append(args) or SimpleNamespace(pid=0))
    await cli_launch.run()
    [args] = seen
    assert args[args.index("--parallel") + 1] == "16" and args[args.index("--max-queue") + 1] == "17"
    assert args[args.index("--slot-ctx") + 1] == "8192"
    assert cli_launch.errors == [] and len(cli_launch.ran) == 1


async def test_a_load_refused_at_sixteen_lanes_shows_the_engines_own_numbers(cli_launch):
    """DREAM-201: the engine checks at load whether N lanes fit its memory and refuses, with the numbers, a count that
    does not (engine README, request lanes). Dream shows that line as the engine wrote it, and nothing runs."""
    cli_launch.options = {"temperature": 0.7, "parallel": 16}
    cli_launch.ready = False
    log = config.LOG_DIR / "machx.log"
    log.write_text("[09:00:00] load failed: from an earlier launch\n")
    refusal = "load failed: lanes: 15 more lanes at 32768 need 20.10 GiB on card 0, 6.20 GiB free"
    original = cli_launch.served

    class Recording(list):
        def append(self, options):
            super().append(options)
            with log.open("a") as f:
                f.write(f"[09:05:00] {refusal}\n")
    cli_launch.served = Recording(original)
    await cli_launch.run()
    assert list(cli_launch.served) == [{"temperature": 0.7, "parallel": 16}]
    [error] = cli_launch.errors
    assert "didn't come up" in error and f"The engine said: {refusal}" in error
    assert "earlier launch" not in error and cli_launch.ran == []


async def test_the_terminal_launcher_starts_v41_on_its_own_lanes_and_says_so(cli_launch):
    cli_launch.caps = dict(V41)
    cli_launch.options = {"temperature": 0.7, "parallel": 2}
    await cli_launch.run()
    assert cli_launch.served == [{"temperature": 0.7, "parallel": 2}]
    assert not any("does not apply" in line for line in cli_launch.lines)
    ready = next(line for line in cli_launch.lines if line.startswith("MachX ready"))
    assert ready.endswith("MachX serving 2 lanes: up to 2 requests run at once")
    assert cli_launch.errors == [] and len(cli_launch.ran) == 1


async def test_the_terminal_launcher_checks_the_gpu_count_before_the_lanes(cli_launch, monkeypatch):
    """DREAM-155 (DREAM-154 gate finding 3): `dream local` ran engine_lanes before the preflight, so a V4.1 preset
    saved with gpus=1 on a two-card machine got the lanes' "two cards" message instead of the preflight's own. The
    preflight now comes first, as on the desktop."""
    def preflight(size, gpus, caps):
        if gpus == 1:
            raise ValueError("This model uses all visible GPUs. Select auto or all detected GPUs so every card is "
                             "checked.")
        return "fits"
    monkeypatch.setattr(startup, "launch_preflight", preflight)
    cli_launch.caps, cli_launch.gpus = dict(V41), 1
    cli_launch.options = {"temperature": 0.7, "parallel": 2}
    await cli_launch.run()
    assert cli_launch.served == [] and cli_launch.errors == [
        "This model uses all visible GPUs. Select auto or all detected GPUs so every card is checked."]


async def test_the_terminal_launcher_refuses_v41_lanes_its_engine_would_refuse(cli_launch, monkeypatch):
    monkeypatch.setenv("IE_DS41_SPEC", "1")
    cli_launch.caps = dict(V41)
    cli_launch.options = {"temperature": 0.7, "parallel": 2}
    await cli_launch.run()
    assert cli_launch.served == [] and cli_launch.ran == []
    [error] = cli_launch.errors
    assert error.startswith("DeepSeek-V4.1 cannot serve 2 lanes here: ") and "IE_DS41_SPEC=1" in error


# --- the desktop launcher ---------------------------------------------------------------------------------------

@pytest.fixture
def desktop_launch(monkeypatch, tmp_path):
    from dream.tui import app
    path = tmp_path / "MiMo-V2.6-Flash-RL"
    path.mkdir()
    model_settings = dict(identity="model-key", revision=None, context_limit=262144, max_gpus=2, architecture="mimo_v2",
                          controls=[dict(name="temperature", choices=[]), dict(name="parallel", choices=[]),
                                    dict(name="slot_ctx", choices=[])],
                          capabilities=dict(MIMO),
                          selection=dict(ctx=32768, gpus=2, options={"temperature": 0.7, "parallel": 1}))
    state = SimpleNamespace(served=[], saved=[], published=[], lanes=2, settings=model_settings)
    monkeypatch.setattr(startup, "model_settings", lambda _: state.settings)
    monkeypatch.setattr(startup, "launch_preflight", lambda *_: "fits")
    monkeypatch.setattr(model_presets, "model_key", lambda _: "model-key")
    monkeypatch.setattr(machx, "list_models_detailed", lambda: [SimpleNamespace(path=path, size_gb=1, name="MiMo")])
    monkeypatch.setattr(machx, "is_serving", lambda: False)
    proc = Mock(poll=Mock(return_value=0))

    def serve(p, gpus=None, ctx=None, options=None, **kw):
        state.served.append(dict(options))
        return proc
    monkeypatch.setattr(machx, "serve", serve)
    monkeypatch.setattr(machx, "wait_ready", lambda _: True)
    monkeypatch.setattr(machx, "served_model_id", lambda: "MiMo-V2.6-Flash-RL")
    monkeypatch.setattr(machx, "served_lanes", lambda timeout=3.0: state.lanes)
    monkeypatch.setattr(model_presets.Presets, "save", lambda self, p, selection, **kw: state.saved.append(selection))
    monkeypatch.setattr(startup, "publish", lambda _path, kind, message: state.published.append((kind, message)))
    monkeypatch.setattr(app, "App", Mock(return_value=SimpleNamespace(run=AsyncMock())))
    state.request = dict(workspace=str(tmp_path), choice=dict(kind="local", provider="machx", path=str(path)),
                         selection=model_settings["selection"], identity="model-key", revision=None)
    return state


def _choose(desktop_launch, **options):
    """What the owner set on the load screen for this launch."""
    desktop_launch.request["selection"] = {**desktop_launch.request["selection"],
                                           "options": {**desktop_launch.request["selection"]["options"], **options}}


async def test_the_desktop_starts_mimo_on_its_own_lanes_and_says_so(desktop_launch, tmp_path):
    _write_global(RETIRED)                                                 # the retired keys change nothing
    _choose(desktop_launch, parallel=2, slot_ctx=16384)
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == [{"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}]
    assert desktop_launch.saved[0]["options"] == {"temperature": 0.7, "parallel": 2, "slot_ctx": 16384}
    messages = [message for _, message in desktop_launch.published]
    assert any(m.startswith("2 lanes: up to 2 requests") for m in messages)
    assert "MachX serving 2 lanes" in " ".join(messages) and "engine.parallel" not in " ".join(messages)


async def test_the_desktop_saves_parallel_and_the_sub_agent_context_per_model_and_reads_them_back(
        desktop_launch, tmp_path, monkeypatch):
    """The owner: [feedback]. The real preset store: the next load screen of
    this model shows its parallel and sub-agent context, from the preset."""
    from dream.local import model_defaults
    monkeypatch.setattr(model_presets.Presets, "save", _REAL_SAVE)
    _choose(desktop_launch, parallel=3, slot_ctx=8192)
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    path = tmp_path / "MiMo-V2.6-Flash-RL"
    saved = model_presets.Presets().load(path)["selection"]
    assert saved == {"ctx": 32768, "gpus": 2, "options": {"temperature": 0.7, "parallel": 3, "slot_ctx": 8192}}
    monkeypatch.setattr(machx, "capabilities", lambda _: dict(MIMO))
    monkeypatch.setattr(model_defaults, "inspect_hardware", lambda: {"devices": [], "ram_total_gb": 256})
    data = _REAL_MODEL_SETTINGS(path)
    assert data["selection"]["options"]["parallel"] == 3 and data["selection"]["options"]["slot_ctx"] == 8192
    assert data["sources"]["parallel"] == data["sources"]["slot_ctx"] == "Saved for this model"
    assert data["recommended"]["options"]["parallel"] == 1 and data["recommended"]["options"]["slot_ctx"] == 0
    [control] = [c for c in data["controls"] if c["name"] == "slot_ctx"]
    assert control["hint"].startswith("sub-agent context: tokens each lane after the first holds")


async def test_the_desktop_starts_sixteen_lanes_and_says_what_it_got(desktop_launch, tmp_path):
    """DREAM-201: the desktop launch takes 16 too, and reports the lanes the engine really came up with (8 here)."""
    _choose(desktop_launch, parallel=16, slot_ctx=16384)
    desktop_launch.lanes = 8
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == [{"temperature": 0.7, "parallel": 16, "slot_ctx": 16384}]
    messages = [message for _, message in desktop_launch.published]
    note = next(m for m in messages if m.startswith("16 lanes: up to 16 requests"))
    assert "lanes 2-16 hold 16,384 tokens each" in note
    assert any(m.startswith("MachX serves 8 lanes, not the 16 asked for") for m in messages)


async def test_the_desktop_starts_lanes_with_the_queue(desktop_launch, tmp_path, monkeypatch):
    """DREAM-209: the desktop launch's real `ie serve` command carries --max-queue with its lanes; an unusable nested
    section no longer stops it (the queue reads no setting)."""
    _write_global({"version": 1, "nested": {"max_workers": 12}})
    _choose(desktop_launch, parallel=4)
    seen = []
    monkeypatch.setattr(machx, "serve", _REAL_SERVE)
    monkeypatch.setattr(machx, "_launch", lambda args, **kw: seen.append(args) or Mock(poll=Mock(return_value=0)))
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    [args] = seen
    assert args[args.index("--parallel") + 1] == "4" and args[args.index("--max-queue") + 1] == "8"


async def test_the_desktop_at_parallel_1_is_unchanged(desktop_launch, tmp_path, monkeypatch):
    monkeypatch.setattr(machx, "served_lanes", lambda timeout=3.0: pytest.fail("no /props read at one lane"))
    _write_global(RETIRED)
    _choose(desktop_launch, slot_ctx=16384)                              # a sub-agent context at one lane: not sent
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == [{"temperature": 0.7, "parallel": 1}]
    assert desktop_launch.saved[0]["options"] == {"temperature": 0.7, "parallel": 1, "slot_ctx": 16384}
    assert not any("lane" in message for _, message in desktop_launch.published)


def _as_v41(desktop_launch, gpus=2):
    desktop_launch.settings = {**desktop_launch.settings, "architecture": "deepseek_v41", "capabilities": dict(V41)}
    desktop_launch.request["selection"] = {**desktop_launch.request["selection"], "gpus": gpus}


async def test_the_desktop_starts_v41_on_its_own_lanes_and_says_so(desktop_launch, tmp_path):
    _as_v41(desktop_launch)
    _choose(desktop_launch, parallel=2)
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == [{"temperature": 0.7, "parallel": 2}]
    messages = [message for _, message in desktop_launch.published]
    note = next(m for m in messages if m.startswith("2 lanes: up to 2 requests"))
    assert "images need 1" not in note and "images still work" in note
    assert "MachX serving 2 lanes: up to 2 requests run at once" in messages


async def test_the_desktop_refuses_v41_lanes_on_one_card_before_loading(desktop_launch, tmp_path):
    _as_v41(desktop_launch, gpus=1)
    _choose(desktop_launch, parallel=2)
    with pytest.raises(ValueError, match="two cards"):
        await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == []


async def test_the_desktop_v41_at_parallel_1_is_unchanged(desktop_launch, tmp_path, monkeypatch):
    monkeypatch.setattr(machx, "served_lanes", lambda timeout=3.0: pytest.fail("no /props read at one lane"))
    _as_v41(desktop_launch)
    await startup.run(desktop_launch.request, tmp_path / "status.json")
    assert desktop_launch.served == [{"temperature": 0.7, "parallel": 1}]
    assert not any("lane" in message for _, message in desktop_launch.published)


def test_the_desktop_memory_note_no_longer_promises_one_request_for_lanes_models(monkeypatch, tmp_path):
    """MiMo since DREAM-151; V4.1 since DREAM-154 (its note says the two cards, not "images need 1")."""
    from dream.local import model_defaults
    path = tmp_path / "MiMo-V2.6-Flash-RL"
    path.mkdir()
    monkeypatch.setattr(model_presets, "model_key", lambda _: "model-key")
    monkeypatch.setattr(model_presets.Presets, "load", lambda self, p: None)
    for caps, images in ((MIMO, True), (V41, False)):
        monkeypatch.setattr(machx, "capabilities", lambda _, caps=caps: dict(caps))
        monkeypatch.setattr(model_defaults, "recommend", lambda p, c: {
            "gpus": 2, "ctx": 32768, "options": {"parallel": 1}, "sources": {"gpus": "x", "ctx": "x", "parallel": "x"},
            "context_limit": None, "notes": []})
        note = startup.model_settings(path)["notes"][0]
        assert "one request at a time" not in note and "engine.parallel" not in note, note
        assert "as many requests at once as its parallel below" in note, note
        assert ("(images need 1)" in note) is images and ("two cards" in note) is not images, note
    _write_global(RETIRED)
    monkeypatch.setattr(machx, "capabilities", lambda _: dict(MIMO))
    data = startup.model_settings(path)
    assert data["sources"]["parallel"] == "x"                            # nothing replaces it at launch any more
