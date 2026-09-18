"""Studio JS and its file bindings run inside the OS boundary, using CPU canvas."""
import asyncio
import io
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from dream.core import execution as ex, script_execution as scripts
from dream.tools import studio


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "ctx", lambda: SimpleNamespace(workspace=tmp_path))
    monkeypatch.setattr(studio, "get_preview", lambda: SimpleNamespace(captures={}))
    return tmp_path


async def require_runtime(scope):
    cap = await ex.probe_sandbox(scope)
    if not cap.available:
        pytest.skip(cap.reason)
    try:
        scripts._runtime()
    except ex.ExecutionRefused as exc:
        pytest.skip(str(exc))


async def test_script_has_no_host_fallback_or_preview_execution(workspace, monkeypatch):
    async def unavailable(scope):
        return ex.SandboxCapability(False, "fixture namespace denied", scope)
    monkeypatch.setattr(ex, "probe_sandbox", unavailable)
    monkeypatch.setattr(scripts, "_runtime", lambda: (Path("/bin/true"), ()))
    scope = ex.ExecutionScope(workspace)
    approval = ex.CommandApproval("anything", scope, time.monotonic()+10, allow_uncontained=True)
    with ex.execution_context(scope, approval=approval):
        result = await studio.run_script.handler({"code": "await saveFile('escaped', 'bad')"})
    assert result["is_error"] and "sandbox unavailable" in result["content"][0]["text"]
    assert not (workspace / "escaped").exists()


async def test_script_preserves_text_images_canvas_and_capture_blobs(workspace, monkeypatch):
    await require_runtime(ex.ExecutionScope(workspace))
    (workspace / "source.txt").write_text("hello")
    image = Image.new("RGB", (20, 10), "blue")
    image.save(workspace / "image.png")
    capture = io.BytesIO()
    image.save(capture, format="PNG")
    monkeypatch.setattr(studio, "get_preview", lambda: SimpleNamespace(captures={"fixture": [capture.getvalue()]}))
    result = await studio.run_script.handler({"code": """
      log(await ls());
      await saveFile('result.txt', (await readFile('source.txt')).toUpperCase());
      const image = await readImage('image.png');
      const canvas = createCanvas(image.width, image.height);
      const c = canvas.getContext('2d'); c.drawImage(image, 0, 0);
      c.fillStyle = 'red'; c.fillRect(0, 0, 10, 10);
      await saveFile('result.png', canvas);
      const captures = await getCaptures('fixture');
      await saveFile('capture.png', captures[0]);
      log('captures', captures.length, captures[0].type);
    """})
    assert not result.get("is_error"), result
    assert (workspace / "result.txt").read_text() == "HELLO"
    assert (workspace / "capture.png").read_bytes() == capture.getvalue()
    with Image.open(workspace / "result.png") as out:
        assert out.getpixel((2, 2))[:3] == (255, 0, 0)
        assert out.getpixel((15, 2))[:3] == (0, 0, 255)
    assert "captures 1 image/png" in result["content"][0]["text"]


async def test_os_boundary_survives_a_file_helper_path_check_bug(workspace, monkeypatch):
    outside = workspace.parent / (workspace.name + "-outside")
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("preserved")
    scope = ex.ExecutionScope(workspace, read_roots=(outside,))
    await require_runtime(scope)
    # Deliberately remove the Python helper check. The OS, not this string/path
    # validation, must still deny the actual write to the exposed read-only root.
    monkeypatch.setattr(scripts, "_WORKER", scripts._WORKER.replace(
        "if not path.is_relative_to(workspace):", "if False:"))
    with ex.execution_context(scope):
        result = await studio.run_script.handler({"code": f"await saveFile({str(sentinel)!r}, 'escaped')"})
    assert result["is_error"] and sentinel.read_text() == "preserved"


async def test_scripts_cannot_write_git_or_use_network(workspace):
    (workspace / ".git").mkdir()
    (workspace / ".git/config").write_text("preserved")
    await require_runtime(ex.ExecutionScope(workspace))
    result = await studio.run_script.handler({"code": "await saveFile('.git/config','changed')"})
    assert result["is_error"] and (workspace / ".git/config").read_text() == "preserved"
    result = await studio.run_script.handler({"code": "log(await fetch('http://127.0.0.1:1').then(()=>'escaped').catch(()=>'blocked'))"})
    assert not result.get("is_error") and "blocked" in result["content"][0]["text"]


async def test_redteam_script_only_writes_the_explicit_target(workspace):
    target = workspace / "target"
    target.mkdir()
    scope = ex.ExecutionScope(workspace, red_team=True, target_roots=(target,), expires_at=time.monotonic()+20)
    await require_runtime(scope)
    with ex.execution_context(scope):
        allowed = await studio.run_script.handler({"code": "await saveFile('target/ok','allowed')"})
        denied = await studio.run_script.handler({"code": "await saveFile('outside-target','bad')"})
    assert not allowed.get("is_error"), allowed
    assert denied["is_error"] and not (workspace / "outside-target").exists()


async def test_busy_script_timeout_preserves_logs_and_allows_next_call(workspace, monkeypatch):
    await require_runtime(ex.ExecutionScope(workspace))
    with monkeypatch.context() as timing:
        # The worker budget includes a fresh CPU Chromium startup. Leave room
        # for startup while still verifying the configured deadline and logs.
        timing.setattr(scripts, "SCRIPT_TIMEOUT", 3)
        started = time.monotonic()
        result = await studio.run_script.handler({"code": "await log('before busy loop'); while (true) {}"})
        assert result["is_error"] and "timed out" in result["content"][0]["text"]
        assert "limit 3 s" in result["content"][0]["text"]
        assert "before busy loop" in result["content"][0]["text"]
        assert 3 <= time.monotonic() - started < 6
    async with asyncio.timeout(5):
        recovered = await studio.run_script.handler({"code": "log('alive')"})
    assert not recovered.get("is_error"), recovered
    assert "alive" in recovered["content"][0]["text"]


async def test_script_cancellation_reaps_its_worker(workspace):
    await require_runtime(ex.ExecutionScope(workspace))
    children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    before = set(children.read_text().split())
    task = asyncio.create_task(studio.run_script.handler({"code": "await saveFile('started','yes'); while(true) {}"}))
    async with asyncio.timeout(5):
        while not (workspace / "started").exists():
            await asyncio.sleep(.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert set(children.read_text().split()) <= before


async def test_plan_mode_refuses_script_before_runtime_lookup(workspace, monkeypatch):
    monkeypatch.setattr(scripts, "_runtime", lambda: pytest.fail("plan must not start a worker"))
    with ex.execution_context(ex.ExecutionScope(workspace), mode="plan"):
        result = await studio.run_script.handler({"code": "await saveFile('bad','bad')"})
    assert result["is_error"] and "plan mode" in result["content"][0]["text"]
