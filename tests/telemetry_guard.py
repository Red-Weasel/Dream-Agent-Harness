"""Test-only interception of GpuSampler construction, shared with PTY children.

This guards the sampler entry point, not arbitrary filesystem or subprocess I/O.
Only explicit paths inside the supplied fixture directory may reach real sampling.
"""

from contextlib import contextmanager
from pathlib import Path

from dream.telemetry.gpu import GpuSampler


class TelemetryGuard:
    def __init__(self, fixture_root):
        self.root = Path(fixture_root).resolve()
        self.attempts = []
        self.samplers = []

    def _owned_path(self, value):
        if not isinstance(value, (str, Path)):
            return False
        path = Path(value)
        # Reject host/default paths lexically, before resolving any such path.
        if ".." in path.parts or not path.is_absolute() or not path.is_relative_to(self.root):
            return False
        return path.resolve().is_relative_to(self.root)

    def check(self, kwargs):
        safe = (self._owned_path(kwargs.get("drm_root"))
                and self._owned_path(kwargs.get("proc_root"))
                and "xpu_smi" in kwargs
                and (kwargs["xpu_smi"] is None or self._owned_path(kwargs["xpu_smi"])))
        if not safe:
            self.attempts.append(dict(kwargs))
            raise AssertionError("hardware telemetry construction blocked before discovery")

    def assert_clean(self):
        assert not self.attempts, f"forbidden hardware telemetry attempts: {self.attempts!r}"

    def stop_samplers(self):
        threads = [sampler._thread for sampler in self.samplers]
        for sampler in self.samplers:
            sampler.stop()
        assert all(thread is None or not thread.is_alive() for thread in threads), \
            "fixture GPU sampler thread survived stop"


@contextmanager
def guard_telemetry(fixture_root):
    guard = TelemetryGuard(fixture_root)
    original = GpuSampler.__init__

    def guarded_init(sampler, *args, **kwargs):
        guard.check(kwargs)
        original(sampler, *args, **kwargs)
        guard.samplers.append(sampler)

    # Patch the constructor itself: previously imported class aliases are covered.
    GpuSampler.__init__ = guarded_init
    try:
        yield guard
    finally:
        try:
            guard.stop_samplers()
            guard.assert_clean()  # startup may have swallowed the raised Exception
        finally:
            GpuSampler.__init__ = original
