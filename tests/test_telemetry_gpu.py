"""GpuSampler against a fake sysfs/proc tree: every number the monitor pane
shows must be derivable, and every missing file must degrade to None."""

from __future__ import annotations

import os
import time

import pytest

from dream.telemetry.gpu import GpuSampler, _bdf_tail, _short_name

BDF = "0000:04:00.0"


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def make_card(drm, name="card2", bdf=BDF, vendor="0x8086", hwmon=True, freq=True):
    dev = drm / name / "device"
    dev.mkdir(parents=True)
    (dev / "vendor").write_text(vendor + "\n")
    (dev / "uevent").write_text(f"DRIVER=xe\nPCI_SLOT_NAME={bdf}\n")
    if hwmon:
        hm = dev / "hwmon" / "hwmon7"
        hm.mkdir(parents=True)
        (hm / "temp2_label").write_text("pkg\n")
        (hm / "temp2_input").write_text("44000\n")
        (hm / "temp3_label").write_text("vram\n")
        (hm / "temp3_input").write_text("46000\n")
        (hm / "energy1_label").write_text("card\n")
        (hm / "energy1_input").write_text("1000000000\n")  # 1000 J
        (hm / "fan1_input").write_text("780\n")
        (hm / "power1_cap").write_text("230000000\n")  # 230 W in µW
    if freq:
        fr = dev / "tile0" / "gt0" / "freq0"
        fr.mkdir(parents=True)
        (fr / "act_freq").write_text("2400\n")
        (fr / "rp0_freq").write_text("2800\n")
    return dev


def make_client(
    proc, pid, fd="5", *, comm="llama-server", pdev=BDF, client="42",
    cycles=None, totals=None, caps=None, engine_ns=None, vram_kib=0,
):
    p = proc / str(pid)
    (p / "fd").mkdir(parents=True, exist_ok=True)
    (p / "fdinfo").mkdir(parents=True, exist_ok=True)
    (p / "comm").write_text(comm + "\n")
    link = p / "fd" / fd
    if not link.is_symlink():
        os.symlink("/dev/dri/card2", link)  # dangling target is fine
    lines = ["drm-driver:\txe", f"drm-client-id:\t{client}", f"drm-pdev:\t{pdev}"]
    for cls, v in (cycles or {}).items():
        lines.append(f"drm-cycles-{cls}:\t{v}")
    for cls, v in (totals or {}).items():
        lines.append(f"drm-total-cycles-{cls}:\t{v}")
    for cls, v in (caps or {}).items():
        lines.append(f"drm-engine-capacity-{cls}:\t{v}")
    for cls, v in (engine_ns or {}).items():
        lines.append(f"drm-engine-{cls}:\t{v} ns")
    if vram_kib:
        lines.append(f"drm-resident-vram0:\t{vram_kib} KiB")
    (p / "fdinfo" / fd).write_text("\n".join(lines) + "\n")


@pytest.fixture()
def roots(tmp_path):
    drm = tmp_path / "drm"
    proc = tmp_path / "proc"
    drm.mkdir()
    proc.mkdir()
    return drm, proc


def make_sampler(drm, proc, clock):
    return GpuSampler(drm_root=drm, proc_root=proc, xpu_smi=None, clock=clock)


def test_no_intel_gpus_disables_cleanly(roots):
    drm, proc = roots
    make_card(drm, vendor="0x10de")  # not Intel
    s = make_sampler(drm, proc, Clock())
    snap = s.snapshot()
    assert not snap.ok
    assert "no Intel GPUs" in snap.reason
    s.start()  # must be a no-op, not a crash
    assert s._thread is None


def test_sensors_power_temp_fan_freq(roots):
    drm, proc = roots
    dev = make_card(drm)
    clock = Clock()
    s = make_sampler(drm, proc, clock)
    snap = s.sample_once()
    d = snap.devices[0]
    assert snap.ok and d.bdf == BDF and not d.integrated
    assert d.temp_c == 44.0 and d.vram_temp_c == 46.0
    assert d.fan_rpm == 780 and d.power_cap_w == 230.0
    assert d.freq_mhz == 2400 and d.freq_max_mhz == 2800
    assert d.power_w is None  # first tick: no energy delta yet

    hm = dev / "hwmon" / "hwmon7"
    (hm / "energy1_input").write_text("1100000000\n")  # +100 J
    clock.t += 2.0
    d = s.sample_once().devices[0]
    assert d.power_w == pytest.approx(50.0)  # 100 J / 2 s


def test_energy_counter_wrap_is_not_negative_power(roots):
    drm, proc = roots
    dev = make_card(drm)
    clock = Clock()
    s = make_sampler(drm, proc, clock)
    s.sample_once()
    (dev / "hwmon" / "hwmon7" / "energy1_input").write_text("5\n")  # wrapped
    clock.t += 1.0
    assert s.sample_once().devices[0].power_w is None


def test_missing_hwmon_and_freq_degrade_to_none(roots):
    drm, proc = roots
    make_card(drm, hwmon=False, freq=False)
    s = make_sampler(drm, proc, Clock())
    d = s.sample_once().devices[0]
    assert d.power_w is None and d.temp_c is None and d.fan_rpm is None
    assert d.freq_mhz is None and d.util_pct is None


