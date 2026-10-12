#!/usr/bin/env python3
"""Run fixed offline production-contract gates and emit an evidence report.

The watchdog limits fixture execution only. It does not set a model task budget.
No provider authentication, inference, or owner desktop qualification is performed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
GATES = {
    'http': ('tests/test_production_qualification.py', 'tests/test_model_adaptation.py',
             'tests/test_provider_capabilities.py', 'tests/test_capability_integration.py',
             'tests/test_missing_vision_sidecar.py', 'tests/test_backend_resilience.py'),
    'cli_sdk': ('tests/test_council_effort.py', 'tests/test_cli_exit_reconciliation.py',
                'tests/test_sdk_cancellation_history.py', 'tests/test_gemini_adapter.py',
                'tests/test_gemini_mcp_registration.py', 'tests/test_gemini_mcp_session_scope.py',
                'tests/test_engine_start_cleanup.py', 'tests/test_council_native_controls.py',
                'tests/test_council_requested_catalog.py', 'tests/test_model_catalog.py',
                'tests/test_council_cli_parity.py', 'tests/test_cli_discovery.py',
                'tests/test_council_config.py', 'tests/test_gemini_thinking_overlay.py',
                'tests/test_gemini_thinking_integration.py', 'tests/test_council_ui.py',
                'tests/test_antigravity_adapter.py', 'tests/test_antigravity_routing.py',
                'tests/test_antigravity_integration.py', 'tests/test_council_login_ui.py',
                'tests/test_provider_login.py', 'tests/test_picker.py', 'tests/test_council_startup.py',
                'tests/test_session_navigation_ui.py', 'tests/test_session_navigation_mobile_ui.py'),
    'tools': ('tests/test_computer_action_outcomes.py', 'tests/test_permission_hardening.py',
              'tests/test_tool_provenance.py'),
    'delivery': ('tests/test_delivery_attempts.py', 'tests/test_verifier_execution_evidence.py',
                 'tests/test_media_completion_status.py'),
    'continuity': ('tests/test_context_selection_quality.py', 'tests/test_tool_outcome_evidence.py',
                   'tests/test_memory_directory_durability.py'),
    'package': ('tests/test_distribution_audit.py', 'tests/test_curated_skills.py',
                 'tests/test_diagnostic_assets.py'),
    'managed': ('tests/test_phase_runner.py', 'tests/test_phase_http_workflow.py',
                'tests/test_phase_plan.py', 'tests/test_phase_entrypoint.py',
                'tests/test_task_checkpoints.py', 'tests/test_run_resume.py',
                'tests/test_recovery_runtime.py', 'tests/test_generation_cut_http.py',
                'tests/test_frontier_help.py', 'tests/test_responses_phase_workflow.py',
                'tests/test_endurance_report.py', 'tests/test_phase_endurance_report.py'),
    'context': ('tests/test_context_inspection.py', 'tests/test_context_inspection_http.py',
                'tests/test_responses_context_inspection.py', 'tests/test_description_discovery.py',
                'tests/test_description_discovery_http.py'),
    'local_fixture': ('tests/test_local_harness_eval_config.py', 'tests/test_local_harness_eval_http.py',
                      'tests/test_local_harness_eval_cli.py', 'tests/test_local_harness_eval_identity.py',
                      'tests/test_harness_eval_offline.py', 'tests/test_local_eval_catalog.py',
                      'tests/test_local_eval_skills_api.py', 'tests/test_local_eval_skills_storage.py',
                      'tests/test_local_eval_skills_races.py', 'tests/test_local_eval_skill_order.py'),
    'worker_storage': ('tests/test_worker_checkpoints.py', 'tests/test_worker_checkpoints_storage.py',
                       'tests/test_worker_checkpoint_pause_budget.py',
                       'tests/test_worker_continuation.py', 'tests/test_worker_journal_http.py',
                       'tests/test_worker_journal_controls.py', 'tests/test_worker_journal_crash.py',
                       'tests/test_worker_journal_review_regressions.py',
                       'tests/test_worker_journal_startup_crash.py', 'tests/test_worker_survival.py',
                       'tests/test_worker_checkpoint_originals.py', 'tests/test_worker_checkpoint_process.py',
                       'tests/test_worker_checkpoint_write_guards.py', 'tests/test_worker_checkpoint_review_regressions.py',
                       'tests/test_worker_delegations.py', 'tests/test_worker_delegation_boundaries.py',
                       'tests/test_worker_delegation_review_regressions.py',
                       'tests/test_managed_loop.py',
                       'tests/test_worker_output_restore.py', 'tests/test_worker_evidence_restore.py',
                       'tests/test_parent_continuation.py', 'tests/test_parent_continuation_http.py',
                       'tests/test_parent_continuation_review_regressions.py',
                       'tests/test_parent_continuation_boundary_regressions.py',
                       'tests/test_worker_contracts.py', 'tests/test_worker_evidence_durability.py',
                       'tests/test_worker_partial_results.py',
                       'tests/test_tool_artifacts.py', 'tests/test_artifact_tools.py',
                       'tests/test_native_files.py', 'tests/test_read_file_lines.py'),
}
SOURCES = ('dream/core/backends/openai_compat.py', 'dream/core/backends/cli_agent.py',
           'dream/core/backends/anthropic.py', 'dream/core/backends/gemini_adapter.py',
           'dream/core/capabilities.py', 'dream/core/engine.py',
           'dream/projects/library.py', 'dream/telemetry/turn.py', 'dream/media/providers.py',
           'dream/memory/longterm.py', 'dream/memory/store.py',
           'dream/diagnostics.py', 'dream/config.py', 'pyproject.toml',
           'scripts/audit_distribution.py', 'dream/computer.py', 'scripts/qualify_production.py',
           'dream/core/phase_runner.py', 'dream/core/phase_plan.py',
           'dream/core/task_checkpoints.py', 'dream/core/run_resume.py',
           'dream/core/recovery.py', 'dream/core/recovery_runtime.py',
           'dream/core/help_continuation.py', 'dream/core/frontier_help.py',
           'dream/core/responses_help.py', 'dream/core/context_inspection.py',
           'dream/core/context_counter.py', 'dream/core/discovery.py',
           'dream/core/stream_repetition.py', 'dream/core/stream_text_repetition.py',
           'dream/core/tool_artifacts.py', 'dream/core/endurance_report.py',
           'dream/gui/lucid_routes.py', 'dream/gui/static/library.js',
           'dream/core/loop.py', 'dream/core/run_state.py', 'dream/memory/tasks.py',
           'scripts/report_endurance.py', 'dream/local_harness_eval.py',
           'dream/harness_eval.py', 'dream/eval_fixtures/harness_cases.json', 'dream/local_eval_catalog.py',
           'dream/tools/registry.py', 'dream/tools/demonstration_tools.py', 'dream/tools/capability_tools.py',
           'dream/local_eval_skills.py', 'dream/tools/installed_skill_tools.py', 'dream/tools/artifact_tools.py',
           'dream/core/worker_checkpoints.py', 'dream/tools/native.py', 'dream/tools/files.py',
           'dream/core/worker_result.py', 'dream/core/worker_evidence.py',
           'dream/core/parent_continuation.py', 'dream/core/worker_continuation.py',
           'tests/test_local_subagents.py')
SOURCES += ('dream/core/backends/antigravity_adapter.py', 'dream/core/providers.py',
            'dream/core/provider_login.py', 'dream/mcp/server.py', 'dream/mcp/bridge.py',
            'dream/core/settings.py', 'dream/prompt_optimizer.py', 'dream/tui/app.py',
            'dream/tui/picker.py', 'dream/tui/critique_cmd.py', 'dream/desktop/startup.py',
            'dream/__main__.py', 'dream/core/model_catalog.py', 'dream/core/council_config.py',
            'dream/core/moe.py', 'dream/core/cli_review.py', 'dream/core/cli_discovery.py',
            'dream/core/gemini_thinking.py', 'dream/gui/static/council.js',
            'dream/gui/static/workspace.js', 'dream/gui/static/controls.js',
            'dream/gui/static/workspace.css')


def _junit_validation_error(root: ET.Element) -> str | None:
    """Accept pytest's suite layout, not arbitrary XML containing testcase tags."""
    if root.tag == 'testsuite':
        suites = [root]
    elif root.tag == 'testsuites' and all(child.tag == 'testsuite' for child in root):
        suites = list(root)
    else:
        return 'Fixture report has an unsupported root or suite structure.'
    for suite in suites:
        if any(child.tag not in {'testcase', 'properties', 'system-out', 'system-err'}
               for child in suite):
            return 'Fixture report has unsupported suite content.'
        for case in suite.findall('testcase'):
            if any(child.tag not in {'failure', 'error', 'skipped', 'properties',
                                    'system-out', 'system-err'} for child in case):
                return 'Fixture report has unsupported testcase content.'
    for node in root.iter():
        if node.tag == 'properties':
            if any(child.tag != 'property' or len(child) for child in node):
                return 'Fixture report has unsupported properties content.'
        elif node.tag in {'property', 'failure', 'error', 'skipped', 'system-out', 'system-err'}:
            if len(node):
                return 'Fixture report has unsupported nested result content.'
    # pytest writes these counts on suites. An aggregate root may also report
    # them. Never ignore contradictory totals in otherwise parseable XML.
    for node in ([root] if root.tag == 'testsuites' else []) + suites:
        cases = list(node.iter('testcase'))
        counts = {'tests': len(cases)}
        counts.update({field: sum(case.find(tag) is not None for case in cases)
                       for field, tag in (('failures', 'failure'), ('errors', 'error'),
                                          ('skipped', 'skipped'))})
        for field, observed in counts.items():
            declared = node.get(field)
            if declared is None:
                continue
            if not declared.isascii() or not declared.isdecimal():
                return 'Fixture report contains invalid counters.'
            try:
                count = int(declared)
            except ValueError:
                return 'Fixture report contains invalid counters.'
            # pytest counts successful unittest subTests in the suite total but
            # omits their testcase elements. Failed subTests still contribute
            # failure/error evidence. Keep the declared total separate below.
            if (field == 'tests' and count < observed) or (field != 'tests' and count != observed):
                return 'Fixture report counters disagree with testcase evidence.'
    if (root.tag == 'testsuites' and root.get('tests') is not None
            and all(suite.get('tests') is not None for suite in suites)
            and int(root.get('tests')) != sum(int(suite.get('tests')) for suite in suites)):
        return 'Fixture report aggregate disagrees with suite totals.'
    return None


