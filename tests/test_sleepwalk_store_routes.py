"""Sleepwalk phase 1 (DREAM-156, DREAM-157): the JSON store, the runner's verdict, isolation and the routes.
No model, no network; the Codex isolation check runs against a fake `codex` script."""
import json
import os
import stat
import threading

import httpx
import pytest

from dream.core import council_config, moe
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.sleepwalk import runner, store

CHOICES = [
    {'key': 'machx', 'label': 'MachX (local)', 'available': True, 'efforts': [], 'models': []},
    {'key': 'codex', 'label': 'ChatGPT · Codex', 'available': True, 'efforts': ['low', 'high'],
     'models': [{'id': 'gpt-6-astra', 'label': 'GPT-6 Astra', 'efforts': ['low', 'medium', 'high']}]},
    {'key': 'grok', 'label': 'Grok · xAI', 'available': True, 'efforts': [], 'models': []},
    {'key': 'xai', 'label': 'xAI API', 'available': True, 'efforts': [], 'models': []},
]
AUTO = {'title': 'Morning notes', 'icon': 'sun', 'instructions': 'Summarise one idea.',
        'triggers': [{'every': 'day', 'at': '07:30', 'label': 'lesson'}],
        'runner': {'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'high'}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'sleepwalk')
    monkeypatch.setattr('dream.sleepwalk.background.state', lambda: 'off')   # never ask the real systemctl
    monkeypatch.setattr('dream.sleepwalk.background._logind', lambda prop: '')   # or the real logind
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)
    return tmp_path / 'sleepwalk'


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_save_is_private_atomic_and_validated(home):
    saved = store.save(dict(AUTO))
    assert saved['id'] and saved['enabled'] is True and saved['notify'] == ['app']
    path = home / 'automations.json'
    assert mode(path) == 0o600 and mode(home) == 0o700
    assert json.loads(path.read_text())['version'] == 1
    assert [a['title'] for a in store.load()] == ['Morning notes']
    edited = store.save({**saved, 'title': 'Evening notes'})
    assert edited['id'] == saved['id'] and len(store.load()) == 1
    assert not [p for p in home.iterdir() if p.name.startswith('.') and p.name != '.store.lock']   # no temp file left
    for bad in ({'title': ''}, {'instructions': ' '}, {'icon': 'rocket-ship'},
                {'triggers': [{'every': 'fortnight', 'at': '07:00'}]}, {'triggers': [{'every': 'day', 'at': '25:00'}]},
                {'runner': {'provider': 'nobody'}}, {'triggers': [{'every': 'day', 'at': '07:00'}] * 9}):
        with pytest.raises(ValueError):
            store.save({**AUTO, **bad})
    store.delete(saved['id'])
    assert store.load() == []
    with pytest.raises(KeyError):
        store.delete(saved['id'])


def test_templates_are_generic_and_valid(home):
    names = [t['title'] for t in store.templates()]
    assert names == ['Daily Briefing', 'Learn Something New Every Day', 'Tip of the Day']
    for template in store.templates():
        store.save({**template, 'runner': AUTO['runner']})       # every template passes the store's own rules
    assert '@' not in json.dumps(store.templates())


async def test_run_records_ok_and_passes_a_read_only_call(home, monkeypatch):
    seen = {}

    async def consult(provider, question, context='', **kw):
        seen.update(kw, provider=provider, question=question)
        return 'Here is the lesson.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    auto = store.save(dict(AUTO))
    record = await runner.run(auto)
    assert record['status'] == 'OK' and record['output'] == 'Here is the lesson.' and record['notified'] == ['app']
    assert seen['provider'] == 'codex' and seen['model'] == 'gpt-6-astra' and seen['effort'] == 'high'
    assert seen['mode'] == 'ask' and 'never send' in seen['system'] and 'Summarise one idea.' in seen['question']
    assert seen['isolate'] is True and 'dream-sleepwalk-' in seen['cwd'] and not os.path.exists(seen['cwd'])
    assert not str(seen['cwd']).startswith(str(home))
    rows = store.runs()
    assert [r['status'] for r in rows] == ['OK'] and 'output' not in rows[0]
    assert store.read_run(auto['id'], rows[0]['run'])['output'] == 'Here is the lesson.'
    assert mode(next((home / 'runs' / auto['id']).iterdir())) == 0o600


@pytest.mark.parametrize('answer', ['[ChatGPT · Codex: unavailable — CLIIsolationError: codex CLI is not installed]',
                                    '[unavailable — advisor returned no answer]',
                                    'partial text\n\n[failed — MachX HTTP 500: boom]', '   '])
async def test_an_unavailable_runner_is_failed_never_ok(home, monkeypatch, answer):
    async def consult(*_a, **_k):
        return answer
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    record = await runner.run(store.save(dict(AUTO)))
    assert record['status'] == 'FAILED' and record['output'] == '' and record['error']


async def test_routes(home, monkeypatch, tmp_path):
    async def consult(*_a, **_k):
        return 'Fine.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(tmp_path), 'model': 'fixture'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://127.0.0.1',
                                 headers={'X-Dream-Token': srv.token}) as client:
        page = (await client.get('/api/sleepwalk')).json()
        assert page['automations'] == [] and len(page['templates']) == 3
        assert page['default_runner'] == {'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'high'}
        assert [(r['key'], r['isolated']) for r in page['runners']] == [
            ('machx', True), ('codex', True), ('grok', False), ('xai', True)]
        saved = (await client.post('/api/sleepwalk/save', json={'automation': AUTO})).json()['automation']
        bad = await client.post('/api/sleepwalk/save', json={'automation': {**AUTO, 'title': ''}})
        assert bad.status_code == 400
        run = (await client.post('/api/sleepwalk/run', json={'id': saved['id']})).json()['run']
        assert run['status'] == 'OK'
        runs = (await client.get('/api/sleepwalk/runs')).json()['runs']
        assert [r['run'] for r in runs] == [run['run']]
        one = (await client.get(f"/api/sleepwalk/runs/{saved['id']}/{run['run']}")).json()['run']
        assert one['output'] == 'Fine.'
        assert (await client.get(f"/api/sleepwalk/runs/{saved['id']}/..%2F..%2Fx")).status_code == 404
        assert (await client.post('/api/sleepwalk/run', json={'id': 'missing'})).status_code == 404
        assert (await client.get('/api/sleepwalk', headers={'X-Dream-Token': 'wrong'})).status_code == 401
        foreign = await client.post('/api/sleepwalk/delete', json={'id': saved['id']},
                                    headers={'Origin': 'http://elsewhere'})
        assert foreign.status_code == 403
        assert (await client.post('/api/sleepwalk/delete', json={'id': saved['id']})).json() == {'deleted': True}
        assert (await client.get('/api/sleepwalk')).json()['automations'] == []


def test_default_runner_falls_back_to_grok_then_none(home, monkeypatch):
    from dream.gui import sleepwalk_routes
    no_codex = [dict(c, available=c['key'] != 'codex') for c in CHOICES]
    assert sleepwalk_routes.default_runner(no_codex) == {'provider': 'xai', 'model': None, 'effort': None}
    assert sleepwalk_routes.default_runner([dict(c, available=False) for c in CHOICES]) is None


async def test_system_kwarg_replaces_the_advisor_persona(monkeypatch):
    seen = {}

    async def cli(provider, prompt, **kw):
        seen.update(kw)
        return 'ok'
    monkeypatch.setattr(moe, '_consult_cli', cli)
    assert await moe.consult_advisor('codex', 'q', system='SLEEP', cwd='/') == 'ok'
    assert seen['system'] == 'SLEEP'
    seen.clear()
    await moe.consult_advisor('codex', 'q', cwd='/')
    assert 'system' not in seen          # the Council's own calls are unchanged


@pytest.mark.parametrize('content', ['[]', 'null', '{"version":1,"automations":[null]}', '{"version":1,"automations":[{"id":"x"}]}'])
async def test_malformed_store_is_reported_skipped_and_never_rewritten(home, tmp_path, content):
    home.mkdir(parents=True)
    (home / 'automations.json').write_text(content)
    problems = []
    assert store.load(problems) == [] and len(problems) == 1
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(tmp_path), 'model': 'fixture'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://127.0.0.1',
                                 headers={'X-Dream-Token': srv.token}) as client:
        page = await client.get('/api/sleepwalk')
        assert page.status_code == 200 and page.json()['automations'] == [] and page.json()['problems']
        saved = await client.post('/api/sleepwalk/save', json={'automation': AUTO})
    if content.startswith('{'):
        assert saved.status_code == 200                  # the damaged entry is kept as it is, beside the new one
        entries = json.loads((home / 'automations.json').read_text())['automations']
        assert entries[0] == json.loads(content)['automations'][0] and len(entries) == 2
    else:
        assert saved.status_code == 400 and (home / 'automations.json').read_text() == content


