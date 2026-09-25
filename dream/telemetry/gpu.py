"""GPU telemetry sampler — the nvidia-smi replacement for Intel boxes.

Reads what the xe/i915 drivers publish without root:

- **power** from hwmon energy counters (µJ deltas between ticks → watts),
- **temps** (package + VRAM) and **fan** from hwmon,
- **frequency** from the GT freq sysfs,
- **utilization** and **per-process VRAM** from DRM fdinfo — every client's
  ``drm-cycles-<engine>`` (xe) or ``drm-engine-<engine>`` ns (i915) advance
  against a shared clock, so a delta between two sweeps is engine busy-time,
- **VRAM used/total** from an ``xpu-smi --query-gpu`` one-shot at most once
  every ``XPU_SMI_PERIOD_S`` (authoritative; the last value stands in
  between), falling back to the sum of fdinfo residents.

Everything is best-effort: a missing file is a ``None`` field, a missing GPU
vendor is a disabled sampler with a reason, and no failure ever propagates —
the monitor pane degrades, the session never breaks. All the I/O happens on a
daemon thread; the TUI only ever calls ``snapshot()``.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_INTEL_VENDOR = "0x8086"
# Fix #81: one xpu-smi run costs ~0.4 s and logs 33 HECI/MDAPI permission errors (and tries to
# add an OA perf configuration to the xe driver); at the old every-third-tick cadence that was
# ~18 runs and ~590 journal lines a minute. VRAM total never changes and VRAM used is a slow
# signal, so one run per 30 s; the fdinfo sweep (utilization, per-process VRAM) stays per tick.
XPU_SMI_PERIOD_S = 30.0
_XPU_FIELDS = "index,pci.bus_id,name,memory.total,memory.used"
# fdinfo size values arrive as "201180 KiB"; cycles as bare integers.
_SIZE_RE = re.compile(r"^(\d+)\s*(KiB|MiB|GiB)?$")
_SIZE_MIB = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, None: 1 / (1024 * 1024)}


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    s = _read(path)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _bdf_tail(bdf: str) -> str:
    """Normalize a PCI address to its 'bus:dev.fn' tail — xpu-smi and sysfs
    disagree about domain padding ('0000:04:00.0' vs '00000000:04:00.0')."""
    return bdf.strip().lower().split(":", bdf.count(":") - 1)[-1]


@dataclass(frozen=True)
class GpuProc:
    pid: int
    name: str
    vram_mib: float
    util_pct: float | None


@dataclass(frozen=True)
class GpuDeviceSample:
    index: int
    name: str
    bdf: str
    integrated: bool
    vram_total_mib: float | None = None
    vram_used_mib: float | None = None
    power_w: float | None = None
    power_cap_w: float | None = None
    temp_c: float | None = None
    vram_temp_c: float | None = None
    fan_rpm: int | None = None
    freq_mhz: int | None = None
    freq_max_mhz: int | None = None
    util_pct: float | None = None
    procs: tuple[GpuProc, ...] = ()


@dataclass(frozen=True)
class GpuSnapshot:
    ok: bool
    reason: str | None = None
    ts: float = 0.0
    devices: tuple[GpuDeviceSample, ...] = ()


@dataclass
class _Device:
    """Discovery-time facts + the previous tick's counters."""

    index: int
    card: Path  # /sys/class/drm/cardN
    bdf: str
    integrated: bool
    name: str = "Intel GPU"
    vram_total_mib: float | None = None
    hwmon: Path | None = None
    temp_pkg: Path | None = None
    temp_vram: Path | None = None
    energy: Path | None = None
    last_energy_uj: int | None = None
    last_energy_t: float | None = None
    xpu_used_mib: float | None = None


