"""Real subprocess ownership and fail-closed native execution; no model or GPU."""
import asyncio
import os
import shlex
import socket
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import execution as ex, policy


def command_for(path):
    return shlex.join([sys.executable, str(path)])


async def wait_file(path):
    async with asyncio.timeout(4):
        while not path.exists():
            await asyncio.sleep(.02)


def assert_reaped(path):
    pids = [int(p) for p in path.read_text().split()]
    assert pids
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids), pids


@pytest.fixture
def family(tmp_path):
    script = tmp_path / "family.py"
    # A double fork + setsid escapes a shell process group; the command-local
    # subreaper must still adopt, terminate and reap it. TERM is ignored.
    script.write_text('''
import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork() == 0:
    os.setsid()
    if os.fork() != 0:
        os._exit(0)
    Path("pids").write_text(str(os.getpid()))
    while True: time.sleep(.1)
while not Path("pids").exists(): time.sleep(.01)
with Path("pids").open("a") as f: f.write(" " + str(os.getpid()))
while True: time.sleep(.1)
''')
    return script


@pytest.mark.asyncio
async def test_timeout_reaps_detached_descendants_and_preserves_unrelated_child(tmp_path, family):
    unrelated = await asyncio.create_subprocess_exec("sleep", "20")
    try:
        result = await ex.run_owned([sys.executable, str(family)], cwd=tmp_path,
                                    env=ex.minimal_environment(), timeout=.7)
        assert result.timed_out
        assert_reaped(tmp_path / "pids")
        assert unrelated.returncode is None
    finally:
        unrelated.terminate()
        await unrelated.wait()


@pytest.mark.asyncio
async def test_cancellation_waits_for_detached_descendants_to_be_reaped(tmp_path, family):
    task = asyncio.create_task(ex.run_owned([sys.executable, str(family)], cwd=tmp_path,
                                           env=ex.minimal_environment(), timeout=20))
    await wait_file(tmp_path / "pids")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_reaped(tmp_path / "pids")


@pytest.mark.asyncio
async def test_success_reaps_background_children_that_hold_stdout(tmp_path):
    script = tmp_path / "background.py"
    script.write_text('''
import os,signal,time
from pathlib import Path
if os.fork() == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path("pids").write_text(str(os.getpid()))
    while True: time.sleep(.1)
while not Path("pids").exists(): time.sleep(.01)
print("finished", flush=True)
''')
    result = await ex.run_owned([sys.executable, str(script)], cwd=tmp_path,
                                env=ex.minimal_environment(), timeout=3)
    assert result.returncode == 0 and not result.timed_out
    assert b"finished" in result.output
    assert_reaped(tmp_path / "pids")


@pytest.mark.asyncio
async def test_output_is_drained_but_retention_is_bounded(tmp_path):
    result = await ex.run_owned([sys.executable, "-c", "print('x'*1000000)"], cwd=tmp_path,
                                env=ex.minimal_environment(), max_output=4096)
    assert result.returncode == 0 and len(result.output) == 4096 and result.truncated


@pytest.mark.asyncio
async def test_editable_supervisor_and_path_shims_cannot_replace_enforcement(tmp_path, monkeypatch):
    fake = tmp_path / "execution.py"
    marker = tmp_path / "escaped"
    fake.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    shim = tmp_path / "bwrap"
    shim.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 0\n")
    shim.chmod(0o755)
    monkeypatch.setattr(ex, "__file__", str(fake))
    monkeypatch.setattr(sys, "executable", str(shim))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert ex._bubblewrap_executable() != str(shim)
    result = await ex.run_owned(["/bin/true"], cwd=tmp_path, env=ex.minimal_environment())
    assert result.returncode == 0 and not marker.exists()


@pytest.fixture
def unavailable(monkeypatch):
    async def probe(scope):
        return ex.SandboxCapability(False, "fixture: namespace denied", scope)
    monkeypatch.setattr(ex, "probe_sandbox", probe)


