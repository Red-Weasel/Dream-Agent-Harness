"""Carried verifier context cannot become the user's request or protected budget."""
from dataclasses import replace

import pytest

from dream.core.backends.openai_compat import _compact_messages
from dream.core.profiles import PROFILES
from test_model_adaptation import backend
from test_schema_deferral import _FakeClient, _text_round


REQUEST = 'Stop the old page task. Explain what a hash is.'
FINDINGS = '[verifier findings for old.html — not from the user]\nFix the old layout.'
ERROR = '[verifier could not run on old.html — an error, not a page finding; not from the user]\nTransport unavailable.'


def ready(*, window=4096, policy='compact', report=None):
    b = backend(profile=replace(PROFILES['lean'], context_limit=window))
    b._context_overflow = policy
    b._pending_findings = report
    b._client = _FakeClient([_text_round('A hash is a fixed-size representation.')])
    return b


@pytest.mark.parametrize('report', [FINDINGS, ERROR], ids=['findings', 'transport_error'])
async def test_report_is_attributed_before_exact_user_and_excluded_from_filing(report):
    b = ready(report=report)
    [e async for e in b.ask(REQUEST)]
    messages = b._client.payloads[0]['messages']
    assert messages[-1] == {'role': 'user', 'content': REQUEST + '\n\n[id:m0001]'}
    assert messages[-2] == {'role': 'assistant', 'name': 'dream_verifier_report', 'content': report}
    assert b._pending_findings is None
    assert b._turn_text(REQUEST) == ('The user: ' + REQUEST + '\n\n[id:m0001]\n\n'
                                    'Dream: A hash is a fixed-size representation.')
    assert len(b._client.payloads) == 1
    [e async for e in b.ask('Give an example.')]
    assert sum(m.get('name') == 'dream_verifier_report' for m in b.messages) == 1
    assert 'old.html' not in b._turn_text('Give an example.')


@pytest.mark.parametrize('window', [2048, 4096])
async def test_large_optional_report_cannot_block_small_model_request(window):
    b = ready(window=window, report=FINDINGS + '\n' + 'X' * (window * 6))
    events = [e async for e in b.ask(REQUEST)]
    assert len(b._client.payloads) == 1
    messages = b._client.payloads[0]['messages']
    assert messages[-1]['content'] == REQUEST + '\n\n[id:m0001]'
    assert not any(m.get('name') == 'dream_verifier_report' for m in messages)
    assert any(e.kind == 'system' and 'compacted context:' in str(e.data) for e in events)
    assert not next(e.data for e in events if e.kind == 'result')['is_error']
    assert b.context_report['window'] == window


async def test_optional_report_has_no_residual_stub_cost_at_admission_boundary():
    prompt = 'Explain this text: ' + 'X' * 5590
    plain = ready(window=2048)
    carried = ready(window=2048, report=FINDINGS + 'X' * 9000)
    for b in (plain, carried):
        events = [e async for e in b.ask(prompt)]
        assert len(b._client.payloads) == 1
        assert not next(e.data for e in events if e.kind == 'result')['is_error']
    assert carried._client.payloads[0]['messages'] == plain._client.payloads[0]['messages']


def test_optional_removal_preserves_note_provenance_and_prior_history():
    note_calls = []
    messages = [
        {'role': 'system', 'content': 'System'},
        {'role': 'user', 'content': 'Build old page\n\n[id:m0001]'},
        {'role': 'assistant', 'content': 'Built old page'},
        {'role': 'assistant', 'name': 'dream_verifier_report', 'content': ERROR + 'X' * 9000},
        {'role': 'user', 'content': REQUEST + '\n\n[id:m0002]'},
    ]
    def save(what, body, turn):
        note_calls.append((what, body, turn))
        return 7, ''
    count = _compact_messages(messages, 100, on_elide=save)
    assert count == 1
    assert note_calls == [('verifier report', ERROR + 'X' * 9000, 'm0001')]
    assert messages[1]['content'] == 'Build old page\n\n[id:m0001]'
    assert messages[2]['content'] == 'Built old page'
    assert messages[-1]['content'] == REQUEST + '\n\n[id:m0002]'
    assert not any(m.get('name') == 'dream_verifier_report' for m in messages)


