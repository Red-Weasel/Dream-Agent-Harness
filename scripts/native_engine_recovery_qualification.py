"""Private, software-rendered actual App/Engine recovery qualification.

The fixture has no provider transport or model.  It does use the production App,
Engine, Studio HTTP/WebSocket server and native GTK/WebKit client.  A finite
backend deliberately holds its first request open; the visible native Stop must
cancel it before one explicitly submitted continuation is accepted.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile
import time
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixture_environment(inherited: dict[str, str], workspace: Path) -> dict[str, str]:
    """Provide a private CPU-only App environment before Dream imports."""
    env = {key: inherited[key] for key in ("DISPLAY", "XAUTHORITY", "LANG", "LC_ALL", "TZ") if key in inherited}
    env.update(PATH="/usr/bin:/bin", PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1",
               DREAM_ROOT=str(workspace / "state"), DREAM_GUI="1", DREAM_GUI_OPEN="0",
               DREAM_DESKTOP_SESSION_FILE=str(workspace / "session.json"),
               DREAM_SEMANTIC_MEMORY="0", DREAM_RERANK="0", DREAM_CONSOLIDATE="0",
               GDK_BACKEND="x11", LIBGL_ALWAYS_SOFTWARE="1", GALLIUM_DRIVER="llvmpipe",
               WEBKIT_DISABLE_DMABUF_RENDERER="1", WEBKIT_DISABLE_COMPOSITING_MODE="1",
               GST_PLUGIN_FEATURE_RANK="vaapidecodebin:NONE,vaapih264dec:NONE,vah264dec:NONE,nvh264dec:NONE",
               GIO_USE_VFS="local", GTK_USE_PORTAL="0", NO_AT_BRIDGE="1")
    for key, name in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                      ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                      ("XDG_RUNTIME_DIR", "runtime")):
        directory = workspace / ("xdg-" + name)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        env[key] = str(directory)
    return env


def isolate_environment(workspace: Path) -> None:
    env = fixture_environment(dict(os.environ), workspace)
    os.environ.clear()
    os.environ.update(env)


def validate_report(report: dict) -> None:
    """Keep the acceptance boundary machine-checkable outside the GUI run."""
    if type(report) is not dict:
        raise AssertionError("recovery evidence must be an object")
    required = {"start_requests": (int, 1), "continuations": (int, 1),
                "replayed_start_requests": (int, 0), "wrong_workspace_requests": (int, 0),
                "lingering_fixture_workers": (int, 0), "stop_observed": (bool, True),
                "interrupted": (bool, True), "held_task_cancelled": (bool, True),
                "held_task_finished": (bool, True), "held_task_done_after_stop": (bool, True),
                "workflow_interrupted_endpoint_observed": (bool, True),
                "workflow_task_persisted": (bool, True), "workflow_workspace_persisted": (bool, True),
                "workflow_interruption_persisted": (bool, True),
                "workflow_continuation_persisted": (bool, True), "child_exit_clean": (bool, True)}
    for key, (kind, expected) in required.items():
        if type(report.get(key)) is not kind or report[key] != expected:
            raise AssertionError(f"recovery evidence {key!r} was {report.get(key)!r}, expected {expected!r}")
    if type(report.get("session_id")) is not str or not report["session_id"]:
        raise AssertionError("durable session identity is missing")
    if type(report.get("workflow_task_id")) is not str or len(report["workflow_task_id"]) != 32:
        raise AssertionError("durable workflow task identity is missing")


async def serve(workspace: Path) -> None:
    """Run a real App + Engine, with only its backend replaced by finite transport."""
    isolate_environment(workspace)
    from dream.core.backends.base import Backend, Event
    from dream.core.engine import Engine
    from dream.tui.app import App

    report = {"start_requests": 0, "continuations": 0, "replayed_start_requests": 0,
              "wrong_workspace_requests": 0, "stop_observed": False, "interrupted": False,
              "lingering_fixture_workers": 1, "held_task_cancelled": False, "held_task_finished": False,
              "held_task_done_after_stop": False, "workflow_interrupted_endpoint_observed": False,
              "workflow_task_persisted": False, "workflow_workspace_persisted": False,
              "workflow_interruption_persisted": False, "workflow_continuation_persisted": False,
              "child_exit_clean": False}
    release = asyncio.Event()
    def persist() -> None:
        (workspace / "engine-report.json").write_text(json.dumps(report))

    class FiniteBackend(Backend):
        provider_label = "Finite CPU fixture"
        def __init__(self): self.calls = []; self.held_task = None
        async def connect(self): return None
        async def disconnect(self): return None
        async def set_model(self, model): return None
        async def interrupt(self): report["stop_observed"] = True; release.set(); persist()
        async def _events(self, prompt):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                report["start_requests"] += 1
                persist()
                self.held_task = asyncio.current_task()
                try:
                    yield Event("text_delta", "Finite request is active.")
                    await release.wait()
                    # Cancellation from App.Stop must prevent this marker from escaping.
                    yield Event("text_delta", "ERROR: cancelled request continued")
                except asyncio.CancelledError:
                    report["held_task_cancelled"] = True
                    raise
                finally:
                    report["held_task_finished"] = True
                    persist()
            elif len(self.calls) == 2:
                report["continuations"] += 1
                persist()
                yield Event("text_delta", "Explicit continuation completed once.")
                yield Event("assistant_done", "Explicit continuation completed once.")
                yield Event("result", {"subtype": "success", "is_error": False, "usage": {}})
            else:
                report["replayed_start_requests"] += 1
                yield Event("error", "unexpected automatic replay")
        def ask(self, prompt): return self._events(prompt)

    app = App(provider="anthropic", workspace=workspace, gui=True, consolidate_on_exit=False)
    def boot():
        engine = Engine(provider="anthropic", can_use_tool=app._permission, workspace=workspace,
                        mode_getter=lambda: app.mode, emit=app._render_event)
        backend = FiniteBackend()
        async def create_backend(): return backend
        engine._create_backend = create_backend
        engine._qualification_backend = backend
        return engine
    app._boot_engine = boot
    await app.start()
    report["session_id"] = app.engine.session_id
    report["workspace"] = str(workspace)
    persist()
    try:
        while True:
            prompt = await app._gui_prompts.get()
            if prompt == "/quit": break
            from dream.tui.app import QueuedPrompt
            if not isinstance(prompt, QueuedPrompt):
                report["wrong_workspace_requests"] += 1
                continue
            report["workflow_task_id"] = prompt.workflow_task_id
            await app._ask(prompt.text, workflow=prompt)
            report["interrupted"] = bool(app.interrupted) or report["interrupted"]
            backend = app.engine._qualification_backend
            if app.interrupted:
                report["held_task_done_after_stop"] = backend.held_task is not None and backend.held_task.done()
            else:
                report["held_task_done_after_stop"] = backend.held_task is not None and backend.held_task.done()
            report["lingering_fixture_workers"] = int(not report["held_task_done_after_stop"])
            persist()
    finally:
        if app.studio is not None: await app.studio.stop()
        session_id = app.engine.session_id
        await app.engine.stop(consolidate=False)
        from dream.workflows import WorkflowService
        service = WorkflowService(workspace)
        task = service.get(report.get("workflow_task_id"))
        report["workflow_task_persisted"] = task.get("id") == report.get("workflow_task_id")
        report["workflow_workspace_persisted"] = str(service.store.workspace) == str(workspace.resolve())
        report["workflow_interruption_persisted"] = any(step.get("status") == "interrupted" for step in task.get("history", ()))
        report["workflow_continuation_persisted"] = task.get("attempt") == 2 and len(task.get("requests", ())) == 2
        persist()


def qualify(output: Path) -> None:
    if os.environ.get("LIBGL_ALWAYS_SOFTWARE") != "1":
        raise SystemExit("Set LIBGL_ALWAYS_SOFTWARE=1 and use a private software display.")
    output.mkdir(parents=True, exist_ok=True)
    started, checks, error = time.monotonic(), [], None
    with tempfile.TemporaryDirectory(prefix="dream-native-engine-recovery-") as temp:
        workspace = Path(temp)
        isolate_environment(workspace)
        from dream.desktop.window import DreamWindow, GLib, Gtk, Vte
        if not Gtk.init_check([])[0]: raise SystemExit("A private graphical display is required.")
        window = DreamWindow(str(ROOT / ".venv/bin/python"), str(workspace), [], autostart=False)
        window.running, window.discovery = True, workspace / "session.json"
        state = {"stage": 0, "busy": False}
        window.terminal.connect("child-exited", lambda _term, status: state.update(child_wait_status=status))
        env = fixture_environment(window.child_env(), workspace)
        window.terminal.spawn_async(Vte.PtyFlags.DEFAULT, str(ROOT),
            [str(ROOT / ".venv/bin/python"), str(Path(__file__).resolve()), "--server", str(workspace)],
            [f"{key}={value}" for key, value in env.items()], GLib.SpawnFlags.DEFAULT,
            None, None, -1, None, window._spawned, None)
        def api(path, payload=None):
            base, token = window.address_info
            data = None if payload is None else json.dumps(payload).encode()
            req = Request(base + path, data=data,
                          headers={"Content-Type": "application/json", "x-dream-token": token})
            try:
                with build_opener(ProxyHandler({})).open(req, timeout=3) as response:
                    return json.loads(response.read() or b"{}")
            except HTTPError as exc:
                raise RuntimeError(f"{path}: {exc.read().decode()}") from exc
        def evaluate(code, callback):
            state["busy"] = True
            def done(view, result, _data):
                state["busy"] = False
                try: callback(json.loads(view.evaluate_javascript_finish(result).to_json(0)))
                except Exception as exc: state["exception"] = str(exc)
            window.studio.evaluate_javascript(code, -1, None, None, None, done, None)
        def capture_failure() -> None:
            """Retain only this synthetic temporary workspace when qualification fails."""
            destination = output / f"failed-fixture-workspace-{time.monotonic_ns()}"
            try:
                shutil.copytree(workspace, destination)
            except Exception:
                pass
            try:
                terminal_text, _ = window.terminal.get_text(lambda *_args: True)
                (output / "terminal-output.txt").write_text(terminal_text)
            except Exception:
                pass
        def tick():
            nonlocal error
            try:
                if time.monotonic() - started > 55: raise AssertionError(f"timed out at stage {state['stage']}")
                if state.get("exception"): raise AssertionError(state["exception"])
                if state["busy"]: return True
                report_path = workspace / "engine-report.json"
                report = json.loads(report_path.read_text()) if report_path.exists() else {}
                if state["stage"] == 0 and window.connected:
                    created = api("/api/workflows/create", {"recipe": "report", "inputs": {"goal": "Native recovery fixture", "sources": ""}})["task"]
                    queued = api("/api/workflows/start", {"task_id": created["id"], "expected_version": created["version"], "request_id": "native-recovery-start"})["task"]
                    state["workflow"] = queued
                    state["stage"] = 1
                    checks.append("Native Studio created and dispatched the production guided task.")
                elif state["stage"] == 1:
                    def running(value):
                        if value:
                            window._connect_studio(force=True)
                            state["stage"] = 2
                            checks.append("Actual Engine streamed its held task into the native Studio, then Studio reconnected.")
                    evaluate("document.body.innerText.includes('Finite request is active.')", running)
                elif state["stage"] == 2 and window.connected:
                    evaluate("(()=>{const b=document.getElementById('stop');if(!b||b.hidden||b.disabled)return false;b.click();return true;})()", lambda value: state.update(stage=3) if value else None)
                elif state["stage"] == 3:
                    def inspect_stop(value):
                        (output / "last-dom.txt").write_text(value or "")
                        if "interrupted" in (value or "").lower(): state["stage"] = 4
                    evaluate("document.body.innerText", inspect_stop)
                elif state["stage"] == 4:
                    tasks = api("/api/workflows/list")["tasks"]
                    task = next((item for item in tasks if item["id"] == state["workflow"]["id"]), None)
                    assert task is not None and task["status"] == "interrupted" and task["attempt"] == 1, task
                    report["workflow_interrupted_endpoint_observed"] = True
                    revised = api("/api/workflows/revise", {"task_id": task["id"], "expected_version": task["version"]})["task"]
                    state["workflow"] = api("/api/workflows/start", {"task_id": revised["id"], "expected_version": revised["version"], "request_id": "native-recovery-continue"})["task"]
                    checks.append("The production workflow record was reopened as interrupted before one explicit revise and continuation dispatch.")
                    state["stage"] = 5
                elif state["stage"] == 5:
                    def continued(value):
                        if value:
                            checks.append("Only the explicitly submitted continuation completed in the native UI; the held request showed no post-Stop marker.")
                            api("/api/prompt", {"prompt": "/quit"}); state["stage"] = 6
                    evaluate("document.body.innerText.includes('Explicit continuation completed once.') && !document.body.innerText.includes('ERROR: cancelled request continued')", continued)
                elif state["stage"] == 6 and not window.running:
                    if os.waitstatus_to_exitcode(state.get("child_wait_status", -1)) != 0: raise AssertionError("fixture child did not exit cleanly")
                    if not report_path.exists(): raise AssertionError("final child recovery report is missing")
                    report = json.loads(report_path.read_text())
                    report["child_exit_clean"] = True
                    report["workflow_interrupted_endpoint_observed"] = bool(state.get("workflow"))
                    validate_report(report)
                    (output / "engine-report.json").write_text(json.dumps(report, indent=2))
                    window.destroy(); return False
            except Exception as exc:
                error = str(exc); capture_failure(); window.destroy(); return False
            return True
        GLib.timeout_add(150, tick); Gtk.main()
        report_path = workspace / "engine-report.json"
        if report_path.exists():
            (output / "engine-report.partial.json").write_bytes(report_path.read_bytes())
        try:
            terminal_text, _ = window.terminal.get_text(lambda *_args: True)
            (output / "terminal-output.txt").write_text(terminal_text)
        except Exception:
            pass
        if window.child_pid:
            try: os.kill(window.child_pid, signal.SIGTERM)
            except ProcessLookupError: pass
    result = {"checks": checks, "error": error, "seconds": round(time.monotonic() - started, 2),
              "limits": "Finite scripted backend; actual App/Engine/native transport, not provider inference, model quality or owner acceptance."}
    (output / "native-engine-recovery-report.json").write_text(json.dumps(result, indent=2)); print(json.dumps(result, indent=2))
    if error: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--server", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/tmp/dream-native-engine-recovery")); args = parser.parse_args()
    asyncio.run(serve(args.server)) if args.server else qualify(args.output)
