import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native_engine_recovery_qualification.py"
spec = importlib.util.spec_from_file_location("native_engine_recovery_qualification", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_private_cpu_environment_hides_ambient_runtime_and_sets_software_rendering(tmp_path):
    env = module.fixture_environment({"DISPLAY": ":99", "SECRET": "not-carried"}, tmp_path)
    assert env["DISPLAY"] == ":99"
    assert "SECRET" not in env
    assert env["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert env["GALLIUM_DRIVER"] == "llvmpipe"
    assert env["DREAM_SEMANTIC_MEMORY"] == "0"
    assert Path(env["DREAM_DESKTOP_SESSION_FILE"]).parent == tmp_path
    assert Path(env["DREAM_ROOT"]).parent == tmp_path


def test_report_requires_explicit_once_only_recovery_boundary():
    report = {"start_requests": 1, "continuations": 1, "stop_observed": True, "interrupted": True,
              "lingering_fixture_workers": 0, "replayed_start_requests": 0,
              "wrong_workspace_requests": 0, "held_task_cancelled": True, "held_task_finished": True,
              "held_task_done_after_stop": True, "workflow_interrupted_endpoint_observed": True,
              "workflow_task_persisted": True, "workflow_workspace_persisted": True,
              "workflow_interruption_persisted": True, "workflow_continuation_persisted": True,
              "child_exit_clean": True, "session_id": "private-fixture", "workflow_task_id": "a" * 32}
    module.validate_report(report)


def test_report_rejects_replay_wrong_workspace_and_lingering_worker():
    base = {"start_requests": 1, "continuations": 1, "stop_observed": True, "interrupted": True,
            "lingering_fixture_workers": 0, "replayed_start_requests": 0,
            "wrong_workspace_requests": 0, "held_task_cancelled": True, "held_task_finished": True,
            "held_task_done_after_stop": True, "workflow_interrupted_endpoint_observed": True,
            "workflow_task_persisted": True, "workflow_workspace_persisted": True,
            "workflow_interruption_persisted": True, "workflow_continuation_persisted": True,
            "child_exit_clean": True, "session_id": "private-fixture", "workflow_task_id": "a" * 32}
    for key, value in (("replayed_start_requests", 1), ("wrong_workspace_requests", 1), ("lingering_fixture_workers", 1)):
        rejected = dict(base); rejected[key] = value
        try: module.validate_report(rejected)
        except AssertionError: pass
        else: raise AssertionError(f"{key} should reject the report")


def test_report_rejects_missing_and_bool_int_type_confusion():
    base = {"start_requests": 1, "continuations": 1, "stop_observed": True, "interrupted": True,
            "lingering_fixture_workers": 0, "replayed_start_requests": 0,
            "wrong_workspace_requests": 0, "held_task_cancelled": True, "held_task_finished": True,
            "held_task_done_after_stop": True, "workflow_interrupted_endpoint_observed": True,
            "workflow_task_persisted": True, "workflow_workspace_persisted": True,
            "workflow_interruption_persisted": True, "workflow_continuation_persisted": True,
            "child_exit_clean": True, "session_id": "private-fixture", "workflow_task_id": "a" * 32}
    for key, value in (("session_id", None), ("start_requests", True), ("stop_observed", 1),
                       ("lingering_fixture_workers", False)):
        rejected = dict(base)
        if value is None: rejected.pop(key)
        else: rejected[key] = value
        try: module.validate_report(rejected)
        except AssertionError: pass
        else: raise AssertionError(f"{key} type/missing evidence should reject the report")


def test_script_uses_actual_app_engine_and_native_window_not_callback_only_fixture():
    source = SCRIPT.read_text()
    for symbol in ("from dream.tui.app import App", "from dream.core.engine import Engine", "DreamWindow", "FiniteBackend", "window._connect_studio(force=True)", "/api/workflows/create", "/api/workflows/revise", "/api/workflows/start"):
        assert symbol in source
    assert "qualification interruption workspace=" not in source
