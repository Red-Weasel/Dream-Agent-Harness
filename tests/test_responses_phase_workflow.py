"""Actual phase/HTTP lifecycle; all Responses requests use MockTransport."""
import asyncio
from contextlib import aclosing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from claude_agent_sdk import SdkMcpTool

from dream.core.backends.base import Event
from dream.core.frontier_help import FrontierHelp, HelpRoute, SanitizedPacket
from dream.core.phase_runner import PhaseAssistance, PhaseLimits, PhaseRunner, PhaseSpec
from dream.core.responses_help import ResponsesHelpTransport
from dream.core.run_state import RunState, inspect_run
from dream.core.task_checkpoints import current, wrap_tool
from dream.tools.context import ToolContext, bind_context
from test_assisted_phase_workflow import dispatch_kinds, ledger_events
from test_cache_friendly_head import FakeEngine, backend
from test_managed_loop import worker
from test_recovery_runtime import quick_policy
from test_responses_help import Bytes, KEY, response_body


def workflow(worker, tmp_path, *, phases=1, mode='pass', cap=12):
    seen = SimpleNamespace(repeated=[], changed=[], reviews=[], checks=[], packets=[], calls=[],
                           streams=[], advised=set(), events=[], now=datetime.now(timezone.utc))
    holder = {}
    def active(): return holder['runner'].active_loop.checkpoints
    def artifact(index): return tmp_path / ('phase-result-' + str(index) + '.txt')
    async def repeat(arguments):
        c = current(); assert c is not None and c.unresolved_actions
        seen.repeated.append(c.journal.run_id)
        return {'content': [{'type': 'text', 'text': 'Same generic bounded observation.'}]}
    async def changed(arguments):
        c = current(); assert c is not None and c.unresolved_actions
        index = c.journal.state['phase_binding']['phase_index']
        seen.changed.append(c.journal.run_id)
        artifact(index).write_text('accepted artifact ' + str(index))
        (c.journal.path / 'progress.md').write_text('The changed approach saved its actual artifact.')
        return {'content': [{'type': 'text', 'text': 'Saved the acceptance artifact.'}]}
    tools = [wrap_tool(SdkMcpTool(name=name, description='Synthetic fixture action.',
        input_schema={'type': 'object', 'properties': {'part': {'type': 'string'}}, 'required': ['part']},
        handler=handler)) for name, handler in [('fixture_repeat', repeat), ('fixture_changed', changed)]]
    def local_reply(payload):
        run_id = active().journal.run_id
        if run_id not in seen.advised: return {'calls': [('fixture_repeat', {'part': 'same'})]}
        if run_id not in seen.changed: return {'calls': [('fixture_changed', {'part': 'changed'})]}
        return {'text': 'STATUS: DONE'}
    inference = FakeEngine(lead=[local_reply] * 80)
    http = backend(inference, tools=tools, system=worker._assembled_system_prompt)
    http.messages = [{'role': 'system', 'content': worker._assembled_system_prompt}]
    context = ToolContext(None, None, None, session_id='responses-workflow-fixture', workspace=tmp_path)
    worker.backend = http; worker._tool_context = context
    async def ask(prompt):
        with bind_context(context):
            async with aclosing(http.ask(prompt)) as events:
                async for event in events: yield event
    worker.ask = ask
    class Reviewer:
        def __init__(self, tools): self.tools = tools
        async def connect(self): pass
        async def disconnect(self): pass
        async def ask(self, prompt):
            c = active(); index = c.journal.state['phase_binding']['phase_index']
            seen.reviews.append(c.journal.run_id)
            observed = await self.tools[0].handler({'path': str(artifact(index))})
            assert 'accepted artifact ' + str(index) in str(observed)
            yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')
    def check(index):
        seen.checks.append(index)
        return artifact(index).read_text() == 'accepted artifact ' + str(index)
    def packet(capsule):
        seen.packets.append(deepcopy(capsule))
        return SanitizedPacket('Suggest a different bounded validation approach.',
            'Two local interventions preserved the original acceptance requirements.',
            approved_for_external_use=True)
    async def request(req):
        c = active(); count = req.url.path.endswith('/input_tokens')
        assert req.url.host == 'api.openai.com' and req.method == 'POST'
        assert c.journal.state['responses_help_preflight']['status'] == ('intent' if count else 'counted')
        seen.calls.append((c.journal.run_id, req.url.path, json.loads(req.content)))
        body = {'object': 'response.input_tokens', 'input_tokens': 37} if count else response_body()
        status = 429 if count and mode == 'count_fault' else 500 if not count and mode == 'inference_fault' else 200
        class Closing(Bytes):
            async def aclose(self):
                await super().aclose()
                if count and mode == 'deadline': seen.now += timedelta(seconds=61)
                if count and mode == 'stop': c.stop()
        stream = Closing(json.dumps(body).encode()); seen.streams.append(stream)
        if not count and status == 200: seen.advised.add(c.journal.run_id)
        return httpx.Response(status, stream=stream)
    transport = ResponsesHelpTransport(api_key=KEY, io_transport=httpx.MockTransport(request))
    assistance = PhaseAssistance(route=HelpRoute('selected-responses', 'public-model', 'd' * 64),
        transport=transport, packet_factory=packet, version='e' * 64)
    specs = tuple(PhaseSpec('phase-' + str(i), 'Finish generic phase ' + str(i),
        '- Save the exact phase acceptance artifact.', 'artifact-' + str(i), 'a' * 64,
        lambda index=i: check(index)) for i in range(phases))
    runner = PhaseRunner(worker, specs, goal='Complete the immutable generic schedule',
        state_dir=tmp_path / 'schedules', assistance=assistance, recovery_policy=quick_policy(),
        limits=PhaseLimits(max_dispatches=cap, max_wall_seconds=60), clock=lambda: seen.now,
        evaluator_backend_factory=lambda settings, tools, *args: Reviewer(tools),
        on_event=lambda event: seen.events.append(event.kind))
    holder['runner'] = runner
    return runner, seen, inference