@pytest.mark.parametrize('report,prompt,policy', [
    (FINDINGS + 'X' * 9000, REQUEST, 'error'),
    (FINDINGS, 'CURRENT ' + 'X' * 12000, 'compact'),
], ids=['explicit_error_policy', 'oversized_current_request'])
async def test_refusal_preserves_explicit_error_policy_and_current_request(report, prompt, policy):
    b = ready(window=2048, policy=policy, report=report)
    events = [e async for e in b.ask(prompt)]
    assert b._client.payloads == []
    assert next(e.data for e in events if e.kind == 'result')['subtype'] == 'context_overflow'
    assert b.messages[-1]['content'] == prompt + '\n\n[id:m0001]'
    if policy == 'error':
        assert b.messages[-2]['content'] == report
    assert b._pending_findings is None


def test_verifier_name_does_not_authorize_removing_a_tool_call_pair():
    messages = [
        {'role': 'system', 'content': 'System'},
        {'role': 'user', 'content': 'Prior request'},
        {'role': 'assistant', 'name': 'dream_verifier_report', 'content': None,
         'tool_calls': [{'id': 'c1', 'type': 'function',
                         'function': {'name': 'read_file', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'X' * 2000},
        {'role': 'user', 'content': REQUEST},
    ]
    _compact_messages(messages, 100)
    assert any(tc['id'] == 'c1' for m in messages for tc in m.get('tool_calls', []))
    assert any(m.get('tool_call_id') == 'c1' for m in messages)
    assert messages[-1]['content'] == REQUEST


def test_required_prior_user_survives_compaction_and_enclosing_snip():
    b = ready()
    prior = {'role': 'user', 'name': 'dream_handoff_user', 'content': 'EXACT PRIOR ' + 'X' * 12000}
    b.messages.extend([{'role': 'user', 'content': 'old\n\n[id:m0001]'}, prior.copy(),
                       {'role': 'assistant', 'content': 'old answer'},
                       {'role': 'user', 'content': REQUEST + '\n\n[id:m0002]'}])
    b._snips = [('m0001', 'm0001', 'prior exchange')]
    assert b._execute_snips() == 0
    _compact_messages(b.messages, 100)
    assert prior in b.messages
    assert b.messages[-1]['content'] == REQUEST + '\n\n[id:m0002]'


@pytest.mark.parametrize('size', [40, 30000])
async def test_salvage_cannot_elide_required_prior_user(size):
    from test_salvage import _FakeClient as RecoveryClient, SALVAGE
    b = ready(window=4096)
    prior = {'role': 'user', 'name': 'dream_handoff_user', 'content': 'EXACT_PRIOR ' + 'X' * size}
    b.messages.extend([prior.copy(), {'role': 'user', 'content': REQUEST}])
    b._client = RecoveryClient(post_scripts=[SALVAGE])
    answer = await b._salvage_reply('fixture stopped')
    assert prior in b.messages
    if size == 40:
        assert answer and prior in b._client.posted[0]['messages']
    else:
        assert answer == '' and b._client.posted == []


async def test_council_projection_does_not_compact_existing_error_policy_verifier():
    from dream.core.council_context import Record, Transfer
    b = ready(window=2048, policy='error', report=FINDINGS + 'X' * 12000)
    record = Record('source', 3, 'council', '{"question":"q","advisors":[{"advisor":"codex","answer":"advice"}]}', 80)
    b.prepare_council_context(Transfer(consultations=(record,)))
    events = [e async for e in b.ask(REQUEST)]
    assert b._client.payloads == []
    assert b.messages[-2]['content'] == FINDINGS + 'X' * 12000
    assert b.messages[-1]['content'] == REQUEST + '\n\n[id:m0001]'
    assert any('codex' in str(e.data) and 'omitted' in str(e.data) for e in events if e.kind == 'system')
