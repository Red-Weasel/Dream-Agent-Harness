"""DREAM-219, the Frontier review loop's runner, sequential core (F4; ADR-070): create, judge, decide, revise until a
stop rule ends the run, with the lead's stop order of 2026-09-30 (an undecided round first). A scripted `call` stands
in for every model; a fake clock for time. No model, no network, no GPU."""
from __future__ import annotations

import asyncio
import copy
import importlib
import json

import pytest

WRITER = 'Brand storyteller'
JUDGES = ['Creative director', 'Motion designer', 'Sceptical viewer']
BRIEF = 'Launch a note-taking app in 30 seconds.'


def fr():
    """The module under test, imported in each test so that each one fails on its own while the module is missing."""
    return importlib.import_module('dream.workflows.frontier_runner')


def template(**stop):
    from dream.workflows.frontier import builtin_templates
    data = copy.deepcopy(builtin_templates()[0])
    data['stop'].update(stop)
    return data


def board(*frames):
    frames = frames or (72, 96, 120, 144, 168, 120)
    shot = lambda n: {'frames': n, 'camera': {'from': [0, -8, 2], 'to': [0, -6, 2], 'target': [0, 0, 1]},
                      'fade_in': 0, 'fade_out': 0,
                      'elements': [{'type': 'sphere', 'at': [0, 0, 1], 'size': 1.5, 'color': '#f2b134'}]}
    return {'version': 1, 'background': '#0b0d10', 'shots': [shot(n) for n in frames]}


def draft(n, frames=()):
    return f'Draft {n}: the app opens on a blank page that fills itself.\n\n```json\n{json.dumps(board(*frames))}\n```\n'


def ok(text, tokens=1000, source='provider'):
    return {'text': text, 'usage': {'prompt_tokens': tokens, 'completion_tokens': 0}, 'usage_source': source,
            'duration_ms': 5, 'outcome': 'ok'}


def failed(outcome='unavailable', text='[Judge: unavailable — RuntimeError: gone]'):
    return {'text': text, 'usage': {'prompt_tokens': 0, 'completion_tokens': 0}, 'usage_source': 'unknown',
            'duration_ms': 5, 'outcome': outcome}


def score(*criteria, note='A note.'):
    keys = ['hook_3s', 'brand_fit', 'blender_renderable', 'clarity']
    return ok(json.dumps({**dict(zip(keys, criteria)), 'note': note}))


# Shown judge scores whose round averages are the mockup's: 7.0 + 6.8 + 6.3 -> 6.7; 8.0 + 7.8 + 7.5 -> 7.8;
# 8.3 + 8.0 + 8.0 -> 8.1 (four whole criteria give .0, .3, .5 or .8).
ROUND_1 = [score(7, 7, 7, 7, note='CD one'), score(7, 7, 6, 7, note='MD one'), score(6, 6, 7, 6, note='SV one')]
ROUND_2 = [score(8, 8, 8, 8, note='CD two'), score(8, 8, 7, 8, note='MD two'), score(7, 8, 7, 8, note='SV two')]
ROUND_3 = [score(8, 8, 9, 8, note='CD three'), score(8, 8, 8, 8, note='MD three'), score(8, 8, 8, 8, note='SV three')]
ROUND_3_LOW = [score(8, 8, 7, 7), score(7, 8, 7, 8), score(7, 8, 8, 7)]           # 7.5 each -> 7.5


def scripted(writer, *rounds, judges=None, clock=None, tick=None):
    """A `call` that answers from queues: the writer's replies in order, each judge's in order."""
    queues = {WRITER: list(writer)}
    if judges is not None:
        queues.update({name: list(replies) for name, replies in judges.items()})
    else:
        for n, name in enumerate(JUDGES):
            queues[name] = [r[n] for r in rounds]
    calls = []

    async def call(persona, system, prompt, *, timeout, effort=None, run=None):
        calls.append({'name': persona['name'], 'system': system, 'prompt': prompt, 'timeout': timeout,
                      'effort': effort, 'run': run})
        if tick is not None:
            clock.now += tick(persona['name'])
        return queues[persona['name']].pop(0)
    call.calls = calls
    return call


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def runner(data, call, **kwargs):
    rows, saves = [], []
    run = fr().FrontierRun(data, BRIEF, run_id='run-1', call=call, record=rows.append,
                           save=lambda state: saves.append(copy.deepcopy(state)), **kwargs)
    run.rows, run.saves = rows, saves
    return run


