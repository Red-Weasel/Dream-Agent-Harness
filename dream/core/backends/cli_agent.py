"""Subscription CLI backend — drive a signed-in coding-agent CLI as a Dream session.

Unlike the Agent SDK and OpenAI-compat backends, there is no in-process model loop
here: each turn spawns the CLI as a subprocess, streams its newline-delimited JSON
event stream, and translates each object into Dream ``Event``s. The CLI owns its own
tool loop and sandbox; Dream's memory reaches it over a stdio MCP server (see
``dream/mcp``). This cycle ships ``codex`` (ChatGPT); other CLIs slot in as adapters.

The CLI-specific knowledge — how to build argv and how to read its event stream —
lives in a :class:`CliAdapter`. :class:`CliAgentBackend` is CLI-agnostic: it spawns,
streams, captures the resume/session id, and yields whatever the adapter translates.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing, contextmanager, nullcontext
import json
import math
import os
import shutil  # noqa: F401  (kept as a module attribute: tests replace shutil.which through it)
import time
from typing import Any, AsyncIterator

from .base import Backend, Event
from ..cli_discovery import locate_command, node_child_env
from ..execution import supervised_command

# One JSON event per line, and a codex `item.completed` can carry a whole command's
# output — routinely past the 64 KiB asyncio StreamReader default, which makes
# readline() raise ValueError and take the turn down with it.
STDOUT_LIMIT = 16 * 1024 * 1024
# Only the stderr TAIL is ever shown (the failure reason lands at the end), so
# only a bounded tail is held — a crash-looping CLI can spew without limit.
_STDERR_KEEP = 64 * 1024
# After terminate(), how long to wait before escalating to kill(). A CLI that
# shrugs off SIGTERM used to hang the wait() forever — the whole session with it.
_TERM_GRACE_S = 3.0


def native_effort(provider: str, level: str | None, model: str | None = None) -> str | None:
    """Validate a CLI control before changing state or creating a process."""
    if level is None:
        return None
    if provider not in ('codex', 'grok', 'gemini', 'antigravity'):
        raise ValueError('Effort is not implemented for this CLI adapter')
    if not isinstance(level, str):
        raise ValueError('Effort must be a supported string or None')
    from ..council_config import validate_effort
    normalized = level.strip().lower()
    if normalized == 'med':
        normalized = 'medium'
    return validate_effort(provider, normalized, model)


def native_effort_argv(provider: str, level: str | None, model: str | None = None) -> list[str]:
    level = native_effort(provider, level, model)
    if level is None:
        return []
    if provider == 'gemini':
        return []  # Per-run settings overlay; no dedicated native CLI flag.
    if provider in ('grok', 'antigravity'):
        return ['--effort', level]
    return ['-c', 'model_reasoning_effort=' + json.dumps(level)]


async def _reap(proc: asyncio.subprocess.Process) -> int:
    """Wait for exit, escalating to kill: never hang on a subprocess. The
    caller has either seen EOF (the CLI is done talking) or already sent
    terminate — either way, past the grace period the process holds nothing we
    still want.

    The final wait is bounded too, and for a subtle reason: a wait() that
    PARKS (called before the child was reaped) resolves only when every stdio
    pipe hits EOF — and children the CLI spawned inherit those pipes and can
    hold them open long after the CLI itself is dead. returncode is set by the
    reaper regardless; past the grace it is the answer."""
    try:
        return await asyncio.wait_for(proc.wait(), max(1.0, _TERM_GRACE_S))
    except asyncio.TimeoutError:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        return await asyncio.wait_for(proc.wait(), max(1.0, _TERM_GRACE_S))
    except asyncio.TimeoutError:
        # Abandoning the transport would leak its pipe fds until GC — close it,
        # which also severs the pipes the orphans are holding.
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        return proc.returncode if proc.returncode is not None else -9


async def _owned_cleanup(proc, *, terminate=False):
    """Let the dedicated subreaper stop/reap its descendants before it exits."""
    if terminate and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    task = asyncio.create_task(_reap(proc))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated cancellation must not abandon owned process cleanup.
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return task.result()


class CliAdapter:
    """Per-CLI knowledge: how to invoke the binary and read its JSON event stream.

    Subclasses set ``key``/``cmd``/``label`` and implement the three hooks. Both
    ``translate`` and ``capture_session`` consume decoded stream objects and can
    be exercised over recorded fixtures with no subprocess. ``commit_result``
    retains accounting only after the backend publishes a settled result.
    """

    key: str = "cli"
    cmd: str = "cli"
    label: str = "CLI"
    # True when the CLI takes an MCP server per-invocation (codex's `-c` overrides,
    # handled in argv); False when it must be registered persistently out-of-band
    # (grok/gemini) — the engine owns that register/deregister lifecycle.
    mcp_via_argv: bool = False

    def stdin_payload(self, prompt: str) -> bytes | None:
        """None keeps existing argv transport; adapters may own stdin framing."""
        return None

    def argv(
        self, prompt: str, *, cwd: str, resume_id: str | None, sandbox: str,
        mcp_config: dict | None = None,
    ) -> list[str]:
        raise NotImplementedError

    def translate(self, obj: dict, state: dict) -> list[Event]:
        """Translate one decoded stream object into zero or more Dream ``Event``s."""
        raise NotImplementedError

    def capture_session(self, obj: dict) -> str | None:
        """Return the resume/session id if this object carries one, else ``None``."""
        return None

    def commit_result(self, event: Event) -> None:
        """Commit accounting only when the backend publishes a settled result."""
        return None

    def mcp_register_argv(self, name: str, env: dict[str, str], server_cmd: list[str]) -> list[str] | None:
        """Argv to register a stdio MCP server with this CLI, or ``None`` if the CLI
        can't (so Dream's memory tools simply aren't offered to it)."""
        return None

    def mcp_deregister_argv(self, name: str) -> list[str] | None:
        return None


