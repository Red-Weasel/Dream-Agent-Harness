"""DREAM-220, the Frontier review loop's round is parallel, blind and stoppable (F5; ADR-070): a round's judge calls all
start before any finishes and never more at once than there are judges; no judge prompt carries another judge's
canary, score or note, in that round or a later one, and each is exactly the rendering of its voice, the rubric, the
brief and the draft's text; scores are saved only after the round returns; the owner's Stop cancels every call in
flight and leaves exactly one stopped row. Scripted fakes only. No model, no network, no GPU."""
from __future__ import annotations

import asyncio
import copy
import importlib
import json

WRITER = 'Brand storyteller'
JUDGES = ['Creative director', 'Motion designer', 'Sceptical viewer']
KEYS = ['hook_3s', 'brand_fit', 'blender_renderable', 'clarity']
BRIEF = 'Launch a note-taking app in 30 seconds.'


def fr():
    """The module under test, imported in each test so that each one fails on its own while the module is missing."""
    return importlib.import_module('dream.workflows.frontier_runner')


def template(judges=None, **stop):
    from dream.workflows.frontier import builtin_templates
    data = copy.deepcopy(builtin_templates()[0])
    data['stop'].update(stop)
    if judges is not None:
        data['judges'] = [{'name': name, 'provider': 'openai', 'model': '', 'voice': f'You are {name}.'}
                          for name in judges]
    return data


def board():
    shot = lambda n: {'frames': n, 'camera': {'from': [0, -8, 2], 'to': [0, -6, 2], 'target': [0, 0, 1]},
                      'fade_in': 0, 'fade_out': 0,
                      'elements': [{'type': 'sphere', 'at': [0, 0, 1], 'size': 1.5, 'color': '#f2b134'}]}
    return {'version': 1, 'background': '#0b0d10', 'shots': [shot(n) for n in (72, 96, 120, 144, 168, 120)]}


def draft(n):
    return f'Draft {n}: the app opens on a blank page.\n\n```json\n{json.dumps(board())}\n```\n'


def ok(text, tokens=1000):
    return {'text': text, 'usage': {'prompt_tokens': tokens, 'completion_tokens': 0}, 'usage_source': 'provider',
            'duration_ms': 5, 'outcome': 'ok'}


def score(criteria, note):
    return ok(json.dumps({**dict(zip(KEYS, criteria)), 'note': note}))


# per round, per judge: whole criteria whose shown scores average to 6.7, 7.8 and 8.1 (the mockup's rounds)
CRITERIA = [[(7, 7, 7, 7), (7, 7, 6, 7), (6, 6, 7, 6)],
            [(8, 8, 8, 8), (8, 8, 7, 8), (7, 8, 7, 8)],
            [(8, 8, 9, 8), (8, 8, 8, 8), (8, 8, 8, 8)]]


def canary(judge, number):
    return f'CANARY-{JUDGES.index(judge)}-{number}'


class Fake:
    """A `call` that answers the writer with drafts and each judge with its round's scores and a unique canary note;
    `hold` (an awaitable factory) runs inside every judge call, and in-flight judge calls are counted."""

    def __init__(self, hold=None, writer_hold=None):
        self.hold, self.writer_hold = hold, writer_hold
        self.calls, self.inflight, self.peak, self.cancelled = [], 0, 0, []
        self.drafts = 0
        self.rounds = {name: 0 for name in JUDGES}

    async def __call__(self, persona, system, prompt, *, timeout, effort=None, run=None):
        name = persona['name']
        self.calls.append({'name': name, 'system': system, 'prompt': prompt})
        try:
            if name == WRITER:
                if self.writer_hold is not None:
                    await self.writer_hold()
                self.drafts += 1
                return ok(draft(self.drafts))
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            try:
                if self.hold is not None:
                    await self.hold(name)
            finally:
                self.inflight -= 1
            self.rounds[name] = number = self.rounds.get(name, 0) + 1
            criteria = CRITERIA[number - 1][JUDGES.index(name)] if name in JUDGES else (8, 8, 8, 8)
            return score(criteria, f'{canary(name, number) if name in JUDGES else name} says keep the logo.')
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise


def runner(data, call, **kwargs):
    rows, saves = [], []
    run = fr().FrontierRun(data, BRIEF, run_id='run-1', call=call, record=rows.append, save=saves.append, **kwargs)
    run.rows, run.saves = rows, saves
    return run


def paired(rows):
    requests = [r['call_id'] for r in rows if r['kind'] == 'request']
    responses = [r['call_id'] for r in rows if r['kind'] == 'response']
    return len(requests) == len(set(requests)) and sorted(requests) == sorted(responses)


