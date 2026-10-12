"""Actual registry/loop boundaries with synthetic handlers, providers and files."""
import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from claude_agent_sdk import SdkMcpTool

from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.recovery import RecoveryPolicy
from dream.core.run_state import RunState
from dream.core.task_checkpoints import ManagedTaskCoordinator, bind, current, wrap_tool
from dream.tools import registry, web
from test_managed_loop import worker, Reviewer
from test_task_checkpoints import managed, evidence


def quick_policy(**kwargs):
    return RecoveryPolicy(repeat_call_limit=2, **kwargs)


def registered(handler):
    return registry.build(extra_tools=[SdkMcpTool(name='fixture_action', description='',
                         input_schema={}, handler=handler)])['tools'][0]


async def test_loop_consumes_two_interventions_then_reserves_help_before_done(worker, tmp_path):
    calls, prompts, reviews = [], [], []
    async def handler(args):
        calls.append(args)
        return {'content': [{'type': 'text', 'text': 'unchanged result'}]}
    tool = registered(handler)
    async def ask(prompt):
        prompts.append(prompt)
        for _ in range(8):
            await tool.handler({'action': 'same'})
            if current().pending_recovery:
                break
        yield Event('assistant_done', 'STATUS: DONE')
    worker.ask = ask
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs', recovery_policy=quick_policy(),
        evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
    result = await driver.run('full cedar research task', acceptance_criteria='- original criterion')
    assert result.status == 'need_input'
    assert len(calls) == 4 and len(prompts) == 3 and reviews == []
    assert all('full cedar research task' in p and 'original criterion' in p for p in prompts)
    assert all('original full standing head' in p and 'ledger.jsonl' in p for p in prompts[1:])
    saved = driver.checkpoints._state['recovery']
    assert saved['total_interventions'] == 2
    assert saved['controller']['steps'] == 4 and saved['controller']['help_used'] == 1
    assert saved['pending']['help_request']['requires_review']
    assert driver.checkpoints.task['status'] != 'done'
    resumed = await driver.run('full cedar research task', resume_run_id=result.run_id,
                               resume_note='Inspect without replaying')
    assert resumed.status == 'need_input' and len(prompts) == 3
    assert driver.checkpoints._state['recovery']['controller']['help_used'] == 1


async def test_pending_blocks_actual_next_handler_and_completion(managed):
    c, key = managed
    calls = []
    async def handler(args):
        calls.append(args)
        return {}
    tool = wrap_tool(SdkMcpTool(name='fixture_action', description='', input_schema={}, handler=handler))
    with bind(c):
        for _ in range(3):
            await tool.handler({})
        assert c.pending_recovery['kind'] == 'recover'
        with pytest.raises(PermissionError):
            await tool.handler({'different': True})
    assert len(calls) == 3
    with pytest.raises(ValueError, match='recovery'):
        c.record_verification(authority=key, verdict='PASS', inspected=evidence(c), assessment={})


def test_only_actual_host_named_checks_count(managed):
    c, key = managed
    invoked = []
    def failed():
        invoked.append(1)
        return False
    with pytest.raises(PermissionError):
        c.execute_check('fixture-check', failed, authority=object())
    assert invoked == []
    # Default host key is the verifier capability only when supplied explicitly.
    c = ManagedTaskCoordinator(c.journal, c.tasks, verification_authority=key, recovery_check_authority=key)
    c.attach()
    c.execute_check('fixture-check', failed, authority=key)
    c.execute_check('fixture-check', failed, authority=key)
    assert invoked == [1, 1]
    assert c.pending_recovery['reason'] == 'repeated_check'
    assert c._state['recovery']['controller']['checks']


async def test_real_readonly_novel_data_counts_but_query_does_not(managed, monkeypatch):
    c, _ = managed
    # Goal relevance is a task-scoped keyword check, not an acceptance verdict.
    c.journal.record('fixture_goal', goal='research cedar transport')
    source = {'title': 'Cedar transport', 'url': 'https://example.invalid/cedar',
              'engine': 'fixture', 'content': 'Cedar uses a bounded transport.'}
    async def search(query, **kwargs):
        return {'query': query, 'answers': [], 'results': [source], 'suggestions': [query]}
    monkeypatch.setattr(web.searxng, 'search', search)
    tool = wrap_tool(web.web_search)
    with bind(c):
        await tool.handler({'query': 'first cedar question'})
        first = c._state['recovery']['controller']
        await tool.handler({'query': 'changed cedar query'})
        second = c._state['recovery']['controller']
    assert len(first['evidence']) == len(second['evidence']) == 1
    assert first['no_progress'] == 0 and second['no_progress'] == 1
    assert c.task['status'] != 'done'