# Dream's permission mode → the CLI's own sandbox posture. Plan/ask stay read-only;
# the edit-friendly modes allow workspace writes. The CLI still runs its own approval
# within the sandbox — an honest near-equivalent to Dream's policy, not identical.
def sandbox_for_mode(mode: str) -> str:
    return "read-only" if mode in ("plan", "ask") else "workspace-write"


class CodexAdapter(CliAdapter):
    """codex (ChatGPT) — ``codex exec --json``. Event schema scouted live; see the
    Phase 2 plan's "Real event schema" section and the fixtures under tests/fixtures."""

    key = "codex"
    cmd = "codex"
    label = "ChatGPT · Codex"
    mcp_via_argv = True  # the dream server rides in via session-scoped `-c` overrides

    def __init__(self) -> None:
        self._usage_thread: str | None = None
        self._usage_totals: dict[str, int] = {}

    def stdin_payload(self, prompt: str) -> bytes:
        # Avoid process-list disclosure and the OS per-argument size limit.
        return prompt.encode('utf-8')

    def argv(
        self, prompt: str, *, cwd: str, resume_id: str | None, sandbox: str,
        mcp_config: dict | None = None,
    ) -> list[str]:
        # Options belong to `exec` and must precede the subcommand:
        #   codex exec [OPTIONS] resume <ID> [PROMPT]
        # Putting them after `resume` made every turn past the first die with
        # "unexpected argument '--sandbox' found" — `codex exec resume` takes no
        # sandbox flag. The first turn worked, so the breakage only showed up in
        # multi-turn work, which is all of it.
        # --skip-git-repo-check: Dream's workspace may not be a git repo (codex
        # otherwise refuses to run outside a "trusted" directory).
        argv = [self.cmd, "exec", "--json", "--skip-git-repo-check",
                "--sandbox", sandbox, "-C", cwd]
        argv += self._mcp_overrides(mcp_config)
        if resume_id:
            argv += ["resume", resume_id]
        argv.append('-')
        return argv

    @staticmethod
    def _mcp_overrides(mcp_config: dict | None) -> list[str]:
        """Session-scoped `-c mcp_servers.<name>.*` overrides that register a stdio
        MCP server for THIS invocation only — no mutation of ~/.codex/config.toml."""
        if not mcp_config:
            return []
        name = mcp_config["name"]
        out = [
            "-c", f'mcp_servers.{name}.command={json.dumps(mcp_config["command"], ensure_ascii=False)}',
            "-c", f"mcp_servers.{name}.args={json.dumps(mcp_config['args'], ensure_ascii=False)}",
        ]
        env = mcp_config.get("env") or {}
        if env:
            inline = "{" + ", ".join(f'{json.dumps(k, ensure_ascii=False)}={json.dumps(v, ensure_ascii=False)}'
                                     for k, v in env.items()) + "}"
            out += ["-c", f"mcp_servers.{name}.env={inline}"]
        return out

    def capture_session(self, obj: dict) -> str | None:
        if obj.get("type") == "thread.started":
            tid = obj.get("thread_id")
            return tid if isinstance(tid, str) and tid else None
        return None

    def mcp_register_argv(self, name: str, env: dict[str, str], server_cmd: list[str]) -> list[str] | None:
        argv = [self.cmd, "mcp", "add", name]
        for k, v in env.items():
            argv += ["--env", f"{k}={v}"]
        argv += ["--", *server_cmd]
        return argv

    def mcp_deregister_argv(self, name: str) -> list[str] | None:
        return [self.cmd, "mcp", "remove", name]

    def translate(self, obj: dict, state: dict) -> list[Event]:
        t = obj.get("type")
        if t == 'thread.started':
            thread = self.capture_session(obj)
            if thread and thread != self._usage_thread:
                self._usage_thread = thread
                self._usage_totals = {}
        if t == "item.started":
            return self._item_started(obj.get("item") or {})
        if t == "item.completed":
            return self._item_completed(obj.get("item") or {})
        if t == "turn.completed":
            raw = obj.get("usage") or {}
            # The exec emitter uses thread totals, including resumed turns.
            # Keep the provider snapshot separate from Dream's additive meter.
            if 'codex_usage' not in state:
                totals = {key: value for key, value in raw.items()
                          if key in ('input_tokens', 'cached_input_tokens', 'output_tokens',
                                     'reasoning_output_tokens', 'cache_write_input_tokens')
                          and type(value) is int and value >= 0}
                decreased = any(value < self._usage_totals.get(key, 0)
                                for key, value in totals.items())
                delta = ({} if decreased else
                         {key: value - self._usage_totals.get(key, 0)
                          for key, value in totals.items()})
                state['codex_usage'] = self._map_usage(delta)
                state['codex_usage_reset'] = decreased
            return [Event("result", {
                "is_error": False,
                "subtype": "success",
                "usage": state['codex_usage'],
                "usage_scope": "turn",
                "context_tokens": None,  # exec JSON does not expose the last request size
                "provider_usage": raw,
                "provider_usage_scope": "session",
                "usage_counter_reset": state['codex_usage_reset'],
                "total_cost_usd": None,
            })]
        # thread.started (captured via capture_session), turn.started, and anything
        # unrecognised carry no rendered event.
        return []

    def commit_result(self, event: Event) -> None:
        raw = event.data.get('provider_usage') or {}
        self._usage_totals.update({key: value for key, value in raw.items()
                                  if key in ('input_tokens', 'cached_input_tokens', 'output_tokens',
                                             'reasoning_output_tokens', 'cache_write_input_tokens')
                                  and type(value) is int and value >= 0})

    def _item_started(self, item: dict) -> list[Event]:
        it = item.get("type")
        if it == "command_execution":
            return [Event("tool_use", {
                "name": "shell",
                "input": {"command": item.get("command", "")},
                "id": item.get("id"),
            })]
        if it == "mcp_tool_call":
            # Dream's own tools (recall/remember/…) reach codex over MCP.
            return [Event("tool_use", {
                "name": item.get("tool", "mcp"),
                "server": item.get("server"),
                "input": item.get("arguments") or {},
                "id": item.get("id"),
            })]
        return []

    def _item_completed(self, item: dict) -> list[Event]:
        it = item.get("type")
        if it == "agent_message":
            return [Event("assistant_done", item.get("text", ""))]
        if it == "command_execution":
            exit_code = item.get("exit_code")
            return [Event("tool_result", {
                "name": "shell",
                "content": item.get("aggregated_output", ""),
                # None = still in progress / unknown, 0 = success; anything else is an error.
                "is_error": exit_code not in (0, None),
                "id": item.get("id"),
            })]
        if it == "mcp_tool_call":
            err = item.get("error")
            result = item.get("result")
            return [Event("tool_result", {
                "name": item.get("tool", "mcp"),
                "server": item.get("server"),
                "content": self._mcp_result_text(result, err),
                "is_error": (err is not None or item.get('status') == 'failed'
                             or isinstance(result, dict) and
                             (result.get('isError') is True or result.get('is_error') is True)),
                "id": item.get("id"),
            })]
        if it == "error":
            msg = item.get("message", "")
            # The skills-budget notice is routine housekeeping, not a warning worth
            # surfacing every turn — drop it; forward any other error as a system note.
            if self._is_skills_budget(msg):
                return []
            return [Event("system", msg)]
        return []

    @staticmethod
    def _mcp_result_text(result: Any, err: Any) -> str:
        """Flatten a codex mcp_tool_call result ({"content":[{"type":"text",...}]})
        to text, or surface the error."""
        if err is not None:
            return f"Error: {err}"
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                return "\n".join(p for p in parts if p)
            if isinstance(content, str):
                return content
        return "" if result is None else str(result)

    @staticmethod
    def _is_skills_budget(msg: str) -> bool:
        m = (msg or "").lower()
        return "skill" in m and "budget" in m

    @staticmethod
    def _map_usage(raw: dict) -> dict[str, Any]:
        """Expose additive turn deltas with both Codex and Dream accounting names."""
        return {
            **raw,
            "prompt_tokens": raw.get("input_tokens"),
            "completion_tokens": raw.get("output_tokens"),
            "cached_tokens": raw.get("cached_input_tokens"),
            "prompt_tokens_details": {"cached_tokens": raw.get("cached_input_tokens")},
        }


