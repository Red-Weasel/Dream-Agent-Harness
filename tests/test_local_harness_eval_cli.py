"""Local evaluator CLI contract; launch is mocked, never a child or endpoint."""

import builtins
import json

import pytest

from dream import harness_eval, local_harness_eval


@pytest.fixture
def launch_calls(monkeypatch):
    calls = []

    def launch(config, run_dir):
        calls.append((config, run_dir))
        return {"passed": True, "origin": "synthetic-cli-fixture"}

    monkeypatch.setattr(local_harness_eval, "launch", launch)
    return calls


def local_args(tmp_path):
    return ["--local", "--endpoint", "http://127.0.0.1:12345/v1",
            "--model", "fixture-model", "--run-dir", str(tmp_path / "new-run")]


@pytest.mark.parametrize("missing", ["--endpoint", "--model", "--run-dir"])
def test_local_requires_all_explicit_targets(tmp_path, launch_calls, missing):
    argv = local_args(tmp_path)
    index = argv.index(missing)
    del argv[index:index + 2]
    with pytest.raises(SystemExit) as exc:
        harness_eval.main(argv)
    assert exc.value.code == 2
    assert launch_calls == []


@pytest.mark.parametrize("option,value", [
    ("--endpoint", "http://127.0.0.1:12345/v1"), ("--model", "fixture-model"),
    ("--run-dir", "new-run"), ("--discovery-mode", "descriptions"),
    ("--max-requests", "8"), ("--max-wall-seconds", "90"),
    ("--catalog-mode", "builtin_tools"),
])
def test_local_options_require_explicit_opt_in(launch_calls, option, value):
    with pytest.raises(SystemExit) as exc:
        harness_eval.main([option, value])
    assert exc.value.code == 2
    assert launch_calls == []


@pytest.mark.parametrize("conflict", [["--list"], ["--grade", "records.json"], ["--export", "records.json"]])
def test_local_conflicts_with_other_actions(tmp_path, launch_calls, conflict):
    with pytest.raises(SystemExit) as exc:
        harness_eval.main(local_args(tmp_path) + conflict)
    assert exc.value.code == 2
    assert launch_calls == []


@pytest.mark.parametrize("explicit", [False, True])
def test_valid_local_forwards_pinned_configuration(tmp_path, launch_calls, capsys, explicit):
    argv = local_args(tmp_path)
    if explicit:
        argv += ["--discovery-mode", "descriptions", "--max-requests", "8", "--max-wall-seconds", "90",
                 "--catalog-mode", "builtin_tools"]
    assert harness_eval.main(argv) == 0
    assert len(launch_calls) == 1
    config, run_dir = launch_calls[0]
    assert config.endpoint == "http://127.0.0.1:12345/v1"
    assert config.model == "fixture-model"
    assert config.catalog_mode == ("builtin_tools" if explicit else "fixture")
    assert run_dir == tmp_path / "new-run"
    assert (config.discovery_mode, config.max_requests, config.max_wall_seconds) == (
        ("descriptions", 8, 90) if explicit else ("default", 24, 300))
    assert json.loads(capsys.readouterr().out) == {"passed": True, "origin": "synthetic-cli-fixture"}
    assert not run_dir.exists()


def test_packaged_skills_catalog_cli_forwards_explicit_mode(tmp_path, launch_calls, capsys):
    assert harness_eval.main(local_args(tmp_path) + ['--catalog-mode', 'builtin_tools_skills']) == 0
    assert launch_calls[0][0].catalog_mode == 'builtin_tools_skills'


@pytest.mark.parametrize("report,expected", [({"passed": True}, 0), ({"passed": False}, 1), ({}, 1), ({"passed": 1}, 1)])
def test_local_exit_requires_explicit_boolean_success(tmp_path, monkeypatch, capsys, report, expected):
    monkeypatch.setattr(local_harness_eval, "launch", lambda *args: report)
    assert harness_eval.main(local_args(tmp_path)) == expected
    assert json.loads(capsys.readouterr().out) == report


@pytest.mark.parametrize("argv", [[], ["--list"]])
def test_offline_actions_do_not_import_or_launch_local_adapter(monkeypatch, capsys, argv):
    original = builtins.__import__

    class OfflineRouteReached(Exception):
        """Stop before native fixture imports; this test checks CLI routing."""

    def guarded(name, *args, **kwargs):
        assert "local_harness_eval" not in name, "Offline action attempted local adapter import"
        if not argv and name == "tools.context":
            raise OfflineRouteReached
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(local_harness_eval, "launch", lambda *args: pytest.fail("Offline action attempted local launch"))
    if not argv:
        with pytest.raises(OfflineRouteReached):
            harness_eval.main(argv)
    else:
        assert harness_eval.main(argv) == 0
        result = json.loads(capsys.readouterr().out)
        assert isinstance(result, list) and result
