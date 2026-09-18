"""Studio controls operate on the real settings API and require session auth."""
import pytest
from starlette.testclient import TestClient
from dream.gui.server import StudioServer
from dream.gui.bus import EventBus
from dream.tui.app import App
from dream.core.profiles import read_settings


def test_runtime_controls_reject_unauthorized_and_cross_origin_requests():
    called = []
    server = StudioServer(EventBus(), runtime=lambda: {"profile": {"name": "lean"}},
                          on_control=lambda payload: called.append(payload))
    with TestClient(server.app) as client:
        assert client.get("/api/runtime").status_code == 401
        assert client.post("/api/control", json={"action": "profile"}).status_code == 401
        headers = {"x-dream-token": server.token}
        assert client.get("/api/runtime", headers=headers).json()["profile"]["name"] == "lean"
        rejected = client.post("/api/control", headers={**headers, "origin": "https://unrelated.example"}, json={"action": "profile"})
        assert rejected.status_code == 403 and not called
        too_big = client.post("/api/control", headers=headers, content=b"x" * 33000)
        assert too_big.status_code == 413


def test_profile_controls_persist_valid_settings_and_reject_bad_values(tmp_path):
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app) as client:
        headers = {"x-dream-token": server.token}
        saved = client.post("/api/control", headers=headers, json={"action": "profile", "profile": "lean",
                            "overrides": {"context_limit": 8192, "max_parallel": 1}})
        assert saved.status_code == 200 and saved.json()["result"]["applies"] == "new sessions"
        assert read_settings()["overrides"]["context_limit"] == 8192
        invalid = client.post("/api/control", headers=headers, json={"action": "profile", "profile": "lean",
                              "overrides": {"max_parallel": 1.5}})
        assert invalid.status_code == 400
        assert read_settings()["overrides"]["max_parallel"] == 1


def test_extensions_toggle_changes_real_catalog_and_requires_boolean(tmp_path):
    from dream import extensions
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app) as client:
        headers = {"x-dream-token": server.token}
        invalid = client.post("/api/control", headers=headers, json={"action": "extension", "id": "skill:fixture", "enabled": "false"})
        assert invalid.status_code == 400
        valid = client.post("/api/control", headers=headers, json={"action": "extension", "id": "skill:fixture", "enabled": False})
        assert valid.status_code == 200 and not extensions.is_enabled("skill:fixture")


def test_red_team_rejects_unscoped_host_paths(tmp_path):
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app) as client:
        headers = {"x-dream-token": server.token}
        reply = client.post("/api/control", headers=headers, json={"action": "red_team", "enabled": True, "target": "/", "minutes": 15})
        assert reply.status_code == 400


def test_draft_can_be_reviewed_through_authenticated_real_api(tmp_path, monkeypatch):
    from test_learning_workflows import prepared_demo
    from dream import demonstrations
    prepared_demo(tmp_path, monkeypatch)
    demonstrations.create_draft("demo-fixture", goal="Review a visible action", steps=[
        {"action": "Open project", "frames": ["frames/00001.jpg"], "basis": "inferred"}])
    server = StudioServer(EventBus())
    with TestClient(server.app) as client:
        endpoint = "/api/learning/demo-fixture/draft"
        assert client.get(endpoint).status_code == 401
        response = client.get(endpoint, headers={"x-dream-token": server.token})
        assert response.status_code == 200 and "inferred" in response.json()["body"]


async def test_explicit_host_choice_runs_only_the_approved_command(tmp_path, monkeypatch):
    from dream.core.execution import probe_sandbox
    from dream.tools.context import ToolContext
    from dream.tools.native import run_bash
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = App(provider="machx", workspace=workspace)
    app.mode = "ask"
    engine = app.engine = app._boot_engine()
    engine._tool_context = ToolContext(None, None, None, engine.session_id, workspace=workspace)
    engine.execution_capability = await probe_sandbox(engine.execution_scope)
    target = tmp_path / "explicit-host-fixture"
    command = "printf approved > " + str(target)
    prompts = []
    async def choose_host(prompt):
        prompts.append(prompt)
        return "h"
    monkeypatch.setattr(app, "_read_answer", choose_host)
    assert await engine._authorize_tool("run_bash", {"command": command})
    assert len(prompts) == 1 and engine._command_approvals[command].allow_uncontained
    result = await engine._wrap_tool(run_bash).handler({"command": command})
    assert not result.get("is_error") and target.read_text() == "approved"
    assert len(prompts) == 1 and not engine._command_approvals
    assert (await engine._wrap_tool(run_bash).handler({"command": command}))["is_error"]


async def test_loop_record_writes_are_denied_and_progress_remains_writable(tmp_path):
    from types import SimpleNamespace
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run = workspace / "run"
    run.mkdir()
    app = App(provider="machx", workspace=workspace)
    app.mode = "auto"
    app._active_loop = SimpleNamespace(workspace=run)
    assert not await app._decide_permission("write_file", {"path": str(run / "ledger.jsonl"), "content": "forged"})
    assert await app._decide_permission("write_file", {"path": str(run / "progress.md"), "content": "progress"})


def test_module_review_and_trust_require_the_exact_visible_source(tmp_path, monkeypatch):
    from dream import config, plugins, extensions
    folder = tmp_path / "custom"
    folder.mkdir()
    source = folder / "review_fixture.py"
    marker = tmp_path / "must-not-execute"
    source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    monkeypatch.setattr(config, "CUSTOM_TOOLS_DIR", folder)
    monkeypatch.setattr(plugins, "loaded", lambda: [])
    monkeypatch.setattr(plugins, "load", lambda *args, **kwargs: None)
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    identifier = "tool:custom/review_fixture"
    endpoint = "/api/extensions/tool:custom/review_fixture/source"
    with TestClient(server.app) as client:
        assert client.get(endpoint).status_code == 401
        headers = {"x-dream-token": server.token}
        review = client.get(endpoint, headers=headers).json()
        assert review["source"] == source.read_text() and not marker.exists()
        assert client.post("/api/control", headers=headers, json={"action": "module_trust", "id": identifier,
                           "sha256": "0" * 64}).status_code == 400
        assert client.post("/api/control", headers=headers, json={"action": "module_trust", "id": identifier,
                           "sha256": review["sha256"]}).status_code == 200
        assert extensions.module_status(source)["trust_state"] == "trusted" and not marker.exists()
        source.write_text(source.read_text() + "# changed\n")
        assert extensions.module_status(source)["trust_state"] == "changed"
        assert client.post("/api/control", headers=headers, json={"action": "module_trust", "id": identifier,
                           "sha256": review["sha256"]}).status_code == 400
