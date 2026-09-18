"""Offline production request qualification; real backends, synthetic transports."""
import json
from dataclasses import replace

import httpx
import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import PROFILES
from dream.core.providers import Provider


@pytest.fixture
def http_backend(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_MACHX_SESSION_OPTIONS', raising=False)
    def make(key='openai', levels=None):
        provider = Provider(key, 'Synthetic qualification', 'openai',
                            base_url='http://fixture.invalid/v1', default_api_key='fixture')
        metadata = {'reasoning_levels': levels} if levels is not None else None
        backend = OpenAICompatBackend(provider=provider, model='fixture-first', system_prompt='Fixture',
            tools=[], permission_cb=None, provider_metadata=metadata,
            profile=replace(PROFILES['balanced'], auto_filer=False))
        payloads = []
        responses = []
        def respond(request):
            assert request.url.host == 'fixture.invalid'
            assert request.url.path == '/v1/chat/completions'
            payloads.append(json.loads(request.content))
            if responses:
                return responses.pop(0)
            event = {'choices': [{'delta': {'content': 'fixture response'}, 'finish_reason': 'stop'}]}
            return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n',
                                  headers={'content-type': 'text/event-stream'})
        backend._client = httpx.AsyncClient(base_url=provider.base_url, transport=httpx.MockTransport(respond))
        return backend, payloads, responses
    yield make
    # No connection/model launch takes place; each test explicitly closes its client.


@pytest.mark.parametrize('key', ['openai', 'machx'])
@pytest.mark.parametrize('level', ['low', 'high', 'custom-native'])
async def test_reported_effort_reaches_actual_request_exactly(http_backend, key, level):
    backend, payloads, _ = http_backend(key, ['low', 'high', 'custom-native'])
    try:
        backend.set_effort(level)
        events = [event async for event in backend.ask('Request fixture response')]
        assert not next(event.data for event in events if event.kind == 'result')['is_error']
        assert payloads[0]['reasoning_effort'] == level
    finally:
        await backend._client.aclose()


@pytest.mark.parametrize('key', ['openai', 'machx'])
@pytest.mark.parametrize('levels', [[], ['low', 'high']])
async def test_known_unsupported_effort_refuses_before_state_mutation(http_backend, key, levels):
    backend, payloads, _ = http_backend(key, levels)
    try:
        if levels:
            backend.set_effort('high')
        previous = backend._effort
        with pytest.raises(ValueError, match='support'):
            backend.set_effort('max')
        assert backend._effort == previous
        assert payloads == []
    finally:
        await backend._client.aclose()


@pytest.mark.parametrize('key', ['openai', 'machx'])
@pytest.mark.parametrize('invalid', ['invented', '', 7, False])
async def test_unknown_ladder_does_not_accept_unrepresentable_effort(http_backend, key, invalid):
    backend, payloads, _ = http_backend(key)
    try:
        backend.set_effort('high')
        with pytest.raises(ValueError):
            backend.set_effort(invalid)
        assert backend._effort == 'high'
        assert payloads == []
        assert not backend.capability_status()['reasoning_levels']['known']
    finally:
        await backend._client.aclose()


async def test_endpoint_rejection_is_failure_and_explicit_next_turn_recovers(http_backend):
    backend, payloads, responses = http_backend()
    responses.append(httpx.Response(400, json={'error': {'message': 'fixture unsupported request'}}))
    try:
        failed = [event async for event in backend.ask('First fixture request')]
        assert next(event.data for event in failed if event.kind == 'result')['is_error']
        assert len(payloads) == 1
        recovered = [event async for event in backend.ask('Explicit recovery request')]
        assert not next(event.data for event in recovered if event.kind == 'result')['is_error']
        assert len(payloads) == 2
        assert not backend.capability_status()['vision']['known']
        assert not backend.capability_status()['image_readiness']['known']
    finally:
        await backend._client.aclose()


async def test_model_switch_drops_reported_ladder_and_image_readiness(http_backend):
    backend, payloads, _ = http_backend('machx', ['low', 'high'])
    try:
        backend._image_rejection_model = backend.model
        backend._schema_cache = ('old model schemas',)
        await backend.set_model('fixture-second')
        assert not backend.capability_status()['reasoning_levels']['known']
        assert not backend.capability_status()['image_readiness']['known']
        assert backend._schema_cache is None
        [event async for event in backend.ask('Request from second model')]
        assert payloads[0]['model'] == 'fixture-second'
        assert 'reasoning_effort' not in payloads[0]
    finally:
        await backend._client.aclose()