def adapter_for(key: str) -> CliAdapter:
    """The CLI adapter for a provider key. Adapters are imported lazily so the
    per-CLI modules (which import ``CliAdapter`` from here) don't create a cycle."""
    if key == "codex":
        return CodexAdapter()
    if key == "grok":
        from .grok_adapter import GrokAdapter

        return GrokAdapter()
    if key == "gemini":
        from .gemini_adapter import GeminiAdapter

        return GeminiAdapter()
    if key == "antigravity":
        from .antigravity_adapter import AntigravityAdapter
        return AntigravityAdapter()
    raise ValueError(f"no CLI adapter for '{key}'")


class CliAgentBackend(Backend):
    """Drive a CLI adapter one turn per subprocess.

    The first turn prepends Dream's system prompt + wake context to the user prompt
    as user-context text; later turns resume the same session via
    the captured id. The mode → sandbox mapping is supplied by ``sandbox_getter`` so
    the sandbox tracks Dream's permission mode as it changes mid-session.
    """

    def __init__(
        self,
        adapter: CliAdapter,
        *,
        system_prompt: str,
        cwd: str,
        model: str | None = None,
        sandbox_getter: Any = None,
        mcp_config: dict | None = None,
        idle_timeout: float | None = None,
        idle_pause_getter: Any = None,
        mode_getter: Any = None,
    ) -> None:
        self.adapter = adapter
        self.provider_label = adapter.label
        self.system_prompt = system_prompt
        self.cwd = cwd
        self.model = model
        self.sandbox_getter = sandbox_getter
        self.mode_getter = mode_getter
        # Registers Dream's stdio memory server for each turn (session-scoped) so the
        # CLI engine can recall/remember. None → the CLI runs with only its own tools.
        self.mcp_config = mcp_config
        self._session_id: str | None = None
        # The CLI file connect() resolved; None until then.
        self._executable: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._spawning: asyncio.Task | None = None
        self._interrupt_generation = 0
        self._effort: str | None = None
        self.idle_timeout = float(idle_timeout if idle_timeout is not None
                                  else os.environ.get('DREAM_CLI_IDLE_TIMEOUT', '300'))
        if not math.isfinite(self.idle_timeout) or not 0 < self.idle_timeout <= 86400:
            raise ValueError('CLI idle timeout must be positive and at most 86400 seconds')
        self.idle_pause_getter = idle_pause_getter
        self._idle_pauses = 0
        self._last_activity = time.monotonic()

    @contextmanager
    def pause_idle_timeout(self):
        """Parent wraps only human approval waits, not tool execution, in this scope."""
        self._idle_pauses += 1
        try:
            yield
        finally:
            self._idle_pauses -= 1
            self._last_activity = time.monotonic()

    async def _read_stdout(self, proc):
        """Read bytes with an idle deadline; partial lines and stderr are activity."""
        reading = asyncio.create_task(proc.stdout.read(65536))
        was_paused = False
        try:
            while True:
                paused = self._idle_pauses or (self.idle_pause_getter and self.idle_pause_getter())
                if paused or was_paused:
                    self._last_activity = time.monotonic()
                was_paused = bool(paused)
                remaining = self.idle_timeout - (time.monotonic() - self._last_activity)
                if remaining <= 0 and not paused:
                    raise asyncio.TimeoutError
                done, _ = await asyncio.wait({reading}, timeout=min(1.0, max(.001, remaining)))
                if done:
                    raw = reading.result()
                    if raw:
                        self._last_activity = time.monotonic()
                    return raw
        finally:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)

    async def connect(self) -> None:
        # DREAM-246: the lookup the picker and Council choices use, so the file shown as
        # installed is the file spawned. Installation only; sign-in is not checked here.
        self._executable = None
        executable = locate_command(self.adapter.cmd)
        if executable is None:
            raise RuntimeError(
                f"{self.adapter.label}: '{self.adapter.cmd}' not found on PATH "
                "or in the known install folders."
            )
        self._executable = executable

    async def ask(self, prompt: str) -> AsyncIterator[Event]:
        settings = nullcontext(None)
        if self.adapter.key == 'gemini':
            from ..gemini_thinking import thinking_environment
            settings = thinking_environment(self.model, self._effort, cwd=self.cwd,
                                            executable=self._executable)
        with settings as run_env:
            async with aclosing(self._ask(prompt, run_env=run_env)) as turn:
                async for event in turn:
                    yield event

    async def _ask(self, prompt: str, *, run_env: dict | None = None) -> AsyncIterator[Event]:
        if self._proc is not None or self._spawning is not None:
            raise RuntimeError('This CLI backend already has an active turn')
        sandbox = self.sandbox_getter() if self.sandbox_getter else "workspace-write"
        resume_id = self._session_id
        # First turn (no session yet): prepend the system prompt + wake context.
        # Resumed turns carry it forward server-side, so send the bare prompt.
        full_prompt = prompt if resume_id else f"{self.system_prompt}\n\n{prompt}"
        stdin_payload = self.adapter.stdin_payload(full_prompt)
        argv = self.adapter.argv(
            full_prompt, cwd=self.cwd, resume_id=resume_id, sandbox=sandbox,
            mcp_config=self.mcp_config,
        )

        options = []
        if self.model:
            options.extend(['--model', self.model])
        options.extend(native_effort_argv(self.adapter.key, self._effort, self.model))
        # Codex exec options precede resume; other CLI options precede their
        # existing arguments. All values remain separate argv cells.
        position = 2 if self.adapter.key == 'codex' else 1
        argv[position:position] = options
        if self.adapter.key == 'antigravity' and self.mode_getter and self.mode_getter() == 'auto':
            argv.append('--dangerously-skip-permissions')
        # Spawn the file connect() found. The adapter's argv stays pure (command
        # names); a backend that was never connected spawns the bare name as before.
        spawn_options: dict[str, Any] = {'env': run_env} if run_env is not None else {}
        if self.adapter.key in ('gemini', 'antigravity'):
            spawn_options['cwd'] = self.cwd
        if stdin_payload is not None:
            spawn_options['stdin'] = asyncio.subprocess.PIPE
        if self.adapter.key == 'antigravity':
            from ...mcp.bridge import DISCOVERY_ENV
            env = dict(os.environ if run_env is None else run_env)
            env.pop(DISCOVERY_ENV, None)
            env.pop('DREAM_SESSION_ID', None)
            if self.mcp_config:
                env.update(self.mcp_config.get('env') or {})
            spawn_options['env'] = env
        if self._executable is not None and argv and argv[0] == self.adapter.cmd:
            argv[0] = self._executable
            # An nvm install is an `env node` script whose node a launcher's PATH lacks. Only then
            # does the child get its own environment (the parent's plus that one folder on PATH);
            # otherwise no env is passed at all, exactly as before. os.environ is never changed.
            child_env = node_child_env(self.adapter.cmd, self._executable, env=spawn_options.get('env', run_env))
            if child_env is not None:
                spawn_options['env'] = child_env

        interrupt_generation = self._interrupt_generation
        self._spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *supervised_command(argv),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STDOUT_LIMIT,
            start_new_session=True,
            **spawn_options,
        ))
        try:
            proc = await asyncio.shield(self._spawning)
            self._proc = proc
        except asyncio.CancelledError:
            while not self._spawning.done():
                try:
                    await asyncio.shield(self._spawning)
                except asyncio.CancelledError:
                    pass
            proc = self._spawning.result()
            await _owned_cleanup(proc, terminate=True)
            raise
        finally:
            self._spawning = None
        self._last_activity = time.monotonic()

        # Drain stderr concurrently so a chatty CLI can't deadlock on a full pipe
        # while we're only pulling stdout.
        stderr_chunks: list[bytes] = []
        native_denied = False

        async def _drain_stderr() -> None:
            nonlocal native_denied
            assert proc.stderr is not None
            kept = 0
            diagnostic_tail = b''
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                self._last_activity = time.monotonic()
                if self.adapter.key == 'antigravity':
                    from .antigravity_adapter import denied_diagnostic
                    diagnostic_tail = diagnostic_tail[-512:] + chunk
                    native_denied = native_denied or denied_diagnostic(diagnostic_tail)
                stderr_chunks.append(chunk)
                kept += len(chunk)
                # Trim from the FRONT: the tail is what gets shown.
                while kept > _STDERR_KEEP and len(stderr_chunks) > 1:
                    kept -= len(stderr_chunks.pop(0))

        stderr_task = asyncio.create_task(_drain_stderr())

        async def _feed_stdin():
            try:
                proc.stdin.write(stdin_payload)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

        feed_task = asyncio.create_task(_feed_stdin()) if stdin_payload is not None else None

        state: dict[str, Any] = {}
        pending_result: Event | None = None
        duplicate_result = False
        fatal_error: str | None = None
        completed = False
        idle_expired = False
        rc: int | None = None
        primary_error: BaseException | None = None
        try:
            assert proc.stdout is not None
            buffer = bytearray()
            discarding = False
            while True:
                raw = await self._read_stdout(proc)
                if not raw:
                    # Preserve the old readline behavior for a final line with
                    # no newline. Oversized lines are discarded through newline.
                    if not buffer or discarding:
                        break
                    raw = b'\n'
                buffer.extend(raw)
                while (end := buffer.find(b'\n')) >= 0:
                    line = bytes(buffer[:end])
                    del buffer[:end + 1]
                    if discarding or len(line) > STDOUT_LIMIT:
                        discarding = False
                        continue
                    try:
                        obj = json.loads(line.decode('utf-8', 'replace'))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(obj, dict):
                        continue
                    sid = self.adapter.capture_session(obj)
                    if sid:
                        self._session_id = sid
                    for ev in self.adapter.translate(obj, state):
                        if ev.kind == "result":
                            if pending_result is None:
                                pending_result = ev
                            else:
                                duplicate_result = True
                            continue
                        if ev.kind == "error" and fatal_error is None:
                            fatal_error = str(ev.data)[:800]
                        yield ev
                        # Time spent in the event consumer is not provider idle.
                        self._last_activity = time.monotonic()
                if len(buffer) > STDOUT_LIMIT:
                    buffer.clear()
                    discarding = True
            completed = True
        except asyncio.TimeoutError:
            idle_expired = True
        except GeneratorExit:
            # Close is control flow, not a primary failure. Let cleanup errors
            # raised in finally reach aclose instead of annotating a suppressed exit.
            raise
        except BaseException as exc:
            primary_error = exc
        finally:
            # If the consumer aborted early (never reached stdout EOF), terminate so
            # we don't block forever on a still-running CLI. Keep both cleanup
            # stages owned and the turn guard held even under repeated cancellation.
            def retain_cleanup_error(primary, secondary):
                detail = f"{type(secondary).__name__}: {str(secondary)[:800]}"
                notes = getattr(secondary, "__notes__", ())
                if notes:
                    detail += "\n" + "\n".join(notes)[-800:]
                primary.add_note("CLI cleanup failed: " + detail)

            async def finish_process():
                process_error = None
                try:
                    exit_code = await _owned_cleanup(proc, terminate=not completed)
                    await asyncio.wait({stderr_task}, timeout=_TERM_GRACE_S)
                    return exit_code
                except BaseException as exc:
                    process_error = exc
                    raise
                finally:
                    if feed_task is not None:
                        feed_task.cancel()
                        await asyncio.gather(feed_task, return_exceptions=True)
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                    # Inspect after cancellation too: closing a pending reader
                    # can fail. Ordinary CancelledError is expected settlement.
                    if not stderr_task.cancelled():
                        stderr_error = stderr_task.exception()
                        if stderr_error is not None:
                            if process_error is None:
                                raise stderr_error
                            retain_cleanup_error(process_error, stderr_error)

            cleanup = asyncio.create_task(finish_process())
            try:
                while not cleanup.done():
                    try:
                        # Waiting observes completion without throwing a child
                        # task's exception through the generator's aclose await.
                        # Cancellation of this wait does not cancel owned cleanup.
                        await asyncio.wait({cleanup})
                    except asyncio.CancelledError as exc:
                        if primary_error is None:
                            primary_error = exc
                try:
                    rc = cleanup.result()
                except BaseException as exc:
                    if primary_error is None:
                        primary_error = exc
                    else:
                        retain_cleanup_error(primary_error, exc)
            finally:
                if self._proc is proc:
                    self._proc = None

            if primary_error is not None:
                raise primary_error

        if idle_expired:
            yield Event('error', f'{self.adapter.label} idle timeout after {self.idle_timeout:g} seconds; inspect completed work before retrying.')
            return

        # Only EOF plus settled process/stderr cleanup can publish terminal
        # evidence. Keep the first accounting record even if the wire repeats it.
        interrupted = interrupt_generation != self._interrupt_generation
        tail = ("check sign-in, model access and native permissions in agy"
                if self.adapter.key == 'antigravity' else
                b"".join(stderr_chunks).decode("utf-8", "replace").strip()[-800:])
        if pending_result is not None:
            diagnostics = []
            if interrupted:
                diagnostics.append(f"{self.adapter.label} interrupted before terminal publication")
            if rc != 0:
                diagnostics.append(f"{self.adapter.label} exited {rc}: {tail}".rstrip())
            if duplicate_result:
                diagnostics.append("CLI protocol failure: multiple terminal results")
            if fatal_error is not None:
                diagnostics.append(f"CLI fatal error: {fatal_error}")
            if native_denied:
                diagnostics.append('Antigravity reported a native permission denial; review permissions in agy')
            if diagnostics:
                data = pending_result.data
                reason = data.get("error")
                diagnostic = "; ".join(diagnostics)
                pending_result = Event("result", {
                    **data, "is_error": True, "subtype": "error",
                    "provider_subtype": data.get("subtype"), "cli_exit_code": rc,
                    "error": f"{reason}; {diagnostic}" if reason else diagnostic,
                })
            self.adapter.commit_result(pending_result)
            yield pending_result
        elif interrupted:
            yield Event("error", f"{self.adapter.label} interrupted before terminal publication; inspect completed work before retrying.")
        elif rc != 0:
            yield Event("error", f"{self.adapter.label} exited {rc}: {tail}".rstrip())
        else:
            yield Event("error", f"{self.adapter.label} exited without a terminal result; inspect completed work before retrying.")

    async def set_model(self, model: str | None) -> None:
        if model:
            self.model = model

    def set_effort(self, level: str | None) -> None:
        self._effort = native_effort(self.adapter.key, level, self.model)

    async def interrupt(self) -> None:
        proc = self._proc
        if proc is not None or self._spawning is not None:
            # Attribute the request before awaiting spawn/cleanup. A later turn
            # captures the new generation and cannot inherit this interruption.
            self._interrupt_generation += 1
        if proc is None and self._spawning is not None:
            proc = await asyncio.shield(self._spawning)
        if proc is not None:
            await _owned_cleanup(proc, terminate=True)

    async def disconnect(self) -> None:
        await self.interrupt()