async def test_real_browse_nonce_and_screenshot_are_not_novel_evidence(managed, monkeypatch):
    c, _ = managed
    c.journal.record('fixture_goal', goal='cedar research')
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        return {'title': 'Cedar', 'final_url': url, 'status': 200,
                'text': 'Cedar transport uses a bounded protocol.',
                'screenshot': f'/synthetic/screenshot-{len(calls)}.png'}
    context = SimpleNamespace(store=SimpleNamespace(recent_user_turns=lambda _: []),
        session_id='fixture', browser=SimpleNamespace(fetch=fetch), multimodal=False)
    monkeypatch.setattr(web, 'ctx', lambda: context)
    tool = wrap_tool(web.browse)
    with bind(c):
        first = await tool.handler({'url': 'https://example.invalid/cedar'})
        second = await tool.handler({'url': 'https://example.invalid/cedar'})
    assert first != second  # Delivery nonce and screenshot changed, source data did not.
    saved = c._state['recovery']['controller']
    assert len(saved['evidence']) == 1 and saved['no_progress'] == 1


def test_research_parser_rejects_oversized_delivery_and_failed_result():
    from dream.core.recovery_runtime import research_digest
    record = {'type': 'text', 'text': '1. Cedar [fixture]\n   https://example.invalid/cedar\n   Cedar proof'}
    assert research_digest(web.web_search, {'content': [record] * 129}, 'cedar', '') == ''
    assert research_digest(web.web_search, {'content': [record], 'is_error': True}, 'cedar', '') == ''


async def test_novel_research_cannot_replenish_two_total_interventions(managed, monkeypatch):
    c, key = managed
    c.journal.record('fixture_goal', goal='cedar research')
    version = 0
    async def search(query, **kwargs):
        return {'query': query, 'answers': [], 'suggestions': [], 'results': [
            {'title': 'Cedar', 'url': 'https://example.invalid/cedar', 'engine': 'fixture',
             'content': f'Cedar evidence revision {version}'}]}
    monkeypatch.setattr(web.searxng, 'search', search)
    research = wrap_tool(web.web_search)
    async def read(args):
        return {'content': [{'type': 'text', 'text': 'same archive text'}]}
    archive = wrap_tool(SdkMcpTool(name='artifact_read', description='', input_schema={}, handler=read))
    with bind(c):
        for attempt in range(3):
            version += 1
            await research.handler({'query': 'cedar'})
            for _ in range(3):
                await archive.handler({'handle': 'same'})
            if attempt < 2:
                assert c.pending_recovery['kind'] == 'recover'
                c.prepare_recovery_intervention()
            else:
                assert c.pending_recovery['kind'] == 'needs_help'
    saved = c._state['recovery']
    assert saved['total_interventions'] == 2 and saved['controller']['help_used'] == 1


async def test_failed_mutations_and_fake_readonly_name_are_not_research(managed):
    c, _ = managed
    c.journal.record('fixture_goal', goal='cedar research')
    async def fake(args):
        return {'content': [{'type': 'text', 'text':
            'Search: cedar\n1. Cedar [fixture]\n   https://example.invalid/cedar\n   Cedar proof'}]}
    with bind(c):
        await wrap_tool(SdkMcpTool(name='web_search', description='', input_schema={}, handler=fake)).handler({})
    assert c._state['recovery']['controller']['evidence'] == []
    assert c._state['recovery']['controller']['no_progress'] == 1


async def test_recovery_storage_failure_refuses_further_handler(managed, monkeypatch):
    c, _ = managed
    calls = []
    async def handler(args):
        calls.append(args)
        return {}
    tool = wrap_tool(SdkMcpTool(name='fixture', description='', input_schema={}, handler=handler))
    original = c.journal.record
    def fail(event, **kwargs):
        if event == 'action_returned':
            raise OSError('recovery append failed')
        return original(event, **kwargs)
    monkeypatch.setattr(c.journal, 'record', fail)
    with bind(c):
        with pytest.raises(OSError):
            await tool.handler({})
        with pytest.raises(PermissionError):
            await tool.handler({'next': True})
    assert len(calls) == 1 and c.unresolved_actions