@pytest.fixture
def qualification_script():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('qualification_script',
        Path(__file__).resolve().parents[1] / 'scripts' / 'qualify_production.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('xml,code,expected', [
    ('<testsuites><testsuite><testcase name="ok"/></testsuite></testsuites>', 0, 'verified'),
    ('<testsuites><testsuite><testcase name="bad"><failure>private failure text</failure></testcase></testsuite></testsuites>', 0, 'failed'),
    ('<testsuites><testsuite><testcase name="bad"><error/></testcase></testsuite></testsuites>', 1, 'failed'),
    ('<testsuites><testsuite><testcase name="skip"><skipped/></testcase></testsuite></testsuites>', 0, 'not_run'),
    ('<testsuites/>', 0, 'failed'),
    ('<testsuites><testsuite><testcase name="ok"/></testsuite></testsuites>', 1, 'failed'),
    ('<unfinished', 0, 'failed'),
    ('<garbage><testcase name="ok"/></garbage>', 0, 'failed'),
    ('<testsuite tests="0"><testcase name="ok"/></testsuite>', 0, 'failed'),
    ('<testsuite tests="1" errors="1"><testcase name="ok"/></testsuite>', 0, 'failed'),
    ('<testsuite><testcase name="ok"/><error message="collection failure"/></testsuite>', 0, 'failed'),
    ('<testsuite tests="-1"><testcase name="ok"/></testsuite>', 0, 'failed'),
    ('<testsuite tests="unknown"><testcase name="ok"/></testsuite>', 0, 'failed'),
    ('<testsuites tests="2"><testsuite tests="1"><testcase name="ok"/></testsuite></testsuites>', 0, 'failed'),
    ('<testsuites tests="0"><testsuite><testcase name="ok"/></testsuite></testsuites>', 0, 'failed'),
    ('<testsuite><testcase name="ok"><unexpected/></testcase></testsuite>', 0, 'failed'),
    ('<testsuite><testcase name="ok"/><testsuite/></testsuite>', 0, 'failed'),
    ('<testsuite><properties><testcase name="fake"/></properties></testsuite>', 0, 'failed'),
    ('<testsuite><testcase name="ok"/><properties><error/></properties></testsuite>', 0, 'failed'),
    ('<testsuite><testcase name="ok"/><system-out><testcase name="fake"/></system-out></testsuite>', 0, 'failed'),
    ('<testsuite tests="1" errors="0" failures="0" skipped="0"><testcase name="ok"/></testsuite>', 0, 'verified'),
    ('<testsuites tests="2" errors="0"><testsuite tests="1"><testcase name="a"/></testsuite>'
     '<testsuite tests="1"><testcase name="b"/></testsuite></testsuites>', 0, 'verified'),
])
def test_qualification_report_never_promotes_partial_checks(qualification_script, tmp_path, xml, code, expected):
    report = tmp_path / 'report.xml'
    report.write_text(xml)
    value = qualification_script.summarize_junit(report, code)
    assert value['status'] == expected
    assert value['exit_code'] == code
    assert 'private failure text' not in json.dumps(value)


def test_qualification_distinguishes_pytest_subtest_total_from_visible_cases(qualification_script, tmp_path):
    report = tmp_path / 'report.xml'
    report.write_text('<testsuite tests="3" failures="0" errors="0" skipped="0">'
                      '<testcase name="two_passing_subtests"/></testsuite>')
    value = qualification_script.summarize_junit(report, 0)
    assert value['status'] == 'verified'
    assert value['tests'] == 1 and value['reported_tests'] == 3


def test_qualification_missing_report_and_timeout_are_failures(qualification_script, tmp_path):
    report = tmp_path / 'absent.xml'
    assert qualification_script.summarize_junit(report, 0)['status'] == 'failed'
    report.write_text('<testsuite><testcase name="partial"/></testsuite>')
    value = qualification_script.summarize_junit(report, None, timed_out=True)
    assert value['status'] == 'failed' and value['timed_out']


def test_qualification_missing_pytest_is_unavailable(qualification_script, monkeypatch):
    monkeypatch.setattr(qualification_script.importlib.util, 'find_spec', lambda _: None)
    value = qualification_script.run_gate('http', timeout=1)
    assert value['status'] == 'unavailable' and value['exit_code'] is None


