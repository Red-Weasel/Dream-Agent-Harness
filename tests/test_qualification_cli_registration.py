"""DREAM-246: fixed CLI qualification covers and binds bridge regressions."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_qualification_managed_gates import qualifier, checkout


FIXTURES = (
    "tests/test_session_navigation_ui.py",
    "tests/test_session_navigation_mobile_ui.py",
    "tests/test_antigravity_adapter.py",
    "tests/test_antigravity_routing.py",
    "tests/test_antigravity_integration.py",
    "tests/test_council_login_ui.py",
    "tests/test_provider_login.py",
    "tests/test_picker.py",
    "tests/test_council_startup.py",
    "tests/test_gemini_mcp_registration.py",
    "tests/test_gemini_mcp_session_scope.py",
    "tests/test_engine_start_cleanup.py",
    "tests/test_council_native_controls.py",
    "tests/test_council_requested_catalog.py",
    "tests/test_model_catalog.py",
    "tests/test_council_cli_parity.py",
    "tests/test_cli_discovery.py",
    "tests/test_council_config.py",
    "tests/test_gemini_thinking_overlay.py",
    "tests/test_gemini_thinking_integration.py",
    "tests/test_council_ui.py",
)


def test_cli_gate_executes_all_registration_and_startup_regressions(
        qualifier, checkout, tmp_path, monkeypatch):
    calls = []

    def process(argv, **kwargs):
        calls.append(argv)
        actual = {arg for arg in argv if arg.startswith("tests/")}
        assert set(FIXTURES) <= actual, "CLI qualification omits required bridge regressions"
        assert actual == set(qualifier.GATES["cli_sdk"])
        assert kwargs["cwd"] == checkout
        junit = Path(next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml=")))
        junit.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0">'
                         '<testcase classname="synthetic" name="cli_contract"/></testsuite>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(qualifier.subprocess, "run", process)
    output = tmp_path / "qualification.json"
    assert qualifier.main(["--gate", "cli_sdk", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert len(calls) == 1
    assert report["gates"]["cli_sdk"]["status"] == "verified"
    assert set(FIXTURES) <= report["source_sha256"].keys()


@pytest.mark.parametrize("source", FIXTURES)
@pytest.mark.parametrize("mutation", ["drift", "missing"])
def test_changed_or_missing_cli_regression_invalidates_passing_gate(
        qualifier, checkout, tmp_path, monkeypatch, source, mutation):
    target = checkout / source
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic CLI regression bytes\n")
    original = hashlib.sha256(target.read_bytes()).hexdigest()
    if mutation == "missing":
        target.unlink()
    calls = []

    def gate(name, *, timeout):
        calls.append(name)
        if mutation == "drift":
            target.write_text("changed fixture during otherwise successful qualification\n")
        return dict(status="verified", exit_code=0, tests=1, failures=[], errors=[], skips=[])

    monkeypatch.setattr(qualifier, "run_gate", gate)
    output = tmp_path / "qualification.json"
    assert qualifier.main(["--gate", "cli_sdk", "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert calls == ["cli_sdk"]
    assert report["gates"]["cli_sdk"]["status"] == "verified"
    assert report["source_sha256"][source] == (original if mutation == "drift" else None)
    assert report["source_consistency"]["status"] == "failed"
    assert report["source_consistency"]["changed_paths"] == ([source] if mutation == "drift" else [])
    assert report["live_provider_inference"] == {"status": "not_run"}
    assert report["owner_desktop_acceptance"] == {"status": "not_run"}