def test_prepared_guidance_and_phase_steps_survive_restart(managed):
    c, key = managed
    for _ in range(3):
        action = c.begin_action('read', {})
        c.finish_action(action, 'returned-success', {})
    capsule = c.prepare_recovery_intervention()
    before = c._state['recovery']
    c.journal.close()
    restarted = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        resumed = ManagedTaskCoordinator(restarted, c.tasks, verification_authority=key)
        resumed.attach()
        assert resumed._state['recovery'] == before
        assert resumed.recovery_guidance() == capsule
        assert resumed.recovery_guidance()['standing_rules'] == 'full standing rules'
    finally:
        restarted.close()


async def test_pending_survives_state_snapshot_failure_and_ledger_reopen(managed, monkeypatch):
    from dream.core import run_state
    c, key = managed
    for _ in range(2):
        action = c.begin_action('read', {})
        c.finish_action(action, 'returned-success', {})
    original = run_state.atomic_write
    def fail(path, text):
        if path.name == 'state.json' and json.loads(text).get('managed', {}).get('recovery', {}).get('pending'):
            raise OSError('snapshot failed after recovery ledger commit')
        return original(path, text)
    with monkeypatch.context() as patch:
        patch.setattr(run_state, 'atomic_write', fail)
        action = c.begin_action('read', {})
        with pytest.raises(OSError):
            c.finish_action(action, 'returned-success', {})
    c.journal.close()
    journal = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        restarted = ManagedTaskCoordinator(journal, c.tasks, verification_authority=key)
        restarted.attach()
        assert restarted.pending_recovery['kind'] == 'recover'
        assert restarted._state['recovery']['controller']['steps'] == 3
        with pytest.raises(PermissionError):
            restarted.begin_action('read', {})
    finally:
        journal.close()


def test_stop_stays_terminal_and_does_not_issue_help(managed):
    c, _ = managed
    c.stop()
    assert c.pending_recovery['kind'] == 'stopped'
    assert c._state['recovery']['controller']['help_used'] == 0
    with pytest.raises(PermissionError):
        c.begin_action('read', {})
    with pytest.raises(PermissionError):
        c.prepare_recovery_intervention()


async def test_actual_failed_handlers_park_recovery_without_automatic_replay(worker, tmp_path):
    calls, prompts = [], []
    async def fail(args):
        calls.append(args)
        return {'is_error': True, 'content': [{'type': 'text', 'text': 'same failure after possible effect'}]}
    tool = registered(fail)
    async def ask(prompt):
        prompts.append(prompt)
        for i in range(3):
            await tool.handler({'different_attempt': i})
            if i < 2:
                action_id = next(iter(current().unresolved_actions))
                current().reconcile_action(action_id, note='Host inspected this specific fixture effect before retry.')
        yield Event('assistant_done', 'STATUS: DONE')
    worker.ask = ask
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs',
                            evaluator_backend_factory=lambda *a: pytest.fail('must not review uncertain effects'))
    result = await driver.run('cedar task', acceptance_criteria='- criterion')
    assert result.status == 'need_input' and 'reconciliation' in result.message
    assert len(calls) == 3 and len(prompts) == 1
    assert driver.checkpoints.pending_recovery['reason'] == 'repeated_error'
    assert driver.checkpoints._state['recovery']['controller']['evidence'] == []
    assert len(driver.checkpoints.unresolved_actions) == 1


async def test_missing_help_route_still_reserves_envelope_once(worker, tmp_path):
    async def handler(args):
        return {}
    tool = registered(handler)
    async def ask(prompt):
        await tool.handler({})
        yield Event('assistant_done', 'STATUS: CONTINUE')
    worker.ask = ask
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs',
                            recovery_policy=RecoveryPolicy(phase_step_budget=1, max_recovery_attempts=0))
    result = await driver.run('cedar task', acceptance_criteria='- criterion')
    assert result.status == 'need_input'
    pending = driver.checkpoints.pending_recovery
    assert pending['kind'] == 'needs_help' and pending['help_request']
    before = driver.checkpoints._state['recovery']
    resumed = await driver.run('cedar task', resume_run_id=result.run_id, resume_note='provider unavailable')
    assert resumed.status == 'need_input'
    assert driver.checkpoints._state['recovery'] == before