def check_bookkeeping(rows):
    """Every call has one request row and one response row; the run ends with exactly one terminal status row."""
    requests = [r['call_id'] for r in rows if r['kind'] == 'request']
    responses = [r['call_id'] for r in rows if r['kind'] == 'response']
    assert len(requests) == len(set(requests)) and sorted(requests) == sorted(responses)
    for call_id in requests:
        ids = [n for n, r in enumerate(rows) if r.get('call_id') == call_id]
        assert [rows[n]['kind'] for n in ids] == ['request', 'response']
    terminal = [r for r in rows if r['kind'] == 'status' and r['status'] in ('needs_you', 'completed', 'failed', 'stopped')]
    assert len(terminal) == 1 and rows[-1] is terminal[0]
    assert all(r['run_id'] == 'run-1' and r['phase'] == 'workflow' and r['workflow'] == 'Frontier review loop'
               for r in rows)
    return terminal[0]


# --- the mockup's path ------------------------------------------------------------------------------------------------

async def test_the_mockups_path_revises_at_6_7_and_7_8_and_is_render_ready_at_8_1():
    call = scripted([ok(draft(1)), ok(draft(2)), ok(draft(3))], ROUND_1, ROUND_2, ROUND_3)
    run = runner(template(), call)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES] * 3                     # the exact call order
    assert [r['average'] for r in state['rounds']] == [6.7, 7.8, 8.1]
    assert [r['decision'] for r in state['rounds']] == ['revise', 'revise', 'render_ready']
    assert (state['status'], state['decision']) == ('needs_you', 'render_ready')
    final = check_bookkeeping(run.rows)
    assert (final['status'], final['decision']) == ('needs_you', 'render_ready')
    decisions = [r for r in run.rows if r['kind'] == 'decision']
    assert [(r['loop_round'], r['average'], r['decision']) for r in decisions] == [
        (1, 6.7, 'revise'), (2, 7.8, 'revise'), (3, 8.1, 'render_ready')]


async def test_each_revision_carries_every_judges_scores_and_note_and_the_previous_draft():
    call = scripted([ok(draft(1)), ok(draft(2)), ok(draft(3))], ROUND_1, ROUND_2, ROUND_3)
    await runner(template(), call).run()
    writer = [c for c in call.calls if c['name'] == WRITER]
    first_revision, second_revision = writer[1]['prompt'], writer[2]['prompt']
    assert draft(1) in first_revision and draft(2) in second_revision
    for name, note, criteria in zip(JUDGES, ['CD one', 'MD one', 'SV one'], [(7, 7, 7, 7), (7, 7, 6, 7), (6, 6, 7, 6)]):
        line = next(line for line in first_revision.splitlines() if line.startswith(f'- {name}:'))
        assert note in line
        assert all(f'{key} {value}' in line for key, value in
                   zip(['hook_3s', 'brand_fit', 'blender_renderable', 'clarity'], criteria))
    assert 'CD two' in second_revision and 'CD one' not in second_revision
    assert BRIEF in writer[0]['prompt'] and BRIEF in first_revision


async def test_writers_and_judges_get_their_timeouts_efforts_and_the_runs_meter():
    call = scripted([ok(draft(1)), ok(draft(2)), ok(draft(3))], ROUND_1, ROUND_2, ROUND_3)
    await runner(template(), call).run()
    for c in call.calls:
        assert (c['timeout'], c['effort']) == ((600.0, 'medium') if c['name'] == WRITER else (300.0, 'low'))
    meters = {id(c['run']) for c in call.calls}
    assert len(meters) == 1 and call.calls[0]['run'].profile.max_run_tokens == 600_000


async def test_a_judge_reads_the_draft_text_but_not_the_storyboard_json():
    call = scripted([ok(draft(1)), ok(draft(2)), ok(draft(3))], ROUND_1, ROUND_2, ROUND_3)
    await runner(template(), call).run()
    judge = next(c for c in call.calls if c['name'] == 'Motion designer')
    assert 'Draft 1: the app opens on a blank page that fills itself.' in judge['prompt']
    assert '```' not in judge['prompt'] and '"shots"' not in judge['prompt']
    assert BRIEF in judge['prompt'] and 'blender_renderable' in judge['prompt']


# --- stops ------------------------------------------------------------------------------------------------------------

async def test_three_rounds_below_the_threshold_stop_after_the_third_judging_and_name_the_best_round():
    call = scripted([ok(draft(1)), ok(draft(2)), ok(draft(3))], ROUND_1, ROUND_2, ROUND_3_LOW)
    run = runner(template(), call)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES] * 3                     # no fourth writer call
    assert [r['average'] for r in state['rounds']] == [6.7, 7.8, 7.5]
    assert (state['status'], state['decision'], state['best_round']) == ('completed', 'stop_rounds', 2)
    final = check_bookkeeping(run.rows)
    assert final['decision'] == 'stop_rounds' and 'round 2' in final['text'] and '7.8' in final['text']