@pytest.mark.asyncio
async def test_unavailable_sandbox_never_runs_even_with_ordinary_approval(tmp_path, unavailable):
    scope = ex.ExecutionScope(tmp_path)
    command = "echo unsafe > marker"
    for approval in (None, ex.CommandApproval(command, scope, time.monotonic() + 10)):
        with pytest.raises(ex.ExecutionRefused, match="sandbox unavailable"):
            await ex.execute_bash(command, ex.ExecutionContext(scope, approval=approval))
    assert not (tmp_path / "marker").exists()


@pytest.mark.asyncio
async def test_uncontained_grant_is_exact_single_use_and_expires(tmp_path, unavailable):
    scope = ex.ExecutionScope(tmp_path)
    command = "echo granted > marker"
    grant = ex.CommandApproval(command, scope, time.monotonic() + 10, allow_uncontained=True)
    context = ex.ExecutionContext(scope, approval=grant)
    with pytest.raises(ex.ExecutionRefused):
        await ex.execute_bash(command + "-other", context)
    result, contained = await ex.execute_bash(command, context)
    assert result.returncode == 0 and not contained
    assert (tmp_path / "marker").read_text().strip() == "granted"
    with pytest.raises(ex.ExecutionRefused):
        await ex.execute_bash(command, context)
    expired = ex.CommandApproval(command, scope, time.monotonic() - 1, allow_uncontained=True)
    with pytest.raises(ex.ExecutionRefused):
        await ex.execute_bash(command, ex.ExecutionContext(scope, approval=expired))


@pytest.mark.asyncio
async def test_native_timeout_enforces_context_and_reports_cleanup(tmp_path, family, unavailable, monkeypatch):
    from dream.tools import native
    monkeypatch.setattr(native, "ctx", lambda: SimpleNamespace(workspace=tmp_path))
    monkeypatch.setattr(native, "_BASH_TIMEOUT", .7)
    scope = ex.ExecutionScope(tmp_path)
    command = command_for(family)
    approval = ex.CommandApproval(command, scope, time.monotonic() + 10, allow_uncontained=True)
    with ex.execution_context(scope, approval=approval):
        result = await native.run_bash.handler({"command": command})
    assert result["is_error"] and "terminated and reaped" in result["content"][0]["text"]
    assert_reaped(tmp_path / "pids")


@pytest.mark.asyncio
async def test_native_arguments_cannot_supply_approvals(tmp_path, unavailable, monkeypatch):
    from dream.tools import native
    monkeypatch.setattr(native, "ctx", lambda: SimpleNamespace(workspace=tmp_path))
    result = await native.run_bash.handler({"command": "echo bad > marker",
                                           "allow_uncontained": True, "mode": "red-team"})
    assert result["is_error"] and not (tmp_path / "marker").exists()


@pytest.fixture
def native_engine(tmp_path, unavailable, monkeypatch):
    from dream.core.engine import Engine
    from dream import hooks

    async def no_hooks(*args, **kwargs):
        return SimpleNamespace(allowed=True, outcomes=[])
    monkeypatch.setattr(hooks, "run_hooks", no_hooks)
    # Exercise the actual integration wrapper without booting a provider, store,
    # hooks, browser or model. The approval simulates one explicit fixture answer.
    engine = Engine.__new__(Engine)
    engine.workspace = tmp_path
    engine.execution_scope = ex.ExecutionScope(tmp_path)
    engine.execution_capability = ex.SandboxCapability(False, "fixture", engine.execution_scope)
    engine._command_approvals = {}
    engine._tool_context = SimpleNamespace(workspace=tmp_path, session_id="execution-fixture")
    engine._mode_getter = lambda: "ask"
    engine.runtime_meter = None
    engine.session_id = "execution-fixture"
    engine.backend = None
    return engine


