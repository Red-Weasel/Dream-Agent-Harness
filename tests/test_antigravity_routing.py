"""Dream-only fixture routing: no native provider/account calls."""
from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace

import pytest

from dream.core import council_config, provider_login, settings
from dream.core.backends.cli_agent import adapter_for, native_effort_argv
from dream.core.backends.antigravity_adapter import AntigravityAdapter
from dream.core.cli_review import CLIConsultation, CLIReviewBackend, CLIIsolationError
from dream.core.providers import get_provider


def test_provider_and_adapter_are_distinct_antigravity_transport():
    provider = get_provider('antigravity')
    assert provider.kind == 'cli' and provider.cli_cmd == 'agy'
    assert isinstance(adapter_for(provider.key), AntigravityAdapter)
    assert adapter_for(provider.key).key == 'antigravity'


def test_council_choices_offer_antigravity_without_legacy_gemini(monkeypatch):
    monkeypatch.setattr('dream.core.council_config.locate_command', lambda name: '/fixture/' + name)
    keys = [row['key'] for row in council_config.provider_choices()]
    assert 'antigravity' in keys
    assert 'gemini' not in keys


@pytest.mark.parametrize('alias', ['gemini', 'agy', 'antigravity'])
def test_login_aliases_select_native_agent_without_launch(alias):
    target = provider_login.resolve(alias)
    assert target.key == 'antigravity'
    assert target.cli == target.command == 'agy'
    assert target.args == () and target.login_only is False


def test_picker_discovers_agy_and_never_legacy_gemini(monkeypatch):
    from dream.tui import picker
    names = []
    def installed(name):
        names.append(name)
        return name == 'agy'
    monkeypatch.setattr(picker, '_cli_installed', installed)
    rows = picker.detect_providers()
    assert 'agy' in names and 'gemini' not in names
    anti = next(row for row in rows if row.key == 'antigravity')
    assert anti.ready
    assert not any(row.key == 'gemini' for row in rows)


@pytest.mark.parametrize('effort', ['low', 'medium', 'high', 'xhigh', 'max'])
def test_effort_is_native_flag(effort):
    assert native_effort_argv('antigravity', effort) == ['--effort', effort]


@pytest.mark.parametrize('mode,native_mode,bypass', [
    ('ask', 'plan', False), ('plan', 'plan', False),
    ('edit', 'accept-edits', False), ('auto', 'accept-edits', True)])
def test_consultation_prepares_native_argv_and_truthful_posture(monkeypatch, tmp_path, mode, native_mode, bypass):
    binary = tmp_path / 'agy'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o700)
    names = []
    def locate(name):
        names.append(name)
        return str(binary)
    monkeypatch.setattr('dream.core.cli_review.locate_command', locate)
    consultation = CLIConsultation(get_provider('antigravity'), model='gemini-3.8-flash-high', cwd=str(tmp_path), mode=mode)
    try:
        consultation.prepare()
        assert names == ['agy']
        assert consultation.argv[0] == str(binary)
        assert consultation.argv[consultation.argv.index('--input-format') + 1] == 'stream-json'
        assert consultation.argv[consultation.argv.index('--mode') + 1] == native_mode
        assert consultation.argv[consultation.argv.index('--model') + 1] == 'gemini-3.8-flash-high'
        assert ('--dangerously-skip-permissions' in consultation.argv) is bypass
        assert '-p' not in consultation.argv and '-c' not in consultation.argv
        assert 'not a read-only' in consultation.provenance['native_sandbox']
        assert consultation.env is None
    finally:
        consultation.close()


async def test_reviewer_refuses_antigravity_before_discovery_or_launch(monkeypatch, tmp_path):
    assert 'antigravity' not in settings._CRITICS
    def unexpected(*args, **kwargs):
        pytest.fail('reviewer must refuse before provider preparation')
    monkeypatch.setattr(CLIConsultation, 'prepare', unexpected)
    backend = CLIReviewBackend(SimpleNamespace(provider=get_provider('antigravity'), model=None,
        runtime_meter=None, mode='plan'), [], 'fixture', str(tmp_path))
    with pytest.raises(CLIIsolationError, match='read-only reviewer'):
        await backend.connect()
    assert backend.consultation.process is None


@pytest.mark.parametrize('main,advisors', [('gemini', []), ('codex', ['gemini'])])
def test_saved_legacy_council_refuses_migration_without_mutating_selection(main, advisors):
    saved = {'orchestrator': main, 'advisors': advisors,
             'advisor_models': {key: 'saved-model' for key in advisors}}
    original = deepcopy(saved)
    with pytest.raises(council_config.ConfigError, match='Antigravity'):
        council_config.parse_config(saved)
    assert saved == original


@pytest.mark.parametrize('denied', [False, True])
async def test_consultation_sends_framed_stdin_and_closes_it_before_completion(monkeypatch, tmp_path, denied):
    binary = tmp_path / 'agy'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o700)
    monkeypatch.setattr('dream.core.cli_review.locate_command', lambda name: str(binary))
    monkeypatch.setattr('dream.core.cli_review.supervised_command', lambda argv: argv)
    writes, cleaned = [], []
    class Input:
        closed = False
        def write(self, payload):
            writes.append(payload)
        async def drain(self):
            pass
        def close(self):
            self.closed = True
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    result = {'status': 'SUCCESS', 'response': 'Fixture answer', 'usage': {}}
    if denied:
        result['denied_actions'] = [{'tool': 'write_to_file'}]
    stdout.feed_data((json.dumps({'event': 'result', 'result': result}) + '\n').encode())
    stdout.feed_eof()
    stderr.feed_eof()
    stdin = Input()
    async def wait():
        assert stdin.closed
        return 0
    proc = SimpleNamespace(stdin=stdin, stdout=stdout, stderr=stderr, returncode=0, wait=wait)
    async def spawn(*argv, **kwargs):
        assert argv[0] == str(binary) and '-p' not in argv
        assert kwargs['cwd'] == str(tmp_path)
        return proc
    async def cleanup(process, **kwargs):
        cleaned.append(process)
    monkeypatch.setattr('dream.core.cli_review.asyncio.create_subprocess_exec', spawn)
    monkeypatch.setattr('dream.core.cli_review._owned_cleanup', cleanup)
    consultation = CLIConsultation(get_provider('antigravity'), cwd=str(tmp_path))
    try:
        consultation.prepare()
        if denied:
            with pytest.raises(CLIIsolationError, match='complete assessment'):
                await consultation.run('first line\nsecond line')
        else:
            assert await consultation.run('first line\nsecond line') == 'Fixture answer'
        assert len(writes) == 1 and writes[0].endswith(b'\n')
        assert json.loads(writes[0]) == {'event': 'user', 'message': {'content': 'first line\nsecond line'}}
        assert stdin.closed and cleaned == [proc]
        assert consultation.process is None
    finally:
        consultation.close()
