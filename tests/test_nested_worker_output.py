"""Nested Dream P2 (DREAM-189): a worker's own words and facts reach the owner's view.

ADR-068 amends ADR-013: a sub-agent's reply is no longer kept from the display. Each round's assistant content is the
`text` of that round's agent_activity `response` row (bounded and redacted like every other activity text), with the
server's usage counts and the request's wall time; every row from the first request on carries the run's model (the
routed role model or the lead's) and its context fill {used, window}. The lead receives the answer exactly as before.
Scripted HTTP fixtures only: no engine, no model, no GPU.
"""
import copy
import json
import time

from dream.agent_activity import bounded_agent_activity
from dream.core.backends import openai_compat
from dream.core.backends.base import Event
from dream.gui.conversation import Conversation
from test_agent_activity import activities, worker
from test_local_subagents import _FakeClient, _PostResp, _backend, _researcher, _sub_final, _sub_toolcall, _tool
from test_settings_roles import _write


def _reply_with_call(text, name, args):
    reply = _sub_toolcall(name, args)
    reply['choices'][0]['message']['content'] = text
    return reply


async def test_response_rows_carry_each_rounds_text_model_usage_and_duration():
    events = []
    first = _reply_with_call('Looking at the source first.', 'web_search', {'query': 'observations'})
    first['usage'] = {'prompt_tokens': 11, 'completion_tokens': 4}
    final = _sub_final('The source confirms it.')
    final['usage'] = {'prompt_tokens': 30, 'completion_tokens': 9}
    backend = worker(events, [first, final])
    answer, failed = await backend._run_subagent('researcher', 'inspect')
    assert (answer, failed) == ('The source confirms it.', False)
    rows = activities(events)
    responses = [r for r in rows if r['kind'] == 'response']
    assert [r['text'] for r in responses] == ['Looking at the source first.', 'The source confirms it.']
    assert [r['usage'] for r in responses] == [{'prompt_tokens': 11, 'completion_tokens': 4},
                                               {'prompt_tokens': 30, 'completion_tokens': 9}]
    assert all(type(r['duration_ms']) is int and r['duration_ms'] >= 0 for r in responses)
    assert all(r['model'] == 'm' for r in rows[1:]), rows
    assert backend._delegated_usage == {'prompt_tokens': 41, 'completion_tokens': 13}


async def test_a_round_without_content_or_usage_shows_neither():
    events = []
    backend = worker(events, [_sub_toolcall('web_search', {'query': 'x'}), _sub_final('done')])
    await backend._run_subagent('researcher', 'inspect')
    first, last = [r for r in activities(events) if r['kind'] == 'response']
    assert 'text' not in first and 'usage' not in first and type(first['duration_ms']) is int
    assert last['text'] == 'done' and 'usage' not in last


async def test_the_model_reported_is_the_routed_one_or_the_leads():
    events = []
    backend = worker(events, [_sub_final('lead answer')])
    await backend._run_subagent('researcher', 'inspect')
    rows = activities(events)
    assert 'model' not in rows[0] and {r['model'] for r in rows[1:]} == {'m'}
    _write({'roles': {'subagents': {'researcher': {'model': 'small-model'}}}})
    events = []
    backend = worker(events, [_sub_final('routed answer')])
    answer, failed = await backend._run_subagent('researcher', 'inspect')
    rows = activities(events)
    assert (answer, failed) == ('routed answer', False) and backend._client.posted[0]['model'] == 'small-model'
    assert 'model' not in rows[0] and {r['model'] for r in rows[1:]} == {'small-model'}


async def test_rows_carry_the_context_fill_and_window_from_the_first_request_on():
    events = []
    first = _sub_toolcall('web_search', {'query': 'observations'})
    first['usage'] = {'prompt_tokens': 11, 'completion_tokens': 4}
    backend = worker(events, [first, _sub_final('done')])
    await backend._run_subagent('researcher', 'inspect')
    rows = activities(events)
    assert 'context' not in rows[0]
    assert all(set(r['context']) == {'used', 'window'} for r in rows[1:]), rows
    window = backend._window()
    requests = [r for r in rows if r['kind'] == 'request']
    assert [r['context']['window'] for r in requests] == [window, window]
    assert 0 < requests[0]['context']['used'] < requests[1]['context']['used'] <= window
    assert rows[-1]['status'] == 'completed' and rows[-1]['context'] == requests[1]['context']


