"""Dream's live-Blender launcher with the nested X server swapped for tests/live_blender_fake_x.py.

Everything else is the real launcher: the same sandbox, the same nested-server bubblewrap
(its mounts and flags come from the real nested_display_argv), the same control socket.
Only the program at the end changes, so tests never open a window on anyone's screen.
"""
import sys
from pathlib import Path

from dream.media import blender_live

FAKE = Path(__file__).resolve().with_name("live_blender_fake_x.py")
_real = blender_live.nested_display_argv


def _fake_nested_display_argv(executable, program, **kwargs):
    argv = _real(executable, program, **kwargs)
    cut = argv.index("--")
    assert argv[cut + 1] == program
    return [*argv[:cut], "--ro-bind", str(FAKE), "/tmp/fake_x.py", "--",
            "/usr/bin/python3", "-I", "/tmp/fake_x.py", *argv[cut + 2:]]


blender_live.nested_display_argv = _fake_nested_display_argv
raise SystemExit(blender_live.main(sys.argv[1:]))
