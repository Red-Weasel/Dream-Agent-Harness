"""Pre-discovery guard regressions; no negative case touches real GPU roots."""

from types import SimpleNamespace

import pytest

from dream.telemetry.gpu import GpuSampler
from dream.tui.app import App
from telemetry_guard import guard_telemetry


@pytest.mark.parametrize("changes", [
    {},
    {"drm_root": "owned"},
    {"proc_root": "owned"},
    {"drm_root": "/sys/class/drm", "proc_root": "owned", "xpu_smi": None},
    {"drm_root": "owned", "proc_root": "/proc", "xpu_smi": None},
    {"drm_root": "owned", "proc_root": "owned"},
    {"drm_root": "owned", "proc_root": "owned", "xpu_smi": "xpu-smi"},
    {"drm_root": "owned", "proc_root": "owned", "xpu_smi": "/usr/bin/xpu-smi"},
])
def test_forbidden_constructor_fails_even_if_exception_is_swallowed(tmp_path, monkeypatch, changes):
    discoveries = []
    monkeypatch.setattr(GpuSampler, "_discover", lambda self: discoveries.append(self))
    kwargs = {key: tmp_path / key if value == "owned" else value
              for key, value in changes.items()}
    with pytest.raises(AssertionError, match="forbidden hardware telemetry attempts"):
        with guard_telemetry(tmp_path) as guard:
            try:
                GpuSampler(**kwargs)
            except AssertionError:
                pass
            assert len(guard.attempts) == 1
            assert not discoveries
    assert not discoveries


def test_symlink_outside_fixture_is_rejected(tmp_path, monkeypatch):
    discoveries = []
    monkeypatch.setattr(GpuSampler, "_discover", lambda self: discoveries.append(self))
    # Resolve an owned link to an ordinary parent directory, never to hardware.
    link = tmp_path / "outside"
    link.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(AssertionError, match="forbidden hardware telemetry attempts"):
        with guard_telemetry(tmp_path):
            try:
                GpuSampler(drm_root=link, proc_root=tmp_path / "proc", xpu_smi=None)
            except AssertionError:
                pass
    assert not discoveries


async def test_old_monitor_start_is_detected_after_app_swallows_exception(tmp_path, monkeypatch):
    discoveries = []
    monkeypatch.setattr(GpuSampler, "_discover", lambda self: discoveries.append(self))
    # This is the old /new fixture's real monitor-start path, without Engine/GUI.
    app = SimpleNamespace(show_monitor=True, gpu=None)
    with pytest.raises(AssertionError, match="forbidden hardware telemetry attempts"):
        with guard_telemetry(tmp_path) as guard:
            await App._start_monitor(app)
            assert app.gpu is None
            assert len(guard.attempts) == 1
            assert not discoveries


def test_guard_finalizer_stops_fixture_thread_on_failure(tmp_path):
    drm, proc = tmp_path / "drm", tmp_path / "proc"
    device = drm / "card0" / "device"
    device.mkdir(parents=True)
    proc.mkdir()
    (device / "vendor").write_text("0x8086\n")
    with pytest.raises(RuntimeError, match="fixture failed"):
        with guard_telemetry(tmp_path):
            sampler = GpuSampler(0.01, drm_root=drm, proc_root=proc, xpu_smi=None)
            sampler.start()
            thread = sampler._thread
            assert thread is not None and thread.is_alive()
            raise RuntimeError("fixture failed")
    assert not thread.is_alive()