async def test_worker_text_is_redacted_for_display_but_the_lead_gets_it_whole():
    events = []
    report = 'Found it: data:image/png;base64,' + 'A' * 3000 + '. Key: ' + 'x' * 300 + '. End.'
    backend = worker(events, [_sub_final(report)])
    answer, failed = await backend._run_subagent('researcher', 'inspect')
    assert (answer, failed) == (report, False)
    shown = next(r for r in activities(events) if r['kind'] == 'response')['text']
    assert shown == 'Found it: [Media payload omitted]. Key: [Long unbroken value omitted]. End.'
    conversation = Conversation()
    for event in events:
        conversation.append(event)
    retained = json.dumps(conversation.snapshot())
    assert 'base64' not in retained and 'A' * 100 not in retained and 'x' * 100 not in retained
    assert 'Found it: [Media payload omitted]' in retained


async def test_a_long_reply_is_cut_for_display_only():
    events = []
    report = ('word ' * 4000).strip()
    backend = worker(events, [_sub_final(report)])
    answer, failed = await backend._run_subagent('researcher', 'inspect')
    assert (answer, failed) == (report, False)
    shown = next(r for r in activities(events) if r['kind'] == 'response')['text']
    assert shown == report[:12000] + '\n[Display truncated]'


def test_bounded_activity_keeps_only_well_formed_worker_facts():
    row = bounded_agent_activity({
        'run_id': 'r', 'agent': 'a', 'kind': 'response', 'text': 'reply', 'model': 'model.' * 60,
        'usage': {'prompt_tokens': 3, 'completion_tokens': '4', 'total_tokens': 7}, 'duration_ms': 12,
        'context': {'used': 5, 'window': 10, 'extra': 1}})
    assert row['text'] == 'reply' and row['model'] == ('model.' * 60)[:200] + '\n[Display truncated]'
    assert row['usage'] == {'prompt_tokens': 3} and row['duration_ms'] == 12
    assert row['context'] == {'used': 5, 'window': 10}
    for bad in ({'model': ''}, {'model': 7}, {'usage': [3, 4]}, {'usage': {'prompt_tokens': float('nan')}},
                {'usage': {'prompt_tokens': -1}}, {'usage': {'completion_tokens': True}}, {'duration_ms': -1},
                {'duration_ms': 1.5}, {'duration_ms': True}, {'duration_ms': 10 ** 400}, {'context': {'used': 5}},
                {'context': {'used': 5, 'window': 'big'}}, {'context': [5, 10]}):
        row = bounded_agent_activity({'run_id': 'r', 'agent': 'a', 'kind': 'status', **bad})
        assert not set(bad) & set(row), (bad, row)
        json.dumps(row, allow_nan=False)


def test_reconnect_history_keeps_the_worker_facts():
    conversation = Conversation()
    conversation.append(Event('agent_activity', {
        'run_id': 'r', 'agent': 'a', 'kind': 'response', 'text': 'reply', 'model': 'm',
        'usage': {'prompt_tokens': 3, 'completion_tokens': 4}, 'duration_ms': 12, 'context': {'used': 5, 'window': 10}}))
    data = conversation.snapshot()['events'][0]['data']
    assert data['text'] == 'reply' and data['model'] == 'm'
    assert data['usage'] == {'prompt_tokens': 3, 'completion_tokens': 4} and data['duration_ms'] == 12
    assert data['context'] == {'used': 5, 'window': 10}


# --- gate P2 follow-ups (DREAM-190 record) ---------------------------------------------------------------------
# 1. Coverage the gate found missing: a stale post-Handoff fill, and a continuation run with no context, both passed
#    the phase tests. These two probes (the gate's, adapted) fail when the context set before the request payload
#    (openai_compat._subagent_loop, "what this round's request carries") is deleted.