async def test_damaged_run_records_are_skipped_and_reported(home, tmp_path):
    folder = home / 'runs' / 'abcdefabcdef'
    folder.mkdir(parents=True)
    for name, text in [('20260101-000000-000000', 'null'), ('20260101-000001-000000', '[1]'),
                       ('20260101-000002-000000', '{"status": "OK"}')]:
        (folder / f'{name}.json').write_text(text)
    problems = []
    assert store.runs(problems=problems) == [] and len(problems) == 1
    with pytest.raises(ValueError):
        store.read_run('abcdefabcdef', '20260101-000000-000000')
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(tmp_path), 'model': 'fixture'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://127.0.0.1',
                                 headers={'X-Dream-Token': srv.token}) as client:
        listed = (await client.get('/api/sleepwalk/runs')).json()
        assert listed['runs'] == [] and listed['problems']
        assert (await client.get('/api/sleepwalk/runs/abcdefabcdef/20260101-000000-000000')).status_code == 400


def test_folders_are_0700_even_when_they_existed(home):
    (home / 'runs').mkdir(parents=True, mode=0o755)
    os.chmod(home, 0o755)
    os.chmod(home / 'runs', 0o775)
    auto = store.save(dict(AUTO))
    store.add_run(auto['id'], {'title': 't', 'status': 'OK', 'started': 's', 'runner': {}})
    with store.running(auto['id']):
        pass
    folders = [home, *(p for p in home.rglob('*') if p.is_dir())]
    assert {p.relative_to(home).as_posix(): mode(p) for p in folders} == {
        '.': 0o700, 'runs': 0o700, f"runs/{auto['id']}": 0o700, 'locks': 0o700}
    assert all(mode(p) == 0o600 for p in home.rglob('*') if p.is_file())


