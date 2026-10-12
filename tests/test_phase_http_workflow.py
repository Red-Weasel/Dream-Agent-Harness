"""Actual compatible HTTP orchestration with synthetic inference, no server."""
from claude_agent_sdk import SdkMcpTool
import pytest

from dream.core.backends.base import Event
from dream.core.task_checkpoints import current, wrap_tool
from dream.tools.context import ToolContext, bind_context
from test_cache_friendly_head import FakeEngine, backend
from test_managed_loop import worker


@pytest.mark.parametrize('phase_count', [2, 8])
async def test_http_phases_preserve_originals_and_advance_only_checked_tasks(worker, tmp_path, phase_count):
    from dream.core.phase_runner import PhaseRunner, PhaseSpec
    from dream.core.run_state import inspect_run
    from dream.core.tool_artifacts import ArtifactStore

    executed = []
    checks = []
    async def save(args):
        phase = args['phase']
        coordinator = current()
        executed.append((phase, coordinator.journal.run_id))
        (tmp_path / f'result-{phase}.txt').write_text(f'checked phase {phase}')
        (coordinator.journal.path / 'progress.md').write_text(f'Finished phase {phase}.')
        return {'content': [{'type': 'text', 'text': f'phase {phase} original\n' + 'x' * 6000}]}

    tool = wrap_tool(SdkMcpTool(name='fixture_save', description='Save a fixture result.',
        input_schema={'type': 'object', 'properties': {'phase': {'type': 'integer'}},
                      'required': ['phase']}, handler=save))
    replies = []
    for phase in range(phase_count):
        replies.extend([{'calls': [('fixture_save', {'phase': phase})]}, {'text': 'STATUS: DONE'}])
    inference = FakeEngine(lead=replies)
    http = backend(inference, tools=[tool], system=worker._assembled_system_prompt)
    http.messages = [{'role': 'system', 'content': worker._assembled_system_prompt}]
    context = ToolContext(None, None, None, session_id='phase-http-fixture', workspace=tmp_path)
    worker.backend = http
    worker._tool_context = context
    async def ask(prompt):
        with bind_context(context):
            async for event in http.ask(prompt):
                yield event
    worker.ask = ask

    class Reviewer:
        def __init__(self, tools): self.tools = tools
        async def connect(self): pass
        async def disconnect(self): pass
        async def ask(self, prompt):
            phase = executed[-1][0]
            await self.tools[0].handler({'path': str(tmp_path / f'result-{phase}.txt')})
            yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')

    def check(phase):
        checks.append(phase)
        return (tmp_path / f'result-{phase}.txt').read_text() == f'checked phase {phase}'
    specs = tuple(PhaseSpec(f'phase-{i}', f'Finish phase {i}', f'- Result {i} is correct',
        f'check-{i}', 'a' * 64, lambda i=i: check(i)) for i in range(phase_count))
    visible_events = []
    owned_scopes = set()
    def observe(event):
        visible_events.append(event.kind)
        if event.kind == 'tool_use':
            assert runner.active_loop is not None
            owned_scopes.add(runner.active_loop.workspace.name)
    runner = PhaseRunner(worker, specs, goal='Finish the original checked schedule',
        state_dir=tmp_path / 'schedules', on_event=observe,
        evaluator_backend_factory=lambda settings, tools, *args: Reviewer(tools))
    result = await runner.run()
    assert result.status == 'done' and result.completed_phases == phase_count
    assert [i for i, _ in executed] == list(range(phase_count))
    assert checks == list(range(phase_count))
    assert len(inference.requests) == phase_count * 2
    assert len({run for _, run in executed}) == phase_count
    assert 'tool_use' in visible_events and 'tool_result' in visible_events
    assert runner.active_loop is None
    schedule = runner.inspect(result.schedule_id)
    assert schedule['dispatches'] == phase_count * 2
    for phase, row in enumerate(schedule['phases']):
        state = inspect_run(tmp_path / 'schedules' / 'children', row['run_id'])['state']
        managed = state['managed']
        assert len(managed['recent']) == 1 and managed['action_revision'] == 2
        assert managed['recent'][0]['disposition'] == 'returned-success'
        assert managed['boundary']['status'] == 'acknowledged'
        assert state['phase_check']['status'] == 'passed'
        assert managed['completed_receipt'] == row['completion']['receipt_id']
        receipt = state['lead_artifacts'][0]
        original = ArtifactStore(tmp_path, receipt['session_id'], row['run_id']).read(receipt['handle'], limit=8192)
        assert original['text'] == f'phase {phase} original\n' + 'x' * 6000
        assert original['eof']
        assert inference.requests[phase * 2]['messages'][0]['content'] == worker._assembled_system_prompt
    resumed = await runner.run(resume_schedule_id=result.schedule_id)
    assert resumed.status == 'done'
    assert len(inference.requests) == phase_count * 2 and len(executed) == phase_count
    # Exercise qualification's real ledger reader against this whole workflow.
    # Synthetic successes count as checked fixture work, never endurance proof.
    import hashlib
    import json
    from dream.core.endurance_report import build_endurance_report
    run_ids = [row['run_id'] for row in schedule['phases']]
    assert owned_scopes == set(run_ids)
    selectors = {run_id: [hashlib.sha256(json.dumps(f'check-{phase}',
        sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()]
        for phase, run_id in enumerate(run_ids)}
    report = build_endurance_report(tmp_path / 'schedules' / 'children', run_ids,
        task_lookup=worker.tasks.get, acceptance_checks=selectors)
    assert report['accepted_work_count'] == phase_count
    assert all(run['host_checks'] == {'passed': 1, 'failed': 0} for run in report['runs'])
    assert all(not gate['qualified'] for gate in report['gates'])