async def test_handoff_rows_report_the_fill_each_request_carried():
    calls = {'n': 0}

    async def h(a):
        calls['n'] += 1
        return {'content': [{'type': 'text', 'text': f"R{calls['n']} " + 'y' * 40_000}]}

    class _ByShape(_FakeClient):
        """Answers by the request's shape, so the trigger may fire at any round: the recovery ask gets the report,
        the fresh Handoff context gets DONE, everything else another distinct tool call."""
        async def post(self, url, json=None):
            self.posted.append(copy.deepcopy(json))
            msgs = json['messages']
            if msgs[-1].get('name') == 'dream_recovery_instruction':
                return _PostResp(_sub_final('REPORT: did R1'))
            if 'automatic Handoff' in (msgs[1].get('content') or ''):
                return _PostResp(_sub_final('DONE'))
            return _PostResp(_sub_toolcall('web_search', {'q': f'x{len(msgs)}'}))

    b = _backend([_tool('web_search', h), _tool('recall', h)], _researcher())
    events = []
    b._background_emit = events.append
    b.n_ctx = 8192
    b.apply_context_policy({'subagent_overflow': 'handoff'})
    b._client = _ByShape()
    out, err = await b._run_subagent('researcher', 'find X')
    assert (out, err) == ('DONE', False)
    rows = activities(events)
    handed = [r for r in rows if r['kind'] == 'status' and 'handed off' in r.get('text', '')]
    assert len(handed) == 1, [r.get('text') for r in rows]
    requests = [r for r in rows if r['kind'] == 'request']
    assert len(requests) >= 2
    # the handoff row reports the fill it fired on (over the trigger); the next request row the fresh, small fill
    window = 8192
    assert handed[0]['context']['window'] == window
    assert handed[0]['context']['used'] > window * b._trigger_at(window)
    assert requests[-1]['context']['used'] < handed[0]['context']['used']
    fresh = b._client.posted[-1]['messages']
    assert requests[-1]['context']['used'] == openai_compat._est_tokens(fresh), (requests[-1], openai_compat._est_tokens(fresh))
    assert all(r['model'] == 'm' for r in rows[1:])


async def test_continuation_rows_carry_the_leads_model_and_context():
    events = []
    b = worker(events, [_sub_final('CHECKED')])
    b.messages.append({'role': 'user', 'content': 'lead history'})
    out, err = await b._run_subagent('researcher', 'check it', continuation=True)
    assert (out, err) == ('CHECKED', False)
    rows = activities(events)
    requests = [r for r in rows if r['kind'] == 'request']
    assert len(requests) == 1 and requests[0]['model'] == 'm'
    assert requests[0]['context']['window'] == b._window() and type(requests[0]['context']['used']) is int
    response = next(r for r in rows if r['kind'] == 'response')
    assert response['text'] == 'CHECKED' and response['model'] == 'm'
    assert b.messages[-1]['role'] == 'assistant' and b.messages[-1]['content'] == 'CHECKED'


# 2. The redaction window: _text used to scan only limit+512 characters, so a payload longer than that hid the
#    words after it, with no marker. Redaction now runs over the whole value; the display bound applies after.

def _shown(text, kind='response'):
    return bounded_agent_activity({'run_id': 'r', 'agent': 'a', 'kind': kind, 'text': text})['text']


def test_words_after_a_long_media_payload_are_still_shown():
    text = 'data:image/png;base64,' + 'A' * 13_000 + ' The answer is 42.'
    assert _shown(text) == '[Media payload omitted] The answer is 42.'
    assert _shown(text, 'status') == '[Media payload omitted] The answer is 42.'
    long = 'Before. ' + 'data:image/png;base64,' + 'A' * 13_000 + ' ' + ('word ' * 4000).strip()
    shown = _shown(long)
    assert shown.startswith('Before. [Media payload omitted] word word') and shown.endswith('\n[Display truncated]')
    assert len(shown) == 12_000 + len('\n[Display truncated]')
    wrapped = 'data:image/png;base64,' + '\n'.join(['QUJD' * 19] * 5) + '\nThen the words.'
    assert _shown(wrapped) == '[Media payload omitted]\nThen the words.'