def children(runner, result):
    schedule = runner.inspect(result.schedule_id)
    states = [inspect_run(runner.children, row['run_id'])['state']
              for row in schedule['phases'] if row['run_id']]
    return schedule, states


def assert_no_replay(before, runner, seen, inference, schedule_id):
    assert before == (len(seen.calls), len(seen.changed), len(seen.reviews), len(seen.checks),
                      len(inference.requests), runner.inspect(schedule_id)['dispatches'])


def observed(runner, seen, inference, schedule_id):
    return (len(seen.calls), len(seen.changed), len(seen.reviews), len(seen.checks),
            len(inference.requests), runner.inspect(schedule_id)['dispatches'])


@pytest.mark.parametrize('phases', [1, 2])
async def test_actual_phase_help_prepares_per_child_and_preserves_terminal_debt(worker, tmp_path, phases):
    runner, seen, inference = workflow(worker, tmp_path, phases=phases)
    result = await runner.run()
    assert result.status == 'done' and result.completed_phases == phases
    schedule, states = children(runner, result)
    assert 'responses_help.py' in schedule['manifest']['runtime_sources']
    assert len(seen.calls) == phases * 2
    assert len(seen.changed) == len(seen.reviews) == len(seen.checks) == phases
    assert len(set(seen.changed)) == phases and all(s.closed for s in seen.streams)
    for index, state in enumerate(states):
        run_id = state['run_id']; managed = state['managed']
        calls = [row for row in seen.calls if row[0] == run_id]
        assert [row[1] for row in calls] == ['/v1/responses/input_tokens', '/v1/responses']
        assert all(calls[0][2][key] == calls[1][2][key] for key in ('model', 'input', 'tools'))
        assert managed['recovery'] == state['help_continuation']['origin']
        assert managed['recovery']['total_interventions'] == 2
        assert managed['recovery']['controller']['help_used'] == 1
        assert managed['recovery']['pending']['kind'] == 'needs_help'
        assert managed['boundary']['status'] == 'acknowledged' and state['context_archive']
        assert managed['completed_receipt'] == schedule['phases'][index]['completion']['receipt_id']
        assert worker.tasks.get(managed['task_id'])['status'] == 'done'
        events = [row['event'] for row in ledger_events(tmp_path, run_id)]
        assert events.index('responses_help_count_intent') < events.index('responses_help_count_counted')
        assert events.index('responses_help_count_counted') < events.index('frontier_help_send_intent')
        assert events.index('frontier_help_returned') < events.index('assistance_authorized')
    kinds = dispatch_kinds(tmp_path, result.schedule_id)
    assert kinds.count('frontier_help') == phases and schedule['dispatches'] == phases * 6
    before = observed(runner, seen, inference, result.schedule_id)
    assert (await runner.run(resume_schedule_id=result.schedule_id)).status == 'done'
    assert_no_replay(before, runner, seen, inference, result.schedule_id)


@pytest.mark.parametrize('mode', ['count_fault', 'inference_fault', 'deadline', 'stop'])
async def test_transport_fault_deadline_or_stop_never_admits_changed_work(worker, tmp_path, mode):
    runner, seen, inference = workflow(worker, tmp_path, mode=mode)
    result = await runner.run()
    assert result.status != 'done' and result.completed_phases == 0
    assert len(seen.calls) == (2 if mode == 'inference_fault' else 1)
    assert seen.changed == seen.reviews == seen.checks == []
    schedule, states = children(runner, result); state = states[0]
    assert state['managed']['completed_receipt'] is None and schedule['phases'][0]['completion'] is None
    assert state['managed']['recovery']['controller']['help_used'] == 1
    before = observed(runner, seen, inference, result.schedule_id)
    resumed = await runner.run(resume_schedule_id=result.schedule_id,
        resume_note='Owner inspected the interrupted advisory request; no unresolved request may be repeated.')
    assert resumed.status != 'done'
    assert_no_replay(before, runner, seen, inference, result.schedule_id)


