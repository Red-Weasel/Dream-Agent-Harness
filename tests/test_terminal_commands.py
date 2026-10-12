"""Owner terminal completion must stay local, scoped and non-executing."""
from types import SimpleNamespace

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from dream.tui import terminal_commands as commands


def complete(completer, text):
    return list(completer.get_completions(Document(text), CompleteEvent()))


def test_slash_discovers_real_commands_with_descriptions_without_catalogs(monkeypatch):
    def forbidden(*args):
        raise AssertionError('plain slash must not discover extension/model catalogs')
    monkeypatch.setattr(commands, 'model_choices', forbidden)
    monkeypatch.setattr(commands, '_extension_options', forbidden)
    items = complete(commands.TerminalCompleter(lambda: 'codex'), '/')
    by_text = {item.text: item for item in items}
    assert {'/help', '/model', '/effort', '/skill', '/plugin', '/login'} <= by_text.keys()
    assert all(item.display_meta_text for item in items)
    assert [item.text for item in complete(commands.TerminalCompleter(lambda: 'codex'), '/mo')] == ['/model', '/monitor']


def test_model_options_are_provider_scoped_and_preserve_custom_current():
    values = commands.model_options('anthropic', 'private-model')
    assert values[0] == ('private-model', 'private-model · current')
    assert any(model == 'claude-opus-5-5' for model, label in values)
    assert all(not model.startswith(('gpt-', 'grok-')) for model, label in values)
    assert all(model for model, label in values)
    assert commands.model_options('machx', None) == []


def test_model_completion_uses_latest_provider_and_current(monkeypatch):
    state = {'provider': 'anthropic', 'model': 'my-current'}
    completer = commands.TerminalCompleter(lambda: state['provider'], lambda: state['model'])
    assert any(item.text == 'my-current' for item in complete(completer, '/model '))
    item, = complete(completer, '/model Opus 5.5')
    assert item.text == 'claude-opus-5-5'
    assert item.start_position == -len('Opus 5.5')
    state.update(provider='grok', model='grok-4.7')
    items = complete(completer, '/model ')
    assert {item.text for item in items} == {'grok-4.7', 'grok-4.7-fast', 'grok-4.6', 'grok-4.5'}
    assert sum('current' in item.display_meta_text for item in items) == 1


def test_skill_completion_reads_only_metadata_on_demand(monkeypatch):
    from dream.tools import installed_skill_tools
    from dream.skills import loader
    calls = []
    monkeypatch.setattr(installed_skill_tools, 'installed', lambda: calls.append('metadata') or [
        SimpleNamespace(name='legal brief', description='Prepare a concise brief.'),
        SimpleNamespace(name='review', description='Review a document.')])
    monkeypatch.setattr(loader, 'load_body', lambda *a: (_ for _ in ()).throw(AssertionError('no bodies')))
    completer = commands.TerminalCompleter(lambda: 'anthropic')
    assert complete(completer, '/skill')
    assert not calls
    item, = complete(completer, '/skill leg')
    assert item.text == "'legal brief' "
    assert item.start_position == -3
    assert item.display_meta_text == 'Prepare a concise brief.'
    assert calls == ['metadata']
    assert not complete(completer, "/skill 'legal brief' write my task")
    assert calls == ['metadata']


def test_plugin_completion_excludes_disabled_and_uses_literal_safe_metadata(monkeypatch):
    from dream import plugins
    monkeypatch.setattr(plugins, 'loaded', lambda: [
        SimpleNamespace(name='enabled', description='[bold]Literal\ntext\x1b[31m', enabled=True),
        SimpleNamespace(name='disabled', description='Hidden', enabled=False)])
    item, = complete(commands.TerminalCompleter(lambda: 'anthropic'), '/plugin ')
    assert item.text == 'enabled '
    assert '\x1b' not in item.display_meta_text
    assert '\n' not in item.display_meta_text
    assert '[bold]' in item.display_meta_text


def test_argument_and_ordinary_text_completion_do_not_hijack_prompt():
    completer = commands.TerminalCompleter(lambda: 'anthropic')
    assert [item.text for item in complete(completer, '/login co')] == ['codex']
    assert not complete(completer, 'explain /model')
    assert not complete(completer, '/review main')
    assert not complete(completer, '/skill name task')
    assert not complete(completer, '/model name\nsecond line')


def test_missing_extension_metadata_leaves_completion_usable(monkeypatch):
    monkeypatch.setattr(commands, '_extension_options', lambda kind: (_ for _ in ()).throw(OSError('unavailable')))
    completer = commands.TerminalCompleter(lambda: 'anthropic')
    assert not complete(completer, '/skill ')
    assert complete(completer, '/help')