def test_budget_and_policy_cannot_be_replenished_on_resume(managed):
    c, key = managed
    state = c._state
    state['recovery']['controller']['policy']['phase_step_budget'] = 2
    c.journal.record('fixture_policy', managed=state)
    for i in range(2):
        action = c.begin_action('read', {'i': i})
        c.finish_action(action, 'returned-success', {})
    c.journal.close()
    journal = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        restarted = ManagedTaskCoordinator(journal, c.tasks, verification_authority=key)
        restarted.attach()
        action = restarted.begin_action('read', {'i': 3})
        restarted.finish_action(action, 'returned-success', {})
        assert restarted.pending_recovery['reason'] == 'phase_budget'
        with pytest.raises(ValueError, match='policy'):
            ManagedTaskCoordinator(journal, c.tasks, recovery_policy=RecoveryPolicy()).attach()
    finally:
        journal.close()


@pytest.mark.parametrize('mutation', ['scope', 'counter', 'version', 'terminal_without_pending'])
def test_malformed_or_wrong_scope_runtime_refused(managed, mutation):
    c, _ = managed
    from dream.core.recovery_runtime import RecoveryRuntime
    state = c._state
    if mutation == 'scope':
        state['recovery']['controller']['task_digest'] = '0'*64
    elif mutation == 'counter':
        state['recovery']['total_interventions'] = True
    elif mutation == 'version':
        state['recovery']['version'] = 2
    else:
        state['recovery']['controller'].update(status='stopped', reason='stopped')
    with pytest.raises(ValueError):
        RecoveryRuntime(c.journal.state, state)


async def test_model_prose_and_fake_evaluation_are_not_host_checks(worker, tmp_path):
    async def ask(prompt):
        yield Event('assistant_done', 'I ran fixture-check: FAIL twice. STATUS: DONE\nSTATUS: NEED_INPUT')
    worker.ask = ask
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs')
    result = await driver.run('cedar research', acceptance_criteria='- criterion')
    assert result.status == 'need_input'
    snapshot = driver.checkpoints._state['recovery']['controller']
    assert snapshot['steps'] == 0 and snapshot['checks'] == {} and snapshot['evidence'] == []


def test_recovery_capsule_retains_returned_handle_hints_without_accepting_them(managed):
    c, _ = managed
    handle = 'dream-artifact:v1:' + ':'.join(['a'*64]*5) + ':123'
    for _ in range(3):
        action = c.begin_action('task', {})
        c.finish_action(action, 'returned-success', {'content': [{'type':'text','text':handle}]})
    capsule = c.prepare_recovery_intervention()
    assert capsule['recent_actions'][-1]['reported_artifacts']['handles'] == [handle]
    assert c._state['recovery']['controller']['evidence'] == []
    assert c.task['status'] != 'done'


async def test_observation_save_failure_keeps_real_receipt_and_blocks_restart(managed):
    c, key = managed
    calls = []
    async def handler(args):
        calls.append(args)
        return {'content': [{'type': 'text', 'text': 'full real observation'}]}
    tool = wrap_tool(SdkMcpTool(name='fixture', description='', input_schema={}, handler=handler))
    with bind(c):
        await tool.handler({})
        receipt = c._state['recent'][-1]
        before = c._state['recovery']
        c.record_observation_failure()
        with pytest.raises(PermissionError):
            await tool.handler({'next': True})
    saved = c._state
    assert saved['recent'][-1] == receipt and receipt['disposition'] == 'returned-success'
    assert saved['observation_failure'] == 'artifact_persistence'
    assert saved['recovery']['pending']['kind'] == 'blocked'
    assert saved['recovery']['total_interventions'] == 0
    for name in ('steps', 'last_sequence', 'calls', 'errors', 'evidence', 'help_used'):
        assert saved['recovery']['controller'][name] == before['controller'][name]
    c.journal.close()
    journal = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        restarted = ManagedTaskCoordinator(journal, c.tasks, verification_authority=key)
        restarted.attach()
        assert restarted.pending_recovery['kind'] == 'blocked'
        with pytest.raises(PermissionError):
            restarted.begin_action('fixture', {})
        with pytest.raises(ValueError, match='recovery'):
            restarted.record_verification(authority=key, verdict='PASS', inspected=evidence(restarted), assessment={})
    finally:
        journal.close()
    assert len(calls) == 1


