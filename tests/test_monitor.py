"""The right-side monitor pane: row model, both renderers, the two-column
footer/toolbar layouts, the /monitor toggle, and sampler lifecycle."""

from __future__ import annotations

import os
from types import SimpleNamespace

from rich.console import Console

from dream.telemetry.gpu import GpuDeviceSample, GpuProc, GpuSnapshot
from dream.tui import monitor
from dream.tui.render import build_footer


def _gpu() -> GpuSnapshot:
    return GpuSnapshot(ok=True, ts=1.0, devices=(
        GpuDeviceSample(
            index=0, name="Intel", bdf="0000:00:02.0", integrated=True,
            vram_total_mib=145078.0, vram_used_mib=23533.0,
        ),
        GpuDeviceSample(
            index=1, name="Arc Pro B70", bdf="0000:04:00.0", integrated=False,
            vram_total_mib=32656.0, vram_used_mib=21300.0, power_w=187.0,
            power_cap_w=230.0, temp_c=54.0, vram_temp_c=56.0, fan_rpm=1200,
            freq_mhz=2400, freq_max_mhz=2800, util_pct=82.0,
            procs=(GpuProc(pid=1433, name="llama-server", vram_mib=18600.0,
                           util_pct=81.0),),
        ),
    ))


def _meter() -> dict:
    return {
        "state": "decoding", "ttft_s": 0.82, "round_ttft_s": 0.5, "pp_time_s": 0.5,
        "pp_tps": 1843.0, "gen_tps": 42.3, "live_tps": 41.0, "out_tokens_est": 512,
        "prompt_tokens": 5000, "elapsed_s": 12.0,
        "last_turn": {"duration_s": 30.0, "ttft_s": 0.9, "pp_tps": 1800.0,
                      "gen_tps": 40.0, "in_tokens": 5000, "out_tokens": 900,
                      "ctx_used": 34800, "n_ctx": 120000},
        "session": {"turns": 3, "in_tokens": 5200, "out_tokens": 1800,
                    "gen_s": 47.0, "avg_tps": 38.3},
    }


def _plain(rows) -> str:
    return "\n".join("".join(s for s, _ in row) for row in rows)


def test_rows_carry_gpu_and_inference_numbers():
    text = _plain(monitor.build_rows(_gpu(), _meter()))
    for expected in ("GPU1", "Arc Pro B70", "82%", "187W", "54°", "20.8/31.9G",
                     "fan 1200 rpm", "igpu", "shared", "llama-server 18.2G",
                     "decoding 12s", "~41.0 t/s live",
                     "ttft 0.82s", "pp 0.50s @1843 t/s", "gen 42.3 t/s",
                     "out ~512 tok", "ctx 34.8k/120.0k (29%)", "Σ 3t",
                     "avg 38.3 t/s"):
        assert expected in text, expected


def test_core_rows_fit_the_pane_unclipped():
    """The numbers the user reads (GPU line, ttft/pp, gen, ctx, Σ) must never be
    ellipsized at pane width — only the procs line may clip."""
    rows = monitor.build_rows(_gpu(), _meter())
    for row in rows:
        plain = "".join(s for s, _ in row)
        if "llama-server" in plain:
            continue  # the procs line is allowed to clip
        assert len(plain) <= monitor.PANE_WIDTH, plain


def test_discrete_gpus_render_before_integrated():
    text = _plain(monitor.build_rows(_gpu(), _meter()))
    assert text.index("GPU1") < text.index("igpu")


def test_gpu_absent_shows_reason_and_inference_survives():
    snap = GpuSnapshot(ok=False, reason="no Intel GPUs found")
    text = _plain(monitor.build_rows(snap, _meter()))
    assert "no Intel GPUs found" in text and "gen 42.3 t/s" in text
    text = _plain(monitor.build_rows(None, _meter()))
    assert "sampler off" in text


def test_idle_before_first_turn():
    im = {"state": "idle", "last_turn": None,
          "session": {"turns": 0, "in_tokens": 0, "out_tokens": 0, "gen_s": 0.0,
                      "avg_tps": None}}
    text = _plain(monitor.build_rows(_gpu(), im))
    assert "metrics arrive with the first turn" in text


def test_rich_lines_clip_to_pane_width():
    rows = [[("x" * 200, None)]]
    lines = monitor.rich_lines(rows)
    assert len(lines) == 1
    assert lines[0].cell_len <= monitor.PANE_WIDTH


def test_pt_lines_escape_and_measure():
    html, plain = monitor.pt_lines([[("a<b>", "#ffffff"), ("cd", None)]])[0]
    assert "a&lt;b&gt;" in html and "cd" in html
    assert plain == 6
    html, plain = monitor.pt_lines([[("y" * 200, None)]])[0]
    assert plain == monitor.PANE_WIDTH  # clipped, and measured as clipped