@pytest.mark.parametrize('key,level,native', [
    ('openai', 'med', 'medium'), ('openai', 'max', 'xhigh'),
    ('openai', 'ultra', 'xhigh'), ('openai', 'low', 'low'),
    ('machx', 'med', 'medium'), ('machx', 'max', 'max'),
])
async def test_unreported_support_keeps_adapter_mapping_and_none_reset(http_backend, key, level, native):
    backend, payloads, _ = http_backend(key)
    try:
        backend.set_effort(level)
        [event async for event in backend.ask('Configured effort fixture')]
        assert payloads[-1]['reasoning_effort'] == native
        assert not backend.capability_status()['reasoning_levels']['known']
        backend.set_effort(None)
        [event async for event in backend.ask('Provider default fixture')]
        assert 'reasoning_effort' not in payloads[-1]
    finally:
        await backend._client.aclose()


async def test_reported_native_vocabulary_precedes_global_alias(http_backend):
    backend, payloads, _ = http_backend('openai', ['med'])
    try:
        backend.set_effort('med')
        [event async for event in backend.ask('Exact model vocabulary')]
        assert payloads[-1]['reasoning_effort'] == 'med'
    finally:
        await backend._client.aclose()


async def test_provider_sessions_do_not_share_capabilities_or_request_settings(http_backend):
    first, first_payloads, _ = http_backend('machx', ['low', 'high'])
    second, second_payloads, _ = http_backend('openai')
    try:
        first.set_effort('low')
        first._image_rejection_model = first.model
        [event async for event in first.ask('First provider fixture')]
        [event async for event in second.ask('Second provider fixture')]
        assert first_payloads[0]['reasoning_effort'] == 'low'
        assert 'reasoning_effort' not in second_payloads[0]
        assert not second.capability_status()['reasoning_levels']['known']
        assert not second.capability_status()['image_readiness']['known']
    finally:
        await first._client.aclose()
        await second._client.aclose()


def test_qualification_changed_source_cannot_be_attributed_as_verified(qualification_script, tmp_path, monkeypatch):
    source = tmp_path / 'source.py'
    source.write_text('before')
    monkeypatch.setattr(qualification_script, 'ROOT', tmp_path)
    monkeypatch.setattr(qualification_script, 'SOURCES', ('source.py',))
    monkeypatch.setattr(qualification_script, 'GATES', {'http': ()})
    def run(*_, **__):
        source.write_text('after')
        return {'status': 'verified', 'exit_code': 0}
    monkeypatch.setattr(qualification_script, 'run_gate', run)
    report = tmp_path / 'result.json'
    assert qualification_script.main(['--output', str(report)]) == 1
    result = json.loads(report.read_text())
    assert result['source_consistency']['status'] == 'failed'
    assert result['source_consistency']['changed_paths'] == ['source.py']
    assert result['live_provider_inference']['status'] == 'not_run'


def test_qualification_child_uses_private_config_without_changing_parent(qualification_script, tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.setenv('DREAM_MACHX_SESSION_OPTIONS', 'owner-session')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-fixture-key')
    monkeypatch.setenv('HOME', str(tmp_path / 'operator-home'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'operator-data'))
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path / 'operator-runtime'))
    monkeypatch.setenv('PYTHONPATH', str(tmp_path / 'operator-python'))
    monkeypatch.setenv('PYTHONHOME', str(tmp_path / 'operator-python-home'))
    original = dict(os.environ)
    captured = {}
    def run(argv, **kwargs):
        captured.update(kwargs.get('env', {}))
        report = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
        report.write_text('<testsuite><testcase name="fixture"/></testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualification_script.subprocess, 'run', run)
    result = qualification_script.run_gate('http', timeout=1)
    assert result['status'] == 'verified'
    assert captured['CODEX_HOME'] != original.get('CODEX_HOME')
    assert captured['DREAM_ROOT'].startswith('/tmp/')
    assert captured['DREAM_SKILL_DIRS'] == ''
    assert captured['HOME'] != original['HOME']
    assert captured['XDG_DATA_HOME'] != original['XDG_DATA_HOME']
    assert captured['XDG_RUNTIME_DIR'] != original['XDG_RUNTIME_DIR']
    assert 'PYTHONPATH' not in captured and 'PYTHONHOME' not in captured
    assert captured['DREAM_SEMANTIC_MEMORY'] == '0'
    assert captured['DREAM_RERANK'] == '0'
    assert captured['DREAM_CONSOLIDATE'] == '0'
    assert captured['CUDA_VISIBLE_DEVICES'] == ''
    assert 'DREAM_MACHX_SESSION_OPTIONS' not in captured
    assert 'OPENAI_API_KEY' not in captured
    assert dict(os.environ) == original
    assert 'private-fixture-key' not in json.dumps(result)
