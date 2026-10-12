"""Read-only context observation of mock Responses traffic, not live qualification."""
from copy import deepcopy
import asyncio
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from dream.core import context_inspection as ci
from dream.core.frontier_help import FrontierHelp
from dream.gui.lucid_routes import context_view
from dream.tools import context as tool_context
from dream.tools.context import bind_context
from test_frontier_help import inputs, reserve_help, restart
from test_managed_loop import worker
from test_lucid_control import lucid, page_server
from test_responses_help import Bytes, KEY, Wire, configured, attempt_prepare
from test_responses_phase_workflow import workflow
from test_task_checkpoints import managed


@pytest.fixture(autouse=True)
def observer(monkeypatch):
    monkeypatch.setattr(ci, 'JOURNAL', ci.Journal())
    monkeypatch.delenv('DREAM_CONTEXT_INSPECTION', raising=False)
    monkeypatch.delenv(ci.ENV_TOKENIZER, raising=False)
    monkeypatch.delenv(ci.ENV_TOKENIZER_MODEL, raising=False)
    monkeypatch.delenv('DREAM_CONTEXT_COUNTER', raising=False)


def api(name):
    value = getattr(ci, name, None)
    assert callable(value), 'Dedicated Responses inspection API is absent: ' + name
    return value


def session(name):
    return SimpleNamespace(session_id=name)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def records(name):
    return [ci.JOURNAL.get(name, row['request_id']) for row in reversed(ci.JOURNAL.list(name))
            if row.get('protocol') == 'openai_responses']


def test_dedicated_protocol_api_captures_actual_input_not_fake_chat():
    capture = api('capture_responses')
    payload = {'model': 'public-model', 'input': 'An approved generic input string.', 'tools': [],
               'tool_choice': 'none', 'parallel_tool_calls': False, 'truncation': 'disabled'}
    before = deepcopy(payload)
    handle = capture(payload, {'recipient': 'advisor'}, session_id='api-fixture',
        private_id='private-fixture', purpose='input_tokens')
    assert isinstance(handle, ci.Attempt) and payload == before
    row, = records('api-fixture')
    assert row['protocol'] == 'openai_responses' and row['recipient'] == 'advisor'
    assert row['request']['messages'] is None
    assert row['request']['input_sha256'] == row['request']['body_sha256'] == digest(payload)
    assert row['components'][0]['kind'] == 'responses_input'
    assert row['components'][0]['source'] == 'host_approved_packet'
    assert all(part['tokens'] is None and part['tokens_reason'] == 'provider_total_only' for part in row['components'])


@pytest.mark.parametrize('bad', [True, -1, 1.5, '37', None])
def test_native_usage_does_not_coerce_missing_or_malformed_counts(bad):
    read = api('read_responses_usage')
    usage = read({'input_tokens': bad, 'output_tokens': 8,
                  'input_tokens_details': {'cached_tokens': bad}})
    assert usage['input_tokens'] is None and usage['cached_tokens'] is None
    assert usage['output_tokens'] == 8


async def test_actual_count_and_inference_are_separate_linked_observations(managed, tmp_path):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    transport = configured(c, wire); before = deepcopy(c._state)
    with bind_context(session('responses-A')):
        await transport.prepare(c, route, packet)
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    rows = records('responses-A')
    assert len(rows) == 2, 'Each actual Responses POST must have its own protocol-specific record.'
    counted, inferred = rows
    assert [row['purpose'] for row in rows] == ['input_tokens', 'inference']
    assert counted['request_id'] != inferred['request_id']
    effective = wire.calls[0][1]
    expected = digest(effective)
    for row, (_, body) in zip(rows, wire.calls):
        assert row['recipient'] == 'advisor' and row['model'] == route.model_id
        assert row['request']['input_sha256'] == expected
        assert row['request']['body_sha256'] == digest(body)
        assert row['request']['messages'] is None
        assert row['params']['tool_choice'] == 'none'
        assert row['params']['parallel_tool_calls'] is False and row['params']['truncation'] == 'disabled'
        assert all(part['tokens'] is None for part in row['components'])
        assert row['components'][0]['sha256'] == hashlib.sha256(packet.payload()).hexdigest()
    assert counted['provider_count']['observed'] is True and counted['provider_count']['input_tokens'] == 37
    assert counted['provider_count']['method'] == 'openai_responses_input_tokens'
    assert counted['usage']['observed'] is False
    assert inferred['provider_count']['observed'] is False
    assert inferred['usage']['observed'] is True
    assert inferred['usage']['input_tokens'] == 37 and inferred['usage']['output_tokens'] == 8
    assert inferred['usage']['cached_tokens'] is None
    assert c._state == before and len(wire.calls) == 2
    encoded = json.dumps(rows)
    assert KEY not in encoded and str(tmp_path) not in encoded
    assert wire.body['output'][0]['content'][0]['text'] not in encoded