def test_concurrent_saves_lose_nothing(home):
    ready = threading.Barrier(12)

    def one(i):
        ready.wait()
        store.save({**AUTO, 'title': f'Automation {i}'})
    threads = [threading.Thread(target=one, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(a['title'] for a in store.load()) == sorted(f'Automation {i}' for i in range(12))


async def test_a_running_automation_refuses_a_second_run(home, monkeypatch):
    async def consult(*_a, **_k):
        return 'unused'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    auto = store.save(dict(AUTO))
    with store.running(auto['id']):
        with pytest.raises(ValueError, match='already running'):
            await runner.run(auto)
    assert (await runner.run(auto))['status'] == 'OK'   # released afterwards


async def test_isolation_per_runner(monkeypatch):
    seen = {}

    async def invoker(provider, prompt, **kw):
        seen[provider.key] = kw
        return 'ok'
    for name in ('_consult_cli', '_consult_anthropic', '_consult_openai'):
        monkeypatch.setattr(moe, name, invoker)
    for key in ('codex', 'anthropic', 'machx', 'xai'):
        assert await moe.consult_advisor(key, 'q', cwd='/', isolate=True) == 'ok'
    assert seen['codex']['isolate'] is True and seen['anthropic']['isolate'] is True
    assert 'isolate' not in seen['machx'] and 'isolate' not in seen['xai']      # HTTP runners have no local tools
    for key in ('grok', 'gemini'):
        answer = await moe.consult_advisor(key, 'q', cwd='/', isolate=True)
        assert 'unavailable' in answer and 'isolated run is refused' in answer and key not in seen
    seen.clear()
    await moe.consult_advisor('anthropic', 'q', cwd='/')
    assert 'isolate' not in seen['anthropic']                                   # the Council's calls are unchanged


async def test_http_runner_sends_no_tools(monkeypatch):
    captured = {}

    async def run_backend(backend, prompt):
        captured['backend'] = backend
        return 'ok'
    monkeypatch.setattr(moe, '_run_backend', run_backend)
    from dream.core.providers import get_provider
    assert await moe._consult_openai(get_provider('machx'), 'q', system='S') == 'ok'
    backend = captured['backend']
    assert backend.tools == [] and not backend._request_tools() and backend.messages[0] == {'role': 'system', 'content': 'S'}


async def test_claude_isolation_loads_no_owner_settings(monkeypatch):
    from dream.core.backends import anthropic
    options = anthropic.claude_code_options(system_prompt='S', cwd='/', mode='ask', model=None, effort=None,
                                            owner_settings=False)
    assert options.setting_sources == [] and options.mcp_servers == {}
    council = anthropic.claude_code_options(system_prompt='S', cwd='/', mode='ask', model=None, effort=None)
    assert council.setting_sources == ['user', 'project', 'local']
    seen = []

    def spy(**kw):
        seen.append(kw['owner_settings'])
        raise RuntimeError('stop before any SDK call')
    monkeypatch.setattr(anthropic, 'claude_code_options', spy)
    from dream.core.providers import get_provider
    for isolate in (True, False):
        with pytest.raises(RuntimeError):
            await moe._consult_anthropic(get_provider('anthropic'), 'q', cwd='/', isolate=isolate)
    assert seen == [False, True]


FAKE_CODEX = """#!/bin/sh
echo "$@" > "$(dirname "$0")/args.txt"
cat "$(dirname "$0")/listing.json"
"""


@pytest.mark.parametrize('listing,refused', [
    ('[{"name":"docs","enabled":false},{"name":"repl","enabled":false}]', None),
    ('[{"name":"docs","enabled":false},{"name":"repl","enabled":true}]', 'repl'),
    ('not json', 'codex mcp list failed'),
    ('[5]', '5'),                                   # a malformed item is refused, not an AttributeError
])
async def test_codex_isolation_flags_and_check(tmp_path, monkeypatch, listing, refused):
    from dream.core.cli_review import CLIIsolationError, codex_isolation
    home = tmp_path / 'codex'
    home.mkdir()
    (home / 'config.toml').write_text('[mcp_servers.docs]\nurl = "https://example.invalid"\n'
                                      '[mcp_servers.repl]\ncommand = "repl"\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    fake = tmp_path / 'codex-bin'
    fake.write_text(FAKE_CODEX)
    fake.chmod(0o755)
    (tmp_path / 'listing.json').write_text(listing)
    if refused:
        with pytest.raises(CLIIsolationError, match=refused):
            await codex_isolation(str(fake), str(tmp_path))
        return
    args = await codex_isolation(str(fake), str(tmp_path))
    for flag in ('plugins', 'apps', 'hooks'):
        assert ['--disable', flag] in [args[i:i + 2] for i in range(len(args))]
    for setting in ('project_doc_max_bytes=0', 'skills.include_instructions=false',
                    'mcp_servers.docs.enabled=false', 'mcp_servers.repl.enabled=false'):
        assert setting in args
    assert (tmp_path / 'args.txt').read_text().split() == ['mcp', 'list', '--json', *args]


async def test_codex_isolation_refuses_an_unreadable_config(tmp_path, monkeypatch):
    from dream.core.cli_review import CLIIsolationError, codex_isolation
    (tmp_path / 'config.toml').write_text('[mcp_servers."a.b"]\ncommand = "x"\n')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    with pytest.raises(CLIIsolationError, match='cannot turn off'):
        await codex_isolation('/bin/false', str(tmp_path))
    (tmp_path / 'config.toml').write_text('not = [toml')
    with pytest.raises(CLIIsolationError, match='cannot read'):
        await codex_isolation('/bin/false', str(tmp_path))


def test_ids_and_times_with_a_trailing_newline_are_refused(home):
    with pytest.raises(KeyError):
        store.read_run('abcdefabcdef\n', '20260101-000000-000000')
    with pytest.raises(KeyError):
        store.read_run('abcdefabcdef', '20260101-000000-000000\n')
    with pytest.raises(KeyError):
        with store.running('abcdefabcdef\n'):
            pass
    with pytest.raises(ValueError):
        store.save({**AUTO, 'triggers': [{'every': 'day', 'at': '07:30\n'}]})


async def test_isolated_codex_provenance_is_not_none(monkeypatch):
    from dream.core import cli_review
    from dream.core.providers import get_provider
    seen = {}

    async def isolation(executable, cwd):
        return ['--disable', 'plugins']

    def prepare(self):
        self.executable, self.argv = 'codex', ['codex', 'exec', '-']

    async def run(self, prompt):
        seen['consultation'] = self
        return 'ok'

    async def stop(self):
        return None
    monkeypatch.setattr(cli_review, 'codex_isolation', isolation)
    monkeypatch.setattr(cli_review.CLIConsultation, 'prepare', prepare)
    monkeypatch.setattr(cli_review.CLIConsultation, 'run', run)
    monkeypatch.setattr(cli_review.CLIConsultation, '_stop', stop)
    for isolate in (True, False):
        assert await moe._consult_cli(get_provider('codex'), 'q', cwd='/', isolate=isolate) == 'ok'
        consultation = seen['consultation']
        assert consultation.provenance['isolation'].startswith('none') is (not isolate)
        assert ('--disable' in consultation.argv) is isolate