def terminal(rows):
    return [r for r in rows if r['kind'] == 'status' and r['status'] in ('needs_you', 'completed', 'failed', 'stopped')]


# --- parallel -----------------------------------------------------------------------------------------------------------

async def test_all_of_a_rounds_judge_calls_start_before_any_finishes():
    barrier = asyncio.Barrier(3)
    fake = Fake(hold=lambda name: barrier.wait())
    state = await asyncio.wait_for(runner(template(), fake).run(), 10)     # a sequential round would never pass it
    assert state['decision'] == 'render_ready' and fake.peak == 3
    assert [c['name'] for c in fake.calls] == [WRITER, *JUDGES] * 3


async def test_concurrency_never_exceeds_the_number_of_judges():
    five = ['A', 'B', 'C', 'D', 'E']
    barrier = asyncio.Barrier(5)
    fake = Fake(hold=lambda name: barrier.wait())
    state = await asyncio.wait_for(runner(template(judges=five), fake).run(), 10)
    assert fake.peak == 5 and state['rounds'][0]['average'] == 8.0


# --- blind --------------------------------------------------------------------------------------------------------------

async def test_no_judge_prompt_holds_another_judges_canary_score_or_note():
    fake = Fake(hold=lambda name: asyncio.sleep(0.001 * JUDGES.index(name)))
    await runner(template(), fake).run()
    judged = [c for c in fake.calls if c['name'] != WRITER]
    assert len(judged) == 9
    canaries = [canary(name, number) for name in JUDGES for number in (1, 2, 3)]
    score_lines = [', '.join(f'{k} {v}' for k, v in zip(KEYS, criteria)) for rnd in CRITERIA for criteria in rnd]
    for call in judged:
        text = call['system'] + call['prompt']
        assert not any(c in text for c in canaries), call['name']
        assert not any(line in text for line in score_lines) and 'keep the logo' not in text
    revisions = [c['prompt'] for c in fake.calls if c['name'] == WRITER][1:]
    assert all(canary(name, 1) in revisions[0] for name in JUDGES)          # the notes reach the writer only


async def test_judge_prompts_are_exactly_the_rendering_of_voice_rubric_brief_and_draft_text():
    fake = Fake()
    data = template()
    await runner(data, fake).run()
    voices = {j['name']: j['voice'] for j in data['judges']}
    rubric = ('- hook_3s: Hook in 3 seconds\n- brand_fit: Brand fit\n- blender_renderable: Can Blender render it\n'
              '- clarity: Clarity')
    answer = ('{"hook_3s": <1-10>, "brand_fit": <1-10>, "blender_renderable": <1-10>, "clarity": <1-10>, '
              '"note": "<one or two sentences>"}')
    drafts = iter(range(1, 4))
    number = 0
    for call in fake.calls:
        if call['name'] == WRITER:
            number = next(drafts)
            continue
        text = f'Draft {number}: the app opens on a blank page.'
        assert call['system'] == voices[call['name']]
        assert call['prompt'] == (f'Rubric, each scored from 1 to 10 in whole numbers:\n{rubric}\n\nBrief:\n{BRIEF}\n\n'
                                  f'Draft:\n<<<\n{text}\n>>>\n\nReply with only one JSON object: {answer}')


# --- saving -------------------------------------------------------------------------------------------------------------

async def test_scores_are_saved_only_after_the_round_returns():
    fake = Fake(hold=lambda name: asyncio.sleep(0.005 * JUDGES.index(name)))
    seen = []
    run = fr().FrontierRun(template(), BRIEF, run_id='run-1', call=fake,
                           save=lambda state: seen.append((fake.inflight, copy.deepcopy(state['rounds']))))
    await run.run()
    assert [len(rounds) for _, rounds in seen] == [1, 2, 3, 3]
    for inflight, rounds in seen:
        assert inflight == 0                                               # no judge was still answering
        assert all(j['outcome'] == 'ok' and j['value'] is not None for r in rounds for j in r['judges'])


# --- stopping -----------------------------------------------------------------------------------------------------------