async def test_a_projection_over_the_cap_stops_before_the_step():
    # the cap is 10,000; the first draft costs 3,000; the judge round expects 47,000 (4,000 + 14,000 + 29,000)
    call = scripted([ok(draft(1), tokens=3_000)], ROUND_1)
    run = runner(template(max_tokens=10_000), call)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER]                                  # no judge call started
    assert (state['status'], state['decision']) == ('completed', 'stop_tokens')
    assert state['tokens'] == {'used': 3_000, 'cap': 10_000, 'estimated': False, 'at_least': False}
    assert check_bookkeeping(run.rows)['decision'] == 'stop_tokens'


async def test_a_single_call_that_overshoots_the_cap_is_shown_as_it_is():
    call = scripted([ok(draft(1), tokens=12_500)], ROUND_1)
    state = await runner(template(max_tokens=10_000), call).run()
    assert state['decision'] == 'stop_tokens' and state['tokens']['used'] == 12_500      # over the cap, as it was


async def test_the_revision_is_projected_after_a_round():
    # a 50,000 cap: the draft costs 1,000 and round 1's judging 3,000; after it the revision projects 4,000 + 13,000
    # and goes ahead (1,000); round 2's judging then projects 5,000 + max(47,000, 3,000) = 52,000: over the cap
    call = scripted([ok(draft(1)), ok(draft(2))], ROUND_1, ROUND_2)
    state = await runner(template(max_tokens=50_000), call).run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES, WRITER]
    assert state['decision'] == 'stop_tokens' and state['tokens']['used'] == 5_000


async def test_too_few_valid_judges_need_you_even_on_the_last_round():
    call = scripted([ok(draft(1))], judges={JUDGES[0]: [ROUND_1[0]], JUDGES[1]: [failed()], JUDGES[2]: [failed('timeout', 'No answer within 300 seconds')]})
    run = runner(template(), call)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES]
    assert (state['status'], state['decision']) == ('needs_you', 'stop_judges')
    assert state['rounds'][0]['average'] is None
    assert [j['outcome'] for j in state['rounds'][0]['judges']] == ['ok', 'unavailable', 'timeout']
    check_bookkeeping(run.rows)
    call = scripted([ok(draft(1))], judges={JUDGES[0]: [failed()], JUDGES[1]: [failed()], JUDGES[2]: [ROUND_1[2]]})
    state = await runner(template(rounds=1), call).run()
    assert state['decision'] == 'stop_judges'                                            # not stop_rounds


async def test_an_unparseable_judge_gets_exactly_one_repair_call_then_is_unparseable():
    call = scripted([ok(draft(1))], judges={
        JUDGES[0]: [ok('I liked it, maybe an 8?'), ok('Still no JSON, sorry.')],
        JUDGES[1]: [ok('```json\n{"hook_3s": 7}\n```'), score(7, 7, 6, 7)],                  # repaired
        JUDGES[2]: [ROUND_1[2]]})
    run = runner(template(rounds=1), call)
    state = await run.run()
    # every judge is asked first, then the repairs (the order F5's parallel round keeps)
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES, JUDGES[0], JUDGES[1]]
    repair = call.calls[4]['prompt']
    assert repair.startswith(call.calls[1]['prompt']) and repair.endswith('Reply with only the JSON object.')
    assert 'I liked it, maybe an 8?' in repair and 'The reply holds no JSON object' in repair
    judges = state['rounds'][0]['judges']
    assert [j['outcome'] for j in judges] == ['unparseable', 'ok', 'ok']
    assert judges[1]['value'] == 6.8 and state['rounds'][0]['average'] == 6.6            # (6.8 + 6.3) / 2 = 6.55
    responses = [r for r in run.rows if r['kind'] == 'response' and r['agent'] == JUDGES[0]]
    assert [r['status'] for r in responses] == ['unparseable', 'unparseable']
    check_bookkeeping(run.rows)


async def test_an_invalid_storyboard_goes_back_to_the_writer_at_most_twice_then_the_run_fails():
    bad = ok(draft(1, (72, 96, 120, 144, 168, 119)))                               # 719 frames
    call = scripted([bad, ok('No storyboard at all.'), bad], ROUND_1)
    run = runner(template(), call)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER, WRITER, WRITER]                 # no judge call
    assert "shots: the shots' frames add up to 719; they must add up to exactly 720" in call.calls[1]['prompt']
    assert 'The reply must hold exactly one storyboard JSON block' in call.calls[2]['prompt']
    assert state['status'] == 'failed' and 'storyboard' in state['reason']
    check_bookkeeping(run.rows)


