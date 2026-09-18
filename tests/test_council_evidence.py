"""Independent, bounded Council calls and inspectable fictional legal fixtures."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import moe
from dream.core.council_evidence import assess_legal, prepare_sources
from dream.core.evaluator import ReviewSettings, SDKReviewBackend, ScopedReader, collect_review
from dream.core.providers import get_provider
from dream.tools import context, moe_tools


async def test_council_concurrency_bound_and_independent_inputs(monkeypatch):
    active = peak = 0
    seen = []
    async def advisor(key, question, background='', **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        seen.append((key, question, background))
        try:
            await asyncio.sleep(0.01)
            return f'{key} disagrees independently'
        finally:
            active -= 1
    monkeypatch.setattr(moe, 'consult_advisor', advisor)
    results = await moe.council(['openai', 'xai', 'anthropic', 'machx', 'codex'], 'question', 'same evidence', max_concurrency=2)
    assert peak == 2 and active == 0
    assert [r['advisor'] for r in results] == ['openai', 'xai', 'anthropic', 'machx', 'codex']
    assert {(q, bg) for _, q, bg in seen} == {('question', 'same evidence')}
    assert all(not r['consensus_is_proof'] and 'disagrees' in r['answer'] for r in results)


async def test_timeout_is_attributed_and_other_advisors_finish(monkeypatch):
    cancelled = asyncio.Event()
    async def hung(provider, prompt, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def ready(provider, prompt, **kwargs):
        return 'independent dissent'
    monkeypatch.setattr(moe, '_consult_openai', hung)
    monkeypatch.setattr(moe, '_consult_anthropic', ready)
    results = await moe.council(['machx', 'anthropic'], 'q', timeout=0.02)
    assert 'TimeoutError' in results[0]['answer'] and results[0]['advisor'] == 'machx'
    assert results[1]['answer'] == 'independent dissent'
    assert cancelled.is_set()


async def test_council_cancellation_cancels_all_active_advisors(monkeypatch):
    started, stopped = 0, 0
    gate = asyncio.Event()
    async def hold(key, *args, **kwargs):
        nonlocal started, stopped
        started += 1
        if started == 2:
            gate.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped += 1
    monkeypatch.setattr(moe, 'consult_advisor', hold)
    task = asyncio.create_task(moe.council(['openai', 'xai', 'machx'], 'q', max_concurrency=2))
    await gate.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped == 2 and started == 2


async def test_explicit_advisor_model_reaches_selected_provider(monkeypatch):
    seen = []
    async def invoke(provider, prompt, *, cwd=None, model=None):
        seen.append((provider.key, model))
        return 'answer'
    monkeypatch.setattr(moe, '_consult_openai', invoke)
    await moe.council(['machx', 'xai'], 'q', models={'machx': 'local-fixture', 'xai': 'remote-fixture'})
    assert seen == [('machx', 'local-fixture'), ('xai', 'remote-fixture')]


def legal_answer(quote='An application must include the signed form.', source_id='rule'):
    return json.dumps({'conclusion': 'Fictional example only', 'dissent': ['The effective date is not independently verified.'],
                       'claims': [{'claim': 'The fictional rule requires a signature.', 'jurisdiction': 'Fixture jurisdiction', 'as_of': '2026-09-04',
                                   'citations': [{'source_id': source_id, 'quote': quote, 'pinpoint': 'section 1'}]}]})


@pytest.fixture
def legal_source(tmp_path):
    path = tmp_path / 'fictional-rule.txt'
    path.write_text('Fictional rule, section 1.\nAn application must include the signed form.\n')
    return [{'id': 'rule', 'path': path.name, 'url': 'https://source.invalid/fictional-rule'}]


async def test_legal_claims_require_inspected_sources_and_retain_dissent(tmp_path, legal_source, monkeypatch):
    async def answer(key, question, context='', **kwargs):
        assert 'never' in context.lower() and 'agreement' in context.lower()
        assert 'An application must include the signed form.' in context
        return legal_answer()
    monkeypatch.setattr(moe, 'consult_advisor', answer)
    result = (await moe.council(['machx'], 'Fictional legal question', cwd=str(tmp_path), legal=True, sources=legal_source))[0]
    check = result['legal_review']
    assert check['status'] == 'source_backed'
    assert check['legal_correctness'] == 'unverified' and not check['consensus_is_proof']
    assert check['dissent'] == ['The effective date is not independently verified.']
    citation = check['claims'][0]['citations'][0]
    assert len(citation['source_sha256']) == 64 and citation['path'] == 'fictional-rule.txt'
    assert result['advisor'] == 'machx' and result['answer'] == legal_answer()


@pytest.mark.parametrize('answer', [legal_answer(), 'Everyone agrees, so it is settled law.', '{bad json'])
def test_no_inspectable_sources_is_always_unverified(answer):
    result = assess_legal(answer, {})
    assert result['status'] == 'unverified' and not result['consensus_is_proof']


def test_fabricated_quotes_and_unknown_source_ids_do_not_verify(tmp_path, legal_source):
    sources, _ = prepare_sources(legal_source, str(tmp_path))
    for answer in (legal_answer(quote='A signature is optional.'), legal_answer(source_id='invented')):
        check = assess_legal(answer, sources)
        assert check['status'] == 'unverified'
        assert not check['claims'][0]['citations'][0]['quote_matches']


async def test_legal_source_path_escape_is_rejected_before_advisor(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (tmp_path / 'private.txt').write_text('unrelated private information')
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(args)
        return 'answer'
    monkeypatch.setattr(moe, 'consult_advisor', forbidden)
    with pytest.raises(ValueError, match='outside'):
        await moe.council(['machx'], 'q', legal=True, sources=[{'id': 'bad', 'path': '../private.txt'}], cwd=str(workspace))
    assert not calls


async def test_legal_tool_setting_cannot_be_disabled_by_model(tmp_path, legal_source, monkeypatch):
    cfg = moe.MoeConfig('machx', ['machx'], legal_review=True)
    monkeypatch.setattr(context, '_CTX', SimpleNamespace(moe_config=cfg, workspace=tmp_path))
    async def answer(provider, prompt, **kwargs):
        return legal_answer()
    monkeypatch.setattr(moe, '_consult_openai', answer)
    result = await moe_tools.consult.handler({'advisor': 'machx', 'question': 'q', 'legal': False, 'sources': legal_source})
    text = result['content'][0]['text']
    assert 'source_backed' in text and 'legal correctness unverified' in text
    assert 'effective date' in text


async def test_sdk_review_has_only_bound_tools_and_denies_builtins(tmp_path, monkeypatch):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    sentinel = object()
    monkeypatch.setattr(context, '_CTX', sentinel)
    seen = []
    async def query(**kwargs):
        opts = kwargs['options']
        seen.append(opts)
        assert opts.tools == [] and opts.setting_sources == []
        assert json.loads(opts.settings)['disableAllHooks'] is True
        assert set(opts.allowed_tools) == {'mcp__review__read_file', 'mcp__review__list_files'}
        denied = await opts.can_use_tool('Bash', {'command': 'touch forbidden'}, None)
        assert denied.behavior == 'deny'
        allowed = await opts.can_use_tool('mcp__review__read_file', {'path': 'fixture'}, None)
        assert allowed.behavior == 'allow'
        yield AssistantMessage(content=[TextBlock(text='VERDICT: PASS\nGAPS: none')], model='fixture-sdk')
        yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id='fixture-sdk', terminal_reason='completed')
    backend = SDKReviewBackend(ReviewSettings(get_provider('anthropic'), 'fixture-sdk'), ScopedReader(tmp_path).tools(), 'review', tmp_path, query)
    assert 'VERDICT: PASS' in await collect_review(backend, 'q', 1)
    assert seen[0].model == 'fixture-sdk'
    assert context._CTX is sentinel


async def test_cli_advisor_reports_missing_signin_without_cross_provider_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('GROK_HOME', str(tmp_path))
    monkeypatch.setattr('dream.core.cli_review.shutil.which', lambda _: '/fixture/grok')
    answer = await moe.consult_advisor('grok', 'q')
    assert 'unavailable' in answer and 'sign-in' in answer


def test_extended_council_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(moe, 'CONFIG_PATH', tmp_path / 'moe.json')
    cfg = moe.MoeConfig('machx', ['machx'], max_concurrency=1, timeout_seconds=42, legal_review=True, advisor_models={'machx': 'fixture'})
    moe.save_config(cfg)
    assert moe.load_config() == cfg


async def test_http_council_profile_and_reported_usage_are_bound_to_parent(tmp_path, monkeypatch):
    from dream.telemetry.runtime import RunMeter
    from dream.core.profiles import resolve_profile
    from dream.core.backends.base import Event
    provider = get_provider('machx')
    meter = RunMeter('fixture', 1, resolve_profile(provider))
    seen = []
    class Backend:
        def __init__(self, **kwargs):
            seen.append(kwargs)
        async def connect(self):
            pass
        async def disconnect(self):
            pass
        async def ask(self, prompt):
            self.runtime_meter.check()
            self.runtime_meter.usage({'prompt_tokens': 7, 'completion_tokens': 3})
            yield Event('assistant_done', 'Independent fixture assessment')
    monkeypatch.setattr(moe, 'OpenAICompatBackend', Backend)
    with context.bind_context(SimpleNamespace(runtime_meter=meter)):
        result = await moe._consult_openai(provider, 'fixture', model='explicit-local')
    assert result == 'Independent fixture assessment'
    assert seen[0]['profile'] is not None and seen[0]['model'] == 'explicit-local'
    assert seen[0]['tools'] == []
    assert meter.prompt_tokens == 7 and meter.output_tokens == 3
    assert meter.phases['council:machx:lead']['requests'] == 1


async def test_anthropic_council_disables_hooks_explicitly(monkeypatch):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    async def query(**kwargs):
        options = kwargs['options']
        assert json.loads(options.settings)['disableAllHooks'] is True
        assert options.tools == [] and options.mcp_servers == {} and options.setting_sources == []
        yield AssistantMessage(content=[TextBlock(text='Independent fixture assessment')], model='fixture-sdk')
        yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id='fixture-sdk')
    monkeypatch.setattr('claude_agent_sdk.query', query)
    assert await moe._consult_anthropic(get_provider('anthropic'), 'fixture') == 'Independent fixture assessment'