async def test_stopping_during_judging_cancels_every_call_in_flight_and_leaves_one_stopped_row():
    all_in, never = asyncio.Event(), asyncio.Event()

    async def hold(name):
        if fake.inflight == 3:
            all_in.set()
        await never.wait()
    fake = Fake(hold=hold)
    run = runner(template(), fake)
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(all_in.wait(), 10)
    run.stop()
    state = await asyncio.wait_for(task, 10)
    assert sorted(fake.cancelled) == sorted(JUDGES)                        # every call in flight was cancelled
    assert state['status'] == 'stopped' and state['rounds'] == []          # nothing decided from a stopped round
    await asyncio.sleep(0.05)
    assert [c['name'] for c in fake.calls] == [WRITER, *JUDGES]            # nothing asked again
    assert paired(run.rows)
    cancelled = [r for r in run.rows if r['kind'] == 'response' and r['status'] == 'cancelled']
    assert sorted(r['agent'] for r in cancelled) == sorted(JUDGES)
    assert len(terminal(run.rows)) == 1 and run.rows[-1]['status'] == 'stopped'
    assert state['tokens']['at_least'] is True and run.saves[-1]['status'] == 'stopped'


async def test_stopping_during_the_writers_call_behaves_the_same():
    started, never = asyncio.Event(), asyncio.Event()

    async def writer_hold():
        started.set()
        await never.wait()
    fake = Fake(writer_hold=writer_hold)
    run = runner(template(), fake)
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(started.wait(), 10)
    run.stop()
    state = await asyncio.wait_for(task, 10)
    assert fake.cancelled == [WRITER] and state['status'] == 'stopped'
    await asyncio.sleep(0.05)
    assert [c['name'] for c in fake.calls] == [WRITER]
    assert paired(run.rows) and len(terminal(run.rows)) == 1 and run.rows[-1]['status'] == 'stopped'


async def test_a_cancellation_from_outside_still_answers_every_request():
    all_in, never = asyncio.Event(), asyncio.Event()

    async def hold(name):
        if fake.inflight == 3:
            all_in.set()
        await never.wait()
    fake = Fake(hold=hold)
    run = runner(template(), fake)
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(all_in.wait(), 10)
    task.cancel()                                     # Dream going away, not the owner's Stop
    try:
        await asyncio.wait_for(task, 10)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError('the run swallowed a cancellation it did not ask for')
    assert sorted(fake.cancelled) == sorted(JUDGES) and paired(run.rows)
    assert terminal(run.rows) == []                   # interrupted: the store marks it (F6), no stopped row is invented


async def test_a_cancellation_from_outside_during_the_writers_call_propagates_even_after_a_stop():
    for press_stop in (False, True):
        started, never = asyncio.Event(), asyncio.Event()

        async def writer_hold():
            started.set()
            await never.wait()
        fake = Fake(writer_hold=writer_hold)
        run = runner(template(), fake)
        task = asyncio.create_task(run.run())
        await asyncio.wait_for(started.wait(), 10)
        if press_stop:
            run.stopping = True                       # a Stop already asked for (its cancel not yet delivered)
        task.cancel()
        try:
            await asyncio.wait_for(task, 10)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError(f'the run swallowed a cancellation it did not ask for (stop {press_stop})')
        assert fake.cancelled == [WRITER] and paired(run.rows) and terminal(run.rows) == []


# --- a Stop that arrives while a step is being set up (gate loop-f3f5 round 1, finding F5-1) ------------------------------

WRITER_QUEUE = [ok(draft(1)), ok(draft(2)), ok(draft(3))]


class Scripted:
    """A `call` answering from queues, yielding a few loop iterations inside every call (as a real call is in flight
    across them), and logging each start, end and cancellation in order with the rows and the Stop."""

    def __init__(self, queues, log, hold=None):
        self.queues, self.log, self.hold = {k: list(v) for k, v in queues.items()}, log, hold
        self.running = 0

    async def __call__(self, persona, system, prompt, *, timeout, effort=None, run=None):
        name = persona['name']
        self.log.append(('start', name))
        self.running += 1
        try:
            for _ in range(3):
                await asyncio.sleep(0)
            if self.hold is not None:
                await self.hold(name, prompt)
            reply = self.queues[name].pop(0)
            self.log.append(('end', name))
            return reply
        except asyncio.CancelledError:
            self.log.append(('cancel', name))
            raise
        finally:
            self.running -= 1


def repair_scenario():
    """The mockup's path with a storyboard repair in round 1 and a judge repair in round 2."""
    bad = ok(draft(1).replace('"frames": 72', '"frames": 71'))   # the first shot one frame short: 719 frames
    return {WRITER: [bad, ok(draft(1)), ok(draft(2)), ok(draft(3))],
            JUDGES[0]: [score(CRITERIA[0][0], 'a1'), score(CRITERIA[1][0], 'a2'), score(CRITERIA[2][0], 'a3')],
            JUDGES[1]: [score(CRITERIA[0][1], 'b1'), ok('no json at all'), score(CRITERIA[1][1], 'b2'),
                        score(CRITERIA[2][1], 'b3')],
            JUDGES[2]: [score(CRITERIA[0][2], 'c1'), score(CRITERIA[1][2], 'c2'), score(CRITERIA[2][2], 'c3')]}