async def test_count_reuse_after_restart_does_not_invent_an_exchange(managed):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    with bind_context(session('restart-fixture')):
        await configured(c, wire).prepare(c, route, packet)
    first, = records('restart-fixture')
    resumed = restart(c)
    try:
        wire.c = resumed; transport = configured(resumed, wire)
        with bind_context(session('restart-fixture')):
            await transport.prepare(resumed, route, packet)
            assert [row['request_id'] for row in records('restart-fixture')] == [first['request_id']]
            assert (await FrontierHelp(resumed).dispatch(route, packet, transport))['status'] == 'returned'
        assert len(records('restart-fixture')) == len(wire.calls) == 2
    finally: resumed.journal.close()


async def test_only_bound_session_can_read_its_exchange(managed, monkeypatch):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    monkeypatch.setattr(tool_context, '_CTX', session('global-decoy'))
    transport = configured(c, wire)
    with bind_context(session('session-A')): await transport.prepare(c, route, packet)
    with bind_context(session('session-B')):
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    counted, = records('session-A'); inferred, = records('session-B')
    assert ci.JOURNAL.get('session-B', counted['request_id']) is None
    assert ci.JOURNAL.get('session-A', inferred['request_id']) is None
    assert records('global-decoy') == []
    assert context_view('session-B', counted['request_id']) is None
    summary = context_view('session-A')
    assert summary['records'][0]['protocol'] == 'openai_responses'
    assert packet.question not in json.dumps(summary) and packet.context not in json.dumps(summary)


async def test_unbound_transport_never_uses_global_context_fallback(managed, monkeypatch):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    monkeypatch.setattr(tool_context, '_CTX', session('global-decoy'))
    transport = configured(c, wire)
    with bind_context(None):
        await transport.prepare(c, route, packet)
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    assert records('global-decoy') == [] and ci.JOURNAL.list(None) == []
    assert len(wire.calls) == 2


async def test_preview_is_redacted_approved_input_not_capsule_or_advice(managed, tmp_path):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    marker = 'SYNTHETIC_PRIVATE_SECRET_VALUE'
    packet = replace(packet, context='A generic note with secret=' + marker + ' and bounded context.'
                     + ' Generic bounded note.' * 300)
    wire.body['output'][0]['content'][0]['text'] = 'ADVISORY_OUTPUT_MUST_NOT_ENTER_CONTEXT_PREVIEW'
    with bind_context(session('redaction-fixture')):
        transport = configured(c, wire); await transport.prepare(c, route, packet)
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    rows = records('redaction-fixture'); assert len(rows) == 2
    for row in rows:
        part, = row['components']
        assert part['source'] == 'host_approved_packet'
        assert part['sha256'] == hashlib.sha256(packet.payload()).hexdigest()
        assert part['bytes'] == len(packet.payload())
        assert part['preview_truncated'] is True
        assert len(part['preview'].encode()) <= ci.MAX_PREVIEW_BYTES
        assert row['stored_bytes'] <= ci.JOURNAL.max_record_bytes
    encoded = json.dumps(rows)
    assert marker not in encoded and KEY not in encoded and str(tmp_path) not in encoded
    assert 'ADVISORY_OUTPUT_MUST_NOT_ENTER_CONTEXT_PREVIEW' not in encoded


@pytest.mark.parametrize('failure', ['capture', 'noop', 'update', 'disabled'])
async def test_observation_failure_cannot_change_help_authority_or_retry(managed, monkeypatch, failure):
    api('capture_responses')
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    transport = configured(c, wire); before = deepcopy(c._state)
    def fail(*args, **kwargs): raise RuntimeError('synthetic observational failure')
    if failure == 'capture': monkeypatch.setattr(ci, 'capture_responses', fail)
    elif failure == 'noop': monkeypatch.setattr(ci, 'capture_responses', lambda *a, **kw: ci.NO_ATTEMPT)
    elif failure == 'update': monkeypatch.setattr(ci.JOURNAL, 'update', fail)
    else: monkeypatch.setenv('DREAM_CONTEXT_INSPECTION', '0')
    with bind_context(session('observer-failure')):
        await transport.prepare(c, route, packet)
        result = await FrontierHelp(c).dispatch(route, packet, transport)
    assert result['status'] == 'returned' and len(wire.calls) == 2
    assert c._state == before and not c.actions_admitted
    if failure == 'disabled': assert ci.JOURNAL.list('observer-failure') == []


