"""scripts/harness_report.py's schema gate, CI's "Measure deterministic harness fixtures" step (DREAM-150).

At 8K the tools array is the mandatory floor alone (the pinned schemas and the lookup tool), exempt from the 15 % limit
only there and only while it is exactly the floor (DREAM-035); from 16K up the whole array stays within 15 %. Each case
runs the real report in its own process, because the report redirects Dream's config for its run, with one change made
in that process only; the measurements go to a temporary file, never to the checkout's artifacts/."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

RUN = r'''
import contextlib, importlib.util, io, json, sys
from pathlib import Path
case, output, script = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
if case == "pinned_bloat":                  # a pinned schema grows: read_file's description, by 2,000 words
    from dream.tools.native import read_file
    read_file.description += " filler" * 2000
elif case == "optional_at_8k":              # the 8K array carries one optional schema on top of the floor
    from dream.core.backends import openai_compat
    request = openai_compat.OpenAICompatBackend._request_tools
    def with_optional(self):
        sent = request(self)
        if self.n_ctx == 8192:
            names = {s["function"]["name"] for s in sent}
            sent = sent + [next(s for s in self.tool_schemas if s["function"]["name"] not in self._pinned | names)]
        return sent
    openai_compat.OpenAICompatBackend._request_tools = with_optional
spec = importlib.util.spec_from_file_location("harness_report", script)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
with contextlib.redirect_stdout(io.StringIO()):
    code = report.main(output)
rows = {row["window"]: row for row in json.loads(output.read_text())["context"]}
print(json.dumps({"exit": code, "rows": rows}))
'''


def _report(tmp_path, case):
    result = subprocess.run([sys.executable, "-c", RUN, case, str(tmp_path / "measurements.json"),
                             str(ROOT / "scripts" / "harness_report.py")],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    return outcome["exit"], {int(window): row for window, row in outcome["rows"].items()}


def test_the_current_toolset_passes_with_the_8k_floor_exempt(tmp_path):
    code, rows = _report(tmp_path, "unchanged")
    assert code == 0, rows
    assert rows[8192]["floor_exempt"] and rows[8192]["within_limit"]
    assert not any(rows[window]["floor_exempt"] for window in (16384, 32768, 131072))


def test_a_bloated_pinned_schema_fails_at_16k(tmp_path):
    """The exemption stops at 8K: the same floor must fit 15 % of a 16K window, so a pinned schema cannot grow unseen."""
    code, rows = _report(tmp_path, "pinned_bloat")
    assert code == 1
    assert rows[8192]["within_limit"]                        # still exactly the floor at 8K
    assert not rows[16384]["within_limit"] and rows[16384]["share"] > .15


def test_an_optional_schema_at_8k_fails(tmp_path):
    """Exempt means exactly the floor: an optional schema sent beside it at 8K fails, whatever the share."""
    code, rows = _report(tmp_path, "optional_at_8k")
    assert code == 1
    assert not rows[8192]["within_limit"]
    assert all(rows[window]["within_limit"] for window in (16384, 32768, 131072))


@pytest.fixture(autouse=True)
def _never_the_checkout(tmp_path):
    """The report writes where it is told; the checkout's artifacts/ stay untouched."""
    target = ROOT / "artifacts" / "harness-validation" / "measurements.json"
    before = target.stat().st_mtime_ns if target.exists() else None
    yield
    assert (target.stat().st_mtime_ns if target.exists() else None) == before