async def test_original_parent_cap_refuses_even_count_disclosure(worker, tmp_path):
    runner, seen, inference = workflow(worker, tmp_path, cap=4)
    result = await runner.run()
    assert result.status != 'done' and seen.calls == seen.changed == seen.reviews == seen.checks == []
    schedule, states = children(runner, result)
    assert schedule['dispatches'] <= 4 and states[0]['managed']['recovery']['controller']['help_used'] == 1
    before = observed(runner, seen, inference, result.schedule_id)
    await runner.run(resume_schedule_id=result.schedule_id, resume_note='Owner confirms the same original dispatch cap.')
    assert_no_replay(before, runner, seen, inference, result.schedule_id)


@pytest.mark.parametrize('point', ['intent', 'counted', 'returned'])
async def test_explicit_resume_reuses_only_exact_settled_request_bookkeeping(worker, tmp_path, monkeypatch, point):
    runner, seen, inference = workflow(worker, tmp_path, cap=6)
    class Crash(BaseException): pass
    actual = RunState.record; captured = {}
    event = {'intent': 'responses_help_count_intent', 'counted': 'responses_help_count_counted',
             'returned': 'frontier_help_returned'}[point]
    def record(journal, name, *args, **kwargs):
        result = actual(journal, name, *args, **kwargs)
        if point != 'counted' and name == event and not captured:
            captured.update(schedule_id=runner._journal.run_id, run_id=journal.run_id)
            raise Crash('synthetic interruption after durable ' + point)
        return result
    async def before_dispatch(helper, *args, **kwargs):
        captured.update(schedule_id=runner._journal.run_id, run_id=helper.journal.run_id)
        assert helper.journal.state['responses_help_preflight']['status'] == 'counted'
        raise Crash('synthetic interruption after settled count before inference intent')
    with monkeypatch.context() as patch:
        patch.setattr(RunState, 'record', record)
        if point == 'counted': patch.setattr(FrontierHelp, 'dispatch', before_dispatch)
        with pytest.raises(Crash): await runner.run()
    assert seen.changed == seen.reviews == seen.checks == []
    assert len(seen.calls) == {'intent': 0, 'counted': 1, 'returned': 2}[point]
    resumed = await runner.run(resume_schedule_id=captured['schedule_id'],
        resume_note='Owner inspected the exact durable request boundary; reuse only already settled evidence.')
    schedule, states = children(runner, resumed)
    assert states[0]['run_id'] == captured['run_id']
    if point == 'intent':
        assert resumed.status != 'done' and seen.calls == seen.changed == seen.reviews == seen.checks == []
        assert schedule['dispatches'] == 4
    else:
        assert resumed.status == 'done' and len(seen.calls) == 2
        assert len(seen.changed) == len(seen.reviews) == len(seen.checks) == 1
        assert schedule['dispatches'] == 6


async def test_counted_resume_requires_owner_note_and_unchanged_input_binding(worker, tmp_path, monkeypatch):
    runner, seen, inference = workflow(worker, tmp_path, cap=6)
    class Crash(BaseException): pass
    captured = {}
    async def before_dispatch(helper, *args, **kwargs):
        captured.update(schedule_id=runner._journal.run_id, run_id=helper.journal.run_id)
        assert helper.journal.state['responses_help_preflight']['status'] == 'counted'
        raise Crash('synthetic interruption before inference intent')
    with monkeypatch.context() as patch:
        patch.setattr(FrontierHelp, 'dispatch', before_dispatch)
        with pytest.raises(Crash): await runner.run()
    assert len(seen.calls) == 1 and seen.changed == seen.reviews == seen.checks == []
    before = observed(runner, seen, inference, captured['schedule_id'])
    implicit = await runner.run(resume_schedule_id=captured['schedule_id'])
    assert implicit.status != 'done'
    assert_no_replay(before, runner, seen, inference, captured['schedule_id'])
    journal = RunState(runner.children, captured['run_id'])
    try:
        changed = deepcopy(journal.state['responses_help_preflight'])
        changed['binding']['input_sha256'] = 'f' * 64
        journal.record('fixture_count_binding_changed', incremental=True, responses_help_preflight=changed)
    finally: journal.close()
    resumed = await runner.run(resume_schedule_id=captured['schedule_id'],
        resume_note='Owner requests inspection; changed count bindings must not authorize disclosure.')
    assert resumed.status != 'done'
    assert_no_replay(before, runner, seen, inference, captured['schedule_id'])