def test_compose_pt_pads_columns():
    body = monitor.compose_pt([("LL", 2)], [("RR", 2)], width=100)
    left_w = 100 - monitor.PANE_WIDTH - 2
    assert body == "LL" + " " * (left_w - 2) + "RR"
    # over-long left line goes ragged (1 space), never truncated
    body = monitor.compose_pt([("L" * 90, 90)], [("RR", 2)], width=100)
    assert body == "L" * 90 + " " + "RR"
    # right column longer than left: left side is blank-padded
    body = monitor.compose_pt([], [("RR", 2)], width=100)
    assert body.endswith("RR")


def test_compact_strip():
    s = monitor.compact_strip(_gpu(), _meter())
    assert "~41" in s and "ttft 0.82s" in s and "GPU1 82% 187W" in s
    assert "fan 1200" in s
    assert "igpu" not in s  # compact strip: discrete cards only


def _footer_data(**over):
    d = {
        "mode_label": "auto-edit", "mode_color": "#34d399", "ws": "~/x",
        "created": 0, "edited": 0, "deleted": 0, "tools": 0,
        "tokens": "tokens Σ 0.0k", "recent": "no file activity yet",
        "mind": None, "mind_plain": "", "plan_strip": None, "plan_plain": "",
        "plan_panel": None, "friction_spark": "", "friction_label": "",
        "friction_reason": "",
        "monitor": monitor.rich_lines(monitor.build_rows(_gpu(), _meter())),
        "monitor_compact": monitor.compact_strip(_gpu(), _meter()),
    }
    d.update(over)
    return d


def _render(d, width):
    console = Console(width=width, record=True)
    console.print(build_footer(d, width))
    return console.export_text()


def test_footer_two_columns_when_wide():
    text = _render(_footer_data(), 120)
    assert "⚡ monitor" in text
    lines = text.splitlines()
    # the monitor rows sit to the RIGHT of the cockpit rows, on the same lines
    assert any("edited 0" in ln and "GPU1" in ln for ln in lines)
    assert any("auto-edit" in ln and "monitor" in ln for ln in lines)


def test_footer_compact_when_narrow():
    text = _render(_footer_data(), 80)
    assert "⚡ monitor" not in text
    assert "GPU1 82% 187W" in text  # the compact strip carries the essentials


def test_footer_unchanged_when_monitor_off():
    text = _render(_footer_data(monitor=None, monitor_compact=None), 120)
    assert "monitor" not in text and "GPU1" not in text


class _FakeSampler:
    def __init__(self):
        self.started = False
        self.stopped = False

    def snapshot(self):
        return _gpu()

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _app(tmp_path):
    from dream.tui.app import App

    app = App(provider="machx", model="m", workspace=tmp_path)
    app.engine = SimpleNamespace(
        last_context_tokens=None, session_tokens=0, total_cost_usd=0.0,
        effort=None, backend=SimpleNamespace(n_ctx=None), store=object(),
    )
    app.gpu = _FakeSampler()
    app.show_monitor = True
    return app


def test_status_data_builds_monitor(tmp_path):
    app = _app(tmp_path)
    d = app._status_data()
    assert d["monitor"] is not None and d["monitor_pt"] is not None
    assert any("GPU1" in t.plain for t in d["monitor"])
    app.show_monitor = False
    d = app._status_data()
    assert d["monitor"] is None and d["monitor_compact"] is None


def test_toolbar_two_columns_wide(tmp_path, monkeypatch):
    import shutil as shutil_mod

    app = _app(tmp_path)
    monkeypatch.setattr(shutil_mod, "get_terminal_size",
                        lambda fallback=(80, 24): os.terminal_size((120, 40)))
    value = app._toolbar().value
    assert "⚡ monitor" in value and "GPU1" in value
    assert "⏵⏵" in value  # the left cockpit is intact

    monkeypatch.setattr(shutil_mod, "get_terminal_size",
                        lambda fallback=(80, 24): os.terminal_size((80, 40)))
    value = app._toolbar().value
    assert "⚡ monitor" not in value
    assert "GPU1 82% 187W" in value  # compact strip below the cockpit


async def test_monitor_command_toggles_and_stops_sampler(tmp_path, monkeypatch):
    app = _app(tmp_path)
    fake = app.gpu

    async def no_start():
        app.gpu = app.gpu or _FakeSampler()

    monkeypatch.setattr(app, "_start_monitor", no_start)
    assert not await app._command("/monitor off")
    assert app.show_monitor is False and fake.stopped and app.gpu is None
    assert not await app._command("/monitor on")
    assert app.show_monitor is True and app.gpu is not None
    assert not await app._command("/monitor")  # bare = toggle
    assert app.show_monitor is False
    assert not await app._command("/monitor sideways")  # usage, no state change
    assert app.show_monitor is False


async def test_shutdown_stops_sampler(tmp_path):
    app = _app(tmp_path)
    fake = app.gpu
    app.engine = None  # even a half-booted app must stop the sampler thread
    await app._shutdown()
    assert fake.stopped and app.gpu is None