@pytest.mark.parametrize('stage', ['count', 'inference'])
async def test_cleanup_failure_cannot_show_a_settled_count_or_inference_usage(managed, stage):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    transport = configured(c, wire)
    with bind_context(session('cleanup-fixture')):
        if stage == 'count':
            wire.count_stream = Bytes(json.dumps(wire.count).encode(), close_error=True)
            await attempt_prepare(transport, c, route, packet)
        else:
            await transport.prepare(c, route, packet)
            wire.response_stream = Bytes(json.dumps(wire.body).encode(), close_error=True)
            assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'unknown'
    rows = records('cleanup-fixture'); assert len(rows) == (1 if stage == 'count' else 2)
    failed = rows[-1]
    assert failed['status'] in {'failed', 'interrupted'}
    assert failed['provider_count']['observed'] is False and failed['usage']['observed'] is False
    assert all(s.closed for s in wire.streams)


async def test_journal_eviction_and_disabled_inspection_cannot_trigger_requests(managed, monkeypatch):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    monkeypatch.setattr(ci, 'JOURNAL', ci.Journal(max_records=1))
    transport = configured(c, wire)
    with bind_context(session('bounded-fixture')):
        await transport.prepare(c, route, packet)
        first, = records('bounded-fixture')
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    retained, = records('bounded-fixture')
    assert retained['purpose'] == 'inference'
    assert ci.JOURNAL.get('bounded-fixture', first['request_id']) is None
    assert ci.JOURNAL.stored_bytes('session', 'bounded-fixture') <= ci.JOURNAL.max_session_bytes
    monkeypatch.setenv('DREAM_CONTEXT_INSPECTION', '0')
    with bind_context(session('bounded-fixture')):
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    assert len(wire.calls) == 2 and len(records('bounded-fixture')) == 1


async def test_actual_runner_binds_advisor_records_to_its_engine_session(worker, tmp_path):
    runner, seen, inference = workflow(worker, tmp_path)
    result = await runner.run()
    assert result.status == 'done'
    rows = records('responses-workflow-fixture')
    assert len(rows) == 2, 'The host assistance scope must bind its own engine session.'
    assert [row['purpose'] for row in rows] == ['input_tokens', 'inference']
    assert all(row['recipient'] == 'advisor' for row in rows)
    assert len(seen.calls) == 2 and len(seen.checks) == 1


def test_responses_capture_does_not_start_a_local_component_token_counter(monkeypatch):
    capture = api('capture_responses')
    def forbidden(*args, **kwargs): raise AssertionError('No component tokenizer is needed for provider-total-only input.')
    monkeypatch.setattr(ci, '_counter_for', forbidden)
    payload = {'model': 'public-model', 'input': 'Generic approved input.', 'tools': [],
               'tool_choice': 'none', 'parallel_tool_calls': False, 'truncation': 'disabled'}
    capture(payload, {}, session_id='no-counter', private_id='private', purpose='input_tokens')
    row, = records('no-counter')
    assert row['components'][0]['tokens'] is None


def test_native_repeated_usage_replaces_observation_and_never_apportions_total():
    capture = api('capture_responses')
    payload = {'model': 'public-model', 'input': 'Generic approved input.', 'tools': [],
               'tool_choice': 'none', 'parallel_tool_calls': False, 'truncation': 'disabled',
               'max_output_tokens': 32, 'store': False, 'stream': False}
    handle = capture(payload, {}, session_id='usage-repeat', private_id='private', purpose='inference')
    handle.responses_body({'usage': {'input_tokens': 37, 'output_tokens': 8,
                                    'input_tokens_details': {'cached_tokens': 4}}})
    handle.responses_body({'usage': {'input_tokens': 37, 'output_tokens': True,
                                    'input_tokens_details': {'cached_tokens': -1}}})
    row, = records('usage-repeat')
    assert row['usage']['observations'] == 2 and row['usage']['input_tokens'] == 37
    assert row['usage']['output_tokens'] is None and row['usage']['cached_tokens'] is None
    assert row['provider_count']['observed'] is False and row['components'][0]['tokens'] is None