def test_xe_cycles_utilization_and_procs(roots):
    drm, proc = roots
    make_card(drm)
    clock = Clock()
    make_client(proc, 100, cycles={"rcs": 0}, totals={"rcs": 1000}, vram_kib=2048 * 1024)
    s = make_sampler(drm, proc, clock)
    snap = s.sample_once()
    d = snap.devices[0]
    assert d.util_pct is None  # first sweep has no baseline
    assert d.vram_used_mib == pytest.approx(2048.0)  # fdinfo resident fallback

    make_client(proc, 100, cycles={"rcs": 500}, totals={"rcs": 2000}, vram_kib=2048 * 1024)
    clock.t += 1.0
    d = s.sample_once().devices[0]
    assert d.util_pct == pytest.approx(50.0)  # Δ500 busy of Δ1000 total
    assert len(d.procs) == 1
    p = d.procs[0]
    assert p.pid == 100 and p.name == "llama-server"
    assert p.vram_mib == pytest.approx(2048.0)
    assert p.util_pct == pytest.approx(50.0)


def test_engine_capacity_normalizes_utilization(roots):
    drm, proc = roots
    make_card(drm)
    clock = Clock()
    kw = dict(totals={"vcs": 1000}, caps={"vcs": 2})
    make_client(proc, 100, cycles={"vcs": 0}, **kw)
    s = make_sampler(drm, proc, clock)
    s.sample_once()
    make_client(proc, 100, cycles={"vcs": 1000}, totals={"vcs": 2000}, caps={"vcs": 2})
    clock.t += 1.0
    # full busy on one of two engines → 50 %
    assert s.sample_once().devices[0].util_pct == pytest.approx(50.0)


def test_i915_engine_ns_utilization(roots):
    drm, proc = roots
    make_card(drm)
    clock = Clock()
    make_client(proc, 200, comm="Xorg", engine_ns={"render": 0}, vram_kib=1024)
    s = make_sampler(drm, proc, clock)
    s.sample_once()
    make_client(proc, 200, comm="Xorg", engine_ns={"render": 1_000_000_000}, vram_kib=1024)
    clock.t += 2.0
    d = s.sample_once().devices[0]
    assert d.util_pct == pytest.approx(50.0)  # 1 s busy over 2 s wall


def test_multiple_clients_sum_and_cap(roots):
    drm, proc = roots
    make_card(drm)
    clock = Clock()
    make_client(proc, 100, client="1", cycles={"rcs": 0}, totals={"rcs": 1000}, vram_kib=1024)
    make_client(proc, 300, client="2", cycles={"rcs": 0}, totals={"rcs": 1000}, vram_kib=1024)
    s = make_sampler(drm, proc, clock)
    s.sample_once()
    make_client(proc, 100, client="1", cycles={"rcs": 900}, totals={"rcs": 2000}, vram_kib=1024)
    make_client(proc, 300, client="2", cycles={"rcs": 800}, totals={"rcs": 2000}, vram_kib=1024)
    clock.t += 1.0
    d = s.sample_once().devices[0]
    assert d.util_pct == 100.0  # 90 % + 80 % capped
    assert {p.pid for p in d.procs} == {100, 300}


def test_same_client_seen_twice_counts_once(roots):
    drm, proc = roots
    make_card(drm)
    clock = Clock()
    # One DRM client visible through two fds — must not double VRAM.
    make_client(proc, 100, fd="5", client="7", cycles={"rcs": 0}, totals={"rcs": 1000},
                vram_kib=1024 * 1024)
    make_client(proc, 100, fd="6", client="7", cycles={"rcs": 0}, totals={"rcs": 1000},
                vram_kib=1024 * 1024)
    s = make_sampler(drm, proc, clock)
    d = s.sample_once().devices[0]
    assert d.vram_used_mib == pytest.approx(1024.0)


def test_integrated_flag(roots):
    drm, proc = roots
    make_card(drm, name="card1", bdf="0000:00:02.0")
    s = make_sampler(drm, proc, Clock())
    assert s.sample_once().devices[0].integrated


def test_xpu_smi_enrichment(roots, tmp_path):
    drm, proc = roots
    make_card(drm)
    stub = tmp_path / "xpu-smi-stub"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "0, 0000:04:00.0, Intel(R) Arc(TM) Pro B70 Graphics, 32656, 895.5"\n'
    )
    stub.chmod(0o755)
    s = GpuSampler(drm_root=drm, proc_root=proc, xpu_smi=str(stub), clock=Clock())
    d = s.sample_once().devices[0]
    assert d.name == "Arc Pro B70"
    assert d.vram_total_mib == pytest.approx(32656.0)
    assert d.vram_used_mib == pytest.approx(895.5)  # authoritative over fdinfo


def test_xpu_refresh_cadence_every_1(roots, tmp_path):
    drm, proc = roots
    make_card(drm)
    stub = tmp_path / "xpu-smi-stub"
    marker = tmp_path / "calls"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo x >> "{marker}"\n'
        'echo "0, 0000:04:00.0, GPU, 32656, 100"\n'
    )
    stub.chmod(0o755)
    s = GpuSampler(drm_root=drm, proc_root=proc, xpu_smi=str(stub), xpu_every=1,
                   clock=Clock())
    for _ in range(3):
        s.sample_once()
    # discovery + ticks 2 and 3 (xpu_every=1 refreshes every tick after the first)
    assert marker.read_text().count("x") == 3


def test_thread_starts_and_stops(roots):
    drm, proc = roots
    make_card(drm)
    s = GpuSampler(0.05, drm_root=drm, proc_root=proc, xpu_smi=None)
    s.start()
    thread = s._thread
    assert thread is not None and thread.is_alive()
    deadline = time.time() + 2.0
    while time.time() < deadline and not s.snapshot().ok:
        time.sleep(0.02)
    assert s.snapshot().ok
    s.stop()
    assert not thread.is_alive()
    s.stop()  # idempotent


def test_helpers():
    assert _bdf_tail("00000000:04:00.0") == _bdf_tail("0000:04:00.0")
    assert _short_name("Intel(R) Arc(TM) Pro B70 Graphics") == "Arc Pro B70"
    assert _short_name("Intel(R) Graphics") != ""
