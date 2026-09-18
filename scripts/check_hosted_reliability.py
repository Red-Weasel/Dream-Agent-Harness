#!/usr/bin/env python3
"""Opt-in, synthetic hosted checks through Dream's real backends.

No live requests run without --live. Each model gets at most four turns; this
measures protocol/continuation and local Stop ownership, not representative task
quality or server-side cancellation. Auth files are copied into a temporary HOME
and deleted with all synthetic native sessions. No owner configuration is copied.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import aclosing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time


MODELS = {'sol': ('codex', 'gpt-5.6-sol'), 'terra': ('codex', 'gpt-5.6-terra'),
          'opus': ('anthropic', 'opus'), 'sonnet': ('anthropic', 'sonnet')}


def failure_category(detail):
    text = str(detail).lower()
    for category, needles in (
        ('rate_limit', ('429', 'rate limit', 'rate_limit')),
        ('budget', ('error_max_budget_usd',)),
        ('authentication', ('401', 'authentication', 'not logged in', 'auth expired')),
        ('connection', ('connection', 'network', 'dns', 'resolve', 'socket')),
        ('interrupted', ('interrupt', 'cancel')),
        ('missing_terminal', ('without a terminal', 'without terminal')),
    ):
        if any(word in text for word in needles):
            return category
    return 'provider_error'


def assess_events(events, *, expected):
    """Export only closed categories/counts; never provider text or metadata."""
    terminals = [e.data for e in events if e.kind == 'result']
    errors = [e.data for e in events if e.kind == 'error']
    answers = [str(e.data).strip() for e in events if e.kind == 'assistant_done']
    category = None
    if errors:
        category = failure_category(errors[0])
    elif not terminals:
        category = 'missing_terminal'
    elif len(terminals) != 1:
        category = 'duplicate_terminal'
    elif not isinstance(terminals[0], dict) or type(terminals[0].get('is_error')) is not bool:
        category = 'invalid_terminal'
    elif terminals[0]['is_error']:
        category = failure_category(terminals[0].get('error', 'provider error'))
    elif terminals[0].get('subtype') != 'success':
        category = 'invalid_terminal'
    elif any(e.kind == 'tool_use' for e in events):
        category = 'unexpected_tool_use'
    elif expected is not None and answers != [expected]:
        category = 'response_mismatch'
    safe_subtypes = {'success', 'error_max_budget_usd', 'error_max_turns',
                     'error_during_execution', 'error', 'error_max_structured_output_retries'}
    safe_reasons = {'completed', 'interrupted', 'cancelled', 'max_turns', 'max_budget_usd'}
    return {'status': 'failed' if category else 'passed', 'category': category,
            'terminal_count': len(terminals), 'response_observed': bool(answers),
            'terminal_subtypes': [t.get('subtype') if isinstance(t.get('subtype'), str)
                                  and t.get('subtype') in safe_subtypes else 'unknown'
                                  for t in terminals if isinstance(t, dict)],
            'terminal_reasons': [t.get('terminal_reason') if isinstance(t.get('terminal_reason'), str)
                                and t.get('terminal_reason') in safe_reasons
                                else 'unknown' for t in terminals if isinstance(t, dict)],
            'event_counts': dict(Counter(e.kind for e in events if e.kind in
                ('result', 'error', 'assistant_done', 'text_delta', 'tool_use', 'tool_result')))}


async def observe_turn(backend, prompt, *, expected, timeout, stop_after=None):
    from dream.core.backends.base import Event
    events = []
    stopped = False
    interrupt_completed = False
    started = time.monotonic()

    async def stop():
        nonlocal stopped, interrupt_completed
        await asyncio.sleep(stop_after)
        stopped = True
        await backend.interrupt()
        interrupt_completed = True

    stopper = asyncio.create_task(stop()) if stop_after is not None else None
    deadline = False
    retained = 0
    try:
        async with asyncio.timeout(timeout):
            async with aclosing(backend.ask(prompt)) as stream:
                async for event in stream:
                    retained += len(str(event.data))
                    if len(events) >= 4096 or retained > 262144:
                        events.append(Event('error', 'Qualification output bound exceeded'))
                        break
                    events.append(event)
    except TimeoutError:
        deadline = True
    except Exception as exc:
        events.append(Event('error', str(exc)))
    finally:
        if stopper is not None:
            # A finished response must not leave a timer targeting a later turn.
            if not stopped:
                stopper.cancel()
            results = await asyncio.gather(stopper, return_exceptions=True)
            if stopped and isinstance(results[0], BaseException):
                events.append(Event('error', 'Stop cleanup failed'))
    result = assess_events(events, expected=expected)
    if deadline:
        result.update(status='failed', category='deadline')
    elif stopped and interrupt_completed and result['category'] == 'interrupted':
        result.update(status='interrupted')
    elif stop_after is not None and result['status'] == 'passed':
        result.update(status='not_observed', category='completed_before_stop')
    result.update(elapsed_seconds=round(time.monotonic() - started, 3),
                  stop_requested=stopped,
                  local_cleanup_complete=(backend._proc is None and backend._spawning is None)
                      if hasattr(backend, '_proc') else None)
    return result


def private_environment(root, repo, source):
    env = {key: source[key] for key in ('PATH', 'LANG', 'TERM') if key in source}
    for key in ('HOME', 'DREAM_ROOT', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR'):
        directory = root / key
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    env.update(PYTHONPATH=str(repo), DREAM_SEMANTIC_MEMORY='0', DREAM_RERANK='0',
               DREAM_CONSOLIDATE='0', DREAM_GUI_OPEN='0', DREAM_MONITOR='0',
               DREAM_SEARXNG_AUTOSTART='0', CUDA_VISIBLE_DEVICES='',
               HIP_VISIBLE_DEVICES='', ROCR_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1', CLAUDE_CODE_SAFE_MODE='1')
    return env


def isolated_codex_adapter():
    from dream.core.backends.cli_agent import CodexAdapter

    class IsolatedCodex(CodexAdapter):
        def argv(self, *args, **kwargs):
            argv = super().argv(*args, **kwargs)
            argv[2:2] = ['--ignore-user-config', '--ignore-rules']
            return argv

    return IsolatedCodex()


def make_backend(provider, model, workspace):
    system = 'Synthetic reliability check. Reply exactly as requested. Do not use tools.'
    if provider == 'codex':
        from dream.core.backends.cli_agent import CliAgentBackend
        backend = CliAgentBackend(isolated_codex_adapter(), system_prompt=system,
            cwd=str(workspace), model=model, sandbox_getter=lambda: 'read-only', idle_timeout=30)
        backend.set_effort('low')
        return backend
    from dream.core.backends.anthropic import AnthropicBackend
    from claude_agent_sdk import create_sdk_mcp_server

    class IsolatedAnthropic(AnthropicBackend):
        def _build_options(self):
            options = super()._build_options()
            options.tools = []
            options.allowed_tools = []
            options.cli_path = shutil.which('claude')
            options.extra_args = {'safe-mode': None, 'disable-slash-commands': None}
            options.max_turns = 1
            options.max_budget_usd = 0.30
            return options

    return IsolatedAnthropic(system_prompt=system,
        mcp_server=create_sdk_mcp_server(name='synthetic', tools=[]),
        preapproved_tool_ids=[], agents=None, permission_cb=None, model=model,
        cwd=str(workspace), stderr_cb=lambda line: None)


async def run_worker(name, timeout):
    provider, model = MODELS[name]
    workspace = Path(os.environ['DREAM_ROOT'])
    backend = make_backend(provider, model, workspace)
    records = []
    nonce = secrets.token_hex(6)
    try:
        async with asyncio.timeout(timeout):
            await backend.connect()
        first = await observe_turn(backend,
            f'Remember synthetic token {nonce}. Reply exactly READY.', expected='READY', timeout=timeout)
        records.append({'turn': 'first', **first})
        if first['status'] == 'passed':
            continuation = await observe_turn(backend, 'Reply with only the synthetic token from earlier.',
                expected=nonce, timeout=timeout)
            records.append({'turn': 'explicit_continuation', **continuation})
            if continuation['status'] == 'passed':
                stopped = await observe_turn(backend, 'Print the integers from 1 to 2000, one per line.',
                    expected=None, timeout=timeout, stop_after=2)
                records.append({'turn': 'stop', **stopped})
                after = await observe_turn(backend,
                    'Explicitly continue. Reply with only the synthetic token from the first turn.',
                    expected=nonce, timeout=timeout)
                records.append({'turn': 'explicit_after_stop', **after})
    except Exception as exc:
        records.append({'turn': 'connection', 'status': 'failed', 'category': failure_category(exc)})
    finally:
        try:
            async with asyncio.timeout(10):
                await backend.disconnect()
        except Exception:
            records.append({'turn': 'disconnect', 'status': 'failed', 'category': 'cleanup'})
    return {'model': model, 'model_identity': 'requested_alias' if provider == 'anthropic' else 'requested_catalog_slug',
            'provider': provider, 'evidence': 'actual_hosted_attempt',
            'turns': records, 'server_cancellation_verified': False}


def run_private_worker(argv, *, cwd, env, timeout):
    # Use the same descendant supervisor as Dream's command tools, including
    # worker timeout and detached provider children. The retained output is bounded.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dream.core.execution import run_owned
    return asyncio.run(run_owned(argv, cwd=cwd, env=env, timeout=timeout, max_output=65536))


def run_live(name, timeout, repo):
    provider, model = MODELS[name]
    command = 'codex' if provider == 'codex' else 'claude'
    owner_home = Path.home()
    relative = Path('.codex/auth.json' if provider == 'codex' else '.claude/.credentials.json')
    auth = owner_home / relative
    unavailable = {'model': model, 'provider': provider, 'evidence': 'not_run', 'turns': []}
    if not shutil.which(command) or not auth.is_file():
        return {**unavailable, 'category': 'missing_cli_or_auth'}
    if provider == 'codex':
        catalog = owner_home / '.codex/models_cache.json'
        try:
            known = [item['slug'] for item in json.loads(catalog.read_text())['models']]
        except (OSError, ValueError, KeyError, TypeError):
            known = []
        if model not in known:
            return {**unavailable, 'category': 'model_not_in_local_catalog'}
    with tempfile.TemporaryDirectory(prefix='dream-hosted-') as temporary:
        root = Path(temporary)
        env = private_environment(root, repo, os.environ)
        env['DREAM_HOSTED_QUALIFICATION_WORKER'] = '1'
        destination = Path(env['HOME']) / relative
        destination.parent.mkdir(mode=0o700)
        shutil.copyfile(auth, destination)
        destination.chmod(0o600)
        child = run_private_worker([sys.executable, str(Path(__file__).resolve()), '--live',
                '--worker', name, '--timeout', str(timeout)], cwd=env['DREAM_ROOT'], env=env,
                timeout=timeout * 5 + 15)
        if child.timed_out:
            return {**unavailable, 'evidence': 'actual_hosted_attempt', 'category': 'worker_deadline'}
        # Only our marked, content-free JSON crosses the private worker boundary.
        for line in reversed(child.output.decode('utf-8', 'replace').splitlines()):
            if line.startswith('DREAM_QUALIFICATION='):
                return json.loads(line.split('=', 1)[1])
        return {**unavailable, 'evidence': 'actual_hosted_attempt', 'category': 'worker_failed',
                'exit_code': child.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--models', nargs='+', choices=MODELS, default=list(MODELS))
    parser.add_argument('--timeout', type=float, default=40)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=MODELS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.live:
        parser.error('Live hosted calls require explicit --live opt-in')
    if not 5 <= args.timeout <= 60:
        parser.error('--timeout must be between 5 and 60 seconds')
    if args.worker:
        if os.environ.get('DREAM_HOSTED_QUALIFICATION_WORKER') != '1':
            parser.error('Internal worker requires the private environment created by this runner')
        print('DREAM_QUALIFICATION=' + json.dumps(asyncio.run(run_worker(args.worker, args.timeout))))
        return
    report = {'schema_version': 1, 'observed_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'synthetic protocol and local Stop; no quality ranking',
              'results': [run_live(name, args.timeout, Path(__file__).resolve().parents[1])
                          for name in dict.fromkeys(args.models)]}
    encoded = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end='')


if __name__ == '__main__':
    main()