# 3. Patterns the gate found unredacted: base64url tokens (- and _), and MIME-wrapped base64 with no data: prefix.

def test_base64url_tokens_and_wrapped_base64_blocks_are_redacted():
    assert _shown('Token: ' + 'x-' * 200 + ' end') == 'Token: [Long unbroken value omitted] end'
    assert _shown('Token: ' + 'ab_' * 100 + ' end') == 'Token: [Long unbroken value omitted] end'
    line = 'QUJD' * 19                                            # one 76-character MIME line
    block = '\n'.join([line] * 20) + '\n'
    assert _shown('Attached:\n' + block + 'That was the file.') == 'Attached:\n[Encoded block omitted]That was the file.'
    crlf = '\r\n'.join([line] * 20) + '\r\n'
    assert _shown('Attached:\r\n' + crlf + 'Done.') == 'Attached:\r\n[Encoded block omitted]Done.'


def test_ordinary_prose_code_urls_and_short_runs_are_not_redacted():
    prose = ('The verifier read the report twice and found no contradiction; it lists every file it opened, '
             'with the line numbers, and says plainly which checks it could not run. ') * 60
    assert _shown(prose) == prose
    code = ('def bounded(value, limit):\n    """Whitelist the display contract."""\n'
            '    return value[:limit] + ("\\n[Display truncated]" if len(value) > limit else "")\n') * 5
    assert _shown(code) == code
    urls = ('See https://example.com/reports/2026/09/29/nested-dream-worker-output-and-facts?run=abc123&view=full '
            'and https://docs.example.org/' + 'section-' * 30 + 'end.html for the details.')
    assert _shown(urls) == urls
    sha512 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' * 2
    assert _shown('digest ' + sha512) == 'digest ' + sha512
    assert _shown('id ' + 'k' * 255 + ' ok') == 'id ' + 'k' * 255 + ' ok'
    line = 'QUJD' * 19
    three = '\n'.join([line] * 3) + '\n'
    assert _shown('Short block:\n' + three + 'kept.') == 'Short block:\n' + three + 'kept.'
    lines = '\n'.join(['a line of prose that is long enough to pass sixty characters in one line here'] * 6)
    assert _shown(lines) == lines


# Gate P3 round 1, blocking 1: the wrapped-block pattern was quadratic on an unbroken run with no newline (20,000
# characters 0.9 s, 100,000 22.6 s), and _text redacts the whole value on the event loop for every row; a worker's
# tool_result row carries up to 200,000 characters (`base64 -w0`, a JSON blob), so one row froze the session. The
# pattern starts a block only at the start of a line now. CPU time of this thread: a busy machine cannot fail it.

def _cpu_ms(value):
    from dream.agent_activity import _text
    started = time.thread_time()
    _text(value, 20_000)
    return (time.thread_time() - started) * 1000


def test_redacting_a_long_unbroken_run_takes_linear_time():
    from dream.agent_activity import _text
    assert _cpu_ms('A' * 20_000) < 50                         # the unanchored pattern: ~900 ms
    assert _cpu_ms('A' * 200_000) < 50                        # ... and ~90 s
    assert _text('A' * 200_000, 20_000) == '[Long unbroken value omitted]'
    assert _cpu_ms('x\n' * 25_000 + 'A' * 200_000) < 50       # 25,000 short lines, then the run
    assert _cpu_ms('\n'.join(['QUJD' * 14 + 'QUJ'] * 20_000)) < 50   # 20,000 lines of 59: never a block


def test_a_wrapped_block_starts_at_its_first_whole_line():
    # A first base64 line with other text before it is not part of the block (it stays shown); the block is the
    # whole lines after it, when four or more follow. Three whole lines after it are not a block: shown as sent.
    line = 'QUJD' * 19
    four = '\n'.join([line] * 4) + '\n'
    assert _shown('Attachment: ' + line + '\n' + four + 'end') == 'Attachment: ' + line + '\n[Encoded block omitted]end'
    three = '\n'.join([line] * 3) + '\n'
    assert _shown('Attachment: ' + line + '\n' + three + 'end') == 'Attachment: ' + line + '\n' + three + 'end'