def summarize_junit(path: Path, exit_code: int | None, *, timed_out=False) -> dict:
    """Only counts and synthetic test IDs leave the private fixture output."""
    result = {'status': 'failed', 'exit_code': exit_code, 'timed_out': timed_out,
              'tests': 0, 'failures': [], 'errors': [], 'skips': []}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        result['reason'] = 'Fixture report is missing or invalid.'
        return result
    invalid = _junit_validation_error(root)
    if invalid:
        result['reason'] = invalid
        return result
    cases = list(root.iter('testcase'))
    result['tests'] = len(cases)
    suites = [root] if root.tag == 'testsuite' else list(root)
    result['reported_tests'] = (sum(int(suite.get('tests')) for suite in suites)
                                if suites and all(suite.get('tests') is not None for suite in suites)
                                else None)
    for case in cases:
        name = case.get('classname', '') + '::' + case.get('name', '')
        for tag, field in (('failure', 'failures'), ('error', 'errors'), ('skipped', 'skips')):
            if case.find(tag) is not None:
                result[field].append(name)
    if timed_out:
        result['reason'] = 'Fixture watchdog expired; gate did not complete.'
    elif exit_code != 0:
        result['reason'] = 'Fixture command returned a nonzero exit code.'
    elif not cases:
        result['reason'] = 'No tests ran; no qualification evidence.'
    elif result['failures'] or result['errors']:
        result['reason'] = 'Fixture report contains failures or errors.'
    elif result['skips']:
        result['status'] = 'not_run'
        result['reason'] = 'Some required fixtures were skipped; gate is not fully verified.'
    else:
        result['status'] = 'verified'
    return result