def scripted_run(queues, *, stop_at=None, hold=None):
    """A run whose Stop, when `stop_at` names a row index, is scheduled with call_soon as that row is recorded (as a
    route handler's Stop arrives between loop iterations)."""
    log, rows = [], []
    fake = Scripted(queues, log, hold)
    holder = {}

    def record(row):
        rows.append(row)
        log.append(('row', row['kind'], row.get('decision')))
        if stop_at is not None and len(rows) - 1 == stop_at:
            asyncio.get_running_loop().call_soon(holder['stop'])
    run = fr().FrontierRun(template(), BRIEF, run_id='run-1', call=fake, record=record)

    def stop():
        log.append(('stop',))
        run.stop()
    holder['stop'] = stop
    return run, fake, log, rows


def started_after_stop(log):
    if ('stop',) not in log:
        return []
    return [e[1] for e in log[log.index(('stop',)) + 1:] if e[0] == 'start']


async def test_a_stop_arriving_as_the_writer_answers_starts_no_judge_call():
    run, fake, log, rows = scripted_run({WRITER: WRITER_QUEUE[:1], **{j: [] for j in JUDGES}}, stop_at=2)
    state = await asyncio.wait_for(run.run(), 10)
    assert [r['kind'] for r in rows[:3]] == ['status', 'request', 'response'] and rows[2]['agent'] == WRITER
    assert [e[1] for e in log if e[0] == 'start'] == [WRITER]          # no judge call ever started
    assert state['status'] == 'stopped' and paired(rows)
    assert len(terminal(rows)) == 1 and rows[-1]['status'] == 'stopped'
    answered = [r for r in rows if r['kind'] == 'response' and r['agent'] in JUDGES]
    assert [r['status'] for r in answered] == ['cancelled'] * 3         # each asked-for judge answered, never called


async def test_no_call_starts_after_a_stop_at_any_row():
    baseline, _, _, base_rows = scripted_run(repair_scenario())
    state = await asyncio.wait_for(baseline.run(), 10)
    assert state['decision'] == 'render_ready' and len(base_rows) > 30
    for at in range(len(base_rows)):
        run, fake, log, rows = scripted_run(repair_scenario(), stop_at=at)
        state = await asyncio.wait_for(run.run(), 10)
        for _ in range(20):
            await asyncio.sleep(0)
        assert started_after_stop(log) == [], (at, base_rows[at]['kind'], started_after_stop(log))
        assert fake.running == 0 and paired(rows) and len(terminal(rows)) == 1 and rows[-1] is terminal(rows)[0], at
        final = next((n for n, e in enumerate(log) if e[:2] == ('row', 'decision') and e[2] == 'render_ready'), len(log))
        expected = 'stopped' if ('stop',) in log and log.index(('stop',)) < final else 'needs_you'
        assert state['status'] == expected, (at, base_rows[at]['kind'], state['status'])


async def test_a_stop_during_a_storyboard_repair_stops_the_run():
    started, never = asyncio.Event(), asyncio.Event()

    async def hold(name, prompt):
        if name == WRITER and 'could not be used' in prompt:
            started.set()
            await never.wait()
    run, fake, log, rows = scripted_run(repair_scenario(), hold=hold)
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(started.wait(), 10)
    run.stop()
    state = await asyncio.wait_for(task, 10)
    assert state['status'] == 'stopped' and state['reason'] == 'Stopped by you.'      # not a writer failure
    assert [e[1] for e in log if e[0] == 'start'] == [WRITER, WRITER] and paired(rows)


async def test_a_stop_during_a_judge_repair_stops_the_run_without_deciding():
    started, never = asyncio.Event(), asyncio.Event()

    async def hold(name, prompt):
        if name == JUDGES[1] and prompt.endswith('Reply with only the JSON object.'):
            started.set()
            await never.wait()
    run, fake, log, rows = scripted_run(repair_scenario(), hold=hold)
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(started.wait(), 10)
    run.stop()
    state = await asyncio.wait_for(task, 10)
    assert state['status'] == 'stopped' and len(state['rounds']) == 1                 # round 2 is not decided
    assert [r['decision'] for r in rows if r['kind'] == 'decision'] == ['revise'] and paired(rows)