class GpuSampler:
    """Samples every Intel GPU on a daemon thread; the TUI reads snapshot()."""

    def __init__(
        self,
        interval: float = 1.0,
        *,
        drm_root: str | Path = "/sys/class/drm",
        proc_root: str | Path = "/proc",
        xpu_smi: str | None = "xpu-smi",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interval = interval
        self._drm_root = Path(drm_root)
        self._proc_root = Path(proc_root)
        self._xpu_smi = xpu_smi
        self._xpu_at: float | None = None  # clock reading of the last xpu-smi attempt
        self._clock = clock
        self._devices: list[_Device] = []
        self._snapshot = GpuSnapshot(ok=False, reason="not started")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # fdinfo state from the previous sweep, keyed by (pdev, client-id):
        # {engine_class: cycles} plus the shared total per class.
        self._prev_clients: dict[tuple[str, str], dict[str, Any]] = {}
        self._prev_sweep_t: float | None = None
        self._discover()

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None or not self._devices:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gpu-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=2.0)

    def snapshot(self) -> GpuSnapshot:
        with self._lock:
            return self._snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as e:  # a sampler bug must never take the TUI down
                with self._lock:
                    self._snapshot = GpuSnapshot(ok=False, reason=f"sampler error: {e}")
            self._stop.wait(self.interval)

    # --- discovery -----------------------------------------------------------

    def _discover(self) -> None:
        devices: list[_Device] = []
        try:
            cards = sorted(
                p for p in self._drm_root.iterdir() if re.fullmatch(r"card\d+", p.name)
            )
        except OSError:
            cards = []
        for card in cards:
            dev = card / "device"
            if _read(dev / "vendor") != _INTEL_VENDOR:
                continue
            bdf = ""
            for line in (_read(dev / "uevent") or "").splitlines():
                if line.startswith("PCI_SLOT_NAME="):
                    bdf = line.split("=", 1)[1]
            d = _Device(
                index=len(devices),
                card=card,
                bdf=bdf,
                integrated=_bdf_tail(bdf) == "00:02.0",
            )
            hwmons = sorted((dev / "hwmon").glob("hwmon*")) if (dev / "hwmon").is_dir() else []
            if hwmons:
                d.hwmon = hwmons[0]
                for lab in d.hwmon.glob("temp*_label"):
                    label = _read(lab)
                    inp = lab.parent / lab.name.replace("_label", "_input")
                    if label == "pkg":
                        d.temp_pkg = inp
                    elif label == "vram":
                        d.temp_vram = inp
                for lab in d.hwmon.glob("energy*_label"):
                    if _read(lab) == "card":
                        d.energy = lab.parent / lab.name.replace("_label", "_input")
                if d.energy is None:
                    inputs = sorted(d.hwmon.glob("energy*_input"))
                    d.energy = inputs[0] if inputs else None
            devices.append(d)
        self._devices = devices
        if not devices:
            self._snapshot = GpuSnapshot(ok=False, reason="no Intel GPUs found")
            return
        self._enrich_from_xpu_smi(totals=True)

    def _enrich_from_xpu_smi(self, totals: bool = False) -> None:
        """One xpu-smi one-shot: names + VRAM total (at discovery) and VRAM used
        (at most once per XPU_SMI_PERIOD_S; the last value stands in between).
        ~0.4 s of subprocess, so never on the TUI thread."""
        if not self._xpu_smi:
            return
        self._xpu_at = self._clock()  # an attempt, successful or not, starts the period
        try:
            out = subprocess.run(
                [self._xpu_smi, f"--query-gpu={_XPU_FIELDS}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        by_tail = {_bdf_tail(d.bdf): d for d in self._devices}
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            d = by_tail.get(_bdf_tail(parts[1]))
            if d is None:
                continue
            if totals:
                if parts[2]:
                    d.name = _short_name(parts[2])
                try:
                    d.vram_total_mib = float(parts[3])
                except ValueError:
                    pass
            try:
                d.xpu_used_mib = float(parts[4])
            except ValueError:
                pass

    # --- sampling ------------------------------------------------------------

    def sample_once(self) -> GpuSnapshot:
        """One synchronous tick — the thread calls this; tests call it directly."""
        if not self._devices:
            return self.snapshot()
        if self._xpu_at is not None and self._clock() - self._xpu_at >= XPU_SMI_PERIOD_S:
            self._enrich_from_xpu_smi()
        now = self._clock()
        utils, procs, resident = self._sweep_fdinfo(now)
        samples = []
        for d in self._devices:
            tail = _bdf_tail(d.bdf)
            samples.append(GpuDeviceSample(
                index=d.index,
                name=d.name,
                bdf=d.bdf,
                integrated=d.integrated,
                vram_total_mib=d.vram_total_mib,
                vram_used_mib=(
                    d.xpu_used_mib if d.xpu_used_mib is not None else resident.get(tail)
                ),
                power_w=self._power_w(d, now),
                power_cap_w=self._microscale(d.hwmon, "power1_cap", 1e6),
                temp_c=self._millideg(d.temp_pkg),
                vram_temp_c=self._millideg(d.temp_vram),
                fan_rpm=_read_int(d.hwmon / "fan1_input") if d.hwmon else None,
                freq_mhz=self._freq(d, "act_freq"),
                freq_max_mhz=self._freq(d, "rp0_freq"),
                util_pct=utils.get(tail),
                procs=tuple(procs.get(tail, [])),
            ))
        snap = GpuSnapshot(ok=True, ts=now, devices=tuple(samples))
        with self._lock:
            self._snapshot = snap
        return snap

    def _power_w(self, d: _Device, now: float) -> float | None:
        if d.energy is None:
            return None
        uj = _read_int(d.energy)
        if uj is None:
            return None
        prev_uj, prev_t = d.last_energy_uj, d.last_energy_t
        d.last_energy_uj, d.last_energy_t = uj, now
        if prev_uj is None or prev_t is None or now <= prev_t or uj < prev_uj:
            return None  # first tick, or the counter wrapped
        return (uj - prev_uj) / 1e6 / (now - prev_t)

    @staticmethod
    def _microscale(hwmon: Path | None, name: str, scale: float) -> float | None:
        if hwmon is None:
            return None
        v = _read_int(hwmon / name)
        return v / scale if v is not None else None

    @staticmethod
    def _millideg(path: Path | None) -> float | None:
        if path is None:
            return None
        v = _read_int(path)
        return v / 1000.0 if v is not None else None

    def _freq(self, d: _Device, which: str) -> int | None:
        for rel in (f"tile0/gt0/freq0/{which}", f"gt/gt0/freq0/{which}"):
            v = _read_int(d.card / "device" / rel)
            if v is not None:
                return v
        return None

    # --- fdinfo sweep (utilization + per-process VRAM) ------------------------

    def _sweep_fdinfo(
        self, now: float
    ) -> tuple[dict[str, float], dict[str, list[GpuProc]], dict[str, float]]:
        """Walk /proc/*/fd for DRM handles, parse their fdinfo, and diff engine
        counters against the previous sweep. Returns, keyed by device BDF tail:
        utilization %, the per-process table, and total resident VRAM (MiB)."""
        tails = {_bdf_tail(d.bdf) for d in self._devices if d.bdf}
        clients: dict[tuple[str, str], dict[str, Any]] = {}
        try:
            pids = [p for p in os.listdir(self._proc_root) if p.isdigit()]
        except OSError:
            pids = []
        for pid in pids:
            fd_dir = self._proc_root / pid / "fd"
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue  # not ours to read
            for fd in fds:
                try:
                    if not os.readlink(fd_dir / fd).startswith("/dev/dri/"):
                        continue
                except OSError:
                    continue
                info = self._parse_fdinfo(self._proc_root / pid / "fdinfo" / fd)
                if info is None:
                    continue
                tail = _bdf_tail(info["pdev"])
                if tail not in tails:
                    continue
                key = (tail, info["client"])
                cur = clients.get(key)
                if cur is None:
                    info["pid"] = int(pid)
                    clients[key] = info
                # Same client via several fds/pids: counters are identical; the
                # first sighting wins and attribution goes to that pid.

        prev, self._prev_clients = self._prev_clients, clients
        prev_t, self._prev_sweep_t = self._prev_sweep_t, now
        dt_ns = (now - prev_t) * 1e9 if prev_t is not None and now > prev_t else None

        util_by_dev: dict[str, float] = {}  # tail -> summed client busy %
        util_by_pid: dict[tuple[str, int], float] = {}
        vram_by_pid: dict[tuple[str, int], float] = {}
        resident: dict[str, float] = {}
        for (tail, client), info in clients.items():
            pid_key = (tail, info["pid"])
            vram_by_pid[pid_key] = vram_by_pid.get(pid_key, 0.0) + info["vram_mib"]
            resident[tail] = resident.get(tail, 0.0) + info["vram_mib"]
            before = prev.get((tail, client))
            client_util: float | None = None
            for cls, cyc in info["cycles"].items():
                total = info["totals"].get(cls)
                b = before["cycles"].get(cls) if before else None
                b_total = before["totals"].get(cls) if before else None
                if None in (total, b, b_total) or total <= b_total or cyc < b:
                    continue
                pct = (cyc - b) / (total - b_total) * 100.0 / info["caps"].get(cls, 1)
                client_util = max(client_util or 0.0, pct)
            for cls, ns in info["engine_ns"].items():
                b = before["engine_ns"].get(cls) if before else None
                if b is None or dt_ns is None or ns < b:
                    continue
                pct = (ns - b) / dt_ns * 100.0 / info["caps"].get(cls, 1)
                client_util = max(client_util or 0.0, pct)
            if client_util is not None:
                util_by_pid[pid_key] = util_by_pid.get(pid_key, 0.0) + client_util
                util_by_dev[tail] = util_by_dev.get(tail, 0.0) + client_util

        utils = {tail: min(100.0, pct) for tail, pct in util_by_dev.items()}
        procs: dict[str, list[GpuProc]] = {}
        for (tail, pid), vram in vram_by_pid.items():
            util = util_by_pid.get((tail, pid))
            if vram < 1.0 and not util:
                continue  # noise clients (0 MiB, idle)
            name = _read(self._proc_root / str(pid) / "comm") or "?"
            procs.setdefault(tail, []).append(
                GpuProc(pid=pid, name=name, vram_mib=vram, util_pct=util)
            )
        for tail in procs:
            procs[tail].sort(key=lambda p: p.vram_mib, reverse=True)
            del procs[tail][8:]
        return utils, procs, resident

    @staticmethod
    def _parse_fdinfo(path: Path) -> dict[str, Any] | None:
        text = _read(path)
        if text is None or "drm-client-id" not in text:
            return None
        out: dict[str, Any] = {
            "pdev": "", "client": "", "vram_mib": 0.0,
            "cycles": {}, "totals": {}, "caps": {}, "engine_ns": {},
        }
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, val = (s.strip() for s in line.split(":", 1))
            if key == "drm-pdev":
                out["pdev"] = val
            elif key == "drm-client-id":
                out["client"] = val
            elif key.startswith("drm-total-cycles-"):
                out["totals"][key.rsplit("-", 1)[1]] = _to_int(val)
            elif key.startswith("drm-cycles-"):
                out["cycles"][key.rsplit("-", 1)[1]] = _to_int(val)
            elif key.startswith("drm-engine-capacity-"):
                out["caps"][key.rsplit("-", 1)[1]] = _to_int(val) or 1
            elif key.startswith("drm-engine-"):
                out["engine_ns"][key.rsplit("-", 1)[1]] = _to_int(val)
            elif key.startswith("drm-resident-vram"):
                m = _SIZE_RE.match(val)
                if m:
                    out["vram_mib"] += int(m.group(1)) * _SIZE_MIB[m.group(2)]
        out["cycles"] = {k: v for k, v in out["cycles"].items() if v is not None}
        out["totals"] = {k: v for k, v in out["totals"].items() if v is not None}
        out["engine_ns"] = {k: v for k, v in out["engine_ns"].items() if v is not None}
        return out if out["client"] else None


def _to_int(s: str) -> int | None:
    try:
        return int(s.split()[0])
    except (ValueError, IndexError):
        return None


def _short_name(name: str) -> str:
    """'Intel(R) Arc(TM) Pro B70 Graphics' → 'Arc Pro B70'."""
    n = re.sub(r"\((R|TM)\)", "", name)
    n = n.replace("Intel", "").replace("Graphics", "").strip()
    return " ".join(n.split()) or name