@pytest.mark.asyncio
async def test_engine_wrapper_transfers_one_exact_human_approval(tmp_path, native_engine):
    from dream.tools import native
    engine = native_engine
    command = "echo integrated > marker"

    async def human(name, args):
        assert name == "run_bash" and args == {"command": command}
        engine.approve_command(command, uncontained=True)
        return True
    engine._user_can_use_tool = human
    assert await engine._authorize_tool("run_bash", {"command": command})
    wrapped = engine._wrap_tool(native.run_bash)
    result = await wrapped.handler({"command": command})
    assert not result.get("is_error"), result
    assert (tmp_path / "marker").read_text().strip() == "integrated"
    assert not engine._command_approvals
    assert (await wrapped.handler({"command": command}))["is_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["before_hook", "runtime_budget"])
async def test_blocked_native_attempt_discards_its_host_approval(tmp_path, native_engine, monkeypatch, blocker):
    from dream import hooks
    from dream.tools import native
    engine = native_engine
    command = "echo approved-once > marker"
    engine.approve_command(command, uncontained=True)

    async def before_hook(event, payload):
        return SimpleNamespace(allowed=False, outcomes=[])

    class ExhaustedBudget:
        def before_tool(self, name):
            raise RuntimeError("fixture tool budget exhausted")

    if blocker == "before_hook":
        monkeypatch.setattr(hooks, "run_hooks", before_hook)
    else:
        engine.runtime_meter = ExhaustedBudget()
    wrapped = engine._wrap_tool(native.run_bash)
    result = await wrapped.handler({"command": command})
    assert result["is_error"] and not (tmp_path / "marker").exists()
    assert not engine._command_approvals, "a blocked attempt must not preserve host access for a later call"

    async def allow_hooks(event, payload):
        return SimpleNamespace(allowed=True, outcomes=[])
    monkeypatch.setattr(hooks, "run_hooks", allow_hooks)
    engine.runtime_meter = None
    assert (await wrapped.handler({"command": command}))["is_error"]
    assert not (tmp_path / "marker").exists()


def test_scope_is_explicit_expiring_and_cannot_enable_host_network(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ValueError):
        ex.ExecutionScope(tmp_path, red_team=True)
    with pytest.raises(ValueError):
        ex.ExecutionScope(tmp_path, red_team=True, target_roots=(tmp_path,), expires_at=time.monotonic()+10)
    with pytest.raises(ex.ExecutionRefused, match="expired"):
        ex.ExecutionScope(tmp_path, red_team=True, target_roots=(target,), expires_at=0).validate()
    with pytest.raises(ex.ExecutionRefused, match="network"):
        ex.ExecutionScope(tmp_path, network=True).validate()
    for expiry in (float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            ex.ExecutionScope(tmp_path, red_team=True, target_roots=(target,), expires_at=expiry)
    with ex.execution_context(ex.ExecutionScope(tmp_path)):
        with pytest.raises(ex.ExecutionRefused, match="another workspace"):
            ex.current_execution(target)


def test_git_symlink_cannot_smuggle_an_outside_read_mount(tmp_path):
    workspace, private = tmp_path / "workspace", tmp_path / "private"
    workspace.mkdir(); private.mkdir()
    (private / "credential").write_text("fixture-secret")
    (workspace / ".git").symlink_to(private, target_is_directory=True)
    with pytest.raises(ex.ExecutionRefused, match="symlinked .git"):
        with ex._mount_descriptors(ex.ExecutionScope(workspace)):
            pytest.fail("outside .git must not be implicitly exposed")


def test_mount_descriptor_survives_source_replacement(tmp_path):
    workspace, read_root, private = (tmp_path / n for n in ("workspace", "read-root", "private"))
    for path in (workspace, read_root, private):
        path.mkdir()
    (read_root / "value").write_text("allowed")
    (private / "value").write_text("secret")
    scope = ex.ExecutionScope(workspace, read_roots=(read_root,))
    with ex._mount_descriptors(scope) as mounts:
        read_root.rename(tmp_path / "original")
        read_root.symlink_to(private, target_is_directory=True)
        fd = os.open("value", os.O_RDONLY, dir_fd=mounts[read_root])
        try:
            assert os.read(fd, 20) == b"allowed"
        finally:
            os.close(fd)


@pytest.mark.parametrize("command", ["python build.py", "python -c 'print(1)'", "bash build.sh",
                                     "pytest -q", "npm run build", "make check", "echo $(pwd)"])
def test_auto_routines_require_actual_executor_capability(tmp_path, command):
    scope = ex.ExecutionScope(tmp_path)
    assert policy.decide("run_bash", {"command": command}, "auto", tmp_path)[0] == "ask"
    cap = ex.SandboxCapability(True, "fixture", scope, "/usr/bin/bwrap")
    assert policy.decide("run_bash", {"command": command}, "auto", tmp_path,
                         execution_scope=scope, execution_capability=cap)[0] == "allow"
    assert policy.decide("run_bash", {"command": command}, "plan", tmp_path,
                         execution_scope=scope, execution_capability=cap)[0] == "deny"


@pytest.mark.parametrize("command", ["rm -rf build", "env rm -rf build", "git push origin main",
                                     "git reset --hard", "npm publish", "sudo true",
                                     "bash -c 'rm -rf build'", "find . -delete", "curl https://example.test",
                                     "echo start\nrm -rf build", "timeout 5 rm -rf build"])
def test_consequences_still_ask_when_contained(tmp_path, command):
    scope = ex.ExecutionScope(tmp_path)
    cap = ex.SandboxCapability(True, "fixture", scope, "/usr/bin/bwrap")
    assert policy.decide("Bash", {"command": command}, "auto", tmp_path,
                         execution_scope=scope, execution_capability=cap)[0] == "ask"


def test_dedicated_delete_and_rm_share_bounded_redteam_exception(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    scope = ex.ExecutionScope(tmp_path, red_team=True, target_roots=(target,), expires_at=time.monotonic()+10)
    kw = dict(execution_scope=scope, execution_capability=ex.SandboxCapability(True, "fixture", scope, "bwrap"))
    for tool, args in [("run_bash", {"command": "rm target/a"}), ("delete_file", {"paths": ["target/a"]})]:
        assert policy.decide(tool, args, "auto", tmp_path, **kw)[0] == "allow"
        assert policy.decide(tool, args, "plan", tmp_path, **kw)[0] == "deny"
    for command in ["rm elsewhere", "rm target/*", "rm target/a; rm elsewhere"]:
        assert policy.decide("run_bash", {"command": command}, "auto", tmp_path, **kw)[0] == "ask"
    (target / "escape").symlink_to(tmp_path.parent)
    assert policy.decide("delete_file", {"paths": ["target/escape/a"]}, "auto", tmp_path, **kw)[0] == "deny"
    assert policy.decide("write_file", {"path": "elsewhere"}, "auto", tmp_path, **kw)[0] == "deny"
    assert policy.decide("thirdparty__run", {}, "auto", tmp_path, **kw)[0] == "deny"
    assert policy.capability("Task") == policy.MUTATING


def test_demonstration_and_lab_tools_have_explicit_capabilities(tmp_path):
    for name in ("demonstration_list", "demonstration_read", "capability_lab_inspect"):
        assert policy.capability(name) == policy.READONLY
        assert policy.decide(name, {}, "plan", tmp_path)[0] == "allow"
    for name in ("demonstration_draft", "capability_lab_create"):
        assert policy.capability(name) == policy.WRITE
        assert policy.decide(name, {}, "plan", tmp_path)[0] == "deny"
        assert policy.decide(name, {}, "auto", tmp_path)[0] == "allow"


async def real_capability(scope):
    capability = await ex.probe_sandbox(scope)
    if not capability.available:
        pytest.skip("actual bubblewrap unavailable: " + capability.reason)
    return capability


@pytest.mark.asyncio
async def test_real_boundary_scripts_symlinks_git_secrets_and_host_socket(tmp_path, monkeypatch):
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir(); outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("untouched")
    monkeypatch.setenv("PLAIN_HOST_CREDENTIAL", "fixture-only-secret")
    scope = ex.ExecutionScope(workspace, read_roots=(outside,))
    await real_capability(scope)
    host_socket = socket.socket(socket.AF_UNIX)
    host_socket.bind(str(workspace / "host.sock"))
    try:
        script = workspace / "probe.py"
        script.write_text(f'''
import os, socket
from pathlib import Path
Path("built.txt").write_text("routine build worked")
assert "PLAIN_HOST_CREDENTIAL" not in os.environ
assert os.environ["HOME"] == "/tmp/home"
assert not Path("/proc/{os.getpid()}").exists()
for fd in range(3, 256):
    try: os.fstat(fd)
    except OSError: pass
    else: raise AssertionError("host descriptor leaked: " + str(fd))
for name in ["escape/sentinel", {str(sentinel)!r}, ".git/config", "/etc/host-escape"]:
    try: Path(name).write_text("escaped")
    except OSError: pass
    else: raise AssertionError("outside write allowed: " + name)
for family in [socket.AF_UNIX, socket.AF_INET]:
    try: socket.socket(family)
    except PermissionError: pass
    else: raise AssertionError("socket boundary escaped")
print("boundary verified")
''')
        result, contained = await ex.execute_bash("/usr/bin/python3 probe.py", ex.ExecutionContext(scope))
        assert contained and result.returncode == 0, result.output
        assert b"boundary verified" in result.output
        assert (workspace / "built.txt").is_file()
        assert sentinel.read_text() == "untouched"
        assert (workspace / ".git" / "config").read_text() == "untouched"
    finally:
        host_socket.close()


@pytest.mark.asyncio
async def test_real_tmp_is_private_to_one_invocation_and_never_written_on_host(tmp_path):
    scope = ex.ExecutionScope(tmp_path)
    await real_capability(scope)
    scratch = Path("/tmp") / ("dream-private-" + uuid.uuid4().hex)
    assert not scratch.exists()
    code = f"from pathlib import Path; p = Path({str(scratch)!r}); p.write_text('private'); assert p.read_text() == 'private'"
    result, contained = await ex.execute_bash(
        shlex.join(["/usr/bin/python3", "-c", code]), ex.ExecutionContext(scope))
    assert contained and result.returncode == 0, result.output
    assert not scratch.exists()
    # A second command receives a fresh /tmp, even within the same session scope.
    code = f"from pathlib import Path; assert not Path({str(scratch)!r}).exists()"
    result, contained = await ex.execute_bash(
        shlex.join(["/usr/bin/python3", "-c", code]), ex.ExecutionContext(scope))
    assert contained and result.returncode == 0, result.output
    assert not scratch.exists()


@pytest.mark.asyncio
async def test_real_redteam_only_target_is_writable_and_runtime_expires(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "preserved").write_text("outside target")
    (target / "delete-me").write_text("fixture")
    scope = ex.ExecutionScope(tmp_path, red_team=True, target_roots=(target,), expires_at=time.monotonic()+10)
    await real_capability(scope)
    script = tmp_path / "redteam.py"
    script.write_text('''
from pathlib import Path
Path("target/delete-me").unlink()
try: Path("preserved").unlink()
except PermissionError: pass
except OSError: pass
else: raise AssertionError("escaped target")
''')
    result, _ = await ex.execute_bash("/usr/bin/python3 redteam.py", ex.ExecutionContext(scope))
    assert result.returncode == 0, result.output
    assert not (target / "delete-me").exists() and (tmp_path / "preserved").exists()
    expiring = ex.ExecutionScope(tmp_path, red_team=True, target_roots=(target,), expires_at=time.monotonic()+1)
    started = time.monotonic()
    result, _ = await ex.execute_bash("sleep 20", ex.ExecutionContext(expiring), timeout=20)
    assert result.timed_out and time.monotonic() - started < 4


@pytest.mark.asyncio
async def test_real_auto_runs_pytest_with_explicit_readonly_toolchain(tmp_path):
    toolchain = Path(sys.prefix).resolve()
    # The fixture's installed pytest is external to the temporary workspace.
    # This explicit read-only toolchain mount is supplied by the test harness.
    scope = ex.ExecutionScope(tmp_path, read_roots=(toolchain,))
    await real_capability(scope)
    (tmp_path / "answer.py").write_text("def answer(): return 6 * 7\n")
    (tmp_path / "test_answer.py").write_text("from answer import answer\ndef test_answer(): assert answer() == 42\n")
    command = "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 " + shlex.join([
        sys.executable, "-m", "pytest", "-q", "test_answer.py"])
    result, contained = await ex.execute_bash(command, ex.ExecutionContext(scope))
    assert contained and result.returncode == 0 and b"1 passed" in result.output, result.output