def test_observation_failure_journal_failure_still_seals_admission(managed, monkeypatch):
    c, _ = managed
    action = c.begin_action('fixture', {})
    c.finish_action(action, 'returned-success', {})
    original = c.journal.record
    def fail(event, **kwargs):
        if event == 'recovery_observation_failed':
            raise OSError('ledger unavailable')
        return original(event, **kwargs)
    monkeypatch.setattr(c.journal, 'record', fail)
    with pytest.raises(OSError):
        c.record_observation_failure()
    with pytest.raises(PermissionError):
        c.begin_action('fixture', {})
    with pytest.raises(ValueError):
        c.open_actions()


async def test_stop_with_failed_recovery_write_keeps_cancellation_and_closes_admission(worker, tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def handler(args):
        calls.append(args)
        entered.set()
        await release.wait()
        return {}
    tool = registered(handler)
    async def ask(prompt):
        await tool.handler({})
        yield Event('assistant_done', 'STATUS: DONE')
    async def interrupt():
        release.set()
    worker.ask, worker.interrupt = ask, interrupt
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs')
    running = asyncio.create_task(driver.run('cedar task', acceptance_criteria='- criterion'))
    await entered.wait()
    original = driver._journal.record
    def fail(event, **kwargs):
        if event == 'recovery_stopped':
            raise OSError('Stop recovery storage failure')
        return original(event, **kwargs)
    monkeypatch.setattr(driver._journal, 'record', fail)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert len(calls) == 1 and driver.checkpoints.task['status'] != 'done'
    with pytest.raises(PermissionError):
        driver.checkpoints.begin_action('another', {})


async def test_actual_scoped_source_reads_and_changed_writes_establish_progress(managed, monkeypatch, tmp_path):
    from dream.tools import native
    c, _ = managed
    monkeypatch.setattr(native, 'ctx', lambda: SimpleNamespace(workspace=tmp_path))
    read = wrap_tool(native.read_file)
    write = wrap_tool(native.write_file)
    with bind(c):
        for i in range(14):
            path = tmp_path / f'source_{i}.py'
            path.write_text(f'def value_{i}():\n    return {i}\n')
            await read.handler({'path': str(path), 'start_line': 1, 'line_count': 30})
            assert c.pending_recovery is None
        for i in range(4):
            await write.handler({'path': 'changed.py', 'content': f'VALUE = {i}\n'})
            assert c.pending_recovery is None
    assert len(c._state['recovery']['controller']['evidence']) == 18


async def test_explicit_owner_resume_after_stop_preserves_budgets(worker, tmp_path):
    calls = []
    async def ask(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            action = current().begin_action('fixture', {})
            current().finish_action(action, 'returned-success', {})
            current().stop()
        yield Event('assistant_done', 'STATUS: NEED_INPUT')
    worker.ask = ask
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs')
    first = await driver.run('cedar task', acceptance_criteria='- criterion')
    assert first.status == 'stopped'
    before = driver.checkpoints._state['recovery']
    implicit = await driver.run('cedar task', resume_run_id=first.run_id)
    assert implicit.status == 'stopped' and len(calls) == 1
    resumed = await driver.run('cedar task', resume_run_id=first.run_id,
                               resume_note='Owner inspected all effects and requests continuation.')
    assert resumed.status == 'need_input' and len(calls) == 2
    after = driver.checkpoints._state['recovery']
    for key in ('steps', 'calls', 'evidence', 'no_progress', 'help_used', 'attempts'):
        assert after['controller'][key] == before['controller'][key]
    assert after['total_interventions'] == before['total_interventions']


async def test_settled_uncertain_effect_blocks_next_handler_until_id_report(managed):
    c, _ = managed
    calls = []
    async def fail(args):
        calls.append(args)
        return {'is_error': True, 'content': [{'type': 'text', 'text': 'effect may have occurred'}]}
    tool = wrap_tool(SdkMcpTool(name='fixture', description='', input_schema={}, handler=fail))
    with bind(c):
        await tool.handler({'attempt': 1})
        with pytest.raises(PermissionError, match='reconcil'):
            await tool.handler({'attempt': 2})
        action_id = next(iter(c.unresolved_actions))
        c.reconcile_action(action_id, note='Owner inspected the specific effect; another attempt is authorized.')
        await tool.handler({'attempt': 2})
    assert len(calls) == 2


async def test_source_mtime_copied_content_and_journal_selfread_do_not_refresh(managed, monkeypatch, tmp_path):
    import os
    from dream.tools import native
    c, _ = managed
    monkeypatch.setattr(native, 'ctx', lambda: SimpleNamespace(workspace=tmp_path))
    read, write = wrap_tool(native.read_file), wrap_tool(native.write_file)
    source = tmp_path / 'source.py'
    source.write_text('VALUE = 1\n')
    copy = tmp_path / 'copy.py'
    copy.write_text(source.read_text())
    with bind(c):
        await read.handler({'path': str(source)})
        os.utime(source, (1, 1))
        await read.handler({'path': str(source), 'start_line': 1})
        await read.handler({'path': str(copy)})
        await write.handler({'path': str(copy), 'content': source.read_text()})
        await read.handler({'path': str(c.journal.path / 'progress.md')})
        assert len(c._state['recovery']['controller']['evidence']) == 1
        assert c._state['recovery']['controller']['no_progress'] == 4
        await write.handler({'path': str(source), 'content': 'VALUE = 2\n'})
        assert len(c._state['recovery']['controller']['evidence']) == 2
        await read.handler({'path': str(source)})
        assert len(c._state['recovery']['controller']['evidence']) == 2


async def test_changed_actual_edit_counts_but_failed_edit_never_does(managed, monkeypatch, tmp_path):
    from dream.tools import files
    c, _ = managed
    monkeypatch.setattr(files, 'ctx', lambda: SimpleNamespace(workspace=tmp_path))
    source = tmp_path / 'source.py'
    source.write_text('VALUE = 1\n')
    edit = wrap_tool(files.str_replace_edit)
    with bind(c):
        await edit.handler({'path': str(source), 'old_string': '1', 'new_string': '2'})
        assert source.read_text() == 'VALUE = 2\n'
        assert len(c._state['recovery']['controller']['evidence']) == 1
        failed = await edit.handler({'path': str(source), 'old_string': 'absent', 'new_string': '3'})
        assert failed['is_error'] and source.read_text() == 'VALUE = 2\n'
        assert len(c._state['recovery']['controller']['evidence']) == 1
        with pytest.raises(PermissionError):
            await edit.handler({'path': str(source), 'old_string': '2', 'new_string': '3'})


async def test_stop_resume_requires_specific_unresolved_action_report(worker, tmp_path):
    calls, ids = [], []
    async def ask(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            action = current().begin_action('fixture', {})
            ids.append(action)
            current().finish_action(action, 'error', {'error': 'uncertain fixture'})
            current().stop()
        yield Event('assistant_done', 'STATUS: NEED_INPUT')
    worker.ask = ask
    driver = AutonomousLoop(worker, state_dir=tmp_path/'runs')
    first = await driver.run('cedar task', acceptance_criteria='- criterion')
    refused = await driver.run('cedar task', resume_run_id=first.run_id,
        resume_note='Inspecting alone does not reconcile the specific action.')
    assert refused.status == 'need_input' and len(calls) == 1
    resumed = await driver.run('cedar task', resume_run_id=first.run_id,
        resume_note='Owner inspected and authorizes continuation.',
        reconciled_actions={ids[0]: 'This specific effect is inspected; do not replay.'})
    assert resumed.status == 'need_input' and len(calls) == 2
    assert driver.checkpoints._state['recovery']['controller']['errors']


def test_stop_resume_cannot_restore_exhausted_interventions_or_help(managed):
    c, _ = managed
    for _ in range(5):
        action = c.begin_action('fixture', {})
        c.finish_action(action, 'returned-success', {})
        if c.pending_recovery and c.pending_recovery['kind'] == 'recover':
            c.prepare_recovery_intervention()
    assert c.pending_recovery['kind'] == 'needs_help'
    before = c._state['recovery']
    c.stop()
    c.resume_stopped(note='Owner explicitly asks to resume, without increasing budgets.')
    assert c._state['recovery'] == before
    with pytest.raises(ValueError):
        c.open_actions()


def test_already_admitted_intents_can_settle_after_uncertain_result(managed):
    c, _ = managed
    first, second = c.begin_action('first', {}), c.begin_action('second', {})
    c.finish_action(first, 'error', {})
    with pytest.raises(PermissionError):
        c.begin_action('third', {})
    c.finish_action(second, 'returned-success', {})
    assert second not in c.unresolved_actions
    c.reconcile_action(first, note='Owner inspected first action only.')
    assert c.begin_action('third', {})


@pytest.mark.parametrize('resume_mode',['checked','unchecked_history','blocked','needs_help'])
async def test_stop_after_real_archive_before_ack_resumes_without_worker_replay(worker, tmp_path, monkeypatch, resume_mode):
    from copy import deepcopy
    from test_local_subagents import _backend
    from dream.core.tool_artifacts import ArtifactStore
    from dream.tools.artifact_tools import artifact_read
    from dream.tools.context import bind_context
    backend = _backend([], None)
    original = [{'role':'system','content':'original full standing head'},
                {'role':'user','content':'original source history'}]
    backend.messages = deepcopy(original)
    worker.backend = backend
    worker._tool_context = SimpleNamespace(workspace=tmp_path,session_id='archive-origin')
    driver = AutonomousLoop(worker,state_dir=tmp_path/'runs',evaluator_backend_factory=lambda *a:Reviewer())
    def stop_before_ack(coordinator, *args, **kwargs):
        coordinator.stop()
        raise asyncio.CancelledError
    with monkeypatch.context() as patch:
        patch.setattr(ManagedTaskCoordinator,'acknowledge_boundary',stop_before_ack)
        with pytest.raises(asyncio.CancelledError):
            await driver.run('cedar task',acceptance_criteria='- criterion')
    run_id = driver._journal.run_id
    archive = deepcopy(driver._journal.state['context_archive'])
    receipt = deepcopy(driver.checkpoints._state['receipt'])
    before = deepcopy(driver.checkpoints._state['recovery'])
    calls = worker.calls
    restarted = _backend([artifact_read],None)
    restarted.messages = [{'role':'system','content':'fresh session standing head'}]
    if resume_mode == 'unchecked_history':
        restarted.messages.append({'role':'user','content':'new unchecked work must survive'})
    worker.backend = restarted
    worker._tool_context = SimpleNamespace(workspace=tmp_path,session_id='archive-resume')
    if resume_mode in {'blocked','needs_help'}:
        with_journal = RunState(tmp_path/'runs',run_id)
        host_key = object()
        coordinator = ManagedTaskCoordinator(with_journal,worker.tasks,recovery_check_authority=host_key)
        try:
            coordinator.attach()
            coordinator.resume_stopped(note='Owner inspected Stop; retain subsequent terminal decision.')
            if resume_mode == 'blocked':
                coordinator.record_observation_failure()
            else:
                for _ in range(4):
                    coordinator.execute_check('fixture-after-archive',lambda:False,authority=host_key)
                    if coordinator.pending_recovery and coordinator.pending_recovery['kind']=='recover':
                        coordinator.prepare_recovery_intervention()
            terminal = coordinator._state['recovery']
            assert terminal['pending']['kind'] == resume_mode
        finally:
            with_journal.close()
        refused = await driver.run('cedar task',resume_run_id=run_id,resume_note='No budget or scope increase.')
        assert refused.status == 'need_input' and worker.calls == calls
        assert terminal['pending']['reason'] in refused.message
        assert driver.checkpoints._state['recovery'] == terminal
        assert getattr(restarted,'_checked_archive_access',None) is None
        assert driver.checkpoints._state['boundary']['status'] == 'prepared'
        return
    implicit = await driver.run('cedar task',resume_run_id=run_id)
    assert implicit.status == 'stopped' and worker.calls == calls
    history_before_resume = deepcopy(restarted.messages)
    explicit = await driver.run('cedar task',resume_run_id=run_id,
        resume_note='Owner inspected the archived completed phase and requests its pending reset.')
    if resume_mode == 'unchecked_history':
        assert explicit.status == 'error' and 'unchecked' in explicit.message
        assert restarted.messages == history_before_resume and worker.calls == calls
        assert driver.checkpoints._state['boundary']['status'] == 'prepared'
        return
    assert explicit.status == 'done' and worker.calls == calls
    assert driver.checkpoints._state['boundary']['status'] == 'acknowledged'
    assert driver.checkpoints._state['receipt'] == receipt
    assert driver._journal.state['context_archive'] == archive
    after = driver.checkpoints._state['recovery']
    assert after['total_interventions'] == before['total_interventions']
    assert after['controller']['steps'] == before['controller']['steps']
    assert after['controller']['help_used'] == before['controller']['help_used']
    assert restarted.messages[0]['content'] == 'fresh session standing head'
    saved = ArtifactStore(tmp_path,'archive-origin',run_id).read(archive['handle'],limit=32768)
    assert json.loads(saved['text']) == original
    with bind_context(worker._tool_context):
        text, failed = await restarted._exec_tool('artifact_read',{'handle':archive['handle'],'limit':1000})
    assert not failed and 'original source history' in text
    # Stop may also be recorded after the terminal done outcome. That saved
    # result must not bypass explicit recovery admission during restoration.
    completed_journal = RunState(tmp_path/'runs',run_id)
    try:
        completed_coordinator = ManagedTaskCoordinator(completed_journal,worker.tasks)
        completed_coordinator.attach()
        completed_coordinator.stop()
    finally:
        completed_journal.close()
    history = deepcopy(restarted.messages)
    resumed_done = await driver.run('cedar task',resume_run_id=run_id,
        resume_note='Owner explicitly resumes the already completed stopped boundary.')
    assert resumed_done.status == 'done' and worker.calls == calls
    assert restarted.messages == history


def test_recovery_guidance_carries_bounded_host_artifact_hints_on_restart(managed, tmp_path):
    from dream.core.tool_artifacts import ArtifactStore
    c, key = managed
    receipts = []
    for i in range(10):
        stored = ArtifactStore(tmp_path,'fixture-session',c.journal.run_id).save(f'original source {i}',f'call-{i}')
        receipts.append({**stored.metadata(),'session_id':'fixture-session','run_id':c.journal.run_id,
                         'workspace':str(tmp_path),'incidental_body':'MUST_NOT_APPEAR'})
    c.journal.record('lead_artifact_saved',lead_artifacts=receipts)
    for _ in range(3):
        action=c.begin_action('fixture',{});c.finish_action(action,'returned-success',{})
    capsule=c.prepare_recovery_intervention()
    hints=capsule['lead_artifacts']
    assert hints['total']==10 and hints['omitted']==2 and len(hints['hints'])==8
    assert hints['hints'][0]['handle']==receipts[2]['handle']
    assert 'MUST_NOT_APPEAR' not in json.dumps(capsule)
    assert len(json.dumps(hints,ensure_ascii=False,separators=(',',':')).encode())<=16384
    c.journal.close()
    restarted=RunState(c.journal.path.parent,c.journal.run_id)
    try:
        resumed=ManagedTaskCoordinator(restarted,c.tasks,verification_authority=key)
        resumed.attach()
        assert resumed.recovery_guidance()['lead_artifacts']==hints
    finally:restarted.close()


@pytest.mark.parametrize('mutation',['foreign_run','foreign_workspace','foreign_handle','unbounded_identity','unbounded_list','missing_metadata'])
def test_recovery_artifact_hints_refuse_invalid_even_omitted_receipts(managed,tmp_path,mutation):
    from dream.core.tool_artifacts import ArtifactStore
    c,_=managed
    stored=ArtifactStore(tmp_path,'fixture-session',c.journal.run_id).save('original','fixture-call')
    receipt={**stored.metadata(),'session_id':'fixture-session','run_id':c.journal.run_id,'workspace':str(tmp_path)}
    bad=dict(receipt)
    if mutation=='foreign_run':bad['run_id']='other'
    elif mutation=='foreign_workspace':bad['workspace']=str(tmp_path.parent)
    elif mutation=='foreign_handle':bad['handle']=bad['handle'].replace(bad['run_key'],'f'*64)
    elif mutation=='unbounded_identity':bad['session_id']='x'*4097
    elif mutation=='missing_metadata':bad.pop('sha256')
    receipts=[bad]+[receipt]*9
    if mutation=='unbounded_list':receipts=[receipt]*257
    c.journal.record('fixture_lead_receipts',lead_artifacts=receipts)
    with pytest.raises(ValueError):c._recovery_capsule(c._state,{'kind':'recover'})


def test_recovery_artifact_hint_utf8_cap_reports_omission(managed,tmp_path):
    from dream.core.tool_artifacts import ArtifactStore
    c,_=managed
    session='界'*1024
    stored=ArtifactStore(tmp_path,session,c.journal.run_id).save('original','fixture-call')
    receipt={**stored.metadata(),'session_id':session,'run_id':c.journal.run_id,'workspace':str(tmp_path)}
    c.journal.record('fixture_lead_receipts',lead_artifacts=[receipt]*8)
    hints=c._recovery_capsule(c._state,{'kind':'recover'})['lead_artifacts']
    assert 0<len(hints['hints'])<8
    assert hints['total']==8 and hints['omitted']==8-len(hints['hints'])
    assert len(json.dumps(hints,ensure_ascii=False,separators=(',',':')).encode())<=16384