def run_gate(name: str, *, timeout: float) -> dict:
    if importlib.util.find_spec('pytest') is None:
        return {'status': 'unavailable', 'exit_code': None, 'reason': 'pytest is not installed.'}
    with tempfile.TemporaryDirectory(prefix='dream-qualification-') as directory:
        report = Path(directory) / 'junit.xml'
        # Configure only the child. Catalog and extension discovery must not
        # inspect the operator's settings while qualifying synthetic fixtures.
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('DREAM_') and key not in {
                   'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                   'XAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY',
                   'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH', 'PYTHONHOME'}}
        # Playwright's Linux default follows XDG_CACHE_HOME/HOME, which the
        # fixture child isolates below. Preserve only installed executable
        # discovery, not the host cache/settings or a browser user profile.
        browser_keys = ('PLAYWRIGHT_BROWSERS_PATH', 'npm_config_playwright_browsers_path',
                        'npm_package_config_playwright_browsers_path')
        browser_binding = next((env[key] for key in browser_keys if key in env), None)
        if sys.platform == 'linux' and not browser_binding:
            browser_cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'ms-playwright'
            if browser_cache.is_dir():
                env['PLAYWRIGHT_BROWSERS_PATH'] = str(browser_cache.absolute())
        env.update(HOME=str(Path(directory) / 'home'),
                   CODEX_HOME=str(Path(directory) / 'codex'),
                   XDG_CONFIG_HOME=str(Path(directory) / 'config'),
                   XDG_CACHE_HOME=str(Path(directory) / 'cache'),
                   XDG_DATA_HOME=str(Path(directory) / 'data'),
                   XDG_RUNTIME_DIR=str(Path(directory) / 'runtime'),
                   DREAM_ROOT=str(Path(directory) / 'dream'), DREAM_SKILL_DIRS='',
                   DREAM_SEMANTIC_MEMORY='0', DREAM_RERANK='0', DREAM_CONSOLIDATE='0',
                   CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='', ROCR_VISIBLE_DEVICES='')
        for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR'):
            Path(env[key]).mkdir(mode=0o700)
        argv = [sys.executable, '-m', 'pytest', '-q', *GATES[name], '--junitxml=' + str(report)]
        try:
            completed = subprocess.run(argv, cwd=ROOT, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=timeout, check=False, env=env)
        except subprocess.TimeoutExpired:
            return summarize_junit(report, None, timed_out=True)
        except OSError:
            return {'status': 'unavailable', 'exit_code': None, 'reason': 'Fixture interpreter could not start.'}
        return summarize_junit(report, completed.returncode)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gate', action='append', choices=tuple(GATES), help='Run selected fixed gates; default all.')
    parser.add_argument('--timeout', type=int, default=180, help='Fixture watchdog seconds per gate, 1–1800.')
    parser.add_argument('--output', type=Path, help='Write JSON to this file instead of stdout.')
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 1800:
        parser.error('--timeout must be between 1 and 1800 seconds')
    selected = list(dict.fromkeys(args.gate or GATES))
    paths = set(SOURCES)
    for name in selected:
        paths.update(GATES[name])
    def snapshot():
        hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                  if (ROOT / path).is_file() else None for path in sorted(paths)}
        if 'local_fixture' in selected:
            # The fixture catalog depends on every supported packaged skill,
            # including new/removed files. Use the same no-follow bounded loader;
            # never scan operator catalogs, plugin roots or supporting files.
            from dream.local_eval_skills import load_packaged_skills
            try:
                skills = load_packaged_skills(ROOT / 'skills')
                hashes.update({'skills/' + row.source: row.sha256 for row in skills})
                if not skills:
                    hashes['skills/catalog-unavailable'] = None
            except (OSError, ValueError, UnicodeError):
                hashes['skills/catalog-unavailable'] = None
        return hashes
    hashes = snapshot()
    gates = {name: run_gate(name, timeout=args.timeout) for name in selected}
    after = snapshot()
    changed = [path for path in sorted(hashes.keys() | after.keys()) if hashes.get(path) != after.get(path)]
    consistent = not changed and all(value is not None for value in (*hashes.values(), *after.values()))
    value = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
             'scope': 'Offline production contracts with synthetic transports; not live model qualification.',
             'python': sys.version.split()[0], 'fixture_watchdog_seconds': args.timeout,
             'gates': gates, 'source_sha256': hashes,
             'source_consistency': {'status': 'verified' if consistent else 'failed',
                                    'changed_paths': changed,
                                    'scope': 'Listed files only; not a complete dependency or installed-wheel audit.'},
             'live_provider_inference': {'status': 'not_run'},
             'owner_desktop_acceptance': {'status': 'not_run'}}
    encoded = json.dumps(value, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end='')
    return 0 if consistent and all(gate['status'] == 'verified' for gate in gates.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