@pytest.mark.parametrize('stage', ['count', 'inference'])
@pytest.mark.parametrize('fault', ['http', 'stop', 'late_cleanup'])
async def test_failed_exchange_is_observed_without_success_or_usage(managed, stage, fault):
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    if fault == 'late_cleanup': route = replace(route, timeout_seconds=0.02)
    transport = configured(c, wire)
    class Faulty(Bytes):
        async def aclose(self):
            self.closed = True
            if fault == 'stop': c.stop()
            elif fault == 'late_cleanup':
                try: await asyncio.sleep(0.06)
                except asyncio.CancelledError: pass
    with bind_context(session('failed-observation')):
        if stage == 'inference': await transport.prepare(c, route, packet)
        stream = Faulty(json.dumps(wire.count if stage == 'count' else wire.body).encode())
        if stage == 'count':
            wire.count_stream = stream
            if fault == 'http': wire.count_status = 429
            await attempt_prepare(transport, c, route, packet)
        else:
            wire.response_stream = stream
            if fault == 'http': wire.response_status = 500
            assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'unknown'
    rows = records('failed-observation'); assert len(rows) == (1 if stage == 'count' else 2)
    row = rows[-1]
    assert row['status'] in {'failed', 'interrupted', 'refused'}
    assert row['provider_count']['observed'] is False and row['usage']['observed'] is False
    if fault == 'http': assert row['http_status'] == (429 if stage == 'count' else 500)
    assert stream.closed and not c.actions_admitted


@pytest.mark.parametrize('width', [1400, 390])
async def test_rendered_lucid_distinguishes_responses_counts_controls_and_text_only_input(managed, page_server, width):
    from playwright.async_api import async_playwright, expect
    from urllib.parse import urlsplit
    c, _ = managed; reserve_help(c); route, packet = inputs(); wire = Wire(c)
    packet = replace(packet, context='Generic approved example: <img src=x onerror="window.__responses_xss=1">')
    with bind_context(session('render-fixture')):
        transport = configured(c, wire); await transport.prepare(c, route, packet)
        assert (await FrontierHelp(c).dispatch(route, packet, transport))['status'] == 'returned'
    counted, inferred = records('render-fixture')
    srv, _, _ = page_server
    srv._session_source['session_id'] = 'render-fixture'
    url = await srv.start(); origin = urlsplit(url)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(args=['--disable-gpu', '--disable-background-networking'])
            page = await browser.new_page(viewport={'width': width, 'height': 1000})
            page.set_default_timeout(5000); errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            async def local_only(request_route):
                target = urlsplit(request_route.request.url)
                if (target.scheme, target.netloc) == (origin.scheme, origin.netloc): await request_route.continue_()
                else: await request_route.abort()
            await page.route('**/*', local_only)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-memory').click()
            panel = page.locator('#dream-memory-page .lucid-context')
            await panel.locator('.lucid-context-refresh').click()
            await expect(panel.locator('select option')).to_have_count(2)
            await expect(panel).to_contain_text('Protocol: OpenAI Responses · advisory inference')
            await expect(panel).to_contain_text('Inference input tokens reported: 37')
            await expect(panel).to_contain_text('Inference output tokens reported: 8')
            await expect(panel).to_contain_text('Whole input counted by the provider: unknown')
            await panel.locator('select').select_option(counted['request_id'])
            await expect(panel).to_contain_text('Protocol: OpenAI Responses · input count preflight')
            await expect(panel).to_contain_text('Whole input counted by the provider: 37')
            await expect(panel).to_contain_text('Inference input tokens reported: unknown')
            await expect(panel).to_contain_text(counted['request']['input_sha256'])
            await expect(panel).to_contain_text(counted['request']['body_sha256'])
            for control in ('"tool_choice":"none"', '"parallel_tool_calls":false', '"truncation":"disabled"'):
                await expect(panel).to_contain_text(control)
            await panel.locator('details summary').click()
            await expect(panel).to_contain_text('Source: host_approved_packet')
            await expect(panel).to_contain_text('Component tokens: unknown')
            await expect(panel).to_contain_text('<img src=x onerror=')
            await expect(panel.locator('img, script, iframe')).to_have_count(0)
            assert await page.evaluate('window.__responses_xss === undefined')
            text = await panel.inner_text()
            assert 'chat template:' not in text and 'tokenizer_text' not in text
            assert KEY not in text and wire.body['output'][0]['content'][0]['text'] not in text
            assert not await page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth')
            await page.screenshot(path='/tmp/dream-responses-context-render-' + str(width) + '.png', full_page=True)
            assert errors == []
            await browser.close()
    finally: await srv.stop()