async def test_a_writer_reply_over_the_cap_takes_the_repair_path_with_its_quote_cut():
    call = scripted([ok('x' * 70_000), ok(draft(2)), ok(draft(3)), ok(draft(4))], ROUND_1, ROUND_2, ROUND_3)
    state = await runner(template(), call).run()
    assert [c['name'] for c in call.calls][:5] == [WRITER, WRITER, *JUDGES]
    repair = call.calls[1]['prompt']
    assert 'The reply is too long: 70,000 characters, over 64,000' in repair
    assert 'x' * 2_000 in repair and 'x' * 2_001 not in repair and len(repair) < 4_000   # not the whole reply again
    assert state['decision'] == 'render_ready'


async def test_a_repaired_storyboard_goes_on_to_the_judges():
    call = scripted([ok(draft(1, (72, 96, 120, 144, 168, 119))), ok(draft(2)), ok(draft(3)), ok(draft(4))],
                    ROUND_1, ROUND_2, ROUND_3)
    state = await runner(template(), call).run()
    assert [c['name'] for c in call.calls][:5] == [WRITER, WRITER, *JUDGES]
    assert state['decision'] == 'render_ready' and state['rounds'][0]['draft'] == draft(2)


async def test_the_time_limit_on_a_fake_clock_gives_stop_time():
    clock = Clock()
    call = scripted([ok(draft(1)), ok(draft(2))], ROUND_1, ROUND_2, clock=clock, tick=lambda name: 700)
    run = runner(template(), call, clock=clock)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES]                          # 2,800 s after round 1
    assert (state['status'], state['decision']) == ('completed', 'stop_time')
    assert state['rounds'][0]['decision'] == 'stop_time'                                  # the round's own decision
    assert [r['decision'] for r in run.rows if r['kind'] == 'decision'] == ['stop_time']


async def test_the_time_limit_is_checked_before_each_step_too():
    # round 1 ends at 1,300 s (under the limit) and revises; the revision ends at 2,300 s: no round-2 judging starts,
    # and the run says stop_time, not a failure of the judges
    clock = Clock()
    call = scripted([ok(draft(1)), ok(draft(2))], ROUND_1, ROUND_2, clock=clock,
                    tick=lambda name: 1000 if name == WRITER else 100)
    state = await runner(template(), call, clock=clock).run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES, WRITER]
    assert state['decision'] == 'stop_time'


async def test_a_stop_after_a_round_decides_to_revise_stops_before_the_revision_starts():
    # decide() takes no Stop: a Stop that arrives once round 1 has decided to revise is honoured before the revision
    call = scripted([ok(draft(1)), ok(draft(2))], ROUND_1, ROUND_2)
    rows = []

    def record(row):
        rows.append(row)
        if row['kind'] == 'decision':
            run.stop()                                    # the owner presses Stop as round 1's decision arrives
    run = fr().FrontierRun(template(), BRIEF, run_id='run-1', call=call, record=record)
    state = await run.run()
    assert [c['name'] for c in call.calls] == [WRITER, *JUDGES]
    assert state['rounds'][0]['decision'] == 'revise' and state['status'] == 'stopped'
    assert check_bookkeeping(rows)['status'] == 'stopped'


async def test_an_unavailable_writer_fails_the_run():
    call = scripted([failed(text='[Brand storyteller: unavailable — RuntimeError: signed out]')])
    run = runner(template(), call)
    state = await run.run()
    assert state['status'] == 'failed' and 'signed out' in state['reason']
    check_bookkeeping(run.rows)


# --- the meter and the saves -------------------------------------------------------------------------------------------

async def test_the_meter_counts_every_call_and_marks_estimates_and_unknowns():
    judges = {JUDGES[0]: [score(8, 8, 8, 8)], JUDGES[1]: [dict(score(8, 8, 8, 8), usage_source='estimated')],
              JUDGES[2]: [failed()]}
    state = await runner(template(rounds=1), scripted([ok(draft(1), tokens=2_000)], judges=judges)).run()
    assert state['tokens'] == {'used': 4_000, 'cap': 600_000, 'estimated': True, 'at_least': True}
    assert state['decision'] == 'render_ready'


async def test_the_state_is_saved_after_each_round_and_at_the_end():
    call = scripted([ok(draft(1)), ok(draft(2)), ok(draft(3))], ROUND_1, ROUND_2, ROUND_3)
    run = runner(template(), call)
    await run.run()
    assert [len(s['rounds']) for s in run.saves] == [1, 2, 3, 3]
    assert run.saves[-1]['status'] == 'needs_you' and run.saves[0]['status'] == 'running'
