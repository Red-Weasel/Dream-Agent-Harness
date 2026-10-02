"""The Frontier review loop's model calls (DREAM-218, ADR-070). One call() per step, on the lean postures:

- Claude: a custom system prompt, no built-in tools, no user, project or local settings, no MCP servers (strict),
  hooks off, one turn, a permission callback that denies everything, an environment allowlisted like Sleepwalk's;
  through the Council's own stream and cancellation handling (moe._consult_anthropic with `options=`).
- Codex: isolated as Sleepwalk's runs are (ADR-064), read-only, low effort, the prompt on stdin.
- Grok: read-only (`--permission-mode plan`) through a prompt file; it cannot be isolated.
- HTTP (openai, xai, machx): no tools. A machx persona is refused while no model is loaded: a workflow never loads one.

Each call runs in a new private 0700 folder outside any git repository (the Sleepwalk run roots) and has its own
meter; every call also feeds the run's RunMeter, whose check() refuses a call once the run's cap is reached (a second
net behind the loop's projection). call() returns {text, usage, usage_source, duration_ms, outcome}: usage as the
provider reported it ("provider"), else for an answer the UTF-8 bytes / 3.5 estimate of what was sent and what came
back ("estimated"), else unknown ("unknown", counted as nothing: the meter reads "at least"). An outcome is "ok",
"unavailable" or "timeout"; a cancelled call raises CancelledError once its process group is gone."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time
from types import SimpleNamespace

from .. import __version__
from ..core import moe
from ..core.cli_review import CLAUDE_SIGN_IN, runner_env
from ..core.context_budget import estimate
from ..core.providers import get_provider
from ..local import machx
from ..sleepwalk.runner import _UNAVAILABLE, NotIsolated, _inside_repository, _work_folder
from ..telemetry.runtime import RunMeter
from .frontier import RUN_SECONDS

NOT_LOADED = 'The local model is not loaded; a workflow never loads one.'


def run_meter(run_id: str, cap: int, seconds: float = RUN_SECONDS) -> RunMeter:
    """The run-level meter every call of a run feeds: its check() refuses a call once the tokens reach `cap` or the
    active time reaches `seconds`."""
    return RunMeter(run_id, 0, SimpleNamespace(max_run_tools=400, max_run_tokens=cap, max_run_seconds=seconds))


class CallMeter:
    """One call's meter: the usage its provider reports, counted as RunMeter counts it (input with cached input, and
    output), passed on to the run's meter when there is one."""

    def __init__(self, run: RunMeter | None, phase: str):
        self.run, self.phase = run, phase
        self.own = RunMeter(phase, 0, None)       # its usage() arithmetic only; nothing is recorded or checked
        self.reported = False

    def check(self):
        if self.run is not None:
            self.run.check()

    def before_tool(self, name):
        if self.run is not None:
            self.run.before_tool(name)

    def usage(self, usage, phase='lead'):
        self.reported = True
        self.own.usage(usage)
        if self.run is not None:
            self.run.usage(usage, phase=self.phase)

    def record(self, kind, **metadata):
        if self.run is not None:
            self.run.record(kind, **metadata)


def lean_claude_options(system: str, cwd: str, model: str | None, effort: str | None):
    """Claude without Claude Code's preset prompt, tools or the owner's settings (ADR-070; DREAM-140 is unchanged for
    the Council)."""
    from claude_agent_sdk import ClaudeAgentOptions, PermissionResultDeny

    async def deny(tool_name, tool_input, context):
        return PermissionResultDeny(message='Not run: a Frontier loop call has no tools.')

    allowed = runner_env(CLAUDE_SIGN_IN)
    return ClaudeAgentOptions(
        system_prompt=system, tools=[], setting_sources=[], mcp_servers={}, strict_mcp_config=True,
        settings='{"disableAllHooks":true}', max_turns=1, can_use_tool=deny, cwd=cwd, model=model, effort=effort,
        # The SDK adds this to the parent's environment: everything outside the allowlist is blanked (as Sleepwalk's).
        env={**{k: '' for k in os.environ if k not in allowed and k != 'CLAUDE_CODE_ENTRYPOINT'},
             'CLAUDE_AGENT_SDK_CLIENT_APP': f'dream/{__version__}'})


async def call(persona: dict, system: str, prompt: str, *, timeout: float, effort: str | None = None,
               run: RunMeter | None = None) -> dict:
    """One model call for a persona of a validated template. `effort` applies to Claude only (Codex is always low;
    Grok and the HTTP providers take their own defaults)."""
    started = time.monotonic()
    provider = get_provider(persona['provider'])
    model = persona['model'] or None
    meter = CallMeter(run, f"frontier:{provider.key}")
    text, outcome = '', 'ok'
    try:
        if provider.key == 'machx' and not await asyncio.to_thread(machx.model_loaded):
            text, outcome = NOT_LOADED, 'unavailable'
        else:
            with _work_folder(prefix='dream-frontier-') as work:
                folder = Path(work).resolve()
                if _inside_repository(folder):
                    raise NotIsolated(f'the call folder {folder} is inside a git repository')
                async with asyncio.timeout(timeout):
                    text = await _invoke(provider, model, system, prompt, str(folder), effort, meter)
            if not text.strip() or _UNAVAILABLE.search(text.strip()):
                outcome = 'unavailable'
    except TimeoutError:
        text, outcome = f'No answer within {timeout:g} seconds', 'timeout'
    except Exception as exc:        # noqa: BLE001 -- a call never sinks the loop; the cancellation still propagates
        text, outcome = f"[{persona['name']}: unavailable — {type(exc).__name__}: {exc}]", 'unavailable'
    if meter.reported:
        usage, source = {'prompt_tokens': meter.own.prompt_tokens, 'completion_tokens': meter.own.output_tokens}, 'provider'
    elif outcome == 'ok':
        usage, source = {'prompt_tokens': estimate(system + '\n\n' + prompt), 'completion_tokens': estimate(text)}, 'estimated'
        if run is not None:
            run.usage(usage, phase=meter.phase)
    else:
        usage, source = {'prompt_tokens': 0, 'completion_tokens': 0}, 'unknown'
    return {'text': text, 'usage': usage, 'usage_source': source,
            'duration_ms': int((time.monotonic() - started) * 1000), 'outcome': outcome}


async def _invoke(provider, model, system, prompt, cwd, effort, meter) -> str:
    if provider.kind == 'anthropic':
        return await moe._consult_anthropic(provider, prompt, cwd=cwd, model=model, system=system, meter=meter,
                                            options=lean_claude_options(system, cwd, model, effort))
    if provider.key == 'codex':
        return await moe._consult_cli(provider, prompt, cwd=cwd, model=model, effort='low', mode='ask', system=system,
                                      isolate=True, meter=meter)
    if provider.kind == 'cli':      # Grok: read-only in an empty folder; it has no switch to leave the owner's setup
        return await moe._consult_cli(provider, prompt, cwd=cwd, model=model, mode='ask', system=system, meter=meter)
    return await moe._consult_openai(provider, prompt, cwd=cwd, model=model, system=system, meter=meter)
